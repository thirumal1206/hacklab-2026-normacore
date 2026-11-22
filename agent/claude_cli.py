"""Ask Claude through the `claude` CLI (the logged-in Claude subscription, no API key).

Trimmed start for speed (about 6 s for text, 8-14 s with an image): no MCP servers, no saved session, only the tools
named, no user or project settings (CLAUDE.md, hooks), and its own short system prompt instead of the coding one.
"""
import json
import os
import re
import subprocess

import real

MODEL_PLAN = os.environ.get("PLAN_MODEL", "sonnet")  # free sentences -> pick plan
MODEL_LABEL = os.environ.get("LABEL_MODEL", "haiku")  # object labels from the top camera
SYSTEM = "You are a component of a robot arm system. Follow the instructions exactly and reply with JSON only."


def ask(prompt, model, tools="", timeout=60):
    real.RUNS.mkdir(parents=True, exist_ok=True)
    mcp = real.RUNS / "no_mcp.json"
    if not mcp.exists():
        mcp.write_text('{"mcpServers": {}}')
    cmd = ["claude", "-p", prompt, "--model", model, "--tools", tools, "--strict-mcp-config", "--mcp-config", str(mcp),
           "--no-session-persistence", "--setting-sources", "", "--system-prompt", SYSTEM]
    if tools:
        cmd += ["--allowedTools", tools]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=real.RUNS)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(f"claude CLI: {e}") from None
    if out.returncode:
        raise RuntimeError(f"claude CLI exit {out.returncode}: {(out.stderr or out.stdout).strip()[:300]}")
    return out.stdout


def ask_json(prompt, model, tools="", timeout=60):
    text = ask(prompt, model, tools, timeout)
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError(f"no JSON in Claude's answer: {text.strip()[:200]}")
    return json.loads(m.group(0))
