#!/usr/bin/env python3
"""
VOICEVOX Engine モックサーバー
テスト環境用 — 実際の音声合成はせず API 互換レスポンスを返す。
Flask で VOICEVOX の主要エンドポイントを再現する。
"""
import json
import struct
import io
from flask import Flask, request, jsonify, Response

app = Flask(__name__)

# ダミーの話者リスト
SPEAKERS = [
    {"name": "四国めたん", "speaker_uuid": "7ffcb7ce-00ec-4bdc-82cd-45a8889e43ff",
     "styles": [{"name": "ノーマル", "id": 2}]},
    {"name": "ずんだもん", "speaker_uuid": "388f246b-8c41-4ac1-8e2d-5d79f3b49f32",
     "styles": [
         {"name": "ノーマル", "id": 3},
         {"name": "あまあま", "id": 1},
         {"name": "ツンツン", "id": 7},
     ]},
]


def generate_silence_wav(duration_sec: float = 0.5, sample_rate: int = 24000) -> bytes:
    """無音の WAV バイト列を生成する"""
    num_samples = int(sample_rate * duration_sec)
    buf = io.BytesIO()
    # WAV header
    data_size = num_samples * 2  # 16-bit mono
    buf.write(b"RIFF")
    buf.write(struct.pack("<I", 36 + data_size))
    buf.write(b"WAVE")
    buf.write(b"fmt ")
    buf.write(struct.pack("<I", 16))          # chunk size
    buf.write(struct.pack("<H", 1))           # PCM
    buf.write(struct.pack("<H", 1))           # mono
    buf.write(struct.pack("<I", sample_rate))
    buf.write(struct.pack("<I", sample_rate * 2))
    buf.write(struct.pack("<H", 2))           # block align
    buf.write(struct.pack("<H", 16))          # bits per sample
    buf.write(b"data")
    buf.write(struct.pack("<I", data_size))
    buf.write(b"\x00" * data_size)
    return buf.getvalue()


@app.route("/speakers", methods=["GET"])
def speakers():
    return jsonify(SPEAKERS)


@app.route("/audio_query", methods=["POST"])
def audio_query():
    """音声クエリ生成 (ダミー)"""
    text = request.args.get("text", "")
    speaker = request.args.get("speaker", 3)
    return jsonify({
        "accent_phrases": [],
        "speedScale": 1.0,
        "pitchScale": 0.0,
        "intonationScale": 1.0,
        "volumeScale": 1.0,
        "prePhonemeLength": 0.1,
        "postPhonemeLength": 0.1,
        "outputSamplingRate": 24000,
        "outputStereo": False,
        "kana": text,
    })


@app.route("/synthesis", methods=["POST"])
def synthesis():
    """音声合成 (無音 WAV を返す)"""
    speaker = request.args.get("speaker", 3)
    wav_bytes = generate_silence_wav(0.5)
    return Response(wav_bytes, mimetype="audio/wav")


@app.route("/version", methods=["GET"])
def version():
    return jsonify("0.21.1-mock")


@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "status": "ok",
        "mock": True,
        "message": "VOICEVOX Engine Mock Server for testing",
    })


if __name__ == "__main__":
    print("[VOICEVOX Mock] Starting on port 50021...")
    app.run(host="0.0.0.0", port=50021)
