from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Connection:
    id: str
    label: str
    deployment: str
    api_base: str
    access_token: str
    refresh_token: str
    token_expires_at: datetime
    org_id: str = ""
    scopes: list[str] = field(default_factory=list)


@dataclass
class Agent:
    id: str
    connection_id: str
    agent_id: str
    name: str
    description: str = ""


@dataclass
class ChannelMapping:
    channel_id: str
    connection_id: str
    agent_id: str


@dataclass
class UserConfig:
    slack_user_id: str
    default_connection_id: str | None = None
    default_agent_id: str | None = None


@dataclass
class ConversationKey:
    slack_user_id: str
    connection_id: str
    agent_id: str
    thread_ts: str
