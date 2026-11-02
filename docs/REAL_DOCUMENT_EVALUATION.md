# Real-document evaluation — 2026-10-25

These four cases use complete LaTeX section files from unpublished academic manuscripts, paired with full-text PDF papers as style references. The sources and PDFs were read only; outputs were written to Recast's ignored `.runtime/` directory, and job profiles, checkpoints, and review reports to ignored `data/jobs/`. The documents themselves are not included in this repository.

All runs used `--strength strong`, the profile-based pipeline, and one allowed repair. The reference PDFs have extractable text but carry a reading-order warning. They include front matter, tables, and references, which can influence extracted style. Source sections and references are both academic prose, so dramatic voice changes are not necessarily appropriate.

| Case | Source | Model | Prose blocks changed | Word tokens changed | Protected spans | Result |
| --- | --- | --- | ---: | ---: | ---: | --- |
| 1 | Framework section (A) | Gemma 4 26B A4B | 3/4 | 160 | 43 | Needs review; one section retained after numeric validation, two negation warnings |
| 2 | Framework section (A) | Qwen 3.5 9B | 4/4 | 142 | 43 | Needs review; two negation warnings |
| 3 | Background section (A) | Gemma 4 26B A4B | 5/9 | 191 | 43 | Needs review; one section retained after validation, three negation warnings |
| 4 | Comparative-analysis section (B) | Gemma 4 26B A4B | 10/10 | 364 | 39 | Needs review; two incomplete claim inventories, two negation and one qualification warning |

The word-token metric counts insertions, deletions, and replacements from a whitespace-token diff across the complete LaTeX file; it is a measure of visible editing, not style quality. `changed_prose_blocks` comes from Recast's syntax-stripped comparison. Protected span counts are from the parser, and block-level validation passed for exported rewrites. The local review findings remain important because automated checks do not establish semantic equivalence or grammatical quality.

Measurements can be recalculated for your own jobs with:

```powershell
python -X utf8 tools\summarize_real_case.py <job-id> [<job-id> ...]
```

## What the cases exposed

The first Gemma attempt on case 1 returned candidate rewrites but omitted verifier claim entries. Recast originally treated missing entries as a hard validation error and exported the unchanged source. The pipeline now keeps otherwise valid candidates with explicit `verification_incomplete` review findings; it still retains the source for marker damage, numeric validation failures, or substantiated material errors. Case 1 was rerun after this change. Its final output changes prose; the final verification covered its blocks.

The first Qwen run on case 2 changed only whitespace and was flagged `style_unchanged`. The pipeline now uses its permitted repair attempt to ask for a faithful wording or rhythm change when an entire section remains unchanged. The rerun changed all four editable blocks, so the final output is not whitespace-only.

Manual spot checks found prose that needs editing despite passing deterministic syntax checks. In case 1, an enumerated item became a clumsy noun phrase. In case 3, some citations were followed by colon-led fragments, and `historically` was added to a claim without explicit support in the source. These are reasons to treat every output as a draft for human review. No output was written back to the original source files.
