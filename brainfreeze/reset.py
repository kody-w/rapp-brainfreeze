"""Factory reset for an installed brainstem, with the old self kept asleep.

Order matters, and each step proves itself before the next one runs:

  1. look     what is installed, what keeps it running, what else lives in its home
  2. freeze   snapshot the brainstem (never sign-in or secrets) to a folder OUTSIDE its home
  3. prove    every file the snapshot should carry is byte-identical inside it; then a
              throwaway twin is woken from it and must answer
  4. stop     only with yes=True: stop what serves the port, and prove the port is free
  5. move     the whole home is moved aside (never deleted), and proven gone
  6. install  a fresh brainstem from the public installer, started, and proven to answer
  7. receipt  where the snapshot and the old home are, how to wake a twin, how to undo

A reset that fails after step 5 puts the old home back. Nothing here deletes anything.
The lesson is the phone's: a factory reset runs from outside the thing being reset, and the
backup is proven to restore before anything is erased.
"""
import hashlib
import json
import os
import plistlib
import shutil
import signal
import subprocess
import tarfile
import time
import urllib.request
from pathlib import Path

from . import _NEVER_FREEZE, ROOT, Throwaway, ThrowawayError, freeze

INSTALLERS = {
    "microsoft/aibast-agents-library": "https://microsoft.github.io/aibast-agents-library/install.sh",
    "kody-w/rapp-installer": "https://raw.githubusercontent.com/kody-w/rapp-installer/main/install.sh",
}
# What the installer itself makes. Anything else in the home is the owner's and is named in the plan.
INSTALLER_OWNED = {"src", "venv"}
KEEP_DAYS = 30


class ResetError(ThrowawayError):
    pass


def _run(args, **kw):
    return subprocess.run(args, capture_output=True, text=True, **kw)


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _answers(port, timeout=2):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def _listeners(port):
    out = _run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"]).stdout
    return sorted({int(p) for p in out.split() if p.isdigit()})


def _belongs(pid, home):
    """True when the process runs from inside this home (its folder or its command line)."""
    cwd = _run(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"]).stdout
    cmd = _run(["ps", "-o", "command=", "-p", str(pid)]).stdout
    return any(str(home) in text for text in (cwd, cmd))


def _launch_jobs(home, agents_dir):
    """Login jobs whose own settings point into this home, and whether each is loaded."""
    loaded = {}
    for line in _run(["launchctl", "list"]).stdout.splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) == 3:
            loaded[parts[2]] = int(parts[0]) if parts[0].isdigit() else None
    jobs = []
    for plist in sorted(Path(agents_dir).glob("*.plist")) if Path(agents_dir).is_dir() else []:
        try:
            data = plistlib.loads(plist.read_bytes())
        except Exception:
            continue
        if str(home) not in json.dumps(data, default=str):
            continue
        label = data.get("Label", plist.stem)
        jobs.append({"label": label, "plist": str(plist), "loaded": label in loaded, "pid": loaded.get(label)})
    return jobs


def _carried(brainstem_dir):
    """Every file a snapshot must carry, relative to the brainstem folder (the freeze rules)."""
    files = []
    for path in sorted(Path(brainstem_dir).rglob("*")):
        rel = path.relative_to(brainstem_dir)
        if path.is_symlink() or not path.is_file():
            continue
        if any(p in _NEVER_FREEZE or p.endswith((".tmp", ".pyc", ".partial")) for p in rel.parts):
            continue
        files.append(rel)
    return files


def plan(home="~/.brainstem", port=7071, agents_dir="~/Library/LaunchAgents"):
    """Look only. What a reset of this home would touch."""
    home = Path(home).expanduser().resolve()
    brainstem = home / "src" / "rapp_brainstem"
    if not (brainstem / "brainstem.py").is_file():
        raise ResetError(f"no installed brainstem at {brainstem}")
    origin = _run(["git", "-C", str(home / "src"), "remote", "get-url", "origin"]).stdout.strip()
    installer = next((url for repo, url in INSTALLERS.items() if repo in origin), None)
    pids = _listeners(port)
    others = sorted(p.name for p in home.iterdir() if p.name not in INSTALLER_OWNED)
    version = (brainstem / "VERSION").read_text().strip() if (brainstem / "VERSION").is_file() else None
    return {
        "home": str(home), "brainstem": str(brainstem), "version": version, "origin": origin or None,
        "installer": installer, "port": port,
        "serving": [{"pid": p, "from_this_home": _belongs(p, home)} for p in pids],
        "jobs": _launch_jobs(home, Path(agents_dir).expanduser()),
        "also_in_home": others,
        "memory_files": sum(1 for p in (brainstem / ".brainstem_data").rglob("*") if p.is_file())
        if (brainstem / ".brainstem_data").is_dir() else 0,
    }


