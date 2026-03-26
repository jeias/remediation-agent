# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Autonomous AI remediation agent for cloud infrastructure — a POC for a Staff AI Engineer interview at Itaú. The agent monitors an ECS Fargate application, diagnoses incidents using a multi-agent Claude pipeline, and takes corrective actions (rollback or escalation).

## Architecture

Three-agent sequential pipeline running in a single Lambda:

1. **Summarization** (Haiku, temp=0) — extracts structured JSON from CloudWatch logs
2. **Classification** (Sonnet, temp=0) — diagnoses type/severity with confidence score, uses `<reasoning>` chain-of-thought
3. **Remediation** (Sonnet, temp=0.3) — reviewer/gatekeeper that gates execution on confidence >= 0.8, composes contextual emails

Key patterns: defense in depth (2 LLMs agree before rollback), Pydantic validation between agents, constrained tool schemas (enums prevent hallucination), fail-open escalation, DRY_RUN mode.

Full specification: `spec/ARCHITECTURE.md` and `spec/SCENARIOS.md`

## Repository Structure

```
spec/              # Architecture and scenario documentation (read first)
infra/             # Terraform (AWS provider 6.x, us-east-2, "dev" profile)
app/               # FastAPI application (Phase 2 — not yet created)
agent/             # Lambda remediation agent (Phase 3 — not yet created)
scripts/           # Demo scripts (Phase 5 — not yet created)
```

## Terraform Commands

```bash
cd infra
terraform init
terraform validate
terraform plan
terraform apply -auto-approve
terraform destroy -auto-approve
```

AWS profile `dev` is hardcoded in `main.tf`. All resources use `remediation-agent-*` prefix.

## Implementation Phases

1. **Phase 1** (done): Infra — VPC, ECS cluster, RDS PostgreSQL, ALB, IAM
2. **Phase 2**: App — FastAPI on ECS Fargate (health + DB endpoints, structured logging)
3. **Phase 3**: Agent — Lambda with 3-agent pipeline, tool implementations, tracing
4. **Phase 4**: Monitoring — CloudWatch alarms, EventBridge rules, SQS queue, SES
5. **Phase 5**: Demo — scripts to trigger both scenarios, polish

## Naming Convention

All AWS resources: `${var.project_name}-*` → `remediation-agent-*`

## Library Versions

ALWAYS use the Context7 MCP plugin (`resolve-library-id` then `query-docs`) to check the latest versions of any library, framework, or provider before writing code or Terraform. Never hardcode versions without verifying them first.

## Key Design Decisions

- **Sequential pipeline** over orchestrator pattern (workflow is deterministic)
- **Lambda** for agent (not ECS) — event-driven, no polling
- **Default VPC with public subnets** — POC simplicity, no NAT/private subnets
- **Confidence gating** — rollback only when confidence >= 0.8, else notify-only
- **No idempotency** in POC — documented as production requirement
- **RDS engine version** fetched dynamically via `aws_rds_engine_version` data source
