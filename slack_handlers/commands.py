import re
import logging

from slack_bolt.async_app import AsyncApp
from slack_sdk.web.async_client import AsyncWebClient

from config import settings
from storage.base import TokenStore
from storage.models import UserConfig
from sumo.deployments import DEPLOYMENTS

logger = logging.getLogger(__name__)

COMMAND_PATTERNS = {
    "connect": re.compile(r"\bconnect(?:\s+<?([^|>\s]+))?", re.IGNORECASE),
    "disconnect": re.compile(r"\bdisconnect\s+(\S+)", re.IGNORECASE),
    "connections": re.compile(r"\bconnections\b", re.IGNORECASE),
    "use": re.compile(r"\buse\s+(\S+)", re.IGNORECASE),
    "set-default": re.compile(r"\bset-default\s+(\S+)", re.IGNORECASE),
    "help": re.compile(r"\bhelp\b", re.IGNORECASE),
}


class CommandHandler:
    def __init__(self, store: TokenStore):
        self.store = store

    def register(self, app: AsyncApp) -> None:
        @app.action("connect_sumo")
        async def handle_connect_action(ack, body, client):
            await ack()

        @app.action("connect_custom_url")
        async def handle_connect_custom_url(ack, body, client):
            await ack()

        @app.action(re.compile(r"^connect_"))
        async def handle_connect_deployment(ack, body, client):
            await ack()

        @app.action("select_agent")
        async def handle_select_agent(ack, body, client: AsyncWebClient):
            await ack()
            user_id = body["user"]["id"]
            selected = body["actions"][0]["selected_option"]["value"]

            config = await self.store.get_user_config(user_id)
            config.default_agent_id = selected
            await self.store.set_user_config(user_id, config)

            await client.chat_postEphemeral(
                channel=body["channel"]["id"],
                user=user_id,
                text=f"Got it! Using *{selected}* as your default agent.",
            )

    def parse_command(self, text: str, bot_user_id: str) -> tuple[str | None, str | None]:
        clean = re.sub(f"<@{bot_user_id}>\\s*", "", text).strip()

        for cmd, pattern in COMMAND_PATTERNS.items():
            match = pattern.search(clean)
            if match:
                arg = match.group(1) if match.lastindex else None
                return cmd, arg

        return None, None

    async def handle_command(
        self,
        command: str,
        arg: str | None,
        user_id: str,
        channel_id: str,
        thread_ts: str,
        client: AsyncWebClient,
    ) -> bool:
        if command == "help":
            await self._handle_help(client, channel_id, user_id, thread_ts)
            return True
        elif command == "connect":
            await self._handle_connect(client, channel_id, user_id, thread_ts, arg)
            return True
        elif command == "connections":
            await self._handle_connections(client, channel_id, user_id, thread_ts)
            return True
        elif command == "disconnect":
            await self._handle_disconnect(client, channel_id, user_id, thread_ts, arg)
            return True
        elif command == "set-default":
            await self._handle_set_default(client, channel_id, user_id, thread_ts, arg)
            return True
        return False

    async def _handle_help(self, client, channel_id, user_id, thread_ts):
        help_text = """*Mobot Commands:*
• `@mobot connect` — Connect a new Sumo Logic account
• `@mobot connections` — List your connected accounts & agents
• `@mobot use <agent>` — Switch to a specific agent for this conversation
• `@mobot set-default <agent>` — Set your default agent
• `@mobot disconnect <connection-label>` — Remove a connection
• `@mobot help` — Show this message

Or just ask me anything and I'll route to your default agent!"""

        await client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            text=help_text,
        )

    async def _handle_connect(self, client, channel_id, user_id, thread_ts, url_arg=None):
        oauth_base = settings.oauth_callback_url.rsplit("/callback", 1)[0]

        # If user provided a URL directly: @mobot connect https://syddata.long.sumologic.net
        if url_arg and url_arg.startswith("http"):
            from urllib.parse import quote
            connect_url = f"{oauth_base}/start?slack_user_id={user_id}&url={quote(url_arg)}&channel_id={channel_id}&thread_ts={thread_ts}"
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                thread_ts=thread_ts,
                text=f"Connecting to `{url_arg}`...",
                blocks=[
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": f"Connecting to `{url_arg}`"},
                    },
                    {
                        "type": "actions",
                        "elements": [{
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Authorize"},
                            "url": connect_url,
                            "action_id": "connect_custom_url",
                            "style": "primary",
                        }],
                    },
                ],
            )
            return

        deployment_buttons = []
        for code, dep in list(DEPLOYMENTS.items())[:8]:
            deployment_buttons.append({
                "type": "button",
                "text": {"type": "plain_text", "text": dep.name},
                "url": f"{oauth_base}/start?slack_user_id={user_id}&deployment={code}&channel_id={channel_id}&thread_ts={thread_ts}",
                "action_id": f"connect_{code}",
            })

        await client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            thread_ts=thread_ts,
            text="Select your Sumo Logic deployment region or paste your URL:",
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "*Select a standard region:*",
                    },
                },
                {
                    "type": "actions",
                    "elements": deployment_buttons[:4],
                },
                {
                    "type": "actions",
                    "elements": deployment_buttons[4:],
                },
                {"type": "divider"},
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "*Or paste your Sumo Logic URL directly:*\n`@mobot connect https://syddata.long.sumologic.net`",
                    },
                },
            ],
        )

    async def _handle_connections(self, client, channel_id, user_id, thread_ts):
        connections = await self.store.get_connections(user_id)
        if not connections:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                thread_ts=thread_ts,
                text="No connected accounts. Use `@mobot connect` to add one.",
            )
            return

        config = await self.store.get_user_config(user_id)
        lines = ["*Your Sumo Logic Connections:*\n"]

        for conn in connections:
            is_default = " _(default)_" if conn.id == config.default_connection_id else ""
            lines.append(f"• *{conn.label}*{is_default} — {conn.deployment}")

            agents = await self.store.get_agents(conn.id)
            for agent in agents:
                is_agent_default = " _(default)_" if agent.agent_id == config.default_agent_id else ""
                lines.append(f"    └ `{agent.agent_id}` — {agent.name}{is_agent_default}")

        await client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            thread_ts=thread_ts,
            text="\n".join(lines),
        )

    async def _handle_disconnect(self, client, channel_id, user_id, thread_ts, label):
        if not label:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                thread_ts=thread_ts,
                text="Usage: `@mobot disconnect <connection-label>`",
            )
            return

        connections = await self.store.get_connections(user_id)
        conn = next((c for c in connections if c.label.lower() == label.lower() or c.id == label), None)
        if not conn:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                thread_ts=thread_ts,
                text=f"Connection `{label}` not found. Use `@mobot connections` to see your accounts.",
            )
            return

        await self.store.delete_connection(user_id, conn.id)
        await client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            thread_ts=thread_ts,
            text=f"Disconnected *{conn.label}*.",
        )

    async def _handle_set_default(self, client, channel_id, user_id, thread_ts, agent_id):
        if not agent_id:
            await client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                thread_ts=thread_ts,
                text="Usage: `@mobot set-default <agent-id>`",
            )
            return

        config = await self.store.get_user_config(user_id)
        config.default_agent_id = agent_id
        await self.store.set_user_config(user_id, config)

        await client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            thread_ts=thread_ts,
            text=f"Default agent set to `{agent_id}`.",
        )
