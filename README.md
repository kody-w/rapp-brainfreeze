# brainfreeze

<!-- rapp1:network-header:start -->
[![RAPP/1](https://kody-w.github.io/rapp-hive-public/portfolio/badges/rapp-brainfreeze.svg)](https://github.com/kody-w/rapp-hive-public/blob/main/portfolio/repos/rapp-brainfreeze.md) · **New to RAPP?** [Start here: get your Brainstem →](https://github.com/kody-w/rapp-installer#start-here)
<!-- rapp1:network-header:end -->

Freeze a running RAPP brainstem and resume it anywhere, with the same engine, agents, soul, memory, model
and conversation, picking up exactly where it left off. Standard library only.

```bash
python3 -m brainfreeze freeze ~/.brainstem/src/rapp_brainstem --run-file
# -> brainstem-<time>.snapshot.tar.gz  and  brainstem-<time>.brainstem.py

python3 brainstem-<time>.brainstem.py          # on any machine: resumes it, opens the browser
python3 brainstem-<time>.brainstem.py --chat   # or continue the conversation in the terminal
```

The `.brainstem.py` run file is self-bootstrapping. It needs only `python3` and `git`, with no brainstem
install. On first use it sets up the Python packages in `~/.brainfreeze/.venv` and asks for a one-time
GitHub Copilot device sign-in, cached in `~/.brainfreeze/.copilot_token`.

## Why it's useful

An `agent.py` is a capability you can drop in and share. A frozen brainstem is a whole working assistant,
in the middle of a conversation, that you can drop in and share. For example:

- **Hand off a live demo.** Whoever builds the product resumes it exactly as the audience saw it, then
  replays the conversation as an acceptance test.
- **Start every demo clean.** Resume a fresh copy of a primed brainstem for each audience, so nothing
  carries over from one to the next.
- **Reproducible bug reports.** Attach a snapshot and the maintainer resumes the exact state.
- **Move between devices** or hand your brainstem to a colleague, with nothing installed on their side.
- **Branch and compare.** Resume one moment twice and try two agents, souls or models side by side.

[USE_CASES.md](USE_CASES.md) has the full list: what works today, with commands, and what isn't built yet.

## What a snapshot holds

| Travels | Never travels |
|---|---|
| Engine code as it ran (local changes included), with version and commit | Sign-in (`.copilot_token`, `.copilot_session`) |
| Agents (`agents/`, including subfolders) | Per-install secret (`.brainstem_secret`) |
| Soul file | `.env` values: only the setting **names** are recorded |
| Memory (`.brainstem_data/`) | Logs and caches (`.brainstem_book.json`, `__pycache__`, `.git`, venvs) |
| Chosen model | |
| Conversation and session id | |

Every snapshot carries a **`bundle.json`** ([docs/BUNDLE.md](docs/BUNDLE.md)): each file by SHA-256, the kernel
pin (a distro's own `kernel.json`, or the engine as frozen), and any **sidecars** bundled with `--with`. A thaw
checks every hash before anything starts and refuses on the first changed file, by name; a run file also checks
its own payload, so a single changed byte stops it. Service sidecars start beside the kernel on their own port and
stop with it.

**Signed and versioned.** `sign` signs `bundle.json` (and so every file) with an SSH key as a GitHub login;
`verify` and every thaw check it against an `allowed_signers` file (`$BRAINFREEZE_ALLOWED_SIGNERS`, or
`$BRAINFREEZE_ROOT/allowed_signers`) or, failing that, the login's keys at `github.com/<login>.keys`. A signature
that does not match stops the thaw. `--update <catalog>` on a run file follows `parent` links through a folder or
an `index.json` URL and prints each newer version with its signer and what changed (agents, soul, memory, kernel,
sidecars).

A snapshot of a throwaway also records its **parent**: the SHA-256 of the snapshot it was thawed from, or
the address of the egg it hatched from. Re-freezing after a change makes a child that points at the exact
freeze it came from (`state.json` → `parent`).

Throwaways started at the same moment never share a port: each one reserves its port atomically
(`$BRAINFREEZE_ROOT/.ports/<port>`) before it starts, and a reservation left by a process that is gone is
taken over.

The brainstem keeps nothing important only in the running process, so freezing the disk state and the
conversation is a full freeze. The web UI keeps the conversation in the browser. A resumed brainstem
writes it in the UI's Import format, so one click on **Import** restores it on screen.

## Python

```python
from brainfreeze import Throwaway, freeze, pack, replay

# freeze any brainstem folder, with the conversation you have
snap = freeze("~/.brainstem/src/rapp_brainstem", "demo.snapshot.tar.gz", history=turns, session_id=sid)
run_file = pack(snap)

# resume it
with Throwaway.thaw("demo.snapshot.tar.gz") as bs:
    print(len(bs.history), "messages restored")
    print(bs.chat("Where were we?").response)
    bs.freeze("demo-later.snapshot.tar.gz")      # and freeze it again to hand it on

# disposable brainstems for testing
with Throwaway(bare=True, agents=["my_agent.py"], env={"KEY": "value"}) as bs:
    r = bs.chat("Hello")
    print(r.response, r.agents_called)
```

`Throwaway` options: `source` (`"grail"`, `"canary"`, a checkout path, or a snapshot file), `ref` (pin a
commit), `port` (default: first free from 7097), `bare` (only your agents), `agents`, `soul`, `env`,
`keep`, `ui_history_cap` (resend history like the web UI: last 40 messages / 60k characters).

## Command line

```bash
python3 -m brainfreeze up [--from grail|canary|<path>] [--ref <commit>] [--bare] [--agent f.py]... [--env K=V]...
python3 -m brainfreeze up --snapshot demo.snapshot.tar.gz     # resume a snapshot and keep it running
python3 -m brainfreeze chat tw-7097 "Hello"                    # continues that throwaway's conversation
python3 -m brainfreeze freeze tw-7097 --run-file               # freeze a throwaway (or a brainstem folder)
python3 -m brainfreeze pack demo.snapshot.tar.gz               # snapshot -> self-bootstrapping .py
python3 -m brainfreeze egg tw-7097 --owner you --slug my-desk  # rapp/1 organism egg (+ session egg)
python3 -m brainfreeze up --egg you--my-desk.egg               # hatch an egg onto the grail engine it expects
python3 -m brainfreeze egg-upgrade old.egg                     # say what an .egg is; convert older brainstem eggs
python3 -m brainfreeze freeze ~/my-distro --with ~/brainstem-mcp --run-file   # bundle a sidecar; kernel pinned
python3 demo.brainstem.py --inspect                            # what is inside, checked, without running it
python3 -m brainfreeze sign demo.snapshot.tar.gz --as <login> --run-file   # sign bundle.json with your SSH key
python3 -m brainfreeze verify demo.brainstem.py                # hashes + signature
python3 demo.brainstem.py --update ~/catalog                   # newer versions of this brainstem, and what changed
python3 -m brainfreeze list
python3 -m brainfreeze down tw-7097                            # or: down all
```

`pip install -e .` adds a `brainfreeze` command.

## Eggs: brainstems for the registry

A snapshot is an exact clone, engine included, for private hand-offs. For publishing, brainfreeze lays a
standard **rapp/1 `organism` egg** instead: the brainstem's agents, soul and (optionally) memory, plus the
engine version it expects, and **never the engine code itself**. An egg hatches onto the receiver's own
engine, so nobody runs engine code downloaded from a registry. The conversation, if any, goes in a
separate `session` egg.

```bash
python3 -m brainfreeze egg tw-7097 --owner <your-github-login> --slug invoice-desk --out eggs/
# -> eggs/<owner>--invoice-desk.egg  (+ .session.egg with the conversation)

python3 -m brainfreeze up --egg eggs/<owner>--invoice-desk.egg [--session eggs/<owner>--invoice-desk.session.egg]
python3 -m brainfreeze up --egg https://raw.githubusercontent.com/kody-w/RAR/main/eggs/@kody-w/invoice-desk.egg   # straight from RAR
```

```python
from brainfreeze import Throwaway, lay_egg
laid = lay_egg("~/.brainstem/src/rapp_brainstem", "eggs/", owner="you", slug="invoice-desk",
               include_memory=False)
with Throwaway.hatch(laid["organism"]) as bs:
    print(bs.chat("Route an invoice for $22,400").response)
```

Eggs follow the RAPP egg spec ([rapp-1 SPEC §9](https://github.com/kody-w/rapp-1/blob/main/SPEC.md)):
byte-reproducible, every file hashed, a minted `rappid`. Each egg is checked with the RAPP reference
implementation before it is written, and again before it hatches. A hatch mints a fresh instance identity
and records the egg it `grown_from`. The reference implementation is vendored verbatim as
`brainfreeze/rapp1.py`; `rapp1.vendor.json` records its source commit and checksum.

| | Snapshot (`freeze`) | Egg (`egg`) |
|---|---|---|
| For | Private hand-offs, device moves, bug repros | Publishing and sharing templates |
| Engine code | Included, exactly as it ran | Never included; hatches onto the receiver's engine |
| Conversation | Included | Optional separate `session` egg |
| Format | `.snapshot.tar.gz` / self-bootstrapping `.brainstem.py` | rapp/1 `organism` + `session` eggs |

### Older eggs

Several tools laid `.egg` files before rapp/1, and other projects use the extension for things that are
not brainstems. `egg-upgrade` says what a file is and converts the brainstem-shaped ones into verified
rapp/1 organism eggs; hatching a wrong file names what it is instead of failing to parse it.

| Format | What it is | `egg-upgrade` |
|---|---|---|
| `brainstem-egg/2.1` twin, `2.2-rapplication`, `2.3-cubby` | Older brainstem exports | Converts soul, agents and memory (old `{"facts": [...]}` memory becomes brainstem memory); engine, UI and data folders are left out and listed |
| `rapp-egg/1.0` rapplication | One agent plus a web UI | Converts the agent with a written soul; for the agent plus its UI use `brainfreeze-studio rapplication` |
| `rapp-application/1.0` twin | A twin with a soul and agents | Converts soul and agents |
| `hologram-cartridge`, Rappter moments and genomes, neighborhood invites, Claude Code agent bundles | Not brainstems | Named, with where to open them |

```bash
python3 -m brainfreeze egg-upgrade ~/Downloads/grandma-rose.egg --check    # what is it?
python3 -m brainfreeze egg-upgrade ~/Downloads/grandma-rose.egg --out eggs/ # -> eggs/<owner>--grandma-rose.egg
```

## Handoff kits

A handoff kit is a folder an agent writes at the end of a demo, so someone else can resume or reproduce
it without installing a brainstem:

```
manifest.json            brainstem version/source/commit, model, agent SHA-256s, settings the agents read
agents/                  the exact agent files the demo ran
soul.md                  the demo's soul file
transcript.json          the conversation: [{"role": "user"|"assistant", "content": "..."}]
demo.snapshot.tar.gz     optional: a brainfreeze snapshot of the demo brainstem
resume-demo.brainstem.py optional: its self-bootstrapping run file
brainfreeze/             optional: a copy of this SDK, so the kit runs with python3 alone
```

```bash
cd <kit>
python3 resume-demo.brainstem.py          # the demo brainstem, live, where the demo left off
python3 -m brainfreeze replay .           # rebuild from the manifest, resend the user's messages,
                                          # write a side-by-side report to replays/
```

The replay is an acceptance test: send the same messages to whatever gets built from the demo and compare
which agents ran and what the answers covered. Model wording varies, so compare behavior, not text.

## Tests

```bash
python3 -m unittest discover -s tests -v    # offline: freeze, pack, safety checks; no network, no model
```

## License

MIT. See [LICENSE](LICENSE).
