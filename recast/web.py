"""Loopback-only browser application with one pipeline worker."""

from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import threading
from urllib.parse import parse_qs, urlparse

from .contracts import validate_profile
from .llm import Cancelled, DEFAULT_URL, LocalModel, configured_models
from .pipeline import Pipeline
from .storage import Store

STATIC = Path(__file__).parent / "static"
BUSY = {"queued", "analyzing", "rewriting", "cancelling"}


class Jobs:
    def __init__(self, store, base_url=DEFAULT_URL):
        self.store, self.base_url = store, base_url
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="recast")
        self.lock = threading.Lock()
        self.pending = {}

    def submit(self, job_id, action, profile=None):
        with self.lock:
            if job_id in self.pending:
                raise ValueError("This document already has an active task.")
            if len(self.pending) >= 2:
                raise ValueError("The local queue is full. Wait for the current documents to finish.")
            job = self.store.get(job_id)
            if job["status"] in BUSY:
                raise ValueError("This job is active in another Recast process.")
            client = LocalModel(job["model"], self.base_url)
            if profile is not None:
                validate_profile(profile)
            self.pending[job_id] = client
            self.store.update(job_id, status="queued", stage="Waiting for the local model…", error=None)
            self.executor.submit(self._run, job_id, action, client, profile)

    def _run(self, job_id, action, client, profile):
        try:
            pipeline = Pipeline(self.store, job_id, client)
            if action == "analyze":
                pipeline.analyze()
            else:
                pipeline.rewrite(profile)
        except Cancelled as exc:
            self.store.update(job_id, status="cancelled", stage=str(exc))
        except Exception as exc:
            self.store.update(job_id, status="failed", stage="Task stopped. Correct the issue, then try again.", error=str(exc))
        finally:
            with self.lock:
                self.pending.pop(job_id, None)

    def cancel(self, job_id):
        with self.lock:
            client = self.pending.get(job_id)
            if client:
                self.store.update(job_id, status="cancelling", stage="Cancelling the current model request…")
                client.cancel()
            else:
                raise ValueError("This document has no active task to cancel.")

    def delete(self, job_id):
        with self.lock:
            if job_id in self.pending:
                raise ValueError("Cancel the active task and wait for it to stop before deleting.")
            self.store.delete(job_id)

    def close(self):
        with self.lock:
            for client in self.pending.values():
                client.cancel()
        self.executor.shutdown(wait=True, cancel_futures=False)


def handler_for(store, jobs):
    class Handler(BaseHTTPRequestHandler):
        server_version = "Recast/0.1"

        def log_message(self, format, *args):
            pass  # Uploaded contents and job URLs stay out of request logs.

        def _trusted(self):
            expected = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            host = self.headers.get("Host", "")
            if host not in expected:
                return False
            origin = self.headers.get("Origin")
            return (not origin or origin in {"http://" + value for value in expected}) and self.headers.get("Sec-Fetch-Site") != "cross-site"

        def _send(self, status, body, content_type="application/json", filename=None):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if filename:
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _body(self):
            if self.headers.get_content_type() != "application/json":
                raise ValueError("Use an application/json request.")
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 64 * 1024 * 1024:
                raise ValueError("Request exceeds the 64 MB upload limit, or is empty.")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("Request must be a JSON object.")
            return body

        def do_GET(self):
            if not self._trusted():
                return self._send(403, {"error": "Use the local Recast address."})
            parsed = urlparse(self.path)
            try:
                if parsed.path in {"/", "/app.js", "/diff.js", "/style.css"}:
                    name = "index.html" if parsed.path == "/" else parsed.path[1:]
                    return self._send(200, (STATIC / name).read_bytes(), (mimetypes.guess_type(name)[0] or "text/plain") + "; charset=utf-8")
                if parsed.path == "/api/models":
                    entries = list(configured_models().values())
                    try:
                        remote = LocalModel(entries[0]["id"], jobs.base_url).request("/models", timeout=3)
                        states = {item["id"]: item.get("status", {}).get("value", "unknown") for item in remote["data"]}
                        return self._send(200, {"available": True, "models": [{"id": entry["id"], "label": entry["label"], "state": states.get(entry["id"], "unavailable")} for entry in entries]})
                    except Exception:
                        return self._send(200, {"available": False, "models": [{"id": entry["id"], "label": entry["label"]} for entry in entries], "message": "Run `python scripts/launch.py router` to connect the local models."})
                match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})(/download)?", parsed.path)
                if match:
                    job_id = match.group(1)
                    if not match.group(2):
                        return self._send(200, store.public(job_id))
                    job = store.get(job_id)
                    artifact = parse_qs(parsed.query).get("artifact", ["output"])[0]
                    if artifact == "profile" and job.get("profile"):
                        return self._send(200, job["profile"], filename="recast-style.json")
                    if job["status"] not in {"completed", "needs_review"}:
                        raise ValueError("Finish the rewrite before downloading its result.")
                    if artifact == "report":
                        return self._send(200, job["report"], filename="recast-review.json")
                    if artifact == "output":
                        return self._send(200, (store.folder(job_id) / job["output_name"]).read_bytes(), "text/plain; charset=utf-8", job["output_name"])
                    raise ValueError("Unknown artifact.")
                self._send(404, {"error": "Not found."})
            except (ValueError, OSError, KeyError) as exc:
                self._send(400, {"error": str(exc)})

        def do_POST(self):
            if not self._trusted():
                return self._send(403, {"error": "Use the local Recast address."})
            try:
                body = self._body()
                if self.path == "/api/jobs":
                    model = body.get("model", "recast-default")
                    if model not in configured_models():
                        raise ValueError("Choose a configured model.")
                    source, reference = body["source"], body["reference"]
                    job = store.create(source["name"], base64.b64decode(source["data"], validate=True), reference["name"], base64.b64decode(reference["data"], validate=True), model, body.get("strength", "balanced"))
                    try:
                        jobs.submit(job["id"], "analyze")
                    except Exception:
                        store.delete(job["id"])
                        raise
                    return self._send(202, {"id": job["id"]})
                match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})/(analyze|rewrite|cancel)", self.path)
                if match:
                    job_id, action = match.groups()
                    if action == "cancel":
                        jobs.cancel(job_id)
                    else:
                        jobs.submit(job_id, action, body.get("profile") if action == "rewrite" else None)
                    return self._send(202, {"id": job_id})
                self._send(404, {"error": "Not found."})
            except (ValueError, TypeError, KeyError, OSError) as exc:
                self._send(400, {"error": str(exc)})

        def do_DELETE(self):
            if not self._trusted():
                return self._send(403, {"error": "Use the local Recast address."})
            match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})", self.path)
            try:
                if not match:
                    return self._send(404, {"error": "Not found."})
                jobs.delete(match.group(1))
                self._send(200, {"deleted": True})
            except (ValueError, OSError) as exc:
                self._send(400, {"error": str(exc)})

    return Handler


class AppServer(ThreadingHTTPServer):
    # Windows SO_REUSEADDR permits binding a port another process already owns,
    # which would silently serve nothing; fail loudly instead.
    allow_reuse_address = os.name != "nt"


def serve(port=8020, base_url=DEFAULT_URL, data_dir=None):
    store = Store(data_dir)
    jobs = Jobs(store, base_url)
    server = AppServer(("127.0.0.1", port), handler_for(store, jobs))
    print(f"Recast is available at http://127.0.0.1:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        jobs.close()
