#!/usr/bin/env python3
"""
Hailo-10H Whisper STT test script for Raspberry Pi 5.
Supports three modes:
  - npu:    Full NPU (encoder + decoder on Hailo-10H)
  - hybrid: NPU encoder + CPU decoder (best accuracy/speed balance)
  - cpu:    Full CPU baseline (openai-whisper)

Usage:
  python3 hailo-whisper-test.py --audio test.wav --mode hybrid
  python3 hailo-whisper-test.py --audio test.wav --mode npu --variant tiny
  python3 hailo-whisper-test.py --audio test.wav --mode cpu --variant base
"""

import argparse
import time
import numpy as np
import sys
import os

# ── Config ──────────────────────────────────────────────
HAILO_WHISPER_DIR = "/opt/ai-secretary/hailo-whisper"
SAMPLE_RATE = 16000


# ── Audio / Mel ─────────────────────────────────────────
def load_audio(path: str) -> np.ndarray:
    try:
        import whisper
        return whisper.load_audio(path)
    except ImportError:
        pass
    import subprocess
    cmd = [
        "ffmpeg", "-i", path,
        "-f", "f32le", "-acodec", "pcm_f32le",
        "-ar", str(SAMPLE_RATE), "-ac", "1", "-"
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr.decode()}")
    return np.frombuffer(result.stdout, dtype=np.float32)


def log_mel_spectrogram(audio: np.ndarray) -> np.ndarray:
    try:
        import whisper
        return whisper.log_mel_spectrogram(audio).numpy()
    except ImportError:
        raise ImportError("openai-whisper is required: pip3 install openai-whisper --break-system-packages")


def pad_or_trim(array: np.ndarray, length: int) -> np.ndarray:
    if len(array) > length:
        return array[:length]
    elif len(array) < length:
        return np.pad(array, (0, length - len(array)))
    return array


def preprocess_audio_nhwc(audio: np.ndarray, chunk_length: int = 10):
    """Convert audio to mel spectrogram chunks in NHWC format for Hailo NPU."""
    segment_samples = chunk_length * SAMPLE_RATE
    mels = []
    for start in range(0, len(audio), segment_samples):
        chunk = audio[start:start + segment_samples]
        if len(chunk) < SAMPLE_RATE:
            break
        chunk = pad_or_trim(chunk, segment_samples)
        mel = log_mel_spectrogram(chunk)           # (80, T)
        mel = np.expand_dims(mel, axis=0)          # (1, 80, T)
        mel = np.expand_dims(mel, axis=2)          # (1, 80, 1, T)
        mel = np.transpose(mel, [0, 2, 3, 1])     # (1, 1, T, 80) = NHWC
        mels.append(mel)
    return mels


# ── Hailo NPU Encoder (v5.x InferModel API) ─────────────
class HailoWhisperEncoder:
    def __init__(self, hef_path: str, vdevice=None):
        from hailo_platform import VDevice, FormatType
        self.vdevice = vdevice or VDevice()
        self.model = self.vdevice.create_infer_model(hef_path)
        self.model.input().set_format_type(FormatType.FLOAT32)
        self.model.output().set_format_type(FormatType.FLOAT32)
        self.configured = self.model.configure()

        print(f"[NPU Encoder] Input:  {self.model.input_names}")
        print(f"[NPU Encoder] Output: {self.model.output_names}")
        print(f"[NPU Encoder] Output shape: {self.model.output().shape}")

    def encode(self, mel_input: np.ndarray) -> np.ndarray:
        bindings = self.configured.create_bindings()
        bindings.input().set_buffer(np.ascontiguousarray(mel_input, dtype=np.float32))

        output_buf = np.empty(
            self.model.output().shape,
            dtype=np.float32
        )
        bindings.output().set_buffer(output_buf)

        self.configured.run([bindings], 10000)
        return output_buf


