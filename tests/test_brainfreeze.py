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


class EggTests(unittest.TestCase):
    RID = "rappid:@kody-w/test-desk:" + "a" * 64
    UTC = "2026-09-23T12:00:00.000Z"

    def lay(self, **kw):
        src = make_brainstem(tempfile.mkdtemp(dir=TMP))
        out = tempfile.mkdtemp(dir=TMP)
        args = dict(rappid=self.RID, history=HISTORY, created_utc=self.UTC)
        args.update(kw)
        return src, bf.lay_egg(src, out, **args)

    def test_organism_egg_verifies_and_carries_no_engine_or_secrets(self):
        _, laid = self.lay()
        blob = laid["organism"].read_bytes()
        self.assertEqual(bf.rapp1.verify_egg(blob)[0], True)
        manifest, files = bf.rapp1.read_egg(blob)
        self.assertEqual(manifest["variant"], "organism")
        self.assertIn("soul.md", files)
        self.assertIn("agents/router_agent.py", files)
        self.assertIn(".brainstem_data/shared_memories/memory.json", files)
        self.assertNotIn("brainstem.py", files)
        for path in files:
            self.assertFalse(path.endswith((".copilot_token", ".env", ".brainstem_secret", ".pyc")), path)
        self.assertNotIn(b"do-not-travel", b"".join(files.values()))
        self.assertEqual(manifest["payload"]["engine"]["version"], "9.9.9")
        self.assertEqual(manifest["payload"]["settings_needed"], ["ROUTER_LIMIT", "SECRET_API_KEY"])

    def test_session_egg_holds_the_conversation(self):
        _, laid = self.lay()
        blob = laid["session"].read_bytes()
        self.assertTrue(bf.rapp1.verify_egg(blob)[0])
        manifest, _ = bf.rapp1.read_egg(blob)
        self.assertEqual(manifest["variant"], "session")
        self.assertEqual(manifest["payload"]["transcript"], HISTORY)

    def test_memory_can_be_left_out(self):
        _, laid = self.lay(include_memory=False)
        _, files = bf.rapp1.read_egg(laid["organism"].read_bytes())
        self.assertFalse(any(p.startswith(".brainstem_data") for p in files))

    def test_byte_reproducible(self):
        _, a = self.lay()
        _, b = self.lay()
        self.assertEqual(a["organism"].read_bytes(), b["organism"].read_bytes())
        self.assertEqual(a["address"], b["address"])

    def test_new_rappid_is_minted_not_name_derived(self):
        _, a = self.lay(rappid=None, owner="Kody-W", slug="test-desk")
        _, b = self.lay(rappid=None, owner="kody-w", slug="test-desk")
        self.assertTrue(a["rappid"].startswith("rappid:@kody-w/test-desk:"))
        self.assertNotEqual(a["rappid"], b["rappid"])

    def test_tampered_egg_is_refused(self):
        _, laid = self.lay()
        blob = bytearray(laid["organism"].read_bytes())
        i = blob.find(b"You are a test soul.")
        blob[i] = ord("Y") ^ 1
        bad = Path(TMP) / "tampered.egg"
        bad.write_bytes(bytes(blob))
        with self.assertRaises(bf.ThrowawayError):
            bf.Throwaway.hatch(bad)

    def test_hatch_lays_files_and_records_lineage(self):
        _, laid = self.lay()
        tw = bf.Throwaway.hatch(laid["organism"], session=laid["session"], name=f"h-{os.urandom(3).hex()}")
        self.assertTrue(tw.bare)
        self.assertEqual(tw.source, "grail")          # "other" engines hatch onto the grail
        tw.brainstem_dir.mkdir(parents=True)
        tw._apply_egg()
        self.assertEqual((tw.brainstem_dir / "soul.md").read_text(), "You are a test soul.\n")
        self.assertTrue((tw.brainstem_dir / "agents" / "router_agent.py").exists())
        self.assertFalse((tw.brainstem_dir / "rappid.json").exists())
        inst = json.loads((tw.dir / "instance.json").read_text())
        self.assertEqual(inst["artifact"], self.RID)
        self.assertEqual(inst["grown_from"], laid["address"])
        self.assertTrue(inst["rappid"].startswith("rappid:@kody-w/test-desk:"))
        self.assertNotEqual(inst["rappid"], self.RID)
        self.assertEqual(tw.history, HISTORY)


class VendorTests(unittest.TestCase):
    def test_reference_implementation_is_unmodified(self):
        pkg = Path(bf.__file__).parent
        meta = json.loads((pkg / "rapp1.vendor.json").read_text())
        import hashlib
        self.assertEqual(hashlib.sha256((pkg / "rapp1.py").read_bytes()).hexdigest(), meta["sha256"])


