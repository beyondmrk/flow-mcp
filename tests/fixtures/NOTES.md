# Flow batchexecute + selector notes (live, 2026-09-29, build boq_labs-ai-sandbox-frontend_20260927.05_p1)

Evidence: one free Image-mode still (Nano Banana 2, x1, 16:9, prompt `a single red apple on a wooden table`)
fired in the new project `_bridge_smoke` (`12144f2f-7426-4e1f-97bb-f3837a9efe62`). 0 credits spent
(balance 14 before and after). Raw captures live in `raw/` (gitignored). Committed fixtures are
re-encoded from raw with signed-URL query strings and `contribution.fife.usercontent.google.com`
URLs replaced by `REDACTED`; `Zzl0ze` payload[3] (69 unrelated library assets from other projects)
was emptied.

All app traffic is `POST https://flow.google.com/_/AiSandboxAngularFrontend/data/batchexecute?rpcids=<id>`.
No other XHR/fetch host carries Flow data (only analytics/ogads).

## The media record (shared shape)

Same list in `Zzl0ze` payload[2][i], `as29s` payload, and `ogiZ0b` payload[0][i]:

| path | meaning |
|---|---|
| `[0]` | **mediaId** (uuid) — also the uuid in `flow-content.google/image/<uuid>` |
| `[1]` | projectId (absent/null in `ogiZ0b`) |
| `[2]` | workflowId — the id in the detail URL `/project/<pid>/edit/<workflowId>` |
| `[5][6][2][0][0]` | prompt (duplicate; not present in `ogiZ0b`) |
| `[5][10]` | media URL (`Zzl0ze`: fife URL, not fetchable; `as29s`: signed flow-content URL) |
| `[5][13]` | original byte size (698310 for the apple = exact download size) |
| `[6][0][7]` | **prompt text** (verbatim as typed) |
| `[6][0][8]` | model **enum** `29` for Nano Banana 2 — no model string on the item |
| `[6][0][13]` | media URL (signed flow-content in `ogiZ0b`/`as29s`, fife in `Zzl0ze`) |
| `[6][0][14]` | `3` on the image still (video value unverified) |
| `[6][2]` | `[w, h]` = `[1376, 768]` |

## Per rpcid

- **`ogiZ0b` — generate result.** Arrives once, ~21 s after clicking Start generation (the request is
  held open until the image exists; not seen on reload). payload[0] = list of media records (with
  signed URL), payload[1] = workflows: `[workflowId, null, null, [title "Red apple on wooden table",
  [created s, ns], null, null, mediaId, <client uuid>, [finished s, ns]], projectId]`.
- **`Zzl0ze` — project contents** (every project page load). payload[1] = workflows (as above),
  payload[2] = media records **with prompt per item** (`[6][0][7]`), payload[3] = account library
  assets (other projects), payload[7] = project default model settings (`abra`, `narwhal_display`).