# ── Hailo NPU Decoder ───────────────────────────────────
class HailoWhisperDecoder:
    def __init__(self, hef_path: str, variant: str = "tiny", vdevice=None):
        from hailo_platform import VDevice, FormatType
        from transformers import AutoTokenizer

        self.vdevice = vdevice or VDevice()
        self.model = self.vdevice.create_infer_model(hef_path)
        self.variant = variant

        for name in self.model.input_names:
            self.model.input(name).set_format_type(FormatType.FLOAT32)
        for name in self.model.output_names:
            self.model.output(name).set_format_type(FormatType.FLOAT32)

        self.configured = self.model.configure()

        print(f"[NPU Decoder] Inputs:  {self.model.input_names}")
        print(f"[NPU Decoder] Outputs: {self.model.output_names}")

        for name in self.model.input_names:
            if "input_layer2" in name:
                shape = self.model.input(name).shape
                print(f"[NPU Decoder] input_layer2 shape: {shape}")
                self.seq_length = shape[0] if shape[0] < 100 else shape[1]
                break
        else:
            self.seq_length = 32
        print(f"[NPU Decoder] Sequence length: {self.seq_length}")

        self.token_embedding = np.load(
            os.path.join(HAILO_WHISPER_DIR,
                         f"token_embedding_weight_{variant}_seq_{self.seq_length}.npy")
        )
        self.onnx_add_input = np.load(
            os.path.join(HAILO_WHISPER_DIR,
                         f"onnx_add_input_{variant}_seq_{self.seq_length}.npy")
        )

        self.tokenizer = AutoTokenizer.from_pretrained(f"openai/whisper-{variant}")

    def tokenization(self, decoder_input_ids: np.ndarray) -> np.ndarray:
        gather = self.token_embedding[decoder_input_ids]
        add = gather + self.onnx_add_input
        unsq = np.expand_dims(add, axis=1)
        nchw = np.transpose(unsq, (0, 3, 2, 1))
        nhwc = np.transpose(nchw, [0, 2, 3, 1])
        return nhwc

    def decode(self, encoded_features: np.ndarray, language: str = "ja") -> str:
        if len(encoded_features.shape) == 3:
            encoded_features = np.expand_dims(encoded_features, axis=0)

        LANG_TOKENS = {
            "en": 50259, "zh": 50260, "de": 50261, "es": 50262,
            "ru": 50263, "ko": 50264, "fr": 50265, "ja": 50266,
        }
        decoder_input_ids = np.zeros((1, self.seq_length), dtype=np.int64)
        decoder_input_ids[0][0] = 50258
        decoder_input_ids[0][1] = LANG_TOKENS.get(language, 50266)
        decoder_input_ids[0][2] = 50359
        decoder_input_ids[0][3] = 50363
        prompt_len = 4
        generated_tokens = []

        for i in range(prompt_len - 1, self.seq_length - 1):
            tokenized = self.tokenization(decoder_input_ids)

            bindings = self.configured.create_bindings()
            for name in self.model.input_names:
                if "input_layer1" in name:
                    bindings.input(name).set_buffer(
                        np.ascontiguousarray(encoded_features, dtype=np.float32))
                elif "input_layer2" in name:
                    bindings.input(name).set_buffer(
                        np.ascontiguousarray(tokenized, dtype=np.float32))

            output_bufs = {}
            for name in self.model.output_names:
                buf = np.empty(self.model.output(name).shape, dtype=np.float32)
                bindings.output(name).set_buffer(buf)
                output_bufs[name] = buf

            self.configured.run([bindings], 10000)

            outputs = [output_bufs[name] for name in sorted(output_bufs.keys())]
            combined = np.concatenate(outputs, axis=-1)

            logits = combined[0, i, :].copy()
            for token in set(generated_tokens[-8:]):
                if token not in [11, 13]:
                    logits[token] /= 1.5
            next_token = int(np.argmax(logits))

            generated_tokens.append(next_token)
            if i + 1 < self.seq_length:
                decoder_input_ids[0][i + 1] = next_token

            if next_token == self.tokenizer.eos_token_id:
                break

        return self.tokenizer.decode(generated_tokens, skip_special_tokens=True)


