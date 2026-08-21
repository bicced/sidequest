"""Run one instruction across many items, cheaply, without reading the output.

This is the command that justifies the tool. Classifying 25,000 job listings,
extracting fields from 4,000 scraped pages, rewriting 900 alt texts -- work that
is mechanical, embarrassingly parallel, and enormous in aggregate but trivial
per item. Handing it to a frontier model is the expensive mistake; handing it to
an agent that reads every result into its context is the *other* expensive
mistake, and the one people notice less.

Three decisions carry most of the value:

**Batching.** The instruction is identical on every call, so sending one item
per request pays for that prefix N times. Batching 40 items amortises it to
1/40th. This matters far more than which cheap model you picked.

**Index echoing.** The model returns `i` with each result and we validate the
set. Small models drop and duplicate items -- silently, and more often on long
batches. A batch that comes back mis-aligned is split and retried rather than
written, because a silently shifted column of 25,000 results is worse than a
crash.

**Incremental writes.** Results land in the output file as batches complete, so
a run that dies at item 19,000 resumes from 19,000 rather than from zero.
"""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import client, ledger

CONTRACT = """You process items in batches. You will be given numbered items.

Return ONLY a JSON object of exactly this shape:

  {"results": [{"i": 0, ...}, {"i": 1, ...}]}

Rules:
  - one entry in "results" for every item you were given, no more and no fewer
  - "i" is the item's number, echoed back exactly as given
  - put the fields the task asks for alongside "i" in the same object
  - no commentary, no markdown fence, no text outside the JSON object
"""

def run(gw: dict, items: list, instruction: str, out_path, *, batch: int = 20,
        workers: int = 8, max_tokens: int = 4096, schema: dict | None = None,
        resume: bool = True, require: list[str] | None = None,
        on_progress=None) -> dict:
    """Map `instruction` over `items`, streaming results to `out_path`."""
    done: set[int] = set()
    if resume and out_path and out_path.exists():
        with open(out_path, encoding="utf-8") as f:
            for line in f:
                try:
                    done.add(int(json.loads(line)["i"]))
                except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                    continue

    todo = [i for i in range(len(items)) if i not in done]
    batches = [todo[i:i + batch] for i in range(0, len(todo), batch)]

    lock = threading.Lock()
    handle = open(out_path, "a", encoding="utf-8") if out_path else None
    stats = {"total": len(items), "skipped": len(done), "ok": 0, "failed": 0,
             "cost": 0.0, "calls": 0, "measured": True, "errors": [],
             "aborted": False}
    # A model that cannot follow the output contract fails every batch the same
    # way, and the split-retry turns that into a lot of paid attempts before
    # anyone notices. If nothing at all has succeeded after a few batches, the
    # model is wrong for the job -- stop and say so.
    abort = threading.Event()
    failed_batches = [0]

    def emit(rows: list[dict]) -> None:
        with lock:
            for row in rows:
                if handle:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            if handle:
                handle.flush()
            stats["ok"] += len(rows)
            if on_progress:
                on_progress(stats)

    def work(idxs: list[int]) -> None:
        if abort.is_set():
            raise RuntimeError("aborted")
        for rows, meta in _batch(gw, items, idxs, instruction, max_tokens,
                                 schema, require=require):
            with lock:
                stats["cost"] += meta["cost"]
                stats["calls"] += 1
                stats["measured"] = stats["measured"] and meta["measured"]
            ledger.record(gw["name"], gw["model"], meta["cost"], meta["measured"],
                          meta["usage"], kind="map", label=f"{len(rows)} items")
            emit(rows)

    try:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {pool.submit(work, b): b for b in batches}
            for fut in as_completed(futures):
                try:
                    fut.result()
                except Exception as e:                    # noqa: BLE001
                    idxs = futures[fut]
                    with lock:
                        stats["failed"] += len(idxs)
                        if str(e) == "aborted":
                            continue
                        failed_batches[0] += 1
                        if len(stats["errors"]) < 5:
                            stats["errors"].append(f"items {idxs[0]}-{idxs[-1]}: {e}")
                        if stats["ok"] == 0 and failed_batches[0] >= 3:
                            stats["aborted"] = True
                            abort.set()
    finally:
        if handle:
            handle.close()
    return stats


