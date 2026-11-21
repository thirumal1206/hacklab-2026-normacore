#!/usr/bin/env bash
# Sorting agent, locked down: only the sorter MCP tools, no built-in tools, our prompt only, capped turns and spend.
# Usage: operator/sorter/run.sh "Sort every pack on the table."    (AGENT_BACKEND=real for the robot, AGENT_DRY_RUN=1 to plan only)
# Stop switch: touch runs/agent/STOP (every tool refuses until it is deleted), or the STOP button on the dashboard.
# Dashboard: .venv/bin/python agent/dashboard.py, then http://127.0.0.1:8770
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
SANDBOX=$ROOT/runs/agent/session   # empty working dir: no project files to read
mkdir -p "$SANDBOX"
MCP=$(printf '{"mcpServers":{"sorter":{"command":"%s","args":["%s"],"env":{"AGENT_BACKEND":"%s","AGENT_DRY_RUN":"%s"}}}}' \
  "$ROOT/.venv/bin/python" "$ROOT/agent/mcp_server.py" "${AGENT_BACKEND:-fake}" "${AGENT_DRY_RUN:-0}")
TOOLS=mcp__sorter__scan,mcp__sorter__list_bins,mcp__sorter__inspect,mcp__sorter__sort,mcp__sorter__flag,mcp__sorter__summary
cd "$SANDBOX"
exec claude -p "${1:-Sort every pack on the table.}" \
  --model "${AGENT_MODEL:-haiku}" \
  --system-prompt "$(cat "$HERE/AGENTS.md")" \
  --tools "" \
  --mcp-config "$MCP" --strict-mcp-config \
  --allowedTools "$TOOLS" \
  --setting-sources "" \
  --disable-slash-commands \
  --no-session-persistence \
  --max-budget-usd "${AGENT_BUDGET:-0.50}" \
  --output-format "${AGENT_OUTPUT:-text}" < /dev/null
