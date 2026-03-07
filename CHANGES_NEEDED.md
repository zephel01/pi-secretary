# voice-bridge → AI秘書化に必要な変更

## 現状の voice-bridge

voice-bridge は**リアルタイム音声翻訳アプリ**として作られている。

```
[システム音声] → BlackHole/WASAPI → faster-whisper(STT) → Google翻訳 → VOICEVOX/EdgeTTS → 再生
```

| 項目 | 現状 | 秘書構成で必要 |
|------|------|----------------|
| 目的 | 音声翻訳 | AI会話（秘書） |
| 音声入力 | システム音声ループバック | USBマイク直接入力 |
| 処理 | Google翻訳 (deep-translator) | OpenAI互換 chat API (OpenClaw) |
| プラットフォーム | macOS / Windows | Raspberry Pi 5 (Linux ARM64) |
| UI | tkinter GUI | ヘッドレス (systemd) |
| ウェイクワード | なし | 「ずんだもん」 |
| 会話履歴 | なし | OpenClaw メモリ |

## そのまま使えるもの

### transcriber.py ✅ ほぼそのまま
- faster-whisper ベースの STT
- 日本語対応済み
- int8 量子化でCPU最適化
- ハルシネーション除去フィルタ付き
- **変更点**: モデルサイズをPi向けに `small` か `base` に固定

### tts_voicevox.py ✅ ほぼそのまま
- VOICEVOX HTTP API クライアント
- ずんだもん (speaker_id=3) 対応済み
- 一時ファイル管理あり
- **変更点**: なし（そのまま動く）

### tts_engine.py ✅ フォールバック用にそのまま
- Edge TTS
- VOICEVOX 未起動時のフォールバックとして残す

## 新規作成が必要

### 1. ai_chat.py 🆕 (translator.py の置き換え)
Google翻訳の代わりに OpenAI互換 chat API を叩くモジュール。
- `openai` ライブラリで `/v1/chat/completions` に送信
- base_url を OpenClaw (127.0.0.1:18789) に向ける
- 会話履歴の保持
- ストリーミング対応（音声応答の低遅延化）

### 2. mic_capture_linux.py 🆕 (audio_capture.py の置き換え)
BlackHole/WASAPI ループバックの代わりに USB マイクから直接録音。
- sounddevice + ALSA で USB マイクをキャプチャ
- VAD (Voice Activity Detection) で発話区間を切り出し
- ウェイクワード検出との連携

### 3. main_headless.py 🆕 (main.py の置き換え)
GUI なしのヘッドレス会話ループ。
- systemd サービスとして動く
- ウェイクワード → 録音 → STT → AI Chat → TTS → 再生
- シグナルハンドリング (SIGTERM で graceful shutdown)

## 修正が必要

### requirements.txt → requirements-pi.txt
```diff
  faster-whisper
- deep-translator        # 削除: 翻訳は使わない
  edge-tts
  sounddevice
- PyAudioWPatch          # 削除: Windows 専用
  pygame
  numpy
  requests
+ openai                 # 追加: OpenAI互換API
+ webrtcvad              # 追加: VAD (発話区間検出)
```

## ファイル対応表

```
現在のファイル          → 秘書構成
─────────────────────────────────────
main.py               → main_headless.py (新規)
gui.py                → 不要（ヘッドレス）
audio_capture.py      → mic_capture_linux.py (新規)
audio_capture_win.py  → 不要（Pi は Linux）
transcriber.py        → そのまま使用
transcriber_moonshine.py → そのまま使用（軽量STT候補）
translator.py         → ai_chat.py (新規)
tts_voicevox.py       → そのまま使用
tts_engine.py         → そのまま使用（フォールバック）
chrome-extension/     → 不要
docs/                 → 残す
```
