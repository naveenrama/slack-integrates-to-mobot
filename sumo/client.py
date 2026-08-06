import logging

import aiohttp

from sumo.models import PollResponse

logger = logging.getLogger(__name__)


class SumoClient:
    def __init__(self):
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    def _headers(self, access_token: str) -> dict:
        return {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "*/*",
        }

    async def create_conversation(self, api_base: str, access_token: str) -> str:
        session = await self._get_session()
        url = f"{api_base}/api/v1/copilot/conversation"
        async with session.post(url, json={}, headers=self._headers(access_token)) as resp:
            resp.raise_for_status()
            data = await resp.json()
            conversation_id = data["id"]
            logger.debug(f"Created conversation: {conversation_id}")
            return conversation_id

    async def send_message(
        self,
        api_base: str,
        access_token: str,
        conversation_id: str,
        prompt: str,
        timezone: str = "UTC",
    ) -> str:
        session = await self._get_session()
        url = f"{api_base}/api/v2/copilot/conversation/{conversation_id}/message"
        payload = {"userPrompt": prompt, "userTimezone": timezone}
        async with session.post(url, json=payload, headers=self._headers(access_token)) as resp:
            resp.raise_for_status()
            data = await resp.json()
            message_id = data["messageId"]
            logger.debug(f"Sent message, got ID: {message_id}")
            return message_id

    async def poll_response(
        self,
        api_base: str,
        access_token: str,
        conversation_id: str,
        message_id: str,
    ) -> PollResponse:
        session = await self._get_session()
        url = f"{api_base}/api/v2/copilot/conversation/{conversation_id}/message/{message_id}/poll"
        async with session.get(url, headers=self._headers(access_token)) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return PollResponse.from_dict(data)
