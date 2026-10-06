"""Older egg formats: say what a file is, and turn the brainstem-shaped ones into rapp/1 organism eggs.

Eggs were laid by several tools before rapp/1 (brainstem-egg/2.x twins, rapp-egg/1.0 rapplications,
rapp-application/1.0 twins). Those carry a soul and agents, so they convert. Other files share the .egg
extension but are not brainstems at all (hologram cartridges, Rappter moments and genomes, neighborhood
invites, Claude Code agent bundles); for those `identify` says what they are instead of failing to parse.
Stdlib only.
"""
import base64
import io
import json
import re
import tempfile
import uuid
import zipfile
from pathlib import Path

from . import rapp1

AGENT_SUFFIX = "_agent.py"


def _zip_manifest(z):
    names = z.namelist()
    if "manifest.json" not in names:
        return None
    try:
        return json.loads(z.read("manifest.json"))
    except ValueError:
        return None


def identify(blob):
    """What an egg file is: {format, what, convertible, hint}."""
    try:
        ok, _, _ = rapp1.verify_egg(blob)
    except Exception:
        ok = False
    if ok:
        variant = rapp1.read_egg(blob)[0]["variant"]
        return {"format": f"rapp/1 {variant}", "what": f"a rapp/1 {variant} egg", "convertible": False,
                "hint": "already rapp/1: hatch it with  brainfreeze up --egg <file>"}
    if blob[:2] == b"PK":
        try:
            z = zipfile.ZipFile(io.BytesIO(blob))
        except zipfile.BadZipFile:
            return _unknown("a damaged ZIP file")
        m = _zip_manifest(z) or {}
        schema = str(m.get("schema", ""))
        names = z.namelist()
        if schema.startswith("brainstem-egg/2"):
            ok = "repo/soul.md" in names or bool(_v2_agents(names))
            return {"format": schema, "what": f"a {m.get('type', 'twin')} egg from the older brainstem exporter",
                    "convertible": ok,
                    "hint": "convert it:  brainfreeze egg-upgrade <file>" if ok else "it carries no soul or agents"}
        if schema == "rapp-egg/1.0" and m.get("type") == "rapplication":
            return {"format": schema, "what": f"a rapplication egg ({m.get('id')}: one agent plus a web UI)",
                    "convertible": "agent.py" in names,
                    "hint": "convert it:  brainfreeze egg-upgrade <file>  (the agent comes across; the UI does "
                            "not, so for the full app use  brainfreeze-studio rapplication)"}
        if schema == "rapp-application/1.0":
            twin = m.get("twin") or {}
            ok = m.get("runtime") == "twin" and twin.get("soul") in names
            return {"format": schema, "what": f"a RAPP application twin ({m.get('name') or m.get('id')})",
                    "convertible": ok, "hint": "convert it:  brainfreeze egg-upgrade <file>" if ok
                    else "it does not describe a twin with a soul"}
        if isinstance(m.get("contents"), list) and any(n.startswith(".claude/") for n in names):
            return {"format": "claude-agent-bundle", "what": "a bundle of Claude Code agents (.claude/agents/*.md)",
                    "convertible": False, "hint": "not a brainstem: unzip it into a project for Claude Code"}
        return _unknown(f"a ZIP file{' with schema ' + schema if schema else ''} that is not a known egg")
    text = blob.strip()
    if text[:3] == b"eyJ":                                   # base64 of a JSON object
        try:
            text = base64.b64decode(text + b"=" * (-len(text) % 4))
        except ValueError:
            pass
    try:
        d = json.loads(text)
    except ValueError:
        return _unknown("not a ZIP or JSON file")
    if not isinstance(d, dict):
        return _unknown("a JSON file that is not an egg")
    schema = str(d.get("schema", ""))
    if schema.startswith("hologram-cartridge"):
        return {"format": schema, "what": f"a hologram cartridge ({d.get('title', 'a creature')}), not a brainstem",
                "convertible": False, "hint": "open it in the hologram viewer it came from"}
    if "neighborhood" in schema:
        return {"format": schema, "what": "a neighborhood invite: a link to someone's twin, with no agents in it",
                "convertible": False, "hint": "open its neighborhood_url instead"}
    if "moment" in d and "format" in d:
        return {"format": str(d.get("format")), "what": "a Rappter moment export, not a brainstem",
                "convertible": False, "hint": "open it in Rappter"}
    if "genome" in d:
        return {"format": "rappter-genome", "what": "a Rappter creature genome, not a brainstem",
                "convertible": False, "hint": "open it in Rappter"}
    return _unknown(f"a JSON file{' with schema ' + schema if schema else ''} that is not a known egg")


V2_AGENT_DIRS = ("repo/agents/", "agents/", "cubby/agents/")


def _v2_agent_name(n):
    """The file name of an agent at the top of one of the older layouts' agent folders, else None."""
    for d in V2_AGENT_DIRS:
        if n.startswith(d) and n.endswith(AGENT_SUFFIX) and "/" not in n[len(d):]:
            return n[len(d):]
    return None


def _v2_agents(names):
    return [n for n in names if _v2_agent_name(n)]


def _written_soul(name):
    return (f"You are {name}, a RAPP brainstem. Use your agents whenever the request fits them, "
            "and say plainly when it does not.\n").encode()


def _unknown(what):
    return {"format": "unknown", "what": what, "convertible": False, "hint": "not something brainfreeze can hatch"}


