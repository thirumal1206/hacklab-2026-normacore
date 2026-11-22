"""Ask Claude through the `claude` CLI (the logged-in Claude subscription, no API key).

Trimmed start for speed (about 6 s for text, 8-14 s with an image): no MCP servers, no saved session, only the tools
named, no user or project settings (CLAUDE.md, hooks), and its own short system prompt instead of the coding one.
"""
import base64
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
    try:  # stdin closed: otherwise the CLI waits 3 s for piped input
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=real.RUNS, stdin=subprocess.DEVNULL)
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


def ask_image_json(prompt, image_path, model, timeout=60):
    """One image plus text, sent inline (stream-json): no Read tool round trip, about 4 s faster than ask_json."""
    real.RUNS.mkdir(parents=True, exist_ok=True)
    mcp = real.RUNS / "no_mcp.json"
    if not mcp.exists():
        mcp.write_text('{"mcpServers": {}}')
    data = base64.b64encode(open(image_path, "rb").read()).decode()
    msg = {"type": "user", "message": {"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}},
        {"type": "text", "text": prompt}]}}
    cmd = ["claude", "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose", "--model", model,
           "--tools", "", "--strict-mcp-config", "--mcp-config", str(mcp), "--no-session-persistence",
           "--setting-sources", "", "--system-prompt", SYSTEM]
    try:
        out = subprocess.run(cmd, input=json.dumps(msg) + "\n", capture_output=True, text=True, timeout=timeout, cwd=real.RUNS)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(f"claude CLI: {e}") from None
    result = next((json.loads(line) for line in out.stdout.splitlines() if '"type":"result"' in line), None)
    if out.returncode or result is None or result.get("is_error"):
        raise RuntimeError(f"claude CLI exit {out.returncode}: {(out.stderr or out.stdout).strip()[-300:]}")
    m = re.search(r"\{.*\}", result.get("result", ""), re.S)
    if not m:
        raise ValueError(f"no JSON in Claude's answer: {result.get('result', '').strip()[:200]}")
    return json.loads(m.group(0))
