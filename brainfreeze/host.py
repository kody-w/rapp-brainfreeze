"""host: wake a frozen brainstem inside any AI, with that AI as its model.

A normal thaw starts a server, sets up packages and asks for a Copilot sign-in. Inside an AI's own sandbox
none of that is wanted: the AI that woke the brainstem can answer for the model itself. `host` thaws a
snapshot into the current folder, runs the brainstem's real /chat in-process (no port, no network), and each
time /chat needs the model it hands the question out and waits for the answer.

    python3 -m brainfreeze host thaw demo.snapshot.tar.gz     # check every hash, wake it here
    python3 -m brainfreeze host tools                         # the agents on offer
    python3 -m brainfreeze host ask "what the person said"    # prints what the model must answer
    python3 -m brainfreeze host answer '<words>'              # finish the turn, or:
    python3 -m brainfreeze host answer '{"tool_calls": [{"function": {"name": "<Agent>", "arguments": {}}}]}'
    python3 -m brainfreeze host chat                          # the conversation so far, and which agents ran
    python3 -m brainfreeze host freeze [out.snapshot.tar.gz]  # a child snapshot that names its parent
    python3 -m brainfreeze host export [chat.json]            # the chat as the Brainstem page's Import file

The engine file is never changed: its two model calls are swapped in memory for the turn and put back.
An agent round that already ran is never run again when a turn is replayed. The Python packages the engine
imports (flask and friends) must be importable; `host` installs nothing and opens no connection.

From Python, any function can be the model:

    from brainfreeze import host
    undo = host.use(brainstem_module, answer)     # answer(messages, tools) -> one assistant message
"""
import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
import tarfile
import time
from pathlib import Path

__all__ = ["use", "scripted", "thaw", "freeze", "ask", "answer", "tools", "chat", "main"]

STATE = Path(os.environ.get("BRAINFREEZE_HOST_STATE", ".host"))
HOME = Path("rapp_brainstem")
WAIT = "\x00brainfreeze-host-waiting-for-the-model\x00"


class HostError(RuntimeError):
    pass


# ── the model plug ───────────────────────────────────────────────────────────

def _message(raw):
    if isinstance(raw, str):
        raw = {"content": raw}
    if not isinstance(raw, dict):
        raise TypeError("the model must return one assistant message (a dict) or plain text")
    msg = {"role": "assistant", "content": raw.get("content")}
    calls = raw.get("tool_calls") or []
    if calls:
        msg["tool_calls"] = []
        for i, c in enumerate(calls, 1):
            fn = c.get("function") or {}
            args = fn.get("arguments", "{}")
            msg["tool_calls"].append({"id": str(c.get("id") or f"call_{i}"), "type": "function",
                                      "function": {"name": str(fn.get("name", "")),
                                                   "arguments": args if isinstance(args, str) else json.dumps(args)}})
    elif msg["content"] is None:
        msg["content"] = ""
    return msg


def use(kernel, answer, name="host-ai"):
    """Make `answer(messages, tools)` the model for `kernel` (the imported brainstem module). Returns undo()."""
    kept = (kernel.call_copilot, kernel.call_copilot_stream)

    def call(messages, tools=None):
        msg = _message(answer(messages, tools))
        return {"choices": [{"index": 0, "message": msg,
                             "finish_reason": "tool_calls" if msg.get("tool_calls") else "stop"}], "model": name}, name

    def stream(messages, tools=None, model=None):
        msg = _message(answer(messages, tools))
        if msg.get("content"):
            yield ("delta", msg["content"])
        yield ("done", {"message": msg, "model": name, "finish_reason": "tool_calls" if msg.get("tool_calls") else "stop"})

    kernel.call_copilot, kernel.call_copilot_stream = call, stream

    def undo():
        kernel.call_copilot, kernel.call_copilot_stream = kept

    return undo


def scripted(messages, tools):
    """A stand-in model for tests: "use <Agent>" runs that agent with no arguments, an agent's output is
    repeated back whole, anything else gets a fixed line. Same input, same output, always."""
    last = messages[-1] if messages else {}
    if last.get("role") == "tool":
        return {"content": "The agent answered:\n" + "\n---\n".join(str(m.get("content", "")) for m in messages if m.get("role") == "tool")}
    text = str(last.get("content", ""))
    for n in sorted((t["function"]["name"] for t in (tools or [])), key=len, reverse=True):
        if ("use " + n).lower() in text.lower():
            return {"content": None, "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": n, "arguments": "{}"}}]}
    return {"content": f"Stand-in model. You said: {text}"}


