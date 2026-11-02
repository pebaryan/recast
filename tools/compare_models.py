"""Run one fixture pair through each allowed model and compare speed and review signals.

Modes: default assumes one running router (--base-url); --isolated starts a
dedicated single-model llama-server per model with per-model flag overrides."""

from __future__ import annotations

import argparse
import configparser
import difflib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
from pathlib import Path
from urllib.request import urlopen, Request

ROOT = Path(__file__).resolve().parents[1]
INI = Path(os.environ.get("RECAST_CONFIG", ROOT / "config" / "models.ini"))


def word_tokens(text: str) -> list[str]:
    return re.findall(r"\S+", text)


def changed_word_tokens(source: str, output: str) -> int:
    return sum(
        max(a2 - a1, b2 - b1)
        for kind, a1, a2, b1, b2 in difflib.SequenceMatcher(None, word_tokens(source), word_tokens(output), autojunk=False).get_opcodes()
        if kind != "equal"
    )


def server_args(model: str, exe: str, overrides: dict[str, str]) -> list[str]:
    config = configparser.ConfigParser(interpolation=None)
    text = INI.read_text(encoding="utf-8")
    config.read_string("[DEFAULT]\n" + text.replace("[*]", "[DEFAULT]", 1))
    flags = {
        "ctx-size": "--ctx-size", "parallel": "--parallel", "batch-size": "--batch-size",
        "ubatch-size": "--ubatch-size", "n-gpu-layers": "--n-gpu-layers",
        "temp": "--temp", "top-p": "--top-p", "top-k": "--top-k", "min-p": "--min-p",
        "n-predict": "--n-predict", "reasoning": "--reasoning",
    }
    args = [exe, "--model", config.get(model, "model"), "--alias", model,
            "--host", "127.0.0.1", "--offline", "--no-webui"]
    if config.getboolean(model, "jinja", fallback=False):
        args.append("--jinja")
    for key, flag in flags.items():
        value = overrides.get(key, config.get(model, key, fallback=None))
        if value is None:
            continue
        if key == "reasoning":
            args += [flag, "on" if str(value).lower() not in {"off", "false", "0", "no"} else "off"]
            continue
        args += [flag, str(value)]
    return args


def start_server(args: list[str], port: int, cache: Path, log: Path) -> subprocess.Popen:
    log.write_text("")
    handle = log.open("w", encoding="utf-8")
    proc = subprocess.Popen(args + ["--port", str(port)], cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT)
    deadline = time.time() + 600
    while time.time() < deadline:
        try:
            if urlopen(f"http://127.0.0.1:{port}/health", timeout=3).read().decode().find('"ok"') >= 0:
                return proc
        except Exception:
            pass
        if proc.poll() is not None:
            raise RuntimeError(f"server on {port} exited early: see log {log}")
        time.sleep(1)
    proc.kill()
    raise RuntimeError(f"server on {port} did not become healthy; see log {log}")


def warm_load(base_url: str, model: str) -> float:
    started = time.monotonic()
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 4}).encode()
    try:
        urlopen(Request(base_url.rstrip("/") + "/v1/chat/completions", body, {"Content-Type": "application/json"}), timeout=900).read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:500]}")
    return round(time.monotonic() - started, 1)


