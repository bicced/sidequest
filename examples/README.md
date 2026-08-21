# Examples

`listings.jsonl` — five job listings, enough to try `map` on.

```bash
sidequest map \
  --items examples/listings.jsonl \
  --instruction "Decide whether each listing is contract or fractional work paying at least \$110/hr. Return verdict (exactly one of: apply, maybe, skip) and reason (max 8 words)." \
  --require verdict,reason \
  --out /tmp/verdicts.jsonl \
  --batch 5
```

Costs well under a cent. Then look at `/tmp/verdicts.jsonl`.

Try it with a deliberately weak model to see the guard fire:

```bash
sidequest map ... -m qwen3.5-0.8b --require verdict,reason
```

It should stop early and tell you the model cannot follow the output contract,
rather than writing five wrong rows.