def prove(snapshot, brainstem_dir, wake=True):
    """The snapshot holds every file it should, byte for byte; then a twin woken from it answers."""
    wanted = _carried(brainstem_dir)
    same, missing, different = 0, [], []
    with tarfile.open(snapshot, "r:gz") as tar:
        members = {m.name: m for m in tar.getmembers() if m.isfile()}
        for rel in wanted:
            member = members.get(str(Path("rapp_brainstem") / rel))
            if member is None:
                missing.append(str(rel))
                continue
            digest = hashlib.sha256(tar.extractfile(member).read()).hexdigest()
            if digest == _sha(Path(brainstem_dir) / rel):
                same += 1
            else:
                different.append(str(rel))
    result = {"files_wanted": len(wanted), "files_identical": same, "missing": missing, "different": different,
              "woke": None}
    if missing or different:
        raise ResetError(f"the snapshot does not match: {same} of {len(wanted)} files identical; "
                         f"missing {missing[:5]}, different {different[:5]}. Nothing was changed.")
    if wake:
        twin = Throwaway.thaw(snapshot, sign_in=False)
        try:
            twin.up()
            health = twin.health()
            result["woke"] = {"port": twin.port, "agents": len(health.get("agents", [])), "status": health.get("status")}
        except ThrowawayError as e:
            raise ResetError(f"a twin would not wake from the snapshot: {e}. Nothing was changed.")
        finally:
            twin._teardown(quiet=True)
    return result


def _stop(found, home, port, say):
    uid = os.getuid()
    stopped = []
    for job in found["jobs"]:
        if job["loaded"]:
            say(f"stopping login job {job['label']}")
            _run(["launchctl", "bootout", f"gui/{uid}/{job['label']}"])
            stopped.append(job)
    for pid in _listeners(port):
        if not _belongs(pid, home):
            raise ResetError(f"port {port} is served by process {pid}, which does not run from {home}. Not stopping it.")
        say(f"asking process {pid} to stop")
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.time() + 20
    while time.time() < deadline and _listeners(port):
        time.sleep(0.5)
    if _listeners(port):
        raise ResetError(f"port {port} is still being served after 20 s. Nothing was moved.")
    return stopped


def _start(home, port, stopped, say, log):
    """Bring back whatever was serving: the login jobs that still have something to run, else a plain start."""
    uid = os.getuid()
    back, left = [], []
    for job in stopped:
        try:
            data = plistlib.loads(Path(job["plist"]).read_bytes())
        except Exception:
            data = {}
        args = data.get("ProgramArguments") or [data.get("Program", "")]
        inside = [a for a in args if isinstance(a, str) and str(home) in a and a.startswith("/")]
        if all(Path(a).exists() for a in inside):
            say(f"starting login job {job['label']}")
            _run(["launchctl", "bootstrap", f"gui/{uid}", job["plist"]])
            back.append(job["label"])
        else:
            left.append(job["label"])
    brainstem = home / "src" / "rapp_brainstem"
    if not _wait(port, 5):
        python = home / "venv" / "bin" / "python"
        say("starting the brainstem")
        with open(log, "ab") as out:
            subprocess.Popen([str(python if python.exists() else "python3"), "brainstem.py"], cwd=brainstem,
                             env={**os.environ, "PORT": str(port)}, stdout=out, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, start_new_session=True)
    return back, left


def _wait(port, seconds):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if _answers(port):
            return True
        time.sleep(0.5)
    return False


