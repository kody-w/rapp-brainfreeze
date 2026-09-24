"""brainfreeze — freeze a running RAPP brainstem and resume it anywhere.

    from brainfreeze import freeze, pack, Throwaway

    snap = freeze("~/.brainstem/src/rapp_brainstem", "demo.snapshot.tar.gz", history=turns)
    pack(snap)                       # -> demo.brainstem.py: run it anywhere, it resumes

    with Throwaway.thaw("demo.snapshot.tar.gz") as bs:
        print(bs.chat("Where were we?").response)

A snapshot holds the brainstem's whole state: engine code as it ran, agents, soul,
memory, chosen model and the conversation. It never holds sign-in, secrets, .env
values, logs or caches. Thawing (or running a packed .py) needs only python3 and git:
it sets up Python packages and a one-time GitHub Copilot sign-in on first use.

Throwaways are the other half: disposable brainstems (grail, canary, a checkout, a
snapshot or a handoff kit) on their own port in ~/.brainfreeze/<name>,
beside the real one on :7071, which is never changed.

Standard library only.
"""
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from . import rapp1   # the RAPP reference implementation, vendored verbatim (see rapp1.vendor.json)

__all__ = ["Throwaway", "ChatResult", "ThrowawayError", "list_throwaways", "down", "replay", "freeze", "pack",
           "lay_egg"]
__version__ = "0.2.0"

ROOT = Path(os.getenv("BRAINFREEZE_ROOT", Path.home() / ".brainfreeze"))
REAL = Path.home() / ".brainstem" / "src" / "rapp_brainstem"
REAL_PY = Path.home() / ".brainstem" / "venv" / "bin" / "python"
HOLDS = Path.home() / "Documents" / "GitHub" / "rapp-tower" / "tools" / "holds.sh"
SOURCES = {
    "grail": "https://github.com/kody-w/rapp-installer.git",
    "canary": "https://github.com/kody-w/rapp-canary.git",
}
FIRST_PORT = 7097
SNAPSHOT_SUFFIX = ".snapshot.tar.gz"
# Never leaves the machine in a snapshot: sign-in, sessions, secrets, local settings, logs, caches.
_NEVER_FREEZE = {".copilot_token", ".copilot_session", ".copilot_pending", ".brainstem_secret", ".env",
                 ".brainstem_book.json", "__pycache__", ".git", ".pytest_cache", "venv", ".venv"}
TOKEN_CACHE = ROOT / ".copilot_token"      # sign-in saved by a throwaway on a machine with no brainstem
SHARED_VENV = ROOT / ".venv"
PUSH_BLOCK = "DISABLED-throwaway-is-read-only"
# Mirrors the web UI (index.html cappedHistory), so multi-turn tests see what a real user's session sends.
UI_HISTORY_MSGS = 40
UI_HISTORY_CHARS = 60000


class ThrowawayError(RuntimeError):
    pass


@dataclass
class ChatResult:
    response: str
    agent_logs: str
    session_id: str
    model: str
    raw: dict = field(repr=False)

    @property
    def agents_called(self):
        """Agent names that ran this turn, from the [Name] prefixes in agent_logs."""
        names = []
        for line in (self.agent_logs or "").splitlines():
            if line.startswith("[") and "]" in line:
                name = line[1:line.index("]")]
                if name not in names:
                    names.append(name)
        return names


