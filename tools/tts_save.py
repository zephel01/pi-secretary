#!/usr/bin/env python3
"""
tts_save.py — テキストをTTSで音声ファイルに保存するツール

使い方:
  # VOICEVOX (ずんだもん)
  python tts_save.py "こんにちは、ボクはずんだもんなのだ" -o demo_zundamon.wav

  # VOICEVOX (スピーカーID指定)
  python tts_save.py "起動しましたわ" -o demo_metan.wav --speaker 2

  # COEIROINK (リリンちゃん)
  python tts_save.py "やっほー！あたしリリンだよ" -o demo_lilin.wav --engine coeiroink

  # 台本ファイルから一括生成
  python tts_save.py --script demo_script.txt --outdir demo_wavs/
"""

import argparse
import os
import sys
import requests
import json


def synth_voicevox(text, speaker_id=3, host="http://127.0.0.1:50021"):
    """VOICEVOX で音声合成"""
    # audio_query
    resp = requests.post(
        f"{host}/audio_query",
        params={"text": text, "speaker": speaker_id},
        timeout=30,
    )
    resp.raise_for_status()
    query = resp.json()

    # synthesis
    resp = requests.post(
        f"{host}/synthesis",
        params={"speaker": speaker_id},
        json=query,
        timeout=60,
    )
    resp.raise_for_status()
    return resp.content


def synth_coeiroink(
    text,
    speaker_uuid="cb11bdbd-78fc-4f16-b528-a400bae1782d",
    style_id=92,
    host="http://192.168.4.85:50033",
):
    """COEIROINK で音声合成"""
    payload = {
        "speakerUuid": speaker_uuid,
        "styleId": style_id,
        "text": text,
        "speedScale": 1.0,
        "volumeScale": 1.0,
        "pitchScale": 0.0,
        "intonationScale": 1.0,
        "prePhonemeLength": 0.1,
        "postPhonemeLength": 0.5,
        "outputSamplingRate": 24000,
    }
    resp = requests.post(f"{host}/v1/synthesis", json=payload, timeout=30)
    resp.raise_for_status()
    return resp.content


def save_wav(data, path):
    """WAVデータをファイルに保存"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    print(f"  保存: {path} ({len(data):,} bytes)")


def process_script(script_path, outdir, args):
    """
    台本ファイルから一括生成

    台本フォーマット (1行1発話):
      engine:speaker_or_style:ファイル名:テキスト

    例:
      voicevox:3:zundamon_01:こんにちは、ボクはずんだもんなのだ
      voicevox:2:metan_01:起動しましたわ。何でもお聞きになって
      coeiroink:92:lilin_01:やっほー！あたしリリンだよ！
    """
    os.makedirs(outdir, exist_ok=True)

    with open(script_path, "r", encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip() and not l.startswith("#")]

    for i, line in enumerate(lines):
        parts = line.split(":", 3)
        if len(parts) < 4:
            print(f"  スキップ (フォーマットエラー): {line}")
            continue

        engine, speaker_style, filename, text = parts
        engine = engine.strip().lower()
        outpath = os.path.join(outdir, f"{filename.strip()}.wav")

        print(f"[{i+1}/{len(lines)}] {engine} | {text[:30]}...")

        try:
            if engine == "coeiroink":
                data = synth_coeiroink(
                    text,
                    speaker_uuid=args.coeiroink_uuid,
                    style_id=int(speaker_style.strip()),
                    host=args.coeiroink_url,
                )
            else:
                data = synth_voicevox(
                    text,
                    speaker_id=int(speaker_style.strip()),
                    host=args.voicevox_url,
                )
            save_wav(data, outpath)
        except Exception as e:
            print(f"  エラー: {e}")


def main():
    parser = argparse.ArgumentParser(description="TTS テキスト→音声保存ツール")
    parser.add_argument("text", nargs="?", help="合成するテキスト")
    parser.add_argument("-o", "--output", default="output.wav", help="出力ファイル名")
    parser.add_argument("--engine", default="voicevox", choices=["voicevox", "coeiroink"])
    parser.add_argument("--speaker", type=int, default=3, help="VOICEVOX スピーカーID")
    parser.add_argument("--voicevox-url", default="http://127.0.0.1:50021")
    parser.add_argument("--coeiroink-url", default="http://192.168.4.85:50033")
    parser.add_argument("--coeiroink-uuid", default="cb11bdbd-78fc-4f16-b528-a400bae1782d")
    parser.add_argument("--coeiroink-style", type=int, default=92)
    parser.add_argument("--script", help="台本ファイル (一括生成)")
    parser.add_argument("--outdir", default="demo_wavs", help="台本モード出力先")
    args = parser.parse_args()

    # 台本モード
    if args.script:
        process_script(args.script, args.outdir, args)
        return

    # 単発モード
    if not args.text:
        parser.error("テキストか --script を指定してください")

    print(f"エンジン: {args.engine}")
    print(f"テキスト: {args.text}")

    try:
        if args.engine == "coeiroink":
            data = synth_coeiroink(
                args.text,
                speaker_uuid=args.coeiroink_uuid,
                style_id=args.coeiroink_style,
                host=args.coeiroink_url,
            )
        else:
            data = synth_voicevox(
                args.text,
                speaker_id=args.speaker,
                host=args.voicevox_url,
            )
        save_wav(data, args.output)
    except Exception as e:
        print(f"エラー: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
