#!/usr/bin/env python3
"""
main_headless.py — ヘッドレス AI 秘書 会話ループ

main.py + gui.py の置き換え。
GUI なしで systemd サービスとして動く。

パイプライン:
  [USBマイク] → VAD → faster-whisper(STT)
    → OpenClaw(AI Chat) → VOICEVOX(TTS) → [スピーカー]
"""

import os
import sys
import signal
import logging
import time
import tempfile
from pathlib import Path
from typing import Optional

# --- ログ設定 ---
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE = os.getenv("LOG_FILE", "")

handlers = [logging.StreamHandler(sys.stdout)]
if LOG_FILE:
    Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
    handlers.append(logging.FileHandler(LOG_FILE, encoding="utf-8"))

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=handlers,
)
logger = logging.getLogger("voice-bridge")

# --- モジュールインポート ---
# 同じディレクトリまたは voice-bridge ルートにあることを想定
try:
    from mic_capture_linux import MicCapture, play_wav
except ImportError:
    sys.path.insert(0, os.path.dirname(__file__))
    from mic_capture_linux import MicCapture, play_wav

try:
    from ai_chat import AiChat
except ImportError:
    sys.path.insert(0, os.path.dirname(__file__))
    from ai_chat import AiChat

# STT — 既存の transcriber.py をそのまま使う
try:
    from transcriber import AudioTranscriber
except ImportError:
    AudioTranscriber = None
    logger.warning("transcriber.py が見つかりません。STT を直接実装します。")

# TTS — 既存の tts_voicevox.py / tts_engine.py を使う
try:
    from tts_voicevox import VoicevoxTTS
except ImportError:
    VoicevoxTTS = None
    logger.warning("tts_voicevox.py が見つかりません")

try:
    from tts_engine import EdgeTTSEngine
except ImportError:
    EdgeTTSEngine = None


# --- 設定 ---
WAKE_WORD = os.getenv("WAKE_WORD", "ずんだもん")
STT_MODEL_SIZE = os.getenv("STT_MODEL_SIZE", "base")
STT_LANGUAGE = os.getenv("STT_LANGUAGE", "ja")
VOICEVOX_URL = os.getenv("VOICEVOX_URL", "http://127.0.0.1:50021")
VOICEVOX_SPEAKER_ID = int(os.getenv("VOICEVOX_SPEAKER_ID", "3"))
USE_WAKE_WORD = bool(WAKE_WORD.strip())


