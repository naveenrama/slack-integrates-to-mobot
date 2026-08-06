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

    def get_answer_text(self) -> str | None:
        for item in self.agent_response:
            if item.type == "Answer":
                text = item.data
                if text.startswith("<markdown>") and text.endswith("</markdown>"):
                    text = text[len("<markdown>"):-len("</markdown>")]
                return text
        return None

    def get_status_text(self) -> str | None:
        for item in self.agent_response:
            if item.type == "Status":
                return item.data
        return None
