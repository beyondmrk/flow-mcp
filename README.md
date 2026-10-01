# flow-mcp

A local MCP server that lets Claude drive [Google Flow](https://flow.google.com/) in a dedicated
Microsoft Edge window over the Chrome DevTools Protocol (CDP). It fires stills and clips, tracks
every fire in a run-state file, and downloads the results.

Unofficial. Not affiliated with or endorsed by Google. See [Caveats](#caveats) before you use it.

## What it is built to prevent

Flow clips cost credits, and a browser-driven pipeline fails in ways that spend them twice. The
bridge is a set of refusals around that:

- **A fire happens once.** Nothing is retried automatically. A fire whose result never arrived
  comes back `UNCONFIRMED` and stays pending; `flow_sweep` settles it. It is never re-fired.
- **Pending is written before the click.** `_flow_run.json` records the attempt first, so a crash
  or an MCP restart cannot lose track of a paid fire.
- **A paid clip needs a lint stamp.** `flow_fire_clip` refuses `LINT_REQUIRED` unless the manifest
  was linted and the prompt it is about to send is the prompt that was linted.
- **A committed line is not fired again.** `ALREADY_COMMITTED`, unless `reroll=True`.
- **One run per project, one job on the tab.** `RUN_LOCKED` and a browser-wide tab lock keep two
  processes from steering the same window.
- **The page is never reloaded while a job renders.** A reload kills in-flight generations, so
  `flow_sweep` refuses `IN_FLIGHT` and `flow_settle` waits on the current page.
- **A path fence.** Every local path must sit under `work_root`, checked after junctions and
  symlinks are resolved.

## Requirements

- Windows with Microsoft Edge (the only platform it has run on)
- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- A Google account with access to Flow

## Setup

1. `uv sync`
2. Copy `config.example.toml` to `config.toml` and set `work_root` to the folder that holds your
   projects. Without it every path is refused.
3. Register the server with Claude Code, in `.mcp.json`:

   ```json
   {
     "mcpServers": {
       "flow": {
         "command": "uv",
         "args": ["--directory", "C:\\path\\to\\flow-mcp", "run", "flow-mcp"]
       }
     }
   }
   ```

4. On the first tool call a dedicated Edge window opens with its own profile (`.edge-profile`,
   separate from your everyday Edge). Sign in to Google in that window once, by hand. The bridge
   never types credentials and never solves a CAPTCHA: it reports `LOGIN_REQUIRED` or `CAPTCHA`
   and waits for you.

Restart Claude Code after changing the bridge's code or config; the running server keeps the old
one.

## Tools

| Tool | Credits | What it does |
|---|---|---|
| `flow_status` | free | Reads the window: url, signed-in state, CAPTCHA, out-of-credits banner, credit balance. |
| `flow_open_project` | free | Opens (or creates) the Flow project by name and binds it to a local `project_dir`. |
| `flow_upload` | free | Uploads a local image into the project's assets; fires attach it by file stem. |
| `flow_fire_still` | 0 | One Image-mode still: Nano Banana 2, 9:16, with 1-3 attached references. |
| `flow_fire_clip` | **12 per output** | One 9:16 Video-mode clip off a start frame, on Omni 1.1 Flash. Count 1-2. |
| `flow_settle` | free | Collects fires made with `queue=True` once their media land. Never navigates mid-render. |
| `flow_sweep` | free | Reloads the bound project and settles pending or partial fires. Never fires. |
| `flow_download` | free | Downloads committed media at original size through the signed URL. |
| `flow_screenshot` | free | PNG of the Flow window, for debugging a `UI_CHANGED` error. |

Credit costs are what Flow showed when the bridge was last verified. Read the tool docstrings in
`src/flow_mcp/server.py` for every status a call can return.

## The project folder

```
<project_dir>/
  _flow_run.json            run state: every attempt, pending or committed (the bridge owns it)
  Creatives/
    <manifest>.json         your shot manifest: an object with a "shots" list
    _flow_lint.json         the lint stamp
  Elements/
    Stills/                 downloaded stills
    Clips/                  downloaded clips
```

## The lint stamp

The bridge does not lint anything. It only checks that something did. Before a paid clip it
requires `Creatives/_flow_lint.json` to hold:

- `manifest_sha256` equal to the sha256 of the manifest's current bytes,
- an entry for the line being fired with `status` of `PASS` or `WARN`,
- a `prompt_sha256` equal to the sha256 of the exact prompt text being sent.

Your own linter writes it once the manifest passes:

```python
from flow_mcp import stamp

stamp.write(project_dir, manifest_path, {
    "L01": {"status": "PASS", "prompt_sha256": stamp.prompt_sha(prompt_for_L01)},
})
```

Editing the manifest or the prompt afterwards invalidates the stamp. Stills never check it.

## Refusal codes

Every refusal is a `FlowError` with a stable code, listed in `src/flow_mcp/guards.py`. The ones you
will meet first:

| Code | Meaning |
|---|---|
| `PATH_REFUSED` | A path is outside `work_root`, under a denied root, or not a bare name. |
| `LOGIN_REQUIRED` / `CAPTCHA` | The window needs you. Sign in or tick the box by hand. |
| `OUT_OF_CREDITS` | Flow shows its out-of-credits banner. |
| `LINT_REQUIRED` | No valid lint stamp for this line and prompt. |
| `RUN_LOCKED` | Another run holds this project. |
| `ALREADY_COMMITTED` | The line already has a committed clip. `reroll=True` fires again and costs again. |
| `UNCONFIRMED` | The fire went out and no result was seen. Run `flow_sweep`. Do not re-fire. |
| `IN_FLIGHT` | Queued fires are rendering; a reload would kill them. Use `flow_settle`. |
| `UI_CHANGED` | A selector no longer matches. Flow's UI moved; see the notes below. |
| `BROWSER_DOWN` | The dedicated Edge cannot be reached over CDP. |

## Development

```
uv run pytest tests/
```

The suite runs against a fake page: no credits, no signed-in window. Tests marked `browser` launch
your local Edge, tests marked `live` need the signed-in window and are deselected by default, and
one test creates an NTFS junction, so the suite is Windows-only.

`tests/fixtures/NOTES.md` is the working log of how Flow's page and its `batchexecute` responses
were read: record shapes, selectors, and what has and has not been verified live. Start there when
`UI_CHANGED` appears. `scripts/capture.py` records fresh responses and `scripts/smoke.py` is a
0-credit end-to-end check against the live window.

## Caveats

- **It will break.** The bridge reads Flow's web UI and its internal responses, neither of which
  is a public API. Google changes both without notice. The selectors and record shapes here were
  read on 2026-09-29 and 2026-09-30.
- **It acts on your account.** Check Google's terms for Flow before automating your account with
  it. You are responsible for how you use it.
- **Clips spend real credits.** The refusals above reduce the ways that goes wrong; they do not
  remove them. A fire that Flow accepts and never lists comes back `LOST` with the spend unknown.
- **Clip waves are less proven than stills.** Queued stills have run live; several clips in
  flight at once have not been proven on a paid run.

## License

MIT. See [LICENSE](LICENSE).
