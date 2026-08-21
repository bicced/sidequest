"""One HTTP client, no dependencies.

Deliberately stdlib-only. This tool exists to be run from inside someone else's
project -- a Claude Code session, a Makefile, a CI step -- and the fastest way
to make that painful is to demand a virtualenv first. `urllib` is not elegant
but it is everywhere.

Two things here are worth more than the transport:

`cost()` reads the real charge off the response when the gateway reports one.
NanoGPT attaches `x_nanogpt_pricing.amount` to every completion, so a run can
report what it actually spent rather than what it expected to. Everything else
falls back to modelled price x tokens, which is clearly labelled as an estimate
because a modelled number that looks measured is worse than no number at all.

`loads()` parses JSON out of a reply that may be fenced, prefaced with prose, or
both. Small models do this constantly no matter what the request asked for, and
a strict parser turns a usable answer into a failed batch.
"""
from __future__ import annotations

import json
import random
import re
import time
import urllib.error
import urllib.request

USER_AGENT = "sidequest/0.1 (+https://github.com/bicced/sidequest)"

# 429 and 5xx are worth waiting out; 400/401/404 never get better on retry.
RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


class ApiError(RuntimeError):
    def __init__(self, status: int, body: str, url: str):
        self.status, self.body, self.url = status, body, url
        super().__init__(f"HTTP {status} from {url}: {body[:300]}")


def request(url: str, api_key: str, body: dict | None = None,
            timeout: float = 300.0, retries: int = 4) -> dict:
    """POST (or GET, when body is None) with backoff on transient failures."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        # OpenRouter attributes traffic with these and it costs nothing to be
        # a good citizen of the gateways we lean on.
        "HTTP-Referer": "https://github.com/bicced/sidequest",
        "X-Title": "sidequest",
    }
    data = json.dumps(body).encode() if body is not None else None
    last: Exception | None = None

    for attempt in range(retries + 1):
        req = urllib.request.Request(
            url, data=data, headers=headers,
            method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8", "replace") or "{}")
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")
            last = ApiError(e.code, text, url)
            if e.code not in RETRY_STATUS or attempt == retries:
                raise last
            # Honour Retry-After when the gateway bothers to send one.
            wait = float(e.headers.get("Retry-After") or 0) or None
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last = e
            if attempt == retries:
                raise
            wait = None
        # Full jitter: synchronised retries from 24 workers are how you turn
        # one rate-limit into a stampede.
        time.sleep(wait if wait is not None
                   else random.uniform(0, min(2 ** attempt, 16)))

    raise last or RuntimeError("request failed")


def complete(gw: dict, messages: list[dict], *, max_tokens: int = 4096,
             temperature: float = 0.0, json_mode: bool = False,
             schema: dict | None = None, timeout: float = 300.0) -> dict:
    """One chat completion. Returns {text, usage, cost, measured, model, raw}.

    Structured-output support varies by model even within one gateway, so a
    strict schema degrades to plain JSON mode and then to nothing rather than
    failing the call. The reply is parsed defensively in every case.
    """
    url = f"{gw['base_url']}/chat/completions"
    base = {"model": gw["model"], "messages": messages,
            "max_tokens": max_tokens, "temperature": temperature}

    attempts: list[dict] = []
    if schema:
        attempts.append({"response_format": {"type": "json_schema", "json_schema": {
            "name": "result", "strict": True, "schema": schema}}})
    if schema or json_mode:
        attempts.append({"response_format": {"type": "json_object"}})
    attempts.append({})

    last: Exception | None = None
    for extra in attempts:
        try:
            raw = request(url, gw["api_key"], {**base, **extra}, timeout=timeout)
        except ApiError as e:
            # A 400 here usually means "this model does not know that
            # response_format", which the next attempt drops. Anything else is
            # real and should surface.
            last = e
            if e.status == 400 and extra:
                continue
            raise
        choices = raw.get("choices") or []
        if not choices:
            last = ApiError(200, json.dumps(raw)[:300], url)
            continue
        text = ((choices[0].get("message") or {}).get("content") or "").strip()
        amount, measured = cost(raw, gw)
        return {"text": text, "usage": raw.get("usage") or {}, "cost": amount,
                "measured": measured, "model": raw.get("model") or gw["model"],
                "raw": raw}

    raise last or RuntimeError("no usable response")


def cost(raw: dict, gw: dict) -> tuple[float, bool]:
    """(dollars, measured?) -- True only when the gateway reported real cost."""
    # NanoGPT: the exact charge, on every completion.
    pricing = raw.get("x_nanogpt_pricing")
    if isinstance(pricing, dict):
        for key in ("amount", "cost", "total"):
            v = pricing.get(key)
            if isinstance(v, (int, float)):
                return float(v), True

    usage = raw.get("usage") or {}
    for src in (raw, usage):
        for key in ("cost", "total_cost"):
            v = src.get(key)
            if isinstance(v, (int, float)):
                return float(v), True

    # Fall back to the modelled price. Flagged as an estimate so a ledger never
    # presents a guess as a measurement.
    pin, pout = gw.get("price", (0.0, 0.0))
    tin = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
    tout = usage.get("completion_tokens") or usage.get("output_tokens") or 0
    return (tin * pin + tout * pout) / 1e6, False


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def loads(text: str):
    """Parse JSON out of a model reply that may be fenced or prose-wrapped."""
    if not text:
        return None
    for candidate in (text, *(m.group(1) for m in _FENCE.finditer(text))):
        try:
            return json.loads(candidate.strip())
        except json.JSONDecodeError:
            pass
    # Last resort: the outermost {...} or [...] span.
    for open_c, close_c in (("[", "]"), ("{", "}")):
        i, j = text.find(open_c), text.rfind(close_c)
        if 0 <= i < j:
            try:
                return json.loads(text[i:j + 1])
            except json.JSONDecodeError:
                pass
    return None
