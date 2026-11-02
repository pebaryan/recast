"""Loopback proxy so Recast can evaluate models on a remote llama.cpp server.

Recast only permits loopback base URLs; this forwards such requests to the
remote server and rewrites the model alias in the query string or JSON body.
"""

import argparse
import json
from pathlib import Path
import http.client
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remote", required=True, help="host:port of the remote llama.cpp server")
    parser.add_argument("--port", type=int, default=8100)
    parser.add_argument("--map", nargs="+", required=True, help="alias=remote_id pairs")
    parser.add_argument("--shim", action="store_true", help="synthesize llama.cpp /apply-template and /tokenize for non-llama.cpp services")
    args = parser.parse_args(argv)
    mapping = {}
    for item in args.map:
        name, remote_id = item.split("=", 1)
        mapping[name] = remote_id
    host, _, port_s = args.remote.partition(":")
    port = int(port_s or "80")

    class Handler(BaseHTTPRequestHandler):
        def shim_endpoint(self, path, body):
            parsed = urlparse(path)
            if not args.shim:
                return None
            data = json.loads(body) if body else {}
            if parsed.path == "/apply-template":
                parts = []
                for message in data.get("messages", []):
                    parts.append((message.get("role") or "user").upper() + ": " + message.get("content", ""))
                return json.dumps({"prompt": "\n".join(parts) + "\nASSISTANT:"})
            if parsed.path == "/tokenize":
                return json.dumps({"tokens": list(range(1, len(str(data.get("content", "")).split()) + 1))})
            return None

        def rewrite(self, path, body):
            parsed = urlparse(path)
            pairs = parse_qsl(parsed.query, keep_blank_values=True)
            pairs = [(k, mapping.get(v, v)) if k == "model" else (k, v) for k, v in pairs]
            new_path = parsed.path + ("?" + urlencode(pairs) if pairs else "")
            if body:
                try:
                    data = json.loads(body)
                    if isinstance(data, dict) and data.get("model") in mapping:
                        data["model"] = mapping[data["model"]]
                        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
                except (ValueError, TypeError):
                    pass
            return new_path, body

        def forward(self, method):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else None
            shimmed = self.shim_endpoint(self.path, body)
            if shimmed is not None:
                self.respond(200, "application/json", shimmed.encode("utf-8"))
                return
            path, body = self.rewrite(self.path, body)
            connection = http.client.HTTPConnection(host, port, timeout=1800)
            try:
                connection.request(method, path, body, {"Content-Type": "application/json"} if body else {})
                response = connection.getresponse()
                raw = response.read(16 * 1024 * 1024 + 1)
            except (OSError, http.client.HTTPException) as exc:
                raw = json.dumps({"error": {"message": f"proxy forward failed: {exc}"}}).encode()
                status, content_type = 500, "application/json"
                self.respond(status, content_type, raw)
                return
            finally:
                try:
                    connection.close()
                except Exception:
                    pass
            self.respond(response.status, response.getheader("Content-Type", "application/json"), raw)

        def respond(self, status, content_type, raw):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self): self.forward("GET")
        def do_POST(self): self.forward("POST")
        def do_DELETE(self): self.forward("DELETE")
        def do_PUT(self): self.forward("PUT")

    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
