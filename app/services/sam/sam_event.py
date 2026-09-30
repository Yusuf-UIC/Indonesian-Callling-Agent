import time
import uuid
from typing import Dict, Any, Optional
from dataclasses import dataclass, field
from enum import Enum


class SAMAgentType(str, Enum):
    ORCHESTRATOR = "orchestrator"
    ACCOUNT_OPERATIONS = "account_operations"
    CARD_SECURITY = "card_security"


@dataclass
class SAMHeader:
    session_id: str
    correlation_id: str = field(default_factory=lambda: f"sam-{uuid.uuid4().hex[:8]}")
    sender_agent: str = SAMAgentType.ORCHESTRATOR.value
    target_agent: str = "broadcast"
    timestamp: float = field(default_factory=time.time)


@dataclass
class SAMEvent:
    header: SAMHeader
    topic: str
    intent: str
    payload: Dict[str, Any]
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "header": {
                "session_id": self.header.session_id,
                "correlation_id": self.header.correlation_id,
                "sender_agent": self.header.sender_agent,
                "target_agent": self.header.target_agent,
                "timestamp": self.header.timestamp,
            },
            "topic": self.topic,
            "intent": self.intent,
            "payload": self.payload,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SAMEvent":
        header_data = data.get("header", {})
        header = SAMHeader(
            session_id=header_data.get("session_id", "default_session"),
            correlation_id=header_data.get("correlation_id", f"sam-{uuid.uuid4().hex[:8]}"),
            sender_agent=header_data.get("sender_agent", SAMAgentType.ORCHESTRATOR.value),
            target_agent=header_data.get("target_agent", "broadcast"),
            timestamp=header_data.get("timestamp", time.time()),
        )
        return cls(
            header=header,
            topic=data.get("topic", ""),
            intent=data.get("intent", "unknown"),
            payload=data.get("payload", {}),
            error=data.get("error"),
        )
