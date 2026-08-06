import re
import logging
from datetime import datetime, timezone

from slack_sdk.web.async_client import AsyncWebClient

logger = logging.getLogger(__name__)

USER_MENTION_RE = re.compile(r"<@(U[A-Z0-9]+)>")
CHANNEL_MENTION_RE = re.compile(r"<#(C[A-Z0-9]+)\|?([^>]*)>")


class ContextEnricher:
    def __init__(self, client: AsyncWebClient):
        self.client = client
        self._user_cache: dict[str, dict] = {}
        self._channel_cache: dict[str, dict] = {}
        self._cache_ttl = 300  # 5 minutes

    async def _get_user_info(self, user_id: str) -> dict:
        cached = self._user_cache.get(user_id)
        if cached and (datetime.now(timezone.utc) - cached["_fetched_at"]).seconds < self._cache_ttl:
            return cached

        try:
            resp = await self.client.users_info(user=user_id)
            user = resp["user"]
            info = {
                "id": user_id,
                "real_name": user.get("real_name", user.get("name", "Unknown")),
                "email": user.get("profile", {}).get("email", ""),
                "title": user.get("profile", {}).get("title", ""),
                "team": user.get("profile", {}).get("team", ""),
                "tz": user.get("tz", "UTC"),
                "_fetched_at": datetime.now(timezone.utc),
            }
            self._user_cache[user_id] = info
            return info
        except Exception as e:
            logger.warning(f"Failed to fetch user info for {user_id}: {e}")
            return {"id": user_id, "real_name": "Unknown User", "email": "", "title": "", "team": "", "tz": "UTC"}

    async def _get_channel_info(self, channel_id: str) -> dict:
        cached = self._channel_cache.get(channel_id)
        if cached and (datetime.now(timezone.utc) - cached["_fetched_at"]).seconds < self._cache_ttl:
            return cached

        try:
            resp = await self.client.conversations_info(channel=channel_id)
            channel = resp["channel"]
            info = {
                "id": channel_id,
                "name": channel.get("name", ""),
                "topic": channel.get("topic", {}).get("value", ""),
                "purpose": channel.get("purpose", {}).get("value", ""),
                "is_im": channel.get("is_im", False),
                "_fetched_at": datetime.now(timezone.utc),
            }
            self._channel_cache[channel_id] = info
            return info
        except Exception as e:
            logger.warning(f"Failed to fetch channel info for {channel_id}: {e}")
            return {"id": channel_id, "name": "unknown", "topic": "", "purpose": "", "is_im": False}

    async def _get_thread_history(self, channel_id: str, thread_ts: str, limit: int = 20) -> list[dict]:
        try:
            resp = await self.client.conversations_replies(
                channel=channel_id,
                ts=thread_ts,
                limit=limit,
            )
            messages = []
            for msg in resp.get("messages", [])[:-1]:  # exclude the current message
                user_info = await self._get_user_info(msg.get("user", ""))
                messages.append({
                    "author": user_info["real_name"],
                    "text": msg.get("text", ""),
                    "ts": msg.get("ts", ""),
                })
            return messages
        except Exception as e:
            logger.warning(f"Failed to fetch thread history: {e}")
            return []

    async def resolve_mentions(self, text: str) -> str:
        resolved = text
        for user_id in USER_MENTION_RE.findall(text):
            user_info = await self._get_user_info(user_id)
            resolved = resolved.replace(f"<@{user_id}>", user_info["real_name"])

        for match in CHANNEL_MENTION_RE.finditer(text):
            channel_id = match.group(1)
            channel_info = await self._get_channel_info(channel_id)
            resolved = resolved.replace(match.group(0), f"#{channel_info['name']}")

        return resolved

    async def build_enriched_prompt(
        self,
        text: str,
        sender_id: str,
        channel_id: str,
        thread_ts: str | None = None,
        bot_user_id: str | None = None,
    ) -> str:
        sender = await self._get_user_info(sender_id)
        channel = await self._get_channel_info(channel_id)

        # Strip bot mention from text
        clean_text = text
        if bot_user_id:
            clean_text = re.sub(f"<@{bot_user_id}>\\s*", "", clean_text).strip()

        resolved_text = await self.resolve_mentions(clean_text)

        # System preamble — instructs the agent to format for Slack
        parts = [SLACK_FORMATTING_PREAMBLE]
        parts.append("")
        parts.append("[CONTEXT]")
        parts.append(f"Requester: {sender['real_name']}")
        if sender["title"]:
            parts.append(f"Role: {sender['title']}")
        if sender["email"]:
            parts.append(f"Email: {sender['email']}")
        parts.append(f"Timezone: {sender['tz']}")

        if not channel["is_im"]:
            channel_desc = f"Channel: #{channel['name']}"
            if channel["purpose"]:
                channel_desc += f" (purpose: {channel['purpose']})"
            parts.append(channel_desc)

        # Thread context
        if thread_ts:
            history = await self._get_thread_history(channel_id, thread_ts)
            if history:
                participants = list({msg["author"] for msg in history})
                parts.append(f"Thread participants: {', '.join(participants)}")
                parts.append("")
                parts.append("[THREAD HISTORY]")
                for msg in history[-10:]:  # last 10 messages
                    parts.append(f"{msg['author']}: {msg['text']}")

        parts.append("")
        parts.append("[MESSAGE]")
        parts.append(resolved_text)

        return "\n".join(parts)


SLACK_FORMATTING_PREAMBLE = """[FORMATTING PREFERENCES]
I'm asking this from Slack, so please format your reply using Slack mrkdwn:
- Bold: *text* (single asterisk, not double)
- Italic: _text_
- Code blocks: ``` with no language specifier
- Links: <https://url.com|display text>
- Use bullet points (- ) for lists
- No # headings (they don't render in Slack) — use *bold text* as section labels instead
- Keep it concise and scannable — I'm reading this in a chat thread
- Wrap any Sumo queries in code blocks
- Bold key findings or action items"""
