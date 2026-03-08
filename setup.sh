#!/usr/bin/env bash
# ============================================================
#  AI Secretary — Raspberry Pi 5 構築スクリプト
#  完成構成: voice-bridge + OpenClaw + VOICEVOX + Ollama + Docker sandbox
# ============================================================
set -euo pipefail

# ---------- 色付き出力 ----------
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ---------- 事前チェック ----------
[[ $(uname -m) == "aarch64" ]] || error "64-bit OS が必要です (aarch64)"
[[ $EUID -eq 0 ]] || error "root で実行してください: sudo bash setup.sh"

INSTALL_DIR="/opt/ai-secretary"
SERVICE_USER="aiagent"
SERVICE_HOME="/home/${SERVICE_USER}"
OPENCLAW_HOME="${SERVICE_HOME}/.openclaw"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

info "=== AI Secretary セットアップ開始 ==="
info "インストール先: ${INSTALL_DIR}"

# ---------- 1. システムパッケージ ----------
info "--- 1/9 システムパッケージ ---"
apt-get update -qq
apt-get install -y -qq \
  git curl wget build-essential python3 python3-pip python3-venv \
  portaudio19-dev libsndfile1 ffmpeg \
  docker.io docker-compose \
  jq alsa-utils

# Node.js 22+ が必要 (OpenClaw 要件)
NODE_VER=$(node -v 2>/dev/null | sed 's/v//' | cut -d. -f1 || true)
if [[ "${NODE_VER:-0}" -lt 22 ]]; then
  info "Node.js 22 をインストールします..."
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
  apt-get install -y -qq nodejs
fi
info "Node.js $(node -v)"

# ---------- 2. 専用ユーザー ----------
info "--- 2/9 専用ユーザー作成 ---"
if ! id "${SERVICE_USER}" &>/dev/null; then
  useradd -r -m -s /bin/bash "${SERVICE_USER}"
  usermod -aG docker "${SERVICE_USER}"
  info "ユーザー ${SERVICE_USER} を作成しました"
else
  info "ユーザー ${SERVICE_USER} は既に存在します"
fi

# ---------- 3. ディレクトリ構成 ----------
info "--- 3/9 ディレクトリ構成 ---"
mkdir -p "${INSTALL_DIR}"/{voice-bridge/{custom,logs},voicevox/data,sandbox/workdir}
mkdir -p "${INSTALL_DIR}/openclaw/tools"
mkdir -p "${INSTALL_DIR}/openclaw/memory"

# ---------- 4. OpenClaw インストール ----------
info "--- 4/9 OpenClaw Gateway ---"
if ! command -v openclaw &>/dev/null; then
  info "OpenClaw CLI をインストール中..."

  # 方法A: 公式インストールスクリプト (推奨)
  curl -fsSL https://openclaw.ai/install.sh | sudo -u "${SERVICE_USER}" bash 2>/dev/null || {
    # 方法B: npm グローバルインストール
    warn "公式スクリプト失敗 — npm install を試みます"
    npm install -g openclaw@latest 2>/dev/null || {
      # 方法C: ソースからビルド
      warn "npm install 失敗 — ソースからビルドします"
      cd /tmp
      git clone https://github.com/openclaw/openclaw.git openclaw-src
      cd openclaw-src
      npm install && npm run build && npm link
      cd /tmp && rm -rf openclaw-src
    }
  }
  info "OpenClaw $(openclaw --version 2>/dev/null || echo '(バージョン不明)') をインストールしました"
else
  info "OpenClaw は既にインストール済みです: $(openclaw --version 2>/dev/null || echo '')"
fi

