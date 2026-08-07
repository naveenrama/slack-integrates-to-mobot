from dataclasses import dataclass
from enum import Enum


class PollStatus(str, Enum):
    IN_PROGRESS = "IN_PROGRESS"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


@dataclass
class AgentResponseItem:
    type: str  # "Status" or "Answer"
    data: str


@dataclass
class PollResponse:
    status: PollStatus
    agent_response: list[AgentResponseItem]
    failure_reason: str | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "PollResponse":
        items = [
            AgentResponseItem(type=item["type"], data=item["data"])
            for item in data.get("agentResponse", [])
        ]
        return cls(
            status=PollStatus(data["status"]),
            agent_response=items,
            failure_reason=data.get("failureReason"),
        )

    def get_answer_text(self, user_timezone: str = "UTC") -> str | None:
        import re, json
        self._user_timezone = user_timezone
        for item in self.agent_response:
            if item.type == "Answer":
                text = item.data

                # Find ALL markdown blocks and ALL json blocks
                md_blocks = re.findall(r"<markdown>([\s\S]*?)</markdown>", text)
                json_blocks = re.findall(r"<json>([\s\S]*?)</json>", text)

                # Also check for unclosed trailing markdown (still streaming)
                trailing_md = re.search(r"<markdown>([\s\S]*?)$", text)
                if not md_blocks and trailing_md and "<json>" not in trailing_md.group(1):
                    md_blocks = [trailing_md.group(1)]

                parts = []

                # Show query info from JSON blocks
                for json_str in json_blocks:
                    try:
                        data = json.loads(json_str)
                        title = data.get("queryTitle", "")
                        query = data.get("logQuery") or data.get("metricsQuery", "")
                        start_ms = data.get("startTime")
                        end_ms = data.get("endTime")
                        time_range = ""
                        if start_ms and end_ms:
                            from datetime import datetime, timezone as tz
                            try:
                                import zoneinfo
                                user_tz = zoneinfo.ZoneInfo(self._user_timezone) if hasattr(self, '_user_timezone') and self._user_timezone else tz.utc
                            except Exception:
                                user_tz = tz.utc
                            start = datetime.fromtimestamp(start_ms / 1000, tz=user_tz).strftime("%Y-%m-%d %I:%M %p %Z")
                            end = datetime.fromtimestamp(end_ms / 1000, tz=user_tz).strftime("%Y-%m-%d %I:%M %p %Z")
                            time_range = f"\n_Time range: {start} → {end}_"
                        if title and query:
                            parts.append(f"*{title}*{time_range}\n```\n{query}\n```")
                        elif title:
                            parts.append(f"*{title}*{time_range}")
                    except (json.JSONDecodeError, KeyError):
                        pass

                # Show all markdown blocks
                for md in md_blocks:
                    if md.strip():
                        parts.append(md.strip())

                if parts:
                    return "\n\n".join(parts)

                # No tags at all — return raw text
                if text.strip() and not text.startswith("<"):
                    return text.strip()

                return None
        return None

    def get_status_text(self) -> str | None:
        for item in self.agent_response:
            if item.type == "Status":
                return item.data
        return None
