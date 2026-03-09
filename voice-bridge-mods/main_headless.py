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

# --- STT 誤認識補正 ---
_stt_corrections = {}
_stt_corrections_file = os.getenv(
    "STT_CORRECTIONS_FILE",
    "/opt/ai-secretary/pi-secretary/config/stt_corrections.json",
)
try:
    import json
    if os.path.isfile(_stt_corrections_file):
        with open(_stt_corrections_file, "r", encoding="utf-8") as _f:
            _data = json.load(_f)
            _stt_corrections = _data.get("corrections", {})
        logger.info(f"STT 補正ルール読み込み: {len(_stt_corrections)} 件")
except Exception as _e:
    logger.warning(f"STT 補正ファイル読み込みエラー: {_e}")


def apply_stt_corrections(text: str) -> str:
    """STT の誤認識を補正する"""
    for wrong, correct in _stt_corrections.items():
        if wrong in text:
            text = text.replace(wrong, correct)
    return text


# STT — 既存の transcriber.py をそのまま使う
try:
    from transcriber import AudioTranscriber
except ImportError:
    AudioTranscriber = None
    logger.warning("transcriber.py が見つかりません。STT を直接実装します。")

# TTS — 既存の tts_voicevox.py / tts_engine.py / tts_coeiroink.py を使う
try:
    from tts_voicevox import VoicevoxTTS
except ImportError:
    VoicevoxTTS = None
    logger.warning("tts_voicevox.py が見つかりません")

try:
    from tts_coeiroink import CoeiroinkTTS
except ImportError:
    CoeiroinkTTS = None

try:
    from tts_engine import EdgeTTSEngine
except ImportError:
    EdgeTTSEngine = None


# --- 設定 ---
WAKE_WORD = os.getenv("WAKE_WORD", "ずんだもん")
STT_MODEL_SIZE = os.getenv("STT_MODEL_SIZE", "base")
STT_LANGUAGE = os.getenv("STT_LANGUAGE", "ja")
CHARACTER = os.getenv("CHARACTER", "zundamon")
VOICEVOX_URL = os.getenv("VOICEVOX_URL", "http://127.0.0.1:50021")
COEIROINK_URL = os.getenv("COEIROINK_URL", "http://192.168.4.85:50033")
USE_WAKE_WORD = bool(WAKE_WORD.strip())

# --- CHARACTER → TTS エンジン自動判定 ---
# COEIROINK 専用キャラは TTS_ENGINE を明示しなくても自動で coeiroink になる
COEIROINK_CHARACTERS = {"lilin"}

_tts_env = os.getenv("TTS_ENGINE", "").lower()
if CHARACTER in COEIROINK_CHARACTERS:
    # COEIROINK 専用キャラは TTS_ENGINE の設定に関わらず強制的に coeiroink
    TTS_ENGINE_TYPE = "coeiroink"
    if _tts_env and _tts_env != "coeiroink":
        logger.info(f"CHARACTER={CHARACTER} は COEIROINK 専用 → TTS_ENGINE={_tts_env} を無視して coeiroink に設定")
elif _tts_env:
    TTS_ENGINE_TYPE = _tts_env
else:
    TTS_ENGINE_TYPE = "voicevox"

# --- CHARACTER → VOICEVOX スピーカーID マッピング ---
# VOICEVOX_SPEAKER_ID が明示的に設定されていればそれを使う。
# 未設定なら CHARACTER から自動判定。
CHARACTER_SPEAKER_MAP = {
    "zundamon": 3,   # ずんだもん ノーマル
    "metan": 2,      # 四国めたん ノーマル
    "tsumugi": 8,    # 春日部つむぎ ノーマル
    "normal": 3,     # デフォルト
}

_speaker_env = os.getenv("VOICEVOX_SPEAKER_ID", "")
if _speaker_env:
    VOICEVOX_SPEAKER_ID = int(_speaker_env)
else:
    VOICEVOX_SPEAKER_ID = CHARACTER_SPEAKER_MAP.get(CHARACTER, 3)

