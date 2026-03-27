"""
Local test for the remediation agent pipeline.
Requires: ANTHROPIC_API_KEY env var and AWS credentials (profile: dev).

Usage:
  cd agent
  export ANTHROPIC_API_KEY="sk-..."
  export AWS_PROFILE=dev
  uv run python test_local.py
"""

import json
import os

# Override config before importing pipeline modules
os.environ.setdefault("DRY_RUN", "true")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-2")

import anthropic
from pipeline.tracing import generate_trace_id, TraceLogger
from pipeline.agents import run_summarization, run_classification, run_remediation

# Real alarm event (from CloudWatch → EventBridge → SQS)
MOCK_EVENT = {
    "Records": [
        {
            "body": json.dumps({
                "detail": {
                    "alarmName": "remediation-agent-error-rate",
                    "state": {
                        "value": "ALARM",
                        "reason": "Threshold Crossed: 1 datapoint [42.0 (27/03/26 01:08:00)] was greater than or equal to the threshold (10.0).",
                        "timestamp": "2026-03-27T01:09:14.563+0000",
                    },
                    "previousState": {
                        "value": "OK",
                        "timestamp": "2026-03-27T00:55:14.563+0000",
                    },
                    "configuration": {
                        "description": "App error rate exceeded threshold — triggers remediation agent",
                    },
                },
            })
        }
    ]
}


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Set ANTHROPIC_API_KEY env var")
        return

    client = anthropic.Anthropic(api_key=api_key)
    trace_id = generate_trace_id()
    trace_logger = TraceLogger(trace_id)

    # Parse event
    alarm_event = json.loads(MOCK_EVENT["Records"][0]["body"])["detail"]
    trace_logger.log_event("pipeline_start", {"alarm_event": alarm_event, "dry_run": True})

    # Agent 1: Summarization
    print("\n=== Agent 1: Summarization ===")
    summary = run_summarization(client, alarm_event, trace_logger)
    print(f"Result: {summary.model_dump_json(indent=2)}")

    # Agent 2: Classification
    print("\n=== Agent 2: Classification ===")
    classification = run_classification(client, summary, trace_logger)
    print(f"Result: {classification.model_dump_json(indent=2)}")

    if classification.recommended_action == "none":
        print(f"\n=== Pipeline complete: not actionable (confidence: {classification.confidence}) ===")
        return

    # Agent 3: Remediation
    print("\n=== Agent 3: Remediation ===")
    remediation = run_remediation(client, classification, summary, trace_logger)
    print(f"Result: {remediation.model_dump_json(indent=2)}")

    trace_logger.log_incident_summary(summary, classification, remediation, dry_run=True)
    print(f"\n=== Pipeline complete: {remediation.action_taken} (verified: {remediation.verified}) ===")


if __name__ == "__main__":
    main()
