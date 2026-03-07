#!/bin/bash
# voice-bridge ヘッドレスモード起動 (未セットアップなら待機)
export HOME="/home/aiagent"
export MOCK_AUDIO="1"
export PYTHONUNBUFFERED="1"

VB_DIR="/opt/ai-secretary/voice-bridge"
VB_PYTHON="${VB_DIR}/venv/bin/python"

if [ -f "${VB_PYTHON}" ] && [ -f "${VB_DIR}/main_headless.py" ]; then
    echo "[voice-bridge] Starting headless mode..."
    cd "${VB_DIR}"
    exec "${VB_PYTHON}" main_headless.py
else
    echo "[voice-bridge] SKIP: not set up (venv or main_headless.py missing). Waiting..."
    exec sleep infinity
fi
