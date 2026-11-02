# Initial research: reference-based text style transfer

Date: 2026-10-25. Status: initial engineering assessment; no Recast quality benchmark has been run.

## Definition and scope

Working name: **Recast**, conveying a change in expression while retaining content. Naming, domain, and trademark availability have not been investigated.

Inputs are document A, which supplies content, and document B, which supplies writing style. The output should preserve A's propositions while adopting B's tone, vocabulary, sentence construction, rhythm, paragraph-level rhetorical patterns, and transitions. Fonts, page layout, and visual imitation are outside the requested scope.

Content preservation includes entities, quantities, units, dates, quotations, citations, relationships, negation, uncertainty, conditions, and who said what. A's conclusions and opinions remain content even when B expresses different views. A confident reference must not turn “may improve” into “will improve.” Summarization and translation are separate operations and are not implied by style transfer.

The initial default is to preserve A's language and section order. Cross-language style transfer needs a separate evaluation. A very brief B or a B containing several voices provides weak evidence of a single target style.

## Alternatives

The assessments below are proposed engineering tradeoffs, not measured rankings.

| Approach | Mechanism | Strengths | Weaknesses | Decision |
| --- | --- | --- | --- | --- |
| Direct prompting | Put A and B in one prompt and request a rewrite. | Lowest implementation effort; works with arbitrary uploaded references. | Topic leakage, copying, omissions, and context pressure; limited visibility into what style was inferred. | Implement as a benchmark baseline. |
| Explicit profile | Extract a structured style description from B, then rewrite A using it. | Inspectable, editable, cacheable, and relatively compact. | Can flatten subtle or context-dependent stylistic traits. | Core MVP component. |
| Profile plus examples | Add a few passages from B illustrating relevant stylistic behavior. | Combines explicit control with concrete demonstrations; supports longer B. | Example selection matters; reference facts must remain excluded from output. | Recommended initial product architecture. |
| Specialized transfer model | Encode B with a learned style/authorship encoder and condition a smaller generator. | Potentially lower inference cost and explicit style interpolation. | Requires separate integration and evaluation, especially for long documents and domain shifts. | Benchmark after the general LLM baseline. |
| LoRA/SFT | Train on reference-conditioned rewrite examples or a repeated house style. | May improve consistency and domain behavior with good data. | A single B provides no trustworthy paired rewriting dataset; one adapter per upload is operationally awkward. | Defer until accepted examples justify training. |
| Rules and substitutions | Apply controlled lexical and syntactic edits. | Predictable and cheap for narrow changes. | Cannot reproduce arbitrary voice or rhetorical structure well. | Supporting checks or constrained modes only. |

