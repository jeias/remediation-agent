# Remediation Agent — Scenarios

## Overview

This project demonstrates an autonomous AI-powered remediation agent for cloud infrastructure. The agent monitors an ECS Fargate application connected to an RDS PostgreSQL database, diagnoses incidents using Claude (Anthropic's LLM), and takes corrective actions — all without human intervention.

The environment simulates a common production setup at scale: a containerized API backed by a managed relational database, with observability and alerting built on AWS-native services.

Two scenarios are demonstrated:

1. **Broken code deployment** — the agent detects, diagnoses, and rolls back a faulty release
2. **Database outage** — the agent detects, classifies, and escalates an infrastructure failure to the operations team

---

## Architecture

```
┌────────────┐
│  Developer │
└─────┬──────┘
      │ deploy
      ▼
┌──────────────────────────────────────────────────────────────┐
│                          AWS Cloud                           │
│                                                              │
│          ┌────────────────┐         ┌─────────────┐          │
│          │      APP       │────────▶│  Database   │          │
│          │ (ECS Fargate)  │         │   (RDS)     │          │
│          └───────┬────────┘         └─────────────┘          │
│                  │ logs                                      │
│                  ▼                                           │
│          ┌────────────────┐                                  │
│          │   CloudWatch   │                                  │
│          │  Logs / Alarms │                                  │
│          └───────┬────────┘                                  │
│                  │                                           │
│                  ▼                                           │
│          ┌────────────────┐                                  │
│          │  EventBridge   │                                  │
│          │    Rules       │                                  │
│          └───────┬────────┘                                  │
│                  │                                           │
│                  ▼                                           │
│          ┌────────────────┐                                  │
│          │   SQS Queue    │                                  │
│          └───────┬────────┘                                  │
│                  │ trigger                                   │
│                  ▼                                           │
│          ┌────────────────┐         ┌─────────────┐          │
│          │   AI Agent     │────────▶│    Email    │          │
│          │   (Lambda)     │         │  (AWS SES)  │          │
│          └───────┬────────┘         └─────────────┘          │                                  
│                  │                                           │
│                  │ rollback                                  │
│                  ▼                                           │
│          ┌────────────────┐                                  │
│          │      APP       │                                  │
│          │ (ECS Fargate)  │                                  │
│          └────────────────┘                                  │
└──────────────────────────────────────────────────────────────┘
```

**Key components:**

- **APP (ECS Fargate)** — the monitored application. A Python FastAPI service with health check and database endpoints.
- **Database (RDS)** — the application's PostgreSQL database.
- **CloudWatch Logs / Alarms** — collects application logs and triggers alarms on failure conditions.
- **EventBridge Rules** — routes CloudWatch alarm state changes to SQS.
- **SQS Queue** — decouples alarm events from the agent, triggers the Lambda function.
- **AI Agent (Lambda)** — the remediation agent. Python Lambda function using the Anthropic SDK. Claude reasons about incidents and decides which actions to take via tool calling.
- **AWS SES** — sends notification and escalation emails.

---

## Scenario 1: Broken Code Deployment

### Context

A developer deploys a new version of the FastAPI application that contains a code bug — a database query references a column (`description`) that doesn't exist in the `items` table. This simulates a common production incident where code is deployed expecting a database migration that was never applied.

The application starts successfully and passes health checks (`SELECT 1` works), but every call to `GET /items` fails with a `ProgrammingError`. This is a subtle, realistic bug — the service appears healthy from ECS's perspective, but is functionally broken.

### Flow

```
Developer deploys version with bad SQL query
        │
        ▼
ECS rolling deployment completes (health check passes)
        │
        ▼
Users/ALB hit GET /items → 500 errors with ProgrammingError
        │
        ▼
Error logs accumulate in CloudWatch
        │
        ▼
CloudWatch Alarm triggers (error rate threshold exceeded)
        │
        ▼
EventBridge routes alarm to SQS
        │
        ▼
Agent picks up event from SQS
        │
        ▼
Agent enters agentic loop with Claude
        │
        ├──▶ Tool: fetch_cloudwatch_logs
        │    Returns: repeated ProgrammingError on GET /items
        │
        ├──▶ Tool: describe_ecs_service
        │    Returns: new task definition deployed recently
        │
        ├──▶ Claude analyzes: "This is a code bug introduced in the
        │    latest deployment. The error is a ProgrammingError (code issue),
        │    not an OperationalError (infra issue). Rolling back."
        │
        ├──▶ Tool: rollback_ecs_service
        │    Action: Updates ECS service to previous task definition revision
        │
        └──▶ Tool: send_email
             Action: Notifies the team about the rollback
```

### Agent Reasoning (realistic Claude output)

```
I received an alarm indicating high error rates for the ECS service "remediation-agent-app".
Let me investigate by fetching the recent logs.

[Tool call: fetch_cloudwatch_logs]
  log_group_name: "/ecs/remediation-agent-app"
  minutes_ago: 10
  filter_pattern: "ERROR"

[Tool result]
  2026-03-25T14:35:12Z ERROR: Database connection failed
    psycopg2.errors.UndefinedColumn: column "description" does not exist
    LINE 1: SELECT id, name, description, created_at FROM items ORDER B...
  2026-03-25T14:35:10Z INFO: GET /items - 500
  2026-03-25T14:34:42Z ERROR: Database connection failed
    psycopg2.errors.UndefinedColumn: column "description" does not exist
  2026-03-25T14:34:40Z INFO: GET /items - 500
  2026-03-25T14:34:12Z INFO: GET /health - 200
  2026-03-25T14:34:00Z INFO: Starting application on 0.0.0.0:8000
  2026-03-25T14:33:55Z INFO: Database initialized successfully

The logs show repeated `psycopg2.errors.UndefinedColumn` errors on the GET /items
endpoint. The error is "column 'description' does not exist" — the application code
is querying a column that doesn't exist in the database. The health check still passes
(SELECT 1 succeeds), so ECS considers the task healthy.

Let me check the deployment state.

[Tool call: describe_ecs_service]
  cluster_name: "remediation-agent-cluster"
  service_name: "remediation-agent-app"

[Tool result]
  Current task definition: remediation-agent-app:5 (deployed 12 minutes ago)
  Previous task definition: remediation-agent-app:4
  Running count: 1, Desired count: 1

Task definition revision 5 was deployed 12 minutes ago, and the errors started
immediately after. The previous revision 4 was running without these errors.

This is a code bug — a ProgrammingError (not an OperationalError), meaning the issue
is in the application's SQL query, not in the database infrastructure. The code
references a column "description" that doesn't exist, likely expecting a database
migration that was never applied.

A rollback to revision 4 will fix this.

[Tool call: rollback_ecs_service]
  cluster_name: "remediation-agent-cluster"
  service_name: "remediation-agent-app"

[Tool result]
  Service updated. Rolling back from revision 5 to revision 4. Deployment in progress.

[Tool call: send_email]
  to: "team@company.com"
  subject: "[RESOLVED] Broken deployment rolled back — remediation-agent-app"
  body: "Incident summary:\n\n- Service: remediation-agent-app\n- Issue: Task definition
    revision 5 introduced a SQL query referencing a non-existent column 'description'
    in the items table. GET /items returned 500 errors.\n- Error:
    psycopg2.errors.UndefinedColumn: column 'description' does not exist\n-
    Action taken: Rolled back to revision 4.\n- Status: Service is recovering.\n\n
    Root cause: Code expected a column from a database migration that was never applied."
  severity: "HIGH"

Incident resolved. The ECS service has been rolled back from revision 5 to revision 4.
The team has been notified via email.
```

### Key Takeaway

The agent identified that the error was a **code bug** (`ProgrammingError` — wrong SQL query) rather than an infrastructure failure (`OperationalError` — database unreachable). It correlated the error timing with a recent deployment and autonomously rolled back the service. The health check kept passing throughout — this was a subtle bug that only affected specific endpoints, making the AI classification essential.

---

## Scenario 2: RDS Database Stopped

### Context

The RDS PostgreSQL instance is stopped (simulating a database outage — could be caused by maintenance, cost-saving automation, or a failure). The FastAPI application is running but cannot reach the database, resulting in connection errors on every request.

Unlike Scenario 1, this is **not a code problem** — the application code is correct, but the underlying infrastructure is unavailable. The agent must recognize this distinction and escalate rather than attempt a rollback.

### Flow

```
RDS instance is stopped
        │
        ▼
FastAPI app starts logging DB connection errors
        │
        ▼
CloudWatch Alarm triggers (error log threshold exceeded)
        │
        ▼
EventBridge routes alarm to SQS
        │
        ▼
Agent picks up event from SQS
        │
        ▼
Agent enters agentic loop with Claude
        │
        ├──▶ Tool: fetch_cloudwatch_logs
        │    Returns: repeated DB connection refused errors
        │
        ├──▶ Claude analyzes: "This is an infrastructure issue —
        │    the database is unreachable. The application code has not
        │    changed. This requires human intervention."
        │
        └──▶ Tool: send_email
             Action: Sends CRITICAL escalation to operations team
```

### Agent Reasoning (realistic Claude output)

```
I received an alarm indicating high error rates for the ECS service "remediation-app".
Let me investigate the logs.

[Tool call: fetch_cloudwatch_logs]
  log_group_name: "/ecs/remediation-app"
  minutes_ago: 10
  filter_pattern: "ERROR"

[Tool result]
  2026-03-25T15:10:45Z ERROR: Database connection failed
  2026-03-25T15:10:45Z psycopg2.OperationalError: could not connect to server:
    Connection refused. Is the server running on host
    "remediation-db.cluster-xxx.us-east-1.rds.amazonaws.com" (10.0.1.50)
    and accepting TCP/IP connections on port 5432?
  2026-03-25T15:10:30Z ERROR: Database connection failed
  2026-03-25T15:10:30Z psycopg2.OperationalError: could not connect to server:
    Connection refused.
  2026-03-25T15:10:15Z ERROR: Database connection failed
  2026-03-25T15:10:00Z ERROR: Database connection failed
  2026-03-25T15:09:45Z INFO: GET /health - 503
  2026-03-25T15:09:30Z INFO: GET /items - 503

The logs show repeated database connection failures. The error is "Connection refused"
on the correct database hostname — this means the database host resolves correctly but
the PostgreSQL service is not accepting connections on port 5432.

Key observations:
- The database hostname is correct (it's the same one that was working before)
- There was no recent deployment — the application task definition has not changed
- The error pattern is consistent: every request fails with the same connection error

This is NOT a code issue. This is an infrastructure problem — the RDS instance appears
to be stopped or unreachable. A rollback would not help because the application code
is correct. This requires human intervention.

Classifying this as CRITICAL and escalating to the operations team.

[Tool call: send_email]
  to: "ops-team@company.com"
  subject: "[CRITICAL] Database unreachable — remediation-app"
  body: "CRITICAL INCIDENT — Immediate attention required.\n\nService: remediation-app\n
    Issue: RDS PostgreSQL instance is unreachable. The application is returning 503 on
    all endpoints.\n\nDiagnosis:\n- Database host resolves correctly but refuses
    connections on port 5432\n- No recent code deployments — this is not a code issue\n-
    The RDS instance may be stopped or experiencing an outage\n\nImpact: Full service
    degradation. All database-dependent endpoints are failing.\n\nRecommended action:
    Check RDS instance status and restart if stopped."
  severity: "CRITICAL"

Incident escalated. The operations team has been notified with a CRITICAL severity email
containing the full diagnosis. A rollback was not attempted because this is an
infrastructure issue, not a deployment problem.
```

### Key Takeaway

The agent correctly distinguished between a **code issue** and an **infrastructure issue**. Instead of blindly rolling back (which would not fix anything), it escalated to the operations team with a detailed diagnosis including the root cause and recommended action.

---

## Live Demo Flow

### Preparation

- All infrastructure deployed via Terraform
- APP running healthy on ECS Fargate
- AI Agent deployed as Lambda, triggered by SQS
- SES email verified and ready

### Demo 1: Broken Deployment (~10 min)

1. **Show healthy state** — curl `/health` (200) and `/items` (200) to prove the app works
2. **Deploy broken version** — deploy a new image where `GET /items` queries a non-existent column (`description`)
3. **Show the subtle bug** — curl `/health` still returns 200, but `/items` returns 500 with `UndefinedColumn` error
4. **Agent reacts** — watch the agent logs as it picks up the error rate alarm, fetches logs, checks deployment state, and decides to rollback
5. **Rollback completes** — show `/items` working again with the previous task definition
6. **Email received** — show the notification email with the incident summary and root cause

### Demo 2: Database Outage (~10 min)

> **Demo tip**: The RDS instance is pre-stopped before starting the demo to avoid the 5-10 minute wait for RDS to fully stop. The agent's behavior is identical whether the DB was stopped 1 minute ago or 10 minutes ago — it reacts to the application errors, not the RDS event itself. During the interview, the full live flow (stopping RDS in real-time) can also be demonstrated if time allows.

1. **Show healthy state** — confirm app was healthy before DB was stopped
2. **Observe errors** — show the app returning 503s and DB connection errors in logs
3. **Agent reacts** — watch the agent logs as it picks up the alarm, fetches logs, analyzes, and decides to escalate
4. **Escalation sent** — agent classifies as CRITICAL, does NOT rollback
5. **Email received** — show the escalation email with the full diagnosis and recommended action