# ── state ────────────────────────────────────────────────────────────────────

def _load(name, default):
    f = STATE / name
    return json.loads(f.read_text()) if f.exists() else default


def _save(name, value):
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / name).write_text(json.dumps(value, indent=1))


# Packages the engine imports only to serve a browser. host never opens a port: a turn goes through the
# engine's own test client, so these have nothing to do here. When one is missing, an inert stand-in is put in
# memory for this process only (nothing installed, nothing written). Anything else missing still stops host.
_SERVE_ONLY = {
    "flask_cors": {"CORS": lambda *a, **k: None, "cross_origin": lambda *a, **k: (lambda f: f)},
    "dotenv": {"load_dotenv": lambda *a, **k: False},   # .env never travels in a snapshot
}


def _stand_in_serve_only():
    import importlib.util, types
    used = []
    for name, attrs in _SERVE_ONLY.items():
        if name in sys.modules:
            continue
        try:
            if importlib.util.find_spec(name) is not None:
                continue
        except ImportError:
            pass
        m = types.ModuleType(name)
        m.__dict__.update(attrs)
        m.__brainfreeze_stand_in__ = True
        sys.modules[name] = m
        used.append(name)
    return used


def _kernel():
    _stand_in_serve_only()
    saved = _load("chat.json", {}).get("saved_model")
    if saved:                       # no sign-in here, so the engine would fall back to its default model name
        os.environ.setdefault("GITHUB_MODEL", saved)
    if not (HOME / "brainstem.py").exists():
        raise HostError("no brainstem is awake in this folder; start with: host thaw <snapshot>")
    home = str(HOME.resolve())
    if home not in sys.path:
        sys.path.insert(0, home)
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            import brainstem
    except ImportError as e:
        raise HostError(f"the engine needs a Python package that is not here: {e.name or e}. host installs nothing; "
                        "make the engine's packages importable first") from None
    return brainstem


# ── thaw and freeze ──────────────────────────────────────────────────────────

def thaw(snapshot):
    """Wake a snapshot in the current folder. Every file is checked against bundle.json before anything is used."""
    from . import bundle as _bundle
    snap = Path(snapshot)
    if not snap.is_file():
        raise HostError(f"snapshot not found: {snapshot}")
    if HOME.exists() or STATE.exists():
        raise HostError("a brainstem is already awake in this folder; thaw into an empty folder")
    stage = Path(".thaw")
    try:
        with tarfile.open(snap, "r:gz") as tar:
            for m in tar.getmembers():
                n = m.name
                if (n.startswith("/") or ".." in Path(n).parts or m.issym() or m.islnk()
                        or not (n in ("state.json", "bundle.json", "bundle.sig", "bundle.signer")
                                or n.startswith(("rapp_brainstem", "sidecars/")) or n == "sidecars")):
                    raise HostError(f"snapshot has an unsafe entry: {n}")
            tar.extractall(stage)
        checked = "no bundle.json (frozen before it existed)"
        if (stage / "bundle.json").is_file():
            try:
                _bundle.verify(stage, json.loads((stage / "bundle.json").read_text()))
            except _bundle.BundleError as e:
                raise HostError(f"refusing to wake it: {e}") from None
            checked = "every file matches bundle.json"
        signature = {"status": "unsigned"}
        if (stage / "bundle.sig").is_file():
            from . import lineage
            allowed = os.getenv("BRAINFREEZE_ALLOWED_SIGNERS") or "allowed_signers"
            status, login, why = lineage.check_signature(snap.read_bytes(), allowed)
            if status == "bad":
                raise HostError(f"refusing to wake it: the signature by {login} does not match ({why})")
            signature = {"status": status, "signer": login, "detail": why}
        state = json.loads((stage / "state.json").read_text())
        (stage / "rapp_brainstem").rename(HOME)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    history = [m for m in state.get("history", []) if isinstance(m, dict) and m.get("role") in ("user", "assistant")]
    sha = hashlib.sha256(snap.read_bytes()).hexdigest()
    _save("history.json", history)
    _save("chat.json", {"session_id": state.get("session_id") or "host-" + time.strftime("%Y%m%d%H%M%S", time.gmtime()),
                        "turns": [dict(m) for m in history], "thawed_from": {"snapshot_sha256": sha},
                        "saved_model": state.get("model")})
    return {"status": "ok", "frozen": state.get("created"), "brainstem": state.get("brainstem", {}).get("version"),
            "messages_restored": len(history), "checked": checked, "signature": signature, "snapshot_sha256": sha,
            "settings_needed": state.get("settings_needed", [])}


