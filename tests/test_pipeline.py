from pathlib import Path
import tempfile
import unittest

from recast.pipeline import Pipeline, samples
from recast.storage import Store, InferenceBusy
from .helpers import FakeModel, REFERENCE
from .test_protection import LATEX


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "jobs")

    def tearDown(self):
        self.temp.cleanup()

    def create(self, text="We checked 24 cases. The results may improve.", name="source.txt"):
        return self.store.create(name, text.encode(), "voice.txt", REFERENCE.encode(), "recast-default")

    def test_pipeline_latex_and_profile_evidence(self):
        job = self.create(LATEX, "source.tex")
        pipeline = Pipeline(self.store, job["id"], FakeModel())
        pipeline.analyze()
        result = pipeline.rewrite()
        output = (self.store.folder(job["id"]) / result["output_name"]).read_text()
        self.assertEqual(output, LATEX.replace("We checked", "We reviewed"))
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["report"]["claims"])
        self.assertEqual(len(result["report"]["calls"]), 3)

    def test_damaged_markers_fall_back_to_source_and_flag_review(self):
        job = self.create()
        pipeline = Pipeline(self.store, job["id"], FakeModel(damage=True))
        pipeline.analyze()
        result = pipeline.rewrite(repairs=1)
        self.assertEqual(result["status"], "needs_review")
        self.assertTrue(any(f["type"] == "source_retained" for f in result["report"]["findings"]))
        self.assertEqual((self.store.folder(job["id"]) / result["output_name"]).read_text(), job["source"]["text"])

    def test_semantic_error_falls_back_to_source(self):
        job = self.create()
        pipeline = Pipeline(self.store, job["id"], FakeModel(findings=True))
        pipeline.analyze()
        result = pipeline.rewrite(repairs=0)
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual((self.store.folder(job["id"]) / result["output_name"]).read_text(), job["source"]["text"])

    def test_truncated_generation_never_exports_partial_text(self):
        job = self.create()
        pipeline = Pipeline(self.store, job["id"], FakeModel(truncate=True))
        pipeline.analyze()
        result = pipeline.rewrite(repairs=0)
        self.assertEqual((self.store.folder(job["id"]) / result["output_name"]).read_text(), job["source"]["text"])

    def test_completed_sections_are_reused_on_resume(self):
        job = self.create()
        first = Pipeline(self.store, job["id"], FakeModel())
        profile = first.analyze()
        first.rewrite(profile)
        second_model = FakeModel()
        result = Pipeline(self.store, job["id"], second_model).rewrite(profile)
        self.assertEqual(second_model.calls, [])
        self.assertEqual(result["status"], "completed")

    def test_samples_cover_reference_end(self):
        text = "\n\n".join(f"Paragraph {i}: " + "Words. " * 80 for i in range(20))
        chosen = samples(text)
        self.assertIn("Paragraph 0:", chosen[0])
        self.assertIn("Paragraph 19:", chosen[-1])

    def test_store_rejects_path_traversal(self):
        with self.assertRaises(ValueError):
            self.store.delete("../../other")

    def test_another_process_cannot_take_the_inference_lease(self):
        with self.store.inference_lease():
            other = Store(self.store.root)
            with self.assertRaises(InferenceBusy):
                with other.inference_lease():
                    pass

    def test_profile_edits_invalidate_completed_sections(self):
        job = self.create()
        first = Pipeline(self.store, job["id"], FakeModel())
        profile = first.analyze()
        first.rewrite(profile)
        profile["rules"] = ["Use a more conversational tone."]
        model = FakeModel()
        Pipeline(self.store, job["id"], model).rewrite(profile)
        self.assertTrue(any(call["stage"] == "rewrite" for call in model.calls))

    def test_unanchored_verifier_quotes_are_uncertain_review_findings(self):
        class UnanchoredModel(FakeModel):
            def complete(self, *args, **kwargs):
                result = super().complete(*args, **kwargs)
                if args[3] == "verify":
                    result["findings"][0]["source_excerpt"] = "This quotation was invented."
                return result
        job = self.create()
        pipeline = Pipeline(self.store, job["id"], UnanchoredModel(findings=True))
        profile = pipeline.analyze()
        result = pipeline.rewrite(profile, repairs=0)
        self.assertEqual(result["status"], "needs_review")
        self.assertTrue(result["report"]["findings"][0]["evidence_unverified"])
        self.assertIn("We reviewed", (self.store.folder(job["id"]) / result["output_name"]).read_text())

    def test_missing_verifier_claims_keep_rewrite_with_review_flag(self):
        class MissingClaimsModel(FakeModel):
            def complete(self, *args, **kwargs):
                result = super().complete(*args, **kwargs)
                if args[3] == "verify":
                    result["claims"] = []
                return result
        job = self.create()
        pipeline = Pipeline(self.store, job["id"], MissingClaimsModel())
        pipeline.analyze()
        result = pipeline.rewrite(repairs=0)
        self.assertEqual(result["status"], "needs_review")
        self.assertTrue(any(f["type"] == "verification_incomplete" for f in result["report"]["findings"]))
        self.assertFalse(any(f["type"] == "source_retained" for f in result["report"]["findings"]))
        self.assertIn("We reviewed", (self.store.folder(job["id"]) / result["output_name"]).read_text())

    def test_unchanged_prose_is_flagged_instead_of_claiming_style_success(self):
        job = self.create("A careful observation may be correct.")
        pipeline = Pipeline(self.store, job["id"], FakeModel())
        profile = pipeline.analyze()
        result = pipeline.rewrite(profile)
        self.assertEqual(result["status"], "needs_review")
        self.assertTrue(any(f["type"] == "style_unchanged" for f in result["report"]["findings"]))

    def test_unchanged_first_attempt_gets_targeted_rewrite_retry(self):
        class ChangesOnRetry(FakeModel):
            def __init__(self):
                super().__init__()
                self.rewrites = 0

            def complete(self, *args, **kwargs):
                if args[3] == "rewrite":
                    self.rewrites += 1
                    if self.rewrites == 1:
                        self.calls.append({"stage": "rewrite"})
                        return {"blocks": [{"id": item["id"], "text": item["text"]} for item in args[1]["source_blocks"]]}
                    self.retry_findings = args[1]["repair_findings"]
                return super().complete(*args, **kwargs)

        job = self.create()
        model = ChangesOnRetry()
        pipeline = Pipeline(self.store, job["id"], model)
        pipeline.analyze()
        result = pipeline.rewrite(repairs=1)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(model.rewrites, 2)
        self.assertEqual(model.retry_findings[0]["type"], "style_unchanged")
        self.assertIn("We reviewed", (self.store.folder(job["id"]) / result["output_name"]).read_text())

    def test_direct_baseline_skips_profile_generation(self):
        job = self.store.create("a.txt", b"We checked 24 cases.", "b.txt", REFERENCE.encode(), "recast-default", mode="direct")
        model = FakeModel()
        result = Pipeline(self.store, job["id"], model).rewrite()
        self.assertEqual(result["status"], "completed")
        self.assertEqual([call["stage"] for call in model.calls], ["rewrite", "verify"])
