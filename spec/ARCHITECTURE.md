# Remediation Agent — Architecture & Technical Specification

## Overview

This document describes the technical architecture of the Remediation Agent — an autonomous AI system that monitors, diagnoses, and remediates incidents in a cloud application. It is designed as a proof of concept for a Staff AI Engineer interview, with emphasis on AI agent design patterns, prompt engineering, and responsible autonomy.

The agent follows a **multi-agent pipeline** architecture: three specialized AI agents (Summarization, Classification, Remediation) process incidents sequentially, each with focused responsibilities, constrained tool access, and appropriate model selection.

---

## Table of Contents

- [Multi-Agent Pipeline](#multi-agent-pipeline)
- [Agent 1: Summarization](#agent-1-summarization)
- [Agent 2: Classification](#agent-2-classification)
- [Agent 3: Remediation](#agent-3-remediation)
- [Tool Definitions](#tool-definitions)
- [Infrastructure](#infrastructure)
- [Alerting Pipeline](#alerting-pipeline)
- [Observability & Tracing](#observability--tracing)
- [Guardrails & Safety](#guardrails--safety)
- [Error Handling](#error-handling)
- [Implementation Phases](#implementation-phases)
- [Production Considerations](#production-considerations)

---

## Multi-Agent Pipeline

The agent is not a single monolithic LLM call. It is a **sequential pipeline of three specialized agents**, each with its own system prompt, tool set, and model. This separation provides:

- **Least-privilege tool access** — each agent only has the tools it needs
- **Cost optimization** — cheaper models for simpler tasks (Haiku for summarization)
- **Debuggability** — each step produces structured output that can be inspected independently
- **Testability** — each agent can be tested in isolation with mocked inputs
- **Defense in depth** — Agent 3 reviews Agent 2's classification before acting, two independent LLM evaluations must agree

```
┌───────────────────────────────────────────────────────────────────────────┐
│                            Lambda Function                                │
│                                                                           │
│  SQS Event                                                                │
│     │                                                                     │
│     ▼                                                                     │
│  ┌──────────────────┐   ┌───────────────────┐   ┌──────────────────────┐  │
│  │  Summarization   │──▶│  Classification   │──▶│  Remediation         │  │
│  │  Agent (Haiku)   │   │  Agent (Sonnet)   │   │  Agent (Sonnet)      │  │
│  │                  │   │                   │   │                      │  │
│  │  Tools:          │   │  Tools:           │   │  Tools:              │  │
│  │  - fetch_logs    │   │  - fetch_logs     │   │  - rollback          │  │
│  │                  │   │  - describe_ecs   │   │  - send_email        │  │
│  │                  │   │                   │   │                      │  │
│  │  Output:         │   │  Output:          │   │  Role:               │  │
│  │  Structured JSON │   │  Classification   │   │  REVIEWER/GATEKEEPER │  │
│  │  summary         │   │  + confidence     │   │  Reviews confidence, │  │
│  │                  │   │  + action hint    │   │  gates execution,    │  │
│  │                  │   │                   │   │  composes email      │  │
│  └──────────────────┘   └───────────────────┘   └──────────────────────┘  │
│                                                                           │
│  Validation: Pydantic schema enforced between each agent step             │
│  If classification = "not_actionable" ──▶ log and exit                    │
│  If confidence < threshold ──▶ Agent 3 downgrades to notify-only          │
│  If any agent fails ──▶ escalate to humans (fail open)                    │
└───────────────────────────────────────────────────────────────────────────┘
```

### Why Sequential Pipeline?

| Pattern | Fits? | Rationale |
|---------|-------|-----------|
| **Sequential Pipeline** | Yes | Workflow is well-defined, steps always follow the same order. Simplest to implement, debug, and test. Each step has a clear input/output contract. |
| **Orchestrator + Specialists** | Overkill | An orchestrator agent is valuable when the workflow is dynamic (unknown steps). Ours is always: summarize → classify → act. An orchestrator adds latency and cost for no flexibility gain. |
| **Debate / Verification** | Partially adopted | Agent 3 acts as a reviewer of Agent 2's classification — a lightweight verification layer. A full debate pattern (two agents arguing) would double LLM cost for marginal safety gain in this scope. |
| **Parallel Agents** | No | Steps are dependent — Classification needs the Summary, Remediation needs the Classification. No opportunity for parallelism. |

### Data Flow

```
SQS Event (CloudWatch Alarm payload)
    │
    ▼
┌─ Summarization Agent ─────────────────────────────────────────────┐
│  Input:  alarm event metadata                                     │
│  Action: calls fetch_cloudwatch_logs to get recent app logs       │
│  Output: {                                                        │
│    "error_type": "connection_error",                              │
│    "first_seen": "2026-03-25T14:32:01Z",                          │
│    "frequency": 47,                                               │
│    "affected_service": "remediation-agent-app",                   │
│    "key_logs": ["psycopg2.OperationalError: could not connect..."]│
│  }                                                                │
│  ▸ Validated against SummarizationOutput Pydantic schema          │
└───────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─ Classification Agent ────────────────────────────────────────────┐
│  Input:  summary JSON from previous step                          │
│  Action: calls describe_ecs_service to check deployment state     │
│          may call fetch_cloudwatch_logs for additional context    │
│          reasons step-by-step in <reasoning> tags before deciding │
│  Output: {                                                        │
│    "type": "deployment" | "infrastructure" | "transient",         │
│    "severity": "LOW" | "MEDIUM" | "HIGH" | "CRITICAL",            │
│    "confidence": 0.0 - 1.0,                                       │
│    "recommended_action": "rollback" | "escalate" | "none",        │
│    "summary": "Bad deployment: wrong DATABASE_HOST in rev 5..."   │
│  }                                                                │
│  ▸ Validated against ClassificationOutput Pydantic schema         │
└───────────────────────────────────────────────────────────────────┘
    │
    ├── if recommended_action = "none" ──▶ log and exit
    │
    ▼
┌─ Remediation Agent (Reviewer/Gatekeeper) ─────────────────────────┐
│  Input:  classification JSON + original summary JSON              │
│  Role:   REVIEW the classification, GATE the execution            │
│                                                                   │
│  Decision matrix:                                                 │
│  ┌─────────────────┬──────────────┬─────────────────────────────┐ │
│  │ Classification   │ Confidence   │ Action                     │ │
│  ├─────────────────┼──────────────┼─────────────────────────────┤ │
│  │ rollback         │ >= 0.8       │ Execute rollback + notify  │ │
│  │ rollback         │ < 0.8        │ SKIP rollback, notify only │ │
│  │ escalate         │ any          │ Send CRITICAL email to ops │ │
│  └─────────────────┴──────────────┴─────────────────────────────┘ │
│                                                                   │
│  Action: reviews confidence and reasoning                         │
│          if confident → calls rollback_ecs_service                │
│          if not confident → downgrades to notification only       │
│          always calls send_email to notify or escalate            │
│          composes rich, contextual email body                     │
│  Output: {                                                        │
│    "action_taken": "rollback" | "escalated" | "notify_only",      │
│    "confidence_accepted": true | false,                           │
│    "details": "Rolled back from revision 5 to revision 4"         │
│  }                                                                │
│  ▸ Validated against RemediationOutput Pydantic schema            │
└───────────────────────────────────────────────────────────────────┘
```

---

## Agent 1: Summarization

**Model**: Claude Haiku (`claude-haiku-4-5-20251001`)
**Temperature**: `0` (deterministic extraction, no creativity needed)
**Purpose**: Extract structured information from raw CloudWatch logs. Reduces noise and token usage for downstream agents.

### System Prompt

```
You are a log analysis agent. Your job is to analyze CloudWatch log events from an ECS
Fargate application and produce a structured summary.

You have one tool: fetch_cloudwatch_logs. Use it to retrieve recent logs from the
application's log group.

After analyzing the logs, you MUST respond with a JSON object containing:
{
  "error_type": "connection_error" | "query_error" | "import_error" | "syntax_error" | "runtime_error" | "timeout" | "unknown",
  "first_seen": "ISO 8601 timestamp of the first error occurrence",
  "frequency": <number of error occurrences in the log window>,
  "affected_service": "name of the affected ECS service",
  "key_logs": ["up to 5 most relevant log lines that describe the error"]
}

Rules:
- Only use the fetch_cloudwatch_logs tool. Do not attempt to fix anything.
- Focus on errors and exceptions. Ignore INFO-level logs unless they provide context.
- If no errors are found, return error_type: "unknown" with empty key_logs.
- Be precise. Only include log lines that actually exist in the tool output.
- Do NOT invent or fabricate log lines. Only quote lines returned by the tool.

## Example

Given these logs:
  2026-03-25T14:32:01Z ERROR: Failed to connect to database
  2026-03-25T14:32:01Z psycopg2.OperationalError: could not connect to server: Connection refused
  2026-03-25T14:31:55Z INFO: Starting application on 0.0.0.0:8000
  2026-03-25T14:31:50Z INFO: Task started - task definition revision: 5

You should respond:
{
  "error_type": "connection_error",
  "first_seen": "2026-03-25T14:32:01Z",
  "frequency": 1,
  "affected_service": "remediation-agent-app",
  "key_logs": [
    "psycopg2.OperationalError: could not connect to server: Connection refused"
  ]
}

## Example 2

Given these logs:
  2026-03-25T14:35:12Z ERROR: Database connection failed
    psycopg2.errors.UndefinedColumn: column "description" does not exist
  2026-03-25T14:35:10Z INFO: GET /items - 500
  2026-03-25T14:34:12Z INFO: GET /health - 200
  2026-03-25T14:34:00Z INFO: Database initialized successfully

You should respond:
{
  "error_type": "query_error",
  "first_seen": "2026-03-25T14:35:12Z",
  "frequency": 1,
  "affected_service": "remediation-agent-app",
  "key_logs": [
    "psycopg2.errors.UndefinedColumn: column \"description\" does not exist"
  ]
}
```

### Output Schema (Pydantic)

```python
class SummarizationOutput(BaseModel):
    error_type: Literal[
        "connection_error", "query_error", "import_error",
        "syntax_error", "runtime_error", "timeout", "unknown"
    ]
    first_seen: str
    frequency: int = Field(ge=0)
    affected_service: str
    key_logs: list[str] = Field(max_length=5)
```

### Tools

| Tool | Purpose |
|------|---------|
| `fetch_cloudwatch_logs` | Fetch recent log events from the application's CloudWatch log group |

### Token Budget

- Max input: ~2000 tokens (system prompt + alarm event + log output)
- Max output: ~500 tokens (structured JSON summary)
- Log lines truncated to last 50 entries to fit within budget

---

## Agent 2: Classification

**Model**: Claude Sonnet (`claude-sonnet-4-6-latest`)
**Temperature**: `0` (deterministic classification, consistency is critical)
**Purpose**: Classify the incident type and severity, recommend an action, and express confidence. Uses the summary from Agent 1 plus ECS service state to make an informed decision.

### System Prompt

```
You are an incident classification agent for cloud infrastructure. You receive a structured
summary of application errors and must classify the incident.

You have two tools:
- fetch_cloudwatch_logs: to fetch additional log context if needed
- describe_ecs_service: to check the current deployment state (task definition revisions,
  running task count, recent deployments)

## Process

1. ALWAYS call describe_ecs_service first to check if a deployment happened recently.
2. If the summary is unclear, call fetch_cloudwatch_logs for additional context.
3. Reason step-by-step inside <reasoning> tags before outputting your classification.
4. Output your classification as a JSON object.

## Reasoning (required)

Before your JSON output, think step by step in <reasoning> tags:
- What type of error is occurring?
- Has there been a recent deployment (task definition change)?
- Is the error in the application code/config or in external infrastructure?
- How severe is the impact (partial vs. full outage)?
- How confident am I, and what could make me wrong?

## Output Format

After your reasoning, respond with a JSON object:
{
  "type": "deployment" | "infrastructure" | "transient",
  "severity": "LOW" | "MEDIUM" | "HIGH" | "CRITICAL",
  "confidence": <float between 0.0 and 1.0>,
  "recommended_action": "rollback" | "escalate" | "none",
  "summary": "one-paragraph explanation of the root cause and reasoning"
}

## Classification Rules

- "deployment": The error started after a recent task definition change. The previous
  revision was healthy. Root cause is in the new code or configuration.
  Key indicators: ProgrammingError (query_error), ImportError, or errors that only
  affect specific endpoints while health check passes. OperationalError with DNS
  failure also indicates bad deployment config.
  → recommended_action: "rollback"

- "infrastructure": The error is related to external dependencies (database unreachable,
  network issues, resource exhaustion). The task definition has NOT changed recently.
  Key indicators: OperationalError with "Connection refused" on a correct hostname,
  affecting ALL endpoints including health check.
  → recommended_action: "escalate"

- "transient": Errors are intermittent, low frequency, or already resolving.
  → recommended_action: "none"

## Confidence Guidelines

- 0.9-1.0: Strong evidence from both logs and ECS service state
- 0.7-0.9: Clear evidence from one source, consistent with the other
- 0.5-0.7: Ambiguous — evidence could support multiple classifications
- Below 0.5: Insufficient evidence to classify confidently

## Severity Guidelines

- CRITICAL: Service is fully down, all requests failing
- HIGH: Service is degraded, most requests failing
- MEDIUM: Partial impact, some requests failing
- LOW: Minor issue, minimal user impact

## Edge Cases

- If you see BOTH deployment errors AND infrastructure errors: classify based on which
  came first chronologically. If a deployment happened AND the DB is down, the DB issue
  takes precedence (infrastructure).
- If logs are empty or insufficient: set confidence below 0.5 and type to "transient".
- If the ECS service shows a recent deployment but the errors don't match typical
  deployment failures: lower your confidence and explain why in the summary.

Be precise. Do not guess — use your tools to gather evidence.

## Examples

### Example 1: Deployment issue (high confidence)

Input summary: {"error_type": "runtime_error", "frequency": 15,
  "key_logs": ["psycopg2.errors.UndefinedColumn: column \"description\" does not exist"]}
ECS service state: revision changed from 4 to 5, 12 minutes ago, running_count: 1

<reasoning>
The logs show a psycopg2.errors.UndefinedColumn error — this is a ProgrammingError,
meaning the application code has a bug in its SQL query. The error references a column
"description" that doesn't exist in the items table. The ECS service deployed a new
revision (5) 12 minutes ago, and the errors started immediately after. The task is
running (health check passes), but the /items endpoint is failing. The previous
revision 4 was stable. This is a code bug introduced by the deployment, not an
infrastructure issue.
</reasoning>

{"type": "deployment", "severity": "HIGH", "confidence": 0.95,
 "recommended_action": "rollback",
 "summary": "Task definition revision 5 introduced a SQL query referencing non-existent column 'description'. GET /items returns 500. Health check still passes. Previous revision 4 was stable."}

### Example 2: Infrastructure issue (high confidence)

Input summary: {"error_type": "connection_error", "frequency": 200,
  "key_logs": ["could not connect to server: Connection refused on port 5432"]}
ECS service state: no deployment in 48 hours, revision 4 stable, running_count: 2

<reasoning>
The logs show connection refused errors to the correct database host on port 5432.
The ECS service has not been redeployed recently — revision 4 has been running for
48 hours. The tasks are running (count: 2) but failing on DB calls. This indicates
the database itself is unreachable, not a code problem.
</reasoning>

{"type": "infrastructure", "severity": "CRITICAL", "confidence": 0.92,
 "recommended_action": "escalate",
 "summary": "RDS PostgreSQL unreachable on port 5432. No recent deployment. Database appears stopped or failing."}
```

### Output Schema (Pydantic)

```python
class ClassificationOutput(BaseModel):
    type: Literal["deployment", "infrastructure", "transient"]
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    confidence: float = Field(ge=0.0, le=1.0)
    recommended_action: Literal["rollback", "escalate", "none"]
    summary: str
```

### Tools

| Tool | Purpose |
|------|---------|
| `fetch_cloudwatch_logs` | Fetch additional log context if the summary is insufficient |
| `describe_ecs_service` | Check current and previous task definition revisions, deployment status |

---

## Agent 3: Remediation (Reviewer/Gatekeeper)

**Model**: Claude Sonnet (`claude-sonnet-4-6-latest`)
**Temperature**: `0.3` (slight flexibility for email composition, deterministic for action decisions)
**Purpose**: Review the Classification agent's recommendation, gate execution based on confidence, execute the appropriate action, and compose a rich contextual notification email.

This agent serves a dual role:
1. **Reviewer** — independently evaluates whether the classification makes sense and whether the confidence justifies action
2. **Executor** — carries out the action (rollback or escalation) and composes the notification email

This is a **defense in depth** pattern: two independent LLM evaluations (Agent 2 + Agent 3) must agree before a destructive action (rollback) is taken.

### System Prompt

```
You are a remediation agent and safety reviewer for cloud infrastructure. You receive an
incident classification from a classification agent and must decide whether to execute
the recommended action.

Your role is twofold:
1. REVIEW: Evaluate whether the classification and confidence score justify the action
2. ACT: Execute the appropriate action and compose a notification email

You have two tools:
- rollback_ecs_service: rolls back the ECS service to the previous task definition revision
- send_email: sends a notification or escalation email via AWS SES

## Decision Rules

You will receive a classification with a confidence score (0.0 to 1.0).

### If recommended_action is "rollback":
- If confidence >= 0.8 AND you agree with the classification:
  1. Call rollback_ecs_service to revert to the previous task definition
  2. Call send_email to notify the team (severity: HIGH)
- If confidence < 0.8 OR you disagree with the classification:
  1. Do NOT call rollback_ecs_service
  2. Call send_email to notify the team to investigate manually (severity: MEDIUM)
  3. Explain why you did not execute the rollback

### If recommended_action is "escalate":
- Regardless of confidence:
  1. Call send_email to the operations team (severity: CRITICAL)
  2. Include the full diagnosis, error logs, and recommended manual action
  3. Do NOT attempt to rollback or fix infrastructure issues

## Email Composition

Write rich, contextual emails that include:
- Service name and current state
- Root cause analysis (from the classification summary and original logs)
- Action taken (or reason for not acting)
- Recommended next steps for the human team

## Output Format

After completing your actions, respond with a JSON object:
{
  "action_taken": "rollback" | "escalated" | "notify_only",
  "confidence_accepted": true | false,
  "details": "description of what was done and why"
}

## Example: High confidence rollback

Classification: {"type": "deployment", "confidence": 0.95, "recommended_action": "rollback",
  "summary": "Revision 5 introduced bad DATABASE_HOST config"}

I agree with this classification — the evidence clearly points to a bad deployment with
high confidence. Executing rollback.

[calls rollback_ecs_service]
[calls send_email with detailed incident summary]

{"action_taken": "rollback", "confidence_accepted": true,
 "details": "Rolled back from revision 5 to revision 4. Bad DATABASE_HOST config."}

## Example: Low confidence — downgrade to notify

Classification: {"type": "deployment", "confidence": 0.6, "recommended_action": "rollback",
  "summary": "Possible deployment issue but errors are intermittent"}

The confidence is below the 0.8 threshold and the errors are intermittent — this could
be a transient issue rather than a bad deployment. Rolling back could cause unnecessary
disruption. Sending notification for manual investigation instead.

[calls send_email asking team to investigate]

{"action_taken": "notify_only", "confidence_accepted": false,
 "details": "Classification confidence too low (0.6). Notified team to investigate manually."}
```

### Output Schema (Pydantic)

```python
class RemediationOutput(BaseModel):
    action_taken: Literal["rollback", "escalated", "notify_only"]
    confidence_accepted: bool
    details: str
```

### Tools

| Tool | Purpose |
|------|---------|
| `rollback_ecs_service` | Update ECS service to the previous task definition revision. Only use when classification confidence >= 0.8 and type is "deployment". |
| `send_email` | Send notification or escalation email via AWS SES. Always called — either as action notification or escalation. |

---

## Tool Definitions

### fetch_cloudwatch_logs

Fetches recent log events from the application's CloudWatch log group.

```json
{
  "name": "fetch_cloudwatch_logs",
  "description": "Fetch recent log events from a CloudWatch log group. Returns the most recent log lines within the specified time window.",
  "input_schema": {
    "type": "object",
    "properties": {
      "log_group_name": {
        "type": "string",
        "enum": ["/ecs/remediation-agent-app"],
        "description": "The CloudWatch log group to query"
      },
      "minutes_ago": {
        "type": "integer",
        "default": 15,
        "description": "How many minutes back to fetch logs"
      },
      "filter_pattern": {
        "type": "string",
        "description": "Optional CloudWatch Logs filter pattern (e.g., 'ERROR')"
      }
    },
    "required": ["log_group_name"]
  }
}
```

**Implementation notes:**
- Returns a maximum of 50 log lines (newest first) to stay within token budget
- Uses `filter_log_events` API with the specified time window
- `log_group_name` is constrained to an enum to prevent hallucinated log group names

### describe_ecs_service

Returns the current deployment state of the ECS service.

```json
{
  "name": "describe_ecs_service",
  "description": "Describe the ECS service to check deployment state. Returns current and previous task definition revisions and deployment status.",
  "input_schema": {
    "type": "object",
    "properties": {
      "cluster_name": {
        "type": "string",
        "enum": ["remediation-agent-cluster"],
        "description": "The ECS cluster name"
      },
      "service_name": {
        "type": "string",
        "enum": ["remediation-agent-app"],
        "description": "The ECS service name"
      }
    },
    "required": ["cluster_name", "service_name"]
  }
}
```

**Implementation notes:**
- Calls `ecs:DescribeServices` API
- Returns: current task definition ARN, previous task definition ARN (from deployment history), running count, desired count, last deployment timestamp
- Both parameters are constrained to enums to prevent acting on wrong services

### rollback_ecs_service

Rolls back the ECS service to the previous task definition revision.

```json
{
  "name": "rollback_ecs_service",
  "description": "Rollback an ECS service to the previous task definition revision. This will trigger a new deployment with the previous version.",
  "input_schema": {
    "type": "object",
    "properties": {
      "cluster_name": {
        "type": "string",
        "enum": ["remediation-agent-cluster"],
        "description": "The ECS cluster name"
      },
      "service_name": {
        "type": "string",
        "enum": ["remediation-agent-app"],
        "description": "The ECS service name"
      }
    },
    "required": ["cluster_name", "service_name"]
  }
}
```

**Implementation notes:**
- Calls `ecs:DescribeServices` to get the current task definition
- Extracts the revision number, decrements by 1
- Calls `ecs:UpdateService` with the previous task definition ARN
- Returns the old and new task definition revisions for audit

### send_email

Sends an incident notification or escalation email via AWS SES.

```json
{
  "name": "send_email",
  "description": "Send an incident notification or escalation email via AWS SES.",
  "input_schema": {
    "type": "object",
    "properties": {
      "to": {
        "type": "string",
        "enum": ["team@company.com", "ops-team@company.com"],
        "description": "Recipient email address"
      },
      "subject": {
        "type": "string",
        "description": "Email subject line"
      },
      "body": {
        "type": "string",
        "description": "Email body with incident details"
      },
      "severity": {
        "type": "string",
        "enum": ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
        "description": "Incident severity level"
      }
    },
    "required": ["to", "subject", "body", "severity"]
  }
}
```

**Implementation notes:**
- Uses `ses:SendEmail` API
- `to` is constrained to known email addresses to prevent misuse
- In DRY_RUN mode, logs the email content instead of sending

---

## Infrastructure

### Tech Stack

| Component | Technology | Naming |
|-----------|-----------|--------|
| Compute (App) | ECS Fargate (0.25 vCPU / 0.5 GB) | `remediation-agent-app` |
| Compute (Agent) | AWS Lambda (512 MB / 300s timeout) | `remediation-agent-lambda` |
| Database | RDS PostgreSQL db.t3.micro | `remediation-agent-db` |
| Container Registry | ECR Public | `remediation-agent-app` |
| Load Balancer | Application Load Balancer | `remediation-agent-alb` |
| Queue | SQS Standard Queue | `remediation-agent-queue` |
| Dead Letter Queue | SQS Standard Queue | `remediation-agent-dlq` |
| Alerting | CloudWatch Alarms + EventBridge | `remediation-agent-*` |
| Email | AWS SES | verified sender identity |
| Secrets | AWS Secrets Manager | `remediation-agent/anthropic-api-key` |
| IaC | Terraform (local state) | flat file structure |

### Network Architecture

Simplified for the POC — no private subnet isolation:

- **Default VPC** with public subnets only
- ECS Fargate tasks run in public subnets with public IP (auto-assign)
- RDS in the same public subnets, secured by security group (allow port 5432 from ECS SG only)
- ALB in the same public subnets
- Lambda runs **outside the VPC** — direct internet access for Anthropic API calls, AWS service calls via public endpoints

### Terraform Structure

```
infra/
├── main.tf              # Provider, backend, data sources
├── variables.tf         # All input variables
├── outputs.tf           # Key outputs (ALB DNS, Lambda ARN, etc.)
├── networking.tf        # Default VPC data sources, security groups
├── ecs.tf               # ECS cluster, service, task definition, ALB
├── rds.tf               # RDS instance, security group
├── lambda.tf            # Lambda function, IAM role, SQS trigger
├── monitoring.tf        # CloudWatch alarms, metric filters, EventBridge rules
├── sqs.tf               # SQS queue, DLQ, EventBridge target
├── ses.tf               # SES email identity verification
├── secrets.tf           # Secrets Manager for Anthropic API key
├── iam.tf               # IAM roles and policies (least privilege)
└── terraform.tfvars     # Environment-specific values
```

### IAM — Least Privilege

**Lambda Execution Role** (`remediation-agent-lambda-role`):

```
# CloudWatch Logs — read app logs + write agent logs
logs:FilterLogEvents    on arn:aws:logs:*:*:log-group:/ecs/remediation-agent-app:*
logs:CreateLogGroup     on arn:aws:logs:*:*:log-group:/aws/lambda/remediation-agent-*
logs:CreateLogStream    on arn:aws:logs:*:*:log-group:/aws/lambda/remediation-agent-*:*
logs:PutLogEvents       on arn:aws:logs:*:*:log-group:/aws/lambda/remediation-agent-*:*

# SQS — receive and delete messages
sqs:ReceiveMessage      on arn:aws:sqs:*:*:remediation-agent-queue
sqs:DeleteMessage       on arn:aws:sqs:*:*:remediation-agent-queue
sqs:GetQueueAttributes  on arn:aws:sqs:*:*:remediation-agent-queue

# ECS — describe and update the app service only
ecs:DescribeServices    on arn:aws:ecs:*:*:service/remediation-agent-cluster/remediation-agent-app
ecs:UpdateService       on arn:aws:ecs:*:*:service/remediation-agent-cluster/remediation-agent-app
ecs:DescribeTaskDefinition on arn:aws:ecs:*:*:task-definition/remediation-agent-app:*

# SES — send email from verified identity only
ses:SendEmail           on arn:aws:ses:*:*:identity/*

# Secrets Manager — read API key
secretsmanager:GetSecretValue on arn:aws:secretsmanager:*:*:secret:remediation-agent/anthropic-api-key-*

```

### SQS Configuration

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Queue Type | Standard | Ordering not critical for alarm events |
| Visibility Timeout | 360s | Greater than Lambda timeout (300s) to prevent duplicate processing |
| Message Retention | 4 days | Default, sufficient for retry scenarios |
| Receive Wait Time | 20s | Long polling enabled for cost efficiency |
| DLQ Max Receive Count | 3 | After 3 failed Lambda invocations, message goes to DLQ |

**Lambda Event Source Mapping:**

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Batch Size | 1 | Process one alarm at a time for clarity |
| Max Batching Window | 0s | No batching delay — process immediately |
| Max Concurrency | 2 | Limit concurrent agent invocations to prevent race conditions |
| Enabled | true | Active by default |

---

## Alerting Pipeline

### CloudWatch Alarms

| Alarm Name | Metric | Condition | Period | Eval Periods |
|------------|--------|-----------|--------|-------------|
| `remediation-agent-running-tasks` | ECS `RunningTaskCount` | < 1 | 60s | 1 |
| `remediation-agent-error-rate` | Custom metric filter on `ERROR` | >= 10 | 60s | 1 |

**Metric Filter** (for error rate alarm):

```
Filter pattern: "ERROR"
Log group: /ecs/remediation-agent-app
Metric namespace: RemediationAgent
Metric name: AppErrorCount
Metric value: 1
```

### EventBridge Rule

```json
{
  "source": ["aws.cloudwatch"],
  "detail-type": ["CloudWatch Alarm State Change"],
  "detail": {
    "state": {
      "value": ["ALARM"]
    },
    "alarmName": [
      { "prefix": "remediation-agent-" }
    ]
  }
}
```

**Target**: SQS queue `remediation-agent-queue`

### End-to-End Flow

```
CloudWatch detects anomaly
    │
    ▼
Alarm transitions to ALARM state
    │
    ▼
EventBridge matches rule (source: aws.cloudwatch, prefix: remediation-agent-)
    │
    ▼
EventBridge delivers event to SQS queue
    │
    ▼
Lambda event source mapping picks up message (batch size: 1)
    │
    ▼
Lambda function invoked with SQS event payload
    │
    ▼
Agent pipeline processes the incident
    │
    ├── Success: message auto-deleted from SQS
    └── Failure: message returns to queue (visibility timeout)
               after 3 failures → DLQ
```

---

## Observability & Tracing

Every incident generates a **trace** — a structured JSON log trail that links all agent steps together under a unique `trace_id`.

### Trace Structure

Each agent step logs a JSON object to CloudWatch:

```json
{
  "trace_id": "inc-20260325-143201-a1b2c3",
  "timestamp": "2026-03-25T14:32:05Z",
  "agent": "summarization",
  "model": "claude-haiku-4-5-20251001",
  "step": "tool_call",
  "tool_name": "fetch_cloudwatch_logs",
  "tool_input": {"log_group_name": "/ecs/remediation-agent-app", "minutes_ago": 10},
  "tool_output_preview": "50 log lines returned",
  "input_tokens": 1250,
  "output_tokens": 380,
  "duration_ms": 2340
}
```

### Token & Cost Tracking

Each Claude API call logs:

```json
{
  "trace_id": "inc-20260325-143201-a1b2c3",
  "agent": "classification",
  "model": "claude-sonnet-4-6-latest",
  "input_tokens": 1800,
  "output_tokens": 450,
  "estimated_cost_usd": 0.0123,
  "duration_ms": 3200
}
```

An **incident summary** is logged at the end of the pipeline:

```json
{
  "trace_id": "inc-20260325-143201-a1b2c3",
  "total_input_tokens": 4500,
  "total_output_tokens": 1200,
  "total_estimated_cost_usd": 0.028,
  "total_duration_ms": 8500,
  "classification": "deployment",
  "severity": "HIGH",
  "confidence": 0.95,
  "confidence_accepted": true,
  "action_taken": "rollback",
  "dry_run": false
}
```

### Reasoning Trace

The Classification agent's `<reasoning>` output is logged separately for debugging and audit:

```json
{
  "trace_id": "inc-20260325-143201-a1b2c3",
  "agent": "classification",
  "step": "reasoning",
  "content": "The logs show a DNS resolution failure for a database host. The ECS service deployed a new revision (5) 8 minutes ago..."
}
```

### Agent Health Alarms

In addition to app-level alarms, the agent itself is monitored:

| Alarm Name | Metric | Condition | Purpose |
|------------|--------|-----------|---------|
| `remediation-agent-lambda-errors` | Lambda `Errors` | > 0 in 5 min | Agent Lambda is failing |
| `remediation-agent-dlq-depth` | SQS DLQ `ApproximateNumberOfMessagesVisible` | > 0 | Unprocessable incidents accumulating |
| `remediation-agent-lambda-duration` | Lambda `Duration` | > 250000ms | Approaching timeout (300s) |

---

## Guardrails & Safety

### Constrained Tool Schemas

All tool parameters that reference AWS resources use **enum values** to prevent the agent from:
- Operating on the wrong ECS service or cluster
- Reading logs from unrelated log groups
- Sending emails to unintended recipients

```python
# Example: service_name is constrained
"service_name": {
    "type": "string",
    "enum": ["remediation-agent-app"]  # Claude cannot hallucinate a different service
}
```

### Inter-Agent Schema Validation

Every agent's output is validated against a **Pydantic model** before being passed to the next agent. This prevents cascading failures from malformed LLM output.

```python
# After each agent call:
for attempt in range(2):
    response = call_claude(agent_config, messages)
    try:
        return OutputSchema.model_validate_json(extract_json(response))
    except ValidationError as e:
        # Retry once with a correction prompt
        messages.append({"role": "assistant", "content": response})
        messages.append({"role": "user", "content": f"Your response was not valid JSON: {e}. Please try again with the correct schema."})

# If both attempts fail → fail open (escalate to humans)
raise AgentOutputValidationError(agent_name, response)
```

This pattern:
- Catches malformed JSON immediately (not silently downstream)
- Gives the agent one chance to self-correct
- Fails open if the agent cannot produce valid output

### Confidence-Based Gating

The Remediation agent (Agent 3) acts as a **safety gate** based on the Classification agent's confidence score:

| Confidence | Action |
|------------|--------|
| >= 0.8 | Execute the recommended action (rollback or escalate) |
| < 0.8 | Downgrade to notification-only — no destructive actions |

This prevents the system from taking irreversible actions when the diagnosis is uncertain.

### Max Tool Calls & Loop Protection

- **Max tool calls per agent**: 5 (prevents infinite loops in the agentic loop)
- **SQS DLQ**: After 3 failed Lambda invocations, the message is moved to the DLQ for manual review.

### Dry Run Mode

Setting the environment variable `DRY_RUN=true` on the Lambda function makes the agent:
- Run the full Summarization and Classification pipeline normally
- **Skip** the Remediation agent's actual tool execution (rollback, email)
- **Log** what the agent _would_ have done

This is useful for:
- Testing the agent's reasoning without side effects
- Building trust before enabling autonomous remediation
- Demo rehearsals

### Fail Open

If any agent in the pipeline fails (unexpected Claude response, API error, tool execution failure):
1. The Lambda catches the exception
2. Attempts to send a **fallback escalation email** with:
   - The raw alarm event
   - Which agent failed and why
   - Any partial results from earlier agents
3. If even the escalation email fails → the Lambda fails, SQS redelivers, eventual DLQ

---

## Error Handling

| Failure | Behavior | Recovery |
|---------|----------|----------|
| Claude API timeout/error | Anthropic SDK built-in retry (exponential backoff) | Automatic |
| Claude returns invalid JSON | Retry once with correction prompt, then fail open | Automatic (1 retry) |
| Pydantic validation fails | Retry once with validation error message, then fail open | Automatic (1 retry) |
| Claude calls unknown tool | Ignore tool call, log warning, continue loop | Automatic |
| Tool execution fails (e.g., ECS API error) | Return error message to Claude, let it decide | Automatic |
| Duplicate alarm event (SQS at-least-once) | Accepted risk for POC — see Production Considerations | N/A |
| Lambda timeout (300s) | Lambda fails, SQS redelivers | Automatic (max 3x) |
| All retries exhausted | Message goes to DLQ | Manual review |
| Secrets Manager unavailable | Lambda fails at cold start | SQS redelivery |

---

## Implementation Phases

### Phase 1: Infrastructure Foundation

**Goal**: Networking, ECS cluster, RDS, ALB — the base environment.

**Deliverables:**
- `networking.tf` — Default VPC data sources, security groups
- `ecs.tf` — ECS Fargate cluster (no services yet)
- `rds.tf` — RDS PostgreSQL db.t3.micro in public subnet
- `iam.tf` — ECS task execution role
- `main.tf` — Provider config, data sources
- `variables.tf` / `outputs.tf`

**Validation**: `terraform apply` succeeds, RDS is reachable from ECS tasks.

### Phase 2: Application Deployment

**Goal**: FastAPI app running on ECS Fargate behind ALB, connected to RDS.

**Deliverables:**
- `app/` — FastAPI application code with:
  - `GET /health` — checks DB connectivity
  - `GET /items` — queries sample table
  - `POST /items` — inserts into sample table
  - Structured JSON logging to stdout
  - Auto-creates tables on startup
- `app/Dockerfile` — Python slim image
- ECR Public repository
- `ecs.tf` updated — task definition, service, ALB target group
- Deploy script to push image and update service

**Validation**: `curl http://<alb-dns>/health` returns 200.

### Phase 3: AI Agent Core

**Goal**: Lambda function with the 3-agent pipeline, tested locally against real Claude API.

**Deliverables:**
- `agent/main.py` — Lambda handler, SQS event parsing, pipeline orchestration
- `agent/agents.py` — Summarization, Classification, Remediation agent classes
- `agent/tools.py` — Tool definitions (schemas) for Claude
- `agent/aws_actions.py` — Tool implementations (boto3 calls)
- `agent/config.py` — Constants, model IDs, resource names, DRY_RUN flag
- `agent/tracing.py` — Structured logging with trace IDs and token tracking
- `lambda.tf` — Lambda function, execution role, Secrets Manager
- Local testing with sample SQS event payloads and real Claude API

**Validation**: Local invocation with a mock SQS event produces correct agent pipeline output.

### Phase 4: Monitoring & Alerting Pipeline

**Goal**: CloudWatch alarms, EventBridge rules, SQS queue — the full alerting pipeline connecting app to agent.

**Deliverables:**
- `monitoring.tf` — CloudWatch alarms (running task count, error rate metric filter)
- `sqs.tf` — SQS queue, DLQ, EventBridge rule targeting SQS
- `lambda.tf` updated — SQS event source mapping
- `ses.tf` — SES email identity verification

**Validation**: Manually trigger a CloudWatch alarm → verify Lambda is invoked with correct event payload.

### Phase 5: Demo Scripts & Polish

**Goal**: End-to-end demo-ready system with scripts for triggering both scenarios.

**Deliverables:**
- `scripts/deploy-broken.sh` — Registers a broken task definition (wrong DATABASE_HOST) and updates the ECS service
- `scripts/deploy-healthy.sh` — Restores the healthy task definition (for resetting between demos)
- `scripts/stop-db.sh` — Stops the RDS instance
- `scripts/start-db.sh` — Starts the RDS instance
- `scripts/tail-agent-logs.sh` — Tails the Lambda CloudWatch log group with formatted output
- End-to-end testing of both scenarios
- README with demo instructions

**Validation**: Both scenarios run successfully end-to-end. Emails are received. Agent logs show full trace.

---

## Production Considerations

The following items are simplified for this POC. In a production environment, they would be addressed:

| Area | POC | Production |
|------|-----|------------|
| **Terraform state** | Local file | S3 + DynamoDB locking, separate state per environment |
| **Environments** | Single AWS account | Separate accounts for dev/staging/prod via AWS Organizations |
| **Agent deployment** | Single Lambda, 3 sequential calls | Step Functions orchestrating 3 separate Lambdas for independent scaling and monitoring |
| **Idempotency** | Not implemented — SQS at-least-once delivery accepted as risk | DynamoDB table with TTL and conditional writes to deduplicate alarm events and prevent duplicate rollbacks |
| **Incident store** | CloudWatch Logs only | DynamoDB table for incident history, deduplication, and audit trail |
| **Secrets rotation** | Manual API key in Secrets Manager | Automatic rotation with Secrets Manager rotation Lambda |
| **Observability** | CloudWatch structured logs | OpenTelemetry traces, X-Ray integration, custom CloudWatch dashboards |
| **Alerting channels** | Email only (SES) | Slack, PagerDuty, OpsGenie integration |
| **Multi-service** | Monitors one app | Configurable per-service alarm mappings, dynamic tool schemas |
| **Testing** | Local invocation with real API | Full CI/CD with unit tests, integration tests, and agent evaluation suites |
| **Cost controls** | Token logging only | Budget alerts, per-incident cost limits, model fallback (Sonnet → Haiku if budget exceeded) |
| **Approval workflow** | Fully autonomous | Human-in-the-loop approval for high-severity actions via Slack/web UI |
| **Network isolation** | Default VPC, public subnets only, SG-based access control | Private subnets for ECS/RDS, NAT Gateway, VPC endpoints, Lambda in VPC |
| **IAM** | Least privilege (resource-level) | AWS Organizations SCPs, permission boundaries, IAM Access Analyzer |

---

## Review Checklist

Summary of AI agent best practices applied in this architecture:

| # | Best Practice | Status | Implementation |
|---|--------------|--------|----------------|
| 1 | Idempotency for at-least-once delivery | Production | Documented as production requirement — DynamoDB with TTL and conditional writes |
| 2 | Schema validation between agents | Done | Pydantic models with 1-retry correction loop |
| 3 | Confidence scoring in classification | Done | Float 0.0-1.0 with guidelines per range |
| 4 | Reviewer/Gatekeeper pattern for destructive actions | Done | Agent 3 reviews confidence before executing rollback |
| 5 | Few-shot examples in system prompts | Done | Examples for each classification type and remediation scenario |
| 6 | Chain-of-thought before structured output | Done | `<reasoning>` tags in Classification agent |
| 7 | Temperature tuning per agent | Done | 0 for extraction/classification, 0.3 for remediation |
| 8 | Agent-level retry for invalid outputs | Done | 1-retry with correction prompt before fail-open |
| 9 | Observability on the agent itself | Done | Lambda error, DLQ depth, and duration alarms |
| 10 | Constrained tool schemas (enum values) | Done | All resource identifiers use enums |
| 11 | Fail open to humans | Done | Escalation email on any pipeline failure |
| 12 | Dry run mode | Done | `DRY_RUN` env var skips destructive actions |
| 13 | Token and cost tracking per agent step | Done | Structured JSON logs with trace ID |
| 14 | Defense in depth (2 LLM evaluations agree) | Done | Classification proposes, Remediation validates |
| 15 | Least-privilege tool access per agent | Done | Each agent only has the tools it needs |
| 16 | Sequential pipeline pattern (justified) | Done | Pattern comparison table with rationale |