# OpenClaw 初期設定 (onboard)
# --non-interactive で対話ウィザードをスキップし、後から設定を注入する
if [[ ! -d "${OPENCLAW_HOME}" ]]; then
  info "OpenClaw の初期設定を実行します (非対話モード)..."

  # プロバイダの優先順位: OpenRouter > Anthropic API > スキップ (Ollama のみ)
  if [[ -n "${OPENROUTER_API_KEY:-}" ]]; then
    info "OPENROUTER_API_KEY を検出 — OpenRouter プロバイダで設定します"
    sudo -u "${SERVICE_USER}" \
      OPENROUTER_API_KEY="${OPENROUTER_API_KEY}" \
      openclaw onboard \
        --non-interactive \
        --accept-risk \
        --flow quickstart \
        --auth-choice openrouter-api-key \
        --openrouter-api-key "${OPENROUTER_API_KEY}" \
        --install-daemon \
        --gateway-port 18789 || {
      warn "openclaw onboard (OpenRouter) が失敗しました"
    }
  elif [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
    info "ANTHROPIC_API_KEY を検出 — Anthropic プロバイダで設定します"
    sudo -u "${SERVICE_USER}" \
      ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY}" \
      openclaw onboard \
        --non-interactive \
        --accept-risk \
        --flow quickstart \
        --auth-choice anthropic-api-key \
        --anthropic-api-key "${ANTHROPIC_API_KEY}" \
        --install-daemon \
        --gateway-port 18789 || {
      warn "openclaw onboard (Anthropic) が失敗しました"
    }
  else
    # API キーなし — 最小構成で初期化 (Ollama のみで運用可)
    sudo -u "${SERVICE_USER}" \
      openclaw onboard \
        --non-interactive \
        --accept-risk \
        --flow quickstart \
        --auth-choice skip \
        --install-daemon \
        --gateway-port 18789 || {
      warn "openclaw onboard (スキップモード) が失敗しました"
    }
    warn "クラウド API キーが未設定です。Ollama (ローカル LLM) のみで動作します。"
    warn "クラウドモデルを使う場合は以下のいずれかを設定してください:"
    warn "  export OPENROUTER_API_KEY=sk-or-..."
    warn "  export ANTHROPIC_API_KEY=sk-ant-..."
  fi
else
  info "OpenClaw は設定済みです: ${OPENCLAW_HOME}"
fi

# chatCompletions エンドポイントを有効化
# (voice-bridge が /v1/chat/completions で通信するため必須)
OPENCLAW_CONFIG="${OPENCLAW_HOME}/openclaw.json"
if [[ -f "${OPENCLAW_CONFIG}" ]]; then
  info "chatCompletions エンドポイントを有効化..."
  # jq で安全に設定を追加/更新
  TMP_CFG=$(mktemp)
  jq '.gateway.http.endpoints.chatCompletions.enabled = true' "${OPENCLAW_CONFIG}" > "${TMP_CFG}" 2>/dev/null && {
    mv "${TMP_CFG}" "${OPENCLAW_CONFIG}"
    chown "${SERVICE_USER}:${SERVICE_USER}" "${OPENCLAW_CONFIG}"
    info "chatCompletions を有効化しました"
  } || {
    rm -f "${TMP_CFG}"
    warn "openclaw.json の自動編集に失敗しました。手動で有効化してください:"
    warn "  ${OPENCLAW_CONFIG} に以下を追加:"
    warn '  "gateway": { "http": { "endpoints": { "chatCompletions": { "enabled": true } } } }'
  }
else
  warn "openclaw.json が見つかりません。openclaw onboard を先に実行してください。"
fi

# 秘書プロンプト配置 (OpenClaw workspace)
WORKSPACE="${OPENCLAW_HOME}/workspace"
if [[ -d "${WORKSPACE}" ]]; then
  # SOUL.md にずんだもん秘書の人格を設定
  if [[ -f "${SCRIPT_DIR}/config/openclaw-secretary-prompt.md" ]]; then
    cp "${SCRIPT_DIR}/config/openclaw-secretary-prompt.md" "${WORKSPACE}/SOUL.md"
    chown "${SERVICE_USER}:${SERVICE_USER}" "${WORKSPACE}/SOUL.md"
    info "ずんだもん秘書プロンプトを SOUL.md に配置しました"
  fi
fi

