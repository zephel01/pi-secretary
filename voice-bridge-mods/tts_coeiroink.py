"""
tts_coeiroink.py — COEIROINK v2 TTS クライアント

COEIROINK v2 の REST API (/v1/synthesis) を使って音声合成する。
VOICEVOX とはAPIが異なるため専用クライアントが必要。

環境変数:
  COEIROINK_URL          — COEIROINK エンドポイント (default: http://127.0.0.1:50032)
  COEIROINK_SPEAKER_UUID — スピーカー UUID
  COEIROINK_STYLE_ID     — スタイル ID (default: 0)
  COEIROINK_SPEED        — 話速 (default: 1.0)
  COEIROINK_VOLUME       — 音量 (default: 1.0)
  COEIROINK_PITCH        — ピッチ (default: 0.0)
  COEIROINK_INTONATION   — 抑揚 (default: 1.0)
"""

import os
import logging
import tempfile
import requests

logger = logging.getLogger(__name__)


class CoeiroinkTTS:
    """COEIROINK v2 TTS クライアント"""

    def __init__(
        self,
        host: str = None,
        speaker_uuid: str = None,
        style_id: int = None,
        speed: float = None,
        volume: float = None,
        pitch: float = None,
        intonation: float = None,
    ):
        self.host = (host or os.getenv("COEIROINK_URL", "http://127.0.0.1:50032")).rstrip("/")
        self.speaker_uuid = speaker_uuid or os.getenv("COEIROINK_SPEAKER_UUID", "")
        self.style_id = style_id if style_id is not None else int(os.getenv("COEIROINK_STYLE_ID", "0"))
        self.speed = speed if speed is not None else float(os.getenv("COEIROINK_SPEED", "1.0"))
        self.volume = volume if volume is not None else float(os.getenv("COEIROINK_VOLUME", "1.0"))
        self.pitch = pitch if pitch is not None else float(os.getenv("COEIROINK_PITCH", "0.0"))
        self.intonation = intonation if intonation is not None else float(os.getenv("COEIROINK_INTONATION", "1.0"))

        if not self.speaker_uuid:
            logger.warning("COEIROINK_SPEAKER_UUID が未設定です")

        logger.info(
            f"CoeiroinkTTS 初期化: {self.host} "
            f"(uuid={self.speaker_uuid}, style={self.style_id})"
        )

    def synthesize(self, text: str) -> str:
        """
        テキストを音声合成して WAV ファイルパスを返す。

        Returns:
            WAV ファイルの一時パス。失敗時は None。
        """
        if not text or not self.speaker_uuid:
            return None

        payload = {
            "speakerUuid": self.speaker_uuid,
            "styleId": self.style_id,
            "text": text,
            "speedScale": self.speed,
            "volumeScale": self.volume,
            "pitchScale": self.pitch,
            "intonationScale": self.intonation,
            "prePhonemeLength": 0.1,
            "postPhonemeLength": 0.5,
            "outputSamplingRate": 24000,
        }

        try:
            resp = requests.post(
                f"{self.host}/v1/synthesis",
                json=payload,
                timeout=30,
            )
            resp.raise_for_status()

            # WAV を一時ファイルに保存
            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            tmp.write(resp.content)
            tmp.close()
            return tmp.name

        except requests.exceptions.ConnectionError:
            logger.error(f"COEIROINK 接続失敗: {self.host}")
            return None
        except requests.exceptions.Timeout:
            logger.error("COEIROINK タイムアウト")
            return None
        except Exception as e:
            logger.error(f"COEIROINK 合成エラー: {e}")
            return None

    def list_speakers(self) -> list:
        """利用可能なスピーカー一覧を取得"""
        try:
            resp = requests.get(f"{self.host}/v1/speakers", timeout=10)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"COEIROINK スピーカー一覧取得エラー: {e}")
            return []