def _batch(gw, items, idxs, instruction, max_tokens, schema, depth: int = 0,
           require: list[str] | None = None):
    """Yield (rows, meta) for one batch, splitting on a mis-aligned reply.

    Items are renumbered 0..n-1 for the model and mapped back afterwards. A
    small model asked to echo `[4187]` will quietly return `[3]` often enough to
    matter; asked to echo `[2]` out of three items, it rarely misses.
    """
    numbered = "\n\n".join(
        f"[{local}]\n{_render(items[abs_i])}" for local, abs_i in enumerate(idxs))
    messages = [
        {"role": "system", "content": f"{instruction.strip()}\n\n{CONTRACT}"},
        {"role": "user", "content": numbered},
    ]
    resp = client.complete(gw, messages, max_tokens=max_tokens,
                           json_mode=True, schema=_array_schema(schema))
    meta = {"cost": resp["cost"], "measured": resp["measured"],
            "usage": resp["usage"]}

    rows = _rows(client.loads(resp["text"]))
    mapped = []
    for row in rows:
        local = row.get("i")
        if not isinstance(local, int) or not 0 <= local < len(idxs):
            continue
        # A small model asked for new fields will sometimes echo the input back
        # instead. That parses cleanly and is completely wrong, so a result
        # missing what the task asked for does not count as an answer.
        if require and any(k not in row for k in require):
            continue
        mapped.append({**row, "i": idxs[local]})
    covered = {r["i"] for r in mapped}

    if len(covered) == len(idxs):
        yield mapped, meta
        return

    # A single item cannot be split further. If anything came back at all, take
    # it and pin the index ourselves rather than losing the item.
    if len(idxs) == 1 or depth >= 3:
        if mapped:
            yield mapped, meta
            return
        if rows and not require:
            yield [{**rows[0], "i": idxs[0]}], meta
            return
        missing_fields = (f" (missing {', '.join(require)})" if require else "")
        raise ValueError(
            f"item {idxs[0]}: no usable result{missing_fields}: "
            f"{resp['text'][:160]!r}")

    # Partial batches still count: keep what aligned, re-ask only for the rest.
    missing = [i for i in idxs if i not in covered]
    yield mapped, meta
    mid = max(1, len(missing) // 2)
    for half in (missing[:mid], missing[mid:]):
        if half:
            yield from _batch(gw, items, half, instruction, max_tokens, schema,
                              depth + 1, require=require)


def _rows(parsed) -> list[dict]:
    if isinstance(parsed, list):
        return [r for r in parsed if isinstance(r, dict)]
    if isinstance(parsed, dict):
        # Models like to wrap the array in {"results": [...]}.
        for v in parsed.values():
            if isinstance(v, list) and all(isinstance(x, dict) for x in v):
                return v
        return [parsed]
    return []


def _render(item) -> str:
    if isinstance(item, str):
        return item
    return json.dumps(item, ensure_ascii=False)


def _array_schema(per_item: dict | None) -> dict | None:
    """Lift a per-item schema to the array-of-results the contract asks for."""
    if not per_item:
        return None
    props = dict(per_item.get("properties") or {})
    props["i"] = {"type": "integer"}
    item = {**per_item, "type": "object", "properties": props,
            "required": sorted({*(per_item.get("required") or []), "i"}),
            "additionalProperties": False}
    return {"type": "object", "additionalProperties": False,
            "required": ["results"],
            "properties": {"results": {"type": "array", "items": item}}}


def load_items(path) -> list:
    """Read items from .jsonl (one object per line), .json (array), or lines."""
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".jsonl":
        return [json.loads(l) for l in text.splitlines() if l.strip()]
    if path.suffix == ".json":
        data = json.loads(text)
        return data if isinstance(data, list) else [data]
    return [l for l in text.splitlines() if l.strip()]
