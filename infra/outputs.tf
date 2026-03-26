output "vpc_id" {
  description = "Default VPC ID"
  value       = data.aws_vpc.default.id
}

output "public_subnet_ids" {
  description = "Public subnet IDs"
  value       = data.aws_subnets.public.ids
}

output "ecs_cluster_name" {
  description = "ECS cluster name"
  value       = aws_ecs_cluster.main.name
}

output "ecs_cluster_arn" {
  description = "ECS cluster ARN"
  value       = aws_ecs_cluster.main.arn
}

output "alb_dns_name" {
  description = "ALB DNS name"
  value       = aws_lb.main.dns_name
}

output "alb_target_group_arn" {
  description = "ALB target group ARN for the app"
  value       = aws_lb_target_group.app.arn
}

output "rds_endpoint" {
  description = "RDS endpoint (host:port)"
  value       = aws_db_instance.main.endpoint
}

output "rds_address" {
  description = "RDS hostname (without port)"
  value       = aws_db_instance.main.address
}

output "rds_port" {
  description = "RDS port"
  value       = aws_db_instance.main.port
}

output "ecs_security_group_id" {
  description = "ECS task security group ID"
  value       = aws_security_group.ecs.id
}

output "alb_security_group_id" {
  description = "ALB security group ID"
  value       = aws_security_group.alb.id
}

output "rds_security_group_id" {
  description = "RDS security group ID"
  value       = aws_security_group.rds.id
}

output "ecr_repository_url" {
  description = "ECR repository URL for the app image"
  value       = aws_ecr_repository.app.repository_url
}

output "app_log_group_name" {
  description = "CloudWatch log group for the app (AI agent reads this)"
  value       = aws_cloudwatch_log_group.app.name
}
