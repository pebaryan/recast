from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
import re
import shutil
import threading
import time
import uuid

from .documents import read_document
from .llm import ROOT


class InferenceBusy(RuntimeError):
    pass


class Store:
    def __init__(self, root=None):
        default_root = ROOT if (ROOT / "config" / "models.ini").exists() else Path.cwd()
        self.root = Path(root or default_root / "data" / "jobs").resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        try:
            with self.inference_lease():
                self._recover_interrupted()
        except InferenceBusy:
            pass  # Another live CLI/app process owns the pipeline.

    @contextmanager
    def inference_lease(self):
        handle = (self.root.parent / ".inference.lock").open("a+b")
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise InferenceBusy("Another Recast process is using the model. Wait for it to finish, then resume this job.") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    def _recover_interrupted(self):
        for folder in self.root.iterdir():
            if folder.is_dir() and re.fullmatch(r"[a-f0-9]{32}", folder.name):
                try:
                    job = self.get(folder.name)
                    if job["status"] in {"queued", "analyzing", "rewriting", "cancelling"}:
                        self.update(folder.name, status="interrupted", stage="Interrupted; analyze or rewrite again to resume completed work.")
                except (OSError, ValueError, KeyError):
                    continue

    def folder(self, job_id):
        if not re.fullmatch(r"[a-f0-9]{32}", job_id):
            raise ValueError("Invalid job ID.")
        folder = (self.root / job_id).resolve()
        if not folder.is_relative_to(self.root) or folder == self.root:
            raise ValueError("Job path is outside the data directory.")
        return folder

    def save(self, job_id, name, value):
        if Path(name).name != name:
            raise ValueError("Invalid artifact name.")
        with self.lock:
            folder = self.folder(job_id)
            folder.mkdir(exist_ok=True)
            target = folder / name
            temporary = folder / (name + ".tmp")
            temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(target)

    def load(self, job_id, name, default=None):
        if Path(name).name != name:
            raise ValueError("Invalid artifact name.")
        with self.lock:
            file = self.folder(job_id) / name
            return json.loads(file.read_text(encoding="utf-8")) if file.exists() else default

    def get(self, job_id):
        job = self.load(job_id, "job.json")
        if job is None:
            raise ValueError("Job not found.")
        return job

    def update(self, job_id, **changes):
        with self.lock:
            job = self.get(job_id)
            job.update(changes, updated_at=time.time())
            self.save(job_id, "job.json", job)
            return job

    def create(self, source_name, source_data, reference_name, reference_data, model, strength="balanced", mode="profile"):
        if strength not in {"subtle", "balanced", "strong"} or mode not in {"profile", "direct"}:
            raise ValueError("Invalid style strength or rewrite mode.")
        source = read_document(source_name, source_data, "source")
        reference = read_document(reference_name, reference_data, "reference")
        job_id = uuid.uuid4().hex
        folder = self.folder(job_id)
        folder.mkdir()
        (folder / ("source" + Path(source.name).suffix.lower())).write_bytes(source_data)
        (folder / ("reference" + Path(reference.name).suffix.lower())).write_bytes(reference_data)
        now = time.time()
        job = {"id": job_id, "status": "ready", "stage": "Ready to analyze the reference.", "model": model, "strength": strength, "mode": mode, "source": source.to_dict(), "reference": reference.to_dict(), "warnings": source.warnings + reference.warnings, "created_at": now, "updated_at": now, "progress": 0, "profile": None, "output_name": None, "report": None}
        self.save(job_id, "job.json", job)
        return job

    def delete(self, job_id):
        with self.lock:
            folder = self.folder(job_id)
            if folder.exists():
                # Resolved target is checked by folder(), before recursive deletion.
                shutil.rmtree(folder)

    def public(self, job_id):
        job = self.get(job_id)
        result = {key: value for key, value in job.items() if key not in {"source", "reference"}}
        result["source"] = {key: value for key, value in job["source"].items() if key != "text"}
        result["reference"] = {key: value for key, value in job["reference"].items() if key != "text"}
        result["reference_preview"] = job["reference"]["text"][:20000]
        result["reference_preview_truncated"] = len(job["reference"]["text"]) > 20000
        result["source_text"] = job["source"]["text"]
        if job["status"] in {"completed", "needs_review"}:
            output = self.folder(job_id) / job["output_name"]
            result["output_text"] = output.read_bytes().decode("utf-8")
        return result
