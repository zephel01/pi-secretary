#!/usr/bin/env python3
"""
ollama-proxy.py — OpenClaw → Ollama 軽量プロキシ

OpenClaw の巨大システムプロンプト (AGENTS.md, SOUL.md 等) を
小さいモデル向けにコンパクトに圧縮して通常 Ollama に転送する。

ポート: 11435 → Ollama :11434
"""

import json
import logging
import subprocess
import sys
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler

OLLAMA_URL = "http://127.0.0.1:11434"
LISTEN_PORT = 11435

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [ollama-proxy] %(levelname)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("ollama-proxy")


def _curl_post(url: str, data: dict, timeout: int = 120) -> str:
    """curl で POST して結果を文字列で返す"""
    result = subprocess.run(
        [
            "curl", "-s", "--max-time", str(timeout),
            "-H", "Content-Type: application/json",
            "-d", json.dumps(data),
            url,
        ],
        capture_output=True,
        text=True,
        timeout=timeout + 5,
    )
    if result.returncode != 0:
        log.error("curl failed (rc=%d): stderr=%s", result.returncode, result.stderr[:300])
    return result.stdout


def _curl_get(url: str, timeout: int = 10) -> str:
    """curl で GET"""
    result = subprocess.run(
        ["curl", "-s", "--max-time", str(timeout), url],
        capture_output=True,
        text=True,
        timeout=timeout + 5,
    )
    return result.stdout


# ---------- システムプロンプト圧縮 ----------

MAX_SYSTEM_CHARS = 500

COMPACT_SYSTEM_PROMPT = (
    "あなたはAI秘書「ずんだもん」です。"
    "日本語で簡潔に応答してください。"
    "ユーザーの質問や依頼に親切に答えてください。"
    "語尾は「なのだ」を使ってください。"
)


def _compact_messages(messages: list) -> list:
    """system メッセージが長すぎる場合、コンパクトなプロンプトに置換"""
    result = []
    for msg in messages:
        if msg.get("role") == "system":
            content = msg.get("content", "")
            if len(content) > MAX_SYSTEM_CHARS:
                log.info(
                    "Compacting system message: %d chars -> %d chars",
                    len(content),
                    len(COMPACT_SYSTEM_PROMPT),
                )
                result.append({"role": "system", "content": COMPACT_SYSTEM_PROMPT})
            else:
                result.append(msg)
        else:
            result.append(msg)
    return result


# ---------- HTTP ハンドラ ----------

class ProxyHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        """GET はそのまま Ollama に転送"""
        try:
            body = _curl_get(f"{OLLAMA_URL}{self.path}")
            self._send_json(200, body.encode())
        except Exception as e:
            log.error("GET error: %s", e)
            self._send_error(502, str(e))

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""

        if self.path == "/api/chat" and body:
            self._handle_chat(body)
        else:
            try:
                data = json.loads(body) if body else {}
                resp = _curl_post(f"{OLLAMA_URL}{self.path}", data)
                self._send_json(200, resp.encode())
            except Exception as e:
                log.error("POST error: %s", e)
                self._send_error(502, str(e))

    def _handle_chat(self, body: bytes):
        try:
            data = json.loads(body)
            was_streaming = data.get("stream", True)
            log.info(
                "Request: model=%s, fields=%s, stream=%s",
                data.get("model"), list(data.keys()), was_streaming,
            )

            # 小モデル非対応フィールドを除去 (tools, options 等)
            for key in ("tools", "options", "tool_choice"):
                if key in data:
                    log.info("Removing unsupported field: %s", key)
                    del data[key]

            # システムプロンプトを圧縮
            if "messages" in data:
                data["messages"] = _compact_messages(data["messages"])

            # stream=false で Ollama に送信 (レスポンス変換を簡単にするため)
            data["stream"] = False

            msg_count = len(data.get("messages", []))
            log.info(
                "Forwarding to Ollama: model=%s, messages=%d",
                data.get("model"), msg_count,
            )

            resp_text = _curl_post(f"{OLLAMA_URL}/api/chat", data)
            log.info("Response length: %d", len(resp_text))

            if not resp_text.strip():
                log.error("Empty response from Ollama")
                self._send_error(502, "Empty response from Ollama")
                return

            # レスポンスをパース (NDJSON の場合もあるので最後の done=true を探す)
            ollama_resp = None
            for line in reversed(resp_text.strip().split("\n")):
                line = line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                    if ollama_resp is None or parsed.get("done"):
                        ollama_resp = parsed
                    if parsed.get("done"):
                        break
                except json.JSONDecodeError:
                    continue

            if not ollama_resp:
                log.error("No valid JSON: %s", resp_text[:300])
                self._send_error(502, "Invalid response from Ollama")
                return

            content = ollama_resp.get("message", {}).get("content", "")
            model = ollama_resp.get("model", "unknown")
            log.info("Content: %d chars, model=%s", len(content), model)

            if was_streaming:
                # OpenClaw は streaming を期待するので NDJSON 形式で返す
                now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

                content_chunk = json.dumps({
                    "model": model,
                    "created_at": now,
                    "message": {"role": "assistant", "content": content},
                    "done": False,
                })
                done_chunk = json.dumps({
                    "model": model,
                    "created_at": now,
                    "message": {"role": "assistant", "content": ""},
                    "done": True,
                    "done_reason": "stop",
                    "total_duration": ollama_resp.get("total_duration", 0),
                    "eval_count": ollama_resp.get("eval_count", 0),
                })

                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()
                self.wfile.write((content_chunk + "\n").encode())
                self.wfile.write((done_chunk + "\n").encode())
                self.wfile.flush()
            else:
                self._send_json(200, json.dumps(ollama_resp).encode())

        except Exception as e:
            log.error("Chat error: %s", e, exc_info=True)
            self._send_error(502, str(e))

    def _send_json(self, code: int, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, code: int, msg: str):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"error": msg}).encode())

    def log_message(self, format, *args):
        log.debug(format, *args)


def main():
    server = HTTPServer(("127.0.0.1", LISTEN_PORT), ProxyHandler)
    log.info("ollama-proxy on 127.0.0.1:%d -> %s", LISTEN_PORT, OLLAMA_URL)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down")
        server.server_close()


if __name__ == "__main__":
    main()
