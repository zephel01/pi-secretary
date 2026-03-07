# AI秘書テスト環境 (macOS Apple Silicon)

Docker を使って Raspberry Pi 5 (ARM64) の環境を macOS 上で模擬し、
`setup.sh` やコンポーネントの動作をテストする環境です。

## 前提条件

- macOS (Apple Silicon / M系)
- Docker Desktop for Mac (ARM64 対応)
- `ANTHROPIC_API_KEY` (OpenClaw で Claude を使う場合)

## クイックスタート

```bash
# プロジェクトルートに移動
cd pi-secretary-setup

# 1. テスト環境を起動 (インタラクティブモード)
docker compose -f test-env/docker-compose.yml up -d pi-secretary

# 2. コンテナに入る
docker exec -it ai-secretary-test bash

# 3. setup.sh をテスト実行 (root で)
cd /mnt/pi-secretary-setup && bash setup.sh

# 4. 検証テスト
bash /mnt/pi-secretary-setup/test-env/run-tests.sh
```

## テストモード

### A. インタラクティブモード (推奨)

```bash
docker compose -f test-env/docker-compose.yml up -d pi-secretary
docker exec -it ai-secretary-test bash
```

コンテナ内に入り、`setup.sh` を手動実行してデバッグできます。

### B. supervisor で全サービス起動

supervisor 設定がマウントされているので、`setup.sh` 実行後は
supervisor で全サービスを管理できます:

```bash
# コンテナ内で
supervisord -n -c /etc/supervisor/supervisord.conf &
supervisorctl status
```

### C. VOICEVOX モック単独テスト

```bash
docker compose -f test-env/docker-compose.yml up voicevox-mock

# 別ターミナルから API テスト
curl -s 'http://localhost:50022/speakers' | jq .
curl -s -X POST 'http://localhost:50022/audio_query?text=テストなのだ&speaker=3' | \
  curl -s -X POST -H 'Content-Type:application/json' \
    -d @- 'http://localhost:50022/synthesis?speaker=3' > /tmp/test.wav
```

## テストスクリプト

`run-tests.sh` で以下を自動検証します:

| # | カテゴリ | 検証内容 |
|---|---------|---------|
| 1 | 環境 | アーキテクチャ(ARM64)、Node.js 22+、Python3 |
| 2 | ディレクトリ | /opt/ai-secretary 配下の構成 |
| 3 | setup.sh | 構文チェック、ポート整合性、パッケージ名 |
| 4 | systemd | サービスファイルの存在とポート参照 |
| 5 | VOICEVOX | API (/speakers, /audio_query, /synthesis) |
| 6 | OpenClaw | /v1/chat/completions エンドポイント |
| 7 | voice-bridge | 改修モジュールの存在と構文チェック |
| 8 | 設定ファイル | ポートや有効化フラグの一貫性 |
| 9 | ツール | 秘書ツールファイルの存在 |

```bash
# コンテナ内から
bash /mnt/pi-secretary-setup/test-env/run-tests.sh

# ホスト側から (API テストはスキップ)
bash test-env/run-tests.sh --host
```

## 制限事項

- **音声デバイス**: Docker 内には実マイク/スピーカーがないため、
  voice-bridge の音声入出力は `MOCK_AUDIO=1` でスキップされます
- **VOICEVOX**: 実エンジンの代わりにモックサーバーが無音 WAV を返します。
  実際の音声品質テストは実機で行ってください
- **OpenClaw onboard**: 対話式ウィザードのため Docker 内では
  `docker exec -it` で入って手動実行が必要です
- **systemd**: Docker 内では使えないため supervisor で代替しています

## クリーンアップ

```bash
docker compose -f test-env/docker-compose.yml down -v
docker rmi ai-secretary-test voicevox-mock 2>/dev/null
```
