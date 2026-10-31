"""Demo server. Standard library only.

Run:  python -m adtrace.app        then open http://localhost:8000
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .engine import Engine
from .llm import summarise

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "adtrace" / "static"
ENGINE = Engine()


class H(BaseHTTPRequestHandler):
    def _send(self, code, body: bytes, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        u = urlparse(self.path)
        qs = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path in ("/", "/index.html"):
            return self._send(200, (STATIC / "index.html").read_bytes(), "text/html")
        if u.path == "/api/ask":
            r = ENGINE.ask(qs.get("q", ""), qs.get("model") or None, qs.get("serial") or None)
            s = summarise(r)
            if s:
                r["ai_summary"] = s
            return self._send(200, json.dumps(r).encode())
        if u.path == "/api/check":
            return self._send(200, json.dumps(ENGINE.check(qs.get("model", "").strip(), qs.get("serial") or None)).encode())
        if u.path == "/api/chunk":
            cid = qs.get("cid", "")
            c = next((c for c in ENGINE.chunks if c["cid"] == cid), None)
            if not c:
                return self._send(404, b'{"error":"not found"}')
            d = ENGINE.docs[c["doc"]]
            return self._send(200, json.dumps({"chunk": c, "doc": {k: d[k] for k in ["id", "docket", "subject", "issue_date", "publish_date", "citation", "pages", "fr_cite", "url", "kind", "applicability", "app_text", "body_source"]},
                                               "sections": [x for x in ENGINE.by_doc[c["doc"]]]}).encode())
        if u.path == "/api/stats":
            ix = ENGINE.ix
            return self._send(200, json.dumps({"corpus": ix.get("corpus", {}), "docs": len(ix["docs"]), "chunks": len(ix["chunks"]), "dockets": len(ix["chains"]),
                                               "withdrawn": sum(c["status"] == "WITHDRAWN" for c in ix["chains"].values()),
                                               "full_text": sum(d["has_full_text"] for d in ix["docs"])}).encode())
        return self._send(404, b'{"error":"not found"}')


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    print(f"Clearway running on http://localhost:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()


if __name__ == "__main__":
    main()
