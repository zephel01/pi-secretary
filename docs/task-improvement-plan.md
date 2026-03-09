# pi-secretary 改善 実装タスクリスト

## 概要

2つの改善を計画:

1. **hailo-whisper 統合（STT高速化）** — Hailo NPU で Whisper を実行し STT を 9秒→1-2秒に短縮
2. **x86_64 対応** — ARM64 専用のセットアップを x86_64 (ミニPC等) でも動作するようにする

---

## Phase 0: hailo-whisper 統合（STT NPU オフロード）★★★ 最優先

### 背景

- 現在: faster-whisper small (CPU) → **約9秒**（全体20秒の45%を占める最大ボトルネック）
- 目標: hailo-whisper base (NPU) → **約1-2秒**（推定）
- 参照: https://github.com/hailocs/hailo-whisper
- 対応モデル: whisper-tiny, whisper-base（small以上は未対応）
- 対応チップ: Hailo-8, Hailo-8L, Hailo-10H
- 要件: HailoRT 4.20+, DFC 5.x (Hailo-10H の場合)

### Task 0-1: hailo-whisper 環境構築 ★★★

**作業内容**: Pi 5 + Hailo-10H 上で hailo-whisper をセットアップ

```bash
# 1. リポジトリ取得
cd /opt/ai-secretary
git clone https://github.com/hailocs/hailo-whisper.git

# 2. セットアップ
cd hailo-whisper
python3 setup.py
source .venv/bin/activate

# 3. HailoRT / PyHailoRT のバージョン確認
hailortcli fw-control identify  # HailoRT 4.20+ が必要
pip show hailort                # PyHailoRT wheel 確認
```

**確認ポイント**:
- HailoRT のバージョンが 4.20+ か（古い場合はアップデートが必要）
- Hailo DFC 5.x のインストール方法（Hailo Developer Zone からダウンロード）
- Pi 5 の OS イメージに HailoRT が含まれているか、別途インストールか

### Task 0-2: hailo-whisper 単体テスト ★★★

**作業内容**: whisper-base モデルで音声認識の精度と速度を検証

```bash
# テスト音声ファイルで検証
# 1. 処理時間の計測
time python3 -m hailo_whisper.transcribe --model base --audio test.wav

# 2. 日本語認識精度の確認
#    - 「こんにちは」「今日は寒いですね」等の簡単なフレーズ
#    - faster-whisper base (CPU 4秒) との精度比較

# 3. メモリ使用量・CPU負荷の確認
```

**比較テーブル**（埋める）:

| 項目 | faster-whisper base (CPU) | hailo-whisper base (NPU) |
|---|---|---|
| 処理時間 | 約4秒 | ? |
| 日本語精度 | ○ | ? |
| CPU負荷 | 高 | ? (低いはず) |
| メモリ | 中 | ? |

### Task 0-3: voice-bridge への hailo-whisper 統合 ★★★

**作業内容**: main_headless.py の STT バックエンドを切り替え可能にする

**変更方針**:

```python
# voice-bridge.env に追加
STT_BACKEND=hailo  # "hailo" or "faster-whisper"

# main_headless.py
if STT_BACKEND == "hailo":
    # hailo-whisper を使用
    from hailo_whisper import transcribe
    # NPU で推論 → リサンプルは hailo-whisper 側が処理するか確認
else:
    # 既存の faster-whisper を使用（フォールバック）
    from faster_whisper import WhisperModel
```

**確認ポイント**:
- hailo-whisper の Python API インターフェース（transcribe 関数の引数・戻り値）
- 入力音声のサンプルレート要件（16000Hz? hailo 側でリサンプルするか?）
- ストリーミング対応の有無（VAD + 区間検出が必要）
- faster-whisper の `initial_prompt` 相当の機能があるか

### Task 0-4: setup.sh に hailo-whisper インストール追加

**作業内容**: Hailo NPU 検出時に hailo-whisper を自動インストール

```bash
# setup.sh に追加
if [[ "$HAS_HAILO" == true ]]; then
  section "hailo-whisper セットアップ"
  cd /opt/ai-secretary
  git clone https://github.com/hailocs/hailo-whisper.git
  cd hailo-whisper && python3 setup.py
  # voice-bridge.env に STT_BACKEND=hailo を設定
fi
```

### Task 0-5: パフォーマンス検証・ドキュメント更新

**作業内容**: 統合後の全体パイプライン計測

目標パイプライン:
```
発話 → STT (hailo-whisper base, NPU) 1-2秒
     → LLM (qwen2.5:1.5b, CPU)       3秒
     → TTS (VOICEVOX, CPU)            7秒
     → 合計: 約11-12秒 (現在20秒から40%短縮)
```