def _port_busy(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _hold(*args):
    if HOLDS.exists():
        subprocess.run(["bash", str(HOLDS), *args], capture_output=True)


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (OSError, TypeError):
        return False


def _read(path, default=None):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return default


def _saved_token():
    """GITHUB_TOKEN, else the installed brainstem's sign-in, else one a throwaway saved earlier."""
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token:
        return token
    for f in (REAL / ".copilot_token", TOKEN_CACHE):
        try:
            token = json.loads(f.read_text()).get("access_token", "")
        except (OSError, ValueError):
            continue
        if token:
            return token
    return ""


class Throwaway:
    """A disposable brainstem. Use as a context manager, or call up() / down() yourself."""

    def __init__(self, source="grail", port=None, name=None, bare=False, agents=(),
                 env=None, keep=False, ui_history_cap=True, start_timeout=45,
                 ref=None, soul=None, sign_in=True):
        self.source = str(source)
        self.ref = ref
        self.soul = Path(soul).expanduser() if soul else None
        self.sign_in = sign_in
        self.commit = None
        self.port = port
        self.name = name
        self.bare = bare
        self.agents = [Path(a) for a in agents]
        self.env = dict(env or {})
        self.keep = keep
        self.ui_history_cap = ui_history_cap
        self.start_timeout = start_timeout
        self.session_id = None
        self.history = []
        self._pid = None

    # ── paths ────────────────────────────────────────────────────────────────
    @property
    def dir(self):
        return ROOT / self.name

    @property
    def brainstem_dir(self):
        return self.dir / "rapp_brainstem"

    @property
    def agents_dir(self):
        return self.brainstem_dir / "agents"

    @property
    def log_path(self):
        return self.dir / "server.log"

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    # ── lifecycle ────────────────────────────────────────────────────────────
    def up(self):
        ROOT.mkdir(parents=True, exist_ok=True)
        if self.port is None:
            self.port = FIRST_PORT
            while _port_busy(self.port) or (ROOT / f"tw-{self.port}").exists():
                self.port += 1
        elif _port_busy(self.port):
            raise ThrowawayError(f"port {self.port} is already in use")
        self.name = self.name or f"tw-{self.port}"
        if self.dir.exists():
            raise ThrowawayError(f"{self.dir} already exists; down('{self.name}') first")

        try:
            self._fetch_source()
            self._prepare_agents()
            self._start()
        except BaseException:
            self._teardown(quiet=True)
            raise
        return self

    def _fetch_source(self):
        if Path(self.source).expanduser().is_file():   # a snapshot, whatever it is named
            self._thaw_into(Path(self.source).expanduser())
        elif self.source in SOURCES:
            subprocess.run(["git", "clone", "-q", "--depth", "1", SOURCES[self.source], str(self.dir)],
                           check=True)
            subprocess.run(["git", "-C", str(self.dir), "remote", "set-url", "--push", "origin", PUSH_BLOCK],
                           check=True)
            if self.ref:
                fetched = subprocess.run(["git", "-C", str(self.dir), "fetch", "-q", "--depth", "1",
                                          "origin", self.ref], capture_output=True, text=True)
                if fetched.returncode != 0:
                    raise ThrowawayError(f"could not fetch {self.ref} from {self.source}: "
                                         f"{fetched.stderr.strip()}")
                subprocess.run(["git", "-C", str(self.dir), "checkout", "-q", "FETCH_HEAD"], check=True)
            self.commit = subprocess.run(["git", "-C", str(self.dir), "rev-parse", "HEAD"],
                                         capture_output=True, text=True).stdout.strip() or None
        else:
            src = Path(self.source).expanduser() / "rapp_brainstem"
            if not (src / "brainstem.py").exists():
                raise ThrowawayError(f"{self.source} has no rapp_brainstem/brainstem.py")
            self.dir.mkdir(parents=True)
            shutil.copytree(src, self.brainstem_dir,
                            ignore=shutil.ignore_patterns(".copilot_token", ".env", "__pycache__", "*.pyc"))
        if not (self.brainstem_dir / "brainstem.py").exists():
            raise ThrowawayError(f"no brainstem.py in {self.brainstem_dir}")
        (self.dir / "source").write_text(self.source + (f"@{self.commit[:12]}" if self.commit else ""))

    def _prepare_agents(self):
        if self.bare:
            parked = self.dir / "parked-agents"
            parked.mkdir(exist_ok=True)
            for f in self.agents_dir.glob("*_agent.py"):
                if f.name != "basic_agent.py":
                    f.rename(parked / f.name)
        if getattr(self, "_egg", None):
            self._apply_egg()
        for a in self.agents:
            self.add_agent(a)

    def _python(self):
        """The installed brainstem's env if there is one, else a venv shared by all throwaways."""
        if REAL_PY.exists():
            return str(REAL_PY)
        bindir = "Scripts" if os.name == "nt" else "bin"
        py = SHARED_VENV / bindir / ("python.exe" if os.name == "nt" else "python")
        if not py.exists():
            subprocess.run([sys.executable, "-m", "venv", str(SHARED_VENV)], check=True)
        subprocess.run([str(py), "-m", "pip", "install", "-q", "--disable-pip-version-check", "-r",
                        str(self.brainstem_dir / "requirements.txt")], check=True)
        return str(py)

    def _start(self):
        env = dict(os.environ)
        env.update({k: str(v) for k, v in self.env.items()})
        env["PORT"] = str(self.port)
        if self.soul:
            if not self.soul.is_file():
                raise ThrowawayError(f"soul file not found: {self.soul}")
            shutil.copy(self.soul, self.dir / "soul.md")
            env["SOUL_PATH"] = str(self.dir / "soul.md")
        token = _saved_token()
        if token:
            env["GITHUB_TOKEN"] = token
        _hold("claim", f"port:{self.port}", f"brainstem throwaway {self.name}")
        with open(self.log_path, "ab") as log:
            proc = subprocess.Popen([self._python(), "brainstem.py"], cwd=self.brainstem_dir, env=env,
                                    stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    start_new_session=True)
        self._pid = proc.pid
        (self.dir / "pid").write_text(str(proc.pid))
        (self.dir / "port").write_text(str(self.port))

        deadline = time.time() + self.start_timeout
        while time.time() < deadline:
            if proc.poll() is not None:
                break
            try:
                h = self.health()
            except ThrowawayError:
                time.sleep(0.5)
                continue
            if self.sign_in and h.get("copilot") != "\u2713":
                self._device_sign_in()
            return
        tail = "\n".join(_read(self.log_path, "").splitlines()[-20:])
        raise ThrowawayError(f"{self.name} did not answer on :{self.port}. Last log lines:\n{tail}")

    def _device_sign_in(self, timeout=900):
        """No saved sign-in on this machine: run GitHub's device login once and cache it."""
        start = self._request("POST", "/login", {}, timeout=30)
        if start.get("error"):
            raise ThrowawayError(f"could not start GitHub sign-in: {start['error']}")
        print(f"\nSign in to GitHub Copilot for this brainstem:\n"
              f"  1. Open {start.get('verification_uri')}\n"
              f"  2. Enter the code {start.get('user_code')}\n", file=sys.stderr, flush=True)
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(5)
            r = self._request("POST", "/login/poll", {}, timeout=30)
            if r.get("status") == "ok":
                saved = self.brainstem_dir / ".copilot_token"
                if saved.exists():
                    shutil.copy(saved, TOKEN_CACHE)
                    os.chmod(TOKEN_CACHE, 0o600)
                print("Signed in. The sign-in is saved for future throwaways.", file=sys.stderr)
                return
            if r.get("status") in ("expired", "error"):
                raise ThrowawayError(f"GitHub sign-in failed: {r.get('error') or r.get('status')}")
        raise ThrowawayError("GitHub sign-in timed out")

    def _thaw_into(self, snapshot):
        if not snapshot.is_file():
            raise ThrowawayError(f"snapshot not found: {snapshot}")
        self.dir.mkdir(parents=True)
        with tarfile.open(snapshot, "r:gz") as tar:
            for member in tar.getmembers():
                name = member.name
                if (name.startswith("/") or ".." in Path(name).parts or member.issym() or member.islnk()
                        or not (name == "state.json" or name.startswith("rapp_brainstem"))):
                    raise ThrowawayError(f"snapshot has an unsafe entry: {name}")
            tar.extractall(self.dir)
        state = json.loads((self.dir / "state.json").read_text())
        self.history = state.get("history", [])
        self.session_id = state.get("session_id")
        self.commit = state.get("brainstem", {}).get("commit")
        self.frozen = state
        self._save_conversation()

    @classmethod
    def hatch(cls, egg, session=None, **overrides):
        """Hatch a rapp/1 organism egg onto the engine it expects (never engine code from the egg).

        The egg is verified first (§9.3). A fresh instance identity is minted and `grown_from`
        records the egg's address (§9.4). A session egg, if given, restores its conversation."""
        blob = Path(egg).expanduser().read_bytes()
        ok, step, why = rapp1.verify_egg(blob)
        if not ok:
            raise ThrowawayError(f"egg failed verification at {step}: {why}")
        manifest, files = rapp1.read_egg(blob)
        if manifest["variant"] != "organism":
            raise ThrowawayError(f"expected an organism egg, got {manifest['variant']}")
        engine = (manifest.get("payload") or {}).get("engine") or {}
        known = engine.get("source") in SOURCES
        kw = dict(source=engine.get("source") if known else "grail",
                  ref=(engine.get("commit") or None) if known else None, bare=True)
        kw.update(overrides)
        tw = cls(**kw)
        tw._egg = (manifest, files)
        if session:
            sblob = Path(session).expanduser().read_bytes()
            ok, step, why = rapp1.verify_egg(sblob)
            if not ok:
                raise ThrowawayError(f"session egg failed verification at {step}: {why}")
            sman, _ = rapp1.read_egg(sblob)
            if sman["variant"] != "session":
                raise ThrowawayError(f"expected a session egg, got {sman['variant']}")
            tw._egg_history = [t for t in sman["payload"]["transcript"]
                               if t.get("role") in ("user", "assistant") and isinstance(t.get("content"), str)]
        return tw

    def _apply_egg(self):
        manifest, files = self._egg
        for path, octets in files.items():
            if path == "rappid.json":
                continue                      # the artifact identity; the instance gets its own below
            dest = self.brainstem_dir / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(octets)
        artifact = manifest["rappid"]
        parts = rapp1.rappid_parts(artifact)
        (self.dir / "instance.json").write_text(json.dumps({
            "rappid": rapp1.mint_rappid(parts["owner"], parts["slug"]),     # §9.4: fresh, from entropy
            "artifact": artifact,
            "grown_from": rapp1.egg_address(manifest),
        }, indent=2))
        (self.dir / "source").write_text(f"egg {artifact}")
        if getattr(self, "_egg_history", None):
            self.history = list(self._egg_history)
            self._save_conversation()

    @classmethod
    def thaw(cls, snapshot, **overrides):
        """Resume a frozen brainstem: same engine, agents, soul, memory, model and conversation."""
        return cls(source=str(Path(snapshot).expanduser()), **overrides)

    def write_browser_transcript(self, path=None):
        """Write the conversation in the web UI's Import format, so the browser can resume it."""
        path = Path(path) if path else self.dir / "conversation-for-browser.json"
        path.write_text(json.dumps({
            "session_id": self.session_id, "model": self.health().get("model"),
            "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "turns": [m for m in self.history if m.get("role") in ("user", "assistant")],
        }, indent=2))
        return path

    def freeze(self, out=None):
        """Snapshot this running throwaway, conversation included."""
        out = out or Path.cwd() / f"{self.name}-{time.strftime('%Y%m%d-%H%M%S')}{SNAPSHOT_SUFFIX}"
        return freeze(self.brainstem_dir, out, history=self.history, session_id=self.session_id,
                      model=self.health().get("model"))

    @classmethod
    def from_kit(cls, kit, **overrides):
        """A throwaway that matches a demo kit: same brainstem commit, soul and agents, nothing else."""
        kit = Path(kit).expanduser()
        m = json.loads((kit / "manifest.json").read_text())
        b = m.get("brainstem", {})
        kw = dict(source=b.get("source") if b.get("source") in SOURCES else "grail",
                  ref=b.get("commit"), bare=True,
                  agents=sorted((kit / "agents").glob("*.py")),
                  soul=kit / "soul.md" if (kit / "soul.md").exists() else None)
        kw.update(overrides)
        return cls(**kw)

    def down(self):
        self._teardown()

    def _teardown(self, quiet=False):
        pid = self._pid or (int(_read(self.dir / "pid", "0") or 0) if self.name else 0)
        if pid and _alive(pid):
            try:
                getattr(os, "killpg", os.kill)(pid, signal.SIGTERM)
            except OSError:
                os.kill(pid, signal.SIGTERM)
            for _ in range(10):
                if not _alive(pid):
                    break
                time.sleep(0.5)
            if _alive(pid):
                try:
                    getattr(os, "killpg", os.kill)(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
                except OSError:
                    pass
        if self.port:
            _hold("release", f"port:{self.port}")
        if self.name and self.dir.exists() and self.dir.parent == ROOT:
            shutil.rmtree(self.dir)
        self._pid = None

    def __enter__(self):
        return self.up()

    def __exit__(self, *exc):
        if not self.keep:
            self.down()

    @classmethod
    def attach(cls, name):
        """Reconnect to a throwaway that is already running (e.g. from another script)."""
        d = ROOT / name
        if not d.exists():
            raise ThrowawayError(f"no throwaway named {name}")
        tw = cls(source=_read(d / "source", "?"), port=int(_read(d / "port")), name=name, keep=True)
        tw._pid = int(_read(d / "pid", "0") or 0)
        try:
            conv = json.loads((d / "conversation.json").read_text())
            tw.session_id, tw.history = conv.get("session_id"), conv.get("history", [])
        except (OSError, ValueError):
            pass
        return tw

    # ── using it ─────────────────────────────────────────────────────────────
    def _request(self, method, path, body=None, timeout=300):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode())
            except ValueError:
                raise ThrowawayError(f"{method} {path} returned {e.code}")
        except (urllib.error.URLError, OSError) as e:
            raise ThrowawayError(f"{self.url} is not answering: {e}")

    def health(self):
        return self._request("GET", "/health", timeout=3)

    def add_agent(self, path):
        """Drop an agent file in. The brainstem picks it up on the next request."""
        path = Path(path).expanduser()
        if not path.is_file():
            raise ThrowawayError(f"agent file not found: {path}")
        shutil.copy(path, self.agents_dir / path.name)
        return self.agents_dir / path.name

    def remove_agent(self, filename):
        (self.agents_dir / Path(filename).name).unlink(missing_ok=True)

    def _sendable_history(self):
        if not self.ui_history_cap:
            return list(self.history)
        msgs = self.history[-UI_HISTORY_MSGS:]
        total = sum(len(m["content"]) for m in msgs)
        while len(msgs) > 1 and total > UI_HISTORY_CHARS:
            total -= len(msgs[0]["content"])
            msgs = msgs[1:]
        return msgs

    def chat(self, message, timeout=300):
        """Send one message, continuing this object's session like the web UI does."""
        body = {"user_input": message, "conversation_history": self._sendable_history()}
        if self.session_id:
            body["session_id"] = self.session_id
        raw = self._request("POST", "/chat", body, timeout=timeout)
        if "error" in raw and "response" not in raw:
            raise ThrowawayError(f"chat failed: {raw['error']}")
        self.session_id = raw.get("session_id", self.session_id)
        reply = raw.get("response", "")
        self.history += [{"role": "user", "content": message}, {"role": "assistant", "content": reply}]
        self._save_conversation()
        return ChatResult(reply, raw.get("agent_logs", ""), self.session_id or "", raw.get("model", ""), raw)

    def new_session(self):
        self.session_id, self.history = None, []
        self._save_conversation()

    def _save_conversation(self):
        """Keep the conversation beside the throwaway so attach() and the CLI can continue it."""
        if self.name and self.dir.exists():
            (self.dir / "conversation.json").write_text(
                json.dumps({"session_id": self.session_id, "history": self.history}, indent=2))

    def log(self, lines=40):
        return "\n".join(_read(self.log_path, "").splitlines()[-lines:])

    def __repr__(self):
        return f"<Throwaway {self.name} {self.url} source={self.source}>"


def list_throwaways():
    rows = []
    if ROOT.exists():
        for d in sorted(p for p in ROOT.iterdir() if p.is_dir() and (p / "port").exists()):
            pid = int(_read(d / "pid", "0") or 0)
            rows.append({"name": d.name, "port": _read(d / "port", "?"),
                         "running": bool(pid) and _alive(pid), "source": _read(d / "source", "?"),
                         "dir": str(d)})
    return rows


def down(name):
    """Stop and delete one throwaway by name, or every one with name='all'."""
    names = [r["name"] for r in list_throwaways()] if name == "all" else [name]
    removed = []
    for n in names:
        Throwaway.attach(n).down()
        removed.append(n)
    return removed


def replay(kit, out=None, **overrides):
    """Rebuild the demo from a handoff kit, resend the user's messages, and write a side-by-side report.

    Returns the path of the report. Whoever builds the end product can send the same
    messages to it and compare.
    """
    kit = Path(kit).expanduser()
    m = json.loads((kit / "manifest.json").read_text())
    turns = json.loads((kit / "transcript.json").read_text())
    original = {}
    pending = None
    rows = []
    for t in turns:
        if t["role"] == "user":
            pending = {"user": t["content"], "original": ""}
            rows.append(pending)
        elif pending is not None:
            pending["original"] = t["content"]
    with Throwaway.from_kit(kit, **overrides) as bs:
        commit = bs.commit
        for row in rows:
            r = bs.chat(row["user"])
            row["replay"], row["agents_called"] = r.response, r.agents_called
        loaded = bs.health().get("agents", [])
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = Path(out) if out else kit / "replays" / f"replay-{stamp}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# Replay: {m.get('customer', '')} — {m.get('use_case', '')}", "",
             f"- **Replayed:** {time.strftime('%Y-%m-%d %H:%M')}",
             f"- **Brainstem:** {m.get('brainstem', {}).get('version', '?')} at commit {(commit or '?')[:12]}",
             f"- **Agents loaded:** {', '.join(loaded) or 'none'}",
             f"- **Messages replayed:** {len(rows)}", "",
             "Model answers vary run to run; compare what each answer does and which agents ran, "
             "not the exact wording.", ""]
    for i, row in enumerate(rows, 1):
        lines += [f"## Turn {i}", "", "**User:**", "", row["user"], "",
                  "**Original demo:**", "", row["original"] or "_(no reply recorded)_", "",
                  f"**Replay** (agents: {', '.join(row['agents_called']) or 'none'}):", "",
                  row["replay"], ""]
    out.write_text("\n".join(lines))
    (out.with_suffix(".json")).write_text(json.dumps(rows, indent=2))
    return out


