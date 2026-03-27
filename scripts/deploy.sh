#!/usr/bin/env bash
set -euo pipefail

AWS_REGION="us-east-2"
AWS_PROFILE="dev"
CLUSTER_NAME="remediation-agent-cluster"
SERVICE_NAME="remediation-agent-app"
TASK_FAMILY="remediation-agent-app"

# Get ECR repository URL from Terraform outputs
ECR_REPO=$(cd "$(dirname "$0")/../infra" && terraform output -raw ecr_repository_url)
ACCOUNT_ID=$(aws sts get-caller-identity --profile "$AWS_PROFILE" --query Account --output text)

echo "ECR Repository: $ECR_REPO"

# Authenticate Docker with ECR
aws ecr get-login-password --region "$AWS_REGION" --profile "$AWS_PROFILE" | \
  docker login --username AWS --password-stdin "$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"

# Build and push
IMAGE_TAG="${1:-$(git rev-parse --short HEAD)}"
echo "Building image with tag: $IMAGE_TAG"
docker build --platform linux/amd64 -t "$ECR_REPO:$IMAGE_TAG" "$(dirname "$0")/../app"
docker push "$ECR_REPO:$IMAGE_TAG"

# Register new task definition revision (copies current, bumps revision number)
echo "Registering new task definition revision..."
CURRENT_TASK_DEF=$(aws ecs describe-task-definition \
  --task-definition "$TASK_FAMILY" \
  --region "$AWS_REGION" \
  --profile "$AWS_PROFILE" \
  --query 'taskDefinition' \
  --output json)

# Strip non-registrable fields and update image tag to new SHA
NEW_IMAGE="$ECR_REPO:$IMAGE_TAG"
NEW_TASK_DEF=$(echo "$CURRENT_TASK_DEF" | python3 -c "
import json, sys
td = json.load(sys.stdin)
for key in ['taskDefinitionArn', 'revision', 'status', 'requiresAttributes', 'compatibilities', 'registeredAt', 'registeredBy']:
    td.pop(key, None)
td['containerDefinitions'][0]['image'] = '$NEW_IMAGE'
print(json.dumps(td))
")

NEW_REVISION=$(aws ecs register-task-definition \
  --cli-input-json "$NEW_TASK_DEF" \
  --region "$AWS_REGION" \
  --profile "$AWS_PROFILE" \
  --query 'taskDefinition.revision' \
  --output text)

echo "New task definition revision: $NEW_REVISION"

# Update service to use new revision
aws ecs update-service \
  --cluster "$CLUSTER_NAME" \
  --service "$SERVICE_NAME" \
  --task-definition "${TASK_FAMILY}:${NEW_REVISION}" \
  --force-new-deployment \
  --region "$AWS_REGION" \
  --profile "$AWS_PROFILE" \
  --no-cli-pager \
  --query 'service.taskDefinition'

echo "Deployment triggered. Revision: $NEW_REVISION, Image: $ECR_REPO:$IMAGE_TAG"
