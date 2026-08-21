"""Long work that outlives the turn that started it.

A 25,000-item map takes a while. An agent that blocks on it is an agent doing
nothing for twenty minutes, and a chat client that times out at 120 seconds
cannot run it at all -- which is precisely the ceiling you hit trying to do this
kind of work through a synchronous tool call.

So: detach, write to a file, hand back an id. The caller checks in when it suits
them. State is a JSON file per job; the log is the process's own stdout.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def home() -> Path:
    return Path(os.environ.get("SIDEQUEST_HOME") or Path.home() / ".sidequest")


def jobs_dir() -> Path:
    d = home() / "jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def new_id() -> str:
    return time.strftime("%m%d-%H%M%S", time.localtime())


def spawn(argv: list[str], label: str = "") -> dict:
    """Re-run this CLI detached, with --background stripped."""
    jid = new_id()
    d = jobs_dir()
    log = d / f"{jid}.log"
    clean = [a for a in argv if a not in ("--background", "-b")]

    # The child runs in the caller's cwd so relative paths in argv still
    # resolve, which means `-m sidequest` cannot rely on cwd to find the
    # package. Put its parent on PYTHONPATH explicitly.
    env = dict(os.environ)
    pkg_parent = str(Path(__file__).resolve().parent.parent)
    env["PYTHONPATH"] = os.pathsep.join(
        [pkg_parent, env["PYTHONPATH"]] if env.get("PYTHONPATH") else [pkg_parent])

    with open(log, "w", encoding="utf-8") as out:
        proc = subprocess.Popen(
            [sys.executable, "-m", "sidequest", *clean],
            stdout=out, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, env=env,
            # Its own process group, so closing the parent shell or ending the
            # agent turn does not take the work with it.
            start_new_session=True,
            cwd=os.getcwd(),
        )
    meta = {"id": jid, "pid": proc.pid, "argv": clean, "label": label,
            "started": time.time(), "log": str(log), "cwd": os.getcwd()}
    (d / f"{jid}.json").write_text(json.dumps(meta, indent=2))
    return meta


def load(jid: str) -> dict | None:
    p = jobs_dir() / f"{jid}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return None


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def status(jid: str) -> dict | None:
    meta = load(jid)
    if not meta:
        return None
    running = alive(meta["pid"])
    log = Path(meta["log"])
    tail = ""
    if log.exists():
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
        tail = "\n".join(lines[-12:])
    return {**meta, "running": running,
            "elapsed": time.time() - meta["started"], "tail": tail}


def listing() -> list[dict]:
    out = []
    for p in sorted(jobs_dir().glob("*.json"), reverse=True):
        s = status(p.stem)
        if s:
            out.append(s)
    return out


def cancel(jid: str) -> bool:
    meta = load(jid)
    if not meta or not alive(meta["pid"]):
        return False
    try:
        # Kill the group -- the child may have spawned workers of its own.
        os.killpg(os.getpgid(meta["pid"]), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        try:
            os.kill(meta["pid"], signal.SIGTERM)
        except ProcessLookupError:
            return False
    return True
