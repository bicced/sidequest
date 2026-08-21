# NanoGPT specifics

[NanoGPT](https://nano-gpt.com) is the default gateway because it is the only one that offers all three of these at once:

- **~600 models on one key**, from many labs, billed at each lab's list price
- **pay per prompt** — no subscription, no monthly minimum
- **the exact cost of every call**, attached to the response

That third one is unusual and it is why `sidequest ledger` reports measurements rather than estimates.

> Not to be confused with [karpathy/nanoGPT](https://github.com/karpathy/nanoGPT), a well-known repository for *training* small GPT models. Same name, unrelated project.

## Cost reporting

Every completion carries an `x_nanogpt_pricing` object:

```json
{
  "choices": [...],
  "usage": { "prompt_tokens": 7, "completion_tokens": 1 },
  "x_nanogpt_pricing": { "amount": 0.0000011 }
}
```

`amount` is the dollars actually charged. Note that it is **not** a standard OpenAI field, and it is not called `cost` — code that looks for `response.cost` or `usage.cost` will silently find nothing and report zero spend. `sidequest` reads `x_nanogpt_pricing.amount` first and falls back to modelled pricing only for gateways that report nothing, flagging those rows as estimates.

## The model catalog

The OpenAI-compatible `/api/v1/models` endpoint returns ids only. Add `?detailed=true` for the useful version:

```json
{
  "id": "qwen3.7-flash",
  "context_length": 991000,
  "pricing": { "prompt": 0.03, "completion": 0.13, "unit": "per_million_tokens" },
  "capabilities": { "vision": true, "reasoning": false }
}
```

`sidequest models` reads this live, so prices are current rather than baked into this repo.

## Endpoints

| Purpose | Path |
|---|---|
| Chat completions | `/api/v1/chat/completions` (also served at `/v1/chat/completions`) |
| Model catalog | `/api/v1/models?detailed=true` |
| Balance | `/api/check-balance` (POST) |

## Which models actually work for batch jobs

Measured while building this, on 1,000 short job listings with a two-field classification task:

| Model | Result |
|---|---|
| `qwen3.7-flash` | 1,000/1,000, $0.0062, 49s |
| `qwen3.5-0.8b` | 0/1,000 — could not follow the output contract, cost $0.0129 in retries |
| `meta-llama/llama-3.1-8b-instruct` | works at small batch sizes |

The lesson is not "0.8B models are useless" — it is that **the cheapest model is not the cheapest run**, and the only way to know is to measure. Run a hundred items through two candidates before committing to 25,000.
