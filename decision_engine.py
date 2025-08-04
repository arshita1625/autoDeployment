# ──────────────────────────────────────────────────────────────────────────────
# decision_engine.py  –  “Step 3” Deployment-Decision Module
#
# Input
# ─────
#   • nlp_info  : result of your NLP extractor, e.g.
#       { "provider": "AWS",
#         "framework": "Flask",
#         "preference": "serverless" }      # preference may be None
#
#   • repo_facts: dictionary returned by  RepoAnalyzer.analyze()
#
# Output
# ──────
#   A **deployment plan** – single JSON-serialisable dict consumed
#   later by the Terraform layer.
#
# External deps:  none (pure std-lib)
# ──────────────────────────────────────────────────────────────────────────────
from __future__ import annotations
from pathlib import Path
from typing import Dict, Optional, List

class DecisionEngine:
    """Rule-based deployment planner (AWS-centric) with frontend detection."""

    # Default region per cloud (expandable)
    DEFAULT_REGION = {
        "AWS": "us-east-1",
        "Azure": "eastus",
        "GCP": "us-central1",
    }

    # Tiny memory-footprint heuristics for serverless
    _LIGHT_LIBS = {"flask", "express", "fastapi"}
    _HEAVY_LIBS = {"tensorflow", "torch", "pytorch", "opencv", "pandas"}

    def __init__(
        self,
        nlp_info: Dict,
        repo_facts: Dict,
    ) -> None:
        self.provider = nlp_info.get("provider", "AWS")  # default AWS
        self.preference = (nlp_info.get("preference") or "").lower()
        self.facts = repo_facts
        self.notes: List[str] = []  # free-form explanations

    # ──────────────────────────────────────────────────────────────
    # public API
    # ──────────────────────────────────────────────────────────────
    def make_plan(self) -> Dict:
        strategy = self._choose_strategy()
        plan = {
            "provider": self.provider,
            "region": self.DEFAULT_REGION.get(self.provider, "us-east-1"),
            "strategy": strategy,  # Lambda | ECS | EC2 | EC2-GPU | Elastic Beanstalk
            "runtime": self._runtime(),  # python3.11, nodejs18.x …
            "size": self._instance_size(strategy),
            "db": self._db_service(),  # e.g. RDS_PostgreSQL or None
            "cache": self._cache_service(),  # e.g. ElastiCache_Redis or None
            "queue": self._queue_service(),  # e.g. SQS or None
            "needs_gpu": self.facts["services_manifest"]["needs_gpu"],
            "notes": self.notes,  # human-readable notes
        }
        return plan

    # ──────────────────────────────────────────────────────────────
    # internal helpers
    # ──────────────────────────────────────────────────────────────
    def _choose_strategy(self) -> str:
        """
        Decide among: Lambda (serverless), ECS-Fargate (container),
        EC2 (VM), EC2-GPU, Elastic Beanstalk.
        """
        manifest = self.facts["services_manifest"]
        has_docker = self.facts["dependencies"].get("docker", False)
        frontend_static = self._has_frontend_static_files()

        # 1. explicit user wish overrides all
        if "kubernetes" in self.preference:
            self.notes.append("User explicitly requested Kubernetes → choose EKS")
            return "EKS"
        if "serverless" in self.preference:
            self.notes.append("User explicitly requested serverless")
            return "Lambda"
        if "container" in self.preference:
            self.notes.append("User explicitly requested containers")
            return "ECS"

        # 2. GPU requirement forces EC2-GPU
        if manifest["needs_gpu"]:
            self.notes.append("GPU libraries detected → choose EC2-GPU")
            return "EC2-GPU"

        # 3. Stateful services (DB / queue / cache) → easier on EC2 or Elastic Beanstalk
        if manifest["db"] or manifest["queue"]:
            self.notes.append("Stateful service detected → choose EC2 or Elastic Beanstalk")
            return "EC2"

        # 4. Frontend static files found with backend → recommend EC2 or Elastic Beanstalk
        if frontend_static:
            self.notes.append("Frontend static files detected → choose EC2 or Elastic Beanstalk")
            return "EC2"

        # 5. Dockerfile present → run container on ECS Fargate
        if has_docker:
            self.notes.append("Dockerfile present → choose ECS-Fargate")
            return "ECS"

        # 6. Lightweight libs + no frontend static + no Dockerfile → Lambda
        py_deps = set(self.facts["dependencies"].get("python", []))
        heavy = py_deps & self._HEAVY_LIBS
        if not heavy:
            self.notes.append("Lightweight runtime and no frontend static → choose Lambda")
            return "Lambda"

        # fallback
        self.notes.append("Default fallback → choose EC2")
        return "EC2"

    def _has_frontend_static_files(self) -> bool:
        repo_path = self.facts.get("repository_path")
        if not repo_path:
            print("[DEBUG] No repository path provided")
            return False

        static_dir_names = {"static", "public", "frontend", "assets"}
        repo = Path(repo_path)
        print(f"[DEBUG] Scanning for static frontend files in: {repo}")

        for path in repo.rglob("*"):
            if path.is_dir() and path.name.lower() in static_dir_names:
                print(f"[DEBUG] Frontend static directory found: {path}")
                return True
            if path.is_file() and path.name.lower() == "index.html":
                print(f"[DEBUG] Frontend index.html found: {path}")
                return True

        print("[DEBUG] No frontend static files detected")
        return False

    def _runtime(self) -> str:
        fw = self.facts.get("framework", "")
        if fw in {"Flask", "Django", "FastAPI"}:
            return "python3.11"
        if fw in {"Express", "Node.js"}:
            return "nodejs18.x"
        return "custom"

    def _instance_size(self, strategy: str) -> Optional[str]:
        if strategy == "EC2-GPU":
            return "g4dn.xlarge"
        if strategy == "EC2":
            return "t3.small"
        if strategy == "ECS":
            return "Fargate-0.5vCPU-1GB"
        # Lambda sizing is set at deployment time; not fixed here
        return None

    def _db_service(self) -> Optional[str]:
        db = self.facts["services_manifest"]["db"]
        if not db:
            return None
        if self.provider == "AWS":
            return f"RDS_{db}"
        if self.provider == "GCP":
            return f"CloudSQL_{db}"
        if self.provider == "Azure":
            return f"AzureDB_{db}"
        return db

    def _cache_service(self) -> Optional[str]:
        cache = self.facts["services_manifest"]["cache"]
        if cache and self.provider == "AWS":
            return "ElastiCache_Redis"
        return cache

    def _queue_service(self) -> Optional[str]:
        queue = self.facts["services_manifest"]["queue"]
        if queue and self.provider == "AWS":
            return "SQS"
        return queue


