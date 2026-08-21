# sidequest

**Cut Claude Code token costs by sending bulk work to cheap models.** A dependency-free CLI that runs one instruction across thousands of items — classify, extract, summarise, rewrite — and writes the results to a file instead of into your agent's context window.

Works with [NanoGPT](https://nano-gpt.com) (~600 models on one pay-per-prompt key), [OpenRouter](https://openrouter.ai), Groq, DeepSeek, Cerebras, Together, or a local Ollama server. Any OpenAI-compatible endpoint, really.

```bash
sidequest map --items listings.jsonl --out verdicts.jsonl \
  --instruction "Classify each listing: apply, maybe or skip. Return verdict and reason." \
  --require verdict,reason --batch 40 --workers 16
```

```
1000 items -> nanogpt/qwen3.7-flash  batch=40 workers=16
1000 written, 0 already done, 0 failed
28 calls  $0.0062  0.8 min
-> verdicts.jsonl
```

That is a real run, not an illustration: **1,000 listings classified for $0.0062 in 49 seconds.** Your agent reads those four lines. The 1,000 results are in a file.

---

## The problem it solves

You have work that is enormous in aggregate and trivial per item: classify 25,000 job listings, extract fields from 4,000 scraped pages, rewrite 900 alt texts, label a dataset. Two expensive mistakes are available.

**Sending it to a frontier model.** The price spread between a capable small model and a frontier one is roughly 100x, and classification does not need frontier reasoning.

**Letting your agent read the results.** This one is quieter and much worse. If Claude Code calls a model and the reply lands in the conversation, you paid *premium context tokens* to store a budget model's output — and your context window fills with data nobody needed to read.

Here is that second cost, measured on the benchmark above:

| | Tokens | Cost |
|---|---|---|
| Inference actually paid to the gateway | — | **$0.0062** |
| Context burned if those results came back into the conversation | **50,808** | far more, at your agent's input rate |

`sidequest` fixes both. Cheap model, and the output goes to a file — the caller gets a summary and a path.

## Install

```bash
pip install sidequest          # or: uv tool install sidequest
export NANOGPT_API_KEY=...     # https://nano-gpt.com — pay per prompt, no subscription
```

No dependencies, Python 3.9+. Stdlib-only on purpose, so it runs inside someone else's project without touching their virtualenv.

## Quickstart

```bash
# What models can I use, and what do they cost?
sidequest models --max-price 0.10

# One cheap question
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

## Using it with Claude Code

This is what `sidequest` was built for. The `claude/` directory ships a ready-made **Claude Code skill** and **subagent**:

```bash
./claude/install.sh          # copies into ~/.claude/
```

You get:

- **a skill** that teaches Claude when to route work to a cheap model, which model to pick, and how to size batches
- **a subagent** that is a thin forwarding wrapper — it shells out to `sidequest` and returns only the summary, so bulk output never enters your main context

Then in a session: *"there are 4,000 scraped pages in data/ — pull the contact email out of each"* and it delegates instead of reading 4,000 pages itself.

See [docs/claude-code.md](docs/claude-code.md), including [why this works better as a CLI than as an MCP server](docs/claude-code.md#why-not-mcp).

## Why `map` is the whole point

Four design decisions do most of the work, and each came from a real failure while building this.

**Batching.** The instruction is identical on every call, so one item per request pays for that prefix N times. `--batch 40` amortises it to a fortieth. This matters more than which cheap model you picked.

**Index validation.** Every result echoes back its item number and the set is checked. Small models drop and duplicate items, silently and more often on long batches. A mis-aligned batch is split and retried rather than written, because a silently shifted column of 25,000 results is far worse than a crash. Getting this right took one 10-item run from 16 calls down to 4.

**Field validation.** `--require verdict,reason` rejects results missing what you asked for. This is not hypothetical: running the benchmark on a 0.8B model produced perfectly-formed JSON that echoed the input back instead of deciding anything. Without `--require`, that is 1,000 silently wrong rows. With it, the run stops after three failed batches and tells you to pick a better model — having spent less than a cent to find out.

**Incremental writes and `--resume`.** Results land as batches complete, so a run that dies at item 19,000 resumes from 19,000.

## Choosing a model

`sidequest models` reads live pricing from the gateway rather than a table baked into this repo that goes stale in a fortnight.

```bash
sidequest models --max-price 0.10          # cheapest first
sidequest models qwen --min-context 100000 # by name, with a context floor
sidequest models --vision                  # can read images
```

Picking by vibes is how you end up paying frontier prices to strip HTML tags, or handing a reasoning task to a 0.8B model and concluding the whole idea does not work. **Benchmark before you commit** — run a hundred items through two candidates and compare against labels you trust.

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

That is this project's own ledger from building it, and it contains the lesson: the 0.8B model cost **twice as much as the stronger one** and produced nothing usable. It failed every batch, and every failure was retried at a smaller batch size before being rejected. **A cheaper model is not a cheaper run.**

Gateways that do not report real cost get a modelled figure from live catalog prices, and the ledger labels those rows as estimates — an estimate presented as a measurement is worse than no number at all.

## Supported gateways

| Gateway | Models | Real cost per call | Notes |
|---|---|---|---|
| **nanogpt** (default) | ~600 | **yes** | one key, list prices, pay per prompt |
| openrouter | ~300 | no | large catalog, credits |
| groq | small | no | very high throughput |
| cerebras | small | no | fastest tokens/sec |
| deepseek | few | no | strong at structured extraction |
| together | many | no | open models |
| gemini | few | no | Google's OpenAI-compatible surface |
| ollama | local | free | nothing leaves the machine |

Any other OpenAI-compatible API works with `--base-url`. See [docs/providers.md](docs/providers.md).

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

### How do I reduce Claude Code token costs?

The largest avoidable cost in an agent session is usually not the model — it is reading bulk data into context that nobody needed to read. Delegate that work with `sidequest map` and your agent reads a summary instead. On the benchmark above that is the difference between 50,808 context tokens and about 40.

### How do I use cheap models from Claude Code?

Install the skill and subagent in `claude/`, then ask Claude to delegate bulk work. It shells out to `sidequest` and only the summary comes back. See [docs/claude-code.md](docs/claude-code.md).

### Why not just use an MCP server?

MCP returns tool results directly into the conversation, which defeats the purpose for bulk work — you save on the model and pay it back in context, at your expensive model's input rate. MCP calls are also synchronous and time-limited (NanoGPT's own MCP server defaults to a 120-second timeout and forces non-streaming), so a twenty-minute job cannot run at all. And a tool schema cannot teach an agent which of 600 models suits a task or how to size a batch — a skill can. [Longer answer](docs/claude-code.md#why-not-mcp).

MCP is genuinely good for image generation, scraping and similar opportunistic capabilities. It is the wrong layer for bulk text work.

### What is the cheapest LLM API for batch classification?

It depends on your text, and the honest answer is to measure rather than trust a table. `sidequest models --max-price 0.10` lists what is currently cheapest on your gateway, and `sidequest ledger` tells you what a real run actually cost. Beware the trap above: the cheapest per-token model is often not the cheapest run, because a model that cannot follow the output contract burns your budget on retries.

### Is this an OpenRouter alternative?

It is not a gateway — it is a client that sits in front of one. It works with OpenRouter, and it also works with NanoGPT, Groq, Cerebras, DeepSeek, Together and local Ollama, so you can switch between them with one flag and compare what they actually cost you.

### What is NanoGPT?

A pay-per-prompt API gateway reaching ~600 models on one key with no subscription, billed at each lab's list price, which reports the exact cost of every call. Not to be confused with [karpathy/nanoGPT](https://github.com/karpathy/nanoGPT), a well-known repository for *training* small GPT models — same name, unrelated project.

### How cheap is cheap?

Measured: 1,000 short job listings classified into `apply`/`maybe`/`skip` with a reason, using `qwen3.7-flash` at `--batch 40 --workers 16`, cost **$0.0062 and took 49 seconds**. That extrapolates to roughly **$0.16 per 25,000 items**. Your costs scale with your text length — measure yours with `sidequest ledger`.

### Does it work outside Claude Code?

Yes. It is a normal CLI. Cron it, pipe it, call it from a Makefile, a CI step, or any language that can run a subprocess.

### What if a model returns garbage?

`--require` rejects results missing the fields you asked for and retries them at a smaller batch size. Anything still failing is reported and left out of the output file rather than silently written. If a run fails its first three batches with nothing usable, it stops and tells you the model cannot follow the output contract.

## Docs

- [docs/claude-code.md](docs/claude-code.md) — the skill, the subagent, and why not MCP
- [docs/nanogpt.md](docs/nanogpt.md) — NanoGPT specifics: per-call cost reporting, the model catalog
- [docs/providers.md](docs/providers.md) — adding a gateway, using any OpenAI-compatible API

## License

MIT