def _memory(raw):
    """Older memory files ({"facts": [...]}) as the brainstem's shared memory; current ones pass through."""
    try:
        d = json.loads(raw)
    except ValueError:
        return None
    if isinstance(d, dict) and isinstance(d.get("facts"), list):
        return json.dumps({str(uuid.uuid5(uuid.NAMESPACE_URL, f)): {
            "conversation_id": "current", "session_id": "current", "message": f, "mood": "neutral",
            "theme": "fact", "importance": 3, "tags": [], "date": "", "time": ""}
            for f in d["facts"] if isinstance(f, str)}, indent=2).encode()
    if isinstance(d, dict) and all(isinstance(v, dict) and "message" in v for v in d.values()):
        return raw
    return None


def extract(blob, dest):
    """Lay a convertible egg out as a brainstem folder (soul.md, agents/, memory). Returns (folder, notes)."""
    info = identify(blob)
    if not info["convertible"]:
        raise ValueError(f"{info['what']}: {info['hint']}")
    z = zipfile.ZipFile(io.BytesIO(blob))
    m = _zip_manifest(z) or {}
    names = z.namelist()
    dest = Path(dest)
    (dest / "agents").mkdir(parents=True, exist_ok=True)
    notes = []

    def put(rel, data):
        p = (dest / rel).resolve()
        if dest.resolve() not in p.parents:
            raise ValueError(f"unsafe path in egg: {rel}")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    schema = info["format"]
    if schema.startswith("brainstem-egg/2"):
        if "repo/soul.md" in names:
            put("soul.md", z.read("repo/soul.md"))
        else:
            put("soul.md", _written_soul(m.get("name") or m.get("slug") or m.get("rapp_id") or "this brainstem"))
            notes.append("soul written for you (this egg carries none)")
        for n in _v2_agents(names):
            put("agents/" + _v2_agent_name(n), z.read(n))
        dropped = sorted({n.split("/", 1)[0] + "/" for n in names if "/" in n and not n.endswith("/")
                          and not n.startswith(V2_AGENT_DIRS) and n != "repo/soul.md"
                          and not n.startswith("repo/") and n != "data/memory.json"})
        if dropped:
            notes.append(f"left out: {', '.join(dropped)} (an organism egg carries agents, soul and memory only)")
        if "data/memory.json" in names:
            mem = _memory(z.read("data/memory.json"))
            if mem:
                put(".brainstem_data/shared_memories/memory.json", mem)
            else:
                notes.append("memory left out: data/memory.json is in a shape this converter does not know")
        if any(n.startswith("repo/") and n.endswith(".py") and "/agents/" not in n for n in names):
            notes.append("engine files under repo/ left out: the egg hatches on the receiver's engine")
    elif schema == "rapp-egg/1.0":
        name = m.get("agent_filename") or f"{m.get('id', 'app')}{AGENT_SUFFIX}"
        if not re.fullmatch(r"[a-z0-9_]+_agent\.py", name):
            name = re.sub(r"[^a-z0-9_]", "_", str(m.get("id", "app")).lower()) + AGENT_SUFFIX
        put(f"agents/{name}", z.read("agent.py"))
        put("soul.md", _written_soul(m.get("id", "this app")))
        notes.append("soul written for you (a rapplication egg carries none)")
        if any(n.startswith("ui/") and not n.endswith("/") for n in names):
            notes.append("ui/ left out: for the agent plus its UI, use  brainfreeze-studio rapplication")
    elif schema == "rapp-application/1.0":
        twin = m["twin"]
        put("soul.md", z.read(twin["soul"]))
        for a in twin.get("agents") or []:
            if a in names and a.endswith(".py"):
                put(f"agents/{Path(a).name}", z.read(a))
        base = str(Path(twin["soul"]).parent) + "/"
        extra = [n for n in names if n.startswith(base) and not n.endswith("/")
                 and n != twin["soul"] and n not in (twin.get("agents") or [])]
        if extra:
            notes.append(f"{len(extra)} data file(s) beside the twin left out (an organism egg carries agents, "
                         "soul and memory only)")
    return dest, notes


def _slug(text):
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "upgraded-egg"


def default_owner(blob):
    """The publisher an older egg names, if any (lowercase GitHub login)."""
    try:
        z = zipfile.ZipFile(io.BytesIO(blob))
        m = _zip_manifest(z) or {}
    except zipfile.BadZipFile:
        return None
    for v in (m.get("publisher"), m.get("rappid")):
        hit = re.search(r"@(?:source/)?([A-Za-z0-9-]+)", str(v or ""))
        if hit:
            return hit.group(1).lower()
    return None


def upgrade(blob, out_dir, owner=None, slug=None, name_hint="egg", include_memory=True):
    """Convert an older egg into a verified rapp/1 organism egg. Returns (laid, notes)."""
    from . import ThrowawayError, lay_egg
    owner = (owner or default_owner(blob) or "").lower()
    if not owner:
        raise ThrowawayError("this egg names no owner; pass --owner <your lowercase GitHub login>")
    slug = slug or _slug(name_hint)
    with tempfile.TemporaryDirectory(prefix="brainfreeze-upgrade-") as tmp:
        folder, notes = extract(blob, Path(tmp) / "rapp_brainstem")
        laid = lay_egg(folder, out_dir, owner=owner, slug=slug, include_memory=include_memory)
    return laid, notes
