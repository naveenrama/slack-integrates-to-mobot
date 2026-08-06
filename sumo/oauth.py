import hashlib
import base64
import secrets
import logging
from urllib.parse import urlencode

import aiohttp

from sumo.deployments import Deployment

logger = logging.getLogger(__name__)


class SumoOAuth:
    def __init__(self, cimd_metadata_url: str, callback_url: str):
        self.cimd_metadata_url = cimd_metadata_url
        self.callback_url = callback_url
        self._pending_states: dict[str, dict] = {}

    def generate_pkce(self) -> tuple[str, str]:
        verifier = secrets.token_urlsafe(64)
        digest = hashlib.sha256(verifier.encode()).digest()
        challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        return verifier, challenge

    def build_authorize_url(
        self,
        deployment: Deployment,
        slack_user_id: str,
        scopes: str = "runLogSearch runMetricsQuery viewLibrary",
    ) -> tuple[str, str]:
        state = secrets.token_urlsafe(32)
        verifier, challenge = self.generate_pkce()

        self._pending_states[state] = {
            "slack_user_id": slack_user_id,
            "deployment": deployment.code,
            "verifier": verifier,
        }

        params = {
            "response_type": "code",
            "client_id": self.cimd_metadata_url,
            "redirect_uri": self.callback_url,
            "scope": scopes,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }

        authorize_url = f"{deployment.service_url}/oauth2/authorize?{urlencode(params)}"
        return authorize_url, state

    def validate_state(self, state: str) -> dict | None:
        return self._pending_states.pop(state, None)

    async def exchange_code(
        self,
        deployment: Deployment,
        code: str,
        verifier: str,
    ) -> dict:
        token_url = f"{deployment.service_url}/oauth2/token"
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.callback_url,
            "client_id": self.cimd_metadata_url,
            "code_verifier": verifier,
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(token_url, data=payload) as resp:
                resp.raise_for_status()
                data = await resp.json()
                logger.info(f"Token exchange successful, expires_in={data.get('expires_in')}")
                return data

    async def refresh_token(
        self,
        deployment: Deployment,
        refresh_token: str,
    ) -> dict:
        token_url = f"{deployment.service_url}/oauth2/token"
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.cimd_metadata_url,
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(token_url, data=payload) as resp:
                resp.raise_for_status()
                data = await resp.json()
                logger.debug("Token refreshed successfully")
                return data
