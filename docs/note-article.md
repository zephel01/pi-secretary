# Raspberry Pi 5 で AI音声秘書を作る — VOICEVOX + COEIROINK リリンちゃん対応

## はじめに

Raspberry Pi 5 と Hailo-10H NPU を使って、音声で会話できるAI秘書を作りました。話しかけると、ずんだもんやリリンちゃんがウェブ検索して天気やニュースを教えてくれます。

この記事では、プロジェクトの全体像と「何ができるか」を無料パートで紹介し、具体的な実装手順とコードを有料パートで公開します。

## システム構成

```
[USBマイク] → [Raspberry Pi 5]
                 ├─ STT: faster-whisper (base, int8)
                 ├─ LLM: GLM-4-flash / OpenAI互換API
                 ├─ Web検索: DuckDuckGo (APIキー不要)
                 └─ TTS: VOICEVOX (Pi ローカル)
                         or COEIROINK v2 (Ubuntu PCリモート)
                              → [スピーカー再生]
```

### ハードウェア

- **Raspberry Pi 5** (8GB) — メイン処理
- **Hailo-10H NPU** (M.2 HAT+) — 将来のSTT高速化用に搭載（実験中）
- **USBスピーカーフォン** — マイク入力＆音声出力
- **Ubuntu PC (NucBox EVO X2)** — COEIROINK用リモートTTSサーバー（オプション）

### ソフトウェアスタック

| レイヤー | 技術 | 備考 |
|---------|------|------|
| STT (音声→テキスト) | faster-whisper base int8 | ~1.8秒、日本語精度良好 |
| LLM (AI応答) | GLM-4-flash | 智谱AI、無料枠あり |
| Web検索 | DuckDuckGo (ddgs) | APIキー不要 |
| TTS (テキスト→音声) | VOICEVOX / COEIROINK v2 | キャラクター切替可能 |

## できること

### 1. 音声会話

ウェイクワード（「ずんだもん」等）で起動し、自然な会話ができます。STTの誤認識に対応するファジーマッチ機能付き。

### 2. ウェブ検索連携

「明日の東京の天気」「最新のニュースを調べて」のような質問に、DuckDuckGoで検索した結果をもとに回答します。

### 3. キャラクター切り替え

`.env` ファイルの1行を変えるだけで、キャラクターの声と口調を切り替えられます。

| キャラクター | TTSエンジン | 口調 |
|-------------|-----------|------|
| ずんだもん | VOICEVOX | 〜のだ |
| 四国めたん | VOICEVOX | 〜ですわ |
| 春日部つむぎ | VOICEVOX | 〜だよ（ギャル） |
| リリンちゃん | COEIROINK | 小悪魔メスガキ |
| ノーマル | VOICEVOX | 丁寧語 |

キャラクターを変えると、LLMの応答スタイル（口調・一人称）とTTSの声が同時に切り替わります。

### 4. VOICEVOX + COEIROINK マルチTTS

Pi上のVOICEVOXだけでなく、LAN上の別PCで動くCOEIROINKの音声も使えます。COEIROINKはx86_64専用なのでPiでは動きませんが、ネットワーク越しにAPIを叩くことで利用可能にしました。

```
[Pi 5] テキスト → HTTP (LAN) → [Ubuntu PC] COEIROINK → WAV → [Pi 5] 再生
```

### 5. STT精度向上のための工夫

- **beam_size=5** — デフォルト(1)から引き上げ、認識精度向上
- **initial_prompt** — ドメイン語彙を含めてWhisperにヒントを与える
- **stt_corrections.json** — よくある誤認識パターンを自動補正
- **ファジーウェイクワード** — 濁音⇔清音の誤認識に対応

## STTベンチマーク（Raspberry Pi 5 実測値）

| エンジン | モデル | 処理時間 | 日本語精度 |
|---------|-------|---------|-----------|
| Hailo NPU | whisper-tiny | 0.5秒 | △（実用未満） |
| faster-whisper | base int8 | 1.8秒 | ○（推奨） |
| faster-whisper | small | 9秒 | ◎ |
| openai-whisper | base | 2.5秒 | ○ |

NPUは速度は圧倒的ですが、tiny モデルの日本語精度が実用レベルに達しませんでした。現時点では faster-whisper base (int8) が速度と精度のベストバランスです。

## デモ

（ここにデモ動画や音声サンプルを貼る）

---

**ここから先は、具体的なセットアップ手順・コード・設定ファイルの詳細です。**

---

## セットアップ手順

### 前提条件

- Raspberry Pi 5 (Raspberry Pi OS Bookworm 64bit)
- VOICEVOX Engine がPi上で起動済み
- Python 3.11+
- （オプション）COEIROINK用のx86_64 Linux PC

### 1. リポジトリのクローンと初期設定

```bash
cd /opt/ai-secretary
git clone https://github.com/zephel01/pi-secretary.git
cd pi-secretary
```

### 2. voice-bridge のセットアップ

