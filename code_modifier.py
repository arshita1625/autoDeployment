# code_modifier.py

from pathlib import Path
from typing import List, Dict


def modify_code_for_deployment(repo_path: str, mod_map: List[Dict], service_endpoints: Dict[str, str]) -> None:
    """
    Modifies source code and configuration files to replace localhost references, ports,
    and inject environment variable placeholders for deployment.

    Parameters:
    -----------
    repo_path : str
        The local path to the checked-out repository.
    mod_map : List[Dict]
        List of modification points in the source code files. Each dict should have "file", "line", "kind", and "snippet".
    service_endpoints : Dict[str, str]
        Mapping of service keys (e.g., "DB_HOST", "REDIS_HOST", "APP_HOST", "PORT") to their actual endpoint values.
    """
    repo = Path(repo_path)
    for mod in mod_map:
        filepath = repo / mod["file"]
        if not filepath.exists():
            print(f"⚠️ Skipping missing file: {filepath}")
            continue

        lines = filepath.read_text(encoding="utf-8").splitlines()
        line_idx = mod["line"] - 1
        if line_idx < 0 or line_idx >= len(lines):
            print(f"⚠️ Invalid line number {mod['line']} in {filepath}")
            continue

        original_line = lines[line_idx]
        new_line = original_line

        if mod["kind"] == "localhost_ref":
            # Replace localhost or 127.0.0.1 with environment variable reference depending on context
            replacement = None
            line_lower = original_line.lower()
            if "database" in line_lower or "db_" in line_lower or "postgres" in line_lower:
                replacement = service_endpoints.get("DB_HOST", "DB_HOST")
            elif "redis" in line_lower:
                replacement = service_endpoints.get("REDIS_HOST", "REDIS_HOST")
            else:
                replacement = service_endpoints.get("APP_HOST", "APP_HOST")

            if filepath.suffix in {".py"}:
                # Python environment variable
                new_line = new_line.replace("localhost", f"os.environ.get('{replacement}', 'localhost')")
                new_line = new_line.replace("127.0.0.1", f"os.environ.get('{replacement}', '127.0.0.1')")
            elif filepath.suffix in {".js", ".ts"}:
                # Node.js environment variable
                new_line = new_line.replace("localhost", f"process.env.{replacement} || 'localhost'")
                new_line = new_line.replace("127.0.0.1", f"process.env.{replacement} || '127.0.0.1'")
            else:
                # Plain replacement for unknown file types
                new_line = new_line.replace("localhost", replacement)
                new_line = new_line.replace("127.0.0.1", replacement)

        elif mod["kind"] == "port_config":
            import re
            # Replace port numbers with environment variable references
            port_pattern = re.compile(r"(\bport\s*=\s*)(\d+)|(:)(\d+)", re.I)

            def port_replacer(match):
                prefix = match.group(1) or match.group(3) or ""
                return f"{prefix}${{PORT}}"

            new_line = port_pattern.sub(port_replacer, new_line)

        if new_line != original_line:
            print(f"✔️ Modified {mod['file']} line {mod['line']}:\n  - {original_line.strip()}\n  + {new_line.strip()}")
            lines[line_idx] = new_line
        else:
            print(f"ℹ️ No change needed in {mod['file']} line {mod['line']}")

        filepath.write_text("\n".join(lines), encoding="utf-8")
