#!/usr/bin/env python3
"""PostToolUse hook: format a file after Claude edits it.

Best effort. Missing formatters are skipped, and failures never block the edit.
"""
import json
import shutil
import subprocess
import sys

try:
    payload = json.load(sys.stdin)
except json.JSONDecodeError:
    sys.exit(0)

path = (payload.get("tool_input") or {}).get("file_path", "")


def run(*cmd: str) -> None:
    if shutil.which(cmd[0]):
        subprocess.run(cmd, check=False, capture_output=True, timeout=30)


if path.endswith(".py"):
    run("ruff", "check", "--fix", "--quiet", path)
    run("ruff", "format", "--quiet", path)
elif path.endswith((".tf", ".tfvars")):
    run("terraform", "fmt", path)
sys.exit(0)
