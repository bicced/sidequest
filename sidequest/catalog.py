"""Which of the 600 models should do this job.

Picking a model by vibes is how you end up paying frontier prices to strip HTML
tags, or handing a reasoning task to a 0.8B model and concluding the whole idea
does not work. Both mistakes are avoidable with a price list.

NanoGPT publishes live pricing, context length and capabilities for its whole
catalog, so `sidequest models` reads the real numbers instead of a table baked
into this file that goes stale in a fortnight. Gateways without a catalog
endpoint degrade to a plain model-id list, which is still better than guessing.
"""
from __future__ import annotations

from . import client


def fetch(gw: dict) -> list[dict]:
    """Normalised catalog rows: id, name, in/out price, context, capabilities."""
    url = gw.get("catalog") or f"{gw['base_url']}/models"
    raw = client.request(url, gw["api_key"], timeout=60)
    rows = []
    for m in (raw.get("data") or raw.get("models") or []):
        if not isinstance(m, dict) or not m.get("id"):
            continue
        rows.append({
            "id": m["id"],
            "name": m.get("name") or m["id"],
            "in": _price(m, "prompt", "input"),
            "out": _price(m, "completion", "output"),
            "context": m.get("context_length") or m.get("context") or 0,
            "vision": bool((m.get("capabilities") or {}).get("vision")),
            "reasoning": bool((m.get("capabilities") or {}).get("reasoning")),
            "description": (m.get("description") or "")[:160],
        })
    return rows


def _price(m: dict, *keys) -> float:
    """Per-million-token price, normalised across the two conventions in use."""
    p = m.get("pricing") or {}
    for k in keys:
        v = p.get(k)
        if v is None:
            continue
        try:
            v = float(v)
        except (TypeError, ValueError):
            continue
        # OpenRouter quotes dollars per token; NanoGPT quotes per million and
        # says so. Anything below this threshold is per-token in disguise.
        unit = str(p.get("unit") or "")
        if "million" in unit:
            return v
        return v * 1e6 if v < 0.001 else v
    return 0.0


def search(rows: list[dict], query: str = "", *, max_price: float | None = None,
           min_context: int = 0, vision: bool = False,
           limit: int = 40) -> list[dict]:
    q = query.lower().strip()
    out = []
    for r in rows:
        if q and q not in r["id"].lower() and q not in r["name"].lower():
            continue
        if min_context and r["context"] < min_context:
            continue
        if vision and not r["vision"]:
            continue
        # Free models report 0.0, which would otherwise dominate a cheap sort
        # and send people to models that are rate-limited into uselessness.
        if max_price is not None and r["out"] > max_price:
            continue
        out.append(r)
    out.sort(key=lambda r: (r["in"] + r["out"], r["id"]))
    return out[:limit]
