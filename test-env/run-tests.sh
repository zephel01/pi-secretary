#!/usr/bin/env bash
# ============================================================
#  AI Secretary テスト環境 — 検証スクリプト
#  コンテナ内 or ホストから実行してコンポーネントをチェックする
#
#  使い方:
#    コンテナ内:  bash /mnt/pi-secretary-setup/test-env/run-tests.sh
#    ホスト側:    bash test-env/run-tests.sh --host
# ============================================================
set -u

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

PASS=0
FAIL=0
SKIP=0

pass() { echo -e "  ${GREEN}✓ PASS${NC} $*"; ((PASS++)); }
fail() { echo -e "  ${RED}✗ FAIL${NC} $*"; ((FAIL++)); }
skip() { echo -e "  ${YELLOW}○ SKIP${NC} $*"; ((SKIP++)); }
section() { echo -e "\n${CYAN}━━━ $* ━━━${NC}"; }

HOST_MODE=false
[[ "${1:-}" == "--host" ]] && HOST_MODE=true

# ホストモードのベース URL
if $HOST_MODE; then
  BASE="http://localhost"
else
  BASE="http://127.0.0.1"
fi

# ============================================================
section "1. 環境チェック"
# ============================================================

# アーキテクチャ
ARCH=$(uname -m)
if [[ "$ARCH" == "aarch64" ]] || [[ "$ARCH" == "arm64" ]]; then
  pass "アーキテクチャ: $ARCH (ARM64)"
else
  fail "アーキテクチャ: $ARCH (ARM64 が期待値)"
fi

# Node.js バージョン
if command -v node &>/dev/null; then
  NODE_VER=$(node -v | sed 's/v//' | cut -d. -f1)
  if [[ "$NODE_VER" -ge 22 ]]; then
    pass "Node.js: $(node -v) (>= 22 必須)"
  else
    fail "Node.js: $(node -v) (22+ が必要)"
  fi
else
  fail "Node.js が見つかりません"
fi

# Python3
if command -v python3 &>/dev/null; then
  pass "Python: $(python3 --version)"
else
  fail "Python3 が見つかりません"
fi

# 必要コマンド
for cmd in jq git curl ffmpeg; do
  if command -v "$cmd" &>/dev/null; then
    pass "コマンド: $cmd"
  else
    fail "コマンド: $cmd が見つかりません"
  fi
done

# ============================================================
section "2. ディレクトリ構成"
# ============================================================

DIRS=(
  "/opt/ai-secretary"
  "/opt/ai-secretary/openclaw/tools"
  "/opt/ai-secretary/openclaw/memory"
  "/opt/ai-secretary/voice-bridge"
  "/opt/ai-secretary/voicevox"
  "/opt/ai-secretary/sandbox"
)

for dir in "${DIRS[@]}"; do
  if [[ -d "$dir" ]]; then
    pass "ディレクトリ: $dir"
  else
    fail "ディレクトリ: $dir が存在しません"
  fi
done

# ============================================================
section "3. setup.sh テスト (dry-run 解析)"
# ============================================================

SETUP_SH="/mnt/pi-secretary-setup/setup.sh"
if $HOST_MODE; then
  SETUP_SH="$(cd "$(dirname "$0")/.." && pwd)/setup.sh"
fi

if [[ -f "$SETUP_SH" ]]; then
  pass "setup.sh が存在します"

  # 構文チェック
  if bash -n "$SETUP_SH" 2>/dev/null; then
    pass "setup.sh 構文チェック OK"
  else
    fail "setup.sh に構文エラーがあります"
  fi

  # ポート番号の一貫性チェック
  if grep -q ":3000" "$SETUP_SH"; then
    fail "setup.sh に旧ポート :3000 の参照が残っています"
  else
    pass "setup.sh ポート参照: :18789 に統一済み"
  fi

  # openclaw パッケージ名
  if grep -q "open-claw" "$SETUP_SH"; then
    fail "setup.sh に誤ったパッケージ名 'open-claw' が残っています"
  else
    pass "setup.sh パッケージ名: openclaw (正)"
  fi

  # Node.js 22
  if grep -q "setup_22.x" "$SETUP_SH"; then
    pass "setup.sh Node.js バージョン: 22 指定"
  else
    fail "setup.sh に Node.js 22 の指定がありません"
  fi
