# Recast

The content of one document, expressed in the writing style of another.

Recast takes **A (content)** and **B (style reference)** and produces one rewritten document. Writing style includes tone, vocabulary, sentence rhythm, transitions, and rhetorical habits. Facts, qualifications, attribution, quotations, and citations belong to A and must survive the rewrite.

## Project documents

- [Initial research](docs/RESEARCH.md): alternatives, evidence, model choices, and evaluation.
- [Implementation plan](docs/IMPLEMENTATION_PLAN.md): architecture, milestones, contracts, and acceptance criteria.
- [Local runtime](docs/LOCAL_RUNTIME.md): machine inventory and limited llama.cpp router setup.
- [Real-document evaluation](docs/REAL_DOCUMENT_EVALUATION.md): four complete LaTeX sections with reading-list PDF references, outputs, measurements, and review findings.

## Current status

A working local browser app and CLI are implemented, with reference extraction, an editable style profile, protected rewriting, model-assisted verification, bounded repair, and resumable sections. The router includes Gemma 4 26B A4B and Sage-A1.5 alongside three Qwen baselines. See [application validation](docs/IMPLEMENTATION_STATUS.md) and [router validation](docs/RUNTIME_VALIDATION.md).

## Run the app

Requires Python 3.10+ (Windows, Linux, or macOS), a llama.cpp `llama-server` binary, and the model files named in `config/models.ini`. Set `RECAST_LLAMA_SERVER` to the `llama-server` path (or pass `--server-path` to the launcher); the launcher otherwise look for `llama-server` on PATH. Put your GGUF files (or symlinks) under `models/` using the file names in `config/models.ini`. No additional Python packages, Node installation, or frontend build are required to run from this checkout. PDF references use the installed `pdftotext`; set `RECAST_PDFTOTEXT` if it is not on PATH. An installed `pypdf` is an optional fallback.

```sh
cd <checkout-directory>
python scripts/launch.py start
```

The GPU settings in `config/models.ini` (`n-gpu-layers`, `tensor-split`, `split-mode`) are tuned for a dual-16-GB-GPU machine; adjust them for your hardware (for example, remove `tensor-split` on a single GPU). The launcher is tested on Windows and Linux; macOS uses the same code paths but is untested.

Open **http://127.0.0.1:8020**. The launcher starts the router if necessary, then runs the app. Ctrl+C stops the app and a router it started itself. An already running router is reused and left running.

From a separate terminal, `python scripts/launch.py stop` also stops the managed app. It checks the recorded process identity before stopping its process tree. Completed sections remain saved for resuming interrupted jobs.

1. Paste or upload content A as `.txt`, `.md` / `.markdown`, or `.tex` / `.latex`.
2. Upload reference B as `.docx` or a PDF with extractable text. TXT/Markdown references also work.
3. Choose a local model and style strength, then analyze the style.
4. Review the extracted text and edit the style summary, rules, or patterns to avoid.
5. Rewrite, compare, and download the result in A's format. Amber highlights in Original show replaced or removed wording; blue highlights in Recast show new wording. Use **Hide highlights** for a clean reading view. Download the profile or review JSON separately if useful.

Source/model settings are locked after analysis to avoid applying a profile to a silently changed document. Use **Start a new document** to change those inputs. **Delete this local job** removes that job's stored uploads, extracted text, profile, attempts, and outputs. Starting a new document keeps prior jobs on disk; save their IDs or remove them before starting another if you do not want to retain them.

## CLI

```powershell
python -X utf8 -m recast inspect .\voice.pdf
python -X utf8 -m recast rewrite --source .\article.tex --reference .\voice.docx --output .\article-recast.tex --model recast-gemma
python -X utf8 -m recast rewrite --source .\draft.md --reference .\voice.pdf --output .\draft-recast.md
python -X utf8 -m recast rewrite --resume JOB_ID --output .\resumed-output.md
```

Start the router first when using the CLI independently. Existing output files are not overwritten. Exit codes: `0` completed without findings, `2` output produced and needs review, `1` failed or cancelled. Optional flags: `--profile edited-style.json`, `--repairs 0|1|2`, `--strength subtle|balanced|strong`, `--mode direct|profile`, `--base-url`, and `--data-dir`.

Jobs are saved under `data/jobs/JOB_ID/`, excluded from version control. A process lock prevents the CLI and browser worker from generating simultaneously. Interrupted jobs retain completed sections; changing the profile, model weights, prompts, engine code, strength, or mode invalidates incompatible checkpoints.

## Format support and current limits

| Role | Supported formats | Handling |
| --- | --- | --- |
| Content A | UTF-8 text | Rewrite prose; protect numbers, URLs, direct quotations, and numeric citations. |
| Content A | Markdown | Preserve headings, fences, code, tables, block quotes, links, citation markers, inline emphasis, and math. |
| Content A | LaTeX | Rewrite ordinary prose; preserve the preamble, commands with arguments, equations, comments, citations, verbatim/unknown environments, and syntax boundaries. |
| Style B | DOCX | Read body paragraphs, hyperlinks, and table-cell text in order; ignore tracked deletions. |
| Style B | PDF | Extract the text layer; show reading-order and missing-text notes. Image-only PDFs require OCR, which is not included. |

LaTeX support is deliberately conservative: macro arguments and opaque environments remain unchanged. External `\\input`/`\\include` files are not expanded, and no source is compiled or executed. Markdown/LaTeX parsers cover common constructs rather than every dialect or macro package. DOCX/PDF references supply writing style, not output layout.

Protected-span damage or a substantiated material semantic issue retains the original section after bounded repair. Unanchored verifier concerns are marked as uncertain review findings with exact block context. A valid model response is not proof of factual preservation; review the output. Style-transfer quality across models and languages has not yet been benchmarked.

## Checks

```powershell
python -m unittest discover -v
node --check recast/static/app.js
```

Node is only needed for the optional JavaScript syntax check. Automated tests cover extraction, OCR-required rejection, syntax retention, numeric/qualification drift, fallback, checkpoint invalidation, process locking, uploads, and local request boundaries.

## Start the local router

```sh
cd <checkout-directory>
python scripts/launch.py router --validate-only
python scripts/launch.py router
```

The router binds to `127.0.0.1:8017`, advertises five explicitly configured models, and permits one loaded model at a time. Startup does not load model weights. See the runtime document for requests and resource limits.

## Initial product decisions

- Writing style only; preserve A's organization by default.
- Local inference through llama.cpp; use the installed model files first.
- Start with a style profile plus selected examples; retain direct prompting as a baseline.
- Preserve original source passages throughout generation and verification.
- Treat automated preservation checks as evidence for review, not a guarantee.
- Collect accepted rewrites before considering fine-tuning.
