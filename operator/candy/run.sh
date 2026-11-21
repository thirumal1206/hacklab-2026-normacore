#!/usr/bin/env bash
# Friday, the candy sorter, locked down: only the candy tools, our prompt, no files or shell, capped spend.
# One conversation for the whole session: if Friday's run ends without a goodbye, she is resumed in the same
# conversation (same memory) instead of starting over. Ctrl-C twice ends it.
#   operator/candy/run.sh                 dry run: plans every move, the arm does not move
#   CANDY_GO=1 operator/candy/run.sh      real arm (hand on the 12 V plug). Speak on the dashboard: http://127.0.0.1:8770
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
RUNS="$ROOT/runs/agent"
mkdir -p "$RUNS/candy-session" && cd "$RUNS/candy-session"
MCP=$(printf '{"mcpServers":{"candy":{"command":"%s","args":["%s"],"env":{"CANDY_GO":"%s","CANDY_CAMERA":"%s","ARM_TTS":"%s","AGENT_PROFILE":"%s"}}}}' \
  "$ROOT/.venv/bin/python" "$ROOT/agent/candy_mcp.py" "${CANDY_GO:-0}" "${CANDY_CAMERA:-}" "${ARM_TTS:-say}" "${AGENT_PROFILE:-}")
TOOLS=mcp__candy__spots,mcp__candy__pick,mcp__candy__say,mcp__candy__listen,mcp__candy__home,mcp__candy__session,mcp__candy__end,mcp__candy__grip
SID=$(uuidgen | tr 'A-Z' 'a-z')
rm -f "$RUNS/candy_end"
printf '{"time": "%s", "event": "session", "id": "%s"}\n' "$(date +%H:%M:%S)" "$SID" >> "$RUNS/candy.jsonl"
for run in $(seq 1 "${CANDY_MAX_RESTARTS:-20}"); do
  if [ "$run" = 1 ]; then ARGS=(--session-id "$SID"); PROMPT="${1:-Run as Friday: greet once, then listen for instructions.}"
  else ARGS=(--resume "$SID"); PROMPT="You were restarted inside the same session; everything above still applies. Do not greet again. Call listen now."; fi
  claude -p "$PROMPT" "${ARGS[@]}" --model "${AGENT_MODEL:-haiku}" \
    --system-prompt "$(cat "$HERE/AGENTS.md")" --tools "" --mcp-config "$MCP" --strict-mcp-config \
    --allowedTools "$TOOLS" --setting-sources "" --disable-slash-commands --max-budget-usd "${AGENT_BUDGET:-2.00}" \
    --output-format "${AGENT_OUTPUT:-text}" < /dev/null
  [ -e "$RUNS/candy_end" ] && { echo "Friday ended the session."; exit 0; }
  echo "Friday's run ended without a goodbye: resuming the same session ($run)." >&2
  sleep 1
done
