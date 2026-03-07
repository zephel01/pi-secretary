#!/usr/bin/env bash
# ============================================================
#  check_system_status — システム状態確認
#  Pi 5 の温度、メモリ、ストレージ、サービス状態を返す
# ============================================================

set -euo pipefail

# --- CPU温度 ---
if [[ -f /sys/class/thermal/thermal_zone0/temp ]]; then
  TEMP_RAW=$(cat /sys/class/thermal/thermal_zone0/temp)
  CPU_TEMP=$(echo "scale=1; ${TEMP_RAW}/1000" | bc 2>/dev/null || echo "${TEMP_RAW}")
else
  CPU_TEMP="取得不可"
fi

# --- メモリ ---
MEM_TOTAL=$(free -m | awk '/^Mem:/ {print $2}')
MEM_USED=$(free -m | awk '/^Mem:/ {print $3}')
MEM_AVAIL=$(free -m | awk '/^Mem:/ {print $7}')
MEM_PERCENT=$(echo "scale=0; ${MEM_USED}*100/${MEM_TOTAL}" | bc 2>/dev/null || echo "?")

# --- ストレージ ---
DISK_USAGE=$(df -h / | awk 'NR==2 {print $5}')
DISK_AVAIL=$(df -h / | awk 'NR==2 {print $4}')

# --- 稼働時間 ---
UPTIME=$(uptime -p 2>/dev/null || uptime)

# --- サービス状態 ---
check_service() {
  local svc="$1"
  if systemctl is-active --quiet "${svc}" 2>/dev/null; then
    echo "稼働中"
  elif systemctl is-enabled --quiet "${svc}" 2>/dev/null; then
    echo "停止中(有効)"
  else
    echo "無効"
  fi
}

SVC_OPENCLAW=$(check_service "openclaw")
SVC_VOICEVOX=$(check_service "voicevox")
SVC_VOICE_BRIDGE=$(check_service "voice-bridge")

# --- Docker sandbox ---
if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "ai-secretary-sandbox"; then
  SVC_SANDBOX="稼働中"
else
  SVC_SANDBOX="停止中"
fi

# --- VOICEVOX ヘルスチェック ---
VOICEVOX_HEALTH="不明"
if curl -s --connect-timeout 2 "http://127.0.0.1:50021/version" >/dev/null 2>&1; then
  VOICEVOX_VER=$(curl -s "http://127.0.0.1:50021/version" 2>/dev/null || echo "?")
  VOICEVOX_HEALTH="応答OK (v${VOICEVOX_VER})"
else
  VOICEVOX_HEALTH="応答なし"
fi

# --- OpenClaw ヘルスチェック ---
OPENCLAW_HEALTH="不明"
if curl -s --connect-timeout 2 "http://127.0.0.1:18789/health" >/dev/null 2>&1; then
  OPENCLAW_HEALTH="応答OK"
else
  OPENCLAW_HEALTH="応答なし"
fi

# --- 結果出力 (JSON) ---
cat <<EOF
{
  "timestamp": "$(date -Iseconds)",
  "system": {
    "cpu_temp_celsius": "${CPU_TEMP}",
    "memory_total_mb": ${MEM_TOTAL},
    "memory_used_mb": ${MEM_USED},
    "memory_available_mb": ${MEM_AVAIL},
    "memory_percent": ${MEM_PERCENT},
    "disk_usage": "${DISK_USAGE}",
    "disk_available": "${DISK_AVAIL}",
    "uptime": "${UPTIME}"
  },
  "services": {
    "openclaw": "${SVC_OPENCLAW}",
    "voicevox": "${SVC_VOICEVOX}",
    "voice_bridge": "${SVC_VOICE_BRIDGE}",
    "docker_sandbox": "${SVC_SANDBOX}"
  },
  "health": {
    "voicevox": "${VOICEVOX_HEALTH}",
    "openclaw": "${OPENCLAW_HEALTH}"
  }
}
EOF
