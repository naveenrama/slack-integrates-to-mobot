import asyncio
import base64 as b64
import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from threading import Thread
from urllib.parse import quote

from flask import Flask, request, redirect, jsonify
from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

from config import settings
from storage.base import TokenStore
from storage.models import Connection
from sumo.client import SumoClient
from sumo.oauth import SumoOAuth
from sumo.deployments import get_deployment, from_url, DEPLOYMENTS
from slack_handlers.mention import MentionHandler
from slack_handlers.commands import CommandHandler

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper()),
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


# --- Storage ---

def create_store() -> TokenStore:
    if settings.token_store_backend == "dynamodb":
        from storage.kms_dynamo_store import KmsDynamoTokenStore
        return KmsDynamoTokenStore(
            table_name=settings.dynamodb_table,
            kms_key_arn=settings.kms_key_arn,
            region=settings.aws_region,
        )
    else:
        from storage.sqlite_store import SQLiteTokenStore
        return SQLiteTokenStore(
            db_path=settings.sqlite_db_path,
            encryption_key=settings.token_encryption_key,
        )


store = create_store()
sumo_client = SumoClient()
oauth = SumoOAuth(
    cimd_metadata_url=settings.cimd_metadata_url,
    callback_url=settings.oauth_callback_url,
)


# --- Flask OAuth Server (runs in background thread) ---

flask_app = Flask(__name__)


@flask_app.route("/.well-known/oauth-client-metadata.json")
def client_metadata():
    return jsonify({
        "client_name": "Mobot - Slack Agent",
        "client_uri": settings.oauth_callback_url.rsplit("/oauth", 1)[0],
        "redirect_uris": [settings.oauth_callback_url],
        "grant_types": ["authorization_code"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "scope": "runLogSearch runMetricsQuery viewLibrary manageCollectors",
    })


@flask_app.route("/oauth/start")
def oauth_start():
    slack_user_id = request.args.get("slack_user_id")
    deployment_code = request.args.get("deployment")
    custom_url = request.args.get("url")
    channel_id = request.args.get("channel_id")
    thread_ts = request.args.get("thread_ts")

    if not slack_user_id:
        return "Missing slack_user_id parameter", 400
    if not deployment_code and not custom_url:
        return "Missing deployment or url parameter", 400

    if custom_url:
        deployment = from_url(custom_url)
    else:
        deployment = get_deployment(deployment_code)

    authorize_url, state = oauth.build_authorize_url(
        deployment, slack_user_id, channel_id=channel_id, thread_ts=thread_ts,
    )
    return redirect(authorize_url)


@flask_app.route("/oauth/callback")
def oauth_callback():
    code = request.args.get("code")
    state = request.args.get("state")
    error = request.args.get("error")

    if error:
        return f"OAuth error: {error} - {request.args.get('error_description', '')}", 400
    if not code or not state:
        return "Missing code or state parameter", 400

    state_data = oauth.validate_state(state)
    if state_data is None:
        return "Invalid or expired state", 400

    slack_user_id = state_data["slack_user_id"]
    deployment_code = state_data["deployment"]
    verifier = state_data["verifier"]

    if deployment_code.startswith("https://"):
        deployment = from_url(deployment_code)
    else:
        deployment = get_deployment(deployment_code)

    token_data = asyncio.run(oauth.exchange_code(deployment, code, verifier))

    # Extract API base from JWT audience claim
    jwt_parts = token_data["access_token"].split(".")
    jwt_payload = json.loads(b64.urlsafe_b64decode(jwt_parts[1] + "=="))
    api_base = jwt_payload.get("aud", [deployment.api_base])[0]

    connection = Connection(
        id=str(uuid.uuid4()),
        label=deployment.name,
        deployment=deployment_code,
        api_base=api_base,
        access_token=token_data["access_token"],
        refresh_token=token_data.get("refresh_token", ""),
        token_expires_at=datetime.now(timezone.utc) + timedelta(seconds=token_data.get("expires_in", 300)),
        org_id=jwt_payload.get("sumo_org_id", ""),
        scopes=token_data.get("scope", "").split(),
    )

    asyncio.run(store.initialize())
    asyncio.run(store.save_connection(slack_user_id, connection))

    # Notify user in Slack (in the channel/thread where they initiated)
    from slack_sdk import WebClient
    slack_client = WebClient(token=settings.slack_bot_token)
    notify_channel = state_data.get("channel_id") or slack_user_id
    notify_thread = state_data.get("thread_ts")
    slack_client.chat_postMessage(
        channel=notify_channel,
        thread_ts=notify_thread,
        text=f"Connected to *{deployment.name}*! You can now ask me questions.",
    )

    return """
    <html><body style="font-family:sans-serif;text-align:center;padding:50px;">
    <h1>Connected!</h1>
    <p>Your Sumo Logic account has been linked to Mobot.</p>
    <p>You can close this window and return to Slack.</p>
    </body></html>
    """


def run_flask():
    flask_app.run(host="0.0.0.0", port=3000, debug=False, use_reloader=False)


# --- Slack Bolt App ---

app = AsyncApp(token=settings.slack_bot_token)

mention_handler = MentionHandler(store=store, sumo_client=sumo_client, oauth=oauth)
command_handler = CommandHandler(store=store)


@app.event("app_mention")
async def handle_app_mention(event, say, client):
    user_id = event["user"]
    channel_id = event["channel"]
    text = event.get("text", "")
    thread_ts = event.get("thread_ts", event["ts"])

    bot_info = await client.auth_test()
    bot_user_id = bot_info["user_id"]

    cmd, arg = command_handler.parse_command(text, bot_user_id)
    if cmd:
        handled = await command_handler.handle_command(
            command=cmd,
            arg=arg,
            user_id=user_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
            client=client,
        )
        if handled:
            return

    await mention_handler._handle(event, say, client)


@app.event("message")
async def handle_message(event, say, client):
    if event.get("channel_type") == "im" and not event.get("bot_id"):
        event["thread_ts"] = event.get("thread_ts", event["ts"])
        await mention_handler._handle(event, say, client)


@app.event("app_home_opened")
async def handle_app_home(event, client):
    if event.get("tab") == "messages":
        user_id = event["user"]
        channel_id = event["channel"]

        connections = await store.get_connections(user_id)
        if not connections:
            await client.assistant_threads_setSuggestedPrompts(
                channel_id=channel_id,
                title="Welcome to Mobot! Connect your Sumo Logic account to get started.",
                prompts=[
                    {"title": "Connect account", "message": "connect"},
                    {"title": "What can you do?", "message": "help"},
                ],
            )
        else:
            await client.assistant_threads_setSuggestedPrompts(
                channel_id=channel_id,
                title="What can I help you investigate?",
                prompts=[
                    {"title": "Investigate errors", "message": "Show me recent errors in production"},
                    {"title": "Check latency", "message": "What's the p99 latency for the API service?"},
                    {"title": "My connections", "message": "connections"},
                ],
            )


command_handler.register(app)


# --- Main ---

async def main():
    await store.initialize()
    logger.info("Token store initialized")

    # Start Flask OAuth server in background thread
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("OAuth server started on :3000")

    # Start Slack bot
    handler = AsyncSocketModeHandler(app, settings.slack_app_token)
    logger.info("Starting Mobot in Socket Mode...")
    logger.info(f"CIMD metadata URL: {settings.cimd_metadata_url}")
    logger.info(f"OAuth callback URL: {settings.oauth_callback_url}")
    await handler.start_async()


if __name__ == "__main__":
    asyncio.run(main())