def _zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, data in files.items():
            z.writestr(n, data if isinstance(data, (bytes, str)) else json.dumps(data))
    return buf.getvalue()


class LegacyEggTests(unittest.TestCase):
    AGENT = "class XAgent: pass\n"
    EGGS = {
        "twin": {"manifest.json": {"schema": "brainstem-egg/2.1", "type": "twin",
                                   "rappid": "rappid:twin:@source/kody-w:98b6c7fecd5af68e"},
                 "repo/soul.md": "You are a twin.\n", "repo/brainstem.py": "engine\n",
                 "repo/agents/context_memory_agent.py": AGENT,
                 "data/memory.json": {"facts": ["I like single-file agents."]}},
        "rapplication": {"manifest.json": {"schema": "rapp-egg/1.0", "type": "rapplication", "id": "bookfactory",
                                           "agent_filename": "bookfactory_agent.py"},
                         "agent.py": AGENT, "ui/index.html": "<html></html>"},
        "rapplication22": {"manifest.json": {"schema": "brainstem-egg/2.2-rapplication", "type": "rapplication",
                                             "publisher": "@kody-w", "name": "Deploy"},
                           "agents/deploy_agent.py": AGENT, "rapp_ui/deploy/index.html": "<html></html>"},
        "cubby": {"manifest.json": {"schema": "brainstem-egg/2.3-cubby", "type": "cubby", "slug": "demo"},
                  "cubby/agents/a_agent.py": AGENT, "cubby/agents/b_agent.py": AGENT, "cubby/transcript.txt": "t"},
        "application": {"manifest.json": {"schema": "rapp-application/1.0", "id": "cook", "publisher": "@kody-w",
                                          "runtime": "twin", "twin": {"soul": "twin/soul.md",
                                                                      "agents": ["twin/agents/cook_agent.py"]}},
                        "twin/soul.md": "You cook.\n", "twin/agents/cook_agent.py": AGENT,
                        "twin/recipes/index.json": "{}"},
    }

    def test_older_brainstem_eggs_convert_to_verified_rapp1(self):
        from brainfreeze import legacy, rapp1
        for name, files in self.EGGS.items():
            with self.subTest(name):
                blob = _zip(files)
                self.assertTrue(legacy.identify(blob)["convertible"])
                out = Path(TMP) / "upgraded" / name
                laid, notes = legacy.upgrade(blob, out, owner="kody-w", name_hint=name)
                egg = laid["organism"].read_bytes()
                self.assertTrue(rapp1.verify_egg(egg)[0])
                manifest, got = rapp1.read_egg(egg)
                self.assertEqual(manifest["variant"], "organism")
                self.assertIn("soul.md", got)
                self.assertTrue(any(p.startswith("agents/") and p.endswith("_agent.py") for p in got))
                self.assertFalse(any("brainstem.py" in p or p.startswith(("ui/", "rapp_ui/")) for p in got))

    def test_owner_comes_from_the_publisher_never_the_twin_name(self):
        from brainfreeze import legacy
        self.assertIsNone(legacy.default_owner(_zip(self.EGGS["twin"])))          # @source/<twin> is not an owner
        self.assertEqual(legacy.default_owner(_zip(self.EGGS["application"])), "kody-w")
        with self.assertRaises(bf.ThrowawayError):
            legacy.upgrade(_zip(self.EGGS["twin"]), Path(TMP) / "no-owner")

    def test_old_memory_facts_become_brainstem_memory(self):
        from brainfreeze import legacy, rapp1
        laid, _ = legacy.upgrade(_zip(self.EGGS["twin"]), Path(TMP) / "upgraded-mem", owner="kody-w")
        _, got = rapp1.read_egg(laid["organism"].read_bytes())
        mem = json.loads(got[".brainstem_data/shared_memories/memory.json"])
        self.assertEqual([m["message"] for m in mem.values()], ["I like single-file agents."])

    def test_non_brainstems_are_named_not_parsed_at(self):
        from brainfreeze import legacy
        cases = {
            b'{"schema": "hologram-cartridge/1.0", "title": "Arachne", "x": 1.5}': "hologram cartridge",
            b'{"format": "holographic-moment-egg/1.0", "moment": {}, "exported": "x"}': "Rappter moment",
            base64.b64encode(b'{"genome": {"layers": []}}'): "Rappter creature genome",
            _zip({"manifest.json": {"contents": []}, ".claude/agents/a.md": "x"}): "Claude Code agents",
            b"not an egg at all": "not a ZIP or JSON",
        }
        for blob, words in cases.items():
            with self.subTest(words):
                info = legacy.identify(blob)
                self.assertFalse(info["convertible"])
                self.assertIn(words, info["what"])

    def test_hatch_names_what_a_wrong_file_is(self):
        p = Path(TMP) / "cartridge.egg"
        p.write_bytes(b'{"schema": "hologram-cartridge/1.0", "title": "Arachne", "x": 1.5}')
        with self.assertRaises(bf.ThrowawayError) as e:
            bf.Throwaway.hatch(p)
        self.assertIn("hologram cartridge", str(e.exception))


