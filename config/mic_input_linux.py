"""
voice-bridge Pi向けマイク入力モジュール

macOS の BlackHole / Windows の WASAPI ループバック の代わりに
USB マイクから直接 ALSA で録音する。

使い方:
  voice-bridge の audio input 部分をこのモジュールに差し替える。
  .env の AUDIO_INPUT_DEVICE でデバイスを指定可能。
"""

import os
import wave
import tempfile
import subprocess
from pathlib import Path

# --- 設定 ---
SAMPLE_RATE = int(os.getenv("AUDIO_SAMPLE_RATE", "16000"))
CHANNELS = 1
FORMAT = "S16_LE"  # 16-bit signed little-endian
INPUT_DEVICE = os.getenv("AUDIO_INPUT_DEVICE", "default")
SILENCE_TIMEOUT = float(os.getenv("SILENCE_TIMEOUT", "2.0"))
MAX_RECORD_SECONDS = int(os.getenv("MAX_RECORD_SECONDS", "30"))


def list_audio_devices():
    """利用可能な録音デバイスを一覧表示"""
    result = subprocess.run(
        ["arecord", "-l"],
        capture_output=True, text=True, timeout=5
    )
    return result.stdout


def record_audio(duration: float = None, output_path: str = None) -> str:
    """
    USB マイクから録音して WAV ファイルパスを返す。

    Args:
        duration: 録音秒数 (None なら MAX_RECORD_SECONDS)
        output_path: 出力先 (None なら一時ファイル)

    Returns:
        録音した WAV ファイルのパス
    """
    if duration is None:
        duration = MAX_RECORD_SECONDS

    if output_path is None:
        fd, output_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)

    cmd = [
        "arecord",
        "-D", INPUT_DEVICE,
        "-f", FORMAT,
        "-r", str(SAMPLE_RATE),
        "-c", str(CHANNELS),
        "-d", str(int(duration)),
        "-q",  # quiet
        output_path
    ]

    try:
        subprocess.run(cmd, timeout=duration + 5, check=True)
    except subprocess.TimeoutExpired:
        pass  # タイムアウトは正常終了扱い
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"録音に失敗しました: {e}")

    return output_path


def play_audio(wav_path: str):
    """WAV ファイルを再生"""
    device = os.getenv("AUDIO_OUTPUT_DEVICE", "default")
    cmd = ["aplay", "-D", device, "-q", wav_path]
    subprocess.run(cmd, check=True, timeout=60)


if __name__ == "__main__":
    print("=== オーディオデバイス一覧 ===")
    print(list_audio_devices())
    print("\n=== 5秒間テスト録音 ===")
    path = record_audio(duration=5)
    print(f"録音完了: {path}")
    print("再生中...")
    play_audio(path)
    print("完了")
    os.unlink(path)
