"""bundle.json: every part of a frozen brainstem by SHA-256, the kernel pin, and the sidecars around it.

See docs/BUNDLE.md. Stdlib only. `Collector` gathers hashes while a snapshot is written; `verify` checks a thawed
folder against its bundle before anything starts; `sidecar_entry` describes a sidecar folder; `start_sidecar`
brings one up beside a running kernel.
"""
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

SCHEMA = "brainfreeze-bundle/1"
PIN_SCHEMA = "ai-brainstem-kernel-pin/1"
SIDECAR_SKIP = {"__pycache__", ".git", ".venv", "venv", "node_modules", ".pytest_cache", "tests"}
NOT_KERNEL_DIRS = ("agents/", "organs/", ".brainstem_data/")
NOT_KERNEL_FILES = {"soul.md", ".brainstem_model", "kernel.json"}


class BundleError(Exception):
    pass


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _git(cwd, *args):
    try:
        r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


class Collector:
    """Hashes of the brainstem files a snapshot ships, sorted into kernel, agents, organs and other."""

    def __init__(self, src):
        self.src = Path(src)
        self.files = {}

    def add(self, rel, path):
        self.files[rel] = sha256_file(path)

    def bundle(self, sidecars=(), parent=None):
        pin_file = self.src / "kernel.json"
        if pin_file.is_file():
            pin = json.loads(pin_file.read_text())
            pinned_by = "kernel.json"
            changed = [f for f, want in (pin.get("files") or {}).items() if self.files.get(f) != want]
            if changed:
                raise BundleError("this distro's kernel differs from its kernel.json pin ("
                                  + ", ".join(changed[:5]) + "); a distro pins the unchanged kernel, so it is not frozen")
        else:
            pinned_by = "freeze"
            pin = {"schema": PIN_SCHEMA, "source": "", "commit": _git(self.src, "rev-parse", "HEAD"),
                   "version": (self.src / "VERSION").read_text().strip() if (self.src / "VERSION").is_file() else "",
                   "files": {f: h for f, h in sorted(self.files.items())
                             if not f.startswith(NOT_KERNEL_DIRS) and f not in NOT_KERNEL_FILES}}
            remote = _git(self.src, "remote", "get-url", "origin")
            pin["source"] = remote
        kernel_files = set(pin.get("files") or {})
        out = {"schema": SCHEMA,
               "kernel": {"pin": pin, "pinned_by": pinned_by},
               "agents": {f: h for f, h in sorted(self.files.items()) if f.startswith("agents/")},
               "organs": {f: h for f, h in sorted(self.files.items()) if f.startswith("organs/")},
               "other": {f: h for f, h in sorted(self.files.items())
                         if f not in kernel_files and not f.startswith(("agents/", "organs/"))},
               "sidecars": list(sidecars)}
        if parent:
            out["parent"] = parent
        return out


def sidecar_entry(folder, name=None):
    """Describe a sidecar folder: its sidecar.json if it has one, else the launch.py convention
    (`python3 launch.py --http <port>`, BRAINSTEM_URL), which is how kody-w/brainstem-mcp runs as a service."""
    folder = Path(folder).expanduser().resolve()
    if (folder / "plugins").is_dir() and not (folder / "launch.py").exists():
        inner = [p for p in (folder / "plugins").iterdir() if (p / "launch.py").exists()]
        if len(inner) == 1:                      # a plugin repo: the sidecar is its one plugin folder
            return sidecar_entry(inner[0], name or folder.name)
    spec = {}
    if (folder / "sidecar.json").is_file():
        spec = json.loads((folder / "sidecar.json").read_text())
    elif (folder / "launch.py").is_file():
        spec = {"kind": "service", "run": ["{python}", "launch.py", "--http", "{port}"],
                "env": {"BRAINSTEM_URL": "{kernel_url}"}, "ready": {"tcp": "{port}"}}
    else:
        raise BundleError(f"{folder} has no sidecar.json or launch.py, so there is no way to start it")
    top = _git(folder, "rev-parse", "--show-toplevel")
    sub = str(folder.relative_to(top)) if top else ""
    entry = {"name": name or spec.get("name") or folder.name,
             "from": {"repo": _git(folder, "remote", "get-url", "origin"), "commit": _git(folder, "rev-parse", "HEAD"),
                      "path": "" if sub == "." else sub},
             "kind": spec.get("kind", "service"), "run": spec["run"], "env": spec.get("env", {}),
             "ready": spec.get("ready", {})}
    entry["path"] = f"sidecars/{entry['name']}"
    files = {}
    for p in sorted(folder.rglob("*")):
        rel = p.relative_to(folder)
        if p.is_file() and not p.is_symlink() and not (set(rel.parts) & SIDECAR_SKIP) and p.suffix != ".pyc":
            files[str(rel)] = sha256_file(p)
    entry["files"] = files
    return entry, folder


def verify(root, bundle):
    """Check a thawed snapshot folder against its bundle. Raises BundleError naming the first mismatch."""
    root = Path(root)
    bs = root / "rapp_brainstem"
    groups = [("kernel", bundle["kernel"]["pin"].get("files") or {}, bs), ("agent", bundle.get("agents", {}), bs),
              ("organ", bundle.get("organs", {}), bs), ("file", bundle.get("other", {}), bs)]
    for sc in bundle.get("sidecars", []):
        groups.append((f"sidecar {sc['name']}", sc.get("files", {}), root / sc["path"]))
    for what, files, base in groups:
        for rel, want in files.items():
            p = base / rel
            if not p.is_file():
                raise BundleError(f"{what} file {rel} is missing")
            got = sha256_file(p)
            if got != want:
                pinned = " (pinned by kernel.json)" if what == "kernel" and bundle["kernel"]["pinned_by"] == "kernel.json" else ""
                raise BundleError(f"{what} file {rel} has been changed{pinned}: expected {want[:16]}, found {got[:16]}")


def _fill(value, ctx):
    return value.format(**ctx) if isinstance(value, str) else value


def _tcp_ready(port, deadline):
    while time.time() < deadline:
        with socket.socket() as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", int(port))) == 0:
                return True
        time.sleep(0.5)
    return False


def start_sidecar(entry, folder, port, kernel_url, log_path, timeout=300):
    """Start a service sidecar beside the kernel; returns its pid once it is ready."""
    ctx = {"python": sys.executable, "port": str(port), "kernel_url": kernel_url}
    argv = [_fill(a, ctx) for a in entry["run"]]
    env = dict(os.environ)
    env.update({k: _fill(v, ctx) for k, v in (entry.get("env") or {}).items()})
    with open(log_path, "ab") as log:
        proc = subprocess.Popen(argv, cwd=folder, env=env, stdout=log, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True)
    ready = entry.get("ready") or {}
    if "tcp" in ready and not _tcp_ready(_fill(ready["tcp"], ctx), time.time() + timeout):
        proc.kill()
        tail = "\n".join(Path(log_path).read_text(errors="replace").splitlines()[-15:])
        raise BundleError(f"sidecar {entry['name']} did not open port {port}. Last log lines:\n{tail}")
    if proc.poll() is not None:
        raise BundleError(f"sidecar {entry['name']} exited at once (code {proc.returncode})")
    return proc.pid
