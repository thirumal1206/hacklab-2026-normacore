#!/usr/bin/env bash
# The whole demo in one command: health checks, dashboard, then Friday (Claude, locked down) listening for voice.
#   ./demo.sh                                   fake packs, macOS voice
#   AGENT_BACKEND=real ARM_TTS=voicestudio ./demo.sh     real arm through the IK service, VoiceStudio voice
#   AGENT_DRY_RUN=1 ...                          plan every move, move nothing
# Stop: say "Friday, stop", press STOP on the dashboard, or touch runs/agent/STOP. Ctrl-C ends Claude.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")" && pwd)
cd "$ROOT"
up() { curl -s -o /dev/null -m 2 "$1"; }
fail() { echo "NOT READY: $1" >&2; exit 1; }

[ -x .venv/bin/python ] || fail "run 'uv sync' first"
[ -e runs/agent/STOP ] && fail "runs/agent/STOP exists (a person stopped the last run). Check why, then delete it."
if [ "${AGENT_BACKEND:-fake}" = real ]; then
  up http://127.0.0.1:8889/ || fail "Station is not running (web UI :8889)"
  up http://127.0.0.1:8020/api/v1/health || fail "elrobot-ik is not running (:8020): venue/elrobot/ik-calib/start_ik.sh"
  AGENT_BACKEND=real .venv/bin/python -c "import sys; sys.path.insert(0,'agent'); import sorter; m=sorter.missing_config(); sys.exit('config incomplete: '+', '.join(m) if m else 0)" \
    || fail "fill agent/config/ first"
fi
if [ "${ARM_TTS:-say}" = voicestudio ] && ! up http://127.0.0.1:3900/; then
  echo "VoiceStudio is not running on :3900: using the macOS voice" >&2
  export ARM_TTS=say
fi
mkdir -p runs/agent
if ! up http://127.0.0.1:8770/state; then
  .venv/bin/python agent/dashboard.py > runs/agent/dashboard.log 2>&1 &
  for _ in $(seq 20); do up http://127.0.0.1:8770/state && break; sleep 0.25; done
fi
up http://127.0.0.1:8770/state || fail "dashboard did not start: see runs/agent/dashboard.log"
echo "Dashboard: http://127.0.0.1:8770  (Chrome: press Hands-free, then say 'Friday, ...')"
open http://127.0.0.1:8770 2>/dev/null || true
AGENT_BUDGET=${AGENT_BUDGET:-2.00} exec operator/sorter/run.sh "Run in assistant mode: greet once, then listen for instructions."
