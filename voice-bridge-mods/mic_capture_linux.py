"""
mic_capture_linux.py — Raspberry Pi 向け USB マイク入力

audio_capture.py の置き換え。
BlackHole / WASAPI ループバックの代わりに、
USB マイクから sounddevice + VAD で発話区間を切り出す。

audio_capture.py の AudioCapture と同じインターフェースを提供し、
既存の main.py からの呼び出しを最小変更で切り替えられるようにする。
"""

import os
import time
import queue
import logging
import threading
import tempfile
import wave
import struct
from typing import Optional, Callable

import numpy as np
import sounddevice as sd

logger = logging.getLogger(__name__)

# --- 設定 ---
SAMPLE_RATE = int(os.getenv("AUDIO_SAMPLE_RATE", "16000"))
CHANNELS = 1
BLOCK_SIZE = int(SAMPLE_RATE * 0.03)  # 30ms フレーム (VAD 用)
SILENCE_TIMEOUT = float(os.getenv("SILENCE_TIMEOUT", "2.0"))
MAX_RECORD_SECONDS = int(os.getenv("MAX_RECORD_SECONDS", "30"))
ENERGY_THRESHOLD = float(os.getenv("ENERGY_THRESHOLD", "0.02"))  # 発話判定の閾値


class MicCapture:
    """
    USB マイクからの音声キャプチャ。
    audio_capture.py の AudioCapture と同じインターフェース。
    """

    def __init__(
        self,
        device: Optional[str] = None,
        sample_rate: int = SAMPLE_RATE,
        channels: int = CHANNELS,
        on_level: Optional[Callable[[float], None]] = None,
    ):
        self.device = device or os.getenv("AUDIO_INPUT_DEVICE") or None
        self.sample_rate = sample_rate
        self.channels = channels
        self.on_level = on_level  # AudioCapture 互換: レベルコールバック

        self._audio_queue: queue.Queue[np.ndarray] = queue.Queue()
        self._is_recording = False
        self._stream: Optional[sd.InputStream] = None

        # デバイスの確認
        self._resolve_device()

    def _resolve_device(self):
        """デバイス名からインデックスを解決"""
        if self.device is None:
            # デフォルトデバイス
            info = sd.query_devices(kind="input")
            logger.info(f"デフォルト入力デバイス: {info['name']}")
            self._device_index = None
            return

        # 文字列なら名前で検索
        if isinstance(self.device, str) and not self.device.isdigit():
            devices = sd.query_devices()
            for i, d in enumerate(devices):
                if self.device.lower() in d["name"].lower() and d["max_input_channels"] > 0:
                    self._device_index = i
                    logger.info(f"入力デバイス: [{i}] {d['name']}")
                    return
            logger.warning(f"デバイス '{self.device}' が見つかりません。デフォルトを使用")
            self._device_index = None
        else:
            self._device_index = int(self.device) if self.device else None

    def list_devices(self) -> str:
        """利用可能な入力デバイスを一覧"""
        devices = sd.query_devices()
        lines = ["=== 入力デバイス一覧 ==="]
        for i, d in enumerate(devices):
            if d["max_input_channels"] > 0:
                marker = " ★" if i == (self._device_index or sd.default.device[0]) else ""
                lines.append(f"  [{i}] {d['name']} (ch={d['max_input_channels']}){marker}")
        return "\n".join(lines)

    def _audio_callback(self, indata: np.ndarray, frames: int, time_info, status):
        """sounddevice コールバック"""
        if status:
            logger.warning(f"Audio status: {status}")

        audio = indata[:, 0].copy() if indata.ndim > 1 else indata.copy().flatten()

        # レベルコールバック (AudioCapture 互換)
        if self.on_level:
            rms = float(np.sqrt(np.mean(audio ** 2)))
            self.on_level(rms)

        if self._is_recording:
            self._audio_queue.put(audio)

    def start_stream(self):
        """マイクストリームを開始"""
        if self._stream is not None:
            return

        self._stream = sd.InputStream(
            device=self._device_index,
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype="float32",
            blocksize=BLOCK_SIZE,
            callback=self._audio_callback,
        )
        self._stream.start()
        logger.info("マイクストリーム開始")

    def stop_stream(self):
        """マイクストリームを停止"""
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
            logger.info("マイクストリーム停止")

    def wait_for_speech(self, timeout: float = None) -> bool:
        """
        発話が始まるまで待つ。
        ウェイクワード検出後に呼ぶ想定。

        Returns:
            True: 発話検出, False: タイムアウト
        """
        self._is_recording = True
        start = time.time()
        timeout = timeout or 10.0

        try:
            while time.time() - start < timeout:
                try:
                    audio = self._audio_queue.get(timeout=0.1)
                    rms = float(np.sqrt(np.mean(audio ** 2)))
                    if rms > ENERGY_THRESHOLD:
                        # 検出した音声をキューに戻す
                        self._audio_queue.put(audio)
                        return True
                except queue.Empty:
                    continue
        finally:
            pass  # recording は続ける

        self._is_recording = False
        return False

    def record_utterance(self) -> Optional[np.ndarray]:
        """
        発話を1つ録音して返す。
        無音が SILENCE_TIMEOUT 続いたら終了。

        Returns:
            numpy array (float32, mono) or None
        """
        self._is_recording = True
        frames: list[np.ndarray] = []
        silence_start: Optional[float] = None
        record_start = time.time()

        try:
            while True:
                elapsed = time.time() - record_start
                if elapsed > MAX_RECORD_SECONDS:
                    logger.info(f"最大録音時間 ({MAX_RECORD_SECONDS}秒) に到達")
                    break

                try:
                    audio = self._audio_queue.get(timeout=0.5)
                except queue.Empty:
                    if silence_start and (time.time() - silence_start > SILENCE_TIMEOUT):
                        break
                    continue

                frames.append(audio)
                rms = float(np.sqrt(np.mean(audio ** 2)))

                if rms > ENERGY_THRESHOLD:
                    silence_start = None
                else:
                    if silence_start is None:
                        silence_start = time.time()
                    elif time.time() - silence_start > SILENCE_TIMEOUT:
                        logger.info(f"無音タイムアウト ({SILENCE_TIMEOUT}秒)")
                        break

        finally:
            self._is_recording = False
            # キューをクリア
            while not self._audio_queue.empty():
                try:
                    self._audio_queue.get_nowait()
                except queue.Empty:
                    break

        if not frames:
            return None

        audio = np.concatenate(frames)
        logger.info(f"録音完了: {len(audio)/self.sample_rate:.1f}秒")
        return audio

    def record_to_wav(self, output_path: str = None) -> Optional[str]:
        """
        発話を WAV ファイルに保存。
        AudioCapture の get_audio_chunk() と似た役割。

        Returns:
            WAV ファイルパス or None
        """
        audio = self.record_utterance()
        if audio is None:
            return None

        if output_path is None:
            fd, output_path = tempfile.mkstemp(suffix=".wav", prefix="vb_mic_")
            os.close(fd)

        # float32 → int16
        audio_int16 = (audio * 32767).astype(np.int16)

        with wave.open(output_path, "wb") as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(self.sample_rate)
            wf.writeframes(audio_int16.tobytes())

        return output_path

    # --- AudioCapture 互換メソッド ---

    def get_audio_chunk(self) -> Optional[np.ndarray]:
        """
        audio_capture.py の AudioCapture.get_audio_chunk() 互換。
        1発話分の音声を返す。
        """
        return self.record_utterance()

    def start(self):
        """AudioCapture.start() 互換"""
        self.start_stream()

    def stop(self):
        """AudioCapture.stop() 互換"""
        self.stop_stream()


