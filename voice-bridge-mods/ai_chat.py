"""
ai_chat.py — デュアル LLM Chat クライアント + ウェブ検索 + メモリ連携

デュアルLLM構成:
  - OpenClaw (ローカル LLM): 通常の秘書業務・作業指示
  - クラウド LLM (GLM, Gemini 等): ウェブ検索が必要な質問

検索結果は OpenClaw のメモリ (memory/YYYY-MM-DD.md) に保存され、
後から OpenClaw が参照できる。

対応バックエンド:
  - OpenAI API (GPT-4o, etc.)
  - OpenClaw Gateway (ローカル LLM)
  - Google Gemini API
  - その他 OpenAI 互換 API (GLM, Groq, Together, etc.)

ウェブ検索:
  - DuckDuckGo (APIキー不要) を使って検索が必要な質問に自動対応

環境変数:
  OPENAI_API_BASE     — OpenClaw / ローカル LLM エンドポイント (default: http://127.0.0.1:18789/v1)
  OPENAI_API_KEY      — ローカル LLM API キー
  OPENAI_MODEL        — ローカル LLM モデル名
  LLM_BACKEND         — ローカルバックエンド種別: openai / gemini (default: openai)
  CLOUD_LLM_BASE      — クラウド LLM エンドポイント (検索時に使用)
  CLOUD_LLM_KEY       — クラウド LLM API キー
  CLOUD_LLM_MODEL     — クラウド LLM モデル名
  CLOUD_LLM_BACKEND   — クラウドバックエンド種別: openai / gemini (default: openai)
  GEMINI_API_KEY      — Gemini API キー
  GEMINI_MODEL        — Gemini モデル名 (default: gemini-2.0-flash)
  WEB_SEARCH          — 検索機能: on / off (default: on)
  OPENCLAW_MEMORY_DIR — OpenClaw メモリディレクトリ (default: /opt/ai-secretary/openclaw/memory)
  CHAT_MAX_HISTORY    — 会話履歴の最大数 (default: 20)
  CHAT_TIMEOUT        — API タイムアウト秒数 (default: 30)
"""

import os
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Generator

logger = logging.getLogger(__name__)

# --- 設定 ---
# ローカル LLM (OpenClaw)
API_BASE = os.getenv("OPENAI_API_BASE", "http://127.0.0.1:18789/v1")
API_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL = os.getenv("OPENAI_MODEL", "openclaw")
LLM_BACKEND = os.getenv("LLM_BACKEND", "openai")

# クラウド LLM (検索時)
CLOUD_LLM_BASE = os.getenv("CLOUD_LLM_BASE", "")
CLOUD_LLM_KEY = os.getenv("CLOUD_LLM_KEY", "")
CLOUD_LLM_MODEL = os.getenv("CLOUD_LLM_MODEL", "")
CLOUD_LLM_BACKEND = os.getenv("CLOUD_LLM_BACKEND", "openai")

# Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

# 共通
WEB_SEARCH = os.getenv("WEB_SEARCH", "on").lower() in ("on", "true", "1", "yes")
MAX_HISTORY = int(os.getenv("CHAT_MAX_HISTORY", "20"))
TIMEOUT = int(os.getenv("CHAT_TIMEOUT", "30"))
CHARACTER = os.getenv("CHARACTER", "zundamon")

# OpenClaw メモリ
OPENCLAW_MEMORY_DIR = os.getenv(
    "OPENCLAW_MEMORY_DIR",
    "/opt/ai-secretary/openclaw/memory",
)

# デュアルLLM が有効か（クラウド側が設定されているか）
DUAL_LLM_ENABLED = bool(CLOUD_LLM_BASE and CLOUD_LLM_KEY and CLOUD_LLM_MODEL)

