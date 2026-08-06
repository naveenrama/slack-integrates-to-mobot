import asyncio
import logging
from datetime import datetime, timezone

from slack_bolt.async_app import AsyncApp
from slack_sdk.web.async_client import AsyncWebClient

from config import settings
from sumo.client import SumoClient
from sumo.models import PollStatus
from sumo.deployments import get_deployment
from sumo.oauth import SumoOAuth
from storage.base import TokenStore
from storage.models import ConversationKey
from slack_handlers.context import ContextEnricher
from slack_handlers.formatter import markdown_to_slack_mrkdwn, truncate_for_slack
from routing.router import AgentRouter

logger = logging.getLogger(__name__)


class MentionHandler:
    def __init__(self, store: TokenStore, sumo_client: SumoClient, oauth: SumoOAuth):
        self.store = store
        self.sumo_client = sumo_client
        self.oauth = oauth

    def register(self, app: AsyncApp) -> None:
        @app.event("app_mention")
        async def handle_mention(event, say, client: AsyncWebClient):
            await self._handle(event, say, client)

    async def _handle(self, event: dict, say, client: AsyncWebClient) -> None:
        user_id = event["user"]
        channel_id = event["channel"]
        text = event.get("text", "")
        thread_ts = event.get("thread_ts", event["ts"])

        # Check if user has connections
        connections = await self.store.get_connections(user_id)
        if not connections:
            await self._send_connect_prompt(client, channel_id, user_id, thread_ts)
            return

        # Route to connection + agent
        router = AgentRouter(self.store)
        route_result = await router.route(user_id, channel_id, text)

        if route_result is None:
            await self._send_connect_prompt(client, channel_id, user_id, thread_ts)
            return

        if route_result.needs_selection:
            await self._send_agent_picker(client, channel_id, user_id, thread_ts, route_result.available_agents)
            return

        connection = route_result.connection

        # Check token freshness
        if connection.token_expires_at <= datetime.now(timezone.utc):
            connection = await self._refresh_token(user_id, connection)
            if connection is None:
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text="Your session has expired. Please reconnect with `@mobot connect`.",
                    thread_ts=thread_ts,
                )
                return

        # Set thinking status
        await client.assistant_threads_setStatus(
            channel_id=channel_id,
            thread_ts=thread_ts,
            status="Thinking...",
        )

        # Enrich context
        enricher = ContextEnricher(client)
        bot_info = await client.auth_test()
        bot_user_id = bot_info["user_id"]

        enriched_prompt = await enricher.build_enriched_prompt(
            text=text,
            sender_id=user_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
            bot_user_id=bot_user_id,
        )

        # Get or create Sumo conversation
        conv_key = ConversationKey(
            slack_user_id=user_id,
            connection_id=connection.id,
            agent_id=route_result.agent.agent_id if route_result.agent else "default",
            thread_ts=thread_ts,
        )
        conversation_id = await self.store.get_conversation_id(conv_key)
        if not conversation_id:
            conversation_id = await self.sumo_client.create_conversation(
                connection.api_base, connection.access_token
            )
            await self.store.set_conversation_id(conv_key, conversation_id)

        # Send message
        message_id = await self.sumo_client.send_message(
            api_base=connection.api_base,
            access_token=connection.access_token,
            conversation_id=conversation_id,
            prompt=enriched_prompt,
            timezone=event.get("user_tz", "UTC"),
        )

        # Poll and stream response
        await self._poll_and_respond(
            client=client,
            channel_id=channel_id,
            thread_ts=thread_ts,
            connection=connection,
            conversation_id=conversation_id,
            message_id=message_id,
        )

    async def _poll_and_respond(
        self,
        client: AsyncWebClient,
        channel_id: str,
        thread_ts: str,
        connection,
        conversation_id: str,
        message_id: str,
    ) -> None:
        last_content = ""
        stream_id = None
        elapsed = 0.0

        try:
            while elapsed < settings.poll_timeout_seconds:
                poll_response = await self.sumo_client.poll_response(
                    api_base=connection.api_base,
                    access_token=connection.access_token,
                    conversation_id=conversation_id,
                    message_id=message_id,
                )

                # Handle status updates
                status_text = poll_response.get_status_text()
                if status_text and poll_response.status == PollStatus.IN_PROGRESS:
                    await client.assistant_threads_setStatus(
                        channel_id=channel_id,
                        thread_ts=thread_ts,
                        status=status_text,
                    )

                # Handle answer content
                answer_text = poll_response.get_answer_text()
                if answer_text:
                    formatted = markdown_to_slack_mrkdwn(answer_text)

                    if stream_id is None:
                        # Start streaming
                        stream_resp = await client.chat_startStream(
                            channel=channel_id,
                            thread_ts=thread_ts,
                        )
                        stream_id = stream_resp.get("stream_id")

                    # Compute delta from formatted content
                    delta = formatted[len(last_content):]
                    if delta and stream_id:
                        await client.chat_appendStream(
                            stream_id=stream_id,
                            channel=channel_id,
                            chunks=[{"type": "markdown_text", "value": delta}],
                        )
                    last_content = formatted

                # Check terminal states
                if poll_response.status == PollStatus.SUCCESS:
                    if stream_id:
                        await client.chat_stopStream(stream_id=stream_id, channel=channel_id)
                    elif answer_text:
                        final_text = truncate_for_slack(markdown_to_slack_mrkdwn(answer_text))
                        await client.chat_postMessage(
                            channel=channel_id,
                            thread_ts=thread_ts,
                            text=final_text,
                        )
                    break

                if poll_response.status == PollStatus.FAILED:
                    error_msg = poll_response.failure_reason or "Unknown error"
                    if stream_id:
                        await client.chat_stopStream(stream_id=stream_id, channel=channel_id)
                    await client.chat_postMessage(
                        channel=channel_id,
                        thread_ts=thread_ts,
                        text=f"Sorry, something went wrong: {error_msg}",
                    )
                    break

                await asyncio.sleep(settings.poll_interval_seconds)
                elapsed += settings.poll_interval_seconds

            else:
                # Timeout
                if stream_id:
                    await client.chat_stopStream(stream_id=stream_id, channel=channel_id)
                await client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=thread_ts,
                    text="Request timed out. Please try a simpler question or check Sumo Logic directly.",
                )

        finally:
            # Always clear status
            try:
                await client.assistant_threads_setStatus(
                    channel_id=channel_id,
                    thread_ts=thread_ts,
                    status="",
                )
            except Exception:
                pass

    async def _refresh_token(self, user_id: str, connection) -> object | None:
        try:
            deployment = get_deployment(connection.deployment)
            token_data = await self.oauth.refresh_token(deployment, connection.refresh_token)
            connection.access_token = token_data["access_token"]
            connection.refresh_token = token_data.get("refresh_token", connection.refresh_token)
            connection.token_expires_at = datetime.now(timezone.utc) + \
                __import__("datetime").timedelta(seconds=token_data.get("expires_in", 300))
            await self.store.save_connection(user_id, connection)
            return connection
        except Exception as e:
            logger.error(f"Token refresh failed for user {user_id}: {e}")
            return None

    async def _send_connect_prompt(self, client, channel_id, user_id, thread_ts):
        await client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            thread_ts=thread_ts,
            text="You haven't connected a Sumo Logic account yet.",
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "You haven't connected a Sumo Logic account yet. Click below to get started.",
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Connect Sumo Logic"},
                            "url": f"{settings.oauth_callback_url.rsplit('/callback', 1)[0]}/start?slack_user_id={user_id}&deployment=us1",
                            "action_id": "connect_sumo",
                            "style": "primary",
                        }
                    ],
                },
            ],
        )

    async def _send_agent_picker(self, client, channel_id, user_id, thread_ts, agents):
        options = [
            {
                "text": {"type": "plain_text", "text": agent.name},
                "value": agent.agent_id,
                "description": {"type": "plain_text", "text": agent.description[:75]} if agent.description else None,
            }
            for agent in (agents or [])
        ]
        # Remove None descriptions
        for opt in options:
            if opt["description"] is None:
                del opt["description"]

        await client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            thread_ts=thread_ts,
            text="Which agent would you like to use?",
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "You have multiple agents available. Which one should I use?",
                    },
                    "accessory": {
                        "type": "static_select",
                        "placeholder": {"type": "plain_text", "text": "Select an agent"},
                        "options": options,
                        "action_id": "select_agent",
                    },
                },
            ],
        )
