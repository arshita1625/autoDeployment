# ──────────────────────────────────────────────────────────────────────────────
# provisioner.py  –  "Step-4"  Terraform‐Provisioning Layer (FIXED)
#
#  INPUT   :  the deployment plan returned by DecisionEngine (step-3)
#  OUTPUT  :  • provisions cloud resources with Terraform
#             • returns a dict of runtime endpoints (public IP, DB URL, …)
#
#  External deps:  pip install jinja2
# ──────────────────────────────────────────────────────────────────────────────
from __future__ import annotations
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List

from jinja2 import Template


class TerraformProvisioner:
    """
    Example:
        prov = TerraformProvisioner(plan, repo_facts)
        outputs = prov.apply()          # blocks until  terraform apply  finishes
    """

    def __init__(self, plan: Dict, repo_facts: Dict) -> None:
        self.plan = plan
        self.facts = repo_facts

        # create disposable working dir
        self.workdir = Path(tempfile.mkdtemp()) / "tf"
        self.workdir.mkdir(parents=True, exist_ok=True)
        print(f"📁 Working directory: {self.workdir}")

    # ──────────────────────────────────────────────────────────────
    #  public API
    # ──────────────────────────────────────────────────────────────
    def apply(self) -> Dict:
        """Render templates ➜ terraform init + apply ➜ return outputs."""
        self._render()
        self._terraform_init()
        self._terraform_apply()
        return self._terraform_output()

    # ──────────────────────────────────────────────────────────────
    #  private helpers
    # ──────────────────────────────────────────────────────────────
    # 1. template rendering
    def _render(self) -> None:
        """Generate Terraform files from simple Jinja templates."""
        strategy = self.plan["strategy"]

        # always write provider & variables files
        (self.workdir / "provider.tf").write_text(_PROVIDER_TMPL.render(
            region=self.plan["region"]
        ))

        (self.workdir / "variables.tf").write_text(_VARIABLES_TMPL)

        # strategy-specific main.tf - FIXED: pass both plan and facts to template
        if strategy in {"EC2", "EC2-GPU"}:
            (self.workdir / "main.tf").write_text(_EC2_TMPL.render(
                instance_type=self.plan["size"] or "t3.small",
                facts=self.facts,
                plan=self.plan
            ))
        elif strategy == "Lambda":
            (self.workdir / "main.tf").write_text(_LAMBDA_TMPL.render(
                facts=self.facts,
                plan=self.plan
            ))
        elif strategy == "ECS":
            (self.workdir / "main.tf").write_text(_ECS_TMPL.render(
                cpu="512",
                memory="1024",
                facts=self.facts,
                plan=self.plan
            ))
        else:
            raise ValueError(f"Unsupported strategy: {strategy}")

        # optional DB
        if db := self.plan.get("db"):
            (self.workdir / "db.tf").write_text(_RDS_TMPL.render(
                db=db,
                facts=self.facts,
                plan=self.plan
            ))

    # 2. terraform subprocess wrappers
    def _terraform_init(self) -> None:
        print("🔧 Running terraform init...")
        subprocess.check_call(["terraform", "init", "-upgrade"],
                              cwd=self.workdir)

    def _terraform_apply(self) -> None:
        print("🚀 Running terraform apply...")
        subprocess.check_call(
            ["terraform", "apply", "-auto-approve"],
            cwd=self.workdir
        )

    def _terraform_output(self) -> Dict:
        print("📊 Getting terraform outputs...")
        completed = subprocess.run(
            ["terraform", "output", "-json"],
            cwd=self.workdir,
            capture_output=True,
            text=True,
            check=True
        )
        raw_outputs = json.loads(completed.stdout)

        # Terraform outputs have format: {"key": {"value": actual_value}}
        # Flatten to just {"key": actual_value}
        return {k: v.get("value") for k, v in raw_outputs.items()}


# ──────────────────────────────────────────────────────────────────────────────
#  Jinja templates (FIXED - simplified and made more robust)
# ──────────────────────────────────────────────────────────────────────────────
_PROVIDER_TMPL = Template("""
terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = "{{ region }}"
}
""")

_VARIABLES_TMPL = """
# Auto-generated variables file
variable "app_name" {
  default = "auto-deployed-app"
}
"""

