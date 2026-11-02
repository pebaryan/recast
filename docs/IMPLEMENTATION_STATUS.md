# Recast 0.1 implementation

Date: 2026-10-25.

## Working application

Run `python scripts/launch.py start` from the project folder, then open `http://127.0.0.1:8020`. A CLI is available through `python -m recast`. Python 3.10+ runs the checkout without third-party Python packages or a frontend build. PDF text extraction uses the installed Poppler `pdftotext`, with optional `pypdf` fallback.

Implemented:

- Content A: UTF-8 text, Markdown, and LaTeX; output in the same format.
- Reference B: DOCX body/table text and text-layer PDFs, plus TXT/Markdown references.
- Extracted-reference preview, sample selection across the reference, an editable style profile, model choice, and style strength.
- Source-linked rewrite blocks, exact retention of protected spans, numeric and qualification checks, model-assisted claim/meaning review, bounded repair, and original-section retention for unresolved material errors.
- Explicit review findings for unanchored verifier evidence and unchanged prose; no claim of successful style application for a no-op rewrite.
- Local browser workflow, source/output comparison with changed-word highlights and a clean-view toggle, document download, separate profile/report downloads, cancellation, and current-job deletion.
- Atomic job/checkpoint storage, interrupted-job recovery, checkpoint invalidation on changed inputs/settings/engine, a single worker with a bounded queue, and a process lock shared by CLI and browser generation.
- Native llama.cpp template application/token counting before inference, prompt/output budgets, truncated-response rejection, local-only endpoints, and the five-model allowlist.
- Direct-reference CLI baseline that skips profile-generation inference for a fairer comparison.

The app uses Python's standard library server and durable JSON/filesystem artifacts rather than the previously proposed FastAPI/SQLite/React stack. This is an implementation choice for the first local release; it does not require a hosted service or dependency installation.

## Automated checks

`python -m unittest discover -v` covers document extraction, role/encoding validation, scanned-PDF rejection, LaTeX/Markdown round trips, macro and word boundaries, inline emphasis, protected-span loss/reordering/duplication, introduced syntax/numbers, negation and uncertainty heuristics, failed/truncated rewrite retention, verifier evidence, no-op detection, direct baseline behavior, resume and invalidation, process locking, actual HTTP uploads/previews, request-origin boundaries, downloads before completion, and file-path confinement.

JavaScript syntax and PowerShell launcher syntax are checked separately. Exact final run results are recorded below after validation.

Final automated run: **32 tests passed**. JavaScript syntax, Python bytecode compilation, PowerShell script parsing, and local documentation links passed their checks. The app launcher was cold-started successfully: it started its own router, verified all five model IDs, and served the application on loopback.

The live browser file chooser uploaded a `.tex` source and a `.pdf` reference. Analysis completed, edited style rules were saved, and rewriting/export completed with a `style_unchanged` review finding on the 9B model. The downloaded `.tex` attachment matched the stored output byte for byte and retained all six protected spans in that fixture. Browser console errors were zero; desktop and 390 px layouts had no horizontal overflow.

A separate **Gemma 4 26B A4B** run used a DOCX reference about a garden and a LaTeX pilot-study source. It changed prose in two blocks while retaining source syntax, numbers, citation, and equation. Its negation-count heuristic raised a review warning rather than marking the result as unconditionally faithful. This demonstrates operation and visible style changes, not a comparative quality result.

The final Markdown/PDF rerun retained all 13 protected spans, including its complete `**careful**` emphasis, fenced Python block, table, block quote, link, inline math, numbers, and citation. It produced a reviewable result rather than silently changing Markdown delimiters. These fixture runs also show that the 9B model can choose essentially unchanged wording; the no-op finding remains visible to the user.

## Live checks and corrections

Tests use synthetic fixture documents, never private user files. Initial end-to-end runs used `recast-default` (Qwen3.5 9B) through the installed llama.cpp router.

The first DOCX-reference/LaTeX-source run exposed repetitive verifier output that hit the completion cap. The pipeline retained the original section and reported the failure. Verification was changed to receive prose/fact views while syntax retention is checked deterministically; claim strings and arrays are bounded.

The first PDF-reference/Markdown-source run exposed whitespace inserted inside bold delimiters. Complete inline emphasis is now protected. LaTeX protection similarly retains whitespace at macro boundaries, so removing a space cannot turn `\\item Text` into `\\itemText`.

The browser workflow was exercised on a 390 px viewport, including profile analysis, rewrite, comparison, and download links. Desktop/mobile screenshots were inspected; no horizontal overflow was found. A missing favicon request was removed. Source and model controls lock after analysis to prevent rewriting an old job after silently editing its displayed inputs.

Verifier quotations are checked against actual source/output text. A quotation that cannot be anchored becomes an explicit uncertain review finding with exact block context; it cannot substantiate a confirmed material error. Original text is retained after failed syntax checks, truncated output, or unresolved substantiated material changes. Generated text identical in prose to the source is flagged for review.

## Limits and next work

- These are operation/preservation checks, not a quality ranking across Gemma, Sage, and Qwen. A human-labeled corpus and blind style/content evaluation remain to be completed.
- OCR for scanned PDFs is not implemented. Mixed PDFs identify pages with no text. Reading order in complex PDFs needs review.
- LaTeX handling is lexical and conservative: macro arguments and unknown/verbatim/math environments remain untouched. External files are not expanded, and no LaTeX is compiled. Markdown covers common syntax rather than all extensions.
- DOCX extraction uses body text and table paragraphs; headers, footers, images, notes, and layout are excluded from style evidence.
- No DOCX/PDF output or layout transfer is implemented; output preserves A's text-based format.
- Progress uses polling. Persistent storage is local JSON/files, not a multi-user database. Starting a new document keeps older jobs on disk; current-job deletion removes its entire artifact directory.
- Full long-document, multilingual, warm-latency, and peak-memory benchmarks remain on the research roadmap. Automated semantic review can miss errors or raise false positives.
