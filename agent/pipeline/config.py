import os

# --- Agent Limits ---
MAX_TOKENS = 8192

# --- AWS Resources (match Terraform naming) ---
CLUSTER_NAME = os.environ.get("CLUSTER_NAME", "remediation-agent-cluster")
SERVICE_NAME = os.environ.get("SERVICE_NAME", "remediation-agent-app")
LOG_GROUP_NAME = os.environ.get("LOG_GROUP", "/ecs/remediation-agent-app")

# --- Agent Limits ---
CONFIDENCE_THRESHOLD = 0.7
MAX_TOOL_CALLS = 5
MAX_LOG_LINES = 50

# --- GitHub (for code diff analysis) ---
GITHUB_REPO = os.environ.get("GITHUB_REPO", "jeias/remediation-agent")
GITHUB_TOKEN_SECRET_ARN = os.environ.get("GITHUB_TOKEN_SECRET_ARN", "")
TASK_FAMILY = os.environ.get("TASK_FAMILY", "remediation-agent-app")

# --- Verification ---
VERIFICATION_WAIT_SECONDS = int(os.environ.get("VERIFICATION_WAIT_SECONDS", "180"))

# --- Operational ---
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"
ANTHROPIC_SECRET_ARN = os.environ.get("ANTHROPIC_SECRET_ARN", "remediation-agent/anthropic-api-key")

# --- SES ---
SES_SENDER = os.environ.get("SES_SENDER", "noreply@example.com")
SES_TEAM_RECIPIENT = os.environ.get("SES_TEAM_RECIPIENT", "team@example.com")
SES_OPS_RECIPIENT = os.environ.get("SES_OPS_RECIPIENT", "ops@example.com")
