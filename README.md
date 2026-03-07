# ずんだもん秘書 (AI Secretary for Raspberry Pi 5)

Raspberry Pi 5 上で動作する音声 AI 秘書システムです。
ずんだもんの声で予定確認・ToDo 管理・メモ・システム監視などを音声対話で行えます。

## システム構成

```
[USB スピーカーフォン]
       ↓ 音声入力
voice-bridge (main_headless.py)
  ├─ STT: faster-whisper (音声→テキスト)
  ├─ Chat: OpenAI 互換 → OpenClaw Gateway
  └─ TTS: VOICEVOX (テキスト→音声)
       ↓
OpenClaw Gateway (127.0.0.1:18789)
  ├─ LLM: Ollama (ローカル) / OpenRouter / Anthropic API
  ├─ 秘書プロンプト (SOUL.md)
  ├─ メモリ / スキル
  ├─ ツール実行
  └─ Docker sandbox (危険処理の隔離)
       ↓
VOICEVOX Engine (127.0.0.1:50021)
       ↓
ずんだもん音声 → [スピーカー]
```

## コンポーネント

| コンポーネント | 役割 | ポート |
|---|---|---|
| **voice-bridge** | 音声入力 (STT)・AI チャット・音声出力 (TTS) | — |
| **OpenClaw Gateway** | 秘書ロジック・ツール実行・メモリ管理 | 18789 |
| **Ollama** | ローカル LLM ランタイム (コスト 0・オフライン可) | 11434 |
| **VOICEVOX Engine** | ずんだもん音声合成 (speaker_id=3) | 50021 |
| **Docker sandbox** | 危険コマンドの隔離実行 (ネットワーク遮断) | — |

## LLM プロバイダ

マルチプロバイダ対応。用途に応じて切り替え可能です。

| プロバイダ | コスト | 品質 | オフライン |
|---|---|---|---|
| **Ollama (ローカル)** | 無料 | △〜○ | ○ |
| **OpenRouter** | 従量課金 | ◎ | × |
| **Anthropic API** | 従量課金 | ◎ | × |

デフォルトは Ollama (qwen2.5:7b) で、クラウドへのフォールバック構成を推奨しています。
詳細はセットアップ手順書の第 4 章を参照してください。

> **注意**: 2026年1月以降、Anthropic は OAuth トークンの第三者ツール利用を禁止しています。
> Anthropic を使う場合は API キー（従量課金）が必要です。

## ディレクトリ構成

```
pi-secretary/
├── setup.sh                          # 自動セットアップスクリプト (9 ステップ)
├── AI秘書システム_セットアップ手順書.md  # 詳細手順書 (全 10 章)
├── README.md
├── config/
│   ├── voice-bridge.env              # voice-bridge 環境変数
│   ├── openclaw.json                 # OpenClaw 設定 (マルチプロバイダ対応)
│   ├── gateway.json                  # Gateway 設定
│   ├── openclaw-secretary-prompt.md  # ずんだもん秘書プロンプト (SOUL.md)
│   ├── secretary_prompt.txt          # voice-bridge 用プロンプト
│   └── mic_input_linux.py            # ALSA マイク入力モジュール
├── voice-bridge-mods/
│   ├── main_headless.py              # Pi 向けヘッドレスエントリポイント
│   ├── ai_chat.py                    # OpenClaw 連携チャットモジュール
│   ├── mic_capture_linux.py          # Linux 向けマイクキャプチャ
│   └── requirements-pi.txt           # Pi 向け Python 依存パッケージ
├── tools/
│   ├── get_schedule.py               # 予定取得ツール
│   ├── get_todos.py                  # ToDo 取得ツール
│   ├── add_note.py                   # メモ追加ツール
│   └── check_status.sh              # システム状態確認ツール
├── systemd/
│   ├── voicevox.service              # VOICEVOX systemd ユニット
│   ├── voice-bridge.service          # voice-bridge systemd ユニット
│   └── openclaw.service              # OpenClaw systemd ユニット
├── docker/
│   └── docker-compose.yml            # sandbox 用 Docker Compose
├── docs/
│   ├── network-diagram.svg           # ネットワーク構成図
│   └── network-zunda.png             # システム構成イメージ
└── test-env/                         # macOS Docker テスト環境
    ├── docker-compose.yml
    ├── Dockerfile
    ├── Dockerfile.voicevox-mock
    ├── entrypoint.sh
    ├── run-tests.sh                  # 自動テスト (42 項目)
    ├── voicevox_mock.py              # VOICEVOX モックサーバー
    ├── README.md
    └── supervisor/
        ├── ai-secretary.conf
        ├── start-voicevox-mock.sh
        ├── start-openclaw.sh
        └── start-voice-bridge.sh
```

