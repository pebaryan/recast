"""Summarize a saved real-document run without sending its text anywhere."""

from __future__ import annotations

import argparse
import difflib
import json
from pathlib import Path
import re


def summarize(folder: Path) -> dict:
    job = json.loads((folder / "job.json").read_text(encoding="utf-8"))
    report = json.loads((folder / "report.json").read_text(encoding="utf-8"))
    source = job["source"]["text"]
    output = (folder / job["output_name"]).read_text(encoding="utf-8")
    source_words = re.findall(r"\S+", source)
    output_words = re.findall(r"\S+", output)
    changed_words = sum(
        max(a2 - a1, b2 - b1)
        for kind, a1, a2, b1, b2 in difflib.SequenceMatcher(None, source_words, output_words, autojunk=False).get_opcodes()
        if kind != "equal"
    )
    return {
        "job_id": job["id"],
        "status": job["status"],
        "source": job["source"]["name"],
        "reference": job["reference"]["name"],
        "source_sha256": job["source"]["sha256"],
        "reference_sha256": job["reference"]["sha256"],
        "model": job["model"],
        "strength": job["strength"],
        "sections": report["sections"],
        "editable_blocks": sum(item["editable"] for item in report["block_mapping"]),
        "changed_prose_blocks": report["changed_blocks"],
        "changed_word_tokens": changed_words,
        "source_word_tokens": len(source_words),
        "output_word_tokens": len(output_words),
        "identical": source == output,
        "whitespace_only": source != output and re.sub(r"\s+", " ", source).strip() == re.sub(r"\s+", " ", output).strip(),
        "protected_spans": report["protected_spans"],
        "findings": [item["type"] for item in report["findings"]],
        "output": str(folder / job["output_name"]),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("job_ids", nargs="+")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1] / "data" / "jobs"
    print(json.dumps([summarize(root / job_id) for job_id in args.job_ids], indent=2))
