from __future__ import annotations

from functools import wraps
import hashlib
import json
from pathlib import Path
import re

from .contracts import STYLE_SCHEMA, REWRITE_SCHEMA, VERIFY_SCHEMA, validate_profile
from .llm import Cancelled, LocalModel, ModelError
from .protection import ProtectedDocument, qualification_findings

PROMPTS = Path(__file__).parent / "prompts"
PROMPT_VERSION = "recast-0.1"
ENGINE_FINGERPRINT = hashlib.sha256(b"".join((Path(__file__).parent / name).read_bytes() for name in ["pipeline.py", "protection.py", "documents.py", "contracts.py", "llm.py"])).hexdigest()


def exclusive(function):
    @wraps(function)
    def run(self, *args, **kwargs):
        if getattr(self, "_lease_held", False):
            return function(self, *args, **kwargs)
        with self.store.inference_lease():
            self._lease_held = True
            try:
                return function(self, *args, **kwargs)
            finally:
                self._lease_held = False
    return run


def prompt(name):
    return (PROMPTS / (name + ".txt")).read_text(encoding="utf-8")


def samples(text, maximum=8):
    pieces = []
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = paragraph.strip()
        if paragraph:
            pieces.extend(paragraph[pos:pos + 750] for pos in range(0, len(paragraph), 750))
    if len(pieces) <= maximum:
        return pieces
    indexes = [round(i * (len(pieces) - 1) / (maximum - 1)) for i in range(maximum)]
    return [pieces[index] for index in indexes]