def _install(installer, home, out, say):
    script = Path(installer).expanduser()
    if not script.is_file():
        script = out / "install.sh"
        say(f"fetching the installer from {installer}")
        with urllib.request.urlopen(installer, timeout=60) as r:
            script.write_bytes(r.read())
    say("installing a fresh brainstem (this can take a few minutes)")
    with open(out / "install.log", "wb") as log:
        done = subprocess.run(["/bin/bash", str(script), "--no-launch"], env={**os.environ, "HOME": str(home.parent)},
                              stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    if done.returncode != 0 or not (home / "src" / "rapp_brainstem" / "brainstem.py").is_file():
        raise ResetError(f"the installer did not finish (exit {done.returncode}); see {out / 'install.log'}")


def factory_reset(home="~/.brainstem", port=7071, installer=None, out=None, yes=False, wake=True,
                  agents_dir="~/Library/LaunchAgents", keep_days=KEEP_DAYS, say=print):
    """Freeze, prove, and only with yes=True: stop, move aside, reinstall, prove. Returns the receipt."""
    found = plan(home, port, agents_dir)
    home = Path(found["home"])
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = Path(out).expanduser() if out else ROOT / "resets" / stamp
    if home in out.resolve().parents or out.resolve() == home:
        raise ResetError("the snapshot folder must be outside the home being reset")
    out.mkdir(parents=True, exist_ok=True)
    installer = installer or found["installer"]

    say(f"1 of 7  looked: brainstem {found['version']} in {home}, {found['memory_files']} memory files, "
        f"{len(found['jobs'])} login jobs point here, {len(found['also_in_home'])} other things live in this home")
    snapshot = freeze(found["brainstem"], out / f"brainstem-{stamp}.snapshot.tar.gz")
    say(f"2 of 7  frozen: {snapshot}")
    proof = prove(snapshot, found["brainstem"], wake=wake)
    woke = f"a twin woke on port {proof['woke']['port']} and answered" if proof["woke"] else "waking was skipped"
    say(f"3 of 7  proven: {proof['files_identical']} of {proof['files_wanted']} files identical; {woke}")

    receipt = {
        "kind": "brainstem-factory-reset", "at": stamp, "home": str(home), "port": port,
        "before": {k: found[k] for k in ("version", "origin", "memory_files", "also_in_home")},
        "snapshot": str(snapshot), "snapshot_sha256": _sha(snapshot), "proof": proof,
        "wake": f"python3 -m brainfreeze up --snapshot {snapshot}",
        "reset": False, "moved_to": None, "undo": None,
    }
    if not yes:
        say("stopped here: nothing was changed. Run again with --yes to reset.")
        (out / "receipt.json").write_text(json.dumps(receipt, indent=1))
        return receipt
    if not installer:
        raise ResetError("no installer known for this brainstem's origin; pass --installer. Nothing was changed.")
    if home.name != ".brainstem":
        raise ResetError("the installer only installs into a folder named .brainstem. Nothing was changed.")

    stopped = _stop(found, home, port, say)
    say(f"4 of 7  stopped: port {port} is free")
    midden = home.with_name(f"{home.name}.midden.{stamp}")
    os.rename(home, midden)
    if home.exists() or not (midden / "src").is_dir():
        raise ResetError(f"the move did not hold; look at {home} and {midden}")
    say(f"5 of 7  moved aside: {midden}")

    def put_back(why):
        say(f"putting the old home back: {why}")
        for pid in _listeners(port):
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        deadline = time.time() + 20
        while time.time() < deadline and _listeners(port):
            time.sleep(0.5)
        if home.exists():
            os.rename(home, home.with_name(f"{home.name}.failed.{stamp}"))
        os.rename(midden, home)
        _start(home, port, stopped, say, out / "server.log")
        if not _wait(port, 60):
            say(f"the old home is back, but nothing answers on port {port} yet; start it by hand")

    try:
        _install(installer, home, out, say)
        back, left = _start(home, port, stopped, say, out / "server.log")
        if not _wait(port, 60):
            raise ResetError(f"the fresh brainstem did not answer on port {port} within 60 s")
    except BaseException as e:
        put_back(str(e))
        raise ResetError(f"reset failed and the old home is back in place: {e}")
    fresh = home / "src" / "rapp_brainstem"
    carried_over = [str(r) for r in _carried(midden / "src" / "rapp_brainstem")
                    if r.parts[0] == ".brainstem_data" and (fresh / r).exists()]
    say(f"6 of 7  fresh brainstem answers on port {port}; {len(carried_over)} old memory files in it")

    expires = time.strftime("%Y-%m-%d", time.localtime(time.time() + keep_days * 86400))
    receipt.update({
        "reset": True, "moved_to": str(midden), "installer": str(installer),
        "after": {"version": (fresh / "VERSION").read_text().strip() if (fresh / "VERSION").is_file() else None,
                  "answers": True, "old_memory_files_present": carried_over},
        "jobs_restarted": back, "jobs_left_stopped": left,
        "undo": f"python3 -m brainfreeze factory-undo {out / 'receipt.json'}",
        "old_home_holds_sign_in": True, "review_old_home_after": expires,
    })
    (out / "receipt.json").write_text(json.dumps(receipt, indent=1))
    shutil.copy(out / "receipt.json", midden / "RESET-RECEIPT.json")
    say(f"7 of 7  receipt: {out / 'receipt.json'}")
    if left:
        say(f"left stopped (what they ran lived in the old home): {', '.join(left)}")
    return receipt


def undo(receipt_path, agents_dir="~/Library/LaunchAgents", say=print):
    """Put the old home back exactly as it was. The fresh one is moved aside, never deleted."""
    receipt = json.loads(Path(receipt_path).expanduser().read_text())
    home, midden, port = Path(receipt["home"]), Path(receipt.get("moved_to") or ""), receipt["port"]
    if not receipt.get("reset") or not midden.is_dir():
        raise ResetError("this receipt has no old home to put back")
    found = plan(home, port, agents_dir) if (home / "src" / "rapp_brainstem" / "brainstem.py").is_file() else \
        {"jobs": _launch_jobs(home, Path(agents_dir).expanduser())}
    stopped = _stop(found, home, port, say)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    aside = None
    if home.exists():
        aside = home.with_name(f"{home.name}.fresh.{stamp}")
        os.rename(home, aside)
    os.rename(midden, home)
    jobs = _launch_jobs(home, Path(agents_dir).expanduser())
    back, left = _start(home, port, [j for j in jobs if not j["loaded"] and j["label"] in
                                     set(receipt.get("jobs_restarted", []) + receipt.get("jobs_left_stopped", []))] or stopped,
                        say, Path(receipt_path).expanduser().parent / "server.log")
    answers = _wait(port, 60)
    say(f"old home is back at {home}; answers on port {port}: {answers}; fresh one kept at {aside}")
    return {"home": str(home), "answers": answers, "fresh_moved_to": str(aside) if aside else None,
            "jobs_restarted": back, "jobs_left_stopped": left}