class BundleTests(unittest.TestCase):
    def _snap(self, name, **kw):
        base = Path(tempfile.mkdtemp(dir=TMP))
        src = make_brainstem(base)
        side = base / "sidecar"
        side.mkdir()
        (side / "sidecar.json").write_text(json.dumps({"kind": "service", "run": ["{python}", "serve.py", "{port}"],
                                                      "env": {"BRAINSTEM_URL": "{kernel_url}"}}))
        (side / "serve.py").write_text("print('sidecar')\n")
        if kw.get("pin"):
            pin = {"schema": "ai-brainstem-kernel-pin/1", "files": {"brainstem.py": bf.bundle.sha256_file(src / "brainstem.py")}}
            if kw["pin"] == "wrong":
                pin["files"]["brainstem.py"] = "0" * 64
            (src / "kernel.json").write_text(json.dumps(pin))
        return src, bf.freeze(src, base / f"{name}.snapshot.tar.gz", sidecars=[side])

    def _bundle(self, snap):
        with tarfile.open(snap) as t:
            return json.load(t.extractfile("bundle.json")), t.getnames()

    def test_every_part_is_hashed_and_the_sidecar_travels(self):
        _, snap = self._snap("plain")
        b, names = self._bundle(snap)
        self.assertEqual(b["schema"], "brainfreeze-bundle/1")
        self.assertEqual(b["kernel"]["pinned_by"], "freeze")
        self.assertIn("brainstem.py", b["kernel"]["pin"]["files"])
        self.assertIn("agents/router_agent.py", b["agents"])
        self.assertNotIn("agents/router_agent.py", b["kernel"]["pin"]["files"])
        self.assertEqual(b["sidecars"][0]["path"], "sidecars/sidecar")
        self.assertIn("sidecars/sidecar/serve.py", names)
        self.assertFalse(any(".copilot_token" in n for n in names))

    def test_a_distro_kernel_is_pinned_by_its_kernel_json(self):
        _, snap = self._snap("distro", pin="right")
        self.assertEqual(self._bundle(snap)[0]["kernel"]["pinned_by"], "kernel.json")

    def test_a_distro_with_a_patched_kernel_is_not_frozen(self):
        with self.assertRaises(bf.ThrowawayError) as e:
            self._snap("patched", pin="wrong")
        self.assertIn("kernel.json pin", str(e.exception))

    def test_verify_names_the_changed_file(self):
        _, snap = self._snap("verify")
        root = Path(tempfile.mkdtemp(dir=TMP))
        with tarfile.open(snap) as t:
            t.extractall(root)
        b = json.loads((root / "bundle.json").read_text())
        bf.bundle.verify(root, b)                                    # untouched: passes
        for rel, words in (("rapp_brainstem/brainstem.py", "kernel file brainstem.py"),
                           ("rapp_brainstem/agents/router_agent.py", "agent file agents/router_agent.py"),
                           ("sidecars/sidecar/serve.py", "sidecar sidecar file serve.py")):
            p = root / rel
            keep = p.read_bytes()
            p.write_bytes(keep + b"#")
            with self.assertRaises(bf.bundle.BundleError) as e:
                bf.bundle.verify(root, b)
            self.assertIn(words, str(e.exception))
            p.write_bytes(keep)

    def test_run_file_refuses_a_changed_payload_and_inspects(self):
        import subprocess
        _, snap = self._snap("runfile")
        run = bf.pack(snap)
        ok = subprocess.run([sys.executable, str(run), "--inspect"], capture_output=True, text=True)
        self.assertIn("sidecar   sidecar", ok.stdout)
        text = run.read_text()
        i = text.index('PAYLOAD = (') + 40
        while not text[i].isalnum():
            i += 1
        run.write_text(text[:i] + ("A" if text[i] != "A" else "B") + text[i + 1:])
        bad = subprocess.run([sys.executable, str(run), "--inspect"], capture_output=True, text=True)
        self.assertIn("changed after it was packed", bad.stderr)


