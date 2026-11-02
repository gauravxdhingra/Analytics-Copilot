#!/usr/bin/env python3
"""PreToolUse hook: refuse edits to secrets and git internals.

Exit code 2 blocks the tool call and feeds stderr back to the model.
"""
import json
import re
import sys

BLOCKED = [
    (r"(^|/)\.env(\..*)?$", "environment files hold secrets"),
    (r"\.(pem|key|p12|pfx)$", "key material"),
    (r"(^|/)\.git/", "git internals"),
    (r"(^|/)secrets/", "secrets directory"),
    (r"(^|/)Generative AI Treasury Management\.md$", "private source notes with employer-specific names"),
]

try:
    payload = json.load(sys.stdin)
except json.JSONDecodeError:
    sys.exit(0)

path = (payload.get("tool_input") or {}).get("file_path", "")
for pattern, reason in BLOCKED:
    if re.search(pattern, path):
        print(f"Blocked: {path} ({reason}). Ask the user to edit it themselves.", file=sys.stderr)
        sys.exit(2)
sys.exit(0)
