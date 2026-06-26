#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from quant_engine import QuantError, factor_analysis, monte_carlo, scenario_analysis


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
PORT = 8866


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/":
            self.serve_file(STATIC / "index.html", "text/html")
            return
        if path in {"/app.js", "/styles.css"}:
            content_type = "application/javascript" if path.endswith(".js") else "text/css"
            self.serve_file(STATIC / path.lstrip("/"), content_type)
            return
        self.send_json({"error": "Not found"}, status=404)

    def do_POST(self) -> None:
        try:
            payload = self.read_json()
            if self.path == "/portfolio/factor-analysis":
                self.send_json(factor_analysis(payload))
            elif self.path == "/portfolio/scenario":
                self.send_json(scenario_analysis(payload))
            elif self.path == "/portfolio/monte-carlo":
                self.send_json(monte_carlo(payload))
            else:
                self.send_json({"error": "Endpoint non trovato."}, status=404)
        except (QuantError, ValueError, KeyError) as exc:
            self.send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            self.send_json({"error": f"Errore interno: {exc}"}, status=500)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def serve_file(self, path: Path, content_type: str) -> None:
        if not path.exists():
            self.send_json({"error": "File non trovato."}, status=404)
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args: Any) -> None:
        return


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Factor Stress standalone: http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer chiuso.")


if __name__ == "__main__":
    main()

