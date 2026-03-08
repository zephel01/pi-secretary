#!/usr/bin/env python3
"""
hailo-proxy.py — OpenClaw ↔ hailo-ollama 互換プロキシ

hailo-ollama は標準 Ollama の一部フィールドしか対応していないため、
OpenClaw が送る追加フィールド (tools, options 等) を除去して転送する。

ポート: 11434 (標準 Ollama と同じ) → hailo-ollama :8000 に転送
"""

import json
import logging
import sys
import http.client
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

HAILO_HOST = "127.0.0.1"
HAILO_PORT = 8000
LISTEN_PORT = 11434

# hailo-ollama が受け付けるフィールドのみ通す
ALLOWED_CHAT_FIELDS = {"model", "messages", "stream"}
ALLOWED_MESSAGE_FIELDS = {"role", "content"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [hailo-proxy] %(levelname)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("hailo-proxy")


class ProxyHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path == "/api/tags":
            self._handle_tags()
        else:
            self._forward("GET")

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""

        if self.path == "/api/chat" and body:
            body = self._filter_chat_body(body)

        self._forward_streaming("POST", body)

    def _filter_chat_body(self, body: bytes) -> bytes:
        try:
            data = json.loads(body)
            log.info("Original fields: %s", list(data.keys()))

            filtered = {k: v for k, v in data.items() if k in ALLOWED_CHAT_FIELDS}

            # messages 内も role, content のみに。tool 等のロールは除外
            if "messages" in filtered:
                filtered["messages"] = [
                    {k: v for k, v in msg.items() if k in ALLOWED_MESSAGE_FIELDS}
                    for msg in filtered["messages"]
                    if msg.get("role") in ("system", "user", "assistant")
                ]

            log.info(
                "Filtered fields: %s, messages: %d, stream: %s",
                list(filtered.keys()),
                len(filtered.get("messages", [])),
                filtered.get("stream"),
            )
            return json.dumps(filtered).encode("utf-8")
        except Exception as e:
            log.warning("Failed to filter body: %s", e)
            return body

    def _forward_streaming(self, method: str, body: bytes):
        """ストリーミング対応で hailo-ollama に転送"""
        try:
            conn = http.client.HTTPConnection(HAILO_HOST, HAILO_PORT, timeout=120)
            headers = {"Content-Type": "application/json"}
            conn.request(method, self.path, body=body, headers=headers)
            resp = conn.getresponse()

            self.send_response(resp.status)
            # Transfer-Encoding: chunked の場合もそのまま流す
            self.send_header("Content-Type", "application/json")
            self.end_headers()

            # チャンクで読んでそのまま流す
            while True:
                chunk = resp.read(4096)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()

            conn.close()
        except Exception as e:
            log.error("Proxy error: %s", e)
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": f"hailo-ollama error: {e}"}).encode())

    def _forward(self, method: str):
        """通常の GET リクエスト転送"""
        try:
            conn = http.client.HTTPConnection(HAILO_HOST, HAILO_PORT, timeout=30)
            conn.request(method, self.path)
            resp = conn.getresponse()
            body = resp.read()

            self.send_response(resp.status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
            conn.close()
        except Exception as e:
            log.error("Forward error: %s", e)
            self.send_response(502)
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def _handle_tags(self):
        """Ollama /api/tags 互換レスポンスを hailo の /hailo/v1/list から生成"""
        try:
            conn = http.client.HTTPConnection(HAILO_HOST, HAILO_PORT, timeout=10)
            conn.request("GET", "/hailo/v1/list")
            resp = conn.getresponse()
            data = json.loads(resp.read())
            conn.close()

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
            body = json.dumps(tags_response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            log.error("Tags error: %s", e)
            self.send_response(502)
            self.end_headers()
            self.wfile.write(b'{"error": "cannot list models"}')

    def log_message(self, format, *args):
        log.debug(format, *args)


def main():
    server = HTTPServer(("127.0.0.1", LISTEN_PORT), ProxyHandler)
    log.info("hailo-proxy listening on 127.0.0.1:%d → %s:%d", LISTEN_PORT, HAILO_HOST, HAILO_PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down")
        server.server_close()


if __name__ == "__main__":
    main()
