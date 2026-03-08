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
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

HAILO_URL = "http://127.0.0.1:8000"
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
    def _proxy(self, method: str):
        path = self.path
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""

        # /api/tags → hailo-ollama の /hailo/v1/list を変換して返す
        if path == "/api/tags":
            return self._handle_tags()

        # チャットリクエストのフィールドをフィルタリング
        if path == "/api/chat" and body:
            body = self._filter_chat_body(body)

        # hailo-ollama に転送
        target_url = f"{HAILO_URL}{path}"
        req = Request(target_url, data=body if method == "POST" else None, method=method)
        req.add_header("Content-Type", "application/json")

        try:
            with urlopen(req) as resp:
                resp_body = resp.read()
                self.send_response(resp.status)
                for key, val in resp.getheaders():
                    if key.lower() not in ("transfer-encoding", "connection"):
                        self.send_header(key, val)
                self.end_headers()
                self.wfile.write(resp_body)
        except HTTPError as e:
            log.error("hailo-ollama returned %d: %s", e.code, e.read().decode("utf-8", errors="replace"))
            self.send_response(e.code)
            self.end_headers()
            self.wfile.write(e.read() if hasattr(e, "fp") else b"")
        except URLError as e:
            log.error("Cannot reach hailo-ollama: %s", e.reason)
            self.send_response(502)
            self.end_headers()
            self.wfile.write(b'{"error": "hailo-ollama unreachable"}')

    def _filter_chat_body(self, body: bytes) -> bytes:
        try:
            data = json.loads(body)
            log.info("Original fields: %s", list(data.keys()))

            # 許可フィールドのみ残す
            filtered = {k: v for k, v in data.items() if k in ALLOWED_CHAT_FIELDS}

            # messages 内も role, content のみに
            if "messages" in filtered:
                filtered["messages"] = [
                    {k: v for k, v in msg.items() if k in ALLOWED_MESSAGE_FIELDS}
                    for msg in filtered["messages"]
                    if msg.get("role") in ("system", "user", "assistant")
                ]

            # stream がなければ false を設定
            if "stream" not in filtered:
                filtered["stream"] = False

            log.info("Filtered fields: %s, messages: %d", list(filtered.keys()), len(filtered.get("messages", [])))
            return json.dumps(filtered).encode("utf-8")
        except (json.JSONDecodeError, Exception) as e:
            log.warning("Failed to filter body: %s", e)
            return body

    def _handle_tags(self):
        """Ollama /api/tags 互換レスポンスを hailo の /hailo/v1/list から生成"""
        try:
            req = Request(f"{HAILO_URL}/hailo/v1/list")
            with urlopen(req) as resp:
                data = json.loads(resp.read())
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
                body = json.dumps(tags_response).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
        except Exception as e:
            log.error("Failed to get tags: %s", e)
            self.send_response(502)
            self.end_headers()
            self.wfile.write(b'{"error": "cannot list models"}')

    def do_GET(self):
        self._proxy("GET")

    def do_POST(self):
        self._proxy("POST")

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
