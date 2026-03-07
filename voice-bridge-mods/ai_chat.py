"""
ai_chat.py — OpenAI互換 Chat クライアント

translator.py の置き換え。
Google翻訳の代わりに OpenClaw Gateway の /v1/chat/completions を叩く。

voice-bridge の既存構造に合わせて、
translate() と同じインターフェースで使えるようにする。
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
MAX_HISTORY = int(os.getenv("CHAT_MAX_HISTORY", "20"))
TIMEOUT = int(os.getenv("CHAT_TIMEOUT", "30"))


class AiChat:
    """
    OpenAI互換APIを使った会話クライアント。

    translator.py の Translator クラスと同じ立ち位置。
    voice-bridge の main.py から呼ばれる。
    """

    def __init__(
        self,
        base_url: str = None,
        api_key: str = None,
        model: str = None,
        system_prompt: str = None,
    ):
        self.base_url = (base_url or API_BASE).rstrip("/")
        self.api_key = api_key or API_KEY
        self.model = model or MODEL
        self.history: list[dict] = []

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

        self.system_message = {"role": "system", "content": system_prompt}

        # openai ライブラリを使う (なければ requests にフォールバック)
        try:
            from openai import OpenAI

            self.client = OpenAI(base_url=self.base_url, api_key=self.api_key)
            self._use_openai_lib = True
            logger.info(f"openai ライブラリで接続: {self.base_url}")
        except ImportError:
            import requests as _req

            self._requests = _req
            self._use_openai_lib = False
            logger.info(f"requests で直接接続: {self.base_url}")

    def chat(self, user_text: str) -> str:
        """
        テキストを送って応答を得る。
        translator.py の translate() と同じ立ち位置。
        """
        if not user_text.strip():
            return ""

        self.history.append({"role": "user", "content": user_text})
        self._trim_history()

        messages = [self.system_message] + self.history

        try:
            if self._use_openai_lib:
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
        """
        ストリーミング応答。
        音声合成の低遅延化のため、文単位で yield する。
        """
        if not user_text.strip():
            return

        self.history.append({"role": "user", "content": user_text})
        self._trim_history()

        messages = [self.system_message] + self.history
        full_reply = ""

        try:
            if self._use_openai_lib:
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

                        # 文末で区切って yield
                        while any(sep in buffer for sep in ["。", "！", "？", "\n"]):
                            for sep in ["。", "！", "？", "\n"]:
                                idx = buffer.find(sep)
                                if idx >= 0:
                                    sentence = buffer[: idx + 1].strip()
                                    buffer = buffer[idx + 1 :]
                                    if sentence:
                                        yield sentence
                                    break

                # バッファに残りがあれば
                if buffer.strip():
                    yield buffer.strip()
            else:
                # requests フォールバック (ストリーミング非対応)
                reply = self._chat_requests(messages)
                full_reply = reply
                yield reply

        except Exception as e:
            logger.error(f"Stream API エラー: {e}")
            full_reply = "すみません、通信エラーなのだ。"
            yield full_reply

        self.history.append({"role": "assistant", "content": full_reply})
        self._trim_history()

    # --- translator.py 互換インターフェース ---

    def translate(self, text: str, _src: str = None, _tgt: str = None) -> str:
        """
        translator.py の translate() と同じシグネチャ。
        翻訳ではなく AI 応答を返す。
        既存の main.py から最小変更で切り替えられるようにする。
        """
        return self.chat(text)

    # --- 内部メソッド ---

    def _chat_openai(self, messages: list[dict]) -> str:
        """openai ライブラリ経由"""
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=512,
            temperature=0.4,
        )
        return response.choices[0].message.content.strip()

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
        """履歴が長くなりすぎたら古いものを削除"""
        if len(self.history) > MAX_HISTORY * 2:
            self.history = self.history[-MAX_HISTORY * 2 :]

    def clear_history(self):
        """会話履歴をクリア"""
        self.history.clear()
        logger.info("会話履歴をクリアしました")


# --- translator.py 互換のモジュールレベル関数 ---
_default_client: Optional[AiChat] = None


def get_client() -> AiChat:
    global _default_client
    if _default_client is None:
        _default_client = AiChat()
    return _default_client


def translate(text: str, src_lang: str = "ja", tgt_lang: str = "ja") -> str:
    """
    translator.py の translate() と完全互換。
    main.py 内の呼び出しを変えずに済む。
    """
    return get_client().chat(text)
