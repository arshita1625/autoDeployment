# ──────────────────────────────────────────────────────────────────────────────
# repo_analyzer.py  –  “Step 2” Repository-Analysis Module
#
# Accepts:
#   • Git repository URL  (HTTPS or SSH)               → clones if possible
#   • Remote ZIP URL
#   • Local ZIP file path
#
# Produces a single “facts” dictionary:
#   {
#     framework, entry_point, start_command,
#     dependencies, services_manifest,
#     modification_map, repository_path
#   }
#
# External deps:  pip install gitpython packaging requests
# ──────────────────────────────────────────────────────────────────────────────

from __future__ import annotations
import json
import os
import re
import shutil
import tempfile
import urllib.parse
import zipfile
from pathlib import Path
from typing import Dict, List, Optional

import requests                    # network download
from packaging import version      # dependency parsing

# GitPython is optional – we fall back to ZIP if it's missing
try:
    import git                     # pip install gitpython
    _HAS_GITPYTHON = True
except ImportError:
    _HAS_GITPYTHON = False


class RepoAnalyzer:
    """Analyze a Git repo OR ZIP archive and return deployment-relevant facts."""

    PY_ENTRY_CANDIDATES   = ["app.py", "main.py", "run.py", "wsgi.py", "manage.py"]
    NODE_ENTRY_CANDIDATES = ["server.js", "index.js", "app.js"]

    DB_KEYWORDS    = {"psycopg2": "PostgreSQL", "pg8000": "PostgreSQL",
                      "mysql": "MySQL", "pymysql": "MySQL",
                      "mongo": "MongoDB", "mongoose": "MongoDB"}
    CACHE_KEYWORDS = {"redis": "Redis"}
    QUEUE_KEYWORDS = {"celery": "RabbitMQ", "boto3": "SQS"}
    GPU_KEYWORDS   = {"tensorflow", "torch", "pytorch", "jax"}

    LOCAL_PAT = re.compile(r"(localhost|127\.0\.0\.1)", re.I)
    PORT_PAT  = re.compile(r"(?<!\w)(port\s*=?\s*\d{2,5}|:\d{2,5})(?!\w)", re.I)

    # ---------------------------------------------------------------------- #
    # constructor                                                            #
    # ---------------------------------------------------------------------- #
    def __init__(
        self,
        source: str,
        *,
        specified_framework: str | None = None,
        scratch_dir: str | Path | None = None,
    ) -> None:
        """
        Parameters
        ----------
        source  : Git URL, remote ZIP URL, or local ZIP path.
        specified_framework : e.g. “Flask” (optional – validator only)
        scratch_dir : working directory; defaults to system temp folder.
        """
        self.source  = source
        self.spec_fw = specified_framework.lower() if specified_framework else None
        self.workdir = Path(scratch_dir or tempfile.mkdtemp()) / "repo"
        self.workdir.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------------- #
    # public API                                                             #
    # ---------------------------------------------------------------------- #
    def analyze(self) -> Dict:
        """Fetch repository, analyse contents and return facts dict."""
        self._fetch()

        framework = self._detect_framework()
        entry_pt  = self._find_entry_point(framework)
        deps      = self._collect_dependencies()
        services  = self._infer_services(deps)
        changes   = self._scan_mod_points()
        start_cmd = self._make_start_cmd(framework, entry_pt)

        return {
            "framework"        : framework,
            "entry_point"      : entry_pt,
            "start_command"    : start_cmd,
            "dependencies"     : deps,
            "services_manifest": services,
            "modification_map" : changes,
            "repository_path"  : str(self.workdir),
        }

    # ---------------------------------------------------------------------- #
    # fetching logic – ZIP or Git                                            #
    # ---------------------------------------------------------------------- #
    def _fetch(self) -> None:
        """Populate self.workdir with project files from Git or ZIP."""
        if self.workdir.exists():
            shutil.rmtree(self.workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)

        src = self.source.lower()

        # 1. local ZIP
        if src.endswith(".zip") and Path(self.source).exists():
            print(f"📦  Extracting local ZIP  {self.source}")
            self._extract_zip(Path(self.source))
            return

        # 2. remote ZIP URL
        if src.endswith(".zip") and src.startswith(("http://", "https://")):
            print(f"📦  Downloading ZIP  {self.source}")
            self._download_and_extract_zip(self.source)
            return

        # 3. Git repository URL
        if self.source.startswith(("http://", "https://", "git@")):
            if _HAS_GITPYTHON:
                try:
                    print(f"🔗  Cloning Git repo  {self.source}")
                    git.Repo.clone_from(self.source, self.workdir)
                    return
                except Exception as exc:
                    print(f"⚠️   Git clone failed: {exc}")
            else:
                print("⚠️   GitPython unavailable.")

            # Git failed → try GitHub ZIP fallback
            self._fallback_to_github_zip()
            return

        raise ValueError(f"Unsupported source format: {self.source}")

    # ---------------------------------------------------------------------- #
    # helpers: ZIP extraction / download                                     #
    # ---------------------------------------------------------------------- #
    def _extract_zip(self, zip_path: Path) -> None:
        with zipfile.ZipFile(zip_path) as zf:
            temp_extract = self.workdir.parent / "zip_tmp"
            if temp_extract.exists():
                shutil.rmtree(temp_extract)
            zf.extractall(temp_extract)

        # handle single top-folder case
        children = list(temp_extract.iterdir())
        if len(children) == 1 and children[0].is_dir():
            shutil.move(str(children[0]), self.workdir)
            shutil.rmtree(temp_extract)
        else:
            shutil.move(str(temp_extract), self.workdir)

    def _download_and_extract_zip(self, url: str) -> None:
        tmp_zip = self.workdir.parent / "download.zip"
        with requests.get(url, stream=True) as r:
            r.raise_for_status()
            with open(tmp_zip, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        self._extract_zip(tmp_zip)
        tmp_zip.unlink()

    def _fallback_to_github_zip(self) -> None:
        """For GitHub links: download repo as ZIP (main / master)."""
        parsed = urllib.parse.urlparse(self.source)
        if "github.com" not in parsed.netloc:
            raise RuntimeError("ZIP fallback only implemented for GitHub URLs.")

        owner, repo = parsed.path.strip("/").split("/")[:2]
        for branch in ("main", "master"):
            zip_url = f"https://github.com/{owner}/{repo}/archive/refs/heads/{branch}.zip"
            try:
                print(f"📦  Trying GitHub ZIP  {zip_url}")
                self._download_and_extract_zip(zip_url)
                return
            except requests.HTTPError as e:
                if e.response.status_code == 404:
                    continue
                raise
        raise RuntimeError("Could not download repo ZIP from GitHub (main/master)")

    # ---------------------------------------------------------------------- #
    # framework detection                                                    #
    # ---------------------------------------------------------------------- #
    def _detect_framework(self) -> str | None:
        if self.spec_fw:
            return self.spec_fw.capitalize()

        if list(self.workdir.rglob("manage.py")):
            return "Django"

        req_files = list(self.workdir.rglob("requirements.txt"))
        if req_files:
            txt = "\n".join(f.read_text() for f in req_files)
            if re.search(r"\bflask\b", txt, re.I):
                return "Flask"
            if re.search(r"\bfastapi\b", txt, re.I):
                return "FastAPI"

        pkg_files = list(self.workdir.rglob("package.json"))
        if pkg_files:
            pkg = json.loads(Path(pkg_files[0]).read_text())
            deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
            if "express" in deps:
                return "Express"
            return "Node.js"

        return None

    # ---------------------------------------------------------------------- #
    # entry-point discovery                                                  #
    # ---------------------------------------------------------------------- #
    def _find_entry_point(self, fw: str | None) -> str | None:
        if fw in {"Flask", "FastAPI", "Django"}:
            for cand in self.PY_ENTRY_CANDIDATES:
                hit = list(self.workdir.rglob(cand))
                if hit:
                    return str(hit[0].relative_to(self.workdir))

        if fw in {"Express", "Node.js"}:
            pkg_files = list(self.workdir.rglob("package.json"))
            if pkg_files:
                pkg = json.loads(Path(pkg_files[0]).read_text())
                if "main" in pkg:
                    return pkg["main"]
            for cand in self.NODE_ENTRY_CANDIDATES:
                hit = list(self.workdir.rglob(cand))
                if hit:
                    return str(hit[0].relative_to(self.workdir))
        return None

    # ---------------------------------------------------------------------- #
    # dependency collection                                                  #
    # ---------------------------------------------------------------------- #
    def _collect_dependencies(self) -> Dict:
        deps: Dict = {}

        # Python
        req_files = list(self.workdir.rglob("requirements.txt"))
        if req_files:
            deps["python"] = [
                l.split("==")[0].strip()
                for f in req_files
                for l in f.read_text().splitlines()
                if l and not l.startswith("#")
            ]

        # Node
        pkg_files = list(self.workdir.rglob("package.json"))
        if pkg_files:
            pkg = json.loads(Path(pkg_files[0]).read_text())
            deps["node"] = {
                "dependencies"    : pkg.get("dependencies", {}),
                "devDependencies" : pkg.get("devDependencies", {}),
            }

        # Docker
        if list(self.workdir.rglob("Dockerfile")):
            deps["docker"] = True

        return deps

    # ---------------------------------------------------------------------- #
    # service inference                                                      #
    # ---------------------------------------------------------------------- #
    def _infer_services(self, deps: Dict) -> Dict:
        flat: List[str] = []

        for lang, val in deps.items():
            if lang == "python":
                flat.extend([d.lower() for d in val])
            if lang == "node":
                flat.extend([k.lower() for k in val["dependencies"].keys()])

        def match(mapping):
            for key, svc in mapping.items():
                if any(key in dep for dep in flat):
                    return svc
            return None

        return {
            "db"        : match(self.DB_KEYWORDS),
            "cache"     : match(self.CACHE_KEYWORDS),
            "queue"     : match(self.QUEUE_KEYWORDS),
            "needs_gpu" : any(g in dep for dep in flat for g in self.GPU_KEYWORDS),
        }

    # ---------------------------------------------------------------------- #
    # modification-point scan                                                #
    # ---------------------------------------------------------------------- #
    def _scan_mod_points(self) -> List[Dict]:
        results: List[Dict] = []
        code_files = list(self.workdir.rglob("*.py")) + list(self.workdir.rglob("*.js"))

        for path in code_files:
            for i, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
                if self.LOCAL_PAT.search(line):
                    results.append({"file": str(path.relative_to(self.workdir)),
                                    "line": i, "kind": "localhost_ref",
                                    "snippet": line.strip()})
                elif self.PORT_PAT.search(line):
                    results.append({"file": str(path.relative_to(self.workdir)),
                                    "line": i, "kind": "port_config",
                                    "snippet": line.strip()})
        return results

    # ---------------------------------------------------------------------- #
    # start-command builder                                                  #
    # ---------------------------------------------------------------------- #
    def _make_start_cmd(self, fw: str | None, entry: str | None) -> str | None:
        if not fw:
            return None
        if fw == "Flask":
            if entry:
                mod = Path(entry).stem
                return f"gunicorn -w 4 -b 0.0.0.0:$PORT {mod}:app"
            return "flask run --host=0.0.0.0 --port=$PORT"
        if fw == "FastAPI":
            mod = Path(entry).stem if entry else "main"
            return f"uvicorn {mod}:app --host 0.0.0.0 --port $PORT"
        if fw == "Django":
            return "gunicorn project.wsgi --bind 0.0.0.0:$PORT"
        if fw in {"Express", "Node.js"}:
            pkg_files = list(self.workdir.rglob("package.json"))
            if pkg_files:
                scripts = json.loads(Path(pkg_files[0]).read_text()).get("scripts", {})
                if "start" in scripts:
                    return "npm start"
            return f"node {entry or 'server.js'}"
        return None


# --------------------------------------------------------------------------- #
# Smoke-test with different source types                                      #
# --------------------------------------------------------------------------- #
# if __name__ == "__main__":
#     TESTS = [
#         # Git clone (will attempt Git, then ZIP fallback if Git absent)
#         ("https://github.com/Arvo-AI/hello_world", "Flask"),
#         # Remote ZIP URL
#         ("https://github.com/Arvo-AI/hello_world/archive/refs/heads/main.zip", "Flask"),
#         # You can add a local ZIP path here to test
#         (r"C:\Users\prade\PyCharmMiscProject\hello_world-main.zip", None),
#     ]
#
#     for src, fw in TESTS:
#         print("\n" + "=" * 72)
#         print(f"Analyzing: {src}")
#         print("=" * 72)
#         try:
#             analyser = RepoAnalyzer(src, specified_framework=fw)
#             facts = analyser.analyze()
#             for k, v in facts.items():
#                 print(f"{k:17}: {v}")
#         except Exception as exc:
#             print(f"Error: {exc}")
