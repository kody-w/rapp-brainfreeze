# bundle.json — what a frozen brainstem carries, pinned by hash

Status: draft 1 (6 Oct 2026). Schema id `brainfreeze-bundle/1`.

A snapshot (and the one-file `.brainstem.py` made from it) carries a `bundle.json` beside `state.json`. It lists
every part of the frozen brainstem by SHA-256 and says how to bring the add-ons up around the kernel. A thaw
checks every hash before anything starts and refuses on the first mismatch, naming the file.

The three userland routes map onto it directly:

| Route | In `bundle.json` | At thaw |
|---|---|---|
| Agent files dropped into `agents/` | `agents` | hashes checked, loaded by the kernel as usual |
| A sidecar that talks to the kernel only through `/chat` and `/health` | `sidecars[]` | hashes checked, started after the kernel answers, health-checked, stopped with it |
| A distro that pins the unchanged kernel (`kernel.json`) and adds organs | `kernel.pin` + `organs` | the kernel is checked against the pin, so a distro can never ship a patched kernel |

## Shape

```json
{
  "schema": "brainfreeze-bundle/1",
  "kernel": {
    "pin": {
      "schema": "ai-brainstem-kernel-pin/1",
      "source": "https://github.com/kody-w/rapp-installer",
      "commit": "49db80c8c6b6caa7647369beaf477d374a8f293c",
      "version": "0.6.16",
      "files": { "brainstem.py": "bd55a7f0…", "local_storage.py": "c38667c1…" }
    },
    "pinned_by": "kernel.json"
  },
  "agents":  { "agents/invoice_router_agent.py": "<sha256>" },
  "organs":  { "organs/skills_organ.py": "<sha256>" },
  "sidecars": [
    {
      "name": "brainstem-mcp",
      "from": { "repo": "https://github.com/kody-w/brainstem-mcp", "commit": "fb50745…", "path": "plugins/brainstem" },
      "path": "sidecars/brainstem-mcp",
      "files": { "launch.py": "<sha256>", "mcp_server.py": "<sha256>" },
      "kind": "service",
      "run": ["{python}", "launch.py", "--http", "{port}"],
      "env": { "BRAINSTEM_URL": "{kernel_url}" },
      "ready": { "tcp": "{port}" }
    }
  ],
  "parent": { "snapshot_sha256": "<sha256 of the snapshot this one was re-frozen from>" }
}
```

- `kernel.pin` is a `kernel.json` as-is when the brainstem is a distro (`pinned_by: "kernel.json"`). For a plain
  brainstem it is made at freeze time from the engine files as they ran (`pinned_by: "freeze"`): then the pin
  proves the file was not altered after the freeze, not that the engine equals the grail.
- `agents`, `organs` and each sidecar's `files` are every file under those folders, by path relative to the
  brainstem (sidecars: relative to the sidecar's own folder).
- Placeholders in `run`/`env`/`ready`: `{python}` (the interpreter running the bootstrap), `{port}` (a free port
  reserved for the sidecar), `{kernel_url}` (the thawed kernel's URL).
- `kind: "service"` sidecars are started and stopped by the bootstrap. `kind: "host-plugin"` sidecars are
  registered with a host on request (`--install-plugins`), never started silently.
- `parent` moves here from `state.json` (both are written while readers move over).

## Signing (`bundle.sig`)

`bundle.json` is signed with an SSH key (`ssh-keygen -Y sign -n brainfreeze`), the same keys GitHub already
publishes at `https://github.com/<login>.keys`. A signature names its signer as a GitHub login; a reader checks it
against that login's published keys (or a local `allowed_signers`), with nothing new to install. Because
`bundle.json` hashes every file, one signature covers the whole brainstem.

## Lineage and updates

`parent` chains freezes. `python3 x.brainstem.py --update <catalog>` looks in a catalog (a folder, or an
`index.json` URL listing snapshots with their SHA-256, parent and signer) for children of this file's snapshot,
and prints what changed: agents added, removed or edited, memory entries, kernel version, and who signed it.

## Not in draft 1

Thin files (fetching shared parts by hash from a store) use the same hashes and come later; draft 1 files are
always whole.
