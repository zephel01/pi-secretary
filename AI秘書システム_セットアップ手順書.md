# AI秘書システム セットアップ手順書

**Raspberry Pi 5 + voice-bridge + OpenClaw + VOICEVOX**

2026年3月

---

## 目次

1. [システム概要](#1-システム概要)
2. [前提条件](#2-前提条件)
3. [セットアップ手順 (8ステップ)](#3-セットアップ手順)
4. [設定ファイルリファレンス](#4-設定ファイルリファレンス)
5. [サービス起動と動作確認](#5-サービス起動と動作確認)
6. [セキュリティガイドライン](#6-セキュリティガイドライン)
7. [Cloudflare Tunnel による外部アクセス](#7-cloudflare-tunnel-による外部アクセス)
8. [トラブルシューティング](#8-トラブルシューティング)
9. [macOS テスト環境](#9-macos-テスト環境)

---

## 1. システム概要

本ドキュメントは、Raspberry Pi 5 上に「ずんだもん秘書」音声AIシステムを構築するための完全手順書です。

### 1.1 完成構成図

```
[USBスピーカーフォン]
   ↓
voice-bridge (main_headless.py)
   ├─ STT (faster-whisper)
   ├─ OpenAI互換 chat client → OpenClaw
   └─ TTS 呼び出し → VOICEVOX
   ↓
OpenClaw Gateway (127.0.0.1:18789)
   ├─ 秘書プロンプト (SOUL.md)
   ├─ メモリ / スキル
   ├─ ツール実行
   └─ 危険処理 → Docker sandbox
   ↓
VOICEVOX Engine (127.0.0.1:50021)
   ↓
ずんだもん音声 → [スピーカー]
```

### 1.2 各コンポーネントの役割

| コンポーネント | 役割 | ポート |
|---|---|---|
| voice-bridge | 耳と口。マイク入力、STT、TTS制御、OpenAI互換 API送信 | — |
| OpenClaw Gateway | 秘書ロジック。ツール実行、メモリ、チャネル統合 | 18789 |
| VOICEVOX Engine | 日本語キャラ音声合成。ずんだもん (speaker_id=3) | 50021 |
| Docker sandbox | 危険処理の隔離実行。ネットワーク完全遮断 | — |

### 1.3 データフロー

すべての通信は 127.0.0.1 (localhost) 内で完結します。

- **音声入力**: USBマイク → sounddevice (ALSA) → faster-whisper (STT) → テキスト
- **AI応答**: テキスト → OpenClaw /v1/chat/completions (:18789) → 秘書応答
- **TTS**: 応答テキスト → VOICEVOX /audio_query + /synthesis (:50021) → WAV → スピーカー再生

### 1.4 インストール元

| コンポーネント | 取得方法 | ソース |
|---|---|---|
| OpenClaw | 公式スクリプト or npm | `curl -fsSL https://openclaw.ai/install.sh \| bash` or `npm install -g openclaw@latest` |
| voice-bridge | git clone | `https://github.com/zephel01/voice-bridge` |
| VOICEVOX Engine | GitHub Releases から ARM64 バイナリ | `https://github.com/VOICEVOX/voicevox_engine/releases` |

---

## 2. 前提条件

### 2.1 ハードウェア

- Raspberry Pi 5 (8GB 推奨)
- microSD 32GB 以上 (64GB 推奨)
- USB スピーカーフォン (マイク・スピーカー一体型)
- 有線 LAN または Wi-Fi 接続
- 電源アダプター (USB-C 5V/5A 推奨)

### 2.2 ソフトウェア

- Raspberry Pi OS Lite 64-bit (ヘッドレス運用)
- SSH 有効化済み
- Node.js 22 以上 (OpenClaw 要件)
- Python 3.9 以上
- インターネット接続 (初回セットアップ時)

### 2.3 メモリ配分目安

| コンポーネント | 上限 | 備考 |
|---|---|---|
| VOICEVOX Engine | 2 GB | モデルロード時に最大 |
| voice-bridge (STT) | 2 GB | faster-whisper small モデル |
| OpenClaw Gateway | 1 GB | Node.js ランタイム |
| Docker sandbox | 512 MB | ツール実行時のみ |
| OS + その他 | 約2.5 GB | — |

> **注意**: 合計約8GB。Pi 5 (8GBモデル) での運用を推奨します。

---

## 3. セットアップ手順

セットアップスクリプト (setup.sh) で自動化されていますが、各ステップの内容を以下に説明します。

### 3.1 ステップ 1: OS 準備

Raspberry Pi Imager で Raspberry Pi OS Lite 64-bit を microSD に書き込みます。

1. Raspberry Pi Imager を開き、OS に「Raspberry Pi OS Lite (64-bit)」を選択
2. 歯車アイコンから設定画面を開き、SSH を有効化、ユーザー名/パスワード、Wi-Fi を設定
3. microSD に書き込み、Pi に挿入して起動
4. SSH で接続し、システムを更新

```bash
ssh pi@raspberrypi.local
sudo apt update && sudo apt upgrade -y
```

### 3.2 ステップ 2: セットアップファイルの転送

pi-secretary-setup フォルダを Pi に転送します。

```bash
scp -r pi-secretary-setup/ pi@raspberrypi.local:~/
```

### 3.3 ステップ 3: マスタースクリプト実行

setup.sh が 8 つのサブステップを自動実行します。

```bash
cd ~/pi-secretary-setup
sudo bash setup.sh
```

スクリプトが実行する内容:

| ステップ | 内容 | 説明 |
|---|---|---|
| 1/8 | システムパッケージ | git, Python3, Docker, **Node.js 22+**, ALSA ツール等 |
| 2/8 | 専用ユーザー | aiagent ユーザー作成、docker グループ追加 |
| 3/8 | ディレクトリ | /opt/ai-secretary/ 以下に全ディレクトリ作成 |
| 4/8 | OpenClaw | **公式スクリプト or npm でインストール → `openclaw onboard --non-interactive`** |
| 5/8 | voice-bridge | **GitHub から git clone**、秘書化モジュール配置、venv 作成、.env 配置 |
| 6/8 | VOICEVOX | ARM64 バイナリダウンロード・展開 |
| 7/8 | ツール群 | 秘書ツール 4種配置、Docker sandbox イメージ取得 |
| 8/8 | systemd | 3サービスファイル配置、有効化 (Docker 環境ではスキップ) |

### 3.4 ステップ 4: OpenClaw 初期設定

setup.sh は `openclaw onboard` を **非対話モード** (`--non-interactive`) で実行します。

#### A. API キーを環境変数で渡す場合 (推奨)

```bash
# セットアップ実行時に API キーを渡す
ANTHROPIC_API_KEY=sk-ant-xxxxx sudo -E bash setup.sh
```

setup.sh は `ANTHROPIC_API_KEY` を検出し、`--flow quickstart` で自動設定します。対話操作は不要です。

#### B. API キーなしで実行する場合

API キーを用意していない場合でも setup.sh は `--auth-choice skip` で初期化を完了します。後から API キーを設定してください:

```bash
sudo -u aiagent openclaw config set agent.apiKey <YOUR_KEY>
```

#### C. 対話式ウィザードで設定する場合

手動でフル設定を行いたい場合は、setup.sh とは別に対話式ウィザードを起動できます:

```bash
sudo -u aiagent openclaw onboard --install-daemon
```

以下を対話で設定します: API プロバイダー選択 (Anthropic 推奨)、API キー入力、モデル選択、認証トークン、チャネル設定 (Telegram/Discord、後から追加可能)。

#### 使用される onboard オプション

| オプション | 用途 |
|---|---|
| `--non-interactive` | 対話プロンプトをスキップ |
| `--flow quickstart` | 最小構成で自動設定 (トークン自動生成) |
| `--yes` | セキュリティ確認を自動承諾 |
| `--install-daemon` | systemd サービスを自動作成 |
| `--gateway-port 18789` | Gateway ポートを明示指定 |
| `--auth-choice skip` | API キーなし時に認証をスキップ |

### 3.5 ステップ 5: chatCompletions エンドポイント有効化

voice-bridge が OpenClaw と通信するために、OpenAI 互換 API エンドポイントを有効化する必要があります。setup.sh が自動で行いますが、手動の場合:

```bash
# ~/.openclaw/openclaw.json を編集
sudo -u aiagent nano /home/aiagent/.openclaw/openclaw.json
```

以下の設定を追加/確認:

```json
{
  "gateway": {
    "http": {
      "endpoints": {
        "chatCompletions": {
          "enabled": true
        }
      }
    }
  }
}
```

### 3.6 ステップ 6: voice-bridge のインストール

setup.sh が自動的に以下を実行します:

```bash
# GitHub からクローン
git clone https://github.com/zephel01/voice-bridge.git /opt/ai-secretary/voice-bridge

# 秘書化モジュールを配置 (voice-bridge-mods/ から)
cp voice-bridge-mods/ai_chat.py /opt/ai-secretary/voice-bridge/
cp voice-bridge-mods/mic_capture_linux.py /opt/ai-secretary/voice-bridge/
cp voice-bridge-mods/main_headless.py /opt/ai-secretary/voice-bridge/

# Python venv 作成 & 依存インストール
python3 -m venv /opt/ai-secretary/voice-bridge/venv
source /opt/ai-secretary/voice-bridge/venv/bin/activate
pip install -r requirements-pi.txt
```

元の voice-bridge は音声翻訳アプリなので、秘書用の3つの新規ファイル (ai_chat.py, mic_capture_linux.py, main_headless.py) がエントリポイントや処理ロジックを置き換えます。改修の詳細は別ドキュメント `CHANGES_NEEDED.md` を参照してください。

> **注意**: VOICEVOX ARM64 バイナリの自動ダウンロードは失敗する場合があります。その場合は [GitHub Releases](https://github.com/VOICEVOX/voicevox_engine/releases) から手動で取得してください。

---

## 4. 設定ファイルリファレンス

### 4.1 voice-bridge.env

ファイルパス: `/opt/ai-secretary/voice-bridge/.env`

| 変数名 | 値 | 説明 |
|---|---|---|
| OPENAI_API_BASE | `http://127.0.0.1:18789/v1` | OpenClaw のエンドポイント (ポート18789) |
| OPENAI_API_KEY | `YOUR_OPENCLAW_TOKEN_HERE` | openclaw onboard で設定した認証トークン |
| OPENAI_MODEL | `openclaw` | `openclaw` でデフォルトエージェント |
| STT_MODEL_SIZE | `small` | tiny / base / small から選択 |
| VOICEVOX_SPEAKER_ID | `3` | ずんだもん ノーマル |
| WAKE_WORD | `ずんだもん` | 空欄で常時リスニング |
| SILENCE_TIMEOUT | `2.0` | 無音判定 (秒) |
| AUDIO_INPUT_DEVICE | (空欄) | arecord -l で確認して設定 |
| AUDIO_OUTPUT_DEVICE | (空欄) | aplay -l で確認して設定 |

### 4.2 openclaw.json

ファイルパス: `/home/aiagent/.openclaw/openclaw.json`

`openclaw onboard` で自動生成されます。追加で必要な設定:

```json
{
  "gateway": {
    "http": {
      "endpoints": {
        "chatCompletions": { "enabled": true }
      }
    }
  },
  "agent": {
    "model": "anthropic/claude-sonnet-4-5-20250514"
  }
}
```

- **chatCompletions.enabled**: `true` 必須。voice-bridge からの通信に必要
- **agent.model**: 使用する LLM モデル名

### 4.3 OpenClaw ワークスペース

ファイルパス: `/home/aiagent/.openclaw/workspace/`

| ファイル | 役割 |
|---|---|
| SOUL.md | ずんだもん秘書の人格・口調・ルール |
| AGENTS.md | 運用ルール、委任、メモリワークフロー |
| USER.md | ユーザー情報、好み |
| TOOLS.md | ローカルツールの使い方 |
| MEMORY.md | 長期記憶 |

setup.sh がずんだもん秘書プロンプトを `SOUL.md` に自動配置します。

### 4.4 systemd サービスファイル

| サービス | ファイル | 主な設定 |
|---|---|---|
| openclaw | openclaw.service | MemoryMax=1G, ポート18789, ~/.openclaw に読み書き |
| voicevox | voicevox.service | MemoryMax=2G, cpu_num_threads=2, TimeoutStart=120s |
| voice-bridge | voice-bridge.service | MemoryMax=2G, **main_headless.py** を起動, ALSA DeviceAllow |

> **注意**: openclaw.service は `openclaw onboard --install-daemon` で自動生成される場合があります。その場合は自動生成版が優先されます。

### 4.5 Docker sandbox (docker-compose.yml)

ファイルパス: `/opt/ai-secretary/sandbox/docker-compose.yml`

セキュリティ設定:

- `network_mode: none` — ネットワーク完全遮断
- `read_only: true` — ファイルシステム読み取り専用
- `cap_drop: ALL` — 全権限削除
- `user: 1000:1000` — 非 root 実行
- リソース制限: CPU 1コア、メモリ 512MB

---

## 5. サービス起動と動作確認

### 5.1 起動順序

依存関係があるため、以下の順序で起動してください。

```bash
# 1. VOICEVOX Engine を起動
sudo systemctl start voicevox

# 2. OpenClaw Gateway を起動
sudo systemctl start openclaw

# 3. voice-bridge を起動
sudo systemctl start voice-bridge
```

### 5.2 動作確認

#### VOICEVOX ヘルスチェック

```bash
curl -s http://127.0.0.1:50021/version
```

バージョン番号が返れば OK です。

#### 音声合成テスト

```bash
curl -s 'http://127.0.0.1:50021/audio_query?text=テストなのだ&speaker=3' | \
  curl -s -H 'Content-Type:application/json' \
  -d @- 'http://127.0.0.1:50021/synthesis?speaker=3' > /tmp/test.wav
aplay /tmp/test.wav
```

ずんだもんの声で「テストなのだ」と再生されれば成功です。

#### OpenClaw API テスト

```bash
curl -s http://127.0.0.1:18789/v1/chat/completions \
  -H 'Authorization: Bearer YOUR_TOKEN' \
  -H 'Content-Type: application/json' \
  -d '{"model":"openclaw","messages":[{"role":"user","content":"今日の予定は？"}]}'
```

JSON レスポンスが返れば OK です。

#### ログ確認

```bash
journalctl -u voicevox -f
journalctl -u openclaw -f
journalctl -u voice-bridge -f
```

### 5.3 Docker sandbox 起動

```bash
cd /opt/ai-secretary/sandbox
docker compose up -d

# 確認
docker ps --format '{{.Names}} {{.Status}}'
```

---

## 6. セキュリティガイドライン

> **警告**: OpenClaw は最近 ClawJacked 脆弱性が報じられました。必ず 2026.2.25 以降に更新してください。

### 6.1 必須対策

1. Gateway は loopback (127.0.0.1) でのみバインド
2. LAN に公開しない
3. 外部アクセスは SSH トンネル、Tailscale、または Cloudflare Tunnel 経由のみ (→ [第7章](#7-cloudflare-tunnel-による外部アクセス))
4. OpenClaw は専用 OS ユーザー (aiagent) で実行
5. 危険処理は必ず Docker sandbox 経由
6. 定期的にアップデートを実施

### 6.2 systemd セキュリティ設定

各サービスに以下が適用済みです:

- `NoNewPrivileges=true` — 権限昇格禁止
- `ProtectSystem=strict` — システムファイル保護
- `MemoryMax` / `CPUQuota` — リソース制限

> **注意**: openclaw.service は `~/.openclaw` に読み書きするため ProtectHome=true は設定していません。

### 6.3 Docker sandbox 原則

- `network_mode: none` — ネットワーク完全遮断
- `read_only: true` — ファイルシステム読み取り専用
- `cap_drop: ALL` — 全権限削除
- tmpfs で一時書き込み領域を提供 (64MB)
- ワークディレクトリのみ bind mount (rw)

---

## 7. Cloudflare Tunnel による外部アクセス

Pi 上の OpenClaw に外部 (スマホ、別 PC) からアクセスする場合、Cloudflare Tunnel (cloudflared) を使うとポート開放なしでセキュアに公開できます。

> **注意**: VOICEVOX Engine はローカル専用 (127.0.0.1:50021) です。認証機能がなく、外部公開すると音声合成 API を無制限に叩かれ Pi のリソースを食い潰されるリスクがあるため、トンネルには含めません。

### 7.1 前提条件

- Cloudflare アカウント (無料プラン可)
- Cloudflare に登録済みのドメイン (例: `yourdomain.com`)
- Pi がインターネットに接続されていること

### 7.2 cloudflared インストール

```bash
# ARM64 用 deb パッケージをダウンロード
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64.deb \
  -o /tmp/cloudflared.deb
sudo dpkg -i /tmp/cloudflared.deb

# バージョン確認
cloudflared --version
```

### 7.3 Cloudflare にログイン

```bash
cloudflared tunnel login
```

ブラウザが開き (SSH 経由の場合は表示される URL をコピー)、Cloudflare アカウントと連携します。認証後、証明書が `~/.cloudflared/` に保存されます。

### 7.4 トンネル作成

```bash
# トンネルを作成
cloudflared tunnel create ai-secretary

# 作成されたトンネル ID を確認
cloudflared tunnel list
```

トンネル ID (UUID) とクレデンシャルファイルが `~/.cloudflared/<TUNNEL_ID>.json` に生成されます。

### 7.5 設定ファイル作成

`~/.cloudflared/config.yml` を作成します:

```yaml
tunnel: ai-secretary
credentials-file: /home/aiagent/.cloudflared/<TUNNEL_ID>.json

ingress:
  # OpenClaw Gateway — メインのアクセスポイント
  - hostname: openclaw.yourdomain.com
    service: http://127.0.0.1:18789
    originRequest:
      noTLSVerify: true

  # キャッチオール (必須)
  - service: http_status:404
```

> **注意**: `<TUNNEL_ID>` は `cloudflared tunnel list` で表示される UUID に置き換えてください。

### 7.6 DNS ルーティング

```bash
# Cloudflare DNS に CNAME を自動登録
cloudflared tunnel route dns ai-secretary openclaw.yourdomain.com
```

### 7.7 起動テスト

```bash
# フォアグラウンドでテスト起動
cloudflared tunnel run ai-secretary
```

別端末から確認:

```bash
curl -s https://openclaw.yourdomain.com/v1/chat/completions \
  -H 'Authorization: Bearer YOUR_TOKEN' \
  -H 'Content-Type: application/json' \
  -d '{"model":"openclaw","messages":[{"role":"user","content":"テスト"}]}'
```

### 7.8 systemd で自動起動

```bash
# cloudflared をシステムサービスとしてインストール
sudo cloudflared service install
sudo systemctl enable cloudflared
sudo systemctl start cloudflared

# 状態確認
sudo systemctl status cloudflared
```

### 7.9 Cloudflare Zero Trust によるアクセス制御 (必須)

> **警告**: トンネルだけでは URL を知っている人は誰でも OpenClaw にアクセスできます。必ず Zero Trust でアクセス制限をかけてください。

1. [Cloudflare Zero Trust ダッシュボード](https://one.dash.cloudflare.com/) を開く
2. **Access → Applications → Add an application** を選択
3. **Self-hosted** を選び、`openclaw.yourdomain.com` を指定
4. ポリシーを設定:

| 方式 | 設定例 | 適用場面 |
|---|---|---|
| メール OTP | 自分のメールアドレスのみ許可 | 個人利用 |
| GitHub OAuth | 特定の GitHub アカウントのみ | 開発者向け |
| Google SSO | Google Workspace ドメイン制限 | チーム利用 |
| IP アドレス | 自宅/VPN の IP のみ | 固定 IP がある場合 |

5. **Save** で保存

これで認証を通過した人だけが OpenClaw にアクセスできます。

### 7.10 リモートから秘書を呼ぶ

外部のクライアントから利用する場合、voice-bridge の `.env` を変更します:

```bash
# ローカル (Pi 上の voice-bridge) → 変更不要
OPENAI_API_BASE=http://127.0.0.1:18789/v1

# リモート (別 PC/スマホの voice-bridge) → トンネル URL に変更
OPENAI_API_BASE=https://openclaw.yourdomain.com/v1
```

Pi 上の voice-bridge は引き続き localhost 直通で動作するため、トンネルの有無に影響されません。

### 7.11 トラブルシューティング

| 症状 | 原因 | 対処 |
|---|---|---|
| `cloudflared tunnel run` がエラー | クレデンシャルファイルのパスが間違い | `cloudflared tunnel list` で ID 確認、config.yml を修正 |
| DNS が解決しない | CNAME 未登録 | `cloudflared tunnel route dns` を再実行 |
| 502 Bad Gateway | OpenClaw が起動していない | `systemctl status openclaw` で確認 |
| 403 Forbidden | Zero Trust ポリシーでブロック | Cloudflare ダッシュボードでポリシーを確認 |
| 接続が遅い | Cloudflare エッジが遠い | 通常は数十 ms の遅延。リアルタイム音声には影響あり |

> **重要**: リアルタイム音声通話はレイテンシの影響を受けるため、Cloudflare Tunnel 経由の voice-bridge はテキスト API としての利用を推奨します。音声 I/O は Pi ローカルで行い、テキストベースの指示だけを外部から送る構成が最適です。

---

## 8. トラブルシューティング

| 症状 | 原因 | 対処 |
|---|---|---|
| VOICEVOX が起動しない | ARM64 バイナリ未配置またはメモリ不足 | GitHub Releases から手動ダウンロード。MemoryMax 確認 |
| STT が遅い/認識精度が低い | モデルサイズが大きすぎる | STT_MODEL_SIZE=base に下げる |
| マイクが認識されない | USB デバイス未検出 | `arecord -l` でデバイス確認。AUDIO_INPUT_DEVICE 設定 |
| OpenClaw に接続できない | ポート競合、未起動、chatCompletions 無効 | `systemctl status openclaw` で確認。openclaw.json で chatCompletions.enabled=true を確認 |
| 認証エラー (401) | voice-bridge.env のトークンが不一致 | OPENAI_API_KEY を openclaw onboard で設定した値に合わせる |
| 音声が出ない | ALSA デバイス設定不備 | `aplay -l` で出力デバイス確認。`speaker-test -t wav` |
| サービスがクラッシュループ | メモリ不足 | `free -h` で確認。不要サービス停止または MemoryMax 調整 |
| openclaw onboard が失敗 | Node.js バージョン不足 | `node -v` で 22 以上を確認 |
| systemd 関連エラー (Docker) | Docker コンテナ内には systemd がない | 想定通り。setup.sh は自動スキップし警告を表示 |

### 8.1 デバッグコマンド集

```bash
# サービス状態
systemctl status openclaw voicevox voice-bridge

# Node.js バージョン確認 (22 以上が必要)
node -v

# オーディオデバイス
arecord -l    # 入力デバイス一覧
aplay -l      # 出力デバイス一覧

# メモリ確認
free -h

# CPU温度
vcgencmd measure_temp

# Docker sandbox 状態
docker ps --format '{{.Names}} {{.Status}}'

# OpenClaw ログ
journalctl -u openclaw --since "5 min ago"

# OpenClaw 設定確認
cat /home/aiagent/.openclaw/openclaw.json
```

### 8.2 ディレクトリ構成

```
/opt/ai-secretary/
├─ openclaw/
│  ├─ memory/ (notes.json, todos.json, schedule.json, profile.json)
│  └─ tools/ (get_schedule.py, get_todos.py, add_note.py, check_status.sh)
├─ voice-bridge/          ← git clone https://github.com/zephel01/voice-bridge
│  ├─ main_headless.py    ← 秘書用エントリポイント (新規)
│  ├─ ai_chat.py          ← OpenClaw 通信 (新規)
│  ├─ mic_capture_linux.py ← USBマイク入力 (新規)
│  ├─ transcriber.py      ← STT (既存そのまま)
│  ├─ tts_voicevox.py     ← VOICEVOX TTS (既存そのまま)
│  ├─ tts_engine.py       ← Edge TTS フォールバック (既存そのまま)
│  ├─ .env
│  ├─ venv/
│  ├─ custom/secretary_prompt.txt
│  └─ logs/
├─ voicevox/engine/
└─ sandbox/workdir/

/home/aiagent/.openclaw/   ← OpenClaw のホームディレクトリ
├─ openclaw.json           ← メイン設定
└─ workspace/
   ├─ SOUL.md              ← ずんだもん秘書の人格プロンプト
   ├─ AGENTS.md
   ├─ USER.md
   ├─ TOOLS.md
   └─ MEMORY.md
```

---

## 9. macOS テスト環境

実機の Raspberry Pi にデプロイする前に、macOS (Apple Silicon) 上で Docker を使って動作検証ができます。

### 9.1 前提条件

- macOS (Apple Silicon / M系)
- Docker Desktop for Mac インストール済み
- `ANTHROPIC_API_KEY` (OpenClaw API テストを行う場合のみ)

### 9.2 テスト環境の構成

`test-env/` ディレクトリに以下のファイルがあります:

| ファイル | 役割 |
|---|---|
| Dockerfile | ARM64 Debian bookworm-slim ベースの Pi 5 模擬環境 |
| docker-compose.yml | Pi 模擬コンテナ + VOICEVOX モックの統合管理 |
| voicevox_mock.py | VOICEVOX API 互換モックサーバー (無音 WAV を返す) |
| supervisor/ | systemd の代替。ラッパースクリプト経由でサービス管理 |
| run-tests.sh | 9カテゴリ・42項目の自動検証スクリプト |

Apple Silicon Docker は実際に ARM64 で動作するため、Pi 5 とほぼ同じバイナリ互換環境が得られます。

### 9.3 クイックスタート

```bash
cd pi-secretary-setup

# 1. テスト環境をビルド＆起動
docker compose -f test-env/docker-compose.yml up -d

# 2. コンテナに入る
docker exec -it ai-secretary-test bash

# 3. setup.sh を実行
cd /mnt/pi-secretary-setup && bash setup.sh

# 4. 検証テスト
bash /mnt/pi-secretary-setup/test-env/run-tests.sh
```

API キーを渡す場合:

```bash
docker exec -it -e ANTHROPIC_API_KEY=sk-ant-xxx ai-secretary-test bash
cd /mnt/pi-secretary-setup && ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY bash setup.sh
```

### 9.4 テスト結果の読み方

`run-tests.sh` は 9 カテゴリの検証を行い、PASS / FAIL / SKIP で結果を表示します。

| 結果 | 意味 |
|---|---|
| **PASS: 42, FAIL: 0** | 実機デプロイ可能な状態 |
| **SKIP: 1 (OpenClaw)** | API キー未設定のため OpenClaw API テストがスキップ。構成自体は問題なし |
| **FAIL あり** | ポート不整合、構文エラー、設定ミスなどが検出されている |

ホスト側 (macOS) から Docker 不要で静的チェックだけ行うこともできます:

```bash
bash test-env/run-tests.sh --host
```

### 9.5 制限事項

- **音声デバイス**: Docker 内にはマイク/スピーカーがないため、voice-bridge の音声入出力はテストできません (`MOCK_AUDIO=1` で起動)
- **VOICEVOX**: 実エンジンの代わりにモックサーバーが無音 WAV を返します。実際の音声品質は実機で確認してください
- **systemd**: Docker 内では使えないため setup.sh が自動スキップします。代わりに supervisor でサービスを管理します

### 9.6 クリーンアップ

```bash
docker compose -f test-env/docker-compose.yml down -v
docker rmi $(docker images -q --filter "reference=*ai-secretary*") 2>/dev/null
```