def freeze(out=None):
    """Freeze the brainstem awake in this folder into a standard snapshot: a child of the one it was thawed from."""
    from . import freeze as _freeze
    if not (HOME / "brainstem.py").exists():
        raise HostError("no brainstem is awake in this folder")
    chat_ = _load("chat.json", {"turns": []})
    out = Path(out or "brainstem-" + time.strftime("%Y%m%d-%H%M%S") + ".snapshot.tar.gz")
    history = [{"role": t["role"], "content": t.get("content", "")} for t in chat_["turns"]]
    model = chat_.get("model") or "host-ai"
    extra = {"woken_by": {"host": True, "model": model, "python": sys.version.split()[0]}}
    if chat_.get("thawed_from"):
        extra["parent"] = chat_["thawed_from"]
    _freeze(HOME, out, history=history, session_id=chat_.get("session_id"), model=chat_.get("saved_model") or model, extra=extra)
    sha = hashlib.sha256(out.read_bytes()).hexdigest()
    chat_["thawed_from"] = {"snapshot_sha256": sha}          # the next freeze is a child of this one
    _save("chat.json", chat_)
    return {"status": "ok", "file": str(out.resolve()), "bytes": out.stat().st_size, "messages": len(history),
            "parent": extra.get("parent"), "sha256": sha}


def _memory():
    """What the Brainstem remembers (its .brainstem_data JSON files), carried in the chat file. The page's Import
    ignores it; `brainfreeze memory-home chat.json` adds it to a Brainstem at home (add-only)."""
    data, out = HOME / ".brainstem_data", {}
    for f in sorted(data.rglob("*.json")) if data.is_dir() else []:
        try:
            out[f.relative_to(data).as_posix()] = json.loads(f.read_text())
        except (OSError, ValueError):
            pass
    return out


def export(out=None):
    """Write the conversation as the Brainstem web page's own Import file (the same shape its Export button makes),
    so the chat continues in any local Brainstem as if it never left."""
    if not (HOME / "brainstem.py").exists():
        raise HostError("no brainstem is awake in this folder")
    chat_ = _load("chat.json", {"turns": []})
    turns = []
    for t in chat_["turns"]:
        if t.get("role") in ("user", "assistant") and isinstance(t.get("content"), str):
            turn = {"role": t["role"], "content": t["content"]}
            if t.get("timestamp"):
                turn["timestamp"] = t["timestamp"]
            if t["role"] == "assistant" and isinstance(t.get("agent_logs"), str) and t["agent_logs"]:
                turn["agent_logs"] = t["agent_logs"]
            turns.append(turn)
    if not turns:
        raise HostError("nothing to export yet")
    try:
        version = (HOME / "VERSION").read_text().strip()
    except OSError:
        version = "unknown"
    out = Path(out or "brainstem-chat-" + time.strftime("%Y-%m-%dT%H-%M-%S") + ".json")
    out.write_text(json.dumps({"exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                               "session_id": chat_.get("session_id"), "brainstem_version": version,
                               "model": chat_.get("saved_model") or "unknown", "turns": turns,
                               "memory": _memory()}, indent=2))
    return {"status": "ok", "file": str(out.resolve()), "turns": len(turns),
            "load_it": "in the Brainstem page, click Import and choose this file"}


# ── a turn ───────────────────────────────────────────────────────────────────

