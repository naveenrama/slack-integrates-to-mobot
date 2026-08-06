import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

from flask import Flask, request, redirect, jsonify

from config import settings
from sumo.oauth import SumoOAuth
from sumo.deployments import get_deployment, from_url, DEPLOYMENTS
from storage.models import Connection
from storage.sqlite_store import SQLiteTokenStore

logger = logging.getLogger(__name__)

app = Flask(__name__)

oauth = SumoOAuth(
    cimd_metadata_url=settings.cimd_metadata_url,
    callback_url=settings.oauth_callback_url,
)

store = SQLiteTokenStore(
    db_path=settings.sqlite_db_path,
    encryption_key=settings.token_encryption_key,
)


@app.route("/.well-known/oauth-client-metadata.json")
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


@app.route("/oauth/start")
def oauth_start():
    slack_user_id = request.args.get("slack_user_id")
    deployment_code = request.args.get("deployment")
    custom_url = request.args.get("url")

    if not slack_user_id:
        return "Missing slack_user_id parameter", 400

    if not deployment_code and not custom_url:
        return "Missing deployment or url parameter", 400

    if custom_url:
        deployment = from_url(custom_url)
    else:
        deployment = get_deployment(deployment_code)

    authorize_url, state = oauth.build_authorize_url(deployment, slack_user_id)
    return redirect(authorize_url)


@app.route("/oauth/callback")
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
    deployment = get_deployment(deployment_code)

    token_data = asyncio.run(oauth.exchange_code(deployment, code, verifier))

    connection = Connection(
        id=str(uuid.uuid4()),
        label=f"{deployment.name}",
        deployment=deployment_code,
        api_base=deployment.api_base,
        access_token=token_data["access_token"],
        refresh_token=token_data.get("refresh_token", ""),
        token_expires_at=datetime.now(timezone.utc) + timedelta(seconds=token_data.get("expires_in", 300)),
        org_id=token_data.get("org_id", ""),
        scopes=token_data.get("scope", "").split(),
    )

    asyncio.run(store.initialize())
    asyncio.run(store.save_connection(slack_user_id, connection))

    return """
    <html>
    <body style="font-family: sans-serif; text-align: center; padding: 50px;">
        <h1>Connected!</h1>
        <p>Your Sumo Logic account has been linked to Mobot.</p>
        <p>You can close this window and return to Slack.</p>
    </body>
    </html>
    """


@app.route("/oauth/deployments")
def list_deployments():
    return jsonify({
        code: {"name": d.name, "service_url": d.service_url}
        for code, d in DEPLOYMENTS.items()
    })


if __name__ == "__main__":
    logging.basicConfig(level=settings.log_level)
    logger.info(f"OAuth server starting on :3000")
    logger.info(f"CIMD metadata: {settings.cimd_metadata_url}")
    logger.info(f"Callback URL: {settings.oauth_callback_url}")
    app.run(host="0.0.0.0", port=3000, debug=True)