def _git_out(cwd, *args):
    try:
        r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def freeze(brainstem_dir, out, history=(), session_id=None, model=None, extra=None,
           include_memory=True, exclude=()):
    """Snapshot a brainstem's full state into one file that thaw() resumes anywhere.

    Captures the engine code as it is (local changes included), agents, soul, memory
    (.brainstem_data), chosen model and the conversation. Never captures sign-in,
    secrets, .env, logs or caches; .env setting NAMES are recorded so the receiver
    knows what to supply.
    """
    src = Path(brainstem_dir).expanduser().resolve()
    if not (src / "brainstem.py").exists():
        raise ThrowawayError(f"no brainstem.py in {src}")
    out = Path(out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)

    settings = []
    env_file = src / ".env"
    if env_file.exists():
        for line in env_file.read_text(errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                settings.append(line.split("=", 1)[0].replace("export ", "").strip())
    version = _read(src / "VERSION")
    state = {
        "kind": "brainstem-snapshot", "snapshot_version": 1,
        "created": time.strftime("%Y-%m-%d %H:%M %Z"),
        "brainstem": {"version": version, "commit": _git_out(src, "rev-parse", "HEAD") or None,
                      "engine_modified": bool(_git_out(src, "status", "--porcelain", "--", "brainstem.py"))},
        "model": model or _read(src / ".brainstem_model"),
        "session_id": session_id,
        "history": [m for m in history if isinstance(m, dict) and m.get("role") in ("user", "assistant", "tool")],
        "settings_needed": settings,
        "left_out": sorted(_NEVER_FREEZE) + ([] if include_memory else [".brainstem_data"]),
        "memory_included": include_memory,
    }
    state.update(extra or {})

    skip = set(_NEVER_FREEZE) | ({".brainstem_data"} if not include_memory else set())
    # Folders inside the brainstem to leave out (e.g. an output folder being written right now).
    skip_paths = set()
    for e in exclude:
        try:
            skip_paths.add(Path("rapp_brainstem") / Path(e).expanduser().resolve().relative_to(src))
        except ValueError:
            pass

    def _keep(info):
        parts = Path(info.name).parts
        if any(Path(*parts[:i]) in skip_paths for i in range(1, len(parts) + 1)):
            return None
        if any(p in skip or p.endswith((".tmp", ".pyc", ".partial")) for p in parts):
            return None
        if info.issym() or info.islnk():
            return None
        return info

    tmp = out.with_name(out.name + ".partial")
    with tarfile.open(tmp, "w:gz") as tar:
        tar.add(src, arcname="rapp_brainstem", filter=_keep)
        data = json.dumps(state, indent=2).encode()
        info = tarfile.TarInfo("state.json")
        info.size, info.mtime = len(data), int(time.time())
        import io
        tar.addfile(info, io.BytesIO(data))
    os.replace(tmp, out)
    return out


_BOOTSTRAP = """#!/usr/bin/env python3
# {title}
#
# A frozen RAPP brainstem: engine, agents, soul, memory and conversation, in one file.
# Run it anywhere with Python 3.9+ and git:
#
#     python3 {filename}                 start it, open the browser, keep it running
#     python3 {filename} --chat          continue the conversation right here in the terminal
#     python3 {filename} --port 7200     pick the port
#
# The first run on a machine with no brainstem sets up Python packages and asks you to
# sign in to GitHub Copilot once. Sign-in and secrets are never inside this file.
# Frozen: {created} | brainstem {version} | {n_msgs} conversation messages | settings: {settings}
import argparse, base64, hashlib, io, os, sys, time, webbrowser, zipfile
from pathlib import Path

PAYLOAD = (
{payload}
)


def main():
    ap = argparse.ArgumentParser(description="Resume a frozen RAPP brainstem.")
    ap.add_argument("--port", type=int)
    ap.add_argument("--chat", action="store_true", help="continue the conversation in this terminal")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--keep", action="store_true", help="leave it running when this script exits")
    ap.add_argument("--env", action="append", default=[], help="KEY=VALUE for agents that need settings")
    a = ap.parse_args()

    blob = base64.b64decode("".join(PAYLOAD))
    home = Path(os.getenv("BRAINFREEZE_ROOT", Path.home() / ".brainfreeze"))
    unpacked = home / ".bootstrap" / hashlib.sha256(blob).hexdigest()[:16]
    if not (unpacked / "snapshot.tar.gz").exists():
        unpacked.mkdir(parents=True, exist_ok=True)
        zipfile.ZipFile(io.BytesIO(blob)).extractall(unpacked)
    sys.path.insert(0, str(unpacked))
    from brainfreeze import Throwaway, ThrowawayError

    env = dict(kv.split("=", 1) for kv in a.env)
    print("Starting the brainstem...", flush=True)
    try:
        bs = Throwaway.thaw(unpacked / "snapshot.tar.gz", port=a.port, env=env, keep=True).up()
    except ThrowawayError as e:
        sys.exit(f"Could not start: {{e}}")
    convo = bs.write_browser_transcript(Path.cwd() / "{stem}-conversation.json")
    missing = [k for k in {settings_list!r} if k not in env and not os.getenv(k)]
    print(f"\\nRunning at {{bs.url}}  (brainstem {{bs.health().get('version', '?')}}, "
          f"agents: {{', '.join(bs.health().get('agents', [])) or 'none'}})")
    print(f"Conversation: {{len(bs.history)}} messages restored.")
    print(f"  In the browser: click Import and choose {{convo}}")
    if missing:
        print(f"  Agents may need these settings: {{', '.join(missing)}} (pass --env NAME=value)")
    if not a.no_browser:
        webbrowser.open(bs.url)
    try:
        if a.chat:
            print("\\nContinue the conversation. Ctrl-C to stop.\\n")
            while True:
                msg = input("you > ").strip()
                if msg:
                    r = bs.chat(msg)
                    print(f"\\nbrainstem > {{r.response}}\\n")
                    if r.agent_logs:
                        print(f"  (agents: {{', '.join(r.agents_called)}})\\n")
        else:
            print("\\nPress Ctrl-C to stop.")
            while True:
                time.sleep(3600)
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        if a.keep:
            print(f"\\nStill running at {{bs.url}} as {{bs.name}}.")
        else:
            bs.down()
            print("\\nStopped and cleaned up.")


if __name__ == "__main__":
    main()
"""


def pack(snapshot, out=None, title=None):
    """Turn a snapshot into one self-bootstrapping .py file: run it and the brainstem resumes."""
    import base64
    import io
    import zipfile
    snapshot = Path(snapshot).expanduser()
    with tarfile.open(snapshot) as t:
        state = json.load(t.extractfile("state.json"))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(snapshot, "snapshot.tar.gz")
        pkg = Path(__file__).parent
        for f in sorted(pkg.glob("*.py")):
            z.write(f, f"brainfreeze/{f.name}")
    b64 = base64.b64encode(buf.getvalue()).decode()
    stem = snapshot.name[:-len(SNAPSHOT_SUFFIX)] if snapshot.name.endswith(SNAPSHOT_SUFFIX) else snapshot.stem
    out = Path(out) if out else snapshot.with_name(stem + ".brainstem.py")
    settings = state.get("settings_needed", [])
    text = _BOOTSTRAP.format(
        title=title or f"Frozen brainstem: {stem}", filename=out.name, stem=stem,
        created=state.get("created", "?"), version=state.get("brainstem", {}).get("version", "?"),
        n_msgs=len(state.get("history", [])), settings=", ".join(settings) or "none",
        settings_list=settings,
        payload="\n".join(f'    "{b64[i:i + 100]}"' for i in range(0, len(b64), 100)))
    out.write_text(text)
    os.chmod(out, 0o755)
    return out


def _utc_ms():
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())


