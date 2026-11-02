import base64
from http.server import ThreadingHTTPServer
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from recast.storage import Store
from recast.web import handler_for
from .helpers import docx_bytes


class StubJobs:
    base_url = "http://127.0.0.1:1"

    def __init__(self, store):
        self.store = store

    def submit(self, job_id, action, profile=None):
        self.store.update(job_id, status="queued")

    def delete(self, job_id):
        self.store.delete(job_id)


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "jobs")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(self.store, StubJobs(self.store)))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        connection.request(method, path, json.dumps(body) if body is not None else None, {"Content-Type": "application/json", **(headers or {})})
        response = connection.getresponse()
        result = (response.status, response.read(), dict(response.getheaders()))
        connection.close()
        return result

    def test_actual_docx_upload_and_extracted_preview(self):
        body = {"source": {"name": "a.md", "data": base64.b64encode(b"# Title\n\nWe checked 24 cases.").decode()}, "reference": {"name": "b.docx", "data": base64.b64encode(docx_bytes()).decode()}}
        status, raw, _ = self.request("POST", "/api/jobs", body)
        self.assertEqual(status, 202)
        job_id = json.loads(raw)["id"]
        status, raw, _ = self.request("GET", f"/api/jobs/{job_id}")
        self.assertEqual(status, 200)
        self.assertIn("ordinary words", json.loads(raw)["reference_preview"])
        status, _, _ = self.request("GET", f"/api/jobs/{job_id}/download")
        self.assertEqual(status, 400)
        status, _, _ = self.request("DELETE", f"/api/jobs/{job_id}")
        self.assertEqual(status, 200)
        self.assertFalse(self.store.folder(job_id).exists())

    def test_cross_origin_and_dns_rebinding_requests_are_rejected(self):
        for headers in [{"Origin": "https://other.example"}, {"Host": "evil.example"}, {"Sec-Fetch-Site": "cross-site"}]:
            with self.subTest(headers=headers):
                status, _, _ = self.request("POST", "/api/jobs", {}, headers)
                self.assertEqual(status, 403)

    def test_unsupported_and_malformed_uploads_get_json_errors(self):
        for body in [{}, {"source": None, "reference": None}, {"source": {"name": "bad.pdf", "data": "YmFk"}, "reference": {"name": "b.docx", "data": "YmFk"}}]:
            status, raw, _ = self.request("POST", "/api/jobs", body)
            self.assertEqual(status, 400)
            self.assertIn("error", json.loads(raw))

    def test_static_paths_do_not_expose_local_files(self):
        for path in ["/../config/models.ini", "/config/models.ini", "/.runtime/live/voice.docx"]:
            status, _, _ = self.request("GET", path)
            self.assertEqual(status, 404)
