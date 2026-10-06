"""Signatures and lineage for frozen brainstems.

A snapshot is signed by signing its bundle.json (which hashes every file) with an SSH key:
`ssh-keygen -Y sign -n brainfreeze`, the same keys GitHub publishes at https://github.com/<login>.keys. The signer
is named by a GitHub login (`bundle.signer`). A reader checks the signature against an allowed_signers file (its
own, or one a catalog ships) or, when there is none, against that login's keys on GitHub.

`find_updates` walks a catalog (a folder of snapshots and run files, or an index.json URL) for descendants of a
snapshot, following each bundle's `parent`, and `describe_change` says what changed between two of them.
Stdlib plus the ssh-keygen that ships with git and OpenSSH.
"""
import base64
import hashlib
import io
import json
import re
import subprocess
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

NAMESPACE = "brainfreeze"
LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")


class LineageError(Exception):
    pass


def _read_member(snapshot_bytes, name):
    with tarfile.open(fileobj=io.BytesIO(snapshot_bytes)) as t:
        try:
            f = t.extractfile(name)
        except KeyError:
            return None
        return f.read() if f else None


def snapshot_bytes(path):
    """The snapshot inside a .snapshot.tar.gz or a .brainstem.py run file."""
    path = Path(path)
    data = path.read_bytes()
    if path.suffix == ".py" or data[:2] == b"#!":
        text = data.decode("utf-8", "replace")
        start = text.index("PAYLOAD = (") + len("PAYLOAD = (")
        b64 = "".join(re.findall(r'"([A-Za-z0-9+/=]+)"', text[start:text.index("\n)", start)]))
        return zipfile.ZipFile(io.BytesIO(base64.b64decode(b64))).read("snapshot.tar.gz")
    return data


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def sign(snapshot, key, login, out=None):
    """Sign a snapshot's bundle.json with an SSH key as a GitHub login. Writes a new snapshot (default: in place)."""
    if not LOGIN.fullmatch(login or ""):
        raise LineageError(f"not a GitHub login: {login!r}")
    snapshot = Path(snapshot)
    src = snapshot.read_bytes()
    bundle = _read_member(src, "bundle.json")
    if bundle is None:
        raise LineageError("this snapshot has no bundle.json to sign; freeze it again with this brainfreeze")
    r = subprocess.run(["ssh-keygen", "-Y", "sign", "-f", str(Path(key).expanduser()), "-n", NAMESPACE, "-q"],
                       input=bundle, capture_output=True)
    if r.returncode != 0 or not r.stdout.startswith(b"-----BEGIN SSH SIGNATURE-----"):
        raise LineageError(f"ssh-keygen could not sign: {r.stderr.decode(errors='replace').strip()}")
    buf = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(src)) as t, tarfile.open(fileobj=buf, mode="w:gz") as o:
        for m in t.getmembers():
            if m.name in ("bundle.sig", "bundle.signer"):
                continue
            o.addfile(m, t.extractfile(m) if m.isfile() else None)
        for name, data in (("bundle.sig", r.stdout), ("bundle.signer", login.encode())):
            info = tarfile.TarInfo(name)
            info.size, info.mtime = len(data), m.mtime
            o.addfile(info, io.BytesIO(data))
    out = Path(out) if out else snapshot
    out.write_bytes(buf.getvalue())
    return out


def _allowed_from_github(login):
    try:
        with urllib.request.urlopen(f"https://github.com/{login}.keys", timeout=20) as r:
            keys = r.read().decode().split("\n")
    except OSError:
        return None
    return "".join(f'{login} namespaces="{NAMESPACE}" {k.strip()}\n' for k in keys if k.strip())


def check_signature(snap_bytes, allowed_signers=None):
    """('unsigned', None) | ('verified', login, source) | ('bad', login, why) | ('unknown', login, why)."""
    sig = _read_member(snap_bytes, "bundle.sig")
    if sig is None:
        return ("unsigned", None, "")
    login = (_read_member(snap_bytes, "bundle.signer") or b"").decode().strip()
    bundle = _read_member(snap_bytes, "bundle.json") or b""
    sources = []
    if allowed_signers and Path(allowed_signers).is_file():
        sources.append((Path(allowed_signers).read_text(), str(allowed_signers)))
    gh = _allowed_from_github(login) if LOGIN.fullmatch(login or "") else None
    if gh:
        sources.append((gh, f"github.com/{login}.keys"))
    if not sources:
        return ("unknown", login, f"no allowed_signers and no keys published at github.com/{login}.keys")
    last = ""
    for text, where in sources:
        with tempfile.TemporaryDirectory() as tmp:
            allowed, sigf = Path(tmp) / "allowed", Path(tmp) / "sig"
            allowed.write_text(text)
            sigf.write_bytes(sig)
            r = subprocess.run(["ssh-keygen", "-Y", "verify", "-f", str(allowed), "-I", login, "-n", NAMESPACE,
                                "-s", str(sigf)], input=bundle, capture_output=True)
        if r.returncode == 0:
            return ("verified", login, where)
        last = (r.stderr or r.stdout).decode(errors="replace").strip()
    if "no principal matched" in last.lower() or "not found" in last.lower() or "unknown" in last.lower():
        return ("unknown", login, f"signed with a key that is not {login}'s ({last})")
    return ("bad", login, last or "signature does not match bundle.json")


