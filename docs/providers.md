# Gateways

Every gateway `sidequest` speaks to uses the OpenAI chat-completions wire format, which is why one adapter covers all of them.

## Built in

| Name | Key | Default model |
|---|---|---|
| `nanogpt` | `NANOGPT_API_KEY` | `gemini-2.5-flash-lite` |
| `openrouter` | `OPENROUTER_API_KEY` | `qwen/qwen3.5-0.8b` |
| `groq` | `GROQ_API_KEY` | `moonshotai/kimi-k2-instruct` |
| `cerebras` | `CEREBRAS_API_KEY` | `llama-3.3-70b` |
| `deepseek` | `DEEPSEEK_API_KEY` | `deepseek-v4-flash` |
| `gemini` | `GEMINI_API_KEY` | `gemini-2.5-flash-lite` |
| `together` | `TOGETHER_API_KEY` | `Qwen/Qwen3-235B-A22B-Instruct-2507-tput` |
| `ollama` | none | `qwen3.5:latest` |

```bash
sidequest ask -g groq "..."
export SIDEQUEST_GATEWAY=openrouter    # or pin it for a project
export SIDEQUEST_MODEL=qwen/qwen3.5-0.8b
```

## Anything else

Any OpenAI-compatible endpoint works without a code change:

```bash
sidequest ask --base-url https://your-endpoint/v1 -m your-model "..."
```

The key comes from `SIDEQUEST_API_KEY` in that case.

## Adding one properly

Add an entry to `GATEWAYS` in `sidequest/gateways.py`. The only required fields are `base_url`, `key_env` and `default_model`. Set `reports_cost: True` only if the gateway returns real per-call cost — and then teach `client.cost()` where to find it, because there is no standard for this.

## Local models

`ollama` costs nothing and nothing leaves your machine, which makes it the right choice for sensitive data. It is much slower for bulk work: one stream on consumer hardware runs at a fraction of a hosted gateway's throughput, and `--workers` cannot fix that because the bottleneck is the GPU rather than the network.

```bash
ollama serve
sidequest map -g ollama -m qwen3.5:latest --items in.jsonl --out out.jsonl -I "..."
```
