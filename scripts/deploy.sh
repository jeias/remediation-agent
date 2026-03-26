#!/usr/bin/env bash
set -euo pipefail

AWS_REGION="us-east-2"
AWS_PROFILE="dev"
CLUSTER_NAME="remediation-agent-cluster"
SERVICE_NAME="remediation-agent-app"

# Get ECR repository URL from Terraform outputs
ECR_REPO=$(cd "$(dirname "$0")/../infra" && terraform output -raw ecr_repository_url)
ACCOUNT_ID=$(aws sts get-caller-identity --profile "$AWS_PROFILE" --query Account --output text)

echo "ECR Repository: $ECR_REPO"

# Authenticate Docker with ECR
aws ecr get-login-password --region "$AWS_REGION" --profile "$AWS_PROFILE" | \
  docker login --username AWS --password-stdin "$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"

# Build and push
IMAGE_TAG="${1:-latest}"
echo "Building image with tag: $IMAGE_TAG"
docker build --platform linux/amd64 -t "$ECR_REPO:$IMAGE_TAG" "$(dirname "$0")/../app"
docker push "$ECR_REPO:$IMAGE_TAG"

# Force new deployment
echo "Triggering ECS deployment..."
aws ecs update-service \
  --cluster "$CLUSTER_NAME" \
  --service "$SERVICE_NAME" \
  --force-new-deployment \
  --region "$AWS_REGION" \
  --profile "$AWS_PROFILE" \
  --no-cli-pager

echo "Deployment triggered. Image: $ECR_REPO:$IMAGE_TAG"
