"""Command line: python -m brainfreeze up|down|list|chat ..."""
import argparse
import json
import sys

from . import Throwaway, ThrowawayError, down, freeze, list_throwaways, pack, replay


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

    fz = sub.add_parser("freeze", help="snapshot a running throwaway, or any brainstem folder")
    fz.add_argument("target", help="throwaway name, or a path to a rapp_brainstem folder")
    fz.add_argument("--out", help="snapshot file to write")
    fz.add_argument("--run-file", action="store_true",
                    help="also write a self-bootstrapping .py that resumes it anywhere")

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
            if a.snapshot:
                tw = Throwaway.thaw(a.snapshot, port=a.port, name=a.name, env=env, keep=True).up()
            elif a.kit:
                tw = Throwaway.from_kit(a.kit, port=a.port, name=a.name, env=env, keep=True).up()
            else:
                tw = Throwaway(source=a.source, port=a.port, name=a.name, bare=a.bare, agents=a.agent,
                               env=env, keep=True, ref=a.ref, soul=a.soul).up()
            h = tw.health()
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
                snap = freeze(a.target, out)
            else:
                snap = Throwaway.attach(a.target).freeze(a.out)
            print(f"snapshot: {snap}")
            if a.run_file:
                print(f"run file: {pack(snap)}")
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