# Demo / smoke-test
if __name__ == "__main__":
    # Simulated NLP output
    nlp_info = {
        "provider": "AWS",
        "preference": None,  # Try 'serverless', 'container', 'kubernetes'
    }

    # Simulated repository facts with frontend static files present
    repo_facts = {
        "framework": "Flask",
        "dependencies": {
            "python": ["flask", "requests"],
            "docker": False,
        },
        "services_manifest": {
            "db": None,
            "cache": None,
            "queue": None,
            "needs_gpu": False,
        },
        "modification_points": [],
        "repository_path": "path/to/your/repo_with_static_files",
    }

    engine = DecisionEngine(nlp_info, repo_facts)
    plan = engine.make_plan()

    import json
    print(json.dumps(plan, indent=4))


# ──────────────────────────────────────────────────────────────────────────────
# Demo / smoke-test
# ──────────────────────────────────────────────────────────────────────────────
# if __name__ == "__main__":
#     # Fake NLP output (what your NLP extractor provides)
#     nlp_info = {
#         "provider": "AWS",
#         "preference": None   # try "serverless", "container", "kubernetes"
#     }
#
#     # Fake RepoAnalyzer facts (simplified)
#     repo_facts = {
#         "framework": "Flask",
#         "dependencies": {
#             "python": ["flask", "psycopg2"],
#             "docker": True
#         },
#         "services_manifest": {
#             "db": "PostgreSQL",
#             "cache": None,
#             "queue": None,
#             "needs_gpu": False
#         },
#         "modification_map": []
#     }
#
#     engine = DecisionEngine(nlp_info, repo_facts)
#     plan   = engine.make_plan()
#
#     import json, textwrap
#     print(textwrap.dedent("""
#         ───────────────────────────────────────────────────────────
#         Deployment plan (step 3 output)
#         ───────────────────────────────────────────────────────────
#     """))
#     print(json.dumps(plan, indent=4))
