# Changelog

## 0.1.0

First release.

- `map` — one instruction across many items: batched, concurrent, resumable,
  writing results to a file rather than to the caller's context.
- `ask` — a single cheap completion.
- `models` — search the gateway's catalog by live price, context and capability.
- `ledger` / `balance` — what a run actually cost, measured where the gateway
  reports real cost rather than modelled from a stale price table.
- `jobs` / `status` / `result` / `cancel` — background runs that outlive the
  shell that started them.
- Claude Code skill and subagent in `claude/`, installable with
  `./claude/install.sh`.
- Gateways: NanoGPT (default), OpenRouter, Groq, Cerebras, DeepSeek, Gemini,
  Together, Ollama, and any other OpenAI-compatible endpoint via `--base-url`.

Three guards, each added after a real failure rather than in anticipation of one:

- batches are renumbered `0..n-1` for the model and mapped back, because small
  models mis-echo absolute offsets often enough to matter. Fixing this took a
  10-item run from 16 calls to 4.
- `--require` rejects results missing the fields the task asked for. A 0.8B
  model returned well-formed JSON echoing its input instead of classifying;
  without this, that is a thousand silently wrong rows.
- a run that fails its first three batches with nothing usable stops and says
  the model cannot follow the output contract.

Measured on 1,000 short job listings: `qwen3.7-flash` classified all of them in
28 calls for $0.0062 in 49 seconds. `qwen3.5-0.8b` classified none and cost
twice as much in retries.