Prompted arbitrary style transfer has research precedent without task-specific fine-tuning. Its sentence-level findings do not establish full-document fidelity. [A Recipe for Arbitrary Text Style Transfer with Large Language Models](https://arxiv.org/abs/2109.03910)

For learned style representations, TextSETTR adapts T5 to extract style vectors and perform targeted restyling from a small number of examples. This is a distinct alternative to writing a prose style profile. [TextSETTR](https://arxiv.org/abs/2010.03802)

TinyStyler combines an 800M-parameter generator with authorship representations and releases inference code and checkpoints. Its stated short-text focus makes it a useful efficiency candidate, with document-wide coherence still needing evaluation. [TinyStyler implementation](https://github.com/zacharyhorvitz/TinyStyler)

HyperStyler separates context-aware style selection from realization through dynamic parameter modulation. It reports results on Reddit, Blog, and News datasets; those results should motivate an experiment rather than be treated as proof for Recast's document workload. [HyperStyler](https://arxiv.org/abs/2609.02772)

## Recommended pipeline

1. Parse A and B into ordered blocks with stable identifiers and source spans. Keep the originals.
2. Analyze representative sections throughout B. Extract a structured profile and retain a small, diverse set of illustrative passages.
3. Record A's protected spans and checkable claims, with links to their original passages. The inventory supplements the original text; it never replaces it.
4. Rewrite coherent sections of A using the profile, examples, original section, and necessary neighboring context.
5. Check missing claims, unsupported additions, contradictions, and altered protected details. Check source-to-output and output-to-source coverage separately.
6. Repair only flagged sections, then repeat checks with a bounded retry count. Preserve unresolved findings for review.
7. Assemble one document and provide an optional review view linking source blocks to rewritten passages.

Illustrative profile dimensions: register, formality, lexical complexity, sentence-length distribution, syntactic patterns, punctuation, paragraph cadence, transitions, direct address, humor, and figurative language. Record observed evidence and confidence. Separate measured features from model interpretations. Do not classify subject-specific nouns as stylistic rules.

Use context-dependent examples: an explanatory paragraph may need a different reference passage than an introduction. Initial selection can use paragraph roles and diversity; ordinary topic embeddings alone can over-select B's subject matter. A vector database is unnecessary for the first version.

## Local model strategy

The earlier general shortlist included Qwen3.5-9B, Gemma 4 26B A4B, and Qwen3.6-35B-A3B. Their publisher cards document the model families, not a task-specific style-transfer ranking. [Qwen3.5-9B](https://huggingface.co/Qwen/Qwen3.5-9B), [Gemma 4 26B A4B](https://huggingface.co/google/gemma-4-26B-A4B-it), [Qwen3.6-35B-A3B](https://huggingface.co/Qwen/Qwen3.6-35B-A3B)

Read-only machine inspection found better starting points already available locally:

| Router ID | Existing GGUF | File size | Initial purpose |
| --- | --- | --- | --- |
| `recast-fast` | `models/Qwen3.5-4B-IQ4_NL.gguf` | 2.40 GiB | Fast baseline and development smoke checks. |
| `recast-default` | `models/Qwen3.5-9B-IQ4_NL.gguf` | 5.00 GiB | First end-to-end candidate. |
| `recast-quality` | `models/Qwen3.8-27B-IQ4_NL.gguf` | 15.22 GiB | Larger comparison candidate; runtime compatibility passed, quality remains to be measured. |
| `recast-gemma` | `models/gemma-4-26B-A4B-it-UD-Q6_K_XL.gguf` | 21.70 GiB | Gemma 4 26B A4B instruction-tuned MoE; independent model-family comparison. |
| `recast-sage` | `models/Sage-A1.5-Q4_K_M.gguf` | 20.22 GiB | Sage-A1.5, a Qwen-family 35B/A3B-class MoE derivative; explicit fine-tuned comparison. |

The names above identify local files; their size and presence do not establish model quality. Keep fine-tuned variants explicitly identified and evaluate them alongside stock baselines. No additional model download is necessary to begin.

The user identified Sage-A1.5 as a Qwen3.5-35B-A3B fine-tune. Its local GGUF confirms the `qwen35moe` architecture, 256 experts, 8 selected experts, and the embedded name `Agents A1.5`. The current publisher card describes Sage-A1.5 as based on Qwen3.6 and Agents-A1; architecture naming alone does not resolve exact checkpoint ancestry. This is the InternScience model, not an unrelated same-name derivative. [Sage-A1.5 publisher card](https://huggingface.co/InternScience/Sage-A1.5), [official GGUF repository](https://huggingface.co/InternScience/Sage-A1.5-Q4_K_M-GGUF)

The downloaded Gemma metadata identifies `gemma4`, `Gemma-4-26B-A4B-It`, 30 blocks, 128 experts, and 8 selected experts. Its higher-bit quantization and Sage's different training make comparisons useful, but prevent attributing every difference solely to model architecture. Neither file requires its vision projector for Recast's text-only workload. MoE active parameter counts affect per-token compute; memory planning still uses the full model weights plus runtime buffers.

The three original Qwen candidates passed load/inference and structured-output smoke checks. The 27B candidate increased certainty in its rewrite, illustrating why compatibility cannot stand in for semantic evaluation. New-model checks are recorded separately in the [runtime validation record](RUNTIME_VALIDATION.md).

The machine has two NVIDIA GeForce RTX 5060 Ti GPUs, each reporting 16,311 MiB total memory. Treat them as separate devices, not a single contiguous 32 GB allocation. Start with layer splitting, one resident model, one inference slot, and a 16,384-token context. Model weights are only part of memory consumption; cache and compute buffers require headroom.

Use the same selected model for all pipeline stages initially, avoiding a model swap for every section. Compare stage-specific routing later only if quality or latency measurements justify the loading overhead.

## Runtime choice

llama.cpp is already installed and supports the required local chat and structured-output workflow. The supplied router configuration uses explicit local model paths, an isolated cache, loopback binding, offline operation, and a maximum of one loaded model. It does not scan the full shared model directory. See [local setup](LOCAL_RUNTIME.md).

The server and preset documentation explain router model selection, INI presets, and model lifecycle endpoints. The installed binary's own help is the compatibility authority for this machine. [llama.cpp server](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md), [INI presets](https://github.com/ggml-org/llama.cpp/blob/master/docs/preset.md)

Ollama remains an alternative wrapper if runtime management changes, but introducing a second runtime is unnecessary now. Its JSON-schema support would serve the same profile/report use cases. [Ollama API](https://github.com/ollama/ollama/blob/main/docs/api.md)

## Evaluation and limits

Evaluate four independent properties: meaning preservation, resemblance to B's style, fluency/coherence, and performance. Measure latency both with a cold model and after loading. Include peak memory, schema failures, truncation, and repair count.

Use human-authored fixtures with names, measurements, dates, citations, negation, conditional claims, attribution, and deliberate uncertainty. Include A/B pairs with unrelated topics, very short references, mixed voices, long documents, and tables or quoted passages that should remain unchanged.

Compare direct prompting, profile only, and profile plus examples on the same fixtures and generation budgets. Test each language intended for launch separately. A small blind human review should assess style and content independently.

Do not collapse these into a single cosine-similarity or BLEU score. Style changes can affect similarity without changing content, and fluent outputs can still lose qualifications. [Mind the Style Gap](https://arxiv.org/abs/2502.15022)

Model-assisted verification may share the generator's blind spots. Treat findings as review support and make no claim of guaranteed preservation. A second verifier model is an experiment to measure, not a substitute for deterministic checks and human evaluation.

## Decision

Build a reference-conditioned rewriting pipeline with an editable profile, selected examples, source-linked verification, and bounded repair. Use the installed 9B model for initial wiring, then prioritize Gemma 4 26B A4B versus Sage-A1.5 in the quality evaluation. Retain the 4B and 27B Qwen baselines. Sage is an existing fine-tune that can be used directly; training a Recast-specific adapter remains deferred until accepted examples justify it. Defer specialized model integration and additional downloads until benchmarks reveal a concrete need.
