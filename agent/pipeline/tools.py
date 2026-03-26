from pipeline.config import CLUSTER_NAME, SERVICE_NAME, LOG_GROUP_NAME, SES_TEAM_RECIPIENT, SES_OPS_RECIPIENT

TOOL_FETCH_LOGS = {
    "name": "fetch_cloudwatch_logs",
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
                "default": 15,
                "description": "How many minutes back to fetch logs",
            },
            "filter_pattern": {
                "type": "string",
                "description": "Optional CloudWatch Logs filter pattern (e.g., 'ERROR')",
            },
        },
        "required": ["log_group_name"],
    },
}

TOOL_DESCRIBE_ECS = {
    "name": "describe_ecs_service",
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
    },
}

TOOL_ROLLBACK_ECS = {
    "name": "rollback_ecs_service",
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
    },
}

TOOL_SEND_EMAIL = {
    "name": "send_email",
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
    },
}

# Tool sets per agent (least-privilege)
SUMMARIZATION_TOOLS = [TOOL_FETCH_LOGS]
CLASSIFICATION_TOOLS = [TOOL_FETCH_LOGS, TOOL_DESCRIBE_ECS]
REMEDIATION_TOOLS = [TOOL_ROLLBACK_ECS, TOOL_SEND_EMAIL]
