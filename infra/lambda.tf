# --- Secrets Manager for Anthropic API Key ---

resource "aws_secretsmanager_secret" "anthropic_api_key" {
  name                    = "${var.project_name}/anthropic-api-key"
  recovery_window_in_days = 0
}

# --- Secrets Manager for GitHub Token ---

resource "aws_secretsmanager_secret" "github_token" {
  name                    = "${var.project_name}/github-token"
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

  # RDS - describe instance status (for infrastructure diagnosis)
  statement {
    sid       = "RDSDescribe"
    actions   = ["rds:DescribeDBInstances"]
    resources = [aws_db_instance.main.arn]
  }

  # IAM - pass role to ECS when updating service task definition
  # Required by UpdateService when changing task definitions (AWS best practice)
  statement {
    sid       = "PassRoleToECS"
    actions   = ["iam:GetRole", "iam:PassRole"]
    resources = [
      aws_iam_role.ecs_task_execution.arn,
      aws_iam_role.ecs_task.arn,
    ]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
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
    resources = [
      aws_secretsmanager_secret.anthropic_api_key.arn,
      aws_secretsmanager_secret.github_token.arn,
    ]
  }

  # SQS - receive and delete messages
  statement {
    sid = "SQSReceive"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
    ]
    resources = [aws_sqs_queue.agent.arn]
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
  timeout       = 600
  memory_size   = 512

  environment {
    variables = {
      DRY_RUN              = "false"
      CLUSTER_NAME         = aws_ecs_cluster.main.name
      SERVICE_NAME         = aws_ecs_service.app.name
      LOG_GROUP            = aws_cloudwatch_log_group.app.name
      ANTHROPIC_SECRET_ARN = aws_secretsmanager_secret.anthropic_api_key.arn
      SES_SENDER           = var.ses_sender_email
      SES_TEAM_RECIPIENT   = var.ses_sender_email
      SES_OPS_RECIPIENT    = var.ses_sender_email
      VERIFICATION_WAIT_SECONDS = "120"
      GITHUB_REPO              = "jeias/remediation-agent"
      GITHUB_TOKEN_SECRET_ARN  = aws_secretsmanager_secret.github_token.arn
      TASK_FAMILY              = aws_ecs_task_definition.app.family
      DB_INSTANCE_ID           = aws_db_instance.main.identifier
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

# --- SQS → Lambda Event Source Mapping ---

resource "aws_lambda_event_source_mapping" "sqs_to_lambda" {
  event_source_arn                   = aws_sqs_queue.agent.arn
  function_name                      = aws_lambda_function.agent.arn
  batch_size                         = 1
  maximum_batching_window_in_seconds = 0
  enabled                            = true

  scaling_config {
    maximum_concurrency = 2
  }
}
