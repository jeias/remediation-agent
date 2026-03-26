import json
from datetime import datetime, timezone, timedelta

import boto3

from pipeline.config import DRY_RUN, MAX_LOG_LINES, SES_SENDER

logs_client = boto3.client("logs")
ecs_client = boto3.client("ecs")
ses_client = boto3.client("ses")


def fetch_cloudwatch_logs(log_group_name: str, minutes_ago: int = 15,
                          filter_pattern: str | None = None) -> str:
    start_time = int((datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).timestamp() * 1000)
    end_time = int(datetime.now(timezone.utc).timestamp() * 1000)

    kwargs = {
        "logGroupName": log_group_name,
        "startTime": start_time,
        "endTime": end_time,
        "limit": MAX_LOG_LINES,
        "interleaved": True,
    }
    if filter_pattern:
        kwargs["filterPattern"] = filter_pattern

    response = logs_client.filter_log_events(**kwargs)
    events = response.get("events", [])
    events.sort(key=lambda e: e["timestamp"], reverse=True)
    lines = [e["message"].strip() for e in events[:MAX_LOG_LINES]]
    return "\n".join(lines) if lines else "No log events found in the specified time window."


def describe_ecs_service(cluster_name: str, service_name: str) -> str:
    response = ecs_client.describe_services(cluster=cluster_name, services=[service_name])
    service = response["services"][0]

    current_task_def = service["taskDefinition"]
    deployments = service.get("deployments", [])
    running_count = service.get("runningCount", 0)
    desired_count = service.get("desiredCount", 0)

    current_revision = int(current_task_def.split(":")[-1])

    result = {
        "current_task_definition": current_task_def,
        "current_revision": current_revision,
        "running_count": running_count,
        "desired_count": desired_count,
        "deployments": [
            {
                "task_definition": d["taskDefinition"],
                "status": d["status"],
                "running_count": d["runningCount"],
                "created_at": d["createdAt"].isoformat(),
            }
            for d in deployments
        ],
    }
    return json.dumps(result, indent=2)


def rollback_ecs_service(cluster_name: str, service_name: str) -> str:
    response = ecs_client.describe_services(cluster=cluster_name, services=[service_name])
    service = response["services"][0]
    current_task_def = service["taskDefinition"]
    current_revision = int(current_task_def.split(":")[-1])

    if current_revision <= 1:
        return json.dumps({"error": "Cannot rollback: already at revision 1"})

    previous_revision = current_revision - 1
    previous_task_def = current_task_def.rsplit(":", 1)[0] + f":{previous_revision}"

    if DRY_RUN:
        return json.dumps({
            "dry_run": True,
            "message": f"Would rollback from revision {current_revision} to {previous_revision}",
            "previous_task_definition": previous_task_def,
        })

    ecs_client.update_service(
        cluster=cluster_name,
        service=service_name,
        taskDefinition=previous_task_def,
    )
    return json.dumps({
        "success": True,
        "rolled_back_from": current_revision,
        "rolled_back_to": previous_revision,
        "previous_task_definition": previous_task_def,
    })


def send_email(to: str, subject: str, body: str, severity: str) -> str:
    if DRY_RUN:
        return json.dumps({
            "dry_run": True,
            "message": f"Would send email to {to}",
            "subject": subject,
            "severity": severity,
        })

    ses_client.send_email(
        Source=SES_SENDER,
        Destination={"ToAddresses": [to]},
        Message={
            "Subject": {"Data": f"[{severity}] {subject}"},
            "Body": {"Text": {"Data": body}},
        },
    )
    return json.dumps({"success": True, "sent_to": to, "subject": subject})


# --- Tool Dispatcher ---

TOOL_EXECUTORS = {
    "fetch_cloudwatch_logs": lambda args: fetch_cloudwatch_logs(**args),
    "describe_ecs_service": lambda args: describe_ecs_service(**args),
    "rollback_ecs_service": lambda args: rollback_ecs_service(**args),
    "send_email": lambda args: send_email(**args),
}


def execute_tool(tool_name: str, tool_input: dict) -> str:
    executor = TOOL_EXECUTORS.get(tool_name)
    if executor is None:
        return json.dumps({"error": f"Unknown tool: {tool_name}"})
    try:
        return executor(tool_input)
    except Exception as e:
        return json.dumps({"error": f"Tool execution failed: {type(e).__name__}: {e}"})