# --- キャラクタープロファイル ---
CHARACTER_PROFILES = {
    "zundamon": {
        "name": "ずんだもん",
        "prompt": (
            "あなたは「ずんだもん」という名前のAI秘書なのだ。\n"
            "ずんだ餅の精霊で、東北地方を応援するキャラクターなのだ。\n"
            "一人称は「ボク」を使うのだ。\n"
            "語尾は必ず「〜のだ」「〜なのだ」を使うのだ。\n"
            "例: 「明日は晴れるのだ！」「了解なのだ」「調べてみたのだ」\n"
            "予定やToDoは短く整理して返すのだ。\n"
            "明るく元気で、ちょっとドジなところがあるのだ。"
        ),
    },
    "metan": {
        "name": "四国めたん",
        "prompt": (
            "あなたは「四国めたん」という名前のAI秘書です。\n"
            "四国地方を応援する、お嬢様風のキャラクターです。\n"
            "一人称は「わたくし」を使います。\n"
            "丁寧で上品な話し方をしますが、時々毒舌が出ます。\n"
            "例: 「明日は晴れますわ」「承知いたしましたわ」\n"
            "予定やToDoは短く整理して返します。"
        ),
    },
    "tsumugi": {
        "name": "春日部つむぎ",
        "prompt": (
            "あなたは「春日部つむぎ」という名前のAI秘書だよ〜！\n"
            "埼玉県春日部市を応援する、明るいギャル風キャラクターだよ。\n"
            "一人称は「あたし」を使うよ。\n"
            "フレンドリーで、語尾に「〜だよ」「〜じゃん」をよく使うよ。\n"
            "例: 「明日は晴れるっぽいよ〜」「りょ〜かい！」\n"
            "予定やToDoは短く整理して返すよ。"
        ),
    },
    "lilin": {
        "name": "リリンちゃん",
        "prompt": (
            "あなたは「リリンちゃん」という名前のAI秘書だよ！\n"
            "ちょっと生意気で小悪魔的な女の子キャラクターだよ。\n"
            "一人称は「あたし」を使うよ。\n"
            "フレンドリーだけどちょっとSっ気がある話し方をするよ。\n"
            "例: 「え〜、そんなことも知らないの〜？」「しょうがないな〜、教えてあげるよ」\n"
            "予定やToDoは短く整理して返すよ。"
        ),
    },
    "normal": {
        "name": "AI秘書",
        "prompt": (
            "あなたは優秀なAI秘書です。\n"
            "丁寧で簡潔な日本語で応答してください。\n"
            "予定やToDoは短く整理して返してください。\n"
            "質問には正確かつ分かりやすく答えてください。"
        ),
    },
}

# 検索判定用キーワード (高速フィルタ, LLM 判定前に使用)
SEARCH_HINT_KEYWORDS = [
    "検索", "調べて", "ググ", "最新", "ニュース", "天気", "今日",
    "現在", "いつ", "何時", "価格", "値段", "株価", "為替",
    "スコア", "結果", "誰が", "どこで", "何が",
    "search", "latest", "news", "weather", "price", "current",
]


