data "aws_rds_engine_version" "postgres" {
  engine = "postgres"
  latest = true

  filter {
    name   = "engine-mode"
    values = ["provisioned"]
  }
}

resource "aws_db_subnet_group" "main" {
  name       = "${var.project_name}-db-subnet"
  subnet_ids = data.aws_subnets.public.ids
}

resource "aws_db_instance" "main" {
  identifier = "${var.project_name}-db"

  engine         = data.aws_rds_engine_version.postgres.engine
  engine_version = data.aws_rds_engine_version.postgres.version
  instance_class = var.db_instance_class

  allocated_storage = 20
  storage_type      = "gp2"

  db_name  = var.db_name
  username = var.db_username
  password = var.db_password

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.rds.id]

  publicly_accessible     = true
  skip_final_snapshot     = true
  backup_retention_period = 0
  multi_az                = false
  apply_immediately       = true
}