# ---------- 5. voice-bridge ----------
info "--- 5/9 voice-bridge ---"
if [[ ! -d "${INSTALL_DIR}/voice-bridge/.git" ]]; then
  info "voice-bridge を GitHub からクローン中..."
  cd "${INSTALL_DIR}"
  # 既存ディレクトリがある場合は退避
  [[ -d "${INSTALL_DIR}/voice-bridge" ]] && mv "${INSTALL_DIR}/voice-bridge" "${INSTALL_DIR}/voice-bridge.bak.$(date +%s)"
  git clone https://github.com/zephel01/voice-bridge.git "${INSTALL_DIR}/voice-bridge"
  info "voice-bridge をクローンしました"
else
  info "voice-bridge は既にクローン済みです"
fi

# Pi向け改修ファイルを配置
info "秘書化モジュールを配置中..."
mkdir -p "${INSTALL_DIR}/voice-bridge/custom"
mkdir -p "${INSTALL_DIR}/voice-bridge/logs"
for mod in ai_chat.py mic_capture_linux.py main_headless.py; do
  if [[ -f "${SCRIPT_DIR}/voice-bridge-mods/${mod}" ]]; then
    cp "${SCRIPT_DIR}/voice-bridge-mods/${mod}" "${INSTALL_DIR}/voice-bridge/"
    info "  ${mod} を配置"
  fi
done

# Pi向け requirements を配置
if [[ -f "${SCRIPT_DIR}/voice-bridge-mods/requirements-pi.txt" ]]; then
  cp "${SCRIPT_DIR}/voice-bridge-mods/requirements-pi.txt" "${INSTALL_DIR}/voice-bridge/"
fi

# .env 配置
cp "${SCRIPT_DIR}/config/voice-bridge.env" "${INSTALL_DIR}/voice-bridge/.env" 2>/dev/null || true

# 秘書プロンプト
cp "${SCRIPT_DIR}/config/secretary_prompt.txt" "${INSTALL_DIR}/voice-bridge/custom/" 2>/dev/null || true

# Python venv 作成 & 依存インストール
if [[ ! -d "${INSTALL_DIR}/voice-bridge/venv" ]]; then
  info "Python venv を作成中..."
  python3 -m venv "${INSTALL_DIR}/voice-bridge/venv"
  "${INSTALL_DIR}/voice-bridge/venv/bin/pip" install --upgrade pip

  # Pi向け requirements を優先、なければオリジナル
  if [[ -f "${INSTALL_DIR}/voice-bridge/requirements-pi.txt" ]]; then
    "${INSTALL_DIR}/voice-bridge/venv/bin/pip" install -r "${INSTALL_DIR}/voice-bridge/requirements-pi.txt"
    info "requirements-pi.txt でインストール完了"
  elif [[ -f "${INSTALL_DIR}/voice-bridge/requirements.txt" ]]; then
    "${INSTALL_DIR}/voice-bridge/venv/bin/pip" install -r "${INSTALL_DIR}/voice-bridge/requirements.txt"
    info "requirements.txt でインストール完了"
  fi
fi

# ---------- 6. VOICEVOX Engine ----------
info "--- 6/9 VOICEVOX Engine ---"
VOICEVOX_VERSION="0.25.1"
VOICEVOX_DIR="${INSTALL_DIR}/voicevox"

