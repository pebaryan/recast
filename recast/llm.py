"""Bounded, cancellable HTTP client for the local llama.cpp router."""

from __future__ import annotations

import configparser
import hashlib
import http.client
import json
import os
from pathlib import Path
import socket
import threading
import time
import sys
from urllib.parse import urlencode, urlparse

from .contracts import validate

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_URL = "http://127.0.0.1:8017"


class ModelError(RuntimeError):
    pass


class Cancelled(ModelError):
    pass


def configured_models() -> dict[str, dict]:
    config = configparser.ConfigParser(interpolation=None)
    paths = [Path(os.environ["RECAST_CONFIG"])] if os.environ.get("RECAST_CONFIG") else [ROOT / "config" / "models.ini", Path(sys.prefix) / "share" / "recast" / "config" / "models.ini"]
    config_path = next((path for path in paths if path.is_file()), None)
    if config_path is None:
        raise ModelError("Model preset not found. Run from the Recast checkout or set RECAST_CONFIG to models.ini.")
    # llama.cpp permits a top-level version key before its first INI section.
    config.read_string("[DEFAULT]\n" + config_path.read_text(encoding="utf-8"))
    labels = {"recast-fast": "Qwen 3.5 · 4B", "recast-default": "Qwen 3.5 · 9B", "recast-quality": "Qwen 3.8 · 27B", "recast-gemma": "Gemma 4 · 26B A4B", "recast-sage": "Sage A1.5 · 35B A3B"}
    result = {}
    for name in config.sections():
        if name == "*" or not config.has_option(name, "model"):
            continue
        path = Path(config.get(name, "model"))
        stat = path.stat() if path.is_file() else None
        fingerprint = hashlib.sha256(f"{path}:{stat.st_size if stat else 0}:{stat.st_mtime_ns if stat else 0}".encode()).hexdigest()
        result[name] = {"id": name, "label": labels.get(name, name), "fingerprint": fingerprint}
    return result


class LocalModel:
    def __init__(self, model="recast-default", base_url=DEFAULT_URL, cancelled=None):
        parsed = urlparse(base_url.rstrip("/").removesuffix("/v1"))
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.path not in {"", "/"} or parsed.username or parsed.query or parsed.fragment:
            raise ModelError("The model endpoint must be a local HTTP llama.cpp router URL.")
        models = configured_models()
        if model not in models:
            raise ModelError("Choose a model from config/models.ini.")
        self.host, self.port, self.model = parsed.hostname, parsed.port or 80, model
        self.fingerprint = models[model]["fingerprint"]
        self.cancelled = cancelled or threading.Event()
        self.active = None
        self.lock = threading.Lock()
        self.calls: list[dict] = []

    def check_cancelled(self):
        if self.cancelled.is_set():
            raise Cancelled("Cancelled. Completed sections remain available for resuming.")

    def cancel(self):
        self.cancelled.set()
        with self.lock:
            if self.active and self.active.sock:
                try:
                    self.active.sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def request(self, path, body=None, timeout=600):
        self.check_cancelled()
        connection = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        with self.lock:
            self.active = connection
        try:
            payload = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
            connection.request("POST" if body is not None else "GET", path, payload, {"Content-Type": "application/json"})
            response = connection.getresponse()
            raw = response.read(8 * 1024 * 1024 + 1)
            self.check_cancelled()
            if len(raw) > 8 * 1024 * 1024:
                raise ModelError("The model response exceeded the response size limit.")
            if response.status >= 400:
                raise ModelError(f"llama.cpp returned HTTP {response.status}: {raw.decode('utf-8', 'replace')[:700]}")
            return json.loads(raw)
        except (OSError, http.client.HTTPException, ValueError) as exc:
            self.check_cancelled()
            raise ModelError(f"Cannot use llama.cpp at {self.host}:{self.port}. Start scripts/start-router.ps1. {exc}") from exc
        finally:
            connection.close()
            with self.lock:
                if self.active is connection:
                    self.active = None

    def count_prompt(self, messages) -> int:
        query = "?" + urlencode({"model": self.model})
        prompt = self.request("/apply-template" + query, {"model": self.model, "messages": messages})["prompt"]
        tokens = self.request("/tokenize" + query, {"model": self.model, "content": prompt, "add_special": False, "parse_special": True})["tokens"]
        return len(tokens)

    def complete(self, system, data, schema, stage, temperature=0.2, max_tokens=2048):
        messages = [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(data, ensure_ascii=False)}]
        started = time.monotonic()
        count = self.count_prompt(messages)
        if count > 11264:
            raise ModelError(f"{stage} needs {count} prompt tokens; the 11,264-token prompt budget was exceeded. Reduce the section or style profile.")
        response = self.request("/v1/chat/completions", {
            "model": self.model, "messages": messages, "temperature": temperature,
            "max_tokens": max_tokens, "stream": False,
            "response_format": {"type": "json_schema", "json_schema": {"name": "recast_" + stage, "strict": True, "schema": schema}},
        })
        self.calls.append({"stage": stage, "seconds": round(time.monotonic() - started, 3), "prompt_tokens_counted": count, "usage": response.get("usage", {})})
        choice = response.get("choices", [{}])[0]
        if choice.get("finish_reason") != "stop":
            raise ModelError(f"{stage} did not finish normally (possibly truncated). No partial text will be exported.")
        try:
            result = json.loads(choice["message"]["content"])
            validate(result, schema)
            return result
        except (KeyError, TypeError, ValueError) as exc:
            raise ModelError(f"{stage} returned an invalid structured response: {exc}") from exc
