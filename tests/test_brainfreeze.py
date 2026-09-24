"""Offline tests: freeze, pack and thaw safety. No network, no model, no running brainstem."""
import base64
import io
import json
import os
import re
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="brainfreeze-test-")
os.environ["BRAINFREEZE_ROOT"] = os.path.join(TMP, "root")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import brainfreeze as bf  # noqa: E402

HISTORY = [{"role": "user", "content": "Remember BLUE HERON."},
           {"role": "assistant", "content": "Noted."}]


def make_brainstem(base):
    b = Path(base) / "rapp_brainstem"
    for rel, text in {
        "brainstem.py": "print('engine')\n",
        "VERSION": "9.9.9\n",
        "soul.md": "You are a test soul.\n",
        "agents/basic_agent.py": "class BasicAgent: pass\n",
        "agents/router_agent.py": "import os\nLIMIT = os.getenv('ROUTER_LIMIT')\n",
        "agents/experimental/deep_agent.py": "# nested\n",
        ".brainstem_data/shared_memories/memory.json": '{"limit": 10000}\n',
        ".brainstem_model": "claude-test\n",
        ".env": "ROUTER_LIMIT=10000\nexport SECRET_API_KEY=do-not-travel\n# comment\n",
        ".copilot_token": '{"access_token": "do-not-travel"}',
        ".copilot_session": "do-not-travel",
        ".brainstem_secret": "do-not-travel",
        ".brainstem_book.json": "[]",
        ".git/HEAD": "ref: refs/heads/main\n",
        "__pycache__/x.cpython-311.pyc": "",
        "outbox/in-progress.md": "being written right now\n",
    }.items():
        p = b / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return b


class FreezeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = make_brainstem(tempfile.mkdtemp(dir=TMP))
        cls.snap = bf.freeze(cls.src, Path(TMP) / "t.snapshot.tar.gz", history=HISTORY,
                             session_id="sess-1", exclude=[cls.src / "outbox"])
        with tarfile.open(cls.snap) as t:
            cls.names = t.getnames()
            cls.state = json.load(t.extractfile("state.json"))
            cls.blob = b"".join(t.extractfile(m).read() for m in t.getmembers() if m.isfile())

    def test_state_travels(self):
        for rel in ("brainstem.py", "soul.md", "agents/router_agent.py", "agents/experimental/deep_agent.py",
                    ".brainstem_data/shared_memories/memory.json", ".brainstem_model"):
            self.assertIn(f"rapp_brainstem/{rel}", self.names)
        self.assertEqual(self.state["history"], HISTORY)
        self.assertEqual(self.state["session_id"], "sess-1")
        self.assertEqual(self.state["brainstem"]["version"], "9.9.9")
        self.assertEqual(self.state["model"], "claude-test")

    def test_secrets_never_travel(self):
        for n in self.names:
            self.assertFalse(n.endswith((".copilot_token", ".copilot_session", ".brainstem_secret", ".env",
                                         ".brainstem_book.json", ".pyc")), n)
            self.assertNotIn("/.git/", n + "/")
        self.assertNotIn(b"do-not-travel", self.blob)

    def test_setting_names_recorded_without_values(self):
        self.assertEqual(sorted(self.state["settings_needed"]), ["ROUTER_LIMIT", "SECRET_API_KEY"])

    def test_exclude_leaves_out_folder(self):
        self.assertFalse(any("outbox" in n for n in self.names))

    def test_memory_can_be_left_out(self):
        snap = bf.freeze(self.src, Path(TMP) / "nomem.snapshot.tar.gz", include_memory=False)
        with tarfile.open(snap) as t:
            self.assertFalse(any(".brainstem_data" in n for n in t.getnames()))
            self.assertFalse(json.load(t.extractfile("state.json"))["memory_included"])


class PackTests(unittest.TestCase):
    def test_run_file_is_valid_python_carrying_snapshot_and_sdk(self):
        src = make_brainstem(tempfile.mkdtemp(dir=TMP))
        snap = bf.freeze(src, Path(TMP) / "p.snapshot.tar.gz", history=HISTORY)
        run = bf.pack(snap)
        text = run.read_text()
        compile(text, str(run), "exec")
        self.assertTrue(os.access(run, os.X_OK))
        self.assertIn("settings: ROUTER_LIMIT, SECRET_API_KEY", text)
        chunks = re.findall(r'^    "([A-Za-z0-9+/=]+)"$', text, re.M)
        z = zipfile.ZipFile(io.BytesIO(base64.b64decode("".join(chunks))))
        self.assertIn("snapshot.tar.gz", z.namelist())
        self.assertIn("brainfreeze/__init__.py", z.namelist())
        self.assertNotIn(b"do-not-travel", z.read("snapshot.tar.gz"))


class ThawSafetyTests(unittest.TestCase):
    def _thaw(self, snap):
        tw = bf.Throwaway(source=str(snap), name=f"t-{os.urandom(3).hex()}")
        bf.ROOT.mkdir(parents=True, exist_ok=True)
        return tw, tw._thaw_into(Path(snap))

    def test_round_trip_restores_conversation(self):
        src = make_brainstem(tempfile.mkdtemp(dir=TMP))
        snap = bf.freeze(src, Path(TMP) / "r.snapshot.tar.gz", history=HISTORY, session_id="sess-9")
        tw, _ = self._thaw(snap)
        self.assertEqual(tw.history, HISTORY)
        self.assertEqual(tw.session_id, "sess-9")
        self.assertTrue((tw.brainstem_dir / "agents" / "router_agent.py").exists())
        self.assertEqual(json.loads((tw.dir / "conversation.json").read_text())["session_id"], "sess-9")

    def _evil(self, name, **kind):
        path = Path(TMP) / f"evil-{os.urandom(3).hex()}.tar.gz"
        with tarfile.open(path, "w:gz") as t:
            info = tarfile.TarInfo(name)
            if kind.get("symlink"):
                info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
                t.addfile(info)
            else:
                data = b"x"
                info.size = len(data)
                t.addfile(info, io.BytesIO(data))
        return path

    def test_rejects_path_escape(self):
        with self.assertRaises(bf.ThrowawayError):
            self._thaw(self._evil("rapp_brainstem/../../escape.txt"))

    def test_rejects_absolute_path(self):
        with self.assertRaises(bf.ThrowawayError):
            self._thaw(self._evil("/tmp/escape.txt"))

    def test_rejects_symlink(self):
        with self.assertRaises(bf.ThrowawayError):
            self._thaw(self._evil("rapp_brainstem/link", symlink=True))

    def test_rejects_unexpected_top_level(self):
        with self.assertRaises(bf.ThrowawayError):
            self._thaw(self._evil("somewhere_else/file.txt"))


class HistoryCapTests(unittest.TestCase):
    def test_matches_web_ui_budget(self):
        tw = bf.Throwaway()
        tw.history = [{"role": "user", "content": "x" * 1000} for _ in range(100)]
        sent = tw._sendable_history()
        self.assertEqual(len(sent), bf.UI_HISTORY_MSGS)
        tw.history = [{"role": "user", "content": "x" * 20000} for _ in range(5)]
        self.assertLessEqual(sum(len(m["content"]) for m in tw._sendable_history()), bf.UI_HISTORY_CHARS)
        tw.ui_history_cap = False
        self.assertEqual(len(tw._sendable_history()), 5)


if __name__ == "__main__":
    unittest.main()