class SignAndUpdateTests(unittest.TestCase):
    def setUp(self):
        import subprocess
        self.base = Path(tempfile.mkdtemp(dir=TMP))
        self.key = self.base / "key"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "test", "-f", str(self.key)], check=True)
        self.allowed = self.base / "allowed_signers"
        pub = " ".join((self.base / "key.pub").read_text().split()[:2])
        self.allowed.write_text(f'tester namespaces="brainfreeze" {pub}\n')
        self.src = make_brainstem(self.base)
        self.catalog = self.base / "catalog"
        self.catalog.mkdir()

    def test_sign_verify_and_reject_an_edit(self):
        snap = bf.freeze(self.src, self.catalog / "v1.snapshot.tar.gz")
        bf.lineage.sign(snap, self.key, "tester")
        data = snap.read_bytes()
        self.assertEqual(bf.lineage.check_signature(data, self.allowed)[0], "verified")
        # a different signer name than the allowed key's
        bf.lineage.sign(snap, self.key, "someone-else", out=self.base / "other.snapshot.tar.gz")
        self.assertNotEqual(bf.lineage.check_signature((self.base / "other.snapshot.tar.gz").read_bytes(), self.allowed)[0], "verified")
        # bundle.json edited after signing
        out = io.BytesIO()
        with tarfile.open(fileobj=io.BytesIO(data)) as t, tarfile.open(fileobj=out, mode="w:gz") as o:
            for m in t.getmembers():
                body = t.extractfile(m).read() if m.isfile() else None
                if m.name == "bundle.json":
                    body = body.replace(b"brainfreeze-bundle/1", b"brainfreeze-bundle/9")
                    m.size = len(body)
                o.addfile(m, io.BytesIO(body) if body is not None else None)
        self.assertEqual(bf.lineage.check_signature(out.getvalue(), self.allowed)[0], "bad")

    def test_update_finds_children_and_says_what_changed(self):
        v1 = bf.freeze(self.src, self.catalog / "v1.snapshot.tar.gz")
        (self.src / "agents" / "new_agent.py").write_text("class NewAgent: pass\n")
        (self.src / ".brainstem_data" / "shared_memories" / "memory.json").write_text('{"limit": 10000, "more": 1}')
        parent = {"snapshot_sha256": bf.lineage.sha256(v1.read_bytes())}
        v2 = bf.freeze(self.src, self.catalog / "v2.snapshot.tar.gz", extra={"parent": parent})
        bf.pack(v2)                                                      # the same v2 as a run file: counted once
        found = bf.lineage.find_updates(v1.read_bytes(), self.catalog)
        self.assertEqual([(n, d) for n, _, d in found], [("v2.brainstem.py", 1)] if found[0][0].endswith(".py")
                         else [("v2.snapshot.tar.gz", 1)])
        change = bf.lineage.describe_change(v1.read_bytes(), found[0][1])
        self.assertIn("agents added: new_agent.py", change)
        self.assertIn("memory: 1 -> 2 entries", change)
        self.assertEqual(bf.lineage.find_updates(v2.read_bytes(), self.catalog), [])


class LineageTests(unittest.TestCase):
    def test_parent_is_the_snapshot_or_egg_it_grew_from(self):
        tw = bf.Throwaway(name="lineage-test")
        tw.dir.mkdir(parents=True, exist_ok=True)
        self.assertIsNone(tw.parent())
        (tw.dir / "instance.json").write_text(json.dumps({"grown_from": "ab" * 32, "artifact": "rappid:x"}))
        self.assertEqual(tw.parent(), {"egg_address": "ab" * 32, "rappid": "rappid:x"})
        (tw.dir / "parent.json").write_text(json.dumps({"snapshot_sha256": "cd" * 32, "snapshot": "a.tar.gz"}))
        self.assertEqual(tw.parent()["snapshot_sha256"], "cd" * 32)


class PortReservationTests(unittest.TestCase):
    PORT = 7987

    def tearDown(self):
        bf._release_port(self.PORT)

    def test_one_owner_per_port(self):
        self.assertTrue(bf._reserve_port(self.PORT))
        self.assertFalse(bf._reserve_port(self.PORT))       # a second throwaway in this process
        bf._release_port(self.PORT)
        self.assertTrue(bf._reserve_port(self.PORT))

    def test_parallel_threads_get_distinct_ports(self):
        import threading
        won, lock = [], threading.Lock()

        def grab():
            if bf._reserve_port(self.PORT):
                with lock:
                    won.append(1)
        threads = [threading.Thread(target=grab) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(won), 1)

    def test_stale_reservation_is_taken_over(self):
        mark = bf.ROOT / ".ports" / str(self.PORT)
        mark.mkdir(parents=True, exist_ok=True)
        (mark / "pid").write_text("999999")                  # an owner that no longer exists
        self.assertTrue(bf._reserve_port(self.PORT))
        self.assertEqual((mark / "pid").read_text(), str(os.getpid()))


if __name__ == "__main__":
    unittest.main()
