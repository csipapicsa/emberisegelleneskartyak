"""A stand-in for the model service (ml/serve.py) for the end-to-end tests: same /score and /healthz API,
deterministic scores from the card text, no model and no PyTorch.

    python -m tests.e2e.fake_model --port 8020 --host 0.0.0.0     # CI: the game container calls it
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def score(text: str) -> float:
    return int(hashlib.sha256(text.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


class Handler(BaseHTTPRequestHandler):
    requests = 0

    def _send(self, status: int, body: dict) -> None:
        out = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def do_GET(self):
        self._send(200, {"status": "ok", "model": "pick-model", "version": "e2e", "requests": Handler.requests})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Handler.requests += 1
        scores = [score(c) for c in body["cards"]]
        exp = [math.exp(s - max(scores)) for s in scores]
        self._send(200, {"model": "pick-model", "version": "e2e", "scores": scores,
                         "probs": [e / sum(exp) for e in exp], "refresh_below": None})

    def log_message(self, *args):
        pass


def start(host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8020)
    args = parser.parse_args()
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
