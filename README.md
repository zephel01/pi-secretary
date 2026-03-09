# ずんだもん秘書 (AI Secretary for Raspberry Pi 5)

Raspberry Pi 5 上で動作する音声 AI 秘書システムです。
ずんだもんの声で予定確認・ToDo 管理・メモ・システム監視などを音声対話で行えます。

## システム構成

```
[USB スピーカーフォン (48000Hz)]
       ↓ 音声入力
voice-bridge (main_headless.py)
  ├─ STT: faster-whisper (48000→16000Hz リサンプル→音声認識)
  ├─ Chat: OpenAI 互換 → OpenClaw Gateway
  └─ TTS: VOICEVOX → 24000→48000Hz リサンプル → スピーカー
       ↓
OpenClaw Gateway (127.0.0.1:18789)
  ├─ LLM: Ollama (ローカル) / OpenRouter / Anthropic API
  ├─ 秘書プロンプト (SOUL.md)
  ├─ メモリ / スキル
  ├─ ツール実行
  └─ Docker sandbox (危険処理の隔離)
       ↓
ollama-proxy (127.0.0.1:11435)
  ├─ システムプロンプト圧縮 (22KB → 73文字)
  ├─ tools/options フィールド除去
  └─ stream 変換
       ↓
Ollama (127.0.0.1:11434)  ← または hailo-ollama :8000 (NPU)
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
| **ollama-proxy** | システムプロンプト圧縮・フィールド除去 | 11435 |
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

デフォルトは Ollama (qwen2.5:1.5b) で、クラウドへのフォールバック構成を推奨しています。
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
│   ├── hailo-proxy.py                # ollama-proxy (プロンプト圧縮プロキシ)
│   ├── get_schedule.py               # 予定取得ツール
│   ├── get_todos.py                  # ToDo 取得ツール
│   ├── add_note.py                   # メモ追加ツール
│   └── check_status.sh              # システム状態確認ツール
├── systemd/
│   ├── voicevox.service              # VOICEVOX systemd ユニット
│   ├── voice-bridge.service          # voice-bridge systemd ユニット
│   ├── openclaw.service              # OpenClaw systemd ユニット
│   └── hailo-proxy.service           # ollama-proxy systemd ユニット
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

## パフォーマンス (Raspberry Pi 5 実測値)

Raspberry Pi 5 (8GB) + Anker PowerConf S3 + qwen2.5:1.5b での実測値です。
発話から応答音声が出るまでの合計は約 20 秒です。

| 処理 | 所要時間 | ボトルネック | 備考 |
|---|---|---|---|
| STT (faster-whisper small) | 約 9 秒 | **CPU** | int8 量子化済み。tiny なら約 3 秒だが精度低下 |
| LLM (qwen2.5:1.5b CPU) | 約 3 秒 | CPU | ollama-proxy でプロンプトを 22KB→73 文字に圧縮 |
| TTS (VOICEVOX) | 約 7 秒 | CPU | ARM64 版、cpu_num_threads=2 |
| リサンプル | < 0.1 秒 | — | scipy.signal.resample_poly 使用 |

### 高速化の選択肢

STT が全体の約 45% を占める最大のボトルネックです。

- **STT モデルサイズ変更** — `base` (精度○・**1.8秒** ← 推奨) / `small` (精度◎・9秒) / `tiny` (精度△・0.8秒) を `voice-bridge.env` の `STT_MODEL_SIZE` で切り替え可能。faster-whisper (CTranslate2, int8) を使用
- **Hailo-10H NPU (実験的)** — whisper-tiny を NPU で実行すると **0.5秒** だが日本語精度が低い。英語なら実用可。`tools/hailo-whisper-test.py` で動作確認可能
- **Hailo-10H NPU (LLM)** — Ollama 互換の hailo-ollama でLLM推論を NPU にオフロード可能。hailo-proxy.py のターゲットを `:8000` に変更するだけで切り替え可能
- **クラウド LLM** — OpenRouter / Anthropic API を使えば LLM 応答は 1 秒以下に短縮
- **VOICEVOX GPU** — CUDA 対応 GPU があれば TTS も大幅に高速化（Pi 5 では非対応）

### 使い分けガイド — 対話モード vs 通知・報告モード

STT が全体の約 45% を占めるため、用途に応じてモードを使い分けることで体感速度が大きく変わります。

| モード | 処理 | 所要時間 | 用途例 |
|---|---|---|---|
| **対話モード** | STT → LLM → TTS | 約 20 秒 | 音声で質問・命令 |
| **通知・報告モード** | LLM → TTS のみ | **約 10 秒** | 予定読み上げ・調査結果報告・定時通知 |

通知・報告モードは STT を使わず、スクリプトやスケジューラから直接 OpenClaw API を叩いて結果を VOICEVOX で読み上げます。STT の 9 秒をスキップできるため、実用的な速度で動作します。

```bash
# 通知モードの例: 今日の予定をずんだもんに読み上げてもらう
RESPONSE=$(curl -s http://127.0.0.1:18789/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"openclaw","messages":[{"role":"user","content":"今日の予定を教えて"}]}' \
  | jq -r '.choices[0].message.content')

curl -s "http://127.0.0.1:50021/audio_query?text=${RESPONSE}&speaker=3" | \
  curl -s -H 'Content-Type:application/json' -d @- \
  'http://127.0.0.1:50021/synthesis?speaker=3' > /tmp/notify.wav
aplay /tmp/notify.wav
```

cron と組み合わせれば「毎朝 8 時にずんだもんが今日の予定を読み上げる」といった使い方が可能です。

### 既知の制限

- **サンプルレート** — USB スピーカーフォンは 48000Hz のみ対応のものが多い（Anker PowerConf S3 等）。faster-whisper は 16000Hz、VOICEVOX は 24000Hz のため、voice-bridge 内でリサンプルが必要。`_resample()` (scipy) で自動変換している
- **OpenClaw システムプロンプト** — OpenClaw は AGENTS.md + SOUL.md 等を含む約 22KB のシステムプロンプトを送信する。1.5B パラメータの小型モデルではコンテキストを圧迫するため、ollama-proxy で 73 文字のずんだもんプロンプトに圧縮している
- **tools フィールド** — OpenClaw は Ollama に `tools`（関数呼び出し定義）を送るが、小型モデルは非対応。ollama-proxy で除去している
- **STT 誤認識** — faster-whisper small モデルでも短い発話や滑舌が悪い場合は誤認識が起きる。`initial_prompt` でヒントを与えて改善しているが完全ではない

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