def _walk(base, prefix, skip):
    """{posix path: octets} for every file under base, leaving out skipped names and invalid paths."""
    files, left_out = {}, []
    if not base.is_dir():
        return files, left_out
    for root, dirs, names in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d not in skip)
        for n in sorted(names):
            if n in skip or n.endswith((".pyc", ".tmp", ".partial")):
                continue
            full = Path(root) / n
            if full.is_symlink():
                continue
            rel = f"{prefix}/{full.relative_to(base).as_posix()}"
            if rapp1._path_valid(rel):
                files[rel] = full.read_bytes()
            else:
                left_out.append(rel)
    return files, left_out


def lay_egg(brainstem_dir, out_dir=".", owner=None, slug=None, rappid=None, include_memory=True,
            history=None, created_utc=None):
    """Lay a rapp/1 `organism` egg from a brainstem (plus a `session` egg for the conversation).

    The egg carries agents, soul, memory and the engine version it expects, and never the
    engine code itself: it hatches onto the receiver's engine. Every egg is verified with
    the reference implementation before it is written. Returns a dict of what was laid.
    """
    src = Path(brainstem_dir).expanduser().resolve()
    if not (src / "soul.md").is_file():
        raise ThrowawayError(f"no soul.md in {src}")
    if rappid:
        if not rapp1.rappid_valid(rappid):
            raise ThrowawayError(f"not a valid rappid: {rappid}")
    else:
        if not owner or not slug:
            raise ThrowawayError("owner and slug are required to mint a new rappid")
        try:
            rappid = rapp1.mint_rappid(owner.lower(), slug)
        except ValueError as e:
            raise ThrowawayError(f"{e}: owner is your lowercase GitHub login, slug is lowercase-with-hyphens")
    parts = rapp1.rappid_parts(rappid)
    utc = created_utc or _utc_ms()

    skip = set(_NEVER_FREEZE)
    files = {"rappid.json": rapp1.canonical({"schema": "rapp/1", "rappid": rappid}).encode("utf-8"),
             "soul.md": (src / "soul.md").read_bytes()}
    agent_files, left_out = _walk(src / "agents", "agents", skip)
    files.update(agent_files)
    if include_memory:
        mem, more = _walk(src / ".brainstem_data", ".brainstem_data", skip)
        files.update(mem)
        left_out += more
        if (src / ".brainstem_model").is_file():
            files[".brainstem_model"] = (src / ".brainstem_model").read_bytes()

    remote = _git_out(src, "remote", "get-url", "origin")
    source = "grail" if "rapp-installer" in remote else "canary" if "rapp-canary" in remote else "other"
    settings = []
    if (src / ".env").exists():
        for line in (src / ".env").read_text(errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                settings.append(line.split("=", 1)[0].replace("export ", "").strip())
    payload = {
        "engine": {"name": "rapp-brainstem", "version": _read(src / "VERSION") or "",
                   "source": source,
                   "commit": (_git_out(src, "rev-parse", "HEAD") if source != "other" else "") or ""},
        "memory_included": bool(include_memory),
        "settings_needed": sorted(set(settings)),
        "made_with": f"brainfreeze/{__version__}",
    }
    blob = rapp1.pack_egg("organism", rappid, utc, files=files, payload=payload)
    ok, step, why = rapp1.verify_egg(blob)
    if not ok:
        raise ThrowawayError(f"laid an egg that fails verification at {step}: {why}")
    out_dir = Path(out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    organism = out_dir / f"{parts['owner']}--{parts['slug']}.egg"
    organism.write_bytes(blob)
    manifest, _ = rapp1.read_egg(blob)
    laid = {"organism": organism, "rappid": rappid, "address": rapp1.egg_address(manifest),
            "files": len(files), "left_out": left_out, "session": None}

    turns = [{"role": m["role"], "content": m["content"]} for m in (history or [])
             if isinstance(m, dict) and m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)]
    if turns:
        sblob = rapp1.pack_egg("session", rappid, utc, payload={
            "runtime": f"rapp-brainstem/{payload['engine']['version'] or 'unknown'}", "transcript": turns})
        ok, step, why = rapp1.verify_egg(sblob)
        if not ok:
            raise ThrowawayError(f"laid a session egg that fails verification at {step}: {why}")
        laid["session"] = out_dir / f"{parts['owner']}--{parts['slug']}.session.egg"
        laid["session"].write_bytes(sblob)
    return laid
