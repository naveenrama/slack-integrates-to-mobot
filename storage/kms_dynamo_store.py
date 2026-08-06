"""
Production token store using DynamoDB + KMS envelope encryption.

Implements FedRAMP/HIPAA-grade encryption:
- AWS KMS CMK (FIPS 140-2 Level 3) for key management
- Per-user Data Encryption Keys (DEKs)
- AES-256-GCM for token encryption at rest
- CloudTrail audit trail for all key operations

Not used in local development — see sqlite_store.py for local dev.
"""

import base64
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import boto3
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from storage.base import TokenStore
from storage.models import Connection, Agent, ChannelMapping, UserConfig, ConversationKey

logger = logging.getLogger(__name__)


class KmsDynamoTokenStore(TokenStore):
    def __init__(self, table_name: str, kms_key_arn: str, region: str = "us-east-1"):
        self.table_name = table_name
        self.kms_key_arn = kms_key_arn
        self.kms = boto3.client("kms", region_name=region)
        self.dynamodb = boto3.resource("dynamodb", region_name=region)
        self.table = self.dynamodb.Table(table_name)

    async def initialize(self) -> None:
        pass

    def _generate_dek(self) -> tuple[bytes, bytes]:
        response = self.kms.generate_data_key(
            KeyId=self.kms_key_arn,
            KeySpec="AES_256",
        )
        return response["Plaintext"], response["CiphertextBlob"]

    def _decrypt_dek(self, encrypted_dek: bytes) -> bytes:
        response = self.kms.decrypt(
            CiphertextBlob=encrypted_dek,
            KeyId=self.kms_key_arn,
        )
        return response["Plaintext"]

    def _encrypt_value(self, plaintext: str) -> dict:
        plaintext_dek, encrypted_dek = self._generate_dek()
        aesgcm = AESGCM(plaintext_dek)
        iv = os.urandom(12)
        ciphertext = aesgcm.encrypt(iv, plaintext.encode(), None)
        return {
            "encrypted_dek": base64.b64encode(encrypted_dek).decode(),
            "ciphertext": base64.b64encode(ciphertext).decode(),
            "iv": base64.b64encode(iv).decode(),
            "kms_key_id": self.kms_key_arn,
            "encrypted_at": datetime.now(timezone.utc).isoformat(),
        }

    def _decrypt_value(self, envelope: dict) -> str:
        encrypted_dek = base64.b64decode(envelope["encrypted_dek"])
        ciphertext = base64.b64decode(envelope["ciphertext"])
        iv = base64.b64decode(envelope["iv"])
        plaintext_dek = self._decrypt_dek(encrypted_dek)
        aesgcm = AESGCM(plaintext_dek)
        return aesgcm.decrypt(iv, ciphertext, None).decode()

    async def get_connections(self, slack_user_id: str) -> list[Connection]:
        response = self.table.query(
            KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
            ExpressionAttributeValues={
                ":pk": f"USER#{slack_user_id}",
                ":prefix": "CONN#",
            },
        )
        connections = []
        for item in response.get("Items", []):
            connections.append(Connection(
                id=item["SK"].replace("CONN#", ""),
                label=item["label"],
                deployment=item["deployment"],
                api_base=item["api_base"],
                access_token=self._decrypt_value(json.loads(item["access_token_envelope"])),
                refresh_token=self._decrypt_value(json.loads(item["refresh_token_envelope"])),
                token_expires_at=datetime.fromisoformat(item["token_expires_at"]),
                org_id=item.get("org_id", ""),
                scopes=json.loads(item.get("scopes", "[]")),
            ))
        return connections

    async def save_connection(self, slack_user_id: str, connection: Connection) -> None:
        self.table.put_item(Item={
            "PK": f"USER#{slack_user_id}",
            "SK": f"CONN#{connection.id}",
            "label": connection.label,
            "deployment": connection.deployment,
            "api_base": connection.api_base,
            "access_token_envelope": json.dumps(self._encrypt_value(connection.access_token)),
            "refresh_token_envelope": json.dumps(self._encrypt_value(connection.refresh_token)),
            "token_expires_at": connection.token_expires_at.isoformat(),
            "org_id": connection.org_id,
            "scopes": json.dumps(connection.scopes),
        })

    async def delete_connection(self, slack_user_id: str, connection_id: str) -> None:
        self.table.delete_item(Key={
            "PK": f"USER#{slack_user_id}",
            "SK": f"CONN#{connection_id}",
        })

    async def get_agents(self, connection_id: str) -> list[Agent]:
        response = self.table.query(
            KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
            ExpressionAttributeValues={
                ":pk": f"CONN#{connection_id}",
                ":prefix": "AGENT#",
            },
        )
        return [
            Agent(
                id=item["SK"].replace("AGENT#", ""),
                connection_id=connection_id,
                agent_id=item["agent_id"],
                name=item["name"],
                description=item.get("description", ""),
            )
            for item in response.get("Items", [])
        ]

    async def save_agents(self, connection_id: str, agents: list[Agent]) -> None:
        with self.table.batch_writer() as batch:
            # Delete existing
            response = self.table.query(
                KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
                ExpressionAttributeValues={
                    ":pk": f"CONN#{connection_id}",
                    ":prefix": "AGENT#",
                },
            )
            for item in response.get("Items", []):
                batch.delete_item(Key={"PK": item["PK"], "SK": item["SK"]})

            # Write new
            for agent in agents:
                batch.put_item(Item={
                    "PK": f"CONN#{connection_id}",
                    "SK": f"AGENT#{agent.id}",
                    "agent_id": agent.agent_id,
                    "name": agent.name,
                    "description": agent.description,
                })

    async def get_user_config(self, slack_user_id: str) -> UserConfig:
        response = self.table.get_item(Key={
            "PK": f"USER#{slack_user_id}",
            "SK": "META",
        })
        item = response.get("Item")
        if not item:
            return UserConfig(slack_user_id=slack_user_id)
        return UserConfig(
            slack_user_id=slack_user_id,
            default_connection_id=item.get("default_connection_id"),
            default_agent_id=item.get("default_agent_id"),
        )

    async def set_user_config(self, slack_user_id: str, config: UserConfig) -> None:
        self.table.put_item(Item={
            "PK": f"USER#{slack_user_id}",
            "SK": "META",
            "default_connection_id": config.default_connection_id,
            "default_agent_id": config.default_agent_id,
        })

    async def get_channel_mapping(self, channel_id: str) -> ChannelMapping | None:
        response = self.table.get_item(Key={
            "PK": f"CHANNEL#{channel_id}",
            "SK": "MAPPING",
        })
        item = response.get("Item")
        if not item:
            return None
        return ChannelMapping(
            channel_id=channel_id,
            connection_id=item["connection_id"],
            agent_id=item["agent_id"],
        )

    async def set_channel_mapping(self, mapping: ChannelMapping) -> None:
        self.table.put_item(Item={
            "PK": f"CHANNEL#{mapping.channel_id}",
            "SK": "MAPPING",
            "connection_id": mapping.connection_id,
            "agent_id": mapping.agent_id,
        })

    async def get_conversation_id(self, key: ConversationKey) -> str | None:
        response = self.table.get_item(Key={
            "PK": f"USER#{key.slack_user_id}",
            "SK": f"CONV#{key.connection_id}#{key.agent_id}#{key.thread_ts}",
        })
        item = response.get("Item")
        return item.get("conversation_id") if item else None

    async def set_conversation_id(self, key: ConversationKey, conversation_id: str) -> None:
        self.table.put_item(Item={
            "PK": f"USER#{key.slack_user_id}",
            "SK": f"CONV#{key.connection_id}#{key.agent_id}#{key.thread_ts}",
            "conversation_id": conversation_id,
        })
