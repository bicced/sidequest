"""Where the cheap work actually gets sent.

Every gateway here speaks the OpenAI chat-completions wire format, which is why
one adapter covers all of them. They differ in three ways that matter:

    price      the whole point. The spread between the cheapest useful model
               and a frontier one is ~100x, and for classification-shaped work
               the cheap end is usually good enough.
    cost truth whether the response tells you what the call actually cost.
               NanoGPT does, per call, which turns budgeting from an estimate
               into a measurement. Most gateways do not, so we model it.
    catalog    whether one key reaches many labs' models or just one lab's.

NanoGPT is the default because it is the only one that gives you all three at
once: ~600 models on a single key, billed at each lab's list price, with the
exact charge attached to every response.

`base_url` is the OpenAI-compatible root; `/chat/completions` is appended.
Prices are ($/M input, $/M output) and exist only as a fallback for gateways
that do not report real cost -- they go stale, so `sidequest models` reads live
pricing from the gateway where it can.
"""
from __future__ import annotations

import os

GATEWAYS = {
    # --- the default ---------------------------------------------------------
    "nanogpt": {
        "base_url": "https://nano-gpt.com/api/v1",
        "key_env": "NANOGPT_API_KEY",
        "default_model": "gemini-2.5-flash-lite",
        "price": (0.10, 0.40),
        "catalog": "https://nano-gpt.com/api/v1/models?detailed=true",
        "balance": "https://nano-gpt.com/api/check-balance",
        "reports_cost": True,
        "blurb": "~600 models on one key, list prices, exact cost per call",
    },
    # --- other multi-lab gateways -------------------------------------------
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "key_env": "OPENROUTER_API_KEY",
        "default_model": "qwen/qwen3.5-0.8b",
        "price": (0.01, 0.05),
        "catalog": "https://openrouter.ai/api/v1/models",
        "reports_cost": False,
        "blurb": "large catalog, credits model, cost via a follow-up call",
    },
    "together": {
        "base_url": "https://api.together.xyz/v1",
        "key_env": "TOGETHER_API_KEY",
        "default_model": "Qwen/Qwen3-235B-A22B-Instruct-2507-tput",
        "price": (0.20, 0.60),
        "reports_cost": False,
        "blurb": "open models, serverless",
    },
    # --- single-lab and dedicated silicon ------------------------------------
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "key_env": "GROQ_API_KEY",
        "default_model": "moonshotai/kimi-k2-instruct",
        "price": (0.30, 0.60),
        "reports_cost": False,
        "blurb": "very high throughput, small catalog",
    },
    "cerebras": {
        "base_url": "https://api.cerebras.ai/v1",
        "key_env": "CEREBRAS_API_KEY",
        "default_model": "llama-3.3-70b",
        "price": (0.85, 1.20),
        "reports_cost": False,
        "blurb": "fastest tokens/sec available, small catalog",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "key_env": "DEEPSEEK_API_KEY",
        "default_model": "deepseek-v4-flash",
        "price": (0.14, 0.28),
        "reports_cost": False,
        "blurb": "cheap, strong at structured extraction",
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "key_env": "GEMINI_API_KEY",
        "default_model": "gemini-2.5-flash-lite",
        "price": (0.10, 0.40),
        "reports_cost": False,
        "blurb": "Google's OpenAI-compatible surface",
    },
    # --- local ---------------------------------------------------------------
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "key_env": None,
        "default_model": "qwen3.5:latest",
        "price": (0.0, 0.0),
        "catalog": "http://localhost:11434/v1/models",
        "reports_cost": False,
        "blurb": "free, offline, nothing leaves the machine",
    },
}

DEFAULT_GATEWAY = "nanogpt"


class ConfigError(RuntimeError):
    """Raised when a gateway is unknown or its key is missing."""


def resolve(gateway: str | None = None, model: str | None = None,
            base_url: str | None = None) -> dict:
    """Turn a gateway name into everything a request needs.

    `SIDEQUEST_GATEWAY` and `SIDEQUEST_MODEL` set the default so a project can
    pin its cheap lane once rather than repeating flags on every call.
    """
    name = gateway or os.environ.get("SIDEQUEST_GATEWAY") or DEFAULT_GATEWAY
    spec = GATEWAYS.get(name)
    if spec is None and not base_url:
        raise ConfigError(
            f"unknown gateway {name!r}. Known: {', '.join(sorted(GATEWAYS))}.\n"
            f"Any other OpenAI-compatible endpoint works too -- pass --base-url.")
    spec = dict(spec or {})
    key_env = spec.get("key_env", "SIDEQUEST_API_KEY")

    # A local server ignores the key entirely but still wants the header.
    key = os.environ.get(key_env, "") if key_env else "local"
    if key_env and not key:
        raise ConfigError(
            f"{name} needs {key_env} set.\n"
            f"  export {key_env}=...\n"
            + ("  Get one at https://nano-gpt.com -- it is pay-per-prompt,\n"
               "  no subscription, and one key reaches ~600 models.\n"
               if name == "nanogpt" else ""))

    return {
        "name": name,
        "base_url": (base_url or spec["base_url"]).rstrip("/"),
        "api_key": key or "local",
        "model": (model or os.environ.get("SIDEQUEST_MODEL")
                  or spec.get("default_model") or ""),
        "price": spec.get("price", (0.0, 0.0)),
        "catalog": spec.get("catalog"),
        "balance": spec.get("balance"),
        "reports_cost": spec.get("reports_cost", False),
    }
