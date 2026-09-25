#!/usr/bin/env bash
# neko-agent-monitor one-shot installer (macOS):
#   venv → editable install with the embedding extra → fetch model → launchd agent.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-python3}"
echo "(=^･ω･^=)  setting up neko-agent-monitor in $(pwd)"
if [ ! -x .venv/bin/python ]; then
  "$PY" -m venv .venv
fi
.venv/bin/python -m pip install --upgrade pip >/dev/null
.venv/bin/python -m pip install -e '.[embed]'
.venv/bin/neko fetch-model || echo "!! model download failed — neko will use the offline hashing fallback"
.venv/bin/neko install
echo
echo "done! open http://127.0.0.1:${NEKO_PORT:-8765}/"
echo "optional: .venv/bin/neko hooks install      # instant state updates from Claude Code"
echo "optional: make overlay                      # cats on your desktop"
