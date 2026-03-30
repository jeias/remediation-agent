from pipeline.config import CLUSTER_NAME, SERVICE_NAME, LOG_GROUP_NAME, SES_TEAM_RECIPIENT, SES_OPS_RECIPIENT, TASK_FAMILY, DB_INSTANCE_ID

TOOL_FETCH_LOGS = {
    "name": "fetch_cloudwatch_logs",
    "strict": True,
    "description": "Fetch recent log events from a CloudWatch log group. Returns the most recent log lines within the specified time window.",
    "input_schema": {
        "type": "object",
        "properties": {
            "log_group_name": {
                "type": "string",
                "enum": [LOG_GROUP_NAME],
                "description": "The CloudWatch log group to query",
            },
            "minutes_ago": {
                "type": "integer",
                "description": "How many minutes back to fetch logs (default 5)",
            },
            "filter_pattern": {
                "type": "string",
                "description": "Optional CloudWatch Logs filter pattern (e.g., 'ERROR')",
            },
            "since_timestamp": {
                "type": "string",
                "description": "ISO 8601 timestamp. Only return logs after this time. Overrides minutes_ago. Use alarm's previousState.timestamp for summarization, or rollback's stabilized_at for post-rollback verification.",
            },
        },
        "required": ["log_group_name"],
        "additionalProperties": False,
    },
}

TOOL_DESCRIBE_ECS = {
    "name": "describe_ecs_service",
    "strict": True,
    "description": "Describe the ECS service to check deployment state. Returns current and previous task definition revisions and deployment status.",
    "input_schema": {
        "type": "object",
        "properties": {
            "cluster_name": {
                "type": "string",
                "enum": [CLUSTER_NAME],
                "description": "The ECS cluster name",
            },
            "service_name": {
                "type": "string",
                "enum": [SERVICE_NAME],
                "description": "The ECS service name",
            },
        },
        "required": ["cluster_name", "service_name"],
        "additionalProperties": False,
    },
}

TOOL_ROLLBACK_ECS = {
    "name": "rollback_ecs_service",
    "strict": True,
    "description": "Rollback an ECS service to the previous task definition revision. Only use when classification confidence >= 0.8 and type is 'deployment'.",
    "input_schema": {
        "type": "object",
        "properties": {
            "cluster_name": {
                "type": "string",
                "enum": [CLUSTER_NAME],
                "description": "The ECS cluster name",
            },
            "service_name": {
                "type": "string",
                "enum": [SERVICE_NAME],
                "description": "The ECS service name",
            },
        },
        "required": ["cluster_name", "service_name"],
        "additionalProperties": False,
    },
}

TOOL_SEND_EMAIL = {
    "name": "send_email",
    "strict": True,
    "description": "Send an incident notification or escalation email via AWS SES. Always called — either as action notification or escalation.",
    "input_schema": {
        "type": "object",
        "properties": {
            "to": {
                "type": "string",
                "enum": [SES_TEAM_RECIPIENT, SES_OPS_RECIPIENT],
                "description": "Recipient email address",
            },
            "subject": {"type": "string", "description": "Email subject line"},
            "body": {"type": "string", "description": "Email body with incident details"},
            "severity": {
                "type": "string",
                "enum": ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
                "description": "Incident severity level",
            },
        },
        "required": ["to", "subject", "body", "severity"],
        "additionalProperties": False,
    },
}

TOOL_GET_TASK_DEF = {
    "name": "get_task_definition",
    "strict": True,
    "description": "Get details of a specific ECS task definition revision, including the container image URI and tag (git SHA). Use this to find what code a specific revision is running.",
    "input_schema": {
        "type": "object",
        "properties": {
            "task_family": {
                "type": "string",
                "enum": [TASK_FAMILY],
                "description": "The task definition family name",
            },
            "revision": {
                "type": "integer",
                "description": "Task definition revision number",
            },
        },
        "required": ["task_family", "revision"],
        "additionalProperties": False,
    },
}

TOOL_COMPARE_COMMITS = {
    "name": "compare_git_commits",
    "strict": True,
    "description": "Compare two git commits and return the code changes (commit messages, changed files, diffs). Use this to understand what code changed between deployments.",
    "input_schema": {
        "type": "object",
        "properties": {
            "base_sha": {
                "type": "string",
                "description": "Git SHA of the previous (base) commit",
            },
            "head_sha": {
                "type": "string",
                "description": "Git SHA of the current (head) commit",
            },
        },
        "required": ["base_sha", "head_sha"],
        "additionalProperties": False,
    },
}

TOOL_DESCRIBE_RDS = {
    "name": "describe_rds_instance",
    "strict": True,
    "description": "Check the status of the RDS database instance. Use this when investigating infrastructure issues to determine WHY the database is unreachable (stopped, failing, etc.).",
    "input_schema": {
        "type": "object",
        "properties": {
            "db_instance_identifier": {
                "type": "string",
                "enum": [DB_INSTANCE_ID],
                "description": "The RDS instance identifier",
            },
        },
        "required": ["db_instance_identifier"],
        "additionalProperties": False,
    },
}

# Tool sets per agent (least-privilege)
SUMMARIZATION_TOOLS = [TOOL_FETCH_LOGS]
CLASSIFICATION_TOOLS = [TOOL_FETCH_LOGS, TOOL_DESCRIBE_ECS, TOOL_GET_TASK_DEF, TOOL_COMPARE_COMMITS, TOOL_DESCRIBE_RDS]
REMEDIATION_TOOLS = [TOOL_ROLLBACK_ECS, TOOL_SEND_EMAIL, TOOL_FETCH_LOGS]
