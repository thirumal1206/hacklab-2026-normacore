#!/usr/bin/env bash
# Friday, the candy sorter, locked down: only the candy tools, our prompt, no files or shell, capped spend.
#   operator/candy/run.sh                 dry run: plans every move, the arm does not move
#   CANDY_GO=1 operator/candy/run.sh      real arm (hand on the 12 V plug). Speak on the dashboard: http://127.0.0.1:8770
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
mkdir -p "$ROOT/runs/agent/candy-session" && cd "$ROOT/runs/agent/candy-session"
MCP=$(printf '{"mcpServers":{"candy":{"command":"%s","args":["%s"],"env":{"CANDY_GO":"%s","CANDY_CAMERA":"%s","ARM_TTS":"%s"}}}}' \
  "$ROOT/.venv/bin/python" "$ROOT/agent/candy_mcp.py" "${CANDY_GO:-0}" "${CANDY_CAMERA:-}" "${ARM_TTS:-say}")
exec claude -p "${1:-Run as Friday: greet once, then listen for instructions.}" --model "${AGENT_MODEL:-haiku}" \
  --system-prompt "$(cat "$HERE/AGENTS.md")" --tools "" --mcp-config "$MCP" --strict-mcp-config \
  --allowedTools mcp__candy__spots,mcp__candy__pick,mcp__candy__say,mcp__candy__listen,mcp__candy__home \
  --setting-sources "" --disable-slash-commands --no-session-persistence --max-budget-usd "${AGENT_BUDGET:-2.00}" \
  --output-format "${AGENT_OUTPUT:-text}" < /dev/null
