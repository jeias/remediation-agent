# --- SQS Queue (receives alarm events from EventBridge) ---

resource "aws_sqs_queue" "agent" {
  name                       = "${var.project_name}-queue"
  visibility_timeout_seconds = 660
  message_retention_seconds  = 345600
  receive_wait_time_seconds  = 20

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.agent_dlq.arn
    maxReceiveCount     = 3
  })
}

# --- Dead Letter Queue ---

resource "aws_sqs_queue" "agent_dlq" {
  name                      = "${var.project_name}-dlq"
  message_retention_seconds = 1209600
}

# --- SQS Queue Policy (allow EventBridge to send messages) ---

resource "aws_sqs_queue_policy" "agent" {
  queue_url = aws_sqs_queue.agent.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowEventBridge"
        Effect    = "Allow"
        Principal = { Service = "events.amazonaws.com" }
        Action    = "sqs:SendMessage"
        Resource  = aws_sqs_queue.agent.arn
        Condition = {
          ArnEquals = {
            "aws:SourceArn" = aws_cloudwatch_event_rule.alarm_to_sqs.arn
          }
        }
      }
    ]
  })
}

# --- EventBridge Rule (routes CloudWatch alarm state changes to SQS) ---

resource "aws_cloudwatch_event_rule" "alarm_to_sqs" {
  name = "${var.project_name}-alarm-to-sqs"

  event_pattern = jsonencode({
    source      = ["aws.cloudwatch"]
    detail-type = ["CloudWatch Alarm State Change"]
    detail = {
      state = {
        value = ["ALARM"]
      }
      alarmName = [{ prefix = "${var.project_name}-" }]
    }
  })
}

resource "aws_cloudwatch_event_target" "sqs" {
  rule = aws_cloudwatch_event_rule.alarm_to_sqs.name
  arn  = aws_sqs_queue.agent.arn
}
