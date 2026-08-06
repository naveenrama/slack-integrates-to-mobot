import json
from datetime import datetime

import aiosqlite
from cryptography.fernet import Fernet

from storage.base import TokenStore
from storage.models import Connection, Agent, ChannelMapping, UserConfig, ConversationKey


class SQLiteTokenStore(TokenStore):
    def __init__(self, db_path: str, encryption_key: str):
        self.db_path = db_path
        self.fernet = Fernet(encryption_key.encode())
        self._db: aiosqlite.Connection | None = None

    def _encrypt(self, value: str) -> str:
        return self.fernet.encrypt(value.encode()).decode()

    def _decrypt(self, value: str) -> str:
        return self.fernet.decrypt(value.encode()).decode()

    async def _get_db(self) -> aiosqlite.Connection:
        if self._db is None:
            self._db = await aiosqlite.connect(self.db_path)
        return self._db

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def initialize(self) -> None:
        db = await self._get_db()
        await db.execute("""
            CREATE TABLE IF NOT EXISTS connections (
                id TEXT NOT NULL,
                slack_user_id TEXT NOT NULL,
                label TEXT NOT NULL,
                deployment TEXT NOT NULL,
                api_base TEXT NOT NULL,
                access_token TEXT NOT NULL,
                refresh_token TEXT NOT NULL,
                token_expires_at TEXT NOT NULL,
                org_id TEXT DEFAULT '',
                scopes TEXT DEFAULT '[]',
                PRIMARY KEY (slack_user_id, id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS agents (
                id TEXT NOT NULL,
                connection_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                PRIMARY KEY (connection_id, id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_config (
                slack_user_id TEXT PRIMARY KEY,
                default_connection_id TEXT,
                default_agent_id TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS channel_mappings (
                channel_id TEXT PRIMARY KEY,
                connection_id TEXT NOT NULL,
                agent_id TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                slack_user_id TEXT NOT NULL,
                connection_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                thread_ts TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                PRIMARY KEY (slack_user_id, connection_id, agent_id, thread_ts)
            )
        """)
        await db.commit()

    async def get_connections(self, slack_user_id: str) -> list[Connection]:
        db = await self._get_db()
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM connections WHERE slack_user_id = ?",
            (slack_user_id,),
        )
        rows = await cursor.fetchall()
        return [
            Connection(
                id=row["id"],
                label=row["label"],
                deployment=row["deployment"],
                api_base=row["api_base"],
                access_token=self._decrypt(row["access_token"]),
                refresh_token=self._decrypt(row["refresh_token"]),
                token_expires_at=datetime.fromisoformat(row["token_expires_at"]),
                org_id=row["org_id"],
                scopes=json.loads(row["scopes"]),
            )
            for row in rows
        ]

    async def save_connection(self, slack_user_id: str, connection: Connection) -> None:
        db = await self._get_db()
        await db.execute(
            """INSERT OR REPLACE INTO connections
               (id, slack_user_id, label, deployment, api_base, access_token,
                refresh_token, token_expires_at, org_id, scopes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                connection.id,
                slack_user_id,
                connection.label,
                connection.deployment,
                connection.api_base,
                self._encrypt(connection.access_token),
                self._encrypt(connection.refresh_token),
                connection.token_expires_at.isoformat(),
                connection.org_id,
                json.dumps(connection.scopes),
            ),
        )
        await db.commit()

    async def delete_connection(self, slack_user_id: str, connection_id: str) -> None:
        db = await self._get_db()
        await db.execute(
            "DELETE FROM connections WHERE slack_user_id = ? AND id = ?",
            (slack_user_id, connection_id),
        )
        await db.execute(
            "DELETE FROM agents WHERE connection_id = ?",
            (connection_id,),
        )
        await db.commit()

    async def get_agents(self, connection_id: str) -> list[Agent]:
        db = await self._get_db()
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM agents WHERE connection_id = ?",
            (connection_id,),
        )
        rows = await cursor.fetchall()
        return [
            Agent(
                id=row["id"],
                connection_id=row["connection_id"],
                agent_id=row["agent_id"],
                name=row["name"],
                description=row["description"],
            )
            for row in rows
        ]

    async def save_agents(self, connection_id: str, agents: list[Agent]) -> None:
        db = await self._get_db()
        await db.execute(
            "DELETE FROM agents WHERE connection_id = ?",
            (connection_id,),
        )
        for agent in agents:
            await db.execute(
                """INSERT INTO agents (id, connection_id, agent_id, name, description)
                   VALUES (?, ?, ?, ?, ?)""",
                (agent.id, connection_id, agent.agent_id, agent.name, agent.description),
            )
        await db.commit()

    async def get_user_config(self, slack_user_id: str) -> UserConfig:
        db = await self._get_db()
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM user_config WHERE slack_user_id = ?",
            (slack_user_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return UserConfig(slack_user_id=slack_user_id)
        return UserConfig(
            slack_user_id=row["slack_user_id"],
            default_connection_id=row["default_connection_id"],
            default_agent_id=row["default_agent_id"],
        )

    async def set_user_config(self, slack_user_id: str, config: UserConfig) -> None:
        db = await self._get_db()
        await db.execute(
            """INSERT OR REPLACE INTO user_config
               (slack_user_id, default_connection_id, default_agent_id)
               VALUES (?, ?, ?)""",
            (slack_user_id, config.default_connection_id, config.default_agent_id),
        )
        await db.commit()

    async def get_channel_mapping(self, channel_id: str) -> ChannelMapping | None:
        db = await self._get_db()
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM channel_mappings WHERE channel_id = ?",
            (channel_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return ChannelMapping(
            channel_id=row["channel_id"],
            connection_id=row["connection_id"],
            agent_id=row["agent_id"],
        )

    async def set_channel_mapping(self, mapping: ChannelMapping) -> None:
        db = await self._get_db()
        await db.execute(
            """INSERT OR REPLACE INTO channel_mappings
               (channel_id, connection_id, agent_id) VALUES (?, ?, ?)""",
            (mapping.channel_id, mapping.connection_id, mapping.agent_id),
        )
        await db.commit()

    async def get_conversation_id(self, key: ConversationKey) -> str | None:
        db = await self._get_db()
        cursor = await db.execute(
            """SELECT conversation_id FROM conversations
               WHERE slack_user_id = ? AND connection_id = ? AND agent_id = ? AND thread_ts = ?""",
            (key.slack_user_id, key.connection_id, key.agent_id, key.thread_ts),
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    async def set_conversation_id(self, key: ConversationKey, conversation_id: str) -> None:
        db = await self._get_db()
        await db.execute(
            """INSERT OR REPLACE INTO conversations
               (slack_user_id, connection_id, agent_id, thread_ts, conversation_id)
               VALUES (?, ?, ?, ?, ?)""",
            (key.slack_user_id, key.connection_id, key.agent_id, key.thread_ts, conversation_id),
        )
        await db.commit()