```bash
# voice-bridge をクローン
git clone https://github.com/zephel01/voice-bridge.git /opt/ai-secretary/voice-bridge

# カスタムファイルを上書きコピー
cp voice-bridge-mods/main_headless.py /opt/ai-secretary/voice-bridge/
cp voice-bridge-mods/ai_chat.py /opt/ai-secretary/voice-bridge/
cp voice-bridge-mods/web_search.py /opt/ai-secretary/voice-bridge/
cp voice-bridge-mods/tts_coeiroink.py /opt/ai-secretary/voice-bridge/

# .env をコピー
cp config/voice-bridge.env /opt/ai-secretary/voice-bridge/.env
```

### 3. Python依存パッケージ

```bash
cd /opt/ai-secretary/voice-bridge
python3 -m venv venv
source venv/bin/activate

pip install faster-whisper openai requests
pip install ddgs  # DuckDuckGo検索
# pip install google-generativeai  # Gemini使う場合
```

### 4. 環境変数の設定

`/opt/ai-secretary/voice-bridge/.env` を編集：

```bash
# --- キャラクター ---
# zundamon / metan / tsumugi / lilin / normal
CHARACTER=zundamon

# --- LLM ---
LLM_BACKEND=openai
OPENAI_API_BASE=https://open.bigmodel.cn/api/paas/v4
OPENAI_API_KEY=your-glm-api-key
OPENAI_MODEL=glm-4-flash

# --- TTS ---
# voicevox (ローカル) / coeiroink (リモート)
TTS_ENGINE=voicevox
VOICEVOX_URL=http://127.0.0.1:50021

# COEIROINK (TTS_ENGINE=coeiroink の場合)
COEIROINK_URL=http://192.168.x.x:50033
COEIROINK_SPEAKER_UUID=your-speaker-uuid
COEIROINK_STYLE_ID=92

# --- STT ---
STT_MODEL_SIZE=base
STT_LANGUAGE=ja

# --- ウェイクワード ---
WAKE_WORD=ずんだもん

# --- Web検索 ---
WEB_SEARCH=on
```

### 5. systemd サービス登録

```bash
sudo tee /etc/systemd/system/voice-bridge.service << 'EOF'
[Unit]
Description=Voice Bridge AI Secretary
After=network.target

[Service]
Type=simple
User=zephel01
WorkingDirectory=/opt/ai-secretary/voice-bridge
EnvironmentFile=/opt/ai-secretary/voice-bridge/.env
ExecStart=/opt/ai-secretary/voice-bridge/venv/bin/python main_headless.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable voice-bridge
sudo systemctl start voice-bridge
```

## 主要コード解説

### パイプライン概要

```
マイク → VAD(発話検出) → faster-whisper(STT) → STT補正
  → ウェイクワード判定 → 検索判定 → DuckDuckGo検索
  → LLM(GLM-4-flash) → テキスト前処理(URL除去等)
  → VOICEVOX/COEIROINK(TTS) → スピーカー再生
```

### キャラクター自動切替の仕組み

`CHARACTER` 環境変数を変えると、3つが同時に切り替わります：

1. **LLMシステムプロンプト** — キャラの口調・性格を指示
2. **VOICEVOXスピーカーID** — キャラに合った声を自動選択
3. **固定メッセージ** — 起動音声やエラーメッセージもキャラに合わせる

```python
CHARACTER_SPEAKER_MAP = {
    "zundamon": 3,   # ずんだもん ノーマル
    "metan": 2,      # 四国めたん ノーマル
    "tsumugi": 8,    # 春日部つむぎ ノーマル
    "normal": 3,     # デフォルト
}

# VOICEVOX_SPEAKER_ID が明示的に設定されていればそれを使う
# 未設定なら CHARACTER から自動判定
_speaker_env = os.getenv("VOICEVOX_SPEAKER_ID", "")
if _speaker_env:
    VOICEVOX_SPEAKER_ID = int(_speaker_env)
else:
    VOICEVOX_SPEAKER_ID = CHARACTER_SPEAKER_MAP.get(CHARACTER, 3)
```

### COEIROINK リモートTTS

COEIROINK v2 は VOICEVOX とAPIが異なるため、専用クライアントを作成しています。

```python
class CoeiroinkTTS:
    def synthesize(self, text: str) -> str:
        payload = {
            "speakerUuid": self.speaker_uuid,
            "styleId": self.style_id,
            "text": text,
            "speedScale": self.speed,
            "volumeScale": self.volume,
            "pitchScale": self.pitch,
            "intonationScale": self.intonation,
            "outputSamplingRate": 24000,
        }
        resp = requests.post(f"{self.host}/v1/synthesis", json=payload, timeout=30)
        # WAVファイルとして保存して返す
```

VOICEVOX（2ステップ: `/audio_query` → `/synthesis`）と違い、COEIROINKは1リクエストで音声が返ります。

### COEIROINK サーバー（Ubuntu PC側）

COEIROINKはx86_64専用のため、LAN上のPCでエンジンを起動し、socatでポート転送します。

```bash
# systemd サービスとして登録
[Service]
ExecStart=/bin/bash -c './engine/engine & sleep 2 && socat TCP-LISTEN:50033,fork,reuseaddr,bind=0.0.0.0 TCP:127.0.0.1:50032'
```