def run_cli(source: Path, reference: Path, model: str, output: Path, data_dir: Path, base_url: str, strength: str, mode: str, repairs: int, timeout: int) -> dict:
    if output.exists():
        output.unlink()
    started = time.monotonic()
    completed = subprocess.run(
        ["python", "-X", "utf8", "-m", "recast", "rewrite", "--source", str(source), "--reference", str(reference), "--output", str(output), "--model", model, "--base-url", base_url, "--data-dir", str(data_dir), "--strength", strength, "--mode", mode, "--repairs", str(repairs)],
        cwd=ROOT, capture_output=True, text=True, timeout=timeout,
    )
    elapsed = round(time.monotonic() - started, 1)
    try:
        emitted = json.loads(completed.stdout)
    except ValueError:
        emitted = {}
    report_path = Path(emitted.get("report", ""))
    if not report_path.is_file():
        return {"exit": completed.returncode, "seconds": elapsed, "error": (completed.stderr or completed.stdout)[-600:]}
    report = json.loads(report_path.read_text(encoding="utf-8"))
    job = json.loads((report_path.parent / "job.json").read_text(encoding="utf-8"))
    output_text = (report_path.parent / job["output_name"]).read_text(encoding="utf-8")
    per_stage = {}
    prompt_tokens = completion_tokens = 0
    for call in report["calls"]:
        stage = "rewrite" if call["stage"] in {"rewrite", "repair"} else call["stage"]
        per_stage[stage] = round(per_stage.get(stage, 0) + call["seconds"], 1)
        prompt_tokens += call["usage"].get("prompt_tokens", 0)
        completion_tokens += call["usage"].get("completion_tokens", 0)
    editable = sum(item["editable"] for item in report["block_mapping"])
    findings = {}
    for item in report["findings"]:
        findings[item["type"]] = findings.get(item["type"], 0) + 1
    return {
        "exit": completed.returncode,
        "status": job["status"],
        "seconds": elapsed,
        "per_stage_seconds": per_stage,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "editable_blocks": editable,
        "changed_blocks": report["changed_blocks"],
        "changed_word_tokens": changed_word_tokens(job["source"]["text"], output_text),
        "identical": job["source"]["text"] == output_text,
        "whitespace_only": job["source"]["text"] != output_text and re.sub(r"\s+", " ", job["source"]["text"]).strip() == re.sub(r"\s+", " ", output_text).strip(),
        "findings": findings,
        "warnings": report["warnings"],
        "job_id": job["id"],
        "output": str(output),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--models", nargs="+", required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8017")
    parser.add_argument("--isolated", action="store_true", help="start a dedicated single-model server per model")
    parser.add_argument("--server-exe", default=os.environ.get("RECAST_LLAMA_SERVER", "llama-server"))
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--port-base", type=int, default=8300)
    parser.add_argument("--override", nargs="*", default=[], help="NAME=key=value GPU overrides, e.g. recast-quality=n-gpu-layers=8")
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--strength", choices=["subtle", "balanced", "strong"], default="balanced")
    parser.add_argument("--mode", choices=["profile", "direct"], default="profile")
    parser.add_argument("--repairs", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--skip-warmup", action="store_true")
    args = parser.parse_args(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    overrides = {}
    for item in args.override:
        name, pair = item.split("=", 1)
        key, value = pair.split("=", 1)
        overrides.setdefault(name, {})[key] = value
    results = {}
    for index, model in enumerate(args.models):
        print(f"model: {model}", flush=True)
        base_url = args.base_url
        server = None
        try:
            if args.isolated:
                args_list = server_args(model, args.server_exe, overrides.get(model, {}))
                cache = args.cache_dir or (args.out_dir / "cache")
                cache.mkdir(exist_ok=True)
                server = start_server(args_list, args.port_base + index, cache, args.out_dir / f"{model}.server.log")
                base_url = f"http://127.0.0.1:{args.port_base + index}"
            warmup = 0.0 if args.skip_warmup else warm_load(base_url, model)
            attempt = 0
            while True:
                output = args.out_dir / f"{model}{args.source.suffix.lower()}"
                result = run_cli(args.source, args.reference, model, output, args.data_dir, base_url, args.strength, args.mode, args.repairs, args.timeout)
                if result.get("exit") != 1 or attempt >= args.retries:
                    break
                attempt += 1
                print(f"retry {attempt} for {model}", flush=True)
            result["warmup_seconds"] = warmup
        except Exception as exc:
            result = {"exit": 1, "seconds": -1, "warmup_seconds": -1, "error": f"{type(exc).__name__}: {exc}"[:300]}
        finally:
            if server:
                server.kill()
                server.wait()
        results[model] = result
        print(json.dumps(result, indent=2), flush=True)
    summary = Path(args.out_dir) / "comparison.json"
    summary.write_text(json.dumps({"created": time.strftime("%Y-%m-%d %H:%M:%S"), "strength": args.strength, "mode": args.mode, "repairs": args.repairs, "results": results}, indent=2), encoding="utf-8")
    header = ["model", "status", "exit", "run_s", "warmup_s", "style_s", "rewrite_s", "verify_s", "prompt_tok", "completion_tok", "changed_blocks/editable", "changed_words", "findings"]
    print("\n".join(["\t".join(header)] + ["\t".join([str(r) for r in [
        model,
        results[model].get("status", "failed"),
        results[model]["exit"],
        results[model]["seconds"],
        results[model]["warmup_seconds"],
        results[model].get("per_stage_seconds", {}).get("style", ""),
        results[model].get("per_stage_seconds", {}).get("rewrite", ""),
        results[model].get("per_stage_seconds", {}).get("verify", ""),
        results[model].get("prompt_tokens", ""),
        results[model].get("completion_tokens", ""),
        f"{results[model].get('changed_blocks', '')}/{results[model].get('editable_blocks', '')}" if "changed_blocks" in results[model] else "",
        results[model].get("changed_word_tokens", ""),
        ";".join(f"{k}={v}" for k, v in results[model].get("findings", {}).items()),
    ]]) for model in results]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
