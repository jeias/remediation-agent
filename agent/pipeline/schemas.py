from typing import Literal

from pydantic import BaseModel, Field


class SummarizationOutput(BaseModel):
    error_type: Literal[
        "connection_error", "query_error", "import_error",
        "syntax_error", "runtime_error", "timeout", "unknown"
    ]
    first_seen: str | None = None
    frequency: int = Field(ge=0)
    affected_service: str
    key_logs: list[str] = Field(max_length=5)


class ClassificationOutput(BaseModel):
    reasoning: str = Field(description="Step-by-step reasoning before classification")
    type: Literal["deployment", "infrastructure", "transient"]
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    confidence: float = Field(ge=0.0, le=1.0)
    recommended_action: Literal["rollback", "escalate", "none"]
    summary: str


class RemediationOutput(BaseModel):
    action_taken: Literal["rollback", "escalated", "notify_only"]
    confidence_accepted: bool
    details: str