### STT精度向上テクニック

```python
segments, info = self.stt.transcribe(
    audio,
    language="ja",
    beam_size=5,  # デフォルト1→5で精度向上
    vad_filter=True,
    initial_prompt=(
        "ずんだもん、今日の天気を教えて。明日の予定は？ニュースを調べて。"
        "リマインダーを設定して。日本の首都はどこ？東京の人口は？"
        "検索して。翻訳して。計算して。何時？いくら？"
        "プログラミング、人工知能、機械学習、ラズベリーパイ。"
    ),
)
```

- `beam_size=5`: 候補を増やして精度向上（+0.3〜0.5秒）
- `initial_prompt`: ドメインで使う語彙をWhisperに事前に教える
- `vad_filter=True`: 無音区間を除去してノイズを減らす

### TTS前処理（URL除去）

LLMの応答にURLが含まれるとVOICEVOX/COEIROINKが処理に失敗するため、TTS前に除去します。

```python
@staticmethod
def _clean_text_for_tts(text: str) -> str:
    import re
    text = re.sub(r'https?://\S+', '', text)      # URL除去
    text = re.sub(r'[「」【】\[\]()（）]', '', text)  # 括弧除去
    text = re.sub(r'\s+', ' ', text).strip()
    return text
```

## COEIROINK リモート接続の詳細手順

### Ubuntu PC側

1. COEIROINK Linux CPU版をダウンロード（https://coeiroink.com/download）

2. 解凍して起動テスト
```bash
cd ~/works/COEIROINK_LINUX_CPU_v.2.12.3
./engine/engine  # ルートディレクトリから実行すること
```

3. ボイスモデルの追加（GUIが必要）
  - Mac/Windows版でCOEIROINKを起動 → リリンちゃん等をダウンロード
  - ダウンロードされたモデル（`speaker_info/` 内）をLinux版にコピー
```bash
scp -r /path/to/speaker_info/lilin-2.1.0 user@ubuntu-pc:~/works/COEIROINK_.../speaker_info/
```

4. systemdサービス登録
```bash
sudo tee /etc/systemd/system/coeiroink.service << 'EOF'
[Unit]
Description=COEIROINK v2 TTS Engine
After=network.target

[Service]
Type=simple
User=zephel01
WorkingDirectory=/home/zephel01/works/COEIROINK_LINUX_CPU_v.2.12.3
ExecStart=/bin/bash -c './engine/engine & sleep 2 && socat TCP-LISTEN:50033,fork,reuseaddr,bind=0.0.0.0 TCP:127.0.0.1:50032'
ExecStop=/bin/bash -c 'kill $(pgrep -f "engine/engine"); kill $(pgrep -f "socat.*50033")'
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable coeiroink
sudo systemctl start coeiroink
```

### Raspberry Pi側

`.env` でCOEIROINKに切り替え：

```bash
CHARACTER=lilin
TTS_ENGINE=coeiroink
COEIROINK_URL=http://192.168.x.x:50033
COEIROINK_SPEAKER_UUID=cb11bdbd-78fc-4f16-b528-a400bae1782d
COEIROINK_STYLE_ID=92
# 90=のーまる, 91=ささやき, 92=メスガキ, 93=理解らされ
```

## トラブルシューティング

### STT関連

- **「首都」→「人は」のような誤認識**: `initial_prompt` にその語彙を追加する。`beam_size` を5に上げる
- **「天気」→「電気」**: `stt_corrections.json` に `{"corrections": {"電気": "天気"}}` を追加
- **「ずんだもん」→「すんだもん」**: ファジーウェイクワード機能で自動対応済み

### TTS関連

- **VOICEVOXで音が出ない**: `curl http://127.0.0.1:50021/version` で接続確認
- **COEIROINKで音が出ない**: URLがテキストに含まれていないか確認。`_clean_text_for_tts()` で除去済み
- **スピーカーIDが無効**: `curl http://127.0.0.1:50021/speakers` で利用可能なIDを確認

### COEIROINK関連

- **engine起動時に `IndexError`**: ルートディレクトリ（engine/ の親）から `./engine/engine` で起動する
- **127.0.0.1でしかバインドされない**: socatでポート転送する
- **ボイスモデルが認識されない**: `speaker_info/` にモデルフォルダをコピーしてエンジン再起動

## クレジット

- **VOICEVOX** — https://voicevox.hiroshiba.jp/
  - VOICEVOX:ずんだもん
  - VOICEVOX:四国めたん
  - VOICEVOX:春日部つむぎ
- **COEIROINK** — https://coeiroink.com/
  - COEIROINK:リリンちゃん（CV: 山田じぇみ子）
- **faster-whisper** — https://github.com/SYSTRAN/faster-whisper
- **GLM-4-flash** — https://open.bigmodel.cn/
- **DuckDuckGo Search** — ddgs パッケージ

---

*この記事のコードは MIT License で公開しています。*
*音声合成キャラクターの利用は各プロジェクトの利用規約に従ってください。*
