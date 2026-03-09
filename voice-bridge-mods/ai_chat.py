"""
ai_chat.py — マルチ LLM Chat クライアント + ウェブ検索

対応バックエンド:
  - OpenAI API (GPT-4o, etc.)
  - OpenClaw Gateway (ローカル LLM)
  - Google Gemini API
  - その他 OpenAI 互換 API (GLM, Groq, Together, etc.)

ウェブ検索:
  - DuckDuckGo (APIキー不要) を使って検索が必要な質問に自動対応
  - LLM が検索の必要性を判断 → 検索実行 → 結果をコンテキストとして渡す

環境変数:
  OPENAI_API_BASE   — API エンドポイント (default: http://127.0.0.1:18789/v1)
  OPENAI_API_KEY    — API キー
  OPENAI_MODEL      — モデル名
  LLM_BACKEND       — バックエンド種別: openai / gemini / openclaw (default: openai)
  GEMINI_API_KEY    — Gemini API キー (LLM_BACKEND=gemini 時)
  GEMINI_MODEL      — Gemini モデル名 (default: gemini-2.0-flash)
  WEB_SEARCH        — 検索機能: on / off (default: on)
  CHAT_MAX_HISTORY  — 会話履歴の最大数 (default: 20)
  CHAT_TIMEOUT      — API タイムアウト秒数 (default: 30)
"""

import os
import json
import logging
import time
from typing import Optional, Generator

logger = logging.getLogger(__name__)

# --- 設定 ---
API_BASE = os.getenv("OPENAI_API_BASE", "http://127.0.0.1:18789/v1")
API_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL = os.getenv("OPENAI_MODEL", "openclaw")
LLM_BACKEND = os.getenv("LLM_BACKEND", "openai")  # openai / gemini / openclaw
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
WEB_SEARCH = os.getenv("WEB_SEARCH", "on").lower() in ("on", "true", "1", "yes")
MAX_HISTORY = int(os.getenv("CHAT_MAX_HISTORY", "20"))
TIMEOUT = int(os.getenv("CHAT_TIMEOUT", "30"))

# 検索判定用キーワード (高速フィルタ, LLM 判定前に使用)
SEARCH_HINT_KEYWORDS = [
    "検索", "調べて", "ググ", "最新", "ニュース", "天気", "今日",
    "現在", "いつ", "何時", "価格", "値段", "株価", "為替",
    "スコア", "結果", "誰が", "どこで", "何が",
    "search", "latest", "news", "weather", "price", "current",
]


