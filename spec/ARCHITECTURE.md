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
│  │  Output:         │   │  + get_task_def   │   │  Role:               │  │
│  │  Structured JSON │   │  + compare_commits│   │  REVIEWER/GATEKEEPER │  │
│  │                  │   │  + describe_rds   │   │                      │  │
│  │  summary         │   │                   │   │  Reviews confidence, │  │
│  │                  │   │  Output:          │   │  gates execution,    │  │
│  │                  │   │  Classification   │   │  composes email      │  │
│  │                  │   │  + confidence     │   │                      │  │
│  │                  │   │  + reasoning      │   │                      │  │
│  └──────────────────┘   └───────────────────┘   └──────────────────────┘  │
│                                                                           │
│  Validation: Structured outputs (API-enforced) + strict tool use           │
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
│          uses get_task_definition + compare_git_commits for diffs │
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
    first_seen: str | None = None
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

**Model**: Claude Sonnet (`claude-sonnet-4-6`)
**Temperature**: `0` (deterministic classification, consistency is critical)
**Purpose**: Classify the incident type and severity, recommend an action, and express confidence. Uses the summary from Agent 1 plus ECS service state to make an informed decision.

### System Prompt

Full prompt in `agent/pipeline/prompts/classification.yaml`. Key design:

**Triage-first pattern** — the agent reads `error_type` from the summary, then chooses an investigation path:

- **PATH A (Deployment)**: `query_error`/`import_error` → `describe_ecs_service` → `get_task_definition` x2 → `compare_git_commits` → classify with code diff evidence
- **PATH B (Infrastructure)**: `connection_error` → `describe_ecs_service` → `describe_rds_instance` → classify with RDS status → escalate
- **PATH C (Ambiguous)**: unclear → gather more evidence → decide

This ensures the agent uses the right tools for each scenario — no wasted code analysis for infrastructure issues, no skipped diagnosis for deployment issues.

### Output Schema (Pydantic)

```python
class ClassificationOutput(BaseModel):
    reasoning: str = Field(description="Step-by-step reasoning before classification")
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
| `get_task_definition` | Inspect a specific revision's container image tag (git SHA) |
| `compare_git_commits` | Compare two git SHAs to see what code changed between deployments |
| `describe_rds_instance` | Check RDS database status (stopped/available) for infrastructure diagnosis |

---

## Agent 3: Remediation (Reviewer/Gatekeeper)

**Model**: Claude Sonnet (`claude-sonnet-4-6`)
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
- If confidence >= 0.7 AND you agree with the classification:
  1. Call rollback_ecs_service to revert to the previous task definition
  2. Call send_email to notify the team (severity: HIGH)
- If confidence < 0.7 OR you disagree with the classification:
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

The confidence is below the 0.7 threshold and the errors are intermittent — this could
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
    verified: bool | None = Field(default=None, description="Null — verification not performed by this agent")
    details: str
```

### Tools

| Tool | Purpose |
|------|---------|
| `rollback_ecs_service` | Update ECS service to the previous task definition revision. Only use when classification confidence >= 0.7 and type is "deployment". |
| `send_email` | Send notification or escalation email via AWS SES. Always called — either as action notification or escalation. |

---

## Tool Definitions

All tools use **strict mode** (`strict: true`) with `additionalProperties: false` — the Anthropic API enforces schema compliance at the token level. Enum constraints, required fields, and parameter types are guaranteed, not just suggested.

Tool definitions are in `agent/pipeline/tools.py`. Tool implementations are in `agent/pipeline/aws_actions.py`.

### Tool Sets per Agent (Least-Privilege)

| Agent | Tools |
|-------|-------|
| Summarization (Haiku) | `fetch_cloudwatch_logs` |
| Classification (Sonnet) | `fetch_cloudwatch_logs`, `describe_ecs_service`, `get_task_definition`, `compare_git_commits`, `describe_rds_instance` |
| Remediation (Sonnet) | `rollback_ecs_service`, `send_email` |

### fetch_cloudwatch_logs

Fetches recent log events from the application's CloudWatch log group.

- Returns max 50 log lines (newest first)
- `log_group_name` constrained to enum
- Used by Summarization (investigation) and Classification (additional context)

### describe_ecs_service

Returns current deployment state: task definition revisions, running count, deployment timestamps.

- `cluster_name` and `service_name` constrained to enums
- Used by Classification to detect recent deployments

### get_task_definition

Gets details of a specific ECS task definition revision, including container image URI and tag (git SHA).

- `task_family` constrained to enum
- Agent extracts the image tag (git SHA) to identify what code each revision runs
- Used by Classification to compare current vs previous deployment

### compare_git_commits