class Pipeline:
    def __init__(self, store, job_id, client=None, progress=None):
        self.store, self.job_id = store, job_id
        self.job = store.get(job_id)
        self.model = client or LocalModel(self.job["model"])
        self.progress = progress or (lambda message: None)

    def update(self, **changes):
        self.job = self.store.update(self.job_id, **changes)
        if "stage" in changes:
            self.progress(changes["stage"])

    @exclusive
    def analyze(self):
        self.update(status="analyzing", stage="Reading the reference's writing style…", error=None)
        excerpts = samples(self.job["reference"]["text"])
        profile = self.model.complete(prompt("style"), {"reference_samples": excerpts}, STYLE_SCHEMA, "style", max_tokens=2048)
        validate_profile(profile)
        warnings = list(self.job["warnings"])
        joined = "\n".join(excerpts)
        for trait in profile["traits"]:
            if trait["evidence"] and trait["evidence"] not in joined:
                trait["evidence"] = ""
                trait["confidence"] = "low"
                warnings.append("A style observation lacked an exact supporting excerpt; its confidence was reduced.")
        self.store.save(self.job_id, "profile.json", profile)
        self.update(status="profile_ready", stage="Review the style profile, then rewrite.", profile=profile, warnings=list(dict.fromkeys(warnings)), progress=0, analysis_calls=self.model.calls.copy())
        return profile

    def _sections(self, protected):
        groups, current, size = [], [], 0
        for block in protected.blocks:
            if not block.editable:
                continue
            if current and (size + len(block.text) > 3400 or len(current) >= 4):
                groups.append(current)
                current, size = [], 0
            current.append(block)
            size += len(block.text)
        if current:
            groups.append(current)
        return groups

    def _rewrite_section(self, protected, blocks, examples, profile, repairs):
        expected = [block.id for block in blocks]
        source = {block.id: protected.restore(block.text) for block in blocks}
        source_views = {block.id: protected.semantic_view(block.text) for block in blocks}
        data = {"source_blocks": [{"id": block.id, "text": block.text} for block in blocks], "style_profile": profile, "style_examples": examples, "style_strength": self.job["strength"]}
        previous_findings, candidate, claims = [], {}, []
        for attempt in range(repairs + 1):
            self.model.check_cancelled()
            try:
                if previous_findings:
                    data["repair_findings"] = previous_findings
                    data["previous_attempt"] = candidate
                result = self.model.complete(prompt("rewrite"), data, REWRITE_SCHEMA, "rewrite", temperature=0.5, max_tokens=4096)
                if [item["id"] for item in result["blocks"]] != expected:
                    raise ModelError("The rewrite changed block IDs, ordering, or coverage.")
                candidate = {item["id"]: item["text"] for item in result["blocks"]}
                bad = [(block.id, error) for block in blocks for error in protected.validate(block.text, candidate[block.id])]
                if bad:
                    raise ModelError("; ".join(f"{block_id}: {error}" for block_id, error in bad))
                if attempt < repairs and all(
                    re.sub(r"\s+", " ", source_views[key]).strip()
                    == re.sub(r"\s+", " ", protected.semantic_view(candidate[key])).strip()
                    for key in expected
                ):
                    previous_findings = [{"block_id": key, "type": "style_unchanged", "severity": "warning", "explanation": "The prior attempt changed only whitespace. Make a faithful wording or rhythm change where the reference supports one; keep the original if no such change is possible."} for key in expected]
                    continue
                restored = {key: protected.restore(text) for key, text in candidate.items()}
                output_views = {key: protected.semantic_view(text) for key, text in candidate.items()}
                verification = self.model.complete(prompt("verify"), {"blocks": [{"block_id": key, "source": source_views[key], "output": output_views[key]} for key in expected]}, VERIFY_SCHEMA, "verify", max_tokens=3072)
                self.store.save(self.job_id, f"review-{expected[0]}-{attempt}.json", {"source": source, "candidate": restored, "verification": verification})
                claims = verification["claims"]
                if any(claim["block_id"] not in expected for claim in claims):
                    raise ModelError("The verification claim inventory referred to an unknown source block.")
                findings = verification["findings"]
                covered = {claim["block_id"] for claim in claims}
                for key in expected:
                    if key not in covered:
                        findings.append({"block_id": key, "type": "verification_incomplete", "severity": "warning", "explanation": "The model did not list source claims for this block. Review its meaning against the original before using the rewrite.", "source_excerpt": source[key][:500], "output_excerpt": restored[key][:500]})
                for finding in findings:
                    key = finding["block_id"]
                    if key not in expected:
                        raise ModelError("A verifier finding referred to an unknown source block.")
                    if finding["source_excerpt"] not in source[key] or finding["output_excerpt"] not in restored[key]:
                        # A paraphrased or invented quotation cannot substantiate
                        # a material error. Surface uncertainty with exact context.
                        finding.update(severity="warning", evidence_unverified=True, source_excerpt=source[key], output_excerpt=restored[key], explanation="The verifier could not anchor its quotation to exact evidence. Review this section. Its concern was: " + finding["explanation"])
                for key in expected:
                    findings.extend(qualification_findings(source[key], restored[key], key))
                if not findings:
                    return {"replacements": candidate, "claims": claims, "findings": [], "attempts": attempt + 1}
                previous_findings = findings
            except Cancelled:
                raise
            except (ModelError, ValueError, KeyError) as exc:
                previous_findings = [{"block_id": key, "type": "validation", "severity": "error", "explanation": str(exc), "source_excerpt": source[key], "output_excerpt": ""} for key in expected]
        # An unresolved rewrite is never silently exported as faithful.
        material = any(f["severity"] == "error" for f in previous_findings)
        if material or not candidate:
            candidate = {block.id: block.text for block in blocks}
            for finding in previous_findings:
                finding["source_retained"] = True
            previous_findings.append({"block_id": expected[0], "type": "source_retained", "severity": "warning", "explanation": "The original section was kept because the rewrite could not pass preservation checks.", "source_excerpt": "", "output_excerpt": ""})
        return {"replacements": candidate, "claims": claims, "findings": previous_findings, "attempts": repairs + 1}

    @exclusive
    def rewrite(self, profile=None, repairs=1):
        if repairs not in {0, 1, 2}:
            raise ValueError("Repair attempts must be 0, 1, or 2.")
        profile = profile or self.job.get("profile")
        if not profile:
            if self.job["mode"] == "direct":
                profile = {"summary": "Direct reference-conditioned baseline; no style-analysis model call.", "traits": [], "rules": ["Infer the writing style directly from the reference examples."], "avoid": []}
            else:
                profile = self.analyze()
        call_start = len(self.model.calls)
        validate_profile(profile)
        self.store.save(self.job_id, "profile.json", profile)
        self.update(status="rewriting", stage="Planning source sections…", profile=profile, output_name=None, report=None, error=None)
        protected = ProtectedDocument(self.job["source"]["text"], self.job["source"]["format"])
        sections = self._sections(protected)
        if not sections:
            raise ValueError("The source has no editable prose outside protected markup, equations, quotations, or code.")
        examples = samples(self.job["reference"]["text"], 3)
        examples = [part[:500] for part in examples]
        if self.job["mode"] == "direct":
            profile_for_model = {"instruction": "Infer and imitate the writing style of the provided examples while preserving source meaning."}
            examples = samples(self.job["reference"]["text"], 8)
        else:
            profile_for_model = profile
        key_data = {"source": self.job["source"]["sha256"], "reference": self.job["reference"]["sha256"], "profile": profile, "model": self.job["model"], "model_fingerprint": self.model.fingerprint, "engine_fingerprint": ENGINE_FINGERPRINT, "strength": self.job["strength"], "mode": self.job["mode"], "repairs": repairs, "prompts": [prompt(name) for name in ["style", "rewrite", "verify"]], "version": PROMPT_VERSION}
        run_key = hashlib.sha256(json.dumps(key_data, sort_keys=True).encode()).hexdigest()
        checkpoint = self.store.load(self.job_id, "checkpoint.json", {})
        completed = checkpoint.get("sections", {}) if checkpoint.get("key") == run_key else {}
        replacements, findings, claims = {}, [], []
        for index, blocks in enumerate(sections):
            self.model.check_cancelled()
            self.update(stage=f"Rewriting and checking section {index + 1} of {len(sections)}…", progress=round(index / len(sections) * 100), sections_total=len(sections))
            result = completed.get(str(index))
            if result is None:
                result = self._rewrite_section(protected, blocks, examples, profile_for_model, repairs)
                completed[str(index)] = result
                self.store.save(self.job_id, "checkpoint.json", {"key": run_key, "sections": completed})
            replacements.update(result["replacements"])
            findings.extend(result["findings"])
            claims.extend(result["claims"])
        self.model.check_cancelled()
        output = protected.assemble(replacements)
        changed_blocks = sum(
            re.sub(r"\s+", " ", protected.semantic_view(block.text)).strip()
            != re.sub(r"\s+", " ", protected.semantic_view(replacements.get(block.id, block.text))).strip()
            for block in protected.blocks if block.editable
        )
        if not changed_blocks and not any(f["type"] == "source_retained" for f in findings):
            findings.append({"block_id": sections[0][0].id, "type": "style_unchanged", "severity": "warning", "explanation": "The model made no prose changes. The source may already match the reference, or this model could not apply the style faithfully. Try another model or a more distinctive reference.", "source_excerpt": "", "output_excerpt": ""})
        extension = ".tex" if self.job["source"]["format"] == "latex" else ".md" if self.job["source"]["format"] == "markdown" else ".txt"
        output_name = "recast" + extension
        (self.store.folder(self.job_id) / output_name).write_bytes(output.encode("utf-8"))
        report = {"prompt_version": PROMPT_VERSION, "engine_fingerprint": ENGINE_FINGERPRINT, "run_key": run_key, "model": self.job["model"], "model_fingerprint": self.model.fingerprint, "mode": self.job["mode"], "sections": len(sections), "changed_blocks": changed_blocks, "protected_spans": len(protected.protected), "claims": claims, "findings": findings, "warnings": self.job["warnings"], "calls": self.job.get("analysis_calls", []) + self.model.calls[call_start:], "verification": "Automated review support; content preservation is not guaranteed.", "block_mapping": [{"id": block.id, "source": protected.restore(block.text), "output": protected.restore(replacements.get(block.id, block.text)), "editable": block.editable} for block in protected.blocks]}
        self.store.save(self.job_id, "report.json", report)
        status = "needs_review" if findings else "completed"
        self.update(status=status, stage="Rewrite ready. Review the findings." if status == "needs_review" else "Rewrite and automated checks complete.", output_name=output_name, report=report, progress=100)
        return self.store.get(self.job_id)
