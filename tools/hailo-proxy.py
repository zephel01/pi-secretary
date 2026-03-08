#!/usr/bin/env python3
"""
hailo-proxy.py — OpenClaw ↔ hailo-ollama 互換プロキシ

hailo-ollama は標準 Ollama の一部フィールドしか対応していないため、
OpenClaw が送る追加フィールド (tools, options 等) を除去して転送する。
ストリーミングは stream=false に強制し、完全なレスポンスを
Ollama ストリーミング形式に変換して返す。

ポート: 11434 (標準 Ollama と同じ) → hailo-ollama :8000 に転送
"""

import json
import logging
import sys
import socket
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone

HAILO_HOST = "127.0.0.1"
HAILO_PORT = 8000
LISTEN_PORT = 11434

ALLOWED_CHAT_FIELDS = {"model", "messages"}
ALLOWED_MESSAGE_FIELDS = {"role", "content"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [hailo-proxy] %(levelname)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("hailo-proxy")


def _raw_http_post(path: str, body: bytes, timeout: int = 120) -> bytes:
    """Raw socket で hailo-ollama に POST し、レスポンスボディを返す"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((HAILO_HOST, HAILO_PORT))
        req = (
            f"POST {path} HTTP/1.1\r\n"
            f"Host: {HAILO_HOST}:{HAILO_PORT}\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        ).encode() + body
        sock.sendall(req)

        # レスポンス全体を受信
        chunks = []
        while True:
            try:
                data = sock.recv(65536)
                if not data:
                    break
                chunks.append(data)
            except socket.timeout:
                break

        raw = b"".join(chunks)
        # ヘッダーとボディを分離
        if b"\r\n\r\n" in raw:
            _, resp_body = raw.split(b"\r\n\r\n", 1)
            return resp_body
        return raw
    finally:
        sock.close()


def _raw_http_get(path: str, timeout: int = 10) -> bytes:
    """Raw socket で GET"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((HAILO_HOST, HAILO_PORT))
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {HAILO_HOST}:{HAILO_PORT}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        ).encode()
        sock.sendall(req)

        chunks = []
        while True:
            try:
                data = sock.recv(65536)
                if not data:
                    break
                chunks.append(data)
            except socket.timeout:
                break

        raw = b"".join(chunks)
        if b"\r\n\r\n" in raw:
            _, resp_body = raw.split(b"\r\n\r\n", 1)
            return resp_body
        return raw
    finally:
        sock.close()


class ProxyHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path == "/api/tags":
            self._handle_tags()
        else:
            try:
                body = _raw_http_get(self.path)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
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
                resp_body = _raw_http_post(self.path, body)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(resp_body)
            except Exception as e:
                log.error("POST error: %s", e)
                self._send_error(502, str(e))

    def _handle_chat(self, body: bytes):
        """チャットリクエストを処理: フィルタ → stream=false で送信 → レスポンス変換"""
        try:
            data = json.loads(body)
            was_streaming = data.get("stream", True)
            log.info("Original fields: %s, stream_requested: %s", list(data.keys()), was_streaming)

            # フィルタ: 許可フィールドのみ + stream=false 固定
            filtered = {k: v for k, v in data.items() if k in ALLOWED_CHAT_FIELDS}
            if "messages" in filtered:
                filtered["messages"] = [
                    {k: v for k, v in msg.items() if k in ALLOWED_MESSAGE_FIELDS}
                    for msg in filtered["messages"]
                    if msg.get("role") in ("system", "user", "assistant")
                ]
            filtered["stream"] = False

            log.info("Sending to hailo: messages=%d, stream=false", len(filtered.get("messages", [])))
            filtered_body = json.dumps(filtered).encode("utf-8")

            # hailo-ollama に送信 (stream=false)
            resp_body = _raw_http_post("/api/chat", filtered_body)

            # hailo-ollama の非ストリーミングレスポンスをパース
            # レスポンスが複数行の NDJSON の場合、最後の行を使う
            resp_text = resp_body.decode("utf-8", errors="replace").strip()
            lines = [l for l in resp_text.split("\n") if l.strip()]

            hailo_resp = None
            for line in reversed(lines):
                try:
                    hailo_resp = json.loads(line)
                    if hailo_resp.get("done"):
                        break
                except json.JSONDecodeError:
                    continue

            if not hailo_resp:
                log.error("No valid JSON in hailo response: %s", resp_text[:200])
                self._send_error(502, "Invalid hailo-ollama response")
                return

            content = hailo_resp.get("message", {}).get("content", "")
            model = hailo_resp.get("model", "unknown")
            log.info("Got response: %d chars from %s", len(content), model)

            if was_streaming:
                # Ollama ストリーミング形式に変換: content チャンク + done チャンク
                now = datetime.now(timezone.utc).isoformat()

                # コンテンツチャンク
                content_chunk = json.dumps({
                    "model": model,
                    "created_at": now,
                    "message": {"role": "assistant", "content": content},
                    "done": False,
                })
                # 完了チャンク
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
                # 非ストリーミング: そのまま返す
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(hailo_resp).encode())

        except Exception as e:
            log.error("Chat error: %s", e, exc_info=True)
            self._send_error(502, str(e))

    def _handle_tags(self):
        """Ollama /api/tags 互換レスポンスを hailo の /hailo/v1/list から生成"""
        try:
            body = _raw_http_get("/hailo/v1/list")
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
            resp = json.dumps(tags_response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(resp)
        except Exception as e:
            log.error("Tags error: %s", e)
            self._send_error(502, str(e))

    def _send_error(self, code: int, msg: str):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"error": msg}).encode())

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