- README.md パフォーマンスセクション更新
- docs/note/note-performance.md 更新

---

## Phase 1〜3: x86_64 対応

### 変更対象ファイルと影響度

| ファイル | 変更内容 | 優先度 |
|---|---|---|
| setup.sh | アーキテクチャ検出・分岐 | ★★★ 最重要 |
| systemd/voicevox.service | バイナリパスの動的化 | ★★★ |
| test-env/Dockerfile | マルチアーキテクチャ対応 | ★★ |
| test-env/docker-compose.yml | platform 指定の条件化 | ★★ |
| test-env/Dockerfile.voicevox-mock | マルチアーキテクチャ対応 | ★★ |
| test-env/run-tests.sh | アーキテクチャチェック緩和 | ★★ |
| config/voice-bridge.env | デバイスパスの説明追加 | ★ |
| README.md | x86_64 対応の記載 | ★ |

---

## タスク一覧（x86_64 対応）

### Task 1: setup.sh — アーキテクチャ自動検出と分岐 ★★★

**現状**: `[[ $(uname -m) == "aarch64" ]] || error` で ARM64 以外を拒否

**変更内容**:

```bash
# 変更前 (Line 18)
[[ $(uname -m) == "aarch64" ]] || error "64-bit OS が必要です (aarch64)"

# 変更後
ARCH=$(uname -m)
case "$ARCH" in
  aarch64|arm64) PLATFORM="arm64" ;;
  x86_64)        PLATFORM="x64"   ;;
  *)             error "未対応アーキテクチャ: $ARCH (aarch64 または x86_64 が必要)" ;;
esac
info "アーキテクチャ: $ARCH → PLATFORM=$PLATFORM"
```

**VOICEVOX ダウンロード分岐** (Lines 233-234):

```bash
# 変更前
VOICEVOX_ARCHIVE="voicevox_engine-linux-cpu-arm64-${VOICEVOX_VERSION}.7z.001"

# 変更後
VOICEVOX_ARCHIVE="voicevox_engine-linux-cpu-${PLATFORM}-${VOICEVOX_VERSION}.7z.001"
```

**確認ポイント**:
- VOICEVOX の x86_64 リリースが `linux-cpu-x64` か `linux-cpu-x86_64` か確認が必要
  → GitHub releases ページで実際のファイル名を確認する
- VOICEVOX x86_64 版は `.7z.001` 分割ではなく単一ファイルの可能性あり

---

### Task 2: systemd/voicevox.service — バイナリパスの動的化 ★★★

**現状**: `ExecStart=/opt/ai-secretary/voicevox/engine/linux-cpu-arm64/run` と固定

**変更方針**: setup.sh で環境変数ファイルを生成し、service から参照

```ini
# /opt/ai-secretary/voicevox/platform.env (setup.sh が生成)
VOICEVOX_PLATFORM=arm64

# voicevox.service
EnvironmentFile=/opt/ai-secretary/voicevox/platform.env
ExecStart=/opt/ai-secretary/voicevox/engine/linux-cpu-${VOICEVOX_PLATFORM}/run \
    --host 127.0.0.1 --port 50021 --cpu_num_threads %THREADS%
```

**補足**:
- `cpu_num_threads` も x86_64 ではコア数に応じて変更したい（Pi5: 2、ミニPC: 4〜8）
- setup.sh で `nproc` から自動算出する or env に設定

---

### Task 3: setup.sh — VOICEVOX cpu_num_threads の自動設定

**現状**: `cpu_num_threads=2` 固定（Pi 5 の 4 コアに合わせた設定）

**変更内容**:

```bash
# コア数に応じて自動設定 (半分のコアを割り当て、最低2)
TOTAL_CORES=$(nproc)
VOICEVOX_THREADS=$(( TOTAL_CORES / 2 ))
[[ $VOICEVOX_THREADS -lt 2 ]] && VOICEVOX_THREADS=2
info "VOICEVOX cpu_num_threads: $VOICEVOX_THREADS (全 $TOTAL_CORES コア)"
```

→ platform.env に `VOICEVOX_THREADS=N` として書き出す

---

### Task 4: test-env/ — マルチアーキテクチャ Docker 対応 ★★

#### 4a. test-env/Dockerfile

```dockerfile
# 変更前
FROM --platform=linux/arm64 debian:bookworm-slim

# 変更後 (ビルド引数で制御)
ARG TARGETPLATFORM=linux/arm64
FROM --platform=${TARGETPLATFORM} debian:bookworm-slim
```

APT リポジトリの `arch=arm64` も分岐:

```dockerfile
ARG DPKG_ARCH=arm64
RUN ... echo "deb [arch=${DPKG_ARCH} signed-by=...] ..." ...
```

