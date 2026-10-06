"""Command line: python -m brainfreeze up|down|list|chat ..."""
import argparse
import json
import sys

from . import Throwaway, ThrowawayError, down, freeze, lay_egg, list_throwaways, pack, replay


def main(argv=None):
    p = argparse.ArgumentParser(prog="brainfreeze",
                                description="Disposable RAPP brainstems beside the real one.")
    sub = p.add_subparsers(dest="cmd", required=True)

    up = sub.add_parser("up", help="start a throwaway")
    up.add_argument("--from", dest="source", default="grail", help="grail, canary, or a local checkout path")
    up.add_argument("--port", type=int)
    up.add_argument("--name")
    up.add_argument("--bare", action="store_true", help="park the stock agents (BasicAgent only)")
    up.add_argument("--agent", action="append", default=[], help="agent file to drop in (repeatable)")
    up.add_argument("--env", action="append", default=[], help="KEY=VALUE for the server (repeatable)")
    up.add_argument("--ref", help="pin a commit, tag or branch of the source")
    up.add_argument("--soul", help="soul.md to use instead of the default")
    up.add_argument("--kit", help="a handoff kit folder: rebuild that exact demo brainstem")
    up.add_argument("--snapshot", help="resume a frozen brainstem (.snapshot.tar.gz), conversation included")
    up.add_argument("--egg", help="hatch a rapp/1 organism egg onto the engine it expects")
    up.add_argument("--session", help="with --egg: a session egg whose conversation to restore")

    eg = sub.add_parser("egg", help="lay a rapp/1 organism egg (no engine code) + session egg")
    eg.add_argument("target", help="throwaway name, or a path to a rapp_brainstem folder")
    eg.add_argument("--owner", help="your lowercase GitHub login (for a new rappid)")
    eg.add_argument("--slug", help="lowercase-hyphen name (for a new rappid)")
    eg.add_argument("--rappid", help="reuse an existing rappid instead of minting one")
    eg.add_argument("--no-memory", action="store_true", help="leave the brainstem's memory out")
    eg.add_argument("--no-session", action="store_true", help="do not lay a session egg")
    eg.add_argument("--out", default=".", help="folder to write the eggs to")

    ug = sub.add_parser("egg-upgrade", help="say what an .egg file is, and turn an older brainstem egg into rapp/1")
    ug.add_argument("egg", help="the .egg file or https URL")
    ug.add_argument("--owner", help="your lowercase GitHub login (default: the publisher the egg names)")
    ug.add_argument("--slug", help="lowercase-hyphen name (default: from the file name)")
    ug.add_argument("--no-memory", action="store_true", help="leave the egg's memory out")
    ug.add_argument("--check", action="store_true", help="only say what it is; write nothing")
    ug.add_argument("--out", default=".", help="folder to write the rapp/1 egg to")

    sg = sub.add_parser("sign", help="sign a snapshot's bundle.json with an SSH key, as a GitHub login")
    sg.add_argument("snapshot")
    sg.add_argument("--key", default="~/.ssh/id_ed25519", help="SSH private key (default ~/.ssh/id_ed25519)")
    sg.add_argument("--as", dest="login", required=True, help="the GitHub login that signs")
    sg.add_argument("--run-file", action="store_true", help="also write the signed self-bootstrapping .py")

    vf = sub.add_parser("verify", help="check a snapshot's or run file's hashes and signature")
    vf.add_argument("file")
    vf.add_argument("--allowed-signers", help="allowed_signers file (default: $BRAINFREEZE_ROOT/allowed_signers, "
                                              "then github.com/<login>.keys)")

    fz = sub.add_parser("freeze", help="snapshot a running throwaway, or any brainstem folder")
    fz.add_argument("target", help="throwaway name, or a path to a rapp_brainstem folder")
    fz.add_argument("--out", help="snapshot file to write")
    fz.add_argument("--run-file", action="store_true",
                    help="also write a self-bootstrapping .py that resumes it anywhere")
    fz.add_argument("--with", dest="sidecars", action="append", default=[],
                    help="a sidecar folder to bundle and start beside the kernel (repeatable; see docs/BUNDLE.md)")

    pk = sub.add_parser("pack", help="turn a snapshot into a self-bootstrapping .py")
    pk.add_argument("snapshot")
    pk.add_argument("--out")

    rp = sub.add_parser("replay", help="rebuild a handoff kit's brainstem and resend its messages")
    rp.add_argument("kit", help="the handoff-kit folder")
    rp.add_argument("--env", action="append", default=[], help="KEY=VALUE for agents that need settings")
    rp.add_argument("--out", help="where to write the report (default: <kit>/replays/)")

    dn = sub.add_parser("down", help="stop and delete a throwaway")
    dn.add_argument("name", help="throwaway name, or 'all'")

    sub.add_parser("list", help="show throwaways")

    ch = sub.add_parser("chat", help="send a message to a running throwaway, continuing its conversation")
    ch.add_argument("name")
    ch.add_argument("message")

    a = p.parse_args(argv)
    try:
        if a.cmd == "up":
            env = dict(kv.split("=", 1) for kv in a.env)
            if a.egg:
                tw = Throwaway.hatch(a.egg, session=a.session, port=a.port, name=a.name, env=env,
                                     keep=True).up()
            elif a.snapshot:
                tw = Throwaway.thaw(a.snapshot, port=a.port, name=a.name, env=env, keep=True).up()
            elif a.kit:
                tw = Throwaway.from_kit(a.kit, port=a.port, name=a.name, env=env, keep=True).up()
            else:
                tw = Throwaway(source=a.source, port=a.port, name=a.name, bare=a.bare, agents=a.agent,
                               env=env, keep=True, ref=a.ref, soul=a.soul).up()
            h = tw.health()
            inst = tw.dir / "instance.json"
            if inst.exists():
                i = json.loads(inst.read_text())
                print(f"hatched: {i['artifact']}\ninstance: {i['rappid']}\ngrown_from: {i['grown_from']}")
            if getattr(tw, "frozen", None):
                print(f"resumed: session {tw.session_id} with {len(tw.history)} messages "
                      f"(frozen {tw.frozen.get('created')})")
            print(f"name:    {tw.name}\nurl:     {tw.url}\nsource:  {tw.source}\n"
                  f"agents:  {tw.agents_dir}\nlog:     {tw.log_path}\n"
                  f"loaded:  {', '.join(h.get('agents', []))}\nmodel:   {h.get('model')}")
        elif a.cmd == "freeze":
            from pathlib import Path
            import time as _t
            if (Path(a.target).expanduser() / "brainstem.py").exists():
                out = a.out or f"brainstem-{_t.strftime('%Y%m%d-%H%M%S')}.snapshot.tar.gz"
                snap = freeze(a.target, out, sidecars=a.sidecars)
            else:
                tw = Throwaway.attach(a.target)
                if a.sidecars:
                    raise ThrowawayError("--with works when freezing a brainstem folder; a throwaway keeps the sidecars it was thawed with")
                snap = tw.freeze(a.out)
                p = tw.parent()
                if p:
                    print(f"parent:   " + (f"snapshot {p['snapshot_sha256'][:16]} ({p.get('snapshot')})"
                                           if "snapshot_sha256" in p else f"egg {p['egg_address'][:16]}"))
            print(f"snapshot: {snap}")
            if a.run_file:
                print(f"run file: {pack(snap)}")
        elif a.cmd == "egg":
            from pathlib import Path
            if (Path(a.target).expanduser() / "soul.md").exists():
                bdir, hist = a.target, []
            else:
                tw = Throwaway.attach(a.target)
                bdir, hist = tw.brainstem_dir, tw.history
            laid = lay_egg(bdir, a.out, owner=a.owner, slug=a.slug, rappid=a.rappid,
                           include_memory=not a.no_memory, history=None if a.no_session else hist)
            print(f"organism: {laid['organism']}  ({laid['files']} files, verified)")
            if laid["session"]:
                print(f"session:  {laid['session']}")
            print(f"rappid:   {laid['rappid']}\naddress:  {laid['address']}")
            if laid["left_out"]:
                print(f"left out (invalid egg paths): {', '.join(laid['left_out'][:10])}")
        elif a.cmd == "egg-upgrade":
            from pathlib import Path
            from . import _fetch, legacy
            blob = _fetch(a.egg)
            info = legacy.identify(blob)
            print(f"this is:  {info['what']}  ({info['format']})")
            if a.check or not info["convertible"]:
                print(f"next:     {info['hint']}")
                return 0 if info["convertible"] or info["format"].startswith("rapp/1") or a.check else 1
            stem = Path(a.egg.rstrip("/").split("/")[-1]).name
            stem = stem[:-4] if stem.endswith(".egg") else stem
            laid, notes = legacy.upgrade(blob, a.out, owner=a.owner, slug=a.slug, name_hint=stem,
                                         include_memory=not a.no_memory)
            print(f"organism: {laid['organism']}  ({laid['files']} files, verified rapp/1)")
            print(f"rappid:   {laid['rappid']}")
            for n in notes:
                print(f"note:     {n}")
            print(f"next:     brainfreeze up --egg {laid['organism']}")
        elif a.cmd == "sign":
            from . import lineage
            out = lineage.sign(a.snapshot, a.key, a.login)
            print(f"signed:   {out} as {a.login}")
            if a.run_file:
                print(f"run file: {pack(out)}")
        elif a.cmd == "verify":
            import io as _io, tarfile as _tar, tempfile as _tmp
            from pathlib import Path
            from . import ROOT, lineage
            from . import bundle as _b
            data = lineage.snapshot_bytes(a.file)
            with _tmp.TemporaryDirectory() as tmp:
                with _tar.open(fileobj=_io.BytesIO(data)) as t:
                    for mem in t.getmembers():
                        if mem.name.startswith("/") or ".." in Path(mem.name).parts or mem.issym() or mem.islnk():
                            print(f"unsafe entry in snapshot: {mem.name}")
                            return 1
                    t.extractall(tmp)
                if (Path(tmp) / "bundle.json").is_file():
                    try:
                        _b.verify(tmp, json.loads((Path(tmp) / "bundle.json").read_text()))
                        print("hashes:    every file matches bundle.json")
                    except _b.BundleError as e:
                        print(f"hashes:    CHANGED: {e}")
                        return 1
                else:
                    print("hashes:    no bundle.json (frozen before bundles)")
            import os as _os
            allowed = a.allowed_signers or _os.getenv("BRAINFREEZE_ALLOWED_SIGNERS") or str(ROOT / "allowed_signers")
            status, login, why = lineage.check_signature(data, allowed)
            print(f"signature: {status}" + (f" as {login} ({why})" if login else ""))
            return 0 if status in ("verified", "unsigned") else 1
        elif a.cmd == "pack":
            print(f"run file: {pack(a.snapshot, a.out)}")
        elif a.cmd == "replay":
            env = dict(kv.split("=", 1) for kv in a.env)
            print(f"report: {replay(a.kit, out=a.out, env=env)}")
        elif a.cmd == "down":
            for n in down(a.name):
                print(f"removed {n}")
        elif a.cmd == "list":
            rows = list_throwaways()
            if not rows:
                print("no throwaways")
            for r in rows:
                print(f"{r['name']:<12} {'running' if r['running'] else 'stopped':<8} "
                      f"http://127.0.0.1:{r['port']:<6} {r['source']}")
        elif a.cmd == "chat":
            r = Throwaway.attach(a.name).chat(a.message)
            print(r.response)
            if r.agent_logs:
                print("\n--- agent logs ---\n" + r.agent_logs)
    except ThrowawayError as e:
        print(f"throwaway: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