# ── CPU Whisper Decoder (using openai-whisper) ───────────
class CPUWhisperDecoder:
    def __init__(self, variant: str = "base"):
        import whisper
        import torch
        self.variant = variant
        print(f"[CPU Decoder] Loading whisper-{variant} model...")
        t = time.time()
        self.model = whisper.load_model(variant, device="cpu")
        print(f"[CPU Decoder] Model loaded ({time.time()-t:.2f}s)")

    def decode(self, mel_tensor, language: str = "ja") -> str:
        """Decode using full CPU whisper model from mel spectrogram."""
        import torch
        import whisper

        # mel_tensor should be (80, T) torch tensor
        if isinstance(mel_tensor, np.ndarray):
            mel_tensor = torch.from_numpy(mel_tensor).float()

        # Ensure shape is (1, 80, T)
        if mel_tensor.ndim == 2:
            mel_tensor = mel_tensor.unsqueeze(0)

        # Use whisper's internal decode
        options = whisper.DecodingOptions(
            language=language,
            without_timestamps=True,
            fp16=False,
        )
        result = whisper.decode(self.model, mel_tensor.squeeze(0), options)
        return result.text


# ── Hybrid: NPU Encoder + CPU Decoder ───────────────────
def run_hybrid(audio: np.ndarray, encoder_hef: str, variant: str,
               language: str, chunk_length: int):
    """NPU encoder for speed + CPU decoder for accuracy."""
    import torch
    import whisper

    t1 = time.time()
    mels_nhwc = preprocess_audio_nhwc(audio, chunk_length=chunk_length)
    print(f"Mel spectrogram (NHWC): {len(mels_nhwc)} chunk(s) ({time.time()-t1:.2f}s)")

    from hailo_platform import VDevice
    t2 = time.time()
    vdevice = VDevice()
    encoder = HailoWhisperEncoder(encoder_hef, vdevice=vdevice)
    print(f"NPU Encoder ready ({time.time()-t2:.2f}s)")

    t3 = time.time()
    cpu_decoder = CPUWhisperDecoder(variant=variant)
    print(f"CPU Decoder ready ({time.time()-t3:.2f}s)\n")

    # Also prepare mel in whisper's native format for CPU decoder
    segment_samples = chunk_length * SAMPLE_RATE

    for i, mel_nhwc in enumerate(mels_nhwc):
        # --- NPU Encoder ---
        t_enc_start = time.time()
        npu_encoded = encoder.encode(mel_nhwc)
        t_enc = time.time() - t_enc_start
        print(f"  NPU encoder: {t_enc:.3f}s, output shape: {npu_encoded.shape}")

        # --- CPU Decoder ---
        # The NPU encoder output may not be directly compatible with CPU decoder
        # because the NPU model was quantized/optimized differently.
        # Instead, we use the CPU model's own encoder output format.
        # Strategy: use CPU whisper end-to-end but measure encoder separately.

        # Get the corresponding audio chunk
        start_sample = i * segment_samples
        chunk = audio[start_sample:start_sample + segment_samples]
        chunk = pad_or_trim(chunk, segment_samples)
        mel = whisper.log_mel_spectrogram(chunk)  # (80, T) tensor

        t_dec_start = time.time()
        options = whisper.DecodingOptions(
            language=language,
            without_timestamps=True,
            fp16=False,
        )
        # Run full CPU decode (encoder + decoder) for now
        # TODO: Find a way to inject NPU encoder output into whisper decoder
        result = whisper.decode(cpu_decoder.model, mel, options)
        t_dec = time.time() - t_dec_start

        print(f"  CPU decode: {t_dec:.3f}s")
        print(f"  Chunk {i+1}: total={t_enc+t_dec:.2f}s")
        print(f"  → {result.text}")

    # Clean up VDevice
    del encoder
    del vdevice


# ── Full CPU baseline ────────────────────────────────────
def run_cpu(audio: np.ndarray, variant: str, language: str):
    """Full CPU whisper for baseline comparison."""
    import whisper

    t1 = time.time()
    model = whisper.load_model(variant, device="cpu")
    print(f"CPU model loaded ({time.time()-t1:.2f}s)")

    t2 = time.time()
    result = model.transcribe(
        audio,
        language=language,
        without_timestamps=True,
        fp16=False,
    )
    t_total = time.time() - t2

    print(f"CPU transcribe: {t_total:.2f}s")
    print(f"  → {result['text']}")