def _run(turn):
    """Replay the turn through the real /chat. Answers already given are fed back in order; agent rounds
    already run come from the turn's record. When /chat asks for an answer not yet given, the turn waits."""
    kernel = _kernel()
    need, seen = {}, {"model": 0, "tools": 0}

    def model(messages, tools):
        i = seen["model"]
        seen["model"] += 1
        if i < len(turn["answers"]):
            return turn["answers"][i]
        if not need:
            need.update(messages=list(messages), tools=tools)
        return {"content": WAIT}

    real_tools = kernel.run_tool_calls

    def tool_round(tool_calls, agents, session_id=None):
        i = seen["tools"]
        seen["tools"] += 1
        if i < len(turn["rounds"]):
            return turn["rounds"][i]["results"], turn["rounds"][i]["logs"]
        results, logs = real_tools(tool_calls, agents, session_id=session_id)
        turn["rounds"].append({"results": results, "logs": logs,
                               "agents": [n for n in (c.get("function", {}).get("name") for c in tool_calls) if n in agents]})
        return results, logs

    undo = use(kernel, model, turn.get("model") or "host-ai")
    kernel.run_tool_calls = tool_round
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            r = kernel.app.test_client().post("/chat", json={
                "user_input": turn["words"], "session_id": turn["session_id"], "conversation_history": turn["history"]})
        body = r.get_json()
    finally:
        kernel.run_tool_calls = real_tools
        undo()
    if need:
        shown = turn.get("shown", 0)
        fresh = need["messages"][shown:]
        turn["shown"] = len(need["messages"])
        return {"need": "your answer, as the model", "new_messages": fresh,
                "agents_you_may_call": [t["function"] for t in (need["tools"] or [])] if not shown else
                                       ("the same agents as before" if need["tools"] else "none on this step: answer in words"),
                "answer_with": "host answer '<words for the person>'   or   "
                               "host answer '{\"tool_calls\": [{\"function\": {\"name\": \"<Agent>\", \"arguments\": {}}}]}'"}
    return {"done": body, "status": r.status_code}


def _step(turn):
    out = _run(turn)
    _save("turn.json", turn)
    if "done" not in out:
        return out
    body = out["done"] if isinstance(out["done"], dict) else {}
    chat_ = _load("chat.json", {"session_id": turn["session_id"], "turns": []})
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    ran = [a for rnd in turn["rounds"] for a in rnd["agents"] if a]
    chat_["model"], chat_["python"] = body.get("model"), sys.version.split()[0]
    chat_["turns"].append({"role": "user", "content": turn["words"], "timestamp": now})
    chat_["turns"].append({"role": "assistant", "content": body.get("response", ""), "timestamp": now,
                           "agents": ran, "agent_logs": body.get("agent_logs", "")})
    _save("chat.json", chat_)
    _save("history.json", turn["history"] + [{"role": "user", "content": turn["words"]},
                                             {"role": "assistant", "content": body.get("response", "")}])
    (STATE / "turn.json").unlink()
    out["agents_that_ran"] = ran
    return out


def ask(words, model="host-ai"):
    """Start a turn. Returns what the model must answer, or the finished turn."""
    chat_ = _load("chat.json", None)
    return _step({"words": words, "answers": [], "rounds": [], "history": _load("history.json", []), "model": model,
                  "session_id": (chat_ or {}).get("session_id") or "host-" + time.strftime("%Y%m%d%H%M%S", time.gmtime())})


def answer(given):
    """Give the model's answer for the waiting turn: plain words, or a dict with tool_calls."""
    turn = _load("turn.json", None)
    if not turn:
        raise HostError('no turn is waiting; start one with: host ask "<what the person said>"')
    if isinstance(given, str):
        try:
            parsed = json.loads(given)
            given = parsed if isinstance(parsed, dict) else {"content": given}
        except ValueError:
            given = {"content": given}
    turn["answers"].append(given)
    return _step(turn)


def tools():
    kernel = _kernel()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        agents = kernel.load_agents()
    return [{"name": n, "description": a.metadata.get("description", "")} for n, a in sorted(agents.items())]


def chat():
    chat_ = _load("chat.json", {"turns": []})
    return {"turns": len([t for t in chat_["turns"] if t.get("role") == "user"]), "messages": len(chat_["turns"]),
            "agents_that_ran": sorted({a for t in chat_["turns"] for a in t.get("agents", [])}),
            "thawed_from": chat_.get("thawed_from"), "chat_file": str((STATE / "chat.json").resolve())}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd, rest = (argv[0] if argv else ""), argv[1:]
    try:
        if cmd == "thaw" and rest:
            out = thaw(rest[0])
        elif cmd == "freeze":
            with contextlib.redirect_stdout(io.StringIO()):
                out = freeze(rest[0] if rest else None)
        elif cmd == "export":
            out = export(rest[0] if rest else None)
        elif cmd == "tools":
            out = tools()
        elif cmd == "ask" and rest:
            out = ask(" ".join(rest), os.environ.get("BRAINFREEZE_HOST_MODEL", "host-ai"))
        elif cmd == "answer" and rest:
            out = answer(" ".join(rest))
        elif cmd == "chat":
            out = chat()
        else:
            print(__doc__)
            return 2
    except HostError as e:
        print(json.dumps({"status": "error", "error": str(e)}))
        return 1
    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
