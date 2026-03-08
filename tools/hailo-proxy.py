#!/usr/bin/env python3
"""
hailo-proxy.py — OpenClaw ↔ hailo-ollama 互換プロキシ

hailo-ollama は標準 Ollama の一部フィールドしか対応していないため、
OpenClaw が送る追加フィールド (tools, options 等) を除去して転送する。
stream=false を強制し、curl で hailo-ollama に送信、
レスポンスを Ollama ストリーミング形式に変換して返す。

ポート: 11434 → hailo-ollama :8000
"""

import json
import logging
import subprocess
import sys
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler

HAILO_URL = "http://127.0.0.1:8000"
LISTEN_PORT = 11434

ALLOWED_CHAT_FIELDS = {"model", "messages"}
ALLOWED_MESSAGE_FIELDS = {"role", "content"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [hailo-proxy] %(levelname)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("hailo-proxy")


def _curl_post(url: str, data: dict, timeout: int = 120) -> str:
    """curl で POST して結果を文字列で返す"""
    body = json.dumps(data)
    result = subprocess.run(
        [
            "curl", "-s", "--max-time", str(timeout),
            "-H", "Content-Type: application/json",
            "-d", body,
            url,
        ],
        capture_output=True,
        text=True,
        timeout=timeout + 5,
    )
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


class ProxyHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path == "/api/tags":
            self._handle_tags()
        else:
            try:
                body = _curl_get(f"{HAILO_URL}{self.path}")
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
                resp = _curl_post(f"{HAILO_URL}{self.path}", data)
                self._send_json(200, resp.encode())
            except Exception as e:
                log.error("POST error: %s", e)
                self._send_error(502, str(e))

    def _handle_chat(self, body: bytes):
        try:
            data = json.loads(body)
            was_streaming = data.get("stream", True)
            log.info("Original fields: %s, stream=%s", list(data.keys()), was_streaming)

            # フィルタ: 許可フィールドのみ + stream=false 固定
            filtered = {k: v for k, v in data.items() if k in ALLOWED_CHAT_FIELDS}
            if "messages" in filtered:
                filtered["messages"] = [
                    {k: v for k, v in msg.items() if k in ALLOWED_MESSAGE_FIELDS}
                    for msg in filtered["messages"]
                    if msg.get("role") in ("system", "user", "assistant")
                ]
            filtered["stream"] = False

            msg_count = len(filtered.get("messages", []))
            log.info("Sending to hailo: messages=%d, stream=false", msg_count)

            # curl で hailo-ollama に送信
            resp_text = _curl_post(f"{HAILO_URL}/api/chat", filtered)
            log.info("Raw response length: %d", len(resp_text))

            if not resp_text.strip():
                log.error("Empty response from hailo-ollama")
                self._send_error(502, "Empty response from hailo-ollama")
                return

            # レスポンスをパース（NDJSON の場合もあるので最後の done=true を探す）
            hailo_resp = None
            for line in reversed(resp_text.strip().split("\n")):
                line = line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                    if hailo_resp is None or parsed.get("done"):
                        hailo_resp = parsed
                    if parsed.get("done"):
                        break
                except json.JSONDecodeError:
                    continue

            if not hailo_resp:
                log.error("No valid JSON: %s", resp_text[:300])
                self._send_error(502, "Invalid response from hailo-ollama")
                return

            content = hailo_resp.get("message", {}).get("content", "")
            model = hailo_resp.get("model", "unknown")
            log.info("Response: %d chars, model=%s", len(content), model)

            if was_streaming:
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
                    "total_duration": hailo_resp.get("total_duration", 0),
                    "eval_count": hailo_resp.get("eval_count", 0),
                })

                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()
                self.wfile.write((content_chunk + "\n").encode())
                self.wfile.write((done_chunk + "\n").encode())
                self.wfile.flush()
            else:
                self._send_json(200, json.dumps(hailo_resp).encode())

        except Exception as e:
            log.error("Chat error: %s", e, exc_info=True)
            self._send_error(502, str(e))

    def _handle_tags(self):
        try:
            body = _curl_get(f"{HAILO_URL}/hailo/v1/list")
            data = json.loads(body)
            models = data.get("models", [])
            tags_response = {
                "models": [
                    {
                        "name": m,
                        "model": m,
                        "modified_at": "2026-01-01T00:00:00Z",
                        "size": 0,
                    }
                    for m in models
                ]
            }
            self._send_json(200, json.dumps(tags_response).encode())
        except Exception as e:
            log.error("Tags error: %s", e)
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
    log.info("hailo-proxy listening on 127.0.0.1:%d → %s", LISTEN_PORT, HAILO_URL)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down")
        server.server_close()


if __name__ == "__main__":
    main()
