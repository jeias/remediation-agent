import json

import anthropic
import boto3

from pipeline.config import ANTHROPIC_SECRET_ARN, DRY_RUN, SES_SENDER, SES_OPS_RECIPIENT
from pipeline.tracing import generate_trace_id, TraceLogger
from pipeline.agents import (
    run_summarization, run_classification, run_remediation,
)

# --- Cold Start: Fetch API Key from Secrets Manager ---

_client = None


def get_anthropic_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        sm = boto3.client("secretsmanager")
        secret = sm.get_secret_value(SecretId=ANTHROPIC_SECRET_ARN)
        api_key = secret["SecretString"]
        _client = anthropic.Anthropic(api_key=api_key)
    return _client


def parse_sqs_event(event: dict) -> dict:
    record = event["Records"][0]
    body = json.loads(record["body"])
    return body.get("detail", body)


def fail_open_escalate(alarm_event: dict, error: Exception, trace_logger: TraceLogger):
    trace_logger.log_event("fail_open", {
        "error_type": type(error).__name__,
        "error_message": str(error),
    })
    if DRY_RUN:
        trace_logger.log_event("fail_open_dry_run", "Would send escalation email")
        return

    try:
        ses = boto3.client("ses")
        ses.send_email(
            Source=SES_SENDER,
            Destination={"ToAddresses": [SES_OPS_RECIPIENT]},
            Message={
                "Subject": {"Data": "[CRITICAL] Remediation Agent Pipeline Failure"},
                "Body": {"Text": {"Data": (
                    f"The remediation agent pipeline failed.\n\n"
                    f"Error: {type(error).__name__}: {error}\n\n"
                    f"Alarm event:\n{json.dumps(alarm_event, indent=2, default=str)}\n\n"
                    f"Trace ID: {trace_logger.trace_id}\n\n"
                    f"Manual investigation required."
                )}},
            },
        )
    except Exception as email_err:
        trace_logger.log_event("fail_open_email_failed", str(email_err))


def handler(event, context):
    trace_id = generate_trace_id()
    trace_logger = TraceLogger(trace_id)
    alarm_event = None

    try:
        client = get_anthropic_client()
        alarm_event = parse_sqs_event(event)
        trace_logger.log_event("pipeline_start", {
            "alarm_event": alarm_event,
            "dry_run": DRY_RUN,
        })

        # Agent 1: Summarization
        summary = run_summarization(client, alarm_event, trace_logger)

        # Agent 2: Classification
        classification = run_classification(client, summary, trace_logger)

        # Short-circuit: not actionable
        if classification.recommended_action == "none":
            trace_logger.log_event("pipeline_end", {
                "reason": "not_actionable",
                "classification": classification.model_dump(),
            })
            return {"statusCode": 200, "body": "Not actionable"}

        # Agent 3: Remediation (reviewer/gatekeeper)
        remediation = run_remediation(client, classification, summary, trace_logger)

        # Final summary
        trace_logger.log_incident_summary(summary, classification, remediation, DRY_RUN)

        return {
            "statusCode": 200,
            "body": json.dumps({
                "trace_id": trace_id,
                "action_taken": remediation.action_taken,
                "confidence_accepted": remediation.confidence_accepted,
            }),
        }

    except Exception as e:
        trace_logger.log_event("pipeline_error", {
            "error_type": type(e).__name__,
            "error_message": str(e),
        })
        fail_open_escalate(alarm_event or {}, e, trace_logger)
        raise