if [[ ! -f "${VOICEVOX_DIR}/engine/run" ]]; then
  cd /tmp
  VOICEVOX_ARCHIVE="voicevox_engine-linux-cpu-arm64-${VOICEVOX_VERSION}.7z.001"
  VOICEVOX_URL="https://github.com/VOICEVOX/voicevox_engine/releases/download/${VOICEVOX_VERSION}/${VOICEVOX_ARCHIVE}"

  if [[ ! -f "${VOICEVOX_ARCHIVE}" ]] || [[ ! -s "${VOICEVOX_ARCHIVE}" ]]; then
    rm -f "${VOICEVOX_ARCHIVE}"
    info "VOICEVOX Engine ${VOICEVOX_VERSION} をダウンロード中..."
    wget -q "${VOICEVOX_URL}" -O "${VOICEVOX_ARCHIVE}" || {
      rm -f "${VOICEVOX_ARCHIVE}"
      warn "VOICEVOX ARM64 バイナリの自動ダウンロードに失敗しました"
      warn "手動で配置してください: ${VOICEVOX_DIR}/engine/"
      warn "URL: ${VOICEVOX_URL}"
    }
  fi

  if [[ -f "${VOICEVOX_ARCHIVE}" ]] && [[ -s "${VOICEVOX_ARCHIVE}" ]]; then
    apt-get install -y -qq p7zip-full 2>/dev/null || true
    mkdir -p "${VOICEVOX_DIR}/engine"
    7z x -y "${VOICEVOX_ARCHIVE}" -o"${VOICEVOX_DIR}/engine" || {
      warn "7z 展開に失敗しました。手動で展開してください。"
    }
    chmod +x "${VOICEVOX_DIR}/engine/run" 2>/dev/null || true
    info "VOICEVOX Engine を展開しました"
  else
    warn "VOICEVOX アーカイブが見つからないか空です。スキップします。"
  fi
else
  info "VOICEVOX Engine は既にインストール済みです"
fi

# ---------- 7. Ollama (ローカル LLM) ----------
info "--- 7/9 Ollama (ローカル LLM) ---"
if ! command -v ollama &>/dev/null; then
  info "Ollama をインストール中..."
  curl -fsSL https://ollama.ai/install.sh | bash 2>/dev/null || {
    warn "Ollama のインストールに失敗しました。手動でインストールしてください:"
    warn "  curl -fsSL https://ollama.ai/install.sh | bash"
  }
fi

if command -v ollama &>/dev/null; then
  info "Ollama $(ollama --version 2>/dev/null || echo '') をインストール済み"

  # Ollama サービスが動いていなければ起動
  if ! curl -sf http://127.0.0.1:11434/api/tags &>/dev/null; then
    if pidof systemd &>/dev/null; then
      systemctl enable --now ollama 2>/dev/null || true
    else
      warn "Ollama サービスを手動で起動してください: ollama serve &"
    fi
  fi

  # 推奨モデルをプル (Pi 5 8GB 向け)
  OLLAMA_MODEL="${OLLAMA_MODEL:-qwen2.5:7b}"
  if ! sudo -u "${SERVICE_USER}" ollama list 2>/dev/null | grep -q "${OLLAMA_MODEL}"; then
    info "モデル ${OLLAMA_MODEL} をダウンロード中 (数分かかります)..."
    sudo -u "${SERVICE_USER}" ollama pull "${OLLAMA_MODEL}" 2>/dev/null || {
      warn "モデル ${OLLAMA_MODEL} のダウンロードに失敗しました"
      warn "手動で実行: ollama pull ${OLLAMA_MODEL}"
    }
  else
    info "モデル ${OLLAMA_MODEL} は取得済みです"
  fi
else
  warn "Ollama のインストールをスキップしました"
fi

# ---------- 8. ツール群とDocker sandbox ----------
info "--- 8/9 ツール群 & Docker sandbox ---"

# 秘書ツールを配置
for tool in get_schedule.py get_todos.py add_note.py check_status.sh; do
  if [[ -f "${SCRIPT_DIR}/tools/${tool}" ]]; then
    cp "${SCRIPT_DIR}/tools/${tool}" "${INSTALL_DIR}/openclaw/tools/"
  fi
done
chmod +x "${INSTALL_DIR}/openclaw/tools/"*.sh 2>/dev/null || true
chmod +x "${INSTALL_DIR}/openclaw/tools/"*.py 2>/dev/null || true

# メモリファイル初期化
for f in notes.json todos.json schedule.json; do
  [[ -f "${INSTALL_DIR}/openclaw/memory/${f}" ]] || echo '[]' > "${INSTALL_DIR}/openclaw/memory/${f}"
