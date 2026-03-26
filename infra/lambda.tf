# --- Secrets Manager for Anthropic API Key ---

resource "aws_secretsmanager_secret" "anthropic_api_key" {
  name                    = "${var.project_name}/anthropic-api-key"
  recovery_window_in_days = 0
}

# --- CloudWatch Log Group for Lambda ---

resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${var.project_name}-lambda"
  retention_in_days = 7
}

# --- Lambda IAM Role ---

data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  name               = "${var.project_name}-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

# --- Lambda IAM Policy (Least Privilege) ---

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

data "aws_iam_policy_document" "lambda_permissions" {
  # CloudWatch Logs - read app logs
  statement {
    sid       = "ReadAppLogs"
    actions   = ["logs:FilterLogEvents"]
    resources = ["${aws_cloudwatch_log_group.app.arn}:*"]
  }

  # CloudWatch Logs - write agent logs
  statement {
    sid = "WriteLambdaLogs"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["${aws_cloudwatch_log_group.lambda.arn}:*"]
  }

  # ECS - describe and update service
  statement {
    sid = "ECSDescribeAndUpdate"
    actions = [
      "ecs:DescribeServices",
      "ecs:UpdateService",
    ]
    resources = [aws_ecs_service.app.id]
  }

  # ECS - describe task definitions (revision ARNs are impractical to scope)
  statement {
    sid       = "ECSDescribeTaskDef"
    actions   = ["ecs:DescribeTaskDefinition"]
    resources = ["*"]
  }

  # SES - send email
  statement {
    sid       = "SESSendEmail"
    actions   = ["ses:SendEmail", "ses:SendRawEmail"]
    resources = ["arn:aws:ses:${data.aws_region.current.id}:${data.aws_caller_identity.current.account_id}:identity/*"]
  }

  # Secrets Manager - read API key
  statement {
    sid       = "ReadAnthropicSecret"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.anthropic_api_key.arn]
  }
}

resource "aws_iam_role_policy" "lambda" {
  name   = "${var.project_name}-lambda-policy"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda_permissions.json
}

# --- Lambda Function (Zip deployment — deploy-agent.sh uploads the code) ---

resource "aws_lambda_function" "agent" {
  function_name = "${var.project_name}-lambda"
  role          = aws_iam_role.lambda.arn
  package_type  = "Zip"
  runtime       = "python3.13"
  handler       = "pipeline.main.handler"
  filename      = "${path.module}/placeholder.zip"
  timeout       = 300
  memory_size   = 512

  environment {
    variables = {
      DRY_RUN              = "true"
      CLUSTER_NAME         = aws_ecs_cluster.main.name
      SERVICE_NAME         = aws_ecs_service.app.name
      LOG_GROUP            = aws_cloudwatch_log_group.app.name
      ANTHROPIC_SECRET_ARN = aws_secretsmanager_secret.anthropic_api_key.arn
      SES_SENDER           = var.ses_sender_email
      SES_TEAM_RECIPIENT   = var.ses_sender_email
      SES_OPS_RECIPIENT    = var.ses_sender_email
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.lambda,
    aws_iam_role_policy.lambda,
  ]

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }
}
