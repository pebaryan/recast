# Local llama.cpp runtime

Inventory date: 2026-10-25. All model sizes below are file sizes, not measured total VRAM requirements.

## Existing machine

- GPU 0: NVIDIA GeForce RTX 5060 Ti, 16,311 MiB total VRAM.
- GPU 1: NVIDIA GeForce RTX 5060 Ti, 16,311 MiB total VRAM.
- Selected executable: set via `RECAST_LLAMA_SERVER` (or `llama-server` on PATH).
- Reported version: `0.4.0-dev`, build `10837`, commit `5202104b5`, Clang 20.1.8, Windows x86_64.

The selected binary's help confirms router presets, maximum loaded models, offline mode, Jinja templates, and reasoning controls. All five configured models passed real load and structured-output smoke checks; see the [validation record](RUNTIME_VALIDATION.md) for the original run and Gemma/Sage addendum. A local file name is not a provenance check.

## Limited router

| ID | Local model | Size |
| --- | --- | --- |
| `recast-fast` | `models/Qwen3.5-4B-IQ4_NL.gguf` | 2.40 GiB |
| `recast-default` | `models/Qwen3.5-9B-IQ4_NL.gguf` | 5.00 GiB |
| `recast-quality` | `models/Qwen3.8-27B-IQ4_NL.gguf` | 15.22 GiB |
| `recast-gemma` | `models/gemma-4-26B-A4B-it-UD-Q6_K_XL.gguf` | 21.70 GiB |
| `recast-sage` | `models/Sage-A1.5-Q4_K_M.gguf` | 20.22 GiB |

The [preset](../config/models.ini) supplies exactly five named entries. The [launcher](../scripts/launch.py) accepts at most five explicit model paths and uses an isolated project cache and no `--models-dir`, preventing discovery of unrelated shared models. Keep the project cache free of downloaded models: presets are not themselves a filter over llama.cpp's default cache discovery.

Gemma's local metadata identifies the 26B A4B instruction-tuned `gemma4` MoE. Sage's local architecture is `qwen35moe`; it is the Qwen-family 35B/A3B-class fine-tuned candidate described in [research](RESEARCH.md), not a small generic model. Leave both vision projectors unloaded for this text-only application. Both larger files require distributed VRAM on this dual-16-GB machine at these settings; the active-expert parameter count is not their weight-memory requirement.

Defaults: loopback port 8017, offline mode, one resident model, one slot, 16,384-token context, 512 logical batch, 128 microbatch, layer splitting across both GPUs, and reasoning disabled. Models load on demand, not at router startup. Changing model IDs can incur unload/load latency.

These settings limit normal application resource use. They are not a sandbox or a hard VRAM quota. The application must enforce its own request budgets and permit only the chosen IDs. The router's management endpoints are trusted local development interfaces.

## Startup

The launcher is a standard-library Python script that runs on Windows, Linux, and macOS:

```sh
cd <path-to-recast>
python scripts/launch.py router --validate-only
python scripts/launch.py router
```

`--validate-only` checks the executable/preset/model paths without loading weights. It does not prove model compatibility or inference quality. Override the executable with `--server-path` or choose a different port with `--router-port` if needed.

The launcher runs in the foreground. Stop it with Ctrl+C. It configures the child process environment without changing global settings or the calling shell's environment. When replacing the executable, repeat all runtime checks.

## Checks and requests

In another PowerShell terminal:

```powershell
Invoke-RestMethod http://127.0.0.1:8017/health
Invoke-RestMethod http://127.0.0.1:8017/models | ConvertTo-Json -Depth 8
```

Before inference, verify that exactly `recast-fast`, `recast-default`, `recast-quality`, `recast-gemma`, and `recast-sage` are listed and all are unloaded. Do not rely solely on `/health`: router readiness does not mean any model has loaded successfully.

Small real generation request (loads the selected model):

```powershell
$recastRequest = @{
    model = 'recast-fast'
    messages = @(@{ role = 'user'; content = 'Rewrite more formally, preserving meaning. Return JSON with a text field: We checked the figures and they look right.' })
    response_format = @{
        type = 'json_schema'
        json_schema = @{
            name = 'recast_smoke'
            strict = $true
            schema = @{
                type = 'object'
                properties = @{ text = @{ type = 'string' } }
                required = @('text')
                additionalProperties = $false
            }
        }
    }
    max_tokens = 128
    temperature = 0.2
    stream = $false
} | ConvertTo-Json -Depth 12
Invoke-RestMethod -Uri http://127.0.0.1:8017/v1/chat/completions `
    -Method Post -ContentType 'application/json' -Body $recastRequest -TimeoutSec 600
```

Unload explicitly after a check when the model is no longer needed:

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8017/models/unload `
    -Method Post -ContentType 'application/json' -Body '{"model":"recast-fast"}'
```

Unloading is asynchronous: poll `/models` until the selected entry becomes `unloaded`. Unloading an already stopped model returns HTTP 400 on this build; select the currently loaded entry rather than unconditionally unloading every configured ID.

The [validation record](RUNTIME_VALIDATION.md) distinguishes the original three-model run from the Gemma/Sage addition. Remaining application integration checks include context overflow, cancellation, durable job recovery, and peak memory measurement. Record cold loading separately from generation speed.

## Validation record

Executable version/help, GGUF paths/sizes, GPU inventory, and real router smoke checks have been verified. See [RUNTIME_VALIDATION.md](RUNTIME_VALIDATION.md) for outputs, timings, and the observed change in certainty. The temporary validation server is stopped after testing; use the launcher to start it for development. No document-level style-transfer quality evaluation is implied by a successful smoke test.

## Sources

Configuration semantics were checked against the installed help and the available local llama.cpp server source/documentation. Upstream references: [server documentation](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md), [INI presets](https://github.com/ggml-org/llama.cpp/blob/master/docs/preset.md).
