import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

import boto3

from pipeline.config import (
    DRY_RUN, MAX_LOG_LINES, SES_SENDER, VERIFICATION_WAIT_SECONDS,
    GITHUB_REPO, GITHUB_TOKEN_SECRET_ARN,
)

logs_client = boto3.client("logs")
ecs_client = boto3.client("ecs")
ses_client = boto3.client("ses")
rds_client = boto3.client("rds")


def fetch_cloudwatch_logs(log_group_name: str, minutes_ago: int = 5,
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
            "success": True,
            "rolled_back_from": current_revision,
            "rolled_back_to": previous_revision,
            "deployment_stable": True,
            "wait_seconds": 0,
            "message": f"Would rollback from revision {current_revision} to {previous_revision}",
        })

    ecs_client.update_service(
        cluster=cluster_name,
        service=service_name,
        taskDefinition=previous_task_def,
    )

    # Poll until deployment is stable or timeout
    start = time.monotonic()
    deployment_stable = False

    while (time.monotonic() - start) < VERIFICATION_WAIT_SECONDS:
        time.sleep(10)
        resp = ecs_client.describe_services(cluster=cluster_name, services=[service_name])
        svc = resp["services"][0]
        deployments = svc.get("deployments", [])
        primary = next((d for d in deployments if d["status"] == "PRIMARY"), None)

        if (
            primary
            and primary["taskDefinition"] == previous_task_def
            and primary["runningCount"] == svc.get("desiredCount", 1)
            and all(d["runningCount"] == 0 for d in deployments if d["status"] != "PRIMARY")
        ):
            deployment_stable = True
            break

    wait_seconds = int(time.monotonic() - start)

    result = {
        "success": True,
        "rolled_back_from": current_revision,
        "rolled_back_to": previous_revision,
        "deployment_stable": deployment_stable,
        "wait_seconds": wait_seconds,
    }
    if not deployment_stable:
        result["message"] = f"Deployment in progress but not yet stable after {wait_seconds}s"

    return json.dumps(result)


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


def get_task_definition(task_family: str, revision: int) -> str:
    td_id = f"{task_family}:{revision}"
    response = ecs_client.describe_task_definition(taskDefinition=td_id)
    td = response["taskDefinition"]
    container = td["containerDefinitions"][0]

    image = container.get("image", "")
    tag = image.split(":")[-1] if ":" in image else "unknown"

    result = {
        "task_family": td["family"],
        "revision": td["revision"],
        "image": image,
        "tag": tag,
        "environment": {
            e["name"]: e["value"]
            for e in container.get("environment", [])
        },
    }
    return json.dumps(result, indent=2)


# --- GitHub Integration ---

_github_token = None


def _get_github_token() -> str | None:
    global _github_token
    if _github_token is None and GITHUB_TOKEN_SECRET_ARN:
        sm = boto3.client("secretsmanager")
        secret = sm.get_secret_value(SecretId=GITHUB_TOKEN_SECRET_ARN)
        _github_token = secret["SecretString"]
    return _github_token


def compare_git_commits(base_sha: str, head_sha: str) -> str:
    if base_sha == head_sha:
        return json.dumps({"message": "Same commit SHA — no code changes"})

    token = _get_github_token()
    if not token:
        return json.dumps({"error": "GitHub token not configured. Cannot compare commits."})

    url = f"https://api.github.com/repos/{GITHUB_REPO}/compare/{base_sha}...{head_sha}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "remediation-agent",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return json.dumps({"error": f"GitHub API error: {e.code}", "detail": e.read().decode()[:200]})
    except Exception as e:
        return json.dumps({"error": f"GitHub API unavailable: {type(e).__name__}: {e}"})

    result = {
        "base_sha": base_sha,
        "head_sha": head_sha,
        "total_commits": len(data.get("commits", [])),
        "commits": [
            {"sha": c["sha"][:7], "message": c["commit"]["message"][:200]}
            for c in data.get("commits", [])[:5]
        ],
        "changed_files": [
            {
                "filename": f["filename"],
                "additions": f["additions"],
                "deletions": f["deletions"],
                "patch": f.get("patch", "")[:500],
            }
            for f in data.get("files", [])[:10]
        ],
    }
    return json.dumps(result, indent=2)


def describe_rds_instance(db_instance_identifier: str) -> str:
    response = rds_client.describe_db_instances(DBInstanceIdentifier=db_instance_identifier)
    instance = response["DBInstances"][0]
    result = {
        "db_instance_identifier": instance["DBInstanceIdentifier"],
        "status": instance["DBInstanceStatus"],
        "engine": instance["Engine"],
        "endpoint": instance.get("Endpoint", {}).get("Address", "N/A"),
        "port": instance.get("Endpoint", {}).get("Port", "N/A"),
    }
    return json.dumps(result, indent=2)


# --- Tool Dispatcher ---

TOOL_EXECUTORS = {
    "fetch_cloudwatch_logs": lambda args: fetch_cloudwatch_logs(**args),
    "describe_ecs_service": lambda args: describe_ecs_service(**args),
    "rollback_ecs_service": lambda args: rollback_ecs_service(**args),
    "send_email": lambda args: send_email(**args),
    "get_task_definition": lambda args: get_task_definition(**args),
    "compare_git_commits": lambda args: compare_git_commits(**args),
    "describe_rds_instance": lambda args: describe_rds_instance(**args),
}


def execute_tool(tool_name: str, tool_input: dict) -> str:
    executor = TOOL_EXECUTORS.get(tool_name)
    if executor is None:
        return json.dumps({"error": f"Unknown tool: {tool_name}"})
    try:
        return executor(tool_input)
    except Exception as e:
        return json.dumps({"error": f"Tool execution failed: {type(e).__name__}: {e}"})