def _catalog_entries(catalog):
    """(name, snapshot bytes) for every snapshot or run file in a folder, or listed by an index.json URL."""
    if str(catalog).startswith("https://"):
        with urllib.request.urlopen(str(catalog), timeout=60) as r:
            index = json.load(r)
        for item in index.get("snapshots", []):
            with urllib.request.urlopen(item["url"], timeout=120) as r:
                data = r.read()
            if item.get("sha256") and sha256(data) != item["sha256"]:
                raise LineageError(f"{item['url']} does not match the SHA-256 the index lists")
            yield item["url"].rsplit("/", 1)[-1], data
        return
    for p in sorted(Path(catalog).expanduser().iterdir()):
        if p.name.endswith((".snapshot.tar.gz", ".brainstem.py")):
            try:
                yield p.name, snapshot_bytes(p)
            except (ValueError, KeyError, zipfile.BadZipFile):
                continue


def find_updates(own, catalog):
    """Descendants of the snapshot `own` (bytes) in a catalog, oldest first: [(name, bytes, depth)]."""
    by_parent = {}
    seen = {}
    for name, data in _catalog_entries(catalog):
        h = sha256(data)
        if h in seen:
            continue
        seen[h] = name
        b = _read_member(data, "bundle.json")
        parent = (json.loads(b).get("parent") or {}).get("snapshot_sha256") if b else None
        if parent:
            by_parent.setdefault(parent, []).append((name, data))
    out, frontier, depth = [], [sha256(own)], 1
    while frontier:
        nxt = []
        for h in frontier:
            for name, data in by_parent.get(h, []):
                out.append((name, data, depth))
                nxt.append(sha256(data))
        frontier, depth = nxt, depth + 1
    return out


def _memories(snap):
    count = 0
    with tarfile.open(fileobj=io.BytesIO(snap)) as t:
        for m in t.getmembers():
            if m.isfile() and "/.brainstem_data/" in f"/{m.name}" and m.name.endswith(".json"):
                try:
                    d = json.load(t.extractfile(m))
                    count += len(d) if isinstance(d, (dict, list)) else 0
                except ValueError:
                    pass
    return count


def describe_change(old, new):
    """What changed from snapshot `old` to `new` (bytes): a list of plain lines."""
    ob = json.loads(_read_member(old, "bundle.json") or b"{}")
    nb = json.loads(_read_member(new, "bundle.json") or b"{}")
    lines = []
    oa, na = ob.get("agents", {}), nb.get("agents", {})
    top = lambda files: {Path(f).name: h for f, h in files.items() if f.count("/") == 1 and f.endswith("_agent.py")}
    oa, na = top(oa), top(na)
    added = sorted(set(na) - set(oa))
    removed = sorted(set(oa) - set(na))
    changed = sorted(a for a in set(oa) & set(na) if oa[a] != na[a])
    if added:
        lines.append(f"agents added: {', '.join(added)}")
    if removed:
        lines.append(f"agents removed: {', '.join(removed)}")
    if changed:
        lines.append(f"agents changed: {', '.join(changed)}")
    if (ob.get("other", {}).get("soul.md") != nb.get("other", {}).get("soul.md")):
        lines.append("soul changed")
    om, nm = _memories(old), _memories(new)
    if om != nm:
        lines.append(f"memory: {om} -> {nm} entries")
    ov = (ob.get("kernel", {}).get("pin") or {}).get("version")
    nv = (nb.get("kernel", {}).get("pin") or {}).get("version")
    if ov != nv:
        lines.append(f"kernel: {ov} -> {nv}")
    osc = {s["name"] for s in ob.get("sidecars", [])}
    nsc = {s["name"] for s in nb.get("sidecars", [])}
    if osc != nsc:
        lines.append(f"sidecars: {', '.join(sorted(osc)) or '-'} -> {', '.join(sorted(nsc)) or '-'}")
    return lines or ["no change to agents, soul, memory, kernel or sidecars"]
