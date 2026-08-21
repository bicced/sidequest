---
name: sidequest
description: Delegate bulk LLM work - classifying, extracting, summarising or rewriting many items - to a cheap model via the sidequest CLI, so results go to a file instead of into context. Use when a task involves running the same instruction over tens, hundreds or thousands of items.
---

# sidequest - delegating bulk work

Some work is enormous in aggregate and trivial per item: classify 4,000
listings, pull a field out of 900 scraped pages, rewrite 300 alt texts. Reading
all of it yourself is the expensive mistake — you burn context on data nobody
needs to read, and you slow down for the rest of the session.

Send it to a cheap model instead and read the summary.

## When to reach for this

**Do** delegate when the work is:
- the same instruction applied to many items, and
- mechanical rather than a matter of judgement, and
- more than about 30 items.

**Do not** delegate:
- reasoning about the user's actual code or architecture
- anything where being wrong is expensive and hard to notice
- fewer than ~20 items — the setup costs more than it saves

## The command

```bash
sidequest map \
  --items items.jsonl \
  --instruction "Decide X about each item. Return fields: verdict, reason." \
  --require verdict,reason \
  --out results.jsonl \
  --batch 40 --workers 16
```

Items can be `.jsonl` (one object per line), `.json` (an array), or a plain text
file (one item per line). Results are `.jsonl` with an `i` field indexing back
into the input.

It prints a summary and a path. **Do not cat the results file.** Read a few
lines to sanity-check if you must, then work with the path.

## Getting it right

**Always pass `--require`,** naming every field the instruction asks for. Small
models will echo your input back in valid JSON rather than doing the task. That
parses cleanly and is completely wrong. `--require` rejects those, retries them
smaller, and reports what still failed instead of writing it.

**Name the fields in the instruction, explicitly.** "Return fields: verdict
(one of apply, maybe, skip) and reason (max 8 words)" works. "Classify these"
does not.

**Batch size matters more than model choice.** The instruction repeats on every
call, so `--batch 40` amortises it to a fortieth. Start at 40; drop to 10-20 if
items are long.

**Pick a model that can actually do it.** `sidequest models --max-price 0.20`
lists what is available with live prices. A 0.8B model cannot reliably follow a
two-field output contract; a small flash-tier model can, and often costs *less
overall* because it does not fail and retry. If a run aborts early saying the
model produced nothing usable, that is what happened — pick a stronger one
rather than lowering the bar.

**Run 20 items first** when the task is unfamiliar, with `--limit 20`. Look at
the output. Then run the rest.

## Long jobs

```bash
sidequest map ... --background        # returns a job id immediately
sidequest status <id>                 # progress
sidequest cancel <id>
```

Use this for anything over a few thousand items. Results stream to the output
file as they complete, and `--resume` (on by default) means a re-run picks up
where it stopped rather than starting over.

## One-off cheap questions

```bash
sidequest ask "Summarise this in 3 bullets" --file notes.md
```

Useful for bulk-ish text work mid-task. Not a substitute for your own reasoning.

## Reporting back

Tell the user what it cost — `sidequest` reports real measured cost on NanoGPT,
not an estimate. If items failed, say how many and why rather than presenting a
partial file as complete. A run that wrote 950 of 1,000 rows is not a success.