Compares two git commits via GitHub API and returns code changes (commit messages, file diffs).

- Agent provides base_sha and head_sha (extracted from task definitions)
- Returns max 5 commits, max 10 files, patches truncated to 500 chars
- Graceful degradation: returns error JSON if GitHub API is unavailable — agent continues with lower confidence
- Used by Classification to correlate code changes with error patterns

### describe_rds_instance

Checks the status of the RDS database instance. Used by Classification in PATH B (infrastructure) to determine WHY the database is unreachable.

- `db_instance_identifier` constrained to enum
- Returns: status (`available`, `stopped`, `starting`, etc.), engine, endpoint, port
- Agent interprets the status: "stopped" = manually stopped, "available" + connection refused = network issue
- Only called for infrastructure investigations, NOT for deployment issues

### rollback_ecs_service

Rolls back the ECS service to the previous task definition revision.

- `cluster_name` and `service_name` constrained to enums
- After calling `ecs:UpdateService`, **polls ECS every 10s** until deployment is stable (running_count matches desired_count, single PRIMARY deployment) or timeout
- Returns `deployment_stable: true/false` and `wait_seconds`
- Prevents rollback to revision 1 (no previous revision)
- Respects DRY_RUN mode

### send_email

Sends incident notification or escalation email via AWS SES.

- `to` constrained to configured email addresses (from env vars)
- `severity` constrained to enum: LOW, MEDIUM, HIGH, CRITICAL
- Respects DRY_RUN mode

---

## Infrastructure

### Tech Stack

| Component | Technology | Naming |
|-----------|-----------|--------|
| Compute (App) | ECS Fargate (0.25 vCPU / 0.5 GB) | `remediation-agent-app` |
| Compute (Agent) | AWS Lambda (512 MB / 600s timeout, ZIP deployment) | `remediation-agent-lambda` |
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

# RDS — describe instance status
rds:DescribeDBInstances on remediation-agent-db ARN

