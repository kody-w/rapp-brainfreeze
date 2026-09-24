# What you can do with brainfreeze

A RAPP brainstem keeps its whole state on disk and in the conversation: engine code, agents, soul, memory,
chosen model, and the messages so far. brainfreeze captures all of that as one small file, about 160 KB
for a typical brainstem. Resuming it gives you a **clone of the brainstem at the moment it was frozen**,
on any machine with `python3` and `git`.

An `agent.py` is a capability you can drop in and share. A frozen brainstem is a whole working assistant,
in the middle of a conversation, that you can drop in and share.

Each use case below says whether it **works today** (with the commands), **works by hand** (you can do it
now, but nothing automates it yet), or is **not built yet**.

---

## Share and hand off

### Hand a live demo to the people who build it next
**Works today.** After a demo, freeze the brainstem. Whoever builds the real product resumes the demo exactly
as the audience saw it, then replays the conversation as an acceptance test.

```bash
python3 -m brainfreeze freeze ~/.brainstem/src/rapp_brainstem --run-file
python3 brainstem-<time>.brainstem.py --chat      # on their machine: picks up mid-conversation
python3 -m brainfreeze replay <handoff-kit>       # resend the messages, side-by-side report
```

### Move your brainstem between devices
**Works today.** Laptop to desktop, desktop to a VM, you to a colleague. The conversation, memory and agents
come along. Sign-in never does: each person signs in as themselves.

### Leave a working demo behind
**Works today.** Give someone the `.brainstem.py` file after a meeting. They run it and try the agent
themselves, signed in with their own GitHub Copilot.

### Workshops and training
**Works today.** Freeze a brainstem that's set up for the exercise. Every attendee starts from the same
moment, with the same agents, soul and starting conversation.

---

## Start clean, every time

### A fresh demo for every audience
**Works today.** Freeze a primed demo brainstem once. Resume a new copy for each meeting. Nothing one audience
said carries over into the next one's memory, and every demo starts from a known-good state.

```python
from brainfreeze import Throwaway
with Throwaway.thaw("primed-demo.snapshot.tar.gz") as bs:
    ...   # this meeting's copy; it disappears afterwards
```

### Save points
**Works by hand today; automatic save points are not built yet.** Freeze before anything risky, such as
installing an agent, switching models or editing the soul. If it goes wrong, resume the save point. The
next step is freezing automatically before those changes.

---

## Debug and test

### Reproducible bug reports
**Works today.** When something goes wrong, freeze it and attach the snapshot. A maintainer resumes the exact
state (engine, agents, memory, conversation) instead of trying to recreate it from a description.

### Test fixtures for agents
**Works today.** A snapshot plus a message is a repeatable test case. Resume it in CI, send the message, and
check which agents ran and what they returned.

```python
with Throwaway.thaw("fixture.snapshot.tar.gz") as bs:
    r = bs.chat("Route an invoice for $18,750")
    assert "InvoiceRouter" in r.agents_called
```

### Upgrade testing
**Not built yet.** Resume real snapshots, shared with permission, on a new engine version, to answer "does
the next release still run people's brainstems?" This needs a "resume with a different engine" option.

### Diffs between snapshots
**Not built yet.** Compare two snapshots to see what changed: memory, agents, soul, model. Useful as an audit
trail ("what did it know at that point?") and for debugging.

---

## Explore

### Branching
**Works by hand today.** Freeze at a decision point, resume it twice, and try two agents, souls or models side
by side from the same starting point.

```python
a = Throwaway.thaw("decision.snapshot.tar.gz").up()
b = Throwaway.thaw("decision.snapshot.tar.gz").up()
a.add_agent("approach_a_agent.py")
b.add_agent("approach_b_agent.py")
question = "Plan the rollout for next week."
print(a.chat(question).response, b.chat(question).response, sep="\n---\n")
a.down(); b.down()
```

To compare models instead, pick a different model in each copy's chat window: a snapshot carries the model
choice, and a choice made in the window takes priority over settings.

### Fan-out
**Works by hand today.** Resume one snapshot many times in parallel to explore different directions, then keep
the one that worked and freeze it.

---

## Scale

### Hibernation
**Works by hand today.** Keep one frozen brainstem per project, person or team as a file, and resume only the
one you need, in about 30 seconds. A hundred brainstems cost only the disk space.

### Local to cloud
**Not tested yet.** Resume a laptop snapshot in a cloud container, taking its state along from local to hosted.

### Ready-to-run brainstems in a registry
**Eggs work today; listing them in the registry is next.** Lay a template brainstem as a standard rapp/1
`organism` egg: agents, soul and optional memory, with no engine code. Anyone can hatch it onto their own
engine. The next step is listing these eggs in the RAPP Agent Registry next to single agents.

```bash
python3 -m brainfreeze egg tw-7097 --owner you --slug invoice-desk --no-memory
python3 -m brainfreeze up --egg you--invoice-desk.egg
```

### Brainstems as agents
**Not built yet.** An agent resumes a snapshot on its own port and passes questions to it, so specialist
brainstems can be plugged into one another.

---

## Before you share a snapshot

A snapshot runs its own engine code and carries the brainstem's memory and conversation. Treat it like the
data inside it:

- Share snapshots only with people you'd share that conversation and memory with.
- Only resume snapshots from people you trust, the same as running any code they send you.
- Freeze with `include_memory=False` when the memory holds things that shouldn't travel.
- Sign-in, secrets and `.env` values are never included.
