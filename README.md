# brainfreeze

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

## What a snapshot holds

| Travels | Never travels |
|---|---|
| Engine code as it ran (local changes included), with version and commit | Sign-in (`.copilot_token`, `.copilot_session`) |
| Agents (`agents/`, including subfolders) | Per-install secret (`.brainstem_secret`) |
| Soul file | `.env` values: only the setting **names** are recorded |
| Memory (`.brainstem_data/`) | Logs and caches (`.brainstem_book.json`, `__pycache__`, `.git`, venvs) |
| Chosen model | |
| Conversation and session id | |

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
python3 -m brainfreeze list
python3 -m brainfreeze down tw-7097                            # or: down all
```

`pip install -e .` adds a `brainfreeze` command.

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

Apache-2.0. See [LICENSE](LICENSE).
