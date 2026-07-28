#!/usr/bin/env python3
"""MAX webhook spike listener — logs all incoming payloads to JSONL."""
import json
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler

LOG_FILE = "/root/ai-antispam/.secrets/max-webhook-payloads.jsonl"
PORT = 9876

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {"raw": body.decode("utf-8", errors="replace")}

        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "path": self.path,
            "headers": dict(self.headers),
            "payload": payload,
        }
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        print(f"[{entry['ts']}] {self.path} — {json.dumps(payload, ensure_ascii=False)[:200]}", flush=True)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"MAX webhook spike listener is running")

    def log_message(self, fmt, *args):
        pass

if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Listening on :{PORT}, logging to {LOG_FILE}", flush=True)
    server.serve_forever()