else
  skip "setup.sh が見つかりません ($SETUP_SH)"
fi

# ============================================================
section "4. systemd サービスファイル検証"
# ============================================================

SYSTEMD_DIR="/mnt/pi-secretary-setup/systemd"
if $HOST_MODE; then
  SYSTEMD_DIR="$(cd "$(dirname "$0")/.." && pwd)/systemd"
fi

for svc in openclaw.service voicevox.service voice-bridge.service; do
  svc_file="${SYSTEMD_DIR}/${svc}"
  if [[ -f "$svc_file" ]]; then
    pass "サービスファイル: $svc"

    # ポート番号チェック
    if grep -q ":3000" "$svc_file"; then
      fail "  $svc に旧ポート :3000 が残っています"
    else
      pass "  $svc ポート参照 OK"
    fi
  else
    fail "サービスファイル: $svc が見つかりません"
  fi
done

# ============================================================
section "5. VOICEVOX API テスト"
# ============================================================

VOICEVOX_URL="${BASE}:50021"

# ヘルスチェック
if curl -sf "${VOICEVOX_URL}/" >/dev/null 2>&1; then
  pass "VOICEVOX: ヘルスチェック OK (${VOICEVOX_URL})"

  # スピーカー一覧
  SPEAKERS=$(curl -sf "${VOICEVOX_URL}/speakers" 2>/dev/null)
  if [[ -n "$SPEAKERS" ]]; then
    # ずんだもん (speaker_id=3) の存在確認
    if echo "$SPEAKERS" | jq -e '.[].styles[] | select(.id == 3)' >/dev/null 2>&1; then
      pass "VOICEVOX: ずんだもん (id=3) 確認"
    else
      fail "VOICEVOX: ずんだもん (id=3) が見つかりません"
    fi
  else
    fail "VOICEVOX: /speakers レスポンスが空です"
  fi

  # 音声クエリ → 合成
  QUERY=$(curl -sf -X POST "${VOICEVOX_URL}/audio_query?text=%E3%83%86%E3%82%B9%E3%83%88%E3%81%AA%E3%81%AE%E3%81%A0&speaker=3" 2>/dev/null)
  if [[ -n "$QUERY" ]]; then
    pass "VOICEVOX: audio_query OK"

    # 合成テスト
    WAV=$(curl -sf -X POST \
      -H "Content-Type: application/json" \
      -d "$QUERY" \
      "${VOICEVOX_URL}/synthesis?speaker=3" 2>/dev/null | head -c 4)
    if [[ "$WAV" == "RIFF" ]]; then
      pass "VOICEVOX: synthesis → WAV 生成 OK"
    else
      fail "VOICEVOX: synthesis レスポンスが WAV ではありません"
    fi
  else
    fail "VOICEVOX: audio_query が失敗しました"
  fi
else
  skip "VOICEVOX: サーバーが起動していません (${VOICEVOX_URL})"
fi

# ============================================================
section "6. OpenClaw API テスト"
# ============================================================

OPENCLAW_URL="${BASE}:18789"

if curl -sf "${OPENCLAW_URL}/" >/dev/null 2>&1; then
  pass "OpenClaw: ヘルスチェック OK (${OPENCLAW_URL})"

  # /v1/chat/completions エンドポイント
  CHAT_RES=$(curl -sf -X POST \
    "${OPENCLAW_URL}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer test-token" \
    -d '{"model":"openclaw","messages":[{"role":"user","content":"テスト"}]}' 2>/dev/null)
  if [[ -n "$CHAT_RES" ]]; then
    pass "OpenClaw: /v1/chat/completions レスポンスあり"
  else
    fail "OpenClaw: /v1/chat/completions が空 or エラー"
  fi