class AiChat:
    """
    マルチバックエンド AI Chat クライアント + ウェブ検索。
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
            prompt_file = os.getenv(
                "SYSTEM_PROMPT_FILE",
                "/opt/ai-secretary/voice-bridge/custom/secretary_prompt.txt",
            )
            if os.path.isfile(prompt_file):
                with open(prompt_file, "r", encoding="utf-8") as f:
                    system_prompt = f.read().strip()
                logger.info(f"システムプロンプト読み込み: {prompt_file}")
            else:
                system_prompt = (
                    "あなたはずんだもん秘書なのだ。"
                    "予定やToDoを短く整理して返すのだ。"
                    "語尾は「〜のだ」を使うのだ。"
                )
                logger.warning(f"プロンプトファイルなし、デフォルトを使用: {prompt_file}")

        # 検索対応のシステムプロンプト拡張
        if self.web_search_enabled:
            system_prompt += (
                "\n\nあなたはインターネット検索の結果を参照できます。"
                "検索結果が提供された場合は、その情報を元に正確に回答してください。"
                "情報の出典が明確な場合は簡潔に言及してください。"
            )

        self.system_prompt_text = system_prompt
        self.system_message = {"role": "system", "content": system_prompt}

        # クライアント初期化
        self._init_client()

    def _init_client(self):
        """バックエンドに応じたクライアントを初期化"""
        self._use_openai_lib = False
        self._use_gemini = False

        if self.backend == "gemini":
            self._init_gemini()
        else:
            # openai / openclaw / その他 OpenAI 互換
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
            logger.info(f"OpenAI 互換クライアント: {self.base_url} (model={self.model})")
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
            logger.error("google-generativeai がインストールされていません: "
                          "pip3 install google-generativeai --break-system-packages")
            # フォールバック: OpenAI 互換に切り替え
            logger.info("OpenAI 互換にフォールバック")
            self.backend = "openai"
            self._init_openai()

    # --- 検索判定 ---

    def _needs_search(self, text: str) -> bool:
        """ユーザーの質問がウェブ検索を必要とするか簡易判定"""
        if not self.web_search_enabled:
            return False
        text_lower = text.lower()
        return any(kw in text_lower for kw in SEARCH_HINT_KEYWORDS)

    def _extract_search_query(self, user_text: str) -> str:
        """ユーザー発話から検索クエリを抽出 (簡易版)"""
        # 不要な接頭辞を除去
        remove_prefixes = [
            "検索して", "調べて", "ググって", "教えて",
            "について調べて", "について教えて", "について検索して",
        ]
        query = user_text
        for prefix in remove_prefixes:
            query = query.replace(prefix, "")
        return query.strip() or user_text

    def _search_web(self, query: str) -> str:
        """DuckDuckGo でウェブ検索"""
        try:
            from web_search import search_and_format
            return search_and_format(query, max_results=3)
        except ImportError:
            logger.warning("web_search.py が見つかりません")
            return ""

    # --- メインの chat ---

    def chat(self, user_text: str) -> str:
        """テキストを送って応答を得る。検索が必要なら自動で検索する。"""
        if not user_text.strip():
            return ""

        # 検索判定 & 実行
        search_context = ""
        if self._needs_search(user_text):
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
            if self._use_gemini:
                reply = self._chat_gemini(augmented_text)
            elif self._use_openai_lib:
                reply = self._chat_openai(messages)
            else:
                reply = self._chat_requests(messages)
        except Exception as e:
            logger.error(f"Chat API エラー: {e}")
            reply = "すみません、通信エラーなのだ。もう一度言ってほしいのだ。"

        self.history.append({"role": "assistant", "content": reply})
        self._trim_history()

        return reply

    def chat_stream(self, user_text: str) -> Generator[str, None, None]:
        """ストリーミング応答。文単位で yield する。"""
        if not user_text.strip():
            return

        # 検索判定 & 実行
        search_context = ""
        if self._needs_search(user_text):
            query = self._extract_search_query(user_text)
            logger.info(f"検索実行: {query}")
            search_context = self._search_web(query)

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
        full_reply = ""

        try:
            if self._use_gemini:
                # Gemini ストリーミング
                reply = self._chat_gemini(augmented_text)
                full_reply = reply
                yield reply
            elif self._use_openai_lib:
                stream = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    stream=True,
                    max_tokens=512,
                    temperature=0.4,
                )
                buffer = ""
                for chunk in stream:
                    delta = chunk.choices[0].delta
                    if delta.content:
                        buffer += delta.content
                        full_reply += delta.content

                        while any(sep in buffer for sep in ["。", "！", "？", "\n"]):
                            for sep in ["。", "！", "？", "\n"]:
                                idx = buffer.find(sep)
                                if idx >= 0:
                                    sentence = buffer[: idx + 1].strip()
                                    buffer = buffer[idx + 1 :]
                                    if sentence:
                                        yield sentence
                                    break

                if buffer.strip():
                    yield buffer.strip()
            else:
                reply = self._chat_requests(messages)
                full_reply = reply
                yield reply

        except Exception as e:
            logger.error(f"Stream API エラー: {e}")
            full_reply = "すみません、通信エラーなのだ。"
            yield full_reply

        self.history.append({"role": "assistant", "content": full_reply})
        self._trim_history()

    # --- translator.py 互換 ---

    def translate(self, text: str, _src: str = None, _tgt: str = None) -> str:
        return self.chat(text)

    # --- バックエンド別メソッド ---

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
            # Gemini チャット履歴もリセット
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