- **`as29s` — media detail** (opening a tile's detail view; also fires on reload of a detail URL).
  payload = one media record with a fresh signed URL.
- **`nzlxg` — credits**, polled every ~30 s. `[2696, 8, 9, 3, 2682, 14, [resetTs s, ns]]`;
  `[5]` = remaining credits (14 = the Account-details popover; = `[0] - [4]`). Unchanged by the fire.
- `WuwhI` → `[]` fires at submit. `HTrJv` = model catalog (`abra` = "Omni 1.1 Flash",
  `GEM_PIX_2`, `NARWHAL`, veo_* keys). `yBhWQ` = enabled model families. `ngNC2`/`jHPbke`/`o8DA4`
  = project title get/set. `UpteDb` = project list. Others (`LPzVkd`, `mrlkwd`, `qJcgMc`, `DTaVef`,
  `NfrxTb`, `ve2Lsc`, `KV2T2d`, `Yizz8d`, `cPZSdc`, `o30O0e`, `tRARke`) carry no media;
  `o30O0e` contains the account email (never commit it).

**Step-4 decision: YES — prompt text per media item** at `[6][0][7]` in `Zzl0ze` (and `ogiZ0b`,
`as29s`). mediaId `[0]`. Model: no string per item (enum only), so `Media.model` is None for stills.

## Kind (image vs video)

Image = signed URL path `flow-content.google/image/` or `[6][0][14] == 3` (both observed on the
Nano Banana 2 still). **Video classification is UNVERIFIED** — to be confirmed in the first paid
check. Expected marker: a `flow-content.google/video/<uuid>` signed URL (per the 2026-09-19
notes), but `flow_extract` does NOT act on it: without a positive image signal, kind is `"unknown"`.

## What `Media` carries for Task 6 (fix round 1)

- `workflow_id` = `[2]` (all three rpcs) — the id for the detail URL `/project/<pid>/edit/<workflowId>`.
- `url` = `[6][0][13]` **only when it is a signed `https://flow-content.google/...` URL**
  (`ogiZ0b`, `as29s`). `Zzl0ze` carries a fife URL at that path which returns 400, so it is
  ignored there. The Ledger fills `url` from a later record and refreshes it when a newer record
  brings a different signed URL (signatures expire; `Expires` ≈ 6 h after minting).
- `model` is scanned from non-prompt fields only (all prompt copies are removed before the scan).

## Fetching bytes (tested on the apple)

| route | result |
|---|---|
| signed `flow-content.google/image/<uuid>?Expires&KeyName&Signature` from `ogiZ0b`/`as29s`, via `page.request.get` | **200, image/jpeg, 698310 B, 1376x768 — the original** |
| grid tile `flow.google.com/asb/<token>=s0`, via `page.request.get` | 200, image/jpeg, 136663 B, 1376x768 — full dims but re-encoded |
| fife URL from `Zzl0ze` (raw or `=s0`) | 400 |
| Detail view `button[aria-label="Download media"]` → menu `1K Original size` | `download` event fires; `suggested_filename` = `Red_apple_on_wooden_table_<yyyymmddhhmmss>.jpg`; 698310 B (identical to signed). Menu also offers `2K Upscaled`, `4K Upscaled` — never click those |

Use the signed URL (harvest from `ogiZ0b` at fire or `as29s` on detail open). Clip fetch: unverified.

## Selectors (each confirmed with `locator.count()` on the live project page)

| thing | selector / signal | live |
|---|---|---|
| Agent pill | `button.agent-mode-chip`, `aria-pressed="false"` = OFF. `button:text-is("Agent")` returns **0** (label is in a child span). The chip was not in the DOM right after project creation; it appeared later | 1 |
| Settings chip | `button[aria-label="Settings trigger"]`; Image: `🍌 Nano Banana 2 crop_16_9 x1`; Video: `Video · 720p · 8s crop_16_9 x1` | 1 |
| Settings panel toggles | `button.mat-button-toggle-button`, selected = `aria-checked="true"`; texts `image Image`, `videocam Video`, `crop_free Frames`, `chrome_extension Ingredients`, aspect `crop_16_9 16:9`… , counts `x1`–`x4` (match `/^\s*x1\s*$/`; `:text-is('x1')` times out) | 11 (Image) |
| Video-only | durations `4s` `6s` `8s` `10s`, resolutions `360p` `720p`, model `Omni 1.1 Flash`; cost 12 credits for 720p·8s·x1 | seen read-only |
| Aspect (Image mode) | same toggle group, `role="radio"`, selected = `aria-checked="true"`. 9:16 option: `button.mat-button-toggle-button` with text `crop_9_16 9:16` (match `/crop_9_16\s*9:16/`). Selecting it flips 16:9 to `false`, the chip becomes `🍌 Nano Banana 2 crop_9_16 x1`, cost stays 0. Read live 2026-09-29, then restored to 16:9. **Post-fire check:** the media record's `[6][2]` should read `[768, 1376]` for 9:16 (observed `[1376, 768]` for the 16:9 apple) | 1 |
| Cost | overlay text `Generating will use N credits`; `a.credit-cost-link` = `N credits` | 1 |
| Attach / picker open | `button[aria-label="Add ingredients to the prompt box"]` (text `add`, not `add_2`) | 1 |
| Picker search | `input[aria-label="Search assets"]` | 1 |
| Picker confirm | button text `Add to prompt` (match `/^add to prompt$/i`) | 1 |
| Fire | `button[aria-label="Start generation"]` (text is only `arrow_forward`; `/arrow_forward\s*Create/` = **0**) | 1 |
| Prompt editor | `div.ProseMirror[contenteditable="true"]`, width 560 | 1 |
| Project title | `input[aria-label="Editable text"]` (fill + Enter renames) | 1 |
| New project (home) | `button:has-text("New project")` | 1 |
| Download | detail view `button[aria-label="Download media"]` → `[role=menuitem]` `1K Original size` | 1 |
| Credits balance | `[aria-label="Account details"]` popover: `14 Google Flow credits`; or `nzlxg[5]`. Escape does NOT close this popover and its backdrop blocks every later click; close it with `button[aria-label="Close account panel"]` | — |
| Out of credits | text `out of Google Flow` | 0 (not in state) |
| CAPTCHA / signed out | URL contains `google.com/sorry` / URL on `accounts.google.com` | not encountered |

## Observations (no policy encoded)

- **Agent pill may be absent in a fresh project.** Right after `New project` → fire, no
  `button.agent-mode-chip` (and no element with text "Agent") was in the DOM. It was observed
  later in the same project with `aria-pressed="false"`. What to do when it is absent is the operator's
  decision; nothing in the code decides it.
- The settings panel sometimes needs a second click to open (retry with ~4 s waits worked).

## Task 7 selector pass (live, read-only probes on port 9333, 2026-09-29)

Every selector Task 6 left UNVERIFIED was read on the live `_bridge_smoke` page before the smoke.

| constant | was | now (live) | notes |
|---|---|---|---|
| `MODEL_TRIGGER` | `button[aria-haspopup="listbox"]` (0) | `button[aria-label="Select model family"]` (1, inside the settings popover) | `aria-haspopup="menu"`; text `🍌 Nano Banana 2` + icon ligature `arrow_drop_down` (stripped before the label match) |
| `MODEL_OPTION` | `[role="listbox"] [role="option"]` (0) | `[role="menu"] [role="menuitem"]` (3 in Image mode) | `🍌 Nano Banana Pro`, `🍌 Nano Banana 2`, `🍌 Nano Banana 2 Lite`; match `(^|\s)<label>\s*$` so "2" never hits "2 Lite". Video-mode labels NOT read (mode was never switched to Video) |
| `MODE_RE` | `image\s+Image` | `image\s*Image` | toggle textContent is `imageImage` / `videocamVideo` (no space); `\s+` matched 0 and failed smoke run 1 |
| settings close | chip click | `Escape` | Escape closed the popover (verified); chip-click toggle not verified |
| `PICKER_FILE_INPUT` | `input[type="file"]` | removed | no file input exists anywhere in the DOM. Upload = picker button `Upload media` -> native file chooser (Playwright `expect_file_chooser`, works over CDP, `multiple`) |
| `PICKER_ROW` | `[role="dialog"] [role="option"]` (0) | `[aria-label="Asset list"] [role="option"]` | picker is a `cdk-overlay-pane`, not a dialog; rows are `button.asset-item`, text `<file or title> / Image` |
| `PICKER_ROW_ACTIVE` | - | class `asset-item-active` | the first / lone search hit is pre-selected; **clicking a selected row deselects it** (preview disappears) |
| `PICKER_PREVIEW` | `[role="dialog"] img...` (0) | `img.detail-preview-image` | alt `Preview of <title>`; the image usually never loads (0x0 box) so it is never "visible" - attach waits on the alt instead |
| uploading marker | - | row text contains `Uploading` | textContent starts with an icon ligature, so the marker is NOT anchored at `^`. Lasts ~7 s, ends when rpc `maseQ` returns |
| `PROMPT_BAR_THUMB` | `.prompt-bar img` (0) | `flow-base-prompt-box img.chip-image` | alt `Ingredient image`, inside `button[aria-label="Ingredient"]`; reload clears it |
| `PROJECT_TILE` | `a[href*="/project/"]` + text filter | `flow-project-card` filtered by `.project-title-label` (text `<name> edit`), click `a[aria-label="Open project"]` | the anchor has NO text (old filter matched 0 -> would have created a duplicate project). The card also holds `button[aria-label="Delete project"]` - never click the card itself. Grid renders seconds after goto; wait for a card before counting |

### Picker listing lag (drives `upload()`)

The picker's asset list is fetched once per page load. A fresh upload shows immediately in the
page that uploaded it, but a reload within ~10 s may NOT list it yet (server listing lags); a later
reload does. `upload()` therefore reloads + searches until a fresh listing has the asset (<= 120 s,
observed ~21 s total) and only then returns. Upload the same file twice = two rows = attach refuses
(`REF_NOT_ATTACHED`, exactly-one rule) - never re-upload a name that is already in the project.

### Other

- `nzlxg` did not arrive within 40 s on the first smoke run; the smoke falls back to the Account
  details popover (`N Google Flow credits`) and closes it with `Close account panel`.
- The downloaded still is JPEG bytes (Flow serves `image/jpeg`) written under the run-state name
  `..._line01.png`.
- 9:16 confirmed on the record: smoke still `[6][2]` = `[768, 1376]` (`aspect_checked: true`).

## Failed / refused generations (NOT yet captured)

No failed or policy-refused generation has been captured yet. The bridge assumes a real result
always carries a media URL at `[6][0][13]` (signed in `ogiZ0b`/`as29s`, fife in `Zzl0ze` - the raw
`Zzl0ze` capture has one; the committed fixture shows `REDACTED`), and treats a record that never
carries one as `NO_MEDIA_URL` (credits unknown, not a take). **Capture a real failure fixture in the
paid smoke** and confirm: does a failed record carry no URL, a placeholder URL, or an error field?
Adjust `Media.has_url` / `Service._verdict` to match.

### Task 4 live check (2026-09-30): multi-ref still + flow_fire stills

- **Picker search is a substring filter.** Searching `ref` lists `ref2.png` too, and the first hit is
  pre-selected. `attach()` now keeps only rows whose title is the name or whose file stem is the name,
  and checks the preview alt the same way (`Preview of ref2.png` is not `ref`). `_listed_after_reload`
  also counts only exact rows.
- **Multi-ref works live.** `attach_many(["ref", "ref2"])` put 2 prompt-bar thumbnails on one Image prompt.
  flow_fire `--stage stills --only L11,L12` committed both lines (L12 with char_ref + ref2) at
  0 credits. The stills downloaded as real PNG 768x1376. Balance was 0 before and after
  (`Account details` popover; `nzlxg` never arrived within 75 s).
- **Tabs created after attach cannot navigate (OPEN).** In this Edge, a tab made by `Target.createTarget`
  (the own-window path) or by `context.new_page()` renders its first URL. Its first `goto` or `reload`
  then hangs, and the renderer stops answering `Runtime.evaluate` / `Page.enable`. It is not a dialog
  (`handleJavaScriptDialog`: "No dialog is showing") and not paused. While such a tab exists,
  every later `connect_over_cdp` times out (180 s), and that includes the MCP server's. Only the tab
  Edge launched with navigates normally (goto 1.7 s / 0.5 s). Recovery: `/json/close/<id>` on the
  hung tab, and never close the last window. The Task 4 run drove the launched tab by pre-setting
  `Browser._target_id`.

### Fix round 1 (2026-09-30): attach-only shared tab

- `browser.py` no longer creates tabs on attach. The target comes from `GET /json/list` (first
  `flow.google.com` page, or the remembered target id). A bounded raw-CDP `Runtime.evaluate` ping
  (stdlib WebSocket, 5 s) runs before `connect_over_cdp`, and `connect_over_cdp` itself is capped
  at 30 s x 2 (a failure names any page target that does not answer). A tab is created only when
  this process launched Edge (the launch URL opens it).
- The peers (MCP server, flow_fire) serialise on `<profile_dir>/_tab.lock`, a `runlock.TabLock` taken
  before the RunLock. Each fire, sweep and download moves the shared tab to the run's bound project
  (`goto_project`). `upload(path, project_dir)` does the same.
- LIVE Frames-slot check: a start frame attached in Video/Frames mode shows as `FRAME_FILLED` plus one
  `img.chip-image` (it counts as a prompt-bar thumb). This time it did NOT survive a project reload,
  and Image mode showed 0 thumbs. An earlier live run saw frames persist across reloads, so that
  behaviour is not stable. With the frame left in the slot (no reload), a flow_fire Image still
  committed with exactly 1 thumb (`attach_many` requires count == len(refs)): the frame did not ride along.

## Live wave - queued acceptance facts (2026-09-30)

- The prompt editor is emptied ~0.03-0.07 s after Start generation is clicked, for every fire. It is NOT an
  acceptance signal: 3 of 4 queued stills whose editor cleared never existed in the project.
- A `goto_project` on the shared tab ~5-10 s after the click (the next queued fire's reload, or settle)
  lost the generation 3 times of 4. The one that survived was listed by `Zzl0ze` with a URL ~39 s after
  its click. No `ogiZ0b` media record arrived for any queued fire (the tab navigated away first).
- The detail-page Download fallback (`_download_original`, used when only the fife URL is known) leaves an
  `edge://downloads-hub/` page target that does not answer CDP. Every `connect_over_cdp` then times out
  until the panel is closed in the Edge window; `/json/close/<id>` does not remove it.
- `[aria-label="Account details"]` is present on the Flow home page too (counted 1); a false `signed_in`
  right after relaunch is a hydration race, not a selector miss.

## Redesign selectors - UNVERIFIED (to confirm on the next live run)

| constant | value | assumption |
|---|---|---|
| `PENDING_TILE` + `PENDING_BADGE` | `flow-pending-tile` holding `.loading-percentage` | LIVE (wave 2): one per queued job, 0.05-0.08 s after the click; only badges inside a pending tile count |
| `PENDING_TILE_ID_ATTRS` | `data-media-id`, `data-id`, `data-workflow-id`, `id`, `aria-label` | UNVERIFIED which (if any) a pending tile carries. None -> the service falls back to count growth, or count flat + another job landed + editor emptied (`tile_swap`) |
| `INGREDIENT_CHIP` | `flow-base-prompt-box button[aria-label="Ingredient"]` | clicking the chip removes it (as `FRAME_FILLED` does) |
| `DETAIL_MARKER` | `button[aria-label="Download media"]` | presence = detail view rendered; NEVER clicked |
| as29s signed URL for a VIDEO | `[7][0][13]` on the detail record | opening a clip's detail view sends a signed flow-content URL (only seen for stills) |

Acceptance of a queued fire = a NEW pending tile (identity set diff; fallback above) vs before the click,
or a ledger record for the prompt. If both selectors miss live, every queued fire comes back UNCONFIRMED
(safe: charged, pending, run stops) - fix the selectors, never fall back to the prompt box.


## Live wave 2 - grid + acceptance facts (2026-09-30)

- A grid tile is `<flow-grid-tile-container aria-label="<title>">` > `flow-tile-container` > `flow-image-tile` >
  `img[data-media-id]`. There is no `a[href*="/edit/"]` in the grid. The grid is windowed, so the tile
  count stays flat when new tiles are added (7 stayed 7).
- Queued still: a `<flow-pending-tile>` with `div.loading-percentage` "NN%" appears 0.05-0.08 s after the
  click (4 fires, badges 0->4). The badges read 68/99/99 % after ~10-24 s and 100 % on landing.
- With no navigation between fires (in-place preparation), 4 of 4 queued stills landed. The signed URL
  came from `ogiZ0b` 21-30 s after each click.
- After a fire, Flow empties the editor (reads "\n") and clears the prompt-bar ingredients (0 chip
  thumbnails), so ingredient-chip removal was not exercised.
- `fetch_bytes` on the `ogiZ0b` signed URL -> PNG 768x1376. No downloads-hub target appeared afterwards.

## Live wave 3 - pending-tile identity (2026-09-30)

- A live `flow-pending-tile` has NO identity attribute: none of data-media-id / data-id / data-workflow-id
  / id / aria-label on it or on its parents (`div.container < flow-image-tile < div.container`), only Angular
  `_ng*` attributes. `pending_tiles()` returns all None.
- So acceptance runs the **`tile_count` fallback** live: the badged pending tiles went 0->1->2->3->4 at
  0.02-0.05 s after each click. The `tile` (identity) and `tile_swap` branches were not exercised.
- `as29s` can carry a signed image URL without the detail view being opened (L11 landed that way);
  the rest came from `ogiZ0b`.
- `upload_many`: one reload + 2 uploads, 6.5 s. `_tab_inflight.json` ends as `{"projects": []}`
  (pruned, not deleted).

## Acceptance branches - what has run live (2026-09-30, waves 2-3)

- **Live branch: `tile_count`.** Pending tiles carry no identity attribute (none of `PENDING_TILE_ID_ATTRS`), so
  every live acceptance so far was the count fallback: more badged `flow-pending-tile`s than before the click.
- **Never run live:** the identity branch (`tile`, set diff of pending-tile ids) and `tile_swap` (count flat +
  another job landed + editor emptied). Both are covered only by fake-page tests.

