#!/usr/bin/env python3
"""
Hailo-10H Whisper STT test script for Raspberry Pi 5.
Uses HEF files converted by hailo-whisper DFC pipeline.
Requires HailoRT v5.x (InferModel API).

Usage:
  python3 hailo-whisper-test.py --audio test.wav
  python3 hailo-whisper-test.py --audio test.wav --variant tiny
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


def preprocess_audio(audio: np.ndarray, chunk_length: int = 10):
    """Convert audio to NHWC mel spectrogram chunks for Hailo."""
    segment_samples = chunk_length * SAMPLE_RATE
    mels = []
    for start in range(0, len(audio), segment_samples):
        chunk = audio[start:start + segment_samples]
        if len(chunk) < SAMPLE_RATE:
            break
        chunk = pad_or_trim(chunk, segment_samples)
        mel = log_mel_spectrogram(chunk)          # (80, T)
        mel = mel[np.newaxis, np.newaxis, :, :]   # (1, 1, 80, T)
        mel = np.transpose(mel, [0, 2, 3, 1])     # (1, 80, T, 1) → NHWC?
        # Actually hailo-whisper uses (1, 1, T, 80) for NHWC
        mel = mel.transpose([0, 3, 2, 1])          # try different order
        # Let's just do what the original code does:
        # mel shape from whisper: (80, T) where T=500 for 10s or T=250 for 5s
        mels.append(mel)
    return mels


def preprocess_audio_v2(audio: np.ndarray, chunk_length: int = 10):
    """Convert audio to mel spectrogram chunks matching hailo-whisper format."""
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


# ── Hailo NPU Inference (v5.x InferModel API) ──────────
class HailoWhisperEncoder:
    def __init__(self, hef_path: str, vdevice=None):
        from hailo_platform import VDevice, FormatType
        self.vdevice = vdevice or VDevice()
        self.model = self.vdevice.create_infer_model(hef_path)
        self.model.input().set_format_type(FormatType.FLOAT32)
        self.model.output().set_format_type(FormatType.FLOAT32)
        self.configured = self.model.configure()

        print(f"[Encoder] Input:  {self.model.input_names}")
        print(f"[Encoder] Output: {self.model.output_names}")

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


class HailoWhisperDecoder:
    def __init__(self, hef_path: str, variant: str = "tiny", vdevice=None):
        from hailo_platform import VDevice, FormatType
        from transformers import AutoTokenizer

        self.vdevice = vdevice or VDevice()
        self.model = self.vdevice.create_infer_model(hef_path)
        self.variant = variant

        # Set all inputs/outputs to float32
        for name in self.model.input_names:
            self.model.input(name).set_format_type(FormatType.FLOAT32)
        for name in self.model.output_names:
            self.model.output(name).set_format_type(FormatType.FLOAT32)

        self.configured = self.model.configure()

        print(f"[Decoder] Inputs:  {self.model.input_names}")
        print(f"[Decoder] Outputs: {self.model.output_names}")

        # Determine sequence length
        # input_layer2 carries the token embeddings
        for name in self.model.input_names:
            if "input_layer2" in name:
                shape = self.model.input(name).shape
                print(f"[Decoder] input_layer2 shape: {shape}")
                # shape is likely (seq, 1, dim) or similar in NHWC
                self.seq_length = shape[0] if shape[0] < 100 else shape[1]
                break
        else:
            self.seq_length = 32
        print(f"[Decoder] Sequence length: {self.seq_length}")

        # Load embedding weights (CPU)
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
        """Token IDs → embeddings (CPU). Returns NHWC array."""
        gather = self.token_embedding[decoder_input_ids]       # (1, seq, dim)
        add = gather + self.onnx_add_input                     # broadcast
        unsq = np.expand_dims(add, axis=1)                     # (1, 1, seq, dim)
        nchw = np.transpose(unsq, (0, 3, 2, 1))               # (1, dim, seq, 1)
        nhwc = np.transpose(nchw, [0, 2, 3, 1])               # (1, seq, 1, dim)
        return nhwc

    def decode(self, encoded_features: np.ndarray) -> str:
        if len(encoded_features.shape) == 3:
            encoded_features = np.expand_dims(encoded_features, axis=0)

        # Start token
        decoder_input_ids = np.zeros((1, self.seq_length), dtype=np.int64)
        decoder_input_ids[0][0] = 50258  # <|startoftranscript|>
        generated_tokens = []

        for i in range(self.seq_length - 1):
            tokenized = self.tokenization(decoder_input_ids)

            bindings = self.configured.create_bindings()

            # Set inputs
            for name in self.model.input_names:
                if "input_layer1" in name:
                    bindings.input(name).set_buffer(
                        np.ascontiguousarray(encoded_features, dtype=np.float32))
                elif "input_layer2" in name:
                    bindings.input(name).set_buffer(
                        np.ascontiguousarray(tokenized, dtype=np.float32))

            # Prepare output buffers
            output_bufs = {}
            for name in self.model.output_names:
                buf = np.empty(self.model.output(name).shape, dtype=np.float32)
                bindings.output(name).set_buffer(buf)
                output_bufs[name] = buf

            self.configured.run([bindings], 10000)

            # Concatenate outputs along last axis
            outputs = [output_bufs[name] for name in sorted(output_bufs.keys())]
            if i == 0:
                print(f"[Decoder] Output shapes: {[o.shape for o in outputs]}")
            combined = np.concatenate(outputs, axis=-1)
            if i == 0:
                print(f"[Decoder] Combined shape: {combined.shape}")

            # Get next token - shape is (1, seq, vocab)
            # batch=0, position=i, all vocab
            logits = combined[0, i, :].copy()
            for token in set(generated_tokens[-8:]):
                if token not in [11, 13]:
                    logits[token] /= 1.5
            next_token = int(np.argmax(logits))

            generated_tokens.append(next_token)
            decoder_input_ids[0][i + 1] = next_token

            if next_token == self.tokenizer.eos_token_id:
                break

        return self.tokenizer.decode(generated_tokens, skip_special_tokens=True)


# ── Main ────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Hailo-10H Whisper STT Test")
    parser.add_argument("--audio", required=True, help="Path to audio file")
    parser.add_argument("--variant", default="tiny", choices=["tiny", "base"])
    args = parser.parse_args()

    if args.variant == "tiny":
        encoder_hef = os.path.join(HAILO_WHISPER_DIR, "tiny-whisper-encoder-10s.hef")
        decoder_hef = os.path.join(HAILO_WHISPER_DIR, "tiny-whisper-decoder-10s-seq-32.hef")
        chunk_length = 10
    else:
        encoder_hef = os.path.join(HAILO_WHISPER_DIR, "base-whisper-encoder-5s.hef")
        decoder_hef = os.path.join(HAILO_WHISPER_DIR, "base-whisper-decoder-5s-seq-24.hef")
        chunk_length = 5

    for f in [encoder_hef, decoder_hef]:
        if not os.path.exists(f):
            print(f"HEF not found: {f}")
            sys.exit(1)

    print(f"=== Hailo Whisper STT Test ({args.variant}) ===")
    print(f"Audio: {args.audio}\n")

    t0 = time.time()
    audio = load_audio(args.audio)
    print(f"Audio loaded: {len(audio)/SAMPLE_RATE:.1f}s ({time.time()-t0:.2f}s)")

    t1 = time.time()
    mels = preprocess_audio_v2(audio, chunk_length=chunk_length)
    print(f"Mel spectrogram: {len(mels)} chunk(s) ({time.time()-t1:.2f}s)")

    from hailo_platform import VDevice
    t2 = time.time()
    vdevice = VDevice()
    print(f"VDevice created ({time.time()-t2:.2f}s)")

    t3 = time.time()
    encoder = HailoWhisperEncoder(encoder_hef, vdevice=vdevice)
    print(f"Encoder loaded ({time.time()-t3:.2f}s)")

    t4_load = time.time()
    decoder = HailoWhisperDecoder(decoder_hef, variant=args.variant, vdevice=vdevice)
    print(f"Decoder loaded ({time.time()-t4_load:.2f}s)\n")

    for i, mel in enumerate(mels):
        t4 = time.time()
        encoded = encoder.encode(mel)
        t_enc = time.time() - t4

        t5 = time.time()
        text = decoder.decode(encoded)
        t_dec = time.time() - t5

        print(f"Chunk {i+1}: encoder={t_enc:.2f}s, decoder={t_dec:.2f}s, total={t_enc+t_dec:.2f}s")
        print(f"  → {text}")

    print(f"\nTotal: {time.time()-t0:.2f}s")


if __name__ == "__main__":
    main()