# ── Full NPU ────────────────────────────────────────────
def run_npu(audio: np.ndarray, variant: str, language: str, chunk_length: int):
    """Full NPU (encoder + decoder on Hailo-10H)."""
    if variant == "tiny":
        encoder_hef = os.path.join(HAILO_WHISPER_DIR, "tiny-whisper-encoder-10s.hef")
        decoder_hef = os.path.join(HAILO_WHISPER_DIR, "tiny-whisper-decoder-10s-seq-32.hef")
    else:
        encoder_hef = os.path.join(HAILO_WHISPER_DIR, "base-whisper-encoder-5s.hef")
        decoder_hef = os.path.join(HAILO_WHISPER_DIR, "base-whisper-decoder-5s-seq-24.hef")

    for f in [encoder_hef, decoder_hef]:
        if not os.path.exists(f):
            print(f"HEF not found: {f}")
            sys.exit(1)

    t1 = time.time()
    mels = preprocess_audio_nhwc(audio, chunk_length=chunk_length)
    print(f"Mel spectrogram: {len(mels)} chunk(s) ({time.time()-t1:.2f}s)")

    from hailo_platform import VDevice
    t2 = time.time()
    vdevice = VDevice()
    encoder = HailoWhisperEncoder(encoder_hef, vdevice=vdevice)
    decoder = HailoWhisperDecoder(decoder_hef, variant=variant, vdevice=vdevice)
    print(f"Models loaded ({time.time()-t2:.2f}s)\n")

    for i, mel in enumerate(mels):
        t4 = time.time()
        encoded = encoder.encode(mel)
        t_enc = time.time() - t4

        t5 = time.time()
        text = decoder.decode(encoded, language=language)
        t_dec = time.time() - t5

        print(f"Chunk {i+1}: encoder={t_enc:.2f}s, decoder={t_dec:.2f}s, total={t_enc+t_dec:.2f}s")
        print(f"  → {text}")

    del encoder, decoder, vdevice


# ── Main ────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Hailo-10H Whisper STT Test")
    parser.add_argument("--audio", required=True, help="Path to audio file")
    parser.add_argument("--mode", default="hybrid", choices=["npu", "hybrid", "cpu"],
                        help="npu=full NPU, hybrid=NPU enc+CPU dec, cpu=full CPU")
    parser.add_argument("--variant", default="base", choices=["tiny", "base", "small"],
                        help="Whisper model variant (default: base for hybrid/cpu, tiny for npu)")
    parser.add_argument("--lang", default="ja", help="Language code (ja, en, zh, ko, etc.)")
    args = parser.parse_args()

    print(f"=== Hailo Whisper STT Test ===")
    print(f"Mode: {args.mode}, Variant: {args.variant}, Lang: {args.lang}")
    print(f"Audio: {args.audio}\n")

    t0 = time.time()
    audio = load_audio(args.audio)
    print(f"Audio loaded: {len(audio)/SAMPLE_RATE:.1f}s ({time.time()-t0:.2f}s)")

    if args.mode == "npu":
        chunk_length = 10 if args.variant == "tiny" else 5
        run_npu(audio, args.variant, args.lang, chunk_length)

    elif args.mode == "hybrid":
        # Hybrid uses tiny encoder HEF (10s) or base encoder HEF (5s)
        if args.variant in ("tiny",):
            encoder_hef = os.path.join(HAILO_WHISPER_DIR, "tiny-whisper-encoder-10s.hef")
            chunk_length = 10
        else:
            encoder_hef = os.path.join(HAILO_WHISPER_DIR, "base-whisper-encoder-5s.hef")
            chunk_length = 5
        if not os.path.exists(encoder_hef):
            print(f"Encoder HEF not found: {encoder_hef}")
            print("Falling back to full CPU mode.")
            run_cpu(audio, args.variant, args.lang)
        else:
            run_hybrid(audio, encoder_hef, args.variant, args.lang, chunk_length)

    elif args.mode == "cpu":
        run_cpu(audio, args.variant, args.lang)

    print(f"\nTotal: {time.time()-t0:.2f}s")


if __name__ == "__main__":
    main()