# --- リサンプル ---

def _resample(data: np.ndarray, orig_rate: int, target_rate: int) -> np.ndarray:
    """numpy だけで簡易リサンプル (線形補間)"""
    if orig_rate == target_rate:
        return data
    ratio = target_rate / orig_rate
    new_len = int(len(data) * ratio)
    indices = np.linspace(0, len(data) - 1, new_len)
    return np.interp(indices, np.arange(len(data)), data).astype(data.dtype)


PLAYBACK_RATE = int(os.getenv("AUDIO_SAMPLE_RATE", "48000"))

# --- 再生ユーティリティ ---

def play_wav(wav_path: str, device: Optional[str] = None):
    """
    WAV ファイルを再生する。
    pygame が使えればそちら、なければ sounddevice で再生。
    """
    try:
        import pygame

        if not pygame.mixer.get_init():
            pygame.mixer.init()
        pygame.mixer.music.load(wav_path)
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            time.sleep(0.1)
    except (ImportError, Exception) as e:
        logger.debug(f"pygame 再生失敗、sounddevice で再生: {e}")
        with wave.open(wav_path, "rb") as wf:
            data = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
            data = data.astype(np.float32) / 32768.0
            out_device = device or os.getenv("AUDIO_OUTPUT_DEVICE") or None
            if out_device and not out_device.isdigit():
                out_device = None  # デフォルトにフォールバック
            sd.play(data, samplerate=wf.getframerate(), device=out_device)
            sd.wait()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    mic = MicCapture()
    print(mic.list_devices())
    print("\n話しかけてください（Ctrl+C で終了）...")

    mic.start_stream()
    try:
        while True:
            print("待機中...")
            if mic.wait_for_speech(timeout=10):
                print("発話検出！録音中...")
                wav = mic.record_to_wav()
                if wav:
                    print(f"録音完了: {wav}")
                    print("再生中...")
                    play_wav(wav)
                    os.unlink(wav)
            else:
                print("タイムアウト")
    except KeyboardInterrupt:
        print("\n終了")
    finally:
        mic.stop_stream()
