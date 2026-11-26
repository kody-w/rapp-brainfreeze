"""host: wake a snapshot with the caller as the model. Offline: a small stand-in engine, no flask, no network."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import brainfreeze as bf  # noqa: E402

ENGINE = '''
import json
RUNS = __import__("pathlib").Path(__file__).with_name("runs.log")
class Echo:
    metadata = {"name": "Echo", "description": "says the secret word", "parameters": {"type": "object", "properties": {}}}
    def perform(self, **kw):
        RUNS.open("a").write("ran\\n")
        return "the word is HERON"
def load_agents(): return {"Echo": Echo()}
def call_copilot(messages, tools=None): raise RuntimeError("no sign-in here")
def call_copilot_stream(messages, tools=None, model=None): raise RuntimeError("no sign-in here")
def run_tool_calls(tool_calls, agents, session_id=None):
    out = [{"role": "tool", "tool_call_id": c["id"], "content": agents[c["function"]["name"]].perform()} for c in tool_calls]
    return out, ["[" + c["function"]["name"] + "] ran" for c in tool_calls]
class _R:
    def __init__(s, body): s.body, s.status_code = body, 200
    def get_json(s): return s.body
class _C:
    def post(s, path, json=None):
        agents = load_agents()
        tools = [{"type": "function", "function": a.metadata} for a in agents.values()]
        msgs = [{"role": "system", "content": "soul"}] + list(json["conversation_history"]) + [{"role": "user", "content": json["user_input"]}]
        logs = []
        for _ in range(3):
            resp, model = call_copilot(msgs, tools)
            m = resp["choices"][0]["message"]; msgs.append(m)
            if not m.get("tool_calls"): break
            res, lg = run_tool_calls(m["tool_calls"], agents, session_id=json["session_id"]); msgs += res; logs += lg
        return _R({"response": m.get("content"), "model": model, "agent_logs": "\\n".join(logs)})
class app:
    test_client = staticmethod(lambda: _C())
'''
HISTORY = [{"role": "user", "content": "Remember BLUE HERON."}, {"role": "assistant", "content": "Noted."}]


def make(base):
    b = Path(base) / "rapp_brainstem"
    for rel, text in {"brainstem.py": ENGINE, "VERSION": "9.9.9\n", "soul.md": "soul\n",
                      "agents/basic_agent.py": "class BasicAgent: pass\n", ".env": "SECRET=do-not-travel\n",
                      ".copilot_token": "do-not-travel"}.items():
        p = b / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return b


def host(cwd, *args):
    r = subprocess.run([sys.executable, "-m", "brainfreeze", "host", *args], cwd=cwd, capture_output=True, text=True,
                       env={**os.environ, "PYTHONPATH": str(REPO), "PYTHONDONTWRITEBYTECODE": "1"})
    return r.returncode, (json.loads(r.stdout) if r.stdout.strip().startswith(("{", "[")) else r.stdout + r.stderr)


class HostTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.snap = t / "one.snapshot.tar.gz"
        bf.freeze(make(t / "src"), self.snap, history=HISTORY, session_id="s1")
        self.here = t / "here"
        self.here.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_thaw_work_freeze_child(self):
        code, out = host(self.here, "thaw", str(self.snap))
        self.assertEqual((code, out["messages_restored"], out["checked"]), (0, 2, "every file matches bundle.json"))
        self.assertEqual([a["name"] for a in host(self.here, "tools")[1]], ["Echo"])
        code, out = host(self.here, "ask", "what is the word?")
        self.assertEqual(out["need"], "your answer, as the model")
        self.assertIn("Remember BLUE HERON.", json.dumps(out["new_messages"]))      # the frozen conversation came back
        code, out = host(self.here, "answer", json.dumps({"tool_calls": [{"function": {"name": "Echo", "arguments": {}}}]}))
        self.assertIn("the word is HERON", json.dumps(out["new_messages"]))          # the agent really ran
        code, out = host(self.here, "answer", "The word is HERON.")
        self.assertEqual((out["status"], out["agents_that_ran"], out["done"]["response"]), (200, ["Echo"], "The word is HERON."))
        self.assertEqual((self.here / "rapp_brainstem" / "runs.log").read_text(), "ran\n")   # once, despite two replays
        code, out = host(self.here, "freeze", "two.snapshot.tar.gz")
        self.assertEqual(out["messages"], 4)
        import hashlib, tarfile
        self.assertEqual(out["parent"], {"snapshot_sha256": hashlib.sha256(self.snap.read_bytes()).hexdigest()})
        with tarfile.open(self.here / "two.snapshot.tar.gz") as t:
            names = t.getnames()
            state = json.load(t.extractfile("state.json"))
            bundle = json.load(t.extractfile("bundle.json"))
        self.assertEqual(bundle["parent"], out["parent"])
        self.assertEqual(state["woken_by"]["host"], True)
        self.assertFalse([n for n in names if n.endswith((".env", ".copilot_token", ".host"))])

    def test_changed_file_is_refused_and_nothing_is_left(self):
        import tarfile
        t = Path(self.tmp.name) / "x"
        with tarfile.open(self.snap) as tar:
            tar.extractall(t)
        (t / "rapp_brainstem" / "soul.md").write_text("changed\n")
        bad = Path(self.tmp.name) / "bad.snapshot.tar.gz"
        with tarfile.open(bad, "w:gz") as tar:
            for n in ("state.json", "bundle.json", "rapp_brainstem"):
                tar.add(t / n, n)
        code, out = host(self.here, "thaw", str(bad))
        self.assertEqual(code, 1)
        self.assertIn("refusing to wake it", out["error"])
        self.assertEqual(list(self.here.iterdir()), [])

    def test_no_turn_waiting_and_second_thaw(self):
        host(self.here, "thaw", str(self.snap))
        self.assertIn("no turn is waiting", host(self.here, "answer", "hi")[1]["error"])
        self.assertIn("already awake", host(self.here, "thaw", str(self.snap))[1]["error"])

    def test_engine_file_is_untouched(self):
        import hashlib
        host(self.here, "thaw", str(self.snap))
        f = self.here / "rapp_brainstem" / "brainstem.py"
        before = hashlib.sha256(f.read_bytes()).hexdigest()
        host(self.here, "ask", "hello")
        host(self.here, "answer", "hi")
        self.assertEqual(hashlib.sha256(f.read_bytes()).hexdigest(), before)


if __name__ == "__main__":
    unittest.main()


class ServeOnlyPackageTests(unittest.TestCase):
    """An AI sandbox (claude.ai, 2026-11-26) had flask but not flask_cors; host stopped at step 2."""

    def test_serve_only_imports_get_a_stand_in_and_others_still_stop(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            b = make(t / "src")
            # an engine that imports what only a served browser page needs; a blocker hides the real packages
            (b / "brainstem.py").write_text("from flask_cors import CORS\nfrom dotenv import load_dotenv\n"
                                            "load_dotenv()\nCORS(None)\n" + ENGINE)
            snap = t / "s.snapshot.tar.gz"
            bf.freeze(b, snap, history=HISTORY, session_id="s1")
            here = t / "here"; here.mkdir()
            block = t / "block"; block.mkdir()
            (block / "sitecustomize.py").write_text(
                "import sys\nclass B:\n"
                "    def find_spec(self, n, p=None, t=None):\n"
                "        if n.split('.')[0] in ('flask_cors', 'dotenv', 'not_a_real_pkg'):\n"
                "            raise ModuleNotFoundError(n, name=n)\n"
                "sys.meta_path.insert(0, B())\n")
            env = {**os.environ, "PYTHONPATH": f"{block}{os.pathsep}{REPO}", "PYTHONDONTWRITEBYTECODE": "1"}
            run = lambda *a: subprocess.run([sys.executable, "-m", "brainfreeze", "host", *a], cwd=here,
                                            capture_output=True, text=True, env=env)
            self.assertEqual(run("thaw", str(snap)).returncode, 0)
            r = run("tools")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("Echo", r.stdout)
            # a package that is not serve-only is still refused, never faked
            (here / "rapp_brainstem" / "brainstem.py").write_text("import not_a_real_pkg\n" + ENGINE)
            r = run("tools")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("not_a_real_pkg", r.stdout + r.stderr)


class ChainTests(unittest.TestCase):
    """Seen on claude.ai 2026-11-26: a second freeze named the original upload as its parent, not the first child."""

    def test_second_freeze_is_a_child_of_the_first(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            snap = t / "one.snapshot.tar.gz"
            bf.freeze(make(t / "src"), snap, history=HISTORY, session_id="s1")
            here = t / "here"; here.mkdir()
            host(here, "thaw", str(snap))
            code, a = host(here, "freeze", str(t / "a.snapshot.tar.gz"))
            code, b = host(here, "freeze", str(t / "b.snapshot.tar.gz"))
            import hashlib
            self.assertEqual(a["parent"]["snapshot_sha256"], hashlib.sha256(snap.read_bytes()).hexdigest())
            self.assertEqual(b["parent"]["snapshot_sha256"], a["sha256"])


class SavedModelTests(unittest.TestCase):
    """Seen on claude.ai 2026-11-26: a child froze its model as host-ai, losing the model the Brainstem was set to."""

    def test_child_keeps_the_saved_model(self):
        import tarfile
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            snap = t / "one.snapshot.tar.gz"
            bf.freeze(make(t / "src"), snap, history=HISTORY, session_id="s1", model="claude-haiku-5.5")
            here = t / "here"; here.mkdir()
            host(here, "thaw", str(snap))
            host(here, "freeze", "a.snapshot.tar.gz")
            with tarfile.open(here / "a.snapshot.tar.gz") as tar:
                state = json.load(tar.extractfile("state.json"))
            self.assertEqual(state["model"], "claude-haiku-5.5")
            self.assertTrue(state["woken_by"]["host"])


class ExportTests(unittest.TestCase):
    """Kody 2026-11-26: export the chat as .json and load it into the local Brainstem like nothing happened."""

    def test_export_matches_the_page_import_shape(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            snap = t / "one.snapshot.tar.gz"
            bf.freeze(make(t / "src"), snap, history=HISTORY, session_id="s1", model="claude-haiku-5.5")
            here = t / "here"; here.mkdir()
            host(here, "thaw", str(snap))
            host(here, "ask", "what is the word?")
            host(here, "answer", json.dumps({"tool_calls": [{"function": {"name": "Echo", "arguments": {}}}]}))
            host(here, "answer", "The word is HERON.")
            code, out = host(here, "export", "chat.json")
            self.assertEqual((code, out["turns"]), (0, 4))
            d = json.loads((here / "chat.json").read_text())
            self.assertEqual((d["session_id"], d["model"], d["brainstem_version"]), ("s1", "claude-haiku-5.5", "9.9.9"))
            for turn in d["turns"]:          # the page's normalizeImportedChat rules
                self.assertIn(turn["role"], ("user", "assistant"))
                self.assertIsInstance(turn["content"], str)
                self.assertIsInstance(turn.get("agent_logs", ""), str)
            self.assertEqual(d["turns"][-1]["content"], "The word is HERON.")
            self.assertIn("Echo", d["turns"][-1]["agent_logs"])


class MemoryHomeTests(unittest.TestCase):
    """Kody 2026-11-26: the chat export carries memory so Import picks up exactly where it left off."""

    def test_export_carries_memory_and_home_adds_only(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            src = make(t / "src")
            (src / ".brainstem_data" / "shared_memories").mkdir(parents=True)
            (src / ".brainstem_data" / "shared_memories" / "memory.json").write_text(
                json.dumps({"a": {"message": "pelican"}}))
            snap = t / "one.snapshot.tar.gz"
            bf.freeze(src, snap, history=HISTORY, session_id="s1")
            here = t / "here"; here.mkdir()
            host(here, "thaw", str(snap))
            mem = here / "rapp_brainstem" / ".brainstem_data" / "shared_memories" / "memory.json"
            mem.write_text(json.dumps({"a": {"message": "pelican"}, "b": {"message": "green"}}))   # learned while away
            host(here, "export", "chat.json")
            d = json.loads((here / "chat.json").read_text())
            self.assertEqual(set(d["memory"]["shared_memories/memory.json"]), {"a", "b"})
            home = t / "home"
            (home / ".brainstem_data" / "shared_memories").mkdir(parents=True)
            (home / "soul.md").write_text("soul\n")
            hm = home / ".brainstem_data" / "shared_memories" / "memory.json"
            hm.write_text(json.dumps({"a": {"message": "CHANGED AT HOME"}, "z": {"message": "mine"}}))
            self.assertEqual(bf.memory_home(here / "chat.json", home), {"shared_memories/memory.json": 1})
            got = json.loads(hm.read_text())
            self.assertEqual(got["a"]["message"], "CHANGED AT HOME")    # never overwritten
            self.assertEqual(set(got), {"a", "b", "z"})
            self.assertTrue(list(hm.parent.glob("memory.json.before-*")))   # undo copy
            self.assertEqual(bf.memory_home(here / "chat.json", home), {})  # running it twice adds nothing

    def test_memory_paths_cannot_leave_the_data_folder(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            (t / "chat.json").write_text(json.dumps({"turns": [], "memory": {"../../outside.json": {"x": 1}}}))
            (t / "home").mkdir()
            (t / "home" / "soul.md").write_text("soul\n")
            self.assertEqual(bf.memory_home(t / "chat.json", t / "home"), {})
            self.assertFalse((t / "outside.json").exists())
