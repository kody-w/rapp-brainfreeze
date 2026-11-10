"""Factory reset, on throwaway homes: a tiny stand-in brainstem, a stand-in installer, no network."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="brainfreeze-reset-")
os.environ.setdefault("BRAINFREEZE_ROOT", os.path.join(TMP, "root"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import brainfreeze as bf  # noqa: E402
from brainfreeze import reset  # noqa: E402

SERVER = '''import json, os
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"status": "ok", "agents": [], "version": open("VERSION").read().strip()}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass
HTTPServer(("127.0.0.1", int(os.environ["PORT"])), H).serve_forever()
'''
INSTALLER = '''#!/bin/bash
set -e
B="$HOME/.brainstem/src/rapp_brainstem"; mkdir -p "$B/agents"
cat > "$B/brainstem.py" <<'PY'
%s
PY
echo "fresh" > "$B/VERSION"; echo "You are new." > "$B/soul.md"
''' % SERVER


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_home(serve=True):
    base = Path(tempfile.mkdtemp(dir=TMP))
    home = (base / ".brainstem").resolve()
    b = home / "src" / "rapp_brainstem"
    for rel, text in {"brainstem.py": SERVER, "VERSION": "old\n", "soul.md": "You are old.\n",
                      "agents/mine_agent.py": "class Mine: pass\n",
                      ".brainstem_data/shared_memories/memory.json": '{"word": "BLUE HERON"}\n',
                      ".copilot_token": "do-not-travel", ".env": "KEY=do-not-travel\n"}.items():
        (b / rel).parent.mkdir(parents=True, exist_ok=True)
        (b / rel).write_text(text)
    (home / "eggs").mkdir()
    (home / "eggs" / "owner.egg").write_text("the owner's own thing\n")
    installer = base / "install.sh"
    installer.write_text(INSTALLER)
    port, proc = free_port(), None
    if serve:
        proc = subprocess.Popen([sys.executable, "brainstem.py"], cwd=b, env={**os.environ, "PORT": str(port)},
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        assert reset._wait(port, 15), "the stand-in brainstem did not start"
    return base, home, port, installer, proc


def version_on(port):
    import urllib.request
    return json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3))["version"]


class ResetTests(unittest.TestCase):
    def setUp(self):
        self.base, self.home, self.port, self.installer, self.proc = make_home()
        self.jobs = self.base / "no-jobs"
        self.jobs.mkdir()
        self.said = []

    def tearDown(self):
        for pid in reset._listeners(self.port):
            try:
                os.kill(pid, 15)
            except ProcessLookupError:
                pass

    def run_reset(self, **kw):
        kw.setdefault("installer", str(self.installer))
        return reset.factory_reset(self.home, self.port, out=self.base / "out", wake=False, agents_dir=self.jobs,
                                   say=self.said.append, **kw)

    def test_without_yes_nothing_changes(self):
        before = sorted(str(p) for p in self.home.rglob("*"))
        r = self.run_reset()
        self.assertFalse(r["reset"])
        self.assertEqual(r["proof"]["files_identical"], r["proof"]["files_wanted"])
        self.assertEqual(r["proof"]["files_wanted"], 5)
        self.assertEqual(before, sorted(str(p) for p in self.home.rglob("*")))
        self.assertEqual(version_on(self.port), "old")
        self.assertTrue(Path(r["snapshot"]).is_file())

    def test_snapshot_never_carries_sign_in(self):
        import tarfile
        r = self.run_reset()
        with tarfile.open(r["snapshot"]) as t:
            blob = b"".join(t.extractfile(m).read() for m in t.getmembers() if m.isfile())
        self.assertNotIn(b"do-not-travel", blob)

    def test_reset_moves_the_old_home_aside_and_starts_fresh(self):
        r = self.run_reset(yes=True)
        self.assertTrue(r["reset"])
        midden = Path(r["moved_to"])
        self.assertEqual(json.loads((midden / "src/rapp_brainstem/.brainstem_data/shared_memories/memory.json").read_text()),
                         {"word": "BLUE HERON"})
        self.assertEqual((midden / "eggs/owner.egg").read_text(), "the owner's own thing\n")
        self.assertEqual((midden / "src/rapp_brainstem/.copilot_token").read_text(), "do-not-travel")
        self.assertTrue((midden / "RESET-RECEIPT.json").is_file())
        self.assertEqual(version_on(self.port), "fresh")
        self.assertFalse((self.home / "src/rapp_brainstem/.brainstem_data").exists())
        self.assertFalse((self.home / "eggs").exists())
        self.assertEqual(r["after"]["old_memory_files_present"], [])
        self.assertEqual(r["before"]["memory_files"], 1)

    def test_failed_install_puts_the_old_home_back(self):
        bad = self.base / "bad.sh"
        bad.write_text("#!/bin/bash\nmkdir -p \"$HOME/.brainstem/half\"\nexit 7\n")
        with self.assertRaises(reset.ResetError) as caught:
            self.run_reset(yes=True, installer=str(bad))
        self.assertIn("old home is back", str(caught.exception))
        self.assertEqual((self.home / "src/rapp_brainstem/VERSION").read_text(), "old\n")
        self.assertTrue((self.home / "eggs/owner.egg").is_file())
        self.assertTrue(reset._wait(self.port, 15))
        self.assertEqual(version_on(self.port), "old")
        self.assertEqual(len(list(self.base.glob(".brainstem.failed.*"))), 1)
        self.assertEqual(list(self.base.glob(".brainstem.midden.*")), [])

    def test_undo_puts_the_old_home_back_and_keeps_the_fresh_one(self):
        self.run_reset(yes=True)
        result = reset.undo(self.base / "out" / "receipt.json", agents_dir=self.jobs, say=self.said.append)
        self.assertTrue(result["answers"])
        self.assertEqual(version_on(self.port), "old")
        self.assertTrue((self.home / "eggs/owner.egg").is_file())
        self.assertEqual((Path(result["fresh_moved_to"]) / "src/rapp_brainstem/VERSION").read_text(), "fresh\n")

    def test_a_stranger_on_the_port_is_never_stopped(self):
        for pid in reset._listeners(self.port):
            os.kill(pid, 15)
        while reset._listeners(self.port):
            time.sleep(0.2)
        other = Path(tempfile.mkdtemp(dir=TMP))
        (other / "VERSION").write_text("stranger\n")
        (other / "s.py").write_text(SERVER)
        stranger = subprocess.Popen([sys.executable, "s.py"], cwd=other, env={**os.environ, "PORT": str(self.port)},
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            self.assertTrue(reset._wait(self.port, 15))
            with self.assertRaises(reset.ResetError):
                self.run_reset(yes=True)
            self.assertEqual(version_on(self.port), "stranger")
            self.assertTrue((self.home / "src/rapp_brainstem/VERSION").is_file())
            self.assertEqual(list(self.base.glob(".brainstem.midden.*")), [])
        finally:
            stranger.terminate()

    def test_snapshot_folder_inside_the_home_is_refused(self):
        with self.assertRaises(reset.ResetError):
            reset.factory_reset(self.home, self.port, out=self.home / "snap", wake=False, agents_dir=self.jobs,
                                say=self.said.append)
        self.assertFalse((self.home / "snap").exists())

    def test_a_snapshot_that_does_not_match_is_refused(self):
        snap = bf.freeze(self.home / "src/rapp_brainstem", self.base / "s.snapshot.tar.gz")
        (self.home / "src/rapp_brainstem/soul.md").write_text("changed after the freeze\n")
        (self.home / "src/rapp_brainstem/agents/new_agent.py").write_text("# added after the freeze\n")
        with self.assertRaises(reset.ResetError) as caught:
            reset.prove(snap, self.home / "src/rapp_brainstem", wake=False)
        self.assertIn("4 of 6", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
