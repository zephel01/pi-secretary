# ============================================================
#  Raspberry Pi 5 模擬環境 (ARM64 / Debian bookworm)
#  Apple Silicon Mac の Docker で Pi 向けセットアップをテストする
# ============================================================
FROM --platform=linux/arm64 debian:bookworm-slim

LABEL description="AI Secretary - Pi 5 test environment"

# Pi OS Lite に近いベースパッケージ
RUN apt-get update && apt-get install -y --no-install-recommends \
    # 基本
    bash sudo git curl wget ca-certificates gnupg \
    build-essential pkg-config \
    # Python
    python3 python3-pip python3-venv python3-dev \
    # オーディオ (実デバイスは無いがライブラリは入れる)
    portaudio19-dev libsndfile1 ffmpeg alsa-utils \
    # ユーティリティ
    jq bc procps nano \
    # systemd の代わりに使うもの
    supervisor \
    && rm -rf /var/lib/apt/lists/*

# Node.js 22 (OpenClaw 要件)
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Docker CLI (sandbox テスト用 — DinD ではなくホストの Docker ソケットをマウント)
RUN curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /usr/share/keyrings/docker.gpg \
    && echo "deb [arch=arm64 signed-by=/usr/share/keyrings/docker.gpg] https://download.docker.com/linux/debian bookworm stable" \
      > /etc/apt/sources.list.d/docker.list \
    && apt-get update && apt-get install -y --no-install-recommends docker-ce-cli \
    && rm -rf /var/lib/apt/lists/*

# aiagent ユーザー
RUN useradd -m -s /bin/bash aiagent \
    && echo "aiagent ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers

# ディレクトリ構成 (setup.sh と同じ — sh ではブレース展開が使えないため個別指定)
RUN mkdir -p \
    /opt/ai-secretary/openclaw/tools \
    /opt/ai-secretary/openclaw/memory \
    /opt/ai-secretary/voice-bridge/custom \
    /opt/ai-secretary/voice-bridge/logs \
    /opt/ai-secretary/voicevox/data \
    /opt/ai-secretary/sandbox/workdir

# aarch64 を偽装 (setup.sh の事前チェック通過用)
# Apple Silicon Docker は実際に aarch64 なので不要だが念のため
RUN uname -m

WORKDIR /opt/ai-secretary

# VOICEVOX モックサーバーを内蔵 (Flask)
RUN pip3 install --break-system-packages --no-cache-dir flask
COPY voicevox_mock.py /opt/ai-secretary/voicevox-mock/voicevox_mock.py

# supervisor ラッパースクリプトを配置
COPY supervisor/start-*.sh /etc/supervisor/conf.d/
RUN chmod +x /etc/supervisor/conf.d/start-*.sh

# ログディレクトリ
RUN mkdir -p /var/log/supervisor && chown aiagent:aiagent /var/log/supervisor

# テスト用エントリポイント
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 18789 50021

CMD ["/entrypoint.sh"]
