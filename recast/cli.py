from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .documents import read_path
from .llm import DEFAULT_URL, LocalModel, configured_models
from .pipeline import Pipeline
from .storage import Store


def main(argv=None):
    parser = argparse.ArgumentParser(description="Recast: rewrite source prose in a reference document's writing style.")
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="Extract text without loading a model")
    inspect.add_argument("path", type=Path)
    inspect.add_argument("--role", choices=["source", "reference"], default="reference")
    rewrite = commands.add_parser("rewrite", help="Analyze, rewrite, check, and export")
    rewrite.add_argument("--source", type=Path)
    rewrite.add_argument("--reference", type=Path)
    rewrite.add_argument("--resume", help="Resume a stored job ID")
    rewrite.add_argument("--model", default="recast-default", choices=list(configured_models()))
    rewrite.add_argument("--strength", choices=["subtle", "balanced", "strong"], default="balanced")
    rewrite.add_argument("--mode", choices=["profile", "direct"], default="profile")
    rewrite.add_argument("--profile", type=Path, help="Use an edited style-profile JSON file")
    rewrite.add_argument("--repairs", type=int, choices=[0, 1, 2], default=1)
    rewrite.add_argument("--output", type=Path, required=True)
    rewrite.add_argument("--base-url", default=DEFAULT_URL)
    rewrite.add_argument("--data-dir", type=Path)
    web = commands.add_parser("serve", help="Start the local browser application")
    web.add_argument("--port", type=int, default=8020)
    web.add_argument("--base-url", default=DEFAULT_URL)
    web.add_argument("--data-dir", type=Path)
    args = parser.parse_args(argv)
    store, job_id = None, None
    try:
        if args.command == "inspect":
            print(json.dumps(read_path(args.path, args.role).to_dict(), ensure_ascii=False, indent=2))
            return 0
        if args.command == "serve":
            from .web import serve
            serve(args.port, args.base_url, args.data_dir)
            return 0
        if not args.resume and (not args.source or not args.reference):
            parser.error("rewrite requires --source and --reference, or --resume JOB_ID")
        if args.source and args.output.resolve() == args.source.resolve():
            raise ValueError("Choose a different output path to preserve the source file.")
        if args.output.exists():
            raise ValueError("The output already exists. Choose a new path; existing files are not overwritten.")
        store = Store(args.data_dir)
        if args.resume:
            job = store.get(args.resume)
            if job["status"] in {"queued", "analyzing", "rewriting", "cancelling"}:
                raise ValueError("This job is active in another Recast process. Wait for it to finish.")
            job_id = args.resume
        else:
            read_path(args.source, "source")  # Check sizes before reading file bytes.
            read_path(args.reference, "reference")
            job = store.create(args.source.name, args.source.read_bytes(), args.reference.name, args.reference.read_bytes(), args.model, args.strength, args.mode)
            job_id = job["id"]
        expected_extensions = {"latex": {".tex", ".latex"}, "markdown": {".md", ".markdown"}, "txt": {".txt"}}[job["source"]["format"]]
        if args.output.suffix.lower() not in expected_extensions:
            raise ValueError("Use an output extension matching the source format: " + ", ".join(sorted(expected_extensions)))
        client = LocalModel(job["model"], args.base_url)
        pipeline = Pipeline(store, job_id, client, lambda message: print(message, file=sys.stderr, flush=True))
        profile = json.loads(args.profile.read_text(encoding="utf-8")) if args.profile else job.get("profile")
        if not profile and job["mode"] != "direct":
            profile = pipeline.analyze()
        result = pipeline.rewrite(profile, args.repairs)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes((store.folder(job_id) / result["output_name"]).read_bytes())
        print(json.dumps({"job_id": job_id, "status": result["status"], "output": str(args.output.resolve()), "report": str(store.folder(job_id) / "report.json"), "profile": str(store.folder(job_id) / "profile.json")}, indent=2))
        return 2 if result["status"] == "needs_review" else 0
    except (Exception, KeyboardInterrupt) as exc:
        if isinstance(exc, KeyboardInterrupt):
            if "client" in locals():
                client.cancel()
            message, status = "Cancelled. Resume using the stored job ID.", "cancelled"
        else:
            message, status = str(exc), "failed"
        if store is not None and job_id is not None:
            try:
                store.update(job_id, status=status, stage=message, error=message)
                print(f"Job ID: {job_id}", file=sys.stderr)
            except (ValueError, OSError):
                pass
        print(message, file=sys.stderr)
        return 1
