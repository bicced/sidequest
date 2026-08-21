# sidequest

**Delegate bulk LLM work to cheap models — from your terminal, your scripts, or from inside Claude Code — without the results flooding your context window.**

One dependency-free Python CLI. Works with [NanoGPT](https://nano-gpt.com) (~600 models on one pay-per-prompt key), OpenRouter, Groq, DeepSeek, Cerebras, Together, or a local Ollama server.

```bash
sidequest map --items listings.jsonl --out verdicts.jsonl \
  --instruction "Classify each listing: apply, maybe or skip. Return verdict and reason." \
  --require verdict
```

```
1000 items -> nanogpt/qwen3.7-flash  batch=40 workers=16
1000 written, 0 already done, 0 failed
28 calls  $0.0062  0.8 min
-> verdicts.jsonl
```

That is a real run, not an illustration: 1,000 listings classified for **$0.0062 in 49 seconds**. Your agent reads the four-line summary. The 1,000 results are in a file.

---

## The problem

You have work that is enormous in aggregate and trivial per item: classify 25,000 job listings, extract fields from 4,000 scraped pages, rewrite 900 alt texts, label a dataset. Two expensive mistakes are available:

1. **Sending it to a frontier model.** The price spread between a capable small model and a frontier one is roughly 100x, and classification does not need frontier reasoning.
2. **Letting an agent read the results.** This one is quieter and worse. If Claude Code calls a cheap model and the reply lands in the conversation, you paid *premium context tokens* to store a budget model's output. The savings evaporate and your context window fills with data nobody needed to read.

`sidequest` fixes both. Cheap model, and the output goes to a file — the caller gets a summary and a path.

## Install

```bash
pip install sidequest          # or: uv tool install sidequest
export NANOGPT_API_KEY=...     # https://nano-gpt.com — pay per prompt, no subscription
```

No dependencies. Python 3.9+. It is stdlib-only on purpose, so it runs inside someone else's project without touching their virtualenv.

## Quickstart

```bash
# What can I use, and what does it cost?
sidequest models --max-price 0.10

# One question
sidequest ask "Summarise this changelog in 3 bullets" --file CHANGELOG.md

# The main event: one instruction across many items
sidequest map \
  --items pages.jsonl \
  --instruction "Extract company, role and hourly rate. Return all three fields." \
  --require company,role,rate \
  --out extracted.jsonl \
  --batch 40 --workers 16

# What has this cost me?
sidequest ledger
```

## Why `map` is the whole point

Three design decisions do most of the work:

**Batching.** The instruction is identical on every call, so one item per request pays for that prefix N times. `--batch 40` amortises it to 1/40th. This matters more than which cheap model you picked.

**Index validation.** Every result echoes back its item number and the set is checked. Small models drop and duplicate items — silently, and more often on long batches. A mis-aligned batch is split and retried rather than written, because a silently shifted column of 25,000 results is far worse than a crash.

**Field validation.** `--require verdict,reason` rejects results missing what you asked for. Small models will happily echo your input back at you in valid JSON; that parses cleanly and is completely wrong. Rejected items are retried at a smaller batch size, then reported as failures — never written.

This is not hypothetical. Running the benchmark above on a 0.8B model produced perfectly-formed JSON that echoed the input `rate` field instead of deciding anything. Without `--require` that is 1,000 silently wrong rows. With it, the run stops after three failed batches and tells you to pick a better model — having spent less than a cent to find out.

**Incremental writes + `--resume`.** Results land as batches complete. A run that dies at item 19,000 resumes from 19,000.

## Using it from Claude Code

This is what `sidequest` was built for. The `claude/` directory has a ready-made skill and subagent:

```bash
./claude/install.sh          # copies into ~/.claude/
```

You get:

- **a skill** that teaches Claude when to route work to a cheap model, how to pick one, and how to size batches
- **a subagent** that is a thin forwarding wrapper — it shells out to `sidequest` and returns only the summary, so the bulk output never enters your main context

Then in a session: *"there are 4,000 scraped pages in data/ — pull out the contact email from each"* and it delegates, rather than reading 4,000 pages itself.

See [docs/claude-code.md](docs/claude-code.md) for the details, including why this works better as a CLI than as an MCP server.

## Choosing a model

`sidequest models` reads live pricing from the gateway rather than a table baked into this repo that goes stale in a fortnight.

```bash
sidequest models --max-price 0.10          # cheapest first
sidequest models qwen --min-context 100000 # by name, with a context floor
sidequest models --vision                  # can read images
```

Picking by vibes is how you end up paying frontier prices to strip HTML tags, or handing a reasoning task to a 0.8B model and concluding the whole idea does not work. **Benchmark before you commit**: whether a cheap model is good enough is measurable, not a matter of taste. Run a hundred items through two models and compare against labels you trust.

## Cost tracking that is actually true

NanoGPT attaches the exact charge to every completion. `sidequest` reads it and logs it:

```
$ sidequest ledger
186 calls, $0.0194 (all time)

model                                                 calls       cost
----------------------------------------------------------------------
nanogpt/qwen3.5-0.8b                                    132     0.0129
nanogpt/qwen3.7-flash                                    28     0.0062
nanogpt/meta-llama/llama-3.1-8b-instruct                 26     0.0004
```

That is this project's own ledger from building it, and it contains a lesson:
the 0.8B model cost **twice as much as the stronger one** and produced nothing.
It failed every batch, and each failure was retried at a smaller batch size
before being rejected. A cheaper model is not a cheaper run.

Gateways that do not report cost get a modelled figure from live catalog prices, and the ledger says so explicitly — an estimate presented as a measurement is worse than no number at all.

## Gateways

| Gateway | Models | Real cost per call | Notes |
|---|---|---|---|
| **nanogpt** (default) | ~600 | **yes** | one key, list prices, pay per prompt |
| openrouter | ~300 | no | large catalog, credits |
| groq | small | no | very high throughput |
| cerebras | small | no | fastest tokens/sec |
| deepseek | few | no | strong at structured extraction |
| together | many | no | open models |
| ollama | local | free | nothing leaves the machine |

Any other OpenAI-compatible endpoint works with `--base-url`.

## Commands

| Command | Does |
|---|---|
| `ask` | one prompt, one answer |
| `map` | one instruction over many items — batched, concurrent, resumable |
| `models` | search the catalog by price, context, capability |
| `balance` | credit remaining at the gateway |
| `ledger` | what this has cost, by model |
| `jobs` / `status` / `result` / `cancel` | background runs |

Add `--background` to `ask` or `map` for work that outlives the shell that started it.

## FAQ

**How do I use cheap models from Claude Code?**
Install the skill in `claude/`, then ask Claude to delegate bulk work. It shells out to `sidequest` and only the summary comes back. See [docs/claude-code.md](docs/claude-code.md).

**Why not just use an MCP server?**
MCP returns tool results directly into the conversation, which defeats the purpose for bulk work — you save on the model and pay it back in context. MCP calls are also synchronous and time-limited, so a twenty-minute job cannot run at all. A CLI can detach, write to a file, and hand back a summary. [Longer answer](docs/claude-code.md#why-not-mcp).

**Is this a NanoGPT client?**
It defaults to NanoGPT because it is the only gateway that gives you a large catalog, list-price billing and true per-call cost at once. But every gateway above works, and so does anything else speaking the OpenAI format.

**What is NanoGPT?**
A pay-per-prompt API gateway reaching ~600 models on one key with no subscription, billed at each lab's list price. (Not to be confused with Karpathy's `nanoGPT`, which is a GPT *training* repo — unrelated project, similar name.)

**How cheap is cheap?**
Measured: 1,000 short job listings classified into `apply`/`maybe`/`skip` with a reason, using `qwen3.7-flash` at `--batch 40 --workers 16`, cost **$0.0062 and took 49 seconds**. That extrapolates to about **$0.16 per 25,000 items**. Your costs scale with your text length — measure yours with `sidequest ledger`.

**Does it work outside Claude Code?**
Yes. It is a normal CLI. Cron it, pipe it, call it from a Makefile or a CI step.

**What if a model returns garbage?**
`--require` rejects results missing the fields you asked for and retries them at a smaller batch size. Anything still failing is reported and left out of the output file rather than silently written.

## Related

- [NanoGPT](https://nano-gpt.com) — the default gateway
- [Claude Code](https://claude.com/claude-code) — the agent this was built to sit beside
- [docs/nanogpt.md](docs/nanogpt.md) — NanoGPT specifics: cost reporting, model catalog
- [docs/providers.md](docs/providers.md) — adding a gateway

## License

MIT
