from abc import ABC, abstractmethod

from storage.models import Connection, Agent, ChannelMapping, UserConfig, ConversationKey


class TokenStore(ABC):
    @abstractmethod
    async def initialize(self) -> None:
        ...

    @abstractmethod
    async def get_connections(self, slack_user_id: str) -> list[Connection]:
        ...

    @abstractmethod
    async def save_connection(self, slack_user_id: str, connection: Connection) -> None:
        ...

    @abstractmethod
    async def delete_connection(self, slack_user_id: str, connection_id: str) -> None:
        ...

    @abstractmethod
    async def get_agents(self, connection_id: str) -> list[Agent]:
        ...

    @abstractmethod
    async def save_agents(self, connection_id: str, agents: list[Agent]) -> None:
        ...

    @abstractmethod
    async def get_user_config(self, slack_user_id: str) -> UserConfig:
        ...

    @abstractmethod
    async def set_user_config(self, slack_user_id: str, config: UserConfig) -> None:
        ...

    @abstractmethod
    async def get_channel_mapping(self, channel_id: str) -> ChannelMapping | None:
        ...

    @abstractmethod
    async def set_channel_mapping(self, mapping: ChannelMapping) -> None:
        ...

    @abstractmethod
    async def get_conversation_id(self, key: ConversationKey) -> str | None:
        ...

    @abstractmethod
    async def set_conversation_id(self, key: ConversationKey, conversation_id: str) -> None:
        ...
