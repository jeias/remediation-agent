import json
import secrets
import time
from datetime import datetime, timezone


def generate_trace_id() -> str:
    now = datetime.now(timezone.utc)
    suffix = secrets.token_hex(2)
    return f"inc-{now.strftime('%Y%m%d-%H%M%S')}-{suffix}"


class TraceLogger:
    def __init__(self, trace_id: str):
        self.trace_id = trace_id
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self._start_time = time.monotonic()

    def _emit(self, **fields):
        record = {
            "trace_id": self.trace_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **fields,
        }
        print(json.dumps(record, default=str))

    def log_event(self, step: str, detail):
        self._emit(step=step, detail=detail)

    def log_tool_call(self, agent: str, tool_name: str, tool_input: dict):
        self._emit(agent=agent, step="tool_call", tool_name=tool_name, tool_input=tool_input)

    def log_tool_result(self, agent: str, tool_name: str, result_preview: str):
        self._emit(agent=agent, step="tool_result", tool_name=tool_name,
                   result_preview=result_preview[:200])

    def log_token_usage(self, agent: str, model: str, usage):
        input_tokens = usage.input_tokens
        output_tokens = usage.output_tokens
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self._emit(agent=agent, step="token_usage", model=model,
                   input_tokens=input_tokens, output_tokens=output_tokens)

    def log_reasoning(self, agent: str, reasoning: str):
        self._emit(agent=agent, step="reasoning", content=reasoning)

    def log_agent_response(self, agent: str, output: dict):
        self._emit(agent=agent, step="agent_output", output=output)

    def log_incident_summary(self, summary, classification, remediation, dry_run: bool):
        elapsed = int((time.monotonic() - self._start_time) * 1000)
        self._emit(
            step="incident_summary",
            total_input_tokens=self.total_input_tokens,
            total_output_tokens=self.total_output_tokens,
            total_duration_ms=elapsed,
            classification=classification.type,
            severity=classification.severity,
            confidence=classification.confidence,
            confidence_accepted=remediation.confidence_accepted,
            action_taken=remediation.action_taken,
            dry_run=dry_run,
        )