#### 4b. test-env/docker-compose.yml

```yaml
# 変更前
platform: linux/arm64

# 変更後: platform 指定を削除してホストに合わせる、または .env で制御
platform: ${DOCKER_PLATFORM:-linux/arm64}
```

#### 4c. test-env/Dockerfile.voicevox-mock

```dockerfile
# 変更前
FROM --platform=linux/arm64 python:3.11-slim-bookworm

# 変更後
ARG TARGETPLATFORM=linux/arm64
FROM --platform=${TARGETPLATFORM} python:3.11-slim-bookworm
```

---

### Task 5: test-env/run-tests.sh — アーキテクチャチェック緩和 ★★

```bash
# 変更前
if [[ "$ARCH" == "aarch64" ]] || [[ "$ARCH" == "arm64" ]]; then
  pass "アーキテクチャ: $ARCH (ARM64)"
else
  fail "アーキテクチャ: $ARCH (ARM64 が期待値)"
fi

# 変更後
case "$ARCH" in
  aarch64|arm64) pass "アーキテクチャ: $ARCH (ARM64)" ;;
  x86_64)        pass "アーキテクチャ: $ARCH (x86_64)" ;;
  *)             fail "アーキテクチャ: $ARCH (ARM64 または x86_64 が期待値)" ;;
esac
```

---

### Task 6: config/voice-bridge.env — デバイス設定の説明追加 ★

**現状**: `AUDIO_INPUT_DEVICE=plughw:2,0` が Pi 5 の USB スピーカーフォン固定

**変更内容**: コメントで x86_64 での設定方法を追記

```bash
# USB オーディオデバイスのALSA名
# 確認方法: aplay -l / arecord -l
# Pi 5 + Anker PowerConf S3 の場合: plughw:2,0
# x86_64 ミニPC の場合: plughw:1,0 など (環境依存)
AUDIO_INPUT_DEVICE=plughw:2,0
AUDIO_OUTPUT_DEVICE=plughw:2,0
```

---

### Task 7: Hailo NPU の条件分岐

**現状**: hailo-proxy.service は Hailo の有無に関係なく存在（Ollama への proxy として機能）

**変更内容**: setup.sh で Hailo の有無を検出

```bash
# Hailo NPU 検出
if lspci 2>/dev/null | grep -qi hailo || ls /dev/hailo* 2>/dev/null; then
  HAS_HAILO=true
  info "Hailo NPU を検出しました"
else
  HAS_HAILO=false
  info "Hailo NPU なし — CPU推論のみ"
fi
```

→ Hailo がない x86_64 環境では hailo 関連パッケージのインストールをスキップ

---

### Task 8: README.md — x86_64 対応の記載 ★

**追記内容**:
- 対応アーキテクチャに x86_64 を追加
- x86_64 での期待パフォーマンス（概算）
- セットアップ手順は同じ `setup.sh` で自動判定される旨

---

## 実装順序（推奨）

```
Phase 0: hailo-whisper 統合 ★★★ 最優先（STT 9秒→1-2秒）
  0-1. hailo-whisper 環境構築
  0-2. 単体テスト（精度・速度検証）
  0-3. voice-bridge 統合（STT バックエンド切り替え）
  0-4. setup.sh にインストール追加
  0-5. パフォーマンス検証・ドキュメント更新

Phase 1: x86_64 コア対応（これだけで x86_64 で動く）
  1. Task 1: setup.sh アーキテクチャ検出
  2. Task 2: voicevox.service パス動的化
  3. Task 3: cpu_num_threads 自動設定

Phase 2: x86_64 テスト環境対応
  4. Task 4: Docker マルチアーキテクチャ
  5. Task 5: テストスクリプト修正

Phase 3: 仕上げ
  6. Task 6: voice-bridge.env コメント
  7. Task 7: Hailo 条件分岐
  8. Task 8: README 更新
```

## 事前確認事項

### Phase 0 (hailo-whisper)
- [ ] HailoRT バージョン確認（4.20+ 必要）
- [ ] Hailo DFC 5.x のインストール（Hailo-10H 用）
- [ ] hailo-whisper の Python API 仕様確認
- [ ] 日本語認識精度の検証（base モデル）
- [ ] 入力サンプルレート要件の確認（16000Hz? 48000Hz?）

### Phase 1〜3 (x86_64)
- [ ] VOICEVOX x86_64 リリースのファイル名を確認
      → `voicevox_engine-linux-cpu-x64-X.X.X.7z.001` ？
- [ ] x86_64 テスト環境の用意（ミニPC or VM）
- [ ] faster-whisper の x86_64 での CUDA 対応を検討するか？
