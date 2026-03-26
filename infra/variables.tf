variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-2"
}

variable "project_name" {
  description = "Project name used as prefix for all resources"
  type        = string
  default     = "remediation-agent"
}

variable "db_name" {
  description = "Name of the PostgreSQL database"
  type        = string
  default     = "remediationdb"
}

variable "db_username" {
  description = "Master username for the RDS instance"
  type        = string
  default     = "postgres"
}

variable "db_password" {
  description = "Master password for the RDS instance"
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.db_password) >= 8
    error_message = "Database password must be at least 8 characters."
  }
}

variable "db_instance_class" {
  description = "RDS instance class"
  type        = string
  default     = "db.t3.micro"
}

variable "app_image_tag" {
  description = "Docker image tag for the application"
  type        = string
  default     = "latest"
}
