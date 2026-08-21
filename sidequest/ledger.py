"""What the cheap lane actually cost.

The reason to keep a ledger at all: delegating to a $0.01/M model feels free,
which is exactly the condition under which people stop counting. A run of
25,000 items at a batch size of one costs 40x the same run batched at 40, and
nothing in the output tells you that unless something is adding it up.

One JSONL line per call, appended. Not a database -- a log you can grep, and
that survives the process dying halfway through a long map.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path


def ledger_path() -> Path:
    return Path(os.environ.get("SIDEQUEST_HOME")
                or Path.home() / ".sidequest") / "ledger.jsonl"


def record(gateway: str, model: str, cost: float, measured: bool,
           usage: dict, kind: str = "ask", label: str = "") -> None:
    """Append one call. Never raises -- accounting must not break the work."""
    try:
        path = ledger_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "ts": time.time(),
            "gateway": gateway,
            "model": model,
            "kind": kind,
            "cost": round(float(cost), 10),
            # An estimate and a measurement are different kinds of number and
            # the ledger keeps them distinguishable forever.
            "measured": bool(measured),
            "in": usage.get("prompt_tokens") or usage.get("input_tokens") or 0,
            "out": usage.get("completion_tokens") or usage.get("output_tokens") or 0,
        }
        if label:
            row["label"] = label
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except OSError:
        pass


def read(since: float | None = None) -> list[dict]:
    path = ledger_path()
    if not path.exists():
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue          # a torn line from a killed process
            if since is None or row.get("ts", 0) >= since:
                rows.append(row)
    return rows


def summarise(rows: list[dict]) -> dict:
    by_model: dict[str, dict] = {}
    total = est = 0.0
    for r in rows:
        m = by_model.setdefault(f"{r.get('gateway','?')}/{r.get('model','?')}",
                                {"calls": 0, "cost": 0.0, "in": 0, "out": 0,
                                 "estimated": 0})
        m["calls"] += 1
        m["cost"] += r.get("cost", 0.0)
        m["in"] += r.get("in", 0)
        m["out"] += r.get("out", 0)
        if not r.get("measured"):
            m["estimated"] += 1
            est += r.get("cost", 0.0)
        total += r.get("cost", 0.0)
    return {"calls": len(rows), "total": total, "estimated_portion": est,
            "by_model": dict(sorted(by_model.items(),
                                    key=lambda kv: -kv[1]["cost"]))}