## 前提条件

**ハードウェア**
- Raspberry Pi 5 (8GB 推奨)
- microSD 32GB 以上
- USB スピーカーフォン (マイク + スピーカー)

**ソフトウェア**
- Raspberry Pi OS (64-bit / Bookworm)
- Node.js 22+
- Python 3.11+
- Docker

## クイックスタート

```bash
# 1. リポジトリをクローン
git clone https://github.com/zephel01/pi-secretary.git
cd pi-secretary

# 2. セットアップ実行 (root 権限)
# Ollama + ローカル LLM のみ (無料)
sudo bash setup.sh

# OpenRouter を使う場合
sudo OPENROUTER_API_KEY="sk-or-..." bash setup.sh

# Anthropic API を使う場合
sudo ANTHROPIC_API_KEY="sk-ant-..." bash setup.sh

# 3. サービス起動
sudo systemctl start ollama
sudo systemctl start voicevox
sudo systemctl start openclaw
sudo systemctl start voice-bridge

# 4. 動作テスト — VOICEVOX
curl -s 'http://127.0.0.1:50021/audio_query?text=テストなのだ&speaker=3' | \
  curl -s -H 'Content-Type:application/json' -d @- \
  'http://127.0.0.1:50021/synthesis?speaker=3' > /tmp/test.wav
aplay /tmp/test.wav
```

## setup.sh の処理内容

`setup.sh` は以下の 9 ステップを自動実行します:

1. **システムパッケージ** — apt パッケージ + Node.js 22 のインストール
2. **専用ユーザー** — `aiagent` ユーザーの作成
3. **ディレクトリ構成** — `/opt/ai-secretary/` 配下のディレクトリ作成
4. **OpenClaw** — CLI インストール + 非対話モードで初期設定
5. **voice-bridge** — GitHub からクローン + Pi 向け改修ファイル配置
6. **VOICEVOX Engine** — ARM64 バイナリのダウンロードと展開
7. **Ollama** — ローカル LLM ランタイム + 推奨モデル (qwen2.5:7b) のダウンロード
8. **ツール群 & Docker sandbox** — 秘書ツール配置 + sandbox イメージ取得
9. **systemd サービス** — サービスファイル配置と有効化

## 外部アクセス (Cloudflare Tunnel)

外出先から秘書にアクセスする場合は Cloudflare Tunnel (`cloudflared`) を使用できます。
セットアップ手順書の第 8 章を参照してください。

> **注意**: VOICEVOX Engine は認証機能がないため、外部に公開しないでください。
> OpenClaw Gateway のみを Cloudflare Zero Trust (Access) 経由で公開します。

## macOS テスト環境

Apple Silicon Mac 上で Docker を使い、Pi 環境を模擬してテストできます。
詳細は [`test-env/README.md`](test-env/README.md) を参照してください。

```bash
# テスト環境起動
docker compose -f test-env/docker-compose.yml up -d pi-secretary
docker exec -it ai-secretary-test bash

# セットアップ実行
cd /mnt/pi-secretary-setup && bash setup.sh

# 自動テスト (42 項目)
bash /mnt/pi-secretary-setup/test-env/run-tests.sh
```

## セキュリティ

- すべての内部通信は `127.0.0.1` (localhost) 内で完結
- VOICEVOX は認証なし → **ローカル専用、外部公開禁止**
- Docker sandbox はネットワーク完全遮断 (`network_mode: none`)
- 外部アクセスは Cloudflare Tunnel + Zero Trust で認証保護

## ドキュメント

- [セットアップ手順書](AI秘書システム_セットアップ手順書.md) — 全 10 章の詳細ガイド
- [テスト環境 README](test-env/README.md) — macOS Docker テスト環境の使い方
- [ネットワーク構成図](docs/network-diagram.svg) — 内部接続と外部アクセス経路

## ライセンス

各コンポーネントのライセンスに従います:
- [OpenClaw](https://github.com/openclaw/openclaw) — 公式ライセンス参照
- [VOICEVOX Engine](https://github.com/VOICEVOX/voicevox_engine) — LGPL v3
- [voice-bridge](https://github.com/zephel01/voice-bridge) — リポジトリ参照
- [Ollama](https://github.com/ollama/ollama) — MIT License