done
[[ -f "${INSTALL_DIR}/openclaw/memory/profile.json" ]] || \
  echo '{"name":"","preferences":{}}' > "${INSTALL_DIR}/openclaw/memory/profile.json"

# Docker sandbox
cp "${SCRIPT_DIR}/docker/docker-compose.yml" "${INSTALL_DIR}/sandbox/"
cd "${INSTALL_DIR}/sandbox"
docker compose pull 2>/dev/null || docker-compose pull 2>/dev/null || true
info "Docker sandbox イメージを取得しました"

# ---------- 9. systemd サービス ----------
info "--- 9/9 systemd サービス ---"

# サービスファイルを /etc/systemd/system/ に配置
# (systemd がなくてもファイルコピーは行う — 実機移行時に使用)
if pidof systemd &>/dev/null && systemctl list-unit-files | grep -q openclaw; then
  info "openclaw.service は onboard で作成済みです"
else
  cp "${SCRIPT_DIR}/systemd/openclaw.service" /etc/systemd/system/ 2>/dev/null || \
    warn "openclaw.service の配置をスキップ (/etc/systemd/system/ が無い可能性)"
  info "openclaw.service を配置しました"
fi

# VOICEVOX と voice-bridge は常に配置
cp "${SCRIPT_DIR}/systemd/voicevox.service" /etc/systemd/system/ 2>/dev/null || true
cp "${SCRIPT_DIR}/systemd/voice-bridge.service" /etc/systemd/system/ 2>/dev/null || true

# 所有権
chown -R "${SERVICE_USER}:${SERVICE_USER}" "${INSTALL_DIR}"

# compile cache ディレクトリ
mkdir -p /var/tmp/openclaw-compile-cache
chown "${SERVICE_USER}:${SERVICE_USER}" /var/tmp/openclaw-compile-cache

# systemd が使える環境かチェック (Docker コンテナ内では使えない)
if pidof systemd &>/dev/null; then
  systemctl daemon-reload
  systemctl enable voicevox voice-bridge
  # openclaw は onboard で enable 済みの場合がある
  systemctl enable openclaw 2>/dev/null || true
else
  warn "systemd が検出されません (Docker コンテナ内?)。サービス登録をスキップします。"
  warn "実機では systemctl enable voicevox voice-bridge openclaw を手動で実行してください。"
fi

info "=== セットアップ完了 ==="
echo ""
info "次のステップ:"
echo ""
echo "  1. OpenClaw の初期設定 (まだの場合):"
echo "     sudo -u ${SERVICE_USER} openclaw onboard --install-daemon"
echo ""
echo "  2. 設定ファイルを確認・編集:"
echo "     ${INSTALL_DIR}/voice-bridge/.env"
echo "     ${OPENCLAW_HOME}/openclaw.json"
echo ""
echo "  3. サービスを起動 (この順序で):"
echo "     sudo systemctl start voicevox"
echo "     sudo systemctl start openclaw"
echo "     sudo systemctl start voice-bridge"
echo ""
echo "  4. 音声テスト:"
echo "     curl -s 'http://127.0.0.1:50021/audio_query?text=テストなのだ&speaker=3' | \\"
echo "       curl -s -H 'Content-Type:application/json' -d @- 'http://127.0.0.1:50021/synthesis?speaker=3' > /tmp/test.wav"
echo "     aplay /tmp/test.wav"
echo ""
echo "  5. OpenClaw API テスト:"
echo "     curl -s http://127.0.0.1:18789/v1/chat/completions \\"
echo "       -H 'Authorization: Bearer YOUR_TOKEN' \\"
echo "       -H 'Content-Type: application/json' \\"
echo "       -d '{\"model\":\"openclaw\",\"messages\":[{\"role\":\"user\",\"content\":\"今日の予定は？\"}]}'"
echo ""
warn "重要: OpenClaw は loopback (127.0.0.1) のみで動かしてください"
warn "外部アクセスが必要な場合は SSH トンネルか Tailscale を使ってください"