# Secrets Manager — read API key + GitHub token
secretsmanager:GetSecretValue on arn:aws:secretsmanager:*:*:secret:remediation-agent/*

# IAM — pass roles to ECS when updating service (AWS best practice)
iam:GetRole, iam:PassRole on ECS execution + task role ARNs
  Condition: iam:PassedToService = ecs-tasks.amazonaws.com
```

### SQS Configuration

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Queue Type | Standard | Ordering not critical for alarm events |
| Visibility Timeout | 660s | Greater than Lambda timeout (600s) to prevent duplicate processing |
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
  "model": "claude-sonnet-4-6",
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

The Classification agent's `reasoning` field (from structured output) is logged separately for debugging and audit:

```json
{
  "trace_id": "inc-20260325-143201-a1b2c3",
  "agent": "classification",
  "step": "reasoning",
  "content": "ECS shows revision 5 deployed 12 min ago. get_task_definition shows image tag changed from abc123f to def456a. compare_git_commits shows app/main.py added 'description' column to SQL query. This directly matches the UndefinedColumn error..."
}
```

### Agent Health Alarms

In addition to app-level alarms, the agent itself is monitored:

| Alarm Name | Metric | Condition | Purpose |
|------------|--------|-----------|---------|
| `remediation-agent-lambda-errors` | Lambda `Errors` | > 0 in 5 min | Agent Lambda is failing |
| `remediation-agent-dlq-depth` | SQS DLQ `ApproximateNumberOfMessagesVisible` | > 0 | Unprocessable incidents accumulating |
| `remediation-agent-lambda-duration` | Lambda `Duration` | > 500000ms | Approaching timeout (600s) |

---

## Guardrails & Safety

### Strict Tool Use + Structured Outputs (API-Enforced)

Two complementary Anthropic API features enforce schema compliance at the token level:

**Strict tool use** (`strict: true` on all tools):
- Tool inputs are guaranteed to match the `input_schema`
- Enum constraints (cluster name, service name, email recipients) are enforced by the API, not just suggested
- `additionalProperties: false` prevents agents from inventing extra parameters

**Structured outputs** (`output_config` with `json_schema`):
- Each agent's final response is guaranteed valid JSON matching the Pydantic schema
- No retry loops needed for malformed output — the API handles it
- `transform_schema()` from the Anthropic SDK converts Pydantic models to the required format

Together, these eliminate an entire class of agent reliability issues (invalid JSON, hallucinated parameters, wrong types) without any application-level validation code.

### Confidence-Based Gating

The Remediation agent (Agent 3) acts as a **safety gate** based on the Classification agent's confidence score:

| Confidence | Action |
|------------|--------|
| >= 0.7 | Execute the recommended action (rollback or escalate) |
| < 0.7 | Downgrade to notification-only — no destructive actions |

This prevents the system from taking irreversible actions when the diagnosis is uncertain.

### Max Tool Calls & Loop Protection

- **Max tool calls per agent**: 5 default, 12 for Classification (5 tools, may call some twice), 8 for Remediation
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
- `agent/pipeline/config.py` — Constants, resource names, DRY_RUN flag
- `agent/pipeline/schemas.py` — Pydantic output models
- `agent/pipeline/tracing.py` — Structured logging with trace IDs and token tracking
- `agent/pipeline/prompts/*.yaml` — System prompts with model and temperature per agent
- `agent/test_local.py` — Local integration test
- `infra/lambda.tf` — Lambda function (ZIP), execution role, Secrets Manager
- `scripts/deploy-agent.sh` — Build ZIP and update Lambda

**Validation**: Local invocation with a mock SQS event produces correct agent pipeline output.

### Phase 4: Monitoring & Alerting Pipeline (done)

**Goal**: CloudWatch alarms, EventBridge rules, SQS queue — the full alerting pipeline connecting app to agent.

**Deliverables:**
- `infra/monitoring.tf` — CloudWatch alarms (error rate metric filter, Lambda errors, DLQ depth, Lambda duration)
- `infra/sqs.tf` — SQS queue, DLQ, EventBridge rule targeting SQS
- `infra/lambda.tf` updated — SQS event source mapping (batch size 1, max concurrency 2)
- `infra/ses.tf` — SES email identity verification

**Validation**: Manually trigger a CloudWatch alarm → Lambda invoked with correct event payload → agent pipeline runs.

### Phase 5: Demo Scripts & Polish (done)

**Goal**: End-to-end demo-ready system with scripts for triggering both scenarios.

**Deliverables:**
- `scripts/commit-and-deploy.sh` — Commits changes, pushes, deploys app with git SHA tag
- `scripts/call-items.sh` — Continuously calls GET /items (Ctrl+C to stop)
- `scripts/deploy.sh` — Builds Docker image, tags with git SHA, registers new task def, updates ECS service
- `scripts/deploy-agent.sh` — Builds Lambda ZIP, uploads to AWS
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
| 1 | Structured outputs (API-enforced JSON) | Done | `output_config` + `transform_schema` guarantees valid JSON matching Pydantic schemas |
| 2 | Strict tool use (API-enforced inputs) | Done | `strict: true` + `additionalProperties: false` on all 6 tools — enum constraints enforced at token level |
| 3 | Confidence scoring in classification | Done | Float 0.0-1.0 with guidelines per range (0.95+ with code diff match) |
| 4 | Reviewer/Gatekeeper pattern for destructive actions | Done | Agent 3 reviews confidence before executing rollback |
| 5 | Few-shot examples in YAML prompts | Done | Examples for each classification type and remediation scenario |
| 6 | Chain-of-thought as structured output field | Done | `reasoning` field in ClassificationOutput (not XML tags) |
| 7 | Temperature tuning per agent | Done | 0 for extraction/classification, 0.3 for remediation (configured in YAML) |
| 8 | Atomic tools, agent orchestrates | Done | Classification agent uses 4 tools in sequence: describe_ecs → get_task_def x2 → compare_commits |
| 9 | Code diff for root cause analysis | Done | `get_task_definition` + `compare_git_commits` tools correlate code changes with errors |
| 10 | Constrained tool schemas (enum values) | Done | All resource identifiers use enums, enforced by strict mode |
| 11 | Fail open to humans | Done | Escalation email on any pipeline failure |
| 12 | Dry run mode | Done | `DRY_RUN` env var skips destructive actions (defaults to true) |
| 13 | Token and cost tracking per agent step | Done | Structured JSON logs with trace ID |
| 14 | Defense in depth (2 LLM evaluations agree) | Done | Classification proposes, Remediation validates |
| 15 | Least-privilege tool access per agent | Done | Summarization: 1 tool, Classification: 4 tools, Remediation: 2 tools |
| 16 | Sequential pipeline pattern (justified) | Done | Pattern comparison table with rationale |
| 17 | Rollback with deployment verification | Done | `rollback_ecs_service` polls ECS until deployment stable before returning |
| 18 | Git SHA image tagging | Done | Deploy script tags images with commit SHA for traceability |
| 19 | YAML-based prompt management | Done | System prompts in `pipeline/prompts/*.yaml`, separate from code |
| 20 | Observability on the agent itself | Done | Lambda error, DLQ depth, and duration CloudWatch alarms |
| 21 | Infrastructure diagnosis via RDS status | Done | `describe_rds_instance` tool reports DB state (stopped/available) for infrastructure issues |
| 22 | Idempotency for at-least-once delivery | Production | Documented as production requirement |
