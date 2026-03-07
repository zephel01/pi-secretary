#!/bin/bash
# OpenClaw Gateway 起動 (未インストールなら待機)
export HOME="/home/aiagent"
export PATH="/usr/local/bin:/usr/bin:/bin"

if command -v openclaw >/dev/null 2>&1; then
    echo "[openclaw] Starting gateway on port 18789..."
    exec openclaw gateway --port 18789 --verbose
else
    echo "[openclaw] SKIP: not installed. Waiting..."
    exec sleep infinity
fi
