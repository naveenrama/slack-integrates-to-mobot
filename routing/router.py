import re
import logging
from dataclasses import dataclass

from storage.base import TokenStore
from storage.models import Connection, Agent

logger = logging.getLogger(__name__)

COMMAND_RE = re.compile(r"^use\s+(\S+)", re.IGNORECASE)


@dataclass
class RouteResult:
    connection: Connection
    agent: Agent | None
    needs_selection: bool = False
    available_agents: list[Agent] | None = None


class AgentRouter:
    def __init__(self, store: TokenStore):
        self.store = store

    async def route(
        self,
        slack_user_id: str,
        channel_id: str,
        text: str,
    ) -> RouteResult | None:
        connections = await self.store.get_connections(slack_user_id)
        if not connections:
            return None

        # 1. Check for explicit "use <agent>" command in text
        explicit_agent = self._parse_explicit_agent(text)

        # 2. Check channel mapping
        channel_mapping = await self.store.get_channel_mapping(channel_id)

        # 3. Get user defaults
        user_config = await self.store.get_user_config(slack_user_id)

        # Resolve connection
        connection = None
        if channel_mapping:
            connection = next(
                (c for c in connections if c.id == channel_mapping.connection_id), None
            )
        if not connection and user_config.default_connection_id:
            connection = next(
                (c for c in connections if c.id == user_config.default_connection_id), None
            )
        if not connection:
            connection = connections[0]

        # Resolve agent
        agents = await self.store.get_agents(connection.id)

        if explicit_agent:
            agent = next(
                (a for a in agents if a.agent_id == explicit_agent or a.name.lower() == explicit_agent.lower()),
                None,
            )
            if agent:
                return RouteResult(connection=connection, agent=agent)

        if channel_mapping:
            agent = next(
                (a for a in agents if a.agent_id == channel_mapping.agent_id), None
            )
            if agent:
                return RouteResult(connection=connection, agent=agent)

        if user_config.default_agent_id:
            agent = next(
                (a for a in agents if a.agent_id == user_config.default_agent_id), None
            )
            if agent:
                return RouteResult(connection=connection, agent=agent)

        # If only one agent available, use it
        if len(agents) == 1:
            return RouteResult(connection=connection, agent=agents[0])

        # If no agents discovered yet, proceed without agent routing
        if not agents:
            return RouteResult(connection=connection, agent=None)

        # Multiple agents, no preference — ask user
        return RouteResult(
            connection=connection,
            agent=None,
            needs_selection=True,
            available_agents=agents,
        )

    def _parse_explicit_agent(self, text: str) -> str | None:
        match = COMMAND_RE.search(text)
        return match.group(1) if match else None