else
  skip "OpenClaw: サーバーが起動していません (${OPENCLAW_URL})"
fi

# ============================================================
section "7. voice-bridge モジュール検証"
# ============================================================

VB_DIR="/opt/ai-secretary/voice-bridge"

for mod in ai_chat.py mic_capture_linux.py main_headless.py; do
  if [[ -f "${VB_DIR}/${mod}" ]]; then
    pass "voice-bridge: ${mod} 存在"

    # Python 構文チェック
    if python3 -c "import py_compile; py_compile.compile('${VB_DIR}/${mod}', doraise=True)" 2>/dev/null; then
      pass "voice-bridge: ${mod} 構文 OK"
    else
      fail "voice-bridge: ${mod} に構文エラーがあります"
    fi
  else
    skip "voice-bridge: ${mod} が見つかりません (setup.sh 未実行?)"
  fi
done

# venv チェック
if [[ -f "${VB_DIR}/venv/bin/python" ]]; then
  pass "voice-bridge: venv 作成済み"
else
  skip "voice-bridge: venv 未作成 (setup.sh 未実行?)"
fi

# ============================================================
section "8. 設定ファイル一貫性チェック"
# ============================================================

CONFIG_DIR="/mnt/pi-secretary-setup/config"
if $HOST_MODE; then
  CONFIG_DIR="$(cd "$(dirname "$0")/.." && pwd)/config"
fi

# voice-bridge.env
ENV_FILE="${CONFIG_DIR}/voice-bridge.env"
if [[ -f "$ENV_FILE" ]]; then
  if grep -q "18789" "$ENV_FILE"; then
    pass "voice-bridge.env: ポート 18789"
  else
    fail "voice-bridge.env: ポート 18789 が設定されていません"
  fi
else
  fail "voice-bridge.env が見つかりません"
fi

# openclaw.json
OC_JSON="${CONFIG_DIR}/openclaw.json"
if [[ -f "$OC_JSON" ]]; then
  if jq -e '.gateway.http.endpoints.chatCompletions.enabled' "$OC_JSON" 2>/dev/null | grep -q "true"; then
    pass "openclaw.json: chatCompletions 有効"
  else
    fail "openclaw.json: chatCompletions が無効です"
  fi
else
  fail "openclaw.json が見つかりません"
fi

# ============================================================
section "9. ツールファイル検証"
# ============================================================

TOOLS_DIR="/mnt/pi-secretary-setup/tools"
if $HOST_MODE; then
  TOOLS_DIR="$(cd "$(dirname "$0")/.." && pwd)/tools"
fi

for tool in get_schedule.py get_todos.py add_note.py check_status.sh; do
  if [[ -f "${TOOLS_DIR}/${tool}" ]]; then
    pass "ツール: ${tool}"
  else
    fail "ツール: ${tool} が見つかりません"
  fi
done

# check_status.sh のポート番号
if [[ -f "${TOOLS_DIR}/check_status.sh" ]]; then
  if grep -q ":3000" "${TOOLS_DIR}/check_status.sh"; then
    fail "check_status.sh に旧ポート :3000 が残っています"
  else
    pass "check_status.sh ポート参照 OK"
  fi
fi

# ============================================================
#  結果サマリー
# ============================================================
echo ""
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "  ${GREEN}PASS: ${PASS}${NC}  ${RED}FAIL: ${FAIL}${NC}  ${YELLOW}SKIP: ${SKIP}${NC}"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

if [[ $FAIL -gt 0 ]]; then
  echo -e "\n${RED}テストに失敗があります。上記の FAIL 項目を確認してください。${NC}"
  exit 1
elif [[ $SKIP -gt 0 ]]; then
  echo -e "\n${YELLOW}一部のテストがスキップされました。セットアップ完了後に再実行してください。${NC}"
  exit 0
else
  echo -e "\n${GREEN}すべてのテストに合格しました！${NC}"
  exit 0
fi