# ---------- EC2 (VM) - FIXED --------------------------------------------------
_EC2_TMPL = Template("""
# Security group for web app
resource "aws_security_group" "app_sg" {
  name_prefix = "auto-app-"

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_instance" "app" {
  ami                     = "ami-0e001c9271cf7f3b9"  # Amazon Linux 2023 x86-64
  instance_type           = "{{ instance_type }}"
  vpc_security_group_ids  = [aws_security_group.app_sg.id]
  associate_public_ip_address = true

  user_data = <<-EOF
                #!/bin/bash
                yum update -y
                yum install -y git python3 python3-pip

                # Create app user
                useradd -m appuser

                # Simple startup script placeholder
                echo "#!/bin/bash" > /home/appuser/start.sh
                echo "cd /home/appuser" >> /home/appuser/start.sh
                echo "# TODO: Add actual start command: {{ facts.get('start_command', 'echo No start command') }}" >> /home/appuser/start.sh
                chmod +x /home/appuser/start.sh
                chown appuser:appuser /home/appuser/start.sh
  EOF

  tags = {
    Name = var.app_name
  }
}

output "public_ip" {
  value = aws_instance.app.public_ip
}

output "ssh_command" {
  value = "ssh -i your-key.pem ec2-user@${aws_instance.app.public_ip}"
}
""")

# ---------- Lambda - SIMPLIFIED -----------------------------------------------
_LAMBDA_TMPL = Template("""
# Simplified Lambda - just creates the function without API Gateway
resource "aws_iam_role" "lambda" {
  name = "auto-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_basic" {
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
  role       = aws_iam_role.lambda.name
}

# Create a simple hello world lambda zip
resource "local_file" "lambda_code" {
  content  = <<EOF
def lambda_handler(event, context):
    return {
        'statusCode': 200,
        'body': 'Hello from auto-deployed Lambda!'
    }
EOF
  filename = "${path.module}/lambda_function.py"
}

data "archive_file" "lambda_zip" {
  type        = "zip"
  source_file = local_file.lambda_code.filename
  output_path = "${path.module}/lambda.zip"
  depends_on  = [local_file.lambda_code]
}

resource "aws_lambda_function" "func" {
  filename         = data.archive_file.lambda_zip.output_path
  function_name    = "auto-deployed-function"
  handler          = "lambda_function.lambda_handler"
  runtime          = "python3.11"
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256
  role            = aws_iam_role.lambda.arn
}

output "lambda_arn" {
  value = aws_lambda_function.func.arn
}
""")

# ---------- ECS (Fargate) - SIMPLIFIED ----------------------------------------
_ECS_TMPL = Template("""
# Simplified ECS - just cluster for now
resource "aws_ecs_cluster" "main" {
  name = "auto-cluster"
}

output "cluster_name" {
  value = aws_ecs_cluster.main.name
}
""")

# ---------- Optional RDS - SIMPLIFIED -----------------------------------------
_RDS_TMPL = Template("""
resource "aws_db_subnet_group" "default" {
  name       = "auto-db-subnet"
  subnet_ids = [aws_subnet.main.id, aws_subnet.alt.id]
}

resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true
}

resource "aws_subnet" "main" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.0.1.0/24"
  availability_zone = "us-east-1a"
}

resource "aws_subnet" "alt" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.0.2.0/24"
  availability_zone = "us-east-1b"
}

resource "aws_db_instance" "db" {
  allocated_storage      = 20
  storage_type          = "gp2"
  engine                = "postgres"
  engine_version        = "14.12"
  instance_class        = "db.t3.micro"
  db_name               = "appdb"
  username              = "appuser"
  password              = "ChangeMe123!"
  db_subnet_group_name  = aws_db_subnet_group.default.name
  skip_final_snapshot   = true

  tags = {
    Name = "auto-deployed-db"
  }
}

output "db_endpoint" {
  value = aws_db_instance.db.address
}
""")

# ──────────────────────────────────────────────────────────────────────────────
# Quick smoke-test   (requires Terraform + AWS creds)
# ──────────────────────────────────────────────────────────────────────────────
# if __name__ == "__main__":
#     # dummy plan from DecisionEngine
#     PLAN = {
#         "provider": "AWS",
#         "region": "us-east-1",
#         "strategy": "EC2",
#         "runtime": "python3.11",
#         "size": "t3.micro",
#         "db": None,  # set to "RDS_PostgreSQL" to test DB
#         "cache": None,
#         "queue": None,
#         "needs_gpu": False,
#     }
#
#     # minimal subset of repo_facts
#     FACTS = {
#         "start_command": "python app.py",
#         "framework": "Flask"
#     }
#
#     try:
#         prov = TerraformProvisioner(PLAN, FACTS)
#         outs = prov.apply()
#         print("\n✅ SUCCESS! Terraform outputs:")
#         print(json.dumps(outs, indent=4))
#     except subprocess.CalledProcessError as e:
#         print(f"❌ Terraform command failed: {e}")
#     except FileNotFoundError:
#         print("❌ Terraform not found in PATH. Please install Terraform first.")
#     except Exception as e:
#         print(f"❌ Error: {e}")
