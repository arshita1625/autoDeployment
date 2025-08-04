# AutoDeployment Chat System

## Overview

AutoDeployment Chat System turns a plain-English request like  
“Deploy this Flask app on AWS with Postgres”  
into a fully running cloud service—no manual DevOps steps required.

- Parses the request to pull out cloud provider, framework, DB, etc.
- Inspects the code-base to detect Dockerfiles, entry points, handlers, dependencies.
- Chooses the optimal infrastructure (serverless, container, or VM).
- Provisions resources with Terraform (repeatable, auditable IaC).
- Patches the app (replaces localhost, injects env-vars).
- Transfers code, installs dependencies, starts the service, opens the firewall port.
- Streams live logs and returns the public URL.

## Architecture

| Component           | Role                                                                                   |
|---------------------|----------------------------------------------------------------------------------------|
| **NLP Extractor**      | Uses HuggingFace QA + regex to pull provider, framework, DB from text.                  |
| **Repository Analyzer**| Scans repo for Dockerfile, handler, requirements.txt, ports, etc.                      |
| **Decision Engine**    | Rule-maps findings to serverless / container / VM.                                    |
| **Terraform Provisioner** | Generates & applies modular `.tf` files for AWS.                                    |
| **Code Modifier**      | Rewrites hard-coded hosts, exports env-vars.                                           |
| **Deployment Helper**  | Uploads code, installs deps, starts service, opens security group port.                |
| **FastAPI Orchestrator** | REST API (`/deploy`, `/logs`, live stream) that glues everything together.           |

## Quick Start

### Prerequisites

- Python 3.8+
- Terraform installed & AWS credentials configured
- An EC2 key-pair (`.pem`) on your workstation

### Run the orchestrator API

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000

### Trigger a deployment

To start a deployment, send a POST request to the orchestrator API with your deployment message and repository URL:

```bash
curl -F "message=Deploy this Flask app on AWS" \
     -F "repo_url=https://github.com/Arvo-AI/hello_world" \
     http://localhost:8000/deploy
### Streaming Live Logs

You can watch live deployment logs by accessing the streaming logs endpoint with the `job_id` returned from the deployment request:

```bash
curl http://localhost:8000/stream_logs/{job_id}
