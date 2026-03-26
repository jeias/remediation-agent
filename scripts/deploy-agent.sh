#!/usr/bin/env bash
set -euo pipefail

AWS_REGION="us-east-2"
AWS_PROFILE="dev"
FUNCTION_NAME="remediation-agent-lambda"
AGENT_DIR="$(cd "$(dirname "$0")/../agent" && pwd)"
BUILD_DIR="/tmp/lambda-package"

echo "Building Lambda zip from: $AGENT_DIR"

# Clean build directory
rm -rf "$BUILD_DIR" /tmp/lambda.zip
mkdir -p "$BUILD_DIR"

# Export and install dependencies
cd "$AGENT_DIR"
uv export --frozen --no-dev --no-hashes > /tmp/requirements.txt
pip install -q -r /tmp/requirements.txt -t "$BUILD_DIR" --platform manylinux2014_x86_64 --only-binary=:all:

# Copy pipeline code + prompts
cp -r "$AGENT_DIR/pipeline" "$BUILD_DIR/pipeline"

# Create zip
cd "$BUILD_DIR"
zip -qr /tmp/lambda.zip .

ZIP_SIZE=$(du -sh /tmp/lambda.zip | cut -f1)
echo "Zip size: $ZIP_SIZE"

# Upload
echo "Uploading to Lambda..."
aws lambda update-function-code \
  --function-name "$FUNCTION_NAME" \
  --zip-file fileb:///tmp/lambda.zip \
  --region "$AWS_REGION" \
  --profile "$AWS_PROFILE" \
  --no-cli-pager \
  --query '{FunctionName: FunctionName, CodeSize: CodeSize, LastUpdateStatus: LastUpdateStatus}'

echo "Done."