class VoiceBridgeHeadless:
    """ヘッドレス AI 秘書メインループ"""

    def __init__(self):
        self._running = False

        # --- マイク ---
        logger.info("マイク初期化中...")
        self.mic = MicCapture(on_level=self._on_audio_level)

        # --- STT ---
        logger.info(f"STT 初期化中 (model={STT_MODEL_SIZE}, lang={STT_LANGUAGE})...")
        if AudioTranscriber:
            self.stt = AudioTranscriber(
                model_size=STT_MODEL_SIZE,
                language=STT_LANGUAGE,
            )
        else:
            self.stt = self._init_fallback_stt()

        # --- AI Chat ---
        logger.info("AI Chat 初期化中...")
        self.chat = AiChat()

        # --- TTS ---
        logger.info("TTS 初期化中...")
        self.tts = self._init_tts()

        # --- 再生中フラグ (フィードバック防止) ---
        self._is_playing = False

    def _init_fallback_stt(self):
        """transcriber.py がない場合のフォールバック STT"""
        try:
            from faster_whisper import WhisperModel

            model = WhisperModel(
                STT_MODEL_SIZE,
                device="cpu",
                compute_type="int8",
            )
            logger.info(f"faster-whisper 直接ロード: {STT_MODEL_SIZE}")
            return model
        except ImportError:
            logger.error("faster-whisper がインストールされていません")
            return None

    def _init_tts(self):
        """TTS 初期化 (VOICEVOX 優先、Edge TTS フォールバック)"""
        # VOICEVOX チェック
        if VoicevoxTTS:
            try:
                import requests

                resp = requests.get(f"{VOICEVOX_URL}/version", timeout=3)
                if resp.status_code == 200:
                    tts = VoicevoxTTS(
                        host=VOICEVOX_URL,
                        speaker_id=VOICEVOX_SPEAKER_ID,
                    )
                    logger.info(f"VOICEVOX 接続OK (v{resp.text})")
                    return tts
            except Exception as e:
                logger.warning(f"VOICEVOX 接続失敗: {e}")

        # Edge TTS フォールバック
        if EdgeTTSEngine:
            logger.info("Edge TTS にフォールバック")
            return EdgeTTSEngine()

        logger.error("利用可能な TTS エンジンがありません")
        return None

    def _on_audio_level(self, rms: float):
        """音声レベルコールバック (デバッグ用)"""
        pass  # ヘッドレスなのでログは出さない

    def _transcribe(self, audio) -> str:
        """音声 → テキスト"""
        import numpy as np

        if audio is None or len(audio) == 0:
            return ""

        # AudioTranscriber が使える場合
        if AudioTranscriber and isinstance(self.stt, AudioTranscriber):
            # transcriber.py の transcribe() を呼ぶ
            # 引数の形式は transcriber.py の実装に依存
            text = self.stt.transcribe(audio)
            if isinstance(text, list):
                text = " ".join(t.text if hasattr(t, "text") else str(t) for t in text)
            return text.strip() if text else ""

        # faster-whisper 直接
        if self.stt is not None:
            from faster_whisper import WhisperModel

            if isinstance(self.stt, WhisperModel):
                # 48000Hz -> 16000Hz にリサンプル (faster-whisper が要求)
                if self.mic.sample_rate != 16000:
                    import numpy as np
                    try:
                        from scipy.signal import resample_poly
                        from math import gcd
                        g = gcd(16000, self.mic.sample_rate)
                        audio = resample_poly(audio, 16000 // g, self.mic.sample_rate // g).astype(np.float32)
                    except ImportError:
                        ratio = 16000 / self.mic.sample_rate
                        new_len = int(len(audio) * ratio)
                        indices = np.linspace(0, len(audio) - 1, new_len)
                        audio = np.interp(indices, np.arange(len(audio)), audio).astype(np.float32)
                # numpy array を直接渡す
                segments, info = self.stt.transcribe(
                    audio,
                    language=STT_LANGUAGE,
                    beam_size=3,
                    vad_filter=True,
                    initial_prompt="ずんだもん、今日の天気を教えて。明日の予定は？ニュースを調べて。リマインダーを設定して。",
                )
                texts = [seg.text for seg in segments]
                return " ".join(texts).strip()

        return ""

    def _speak(self, text: str):
        """テキスト → 音声再生"""
        if not text or not self.tts:
            return

        self._is_playing = True
        try:
            # VOICEVOX
            if VoicevoxTTS and isinstance(self.tts, VoicevoxTTS):
                wav_path = self.tts.synthesize(text)
                if wav_path and os.path.isfile(wav_path):
                    play_wav(wav_path)
                    try:
                        os.unlink(wav_path)
                    except OSError:
                        pass
                return

            # Edge TTS
            if EdgeTTSEngine and isinstance(self.tts, EdgeTTSEngine):
                wav_path = self.tts.synthesize(text)
                if wav_path and os.path.isfile(wav_path):
                    play_wav(wav_path)
                    try:
                        os.unlink(wav_path)
                    except OSError:
                        pass
                return

        except Exception as e:
            logger.error(f"TTS 再生エラー: {e}")
        finally:
            self._is_playing = False

    def _check_wake_word(self, text: str) -> tuple[bool, str]:
        """
        ウェイクワード判定 (STT の誤認識に対応するファジーマッチ)。

        Returns:
            (triggered, remaining_text)
        """
        if not USE_WAKE_WORD:
            return True, text

        text_lower = text.lower().strip()
        wake_lower = WAKE_WORD.lower()

        # ウェイクワードの誤認識バリエーション
        # STT が濁音・半濁音を間違えることがある
        wake_variants = {wake_lower}
        if wake_lower == "ずんだもん":
            wake_variants.update([
                "すんだもん", "ズンダモン", "ずんだもん",
                "ずんだも", "すんだも", "zunndamonn",
            ])
        # 一般的な濁音⇔清音の揺れを追加
        replacements = [("ず", "す"), ("だ", "た"), ("ぜ", "せ"), ("ど", "と"), ("ば", "は")]
        for old, new in replacements:
            if old in wake_lower:
                wake_variants.add(wake_lower.replace(old, new))

        for variant in wake_variants:
            if variant in text_lower:
                idx = text_lower.index(variant)
                remaining = text[idx + len(variant) :].strip()
                remaining = remaining.lstrip("、，, ")
                return True, remaining

        return False, text

    def run(self):
        """メインループ"""
        logger.info("=" * 50)
        logger.info("voice-bridge ヘッドレスモード起動")
        logger.info(f"  ウェイクワード: {'「' + WAKE_WORD + '」' if USE_WAKE_WORD else '無効（常時リスニング）'}")
        logger.info(f"  STT: faster-whisper ({STT_MODEL_SIZE})")
        logger.info(f"  TTS: {'VOICEVOX' if VoicevoxTTS and isinstance(self.tts, VoicevoxTTS) else 'Edge TTS'}")
        logger.info("=" * 50)

        self._running = True
        self.mic.start_stream()

        # 起動音声
        self._speak("起動したのだ。なんでも聞くのだ。")

        try:
            while self._running:
                self._conversation_turn()
        except KeyboardInterrupt:
            logger.info("Ctrl+C で終了")
        finally:
            self._running = False
            self.mic.stop_stream()
            logger.info("voice-bridge 停止")

    def _conversation_turn(self):
        """1回の会話ターン"""
        # 再生中はスキップ (フィードバック防止)
        if self._is_playing:
            time.sleep(0.1)
            return

        # 発話待ち
        if not self.mic.wait_for_speech(timeout=5.0):
            return  # タイムアウト → 次のループへ

        # 録音
        audio = self.mic.record_utterance()
        if audio is None or len(audio) < self.mic.sample_rate * 0.3:
            return  # 短すぎる

        # STT (処理中はマイクを一時停止して overflow を防ぐ)
        self.mic.stop_stream()
        text = self._transcribe(audio)
        self.mic.start_stream()
        if not text:
            return

        logger.info(f"認識: {text}")

        # ウェイクワード判定
        triggered, command_text = self._check_wake_word(text)
        if not triggered:
            return  # ウェイクワードなし → 無視

        # コマンドテキストが空ならプロンプト
        if not command_text:
            self._speak("はいなのだ。なんでも聞くのだ。")
            # 次の発話を待つ
            if self.mic.wait_for_speech(timeout=5.0):
                audio2 = self.mic.record_utterance()
                if audio2 is not None:
                    self.mic.stop_stream()
                    command_text = self._transcribe(audio2)
                    self.mic.start_stream()

        if not command_text:
            return

        logger.info(f"コマンド: {command_text}")

        # AI Chat
        try:
            reply = self.chat.chat(command_text)
            logger.info(f"応答: {reply}")
        except Exception as e:
            logger.error(f"Chat エラー: {e}")
            reply = "すみません、エラーが発生したのだ。"

        # TTS 再生
        self._speak(reply)

    def stop(self):
        """外部からの停止"""
        self._running = False


# --- シグナルハンドリング ---
_bridge: Optional[VoiceBridgeHeadless] = None


def _signal_handler(signum, frame):
    logger.info(f"シグナル {signum} を受信、停止中...")
    if _bridge:
        _bridge.stop()


def main():
    global _bridge

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    _bridge = VoiceBridgeHeadless()
    _bridge.run()


if __name__ == "__main__":
    main()