# --- キャラクター別の固定メッセージ ---
CHARACTER_MESSAGES = {
    "zundamon": {
        "startup": "起動したのだ。なんでも聞くのだ。",
        "ready": "はいなのだ。なんでも聞くのだ。",
        "error": "すみません、エラーが発生したのだ。",
    },
    "metan": {
        "startup": "起動しましたわ。何でもお聞きになって。",
        "ready": "はい、何でしょう？",
        "error": "申し訳ありません、エラーが発生しましたわ。",
    },
    "tsumugi": {
        "startup": "起動したよ〜！なんでも聞いてね！",
        "ready": "はーい！なんでも聞いてよ〜！",
        "error": "ごめん、エラーが出ちゃった〜。",
    },
    "lilin": {
        "startup": "起動したよ〜！リリンに何でも聞いてね！",
        "ready": "はいはい、何〜？",
        "error": "あちゃ〜、エラーだって〜。",
    },
    "normal": {
        "startup": "起動しました。ご用件をどうぞ。",
        "ready": "はい、何でしょうか。",
        "error": "すみません、エラーが発生しました。",
    },
}
_char_msgs = CHARACTER_MESSAGES.get(CHARACTER, CHARACTER_MESSAGES["normal"])


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
        """TTS 初期化 (TTS_ENGINE 設定に応じて選択)"""
        import requests as _req

        # --- COEIROINK ---
        if TTS_ENGINE_TYPE == "coeiroink" and CoeiroinkTTS:
            try:
                resp = _req.get(f"{COEIROINK_URL}/v1/speakers", timeout=5)
                if resp.status_code == 200:
                    tts = CoeiroinkTTS(host=COEIROINK_URL)
                    logger.info(f"COEIROINK 接続OK ({COEIROINK_URL})")
                    return tts
            except Exception as e:
                logger.warning(f"COEIROINK 接続失敗: {e}")
                # フォールバック: VOICEVOX を試す
                logger.info("VOICEVOX にフォールバック")

        # --- VOICEVOX ---
        if VoicevoxTTS:
            try:
                resp = _req.get(f"{VOICEVOX_URL}/version", timeout=3)
                if resp.status_code == 200:
                    tts = VoicevoxTTS(
                        host=VOICEVOX_URL,
                        speaker_id=VOICEVOX_SPEAKER_ID,
                    )
                    logger.info(f"VOICEVOX 接続OK (v{resp.text})")
                    return tts
            except Exception as e:
                logger.warning(f"VOICEVOX 接続失敗: {e}")

        # --- Edge TTS フォールバック ---
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
                    beam_size=5,
                    vad_filter=True,
                    initial_prompt=(
                        "ずんだもん、今日の天気を教えて。明日の予定は？ニュースを調べて。"
                        "リマインダーを設定して。日本の首都はどこ？東京の人口は？"
                        "検索して。翻訳して。計算して。何時？いくら？"
                        "プログラミング、人工知能、機械学習、ラズベリーパイ。"
                    ),
                )
                texts = [seg.text for seg in segments]
                return " ".join(texts).strip()

        return ""

    @staticmethod
    def _clean_text_for_tts(text: str) -> str:
        """TTS に渡す前にテキストを前処理する"""
        import re
        # URL を除去
        text = re.sub(r'https?://\S+', '', text)
        # 残った括弧内の空白やゴミを整理
        text = re.sub(r'[「」【】\[\]()（）]', '', text)
        # 連続空白・改行を整理
        text = re.sub(r'\s+', ' ', text).strip()
        # 空になったら None 的扱い
        return text

    def _speak(self, text: str):
        """テキスト → 音声再生"""
        if not text or not self.tts:
            return

        # TTS 用にテキストをクリーンアップ
        clean = self._clean_text_for_tts(text)
        if not clean:
            logger.warning(f"TTS テキストが空になりました (元: {text[:50]}...)")
            return

        self._is_playing = True
        try:
            # COEIROINK
            if CoeiroinkTTS and isinstance(self.tts, CoeiroinkTTS):
                wav_path = self.tts.synthesize(clean)
                if wav_path and os.path.isfile(wav_path):
                    play_wav(wav_path)
                    try:
                        os.unlink(wav_path)
                    except OSError:
                        pass
                else:
                    logger.warning("COEIROINK 合成失敗")
                return

            # VOICEVOX
            if VoicevoxTTS and isinstance(self.tts, VoicevoxTTS):
                wav_path = self.tts.synthesize(clean)
                if wav_path and os.path.isfile(wav_path):
                    play_wav(wav_path)
                    try:
                        os.unlink(wav_path)
                    except OSError:
                        pass
                return

            # Edge TTS
            if EdgeTTSEngine and isinstance(self.tts, EdgeTTSEngine):
                wav_path = self.tts.synthesize(clean)
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
        logger.info(f"  キャラクター: {CHARACTER}")
        if CoeiroinkTTS and isinstance(self.tts, CoeiroinkTTS):
            logger.info(f"  TTS: COEIROINK ({COEIROINK_URL})")
        elif VoicevoxTTS and isinstance(self.tts, VoicevoxTTS):
            logger.info(f"  TTS: VOICEVOX (speaker_id={VOICEVOX_SPEAKER_ID})")
        else:
            logger.info(f"  TTS: Edge TTS (フォールバック)")
        logger.info("=" * 50)

        self._running = True
        self.mic.start_stream()

        # 起動音声
        self._speak(_char_msgs["startup"])

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

        # STT 誤認識を補正
        corrected = apply_stt_corrections(text)
        if corrected != text:
            logger.info(f"認識: {text} → 補正: {corrected}")
            text = corrected
        else:
            logger.info(f"認識: {text}")

        # ウェイクワード判定
        triggered, command_text = self._check_wake_word(text)
        if not triggered:
            return  # ウェイクワードなし → 無視

        # コマンドテキストが空ならプロンプト
        if not command_text:
            self._speak(_char_msgs["ready"])
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
            reply = _char_msgs["error"]

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