class AiChat:
    """
    デュアル LLM Chat クライアント。

    - 通常の質問 → ローカル LLM (OpenClaw)
    - 検索が必要な質問 → クラウド LLM (GLM 等) + DuckDuckGo
    - 検索結果は OpenClaw のメモリに保存
    """

    def __init__(
        self,
        base_url: str = None,
        api_key: str = None,
        model: str = None,
        system_prompt: str = None,
        backend: str = None,
    ):
        self.backend = backend or LLM_BACKEND
        self.base_url = (base_url or API_BASE).rstrip("/")
        self.api_key = api_key or API_KEY
        self.model = model or MODEL
        self.history: list[dict] = []
        self.web_search_enabled = WEB_SEARCH

        # Gemini 設定
        self.gemini_api_key = GEMINI_API_KEY
        self.gemini_model = GEMINI_MODEL

        # システムプロンプト
        if system_prompt is None:
            system_prompt = self._load_system_prompt()

        # 検索対応のシステムプロンプト拡張
        if self.web_search_enabled:
            system_prompt += (
                "\n\nあなたはインターネット検索の結果を参照できます。"
                "検索結果が提供された場合は、その情報を元に正確に回答してください。"
                "情報の出典が明確な場合は簡潔に言及してください。"
            )

        self.system_prompt_text = system_prompt
        self.system_message = {"role": "system", "content": system_prompt}

        # ローカル LLM クライアント初期化
        self._init_client()

        # クラウド LLM クライアント初期化 (デュアルLLM)
        self._cloud_client = None
        if DUAL_LLM_ENABLED:
            self._init_cloud_client()
            logger.info(
                f"デュアルLLM: ローカル={self.base_url} / "
                f"クラウド={CLOUD_LLM_BASE} ({CLOUD_LLM_MODEL})"
            )
        else:
            logger.info("シングルLLM モード (クラウド未設定)")

    def _load_system_prompt(self) -> str:
        """システムプロンプトをファイルまたは内蔵から読み込む"""
        # 1. キャラクター別プロンプトファイル
        char_dir = os.getenv(
            "CHARACTER_DIR",
            "/opt/ai-secretary/pi-secretary/config/characters",
        )
        char_file = os.path.join(char_dir, f"{CHARACTER}.txt")

        # 2. 従来のカスタムプロンプトファイル (互換性)
        legacy_file = os.getenv(
            "SYSTEM_PROMPT_FILE",
            "/opt/ai-secretary/voice-bridge/custom/secretary_prompt.txt",
        )

        if os.path.isfile(char_file):
            with open(char_file, "r", encoding="utf-8") as f:
                prompt = f.read().strip()
            profile_name = CHARACTER_PROFILES.get(CHARACTER, {}).get("name", CHARACTER)
            logger.info(f"キャラクター: {profile_name} ({char_file})")
            return prompt
        elif os.path.isfile(legacy_file):
            with open(legacy_file, "r", encoding="utf-8") as f:
                prompt = f.read().strip()
            logger.info(f"システムプロンプト読み込み: {legacy_file}")
            return prompt
        else:
            profile = CHARACTER_PROFILES.get(CHARACTER, CHARACTER_PROFILES["normal"])
            logger.info(f"キャラクター: {profile['name']} (内蔵)")
            return profile["prompt"]

    def _init_client(self):
        """ローカル LLM バックエンドに応じたクライアントを初期化"""
        self._use_openai_lib = False
        self._use_gemini = False

        if self.backend == "gemini":
            self._init_gemini()
        else:
            self._init_openai()

    def _init_openai(self):
        """OpenAI 互換クライアント初期化"""
        try:
            from openai import OpenAI
            self.client = OpenAI(
                base_url=self.base_url,
                api_key=self.api_key,
                timeout=TIMEOUT,
            )
            self._use_openai_lib = True
            logger.info(f"ローカルLLM: {self.base_url} (model={self.model})")
        except ImportError:
            import requests as _req
            self._requests = _req
            self._use_openai_lib = False
            logger.info(f"requests で直接接続: {self.base_url}")

    def _init_gemini(self):
        """Google Gemini クライアント初期化"""
        try:
            import google.generativeai as genai
            genai.configure(api_key=self.gemini_api_key)
            self._gemini_model = genai.GenerativeModel(
                self.gemini_model,
                system_instruction=self.system_prompt_text,
            )
            self._gemini_chat = self._gemini_model.start_chat(history=[])
            self._use_gemini = True
            logger.info(f"Gemini クライアント: {self.gemini_model}")
        except ImportError:
            logger.error("google-generativeai がインストールされていません")
            logger.info("OpenAI 互換にフォールバック")
            self.backend = "openai"
            self._init_openai()

    def _init_cloud_client(self):
        """クラウド LLM クライアント初期化 (検索時用)"""
        try:
            if CLOUD_LLM_BACKEND == "gemini":
                import google.generativeai as genai
                genai.configure(api_key=CLOUD_LLM_KEY)
                self._cloud_gemini_model = genai.GenerativeModel(
                    CLOUD_LLM_MODEL,
                    system_instruction=self.system_prompt_text,
                )
                self._cloud_gemini_chat = self._cloud_gemini_model.start_chat(history=[])
                self._cloud_client = "gemini"
            else:
                from openai import OpenAI
                self._cloud_openai = OpenAI(
                    base_url=CLOUD_LLM_BASE.rstrip("/"),
                    api_key=CLOUD_LLM_KEY,
                    timeout=TIMEOUT,
                )
                self._cloud_client = "openai"
            logger.info(f"クラウドLLM: {CLOUD_LLM_BASE} ({CLOUD_LLM_MODEL})")
        except Exception as e:
            logger.error(f"クラウドLLM 初期化失敗: {e}")
            self._cloud_client = None

    # --- 検索判定 ---

    def _needs_search(self, text: str) -> bool:
        """ユーザーの質問がウェブ検索を必要とするか簡易判定"""
        if not self.web_search_enabled:
            return False
        text_lower = text.lower()
        return any(kw in text_lower for kw in SEARCH_HINT_KEYWORDS)

    def _extract_search_query(self, user_text: str) -> str:
        """ユーザー発話から検索クエリを抽出"""
        import re
        query = user_text

        wake = os.getenv("WAKE_WORD", "").strip()
        if wake:
            query = re.sub(rf"{re.escape(wake)}[、，,\s]*", "", query)
            if len(wake) >= 3:
                for i in range(2, len(wake)):
                    partial = wake[:i]
                    query = re.sub(rf"^{re.escape(partial)}[、，,\s]*", "", query)

        remove_words = [
            "検索して", "調べて", "ググって", "教えて",
            "について調べて", "について教えて", "について検索して",
            "を教えて", "は？", "は。", "って何",
        ]
        for word in remove_words:
            query = query.replace(word, "")

        return query.strip() or user_text

    def _search_web(self, query: str) -> str:
        """DuckDuckGo でウェブ検索"""
        try:
            from web_search import search_and_format
            return search_and_format(query, max_results=3)
        except ImportError:
            logger.warning("web_search.py が見つかりません")
            return ""

    # --- OpenClaw メモリ連携 ---

    def _save_to_memory(self, query: str, search_result: str, reply: str):
        """検索結果を OpenClaw のメモリ (日次ログ) に保存"""
        try:
            memory_dir = Path(OPENCLAW_MEMORY_DIR)
            memory_dir.mkdir(parents=True, exist_ok=True)

            today = datetime.now().strftime("%Y-%m-%d")
            memory_file = memory_dir / f"{today}.md"

            timestamp = datetime.now().strftime("%H:%M")
            entry = (
                f"\n## [{timestamp}] 検索: {query}\n\n"
                f"**検索結果サマリー:**\n{reply}\n\n"
                f"---\n"
            )

            with open(memory_file, "a", encoding="utf-8") as f:
                f.write(entry)

            logger.info(f"メモリ保存: {memory_file}")
        except Exception as e:
            logger.warning(f"メモリ保存失敗: {e}")

    # --- クラウド LLM での応答 ---

    def _chat_cloud(self, messages: list[dict]) -> str:
        """クラウド LLM で応答を得る"""
        if self._cloud_client == "gemini":
            # Gemini
            user_text = messages[-1]["content"]
            response = self._cloud_gemini_chat.send_message(user_text)
            return response.text.strip()
        elif self._cloud_client == "openai":
            # OpenAI 互換
            response = self._cloud_openai.chat.completions.create(
                model=CLOUD_LLM_MODEL,
                messages=messages,
                max_tokens=512,
                temperature=0.4,
            )
            return response.choices[0].message.content.strip()
        else:
            raise RuntimeError("クラウドLLM が初期化されていません")

    # --- メインの chat ---

    def chat(self, user_text: str) -> str:
        """
        テキストを送って応答を得る。

        デュアルLLM モード:
          - 検索が必要 → クラウド LLM + DuckDuckGo → 結果をメモリに保存
          - 通常の質問 → ローカル LLM (OpenClaw)

        シングルLLM モード (クラウド未設定):
          - 従来通り、設定されたバックエンドですべて処理
        """
        if not user_text.strip():
            return ""

        # 検索判定
        needs_search = self._needs_search(user_text)
        search_context = ""

        if needs_search:
            query = self._extract_search_query(user_text)
            logger.info(f"検索実行: {query}")
            search_context = self._search_web(query)
            if search_context:
                logger.info(f"検索結果取得済み ({len(search_context)} chars)")

        # メッセージ構築
        if search_context:
            augmented_text = (
                f"{user_text}\n\n"
                f"【参考: ウェブ検索結果】\n{search_context}"
            )
        else:
            augmented_text = user_text

        self.history.append({"role": "user", "content": augmented_text})
        self._trim_history()

        messages = [self.system_message] + self.history

        try:
            if needs_search and search_context and DUAL_LLM_ENABLED and self._cloud_client:
                # 検索あり → クラウド LLM
                logger.info("クラウドLLM で応答生成")
                reply = self._chat_cloud(messages)

                # 検索結果をOpenClawメモリに保存
                self._save_to_memory(
                    query=self._extract_search_query(user_text),
                    search_result=search_context,
                    reply=reply,
                )
            else:
                # 通常 → ローカル LLM
                if self._use_gemini:
                    reply = self._chat_gemini(augmented_text)
                elif self._use_openai_lib:
                    reply = self._chat_openai(messages)
                else:
                    reply = self._chat_requests(messages)

        except Exception as e:
            logger.error(f"Chat API エラー: {e}")
            # クラウド失敗時はローカルにフォールバック
            if needs_search and DUAL_LLM_ENABLED:
                logger.info("クラウドLLM 失敗、ローカルLLM にフォールバック")
                try:
                    if self._use_gemini:
                        reply = self._chat_gemini(augmented_text)
                    elif self._use_openai_lib:
                        reply = self._chat_openai(messages)
                    else:
                        reply = self._chat_requests(messages)
                except Exception as e2:
                    logger.error(f"ローカルLLM もエラー: {e2}")
                    reply = "すみません、通信エラーが発生しました。もう一度お願いします。"
            else:
                reply = "すみません、通信エラーが発生しました。もう一度お願いします。"

        self.history.append({"role": "assistant", "content": reply})
        self._trim_history()

        return reply

    def chat_stream(self, user_text: str) -> Generator[str, None, None]:
        """ストリーミング応答。文単位で yield する。"""
        # ストリーミングはシンプルに chat() を使う
        # (デュアルLLM対応のストリーミングは複雑になるため)
        reply = self.chat(user_text)
        if reply:
            yield reply

    # --- translator.py 互換 ---

    def translate(self, text: str, _src: str = None, _tgt: str = None) -> str:
        return self.chat(text)

    # --- バックエンド別メソッド (ローカル LLM) ---

    def _chat_openai(self, messages: list[dict]) -> str:
        """OpenAI 互換 API"""
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=512,
            temperature=0.4,
        )
        return response.choices[0].message.content.strip()

    def _chat_gemini(self, user_text: str) -> str:
        """Google Gemini API"""
        response = self._gemini_chat.send_message(user_text)
        return response.text.strip()

    def _chat_requests(self, messages: list[dict]) -> str:
        """requests で直接 HTTP"""
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": 512,
            "temperature": 0.4,
        }

        resp = self._requests.post(
            url, json=payload, headers=headers, timeout=TIMEOUT
        )
        resp.raise_for_status()
        data = resp.json()

        return data["choices"][0]["message"]["content"].strip()

    def _trim_history(self):
        if len(self.history) > MAX_HISTORY * 2:
            self.history = self.history[-MAX_HISTORY * 2 :]

    def clear_history(self):
        self.history.clear()
        if self._use_gemini:
            import google.generativeai as genai
            self._gemini_chat = self._gemini_model.start_chat(history=[])
        logger.info("会話履歴をクリアしました")


# --- translator.py 互換のモジュールレベル関数 ---
_default_client: Optional[AiChat] = None


def get_client() -> AiChat:
    global _default_client
    if _default_client is None:
        _default_client = AiChat()
    return _default_client


def translate(text: str, src_lang: str = "ja", tgt_lang: str = "ja") -> str:
    return get_client().chat(text)
