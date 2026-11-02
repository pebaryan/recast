# Runtime validation

Date: 2026-10-25. Scope: runtime compatibility and basic structured generation, not an application quality benchmark.

## Environment and configuration

The initial record below used the original three-model preset. The Gemma/Sage addendum records validation of the expanded five-model preset.

Executable: `models/llama-b10837-bin-win-cuda-13.3-x64\llama-server.exe`.

Reported version: `0.4.0-dev`, build `10837`, commit `5202104b5`.

Executable SHA-256: `4ECE83D6180FDACDEE2D6A67E5AD49B71241AE3E56C9CDF3804C9FBD944B0623`.

Two RTX 5060 Ti GPUs, each with 16,311 MiB VRAM. Used [models.ini](../config/models.ini) and [start-router.ps1](../scripts/start-router.ps1), loopback port 8017, isolated cache, offline operation, 16,384-token contexts, one inference slot, and one resident model maximum.

## Checks performed

| Check | Observed result |
| --- | --- |
| Launcher PowerShell syntax and `-ValidateOnly` | Passed; all three local model paths exist. |
| Router `/health` | HTTP success with `status: ok`. |
| Initial `/models` | Exactly `recast-fast`, `recast-default`, and `recast-quality`; all unloaded and sourced from the preset. |
| Actual inference | All three models loaded and completed a small rewrite request. |
| JSON-schema output | All three returned a parseable object containing a nonempty `text` field, with finish reason `stop`. |
| Sequential model switching | After each generation, only the requested model was loaded. |
| Unknown model | `recast-unknown` rejected with HTTP 400. |
| Unloading | Final polling showed all three entries unloaded. |
| GPU memory after unloading | GPU 0 used 1,356 MiB; GPU 1 used 0 MiB. Values include other system activity and are not process-specific peak measurements. |
| Project documentation | Local Markdown links and fenced code blocks checked. |

The temporary validation router is stopped after testing. No system service or startup task was installed.

## Generation observations

Prompt: “Rewrite this sentence more formally, preserving its meaning. Return JSON with a text field: We checked the figures and they look right.”

Settings: temperature 0.2, maximum 128 completion tokens, JSON schema requiring `text: string` with no additional properties. The preset disabled reasoning.

| ID | Request elapsed time | Prompt/completion tokens | Returned text |
| --- | --- | --- | --- |
| `recast-fast` | 4.27 s | 39 / 23 | We have verified the figures, and they appear to be accurate. |
| `recast-default` | 6.79 s | 39 / 23 | We have reviewed the figures, and they appear to be accurate. |
| `recast-quality` | 31.19 s | 39 / 20 | We have verified the figures and confirmed their accuracy. |

Each request included loading a previously unloaded model. These are single request wall times, not warm tokens-per-second benchmarks or repeatable latency estimates. Loading, generation, and switching costs were not separated.

The 27B output strengthens “look right” into “confirmed their accuracy.” This is a content-preservation concern despite its valid schema and successful generation. The other outputs also warrant normal semantic review. A larger model and the alias `recast-quality` do not establish better fidelity; that alias identifies a candidate tier only.

Turn this example into a qualification-preservation fixture when implementing the evaluation harness. Successful runtime checks do not establish document-level style imitation, robust instruction following, or semantic preservation.

## Reproduction and remaining work

Follow [LOCAL_RUNTIME.md](LOCAL_RUNTIME.md). Run its schema-constrained request with each model ID, check `/models` after every switch, and poll until unloading finishes. Raw transient request results are saved under ignored `.runtime/smoke-results.json` on this machine; this document is the durable record.

Still untested: realistic long-document workloads, context overflow handling, cancellation, repeated warm latency, peak VRAM, multilingual transfer, claim verification, and reference-content leakage. These belong to the implementation/evaluation milestones.

## Addendum: Gemma 4 26B A4B and Sage-A1.5

Date: 2026-10-25. The allowlist now contains five models while the maximum loaded count remains one. Both additions use the same 16,384-token context, single slot, layer split, and disabled reasoning as the original run. Their vision projectors were not loaded.

| ID | File | Exact bytes | Local metadata |
| --- | --- | --- | --- |
| `recast-gemma` | `models/gemma-4-26B-A4B-it-UD-Q6_K_XL.gguf` | 23,295,391,456 | `gemma4`, `Gemma-4-26B-A4B-It`, 128 experts, 8 selected. |
| `recast-sage` | `models/Sage-A1.5-Q4_K_M.gguf` | 21,713,463,520 | `qwen35moe`, `Agents A1.5`, 256 experts, 8 selected. |

Launcher validation and PowerShell syntax checks passed. Initial `/models` listed exactly the five configured IDs, all unloaded. Both new models loaded and returned a nonempty `text` field under the same JSON schema and formal-rewrite prompt used above. After each generation, only the requested model was loaded.

| ID | Request elapsed time, including cold loading | Prompt/completion tokens | Returned text |
| --- | --- | --- | --- |
| `recast-gemma` | 33.36 s | 39 / 28 | We have verified the figures, and they appear to be accurate. |
| `recast-sage` | 33.35 s | 39 / 21 | We verified the figures and they appear to be correct. |

Both completions finished with `stop`. This establishes compatibility with the current binary and basic schema-constrained generation. One sentence does not establish comparative writing quality or semantic reliability. Preserve these as observations, not a model ranking.

Sage is now explicitly recorded as a Qwen-family 35B/A3B-class fine-tuned MoE. The user's Qwen3.5 description and the current publisher card's Qwen3.6/Agents-A1 description are distinguished in [research](RESEARCH.md); the local architecture string alone cannot settle that ancestry. No association with an unrelated same-name model is assumed.

Raw transient results: `.runtime/additions-smoke-results.json`. All models were unloaded after the check, and the temporary router was stopped. Use the existing launcher to start development. No model files were moved and no downloads were performed.
