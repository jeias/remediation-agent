# --- Metric Filter (counts ERROR lines in app logs) ---

resource "aws_cloudwatch_log_metric_filter" "app_errors" {
  name           = "${var.project_name}-app-errors"
  log_group_name = aws_cloudwatch_log_group.app.name
  pattern        = "ERROR"

  metric_transformation {
    name          = "AppErrorCount"
    namespace     = "RemediationAgent"
    value         = "1"
    default_value = "0"
  }
}

# --- App-Level Alarm: Error Rate ---

resource "aws_cloudwatch_metric_alarm" "error_rate" {
  alarm_name          = "${var.project_name}-error-rate"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "AppErrorCount"
  namespace           = "RemediationAgent"
  period              = 60
  statistic           = "Sum"
  threshold           = 10
  alarm_description   = "App error rate exceeded threshold — triggers remediation agent"
  treat_missing_data  = "notBreaching"
}

# --- Agent Health Alarm: Lambda Errors ---

resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  alarm_name          = "${var.project_name}-lambda-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "Remediation agent Lambda is failing"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.agent.function_name
  }
}

# --- Agent Health Alarm: DLQ Depth ---

resource "aws_cloudwatch_metric_alarm" "dlq_depth" {
  alarm_name          = "${var.project_name}-dlq-depth"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 60
  statistic           = "Maximum"
  threshold           = 0
  alarm_description   = "Unprocessable incidents accumulating in DLQ"
  treat_missing_data  = "notBreaching"

  dimensions = {
    QueueName = aws_sqs_queue.agent_dlq.name
  }
}

# --- Agent Health Alarm: Lambda Duration ---

resource "aws_cloudwatch_metric_alarm" "lambda_duration" {
  alarm_name          = "${var.project_name}-lambda-duration"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Duration"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Maximum"
  threshold           = 500000
  alarm_description   = "Remediation agent approaching timeout (600s)"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.agent.function_name
  }
}
