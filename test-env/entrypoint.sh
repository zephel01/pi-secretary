#!/usr/bin/env bash
# ============================================================
#  テスト環境エントリポイント
#  supervisor で各サービスを管理 (systemd の代替)
# ============================================================
set -e

echo "========================================"
echo "  AI Secretary テスト環境"
echo "  $(uname -m) / $(cat /etc/os-release | grep PRETTY_NAME | cut -d'"' -f2)"
echo "  Node.js $(node -v) / Python $(python3 --version | cut -d' ' -f2)"
echo "========================================"

# supervisor 設定があれば起動
if [[ -f /etc/supervisor/conf.d/ai-secretary.conf ]]; then
  echo "[*] supervisor でサービスを起動..."
  exec /usr/bin/supervisord -n -c /etc/supervisor/supervisord.conf
else
  echo "[*] インタラクティブモード (bash)"
  echo ""
  echo "使い方:"
  echo "  # セットアップスクリプトのテスト (root で実行)"
  echo "  cd /mnt/pi-secretary-setup && bash setup.sh"
  echo ""
  echo "  # 個別コンポーネントのテスト"
  echo "  cd /opt/ai-secretary/voice-bridge && python3 main_headless.py"
  echo ""
  exec /bin/bash
fi
