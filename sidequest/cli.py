"""The command surface.

Output is deliberately terse. `sidequest map` prints a summary and a file path,
never the results -- if you wanted 25,000 rows in your terminal (or in an
agent's context window) you did not need this tool. Everything that returns bulk
data returns a path to it.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import __version__, catalog, client, gateways, jobs, ledger, mapper


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "cmd", None):
        parser.print_help()
        return 0
    try:
        return args.fn(args, argv)
    except gateways.ConfigError as e:
        print(f"sidequest: {e}", file=sys.stderr)
        return 2
    except client.ApiError as e:
        print(f"sidequest: {e}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        return 130


# ---------------------------------------------------------------- commands --

def cmd_ask(args, argv) -> int:
    if args.background:
        meta = jobs.spawn(argv, label=(args.prompt or "")[:60])
        print(f"job {meta['id']} started (pid {meta['pid']})\n"
              f"  sidequest status {meta['id']}")
        return 0

    gw = gateways.resolve(args.gateway, args.model, args.base_url)
    prompt = args.prompt
    if args.file:
        prompt = Path(args.file).read_text(encoding="utf-8")
    if not prompt:
        prompt = sys.stdin.read()

    messages = []
    if args.system:
        messages.append({"role": "system", "content": args.system})
    messages.append({"role": "user", "content": prompt})

    resp = client.complete(gw, messages, max_tokens=args.max_tokens,
                           temperature=args.temperature, json_mode=args.json)
    ledger.record(gw["name"], resp["model"], resp["cost"], resp["measured"],
                  resp["usage"], kind="ask")

    if args.out:
        Path(args.out).write_text(resp["text"], encoding="utf-8")
        print(f"{args.out}  ({len(resp['text'])} chars, {_money(resp)})")
    else:
        print(resp["text"])
        if not args.quiet:
            print(f"\n-- {resp['model']}  {_money(resp)}", file=sys.stderr)
    return 0


def cmd_map(args, argv) -> int:
    if args.background:
        meta = jobs.spawn(argv, label=f"map {args.items}")
        print(f"job {meta['id']} started (pid {meta['pid']})\n"
              f"  sidequest status {meta['id']}\n"
              f"  results stream to {args.out}")
        return 0

    gw = gateways.resolve(args.gateway, args.model, args.base_url)
    items = mapper.load_items(Path(args.items))
    if args.limit:
        items = items[:args.limit]
    schema = json.loads(Path(args.schema).read_text()) if args.schema else None
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    started = time.time()
    last = [0.0]

    def progress(st):
        # One line every few seconds: enough to see it moving in a job log,
        # not so much that the log becomes the expensive artifact.
        if time.time() - last[0] < 3:
            return
        last[0] = time.time()
        done = st["ok"] + st["skipped"]
        print(f"  {done}/{st['total']}  ${st['cost']:.4f}", flush=True)

    print(f"{len(items)} items -> {gw['name']}/{gw['model']}  "
          f"batch={args.batch} workers={args.workers}", flush=True)
    require = [f.strip() for f in (args.require or "").split(",") if f.strip()]
    st = mapper.run(gw, items, args.instruction, out, batch=args.batch,
                    workers=args.workers, max_tokens=args.max_tokens,
                    schema=schema, resume=not args.no_resume,
                    require=require, on_progress=progress)

    mins = (time.time() - started) / 60
    tag = "" if st["measured"] else " (estimated)"
    print(f"\n{st['ok']} written, {st['skipped']} already done, "
          f"{st['failed']} failed")
    print(f"{st['calls']} calls  ${st['cost']:.4f}{tag}  {mins:.1f} min")
    print(f"-> {out}")
    for err in st["errors"]:
        print(f"   ! {err}", file=sys.stderr)
    if st.get("aborted"):
        print(f"\nStopped early: {gw['model']} failed every batch and produced "
              f"nothing usable.\nIt is most likely not able to follow the output "
              f"contract. Try a stronger model:\n"
              f"  sidequest models --max-price 0.20", file=sys.stderr)
    # Any failure is a non-zero exit. A partial result is still an incomplete
    # one, and `--resume` makes re-running to fill the gaps nearly free.
    return 1 if st["failed"] else 0


def cmd_models(args, argv) -> int:
    gw = gateways.resolve(args.gateway, None, args.base_url)
    rows = catalog.fetch(gw)
    hits = catalog.search(rows, args.query or "", max_price=args.max_price,
                          min_context=args.min_context, vision=args.vision,
                          limit=args.limit)
    if args.json:
        print(json.dumps(hits, indent=2))
        return 0
    if not hits:
        print(f"no models matched (catalog has {len(rows)})")
        return 1
    print(f"{'model':52} {'$/M in':>8} {'$/M out':>8} {'ctx':>9}")
    print("-" * 81)
    for r in hits:
        ctx = f"{r['context'] // 1000}k" if r["context"] else "-"
        print(f"{r['id'][:52]:52} {r['in']:8.3f} {r['out']:8.3f} {ctx:>9}")
    print(f"\n{len(hits)} of {len(rows)} models on {gw['name']}")
    return 0


def cmd_balance(args, argv) -> int:
    gw = gateways.resolve(args.gateway, None, args.base_url)
    if not gw.get("balance"):
        print(f"{gw['name']} has no balance endpoint; "
              f"use `sidequest ledger` for local spend.")
        return 1
    data = client.request(gw["balance"], gw["api_key"], body={}, timeout=30)
    usd = data.get("usd_balance")
    print(f"{gw['name']}: ${float(usd):.4f}" if usd is not None
          else json.dumps(data, indent=2))
    return 0


def cmd_ledger(args, argv) -> int:
    since = time.time() - args.days * 86400 if args.days else None
    rows = ledger.read(since)
    if not rows:
        print("no calls recorded yet")
        return 0
    s = ledger.summarise(rows)
    if args.json:
        print(json.dumps(s, indent=2))
        return 0
    window = f"last {args.days}d" if args.days else "all time"
    print(f"{s['calls']} calls, ${s['total']:.4f} ({window})")
    if s["estimated_portion"]:
        print(f"  ${s['estimated_portion']:.4f} of that is estimated, "
              f"not reported by the gateway")
    print()
    print(f"{'model':52} {'calls':>6} {'cost':>10}")
    print("-" * 70)
    for name, m in s["by_model"].items():
        print(f"{name[:52]:52} {m['calls']:6d} {m['cost']:10.4f}")
    return 0


def cmd_jobs(args, argv) -> int:
    rows = jobs.listing()
    if not rows:
        print("no jobs")
        return 0
    for j in rows:
        state = "running" if j["running"] else "done"
        print(f"{j['id']}  {state:8} {j['elapsed']/60:5.1f}m  {j.get('label','')}")
    return 0


def cmd_status(args, argv) -> int:
    s = jobs.status(args.id)
    if not s:
        print(f"no such job: {args.id}", file=sys.stderr)
        return 1
    print(f"{s['id']}  {'running' if s['running'] else 'done'}  "
          f"{s['elapsed']/60:.1f} min")
    if s["tail"]:
        print("\n" + s["tail"])
    return 0


def cmd_result(args, argv) -> int:
    s = jobs.status(args.id)
    if not s:
        print(f"no such job: {args.id}", file=sys.stderr)
        return 1
    print(Path(s["log"]).read_text(encoding="utf-8", errors="replace"))
    return 0


def cmd_cancel(args, argv) -> int:
    ok = jobs.cancel(args.id)
    print(f"cancelled {args.id}" if ok else f"{args.id} was not running")
    return 0 if ok else 1


# ----------------------------------------------------------------- parsing --

def _money(resp) -> str:
    return (f"${resp['cost']:.6f}" if resp["measured"]
            else f"~${resp['cost']:.6f} est")


def _common(p):
    p.add_argument("-g", "--gateway", help="nanogpt (default), openrouter, groq, ...")
    p.add_argument("-m", "--model", help="model id; see `sidequest models`")
    p.add_argument("--base-url", help="any other OpenAI-compatible endpoint")
    p.add_argument("--max-tokens", type=int, default=4096)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sidequest",
        description="Delegate bulk LLM work to cheap models, from anywhere.",
        epilog="docs: https://github.com/bicced/sidequest")
    p.add_argument("--version", action="version",
                   version=f"sidequest {__version__}")
    sub = p.add_subparsers(dest="cmd")

    a = sub.add_parser("ask", help="one prompt, one answer")
    _common(a)
    a.add_argument("prompt", nargs="?")
    a.add_argument("-f", "--file", help="read the prompt from a file")
    a.add_argument("-s", "--system", help="system prompt")
    a.add_argument("-o", "--out", help="write the answer to a file")
    a.add_argument("--json", action="store_true", help="ask for JSON output")
    a.add_argument("--temperature", type=float, default=0.0)
    a.add_argument("-q", "--quiet", action="store_true")
    a.add_argument("-b", "--background", action="store_true")
    a.set_defaults(fn=cmd_ask)

    m = sub.add_parser("map", help="run one instruction over many items")
    _common(m)
    m.add_argument("-i", "--items", required=True, help=".jsonl, .json or lines")
    m.add_argument("-I", "--instruction", required=True)
    m.add_argument("-o", "--out", required=True, help="results, as .jsonl")
    m.add_argument("--batch", type=int, default=20,
                   help="items per request (default 20; higher is cheaper)")
    m.add_argument("--workers", type=int, default=8)
    m.add_argument("--schema", help="JSON Schema file for each result")
    m.add_argument("--require", help="comma-separated fields every result must "
                                     "have; missing ones are retried, not kept")
    m.add_argument("--limit", type=int, help="only the first N items")
    m.add_argument("--no-resume", action="store_true")
    m.add_argument("-b", "--background", action="store_true")
    m.set_defaults(fn=cmd_map)

    mo = sub.add_parser("models", help="search the catalog by price")
    mo.add_argument("query", nargs="?", default="")
    mo.add_argument("-g", "--gateway")
    mo.add_argument("--base-url")
    mo.add_argument("--max-price", type=float, help="max $/M output tokens")
    mo.add_argument("--min-context", type=int, default=0)
    mo.add_argument("--vision", action="store_true")
    mo.add_argument("--limit", type=int, default=40)
    mo.add_argument("--json", action="store_true")
    mo.set_defaults(fn=cmd_models)

    b = sub.add_parser("balance", help="credit remaining at the gateway")
    b.add_argument("-g", "--gateway")
    b.add_argument("--base-url")
    b.set_defaults(fn=cmd_balance)

    lg = sub.add_parser("ledger", help="what this has cost so far")
    lg.add_argument("--days", type=int, default=0)
    lg.add_argument("--json", action="store_true")
    lg.set_defaults(fn=cmd_ledger)

    j = sub.add_parser("jobs", help="background jobs")
    j.set_defaults(fn=cmd_jobs)
    for name, fn, helptext in (("status", cmd_status, "job progress"),
                               ("result", cmd_result, "job output"),
                               ("cancel", cmd_cancel, "stop a job")):
        s = sub.add_parser(name, help=helptext)
        s.add_argument("id")
        s.set_defaults(fn=fn)

    return p
