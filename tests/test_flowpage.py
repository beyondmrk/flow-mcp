"""FlowPage against a STATIC fake of the Flow DOM (selectors from fixtures/NOTES.md), in a
throwaway headless Edge. Never attaches to the dedicated Edge, never loads flow.google.com."""
import base64

import pytest

from flow_mcp.flowpage import FlowPage
from flow_mcp.guards import FlowError
from flow_mcp.netwatch import Media

pytestmark = pytest.mark.browser

TOGGLES = [("mode", "image", "image Image"), ("mode", "video", "videocam Video"),
           ("vmode", "frames", "crop_free Frames"), ("vmode", "ingredients", "chrome_extension Ingredients"),
           ("aspect", "16:9", "crop_16_9 16:9"), ("aspect", "9:16", "crop_9_16 9:16"),
           ("count", "x1", "x1"), ("count", "x2", "x2"), ("count", "x3", "x3"), ("count", "x4", "x4"),
           ("dur", "4s", "4s"), ("dur", "8s", "8s")]


def fake_flow(pill="off", fire=True, banner=False, account_panel=False, stuck=(), aspect="16:9",
              editor_text="", assets=("ref_a", "ref_b"), model="Nano Banana 2", model_stuck=False,
              add_thumbs=1, add_delay=0, fire_disabled=False, max_thumbs=99, box="image"):
    # box: "image" = Add-ingredients button; "frames" = Frames Start chip only; "late_attach" = a
    # leftover filled frame chip renders at once and the Add-ingredients button only ~700 ms later
    sel = {"mode": "image", "vmode": "frames", "aspect": aspect, "count": "x1", "dur": "8s"}
    tog = "".join(
        f'<button class="mat-button-toggle-button" role="radio" data-g="{g}" data-v="{v}" '
        f'aria-checked="{str(sel[g] == v).lower()}" {"data-stuck=1" if v in stuck else ""}>{t}</button>'
        for g, v, t in TOGGLES)
    pill_html = {"off": '<button class="agent-mode-chip" aria-pressed="false"><span>Agent</span></button>',
                 "on": '<button class="agent-mode-chip" aria-pressed="true"><span>Agent</span></button>',
                 "stuck": '<button class="agent-mode-chip" aria-pressed="true" data-stuck=1><span>Agent</span></button>',
                 None: ""}[pill]
    # live shapes (T7 probe): model menu items carry an emoji prefix and a "... 2 Lite" sibling;
    # picker rows are button[role=option] "<title> / Image" inside the "Asset list" listbox
    models = "".join(f'<button role="menuitem" class="model-opt" data-label="{m}">🍌 {m}</button>'
                     for m in ("Nano Banana Pro", "Nano Banana 2", "Nano Banana 2 Lite",
                               "Omni 1.1 Flash", "Veo 3.1 Lite"))
    rows = "".join(f'<button role="option" class="asset-item"><img src="data:,">{a}<br>Image</button>'
                   for a in assets)
    png = base64.b64encode(b"\x89PNG fake original bytes" * 50).decode()
    return f"""<!doctype html><html><body>
<div aria-label="Account details">acct</div>
{'<div id="acct"><button aria-label="Close account panel">close</button></div>' if account_panel else ''}
{'<div id="banner">You are out of Google Flow credits</div>' if banner else ''}
<button aria-label="Settings trigger" id="chip"></button>
<div id="panel" style="display:none">{tog}
<button aria-label="Select model family" aria-haspopup="menu" id="model" data-label="{model}" {"data-stuck=1" if model_stuck else ""}>🍌 {model}<span>arrow_drop_down</span></button></div>
<div role="menu" id="models" style="display:none">{models}</div>
{pill_html}
<flow-base-prompt-box><div id="thumbs"></div>
<div class="ProseMirror" contenteditable="true">{editor_text}</div>
{ {"image": '<button aria-label="Add ingredients to the prompt box">add</button>',
    "frames": '<button class="empty-chip">Start</button>',
    "late_attach": '<button aria-label="Image ingredient" id="frame" onclick="window.frameClicks=(window.frameClicks||0)+1"><img class="chip-image" src="data:,"></button>'}[box] }
{f'<button aria-label="Start generation" {"disabled" if fire_disabled else ""}>arrow_forward</button>' if fire else ''}</flow-base-prompt-box>
<div class="cdk-overlay-pane" id="picker" style="display:none"><button id="upl">upload Upload media</button>
<input aria-label="Search assets"><div role="listbox" aria-label="Asset list" id="rows">{rows}</div>
<img class="detail-preview-image" style="display:none" src="data:,">
<button id="add">Add to prompt</button></div>
<button aria-label="Download media">download</button>
<div id="menu" style="display:none"><div role="menuitem">2K Upscaled</div>
<a role="menuitem" download="orig.png" href="data:image/png;base64,{png}">1K Original size</a></div>
<script>
const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
function chip() {{
  const on = g => $(`[data-g="${{g}}"][aria-checked="true"]`).textContent;
  $('#chip').textContent = (on('mode').includes('Video') ? 'Video · 720p · ' + on('dur') : '🍌 ' + $('#model').dataset.label) +
    ' ' + on('aspect').split(' ')[0] + ' ' + on('count');
}}
chip();
$('#chip').onclick = () => {{ const p = $('#panel'); p.style.display = p.style.display === 'none' ? 'block' : 'none'; }};
document.addEventListener('keydown', e => {{ if (e.key === 'Escape') {{
  if ($('#models').style.display !== 'none') $('#models').style.display = 'none';
  else if ($('#panel').style.display !== 'none') $('#panel').style.display = 'none';
  else $('#picker').style.display = 'none'; }} }});
$$('.mat-button-toggle-button').forEach(b => b.onclick = () => {{
  if (b.dataset.stuck) return;
  $$(`[data-g="${{b.dataset.g}}"]`).forEach(x => x.setAttribute('aria-checked', 'false'));
  b.setAttribute('aria-checked', 'true'); chip();
}});
$('#model').onclick = () => $('#models').style.display = 'block';
$$('.model-opt').forEach(o => o.onclick = () => {{
  if (!$('#model').dataset.stuck) {{ $('#model').dataset.label = o.dataset.label;
    $('#model').innerHTML = o.textContent + '<span>arrow_drop_down</span>'; }}
  $('#models').style.display = 'none'; chip();
}});
const pill = $('.agent-mode-chip');
if (pill) pill.onclick = () => {{ if (!pill.dataset.stuck) pill.setAttribute('aria-pressed', pill.getAttribute('aria-pressed') === 'true' ? 'false' : 'true'); }};
const acct = $('#acct'); if (acct) acct.querySelector('button').onclick = () => acct.remove();
function wireAdd() {{ const a = $('[aria-label="Add ingredients to the prompt box"]');
  if (a) a.onclick = () => $('#picker').style.display = 'block'; }}
wireAdd();
if ({str(box == "late_attach").lower()}) setTimeout(() => {{ const a = document.createElement('button');
  a.setAttribute('aria-label', 'Add ingredients to the prompt box'); a.textContent = 'add';
  $('flow-base-prompt-box').appendChild(a); wireAdd(); }}, 700);
$('[aria-label="Search assets"]').oninput = e => $$('.asset-item').forEach(r =>
  r.style.display = r.textContent.includes(e.target.value) ? '' : 'none');
function pick(r) {{ window.picked = r.textContent; r.classList.add('asset-item-active'); const pv = $('.detail-preview-image');
  pv.style.display = 'block'; pv.style.width = '0'; pv.style.height = '0'; pv.alt = 'Preview of ' + r.firstChild.nextSibling.textContent; }}
$$('.asset-item').forEach(r => r.onclick = () => {{
  if (r.classList.contains('asset-item-active')) {{ r.classList.remove('asset-item-active'); window.picked = null;
    $('.detail-preview-image').style.display = 'none'; $('.detail-preview-image').alt = ''; return; }}  // live: click toggles off
  $$('.asset-item').forEach(x => x.classList.remove('asset-item-active')); pick(r); }});
window.pick = pick;
$('#upl').onclick = () => {{ const i = document.createElement('input'); i.type = 'file';
  i.onchange = () => {{ const b = document.createElement('button'); b.setAttribute('role', 'option');
    b.className = 'asset-item'; b.innerHTML = 'Uploading<br>' + i.files[0].name + '<br>Image'; $('#rows').appendChild(b);
    setTimeout(() => b.innerHTML = i.files[0].name + '<br>Image', 1200); }};
  i.click(); }};
$('#add').onclick = () => {{ if (window.picked) {{ window.adds = (window.adds || 0) + 1;
  setTimeout(() => {{ for (let k = 0; k < {add_thumbs} && $$('#thumbs img').length < {max_thumbs}; k++) {{ const i = document.createElement('img');
    i.src = 'data:,'; i.className = 'chip-image'; $('#thumbs').appendChild(i); }} }}, {add_delay});
  $('#picker').style.display = 'none'; }} }};
const fire = $('[aria-label="Start generation"]');
if (fire) fire.onclick = () => {{ window.fired = (window.fired || 0) + 1; $('.ProseMirror').textContent = ''; }};
$('[aria-label="Download media"]').onclick = () => $('#menu').style.display = 'block';
</script></body></html>"""


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="msedge", headless=True)
        yield b
        b.close()


class FakeBrowser:
    """Stands in for flow_mcp.browser.Browser: hands FlowPage the throwaway page."""
    def __init__(self, page): self._p = page
    def page(self): return self._p


@pytest.fixture
def make(browser):
    pages = []

    def _make(**kw):
        pg = browser.new_page(); pages.append(pg)
        pg.set_content(fake_flow(**kw))
        fp = FlowPage(FakeBrowser(pg)); fp.ui_timeout_ms = 400
        return fp, pg
    yield _make
    for p in pages:
        p.close()


def _checked(pg, v):
    return pg.locator(f'[data-v="{v}"]').get_attribute("aria-checked")


# --- set_settings: Agent pill (ruling) ----------------------------------------------

def test_pill_on_is_clicked_off(make):
    fp, pg = make(pill="on")
    fp.set_settings(mode="image", model="nano_banana_2", count=1, duration=None, video_mode=None)
    assert pg.locator(".agent-mode-chip").get_attribute("aria-pressed") == "false"


def test_pill_off_ok(make):
    fp, pg = make(pill="off")
    fp.set_settings(mode="image", model="nano_banana_2", count=1, duration=None, video_mode=None)


def test_pill_absent_with_fire_button_ok(make):
    fp, pg = make(pill=None, fire=True)
    fp.set_settings(mode="image", model="nano_banana_2", count=1, duration=None, video_mode=None)


def test_pill_stuck_on_is_settings_mismatch(make):
    fp, pg = make(pill="stuck")
    with pytest.raises(FlowError) as e:
        fp.set_settings(mode="image", model="nano_banana_2", count=1, duration=None, video_mode=None)
    assert e.value.code == "SETTINGS_MISMATCH"


def test_pill_absent_and_fire_absent_is_ui_changed(make):
    fp, pg = make(pill=None, fire=False)
    with pytest.raises(FlowError) as e:
        fp.set_settings(mode="image", model="nano_banana_2", count=1, duration=None, video_mode=None)
    assert e.value.code == "UI_CHANGED"


# --- set_settings: toggles + aspect ---------------------------------------------------------

def test_aspect_forced_to_9x16_and_count_set(make):
    fp, pg = make(aspect="16:9")
    fp.set_settings(mode="image", model="nano_banana_2", count=2, duration=None, video_mode=None)
    assert _checked(pg, "9:16") == "true" and _checked(pg, "16:9") == "false"
    assert _checked(pg, "x2") == "true"


def test_video_mode_duration_and_frames(make):
    fp, pg = make()
    fp.set_settings(mode="video", model="omni_flash", count=1, duration=4, video_mode="ingredients")
    for v in ("video", "4s", "ingredients", "9:16", "x1"):
        assert _checked(pg, v) == "true", v


def test_stuck_toggle_is_settings_mismatch(make):
    fp, pg = make(aspect="16:9", stuck=("9:16",))
    with pytest.raises(FlowError) as e:
        fp.set_settings(mode="image", model="nano_banana_2", count=1, duration=None, video_mode=None)
    assert e.value.code == "SETTINGS_MISMATCH" and "9:16" in e.value.detail


def test_model_is_selected_in_panel(make):
    fp, pg = make(model="Nano Banana Pro")
    fp.set_settings(mode="image", model="nano_banana_2", count=1, duration=None, video_mode=None)
    assert pg.locator("#model").get_attribute("data-label") == "Nano Banana 2"
    assert "Nano Banana 2" in pg.locator("#chip").inner_text()


def test_video_model_is_selected_not_just_present(make):
    fp, pg = make(model="Veo 3.1 Lite")
    fp.set_settings(mode="video", model="omni_flash", count=1, duration=8, video_mode="frames")
    assert pg.locator("#model").get_attribute("data-label") == "Omni 1.1 Flash"


def test_model_that_will_not_select_is_settings_mismatch(make):
    fp, pg = make(model="Veo 3.1 Lite", model_stuck=True)
    with pytest.raises(FlowError) as e:
        fp.set_settings(mode="video", model="omni_flash", count=1, duration=8, video_mode="frames")
    assert e.value.code == "SETTINGS_MISMATCH" and "Omni 1.1 Flash" in e.value.detail


def test_unknown_model_is_settings_mismatch(make):
    fp, pg = make()
    with pytest.raises(FlowError) as e:
        fp.set_settings(mode="image", model="gpt_image_2", count=1, duration=None, video_mode=None)
    assert e.value.code == "SETTINGS_MISMATCH"


def test_account_panel_closed_before_acting(make):
    fp, pg = make(account_panel=True)
    fp.set_settings(mode="image", model="nano_banana_2", count=1, duration=None, video_mode=None)
    assert pg.locator("#acct").count() == 0


# --- fire -------------------------------------------------------------------------------------

def test_fire_clicks_start_generation(make):
    fp, pg = make()
    fp.fire()
    assert pg.evaluate("window.fired") == 1


def test_fire_rechecks_agent_pill_before_click(make):
    fp, pg = make(pill="on")
    fp.fire()
    assert pg.locator(".agent-mode-chip").get_attribute("aria-pressed") == "false"
    assert pg.evaluate("window.fired") == 1


def test_fire_with_stuck_pill_never_clicks(make):
    fp, pg = make(pill="stuck")
    with pytest.raises(FlowError) as e:
        fp.fire()
    assert e.value.code == "SETTINGS_MISMATCH" and pg.evaluate("window.fired") is None


def test_fire_ready_ok_and_disabled(make):
    fp, pg = make()
    fp.fire_ready()
    fp2, pg2 = make(fire_disabled=True)
    with pytest.raises(FlowError) as e:
        fp2.fire_ready()
    assert e.value.code == "UI_CHANGED" and "disabled" in e.value.detail


def test_fire_ready_missing_button(make):
    fp, pg = make(fire=False, banner=True)
    with pytest.raises(FlowError) as e:
        fp.fire_ready()
    assert e.value.code == "OUT_OF_CREDITS"


def test_no_fire_button_with_banner_is_out_of_credits(make):
    fp, pg = make(fire=False, banner=True)
    with pytest.raises(FlowError) as e:
        fp.fire()
    assert e.value.code == "OUT_OF_CREDITS"


def test_no_fire_button_no_banner_is_ui_changed(make):
    fp, pg = make(fire=False, banner=False)
    with pytest.raises(FlowError) as e:
        fp.fire()
    assert e.value.code == "UI_CHANGED" and "Start generation" in e.value.detail


# --- prompt -------------------------------------------------------------------------------------

def test_type_prompt_reads_back(make):
    fp, pg = make()
    fp.type_prompt("a single red apple on a wooden table")
    assert pg.locator(".ProseMirror").inner_text() == "a single red apple on a wooden table"
    assert not fp.prompt_cleared()
    fp.fire()
    assert fp.prompt_cleared()


def test_type_prompt_refuses_dirty_editor(make):
    fp, pg = make(editor_text="leftover prompt")
    with pytest.raises(FlowError) as e:
        fp.type_prompt("new")
    assert e.value.code == "UI_CHANGED"
    assert pg.locator(".ProseMirror").inner_text() == "leftover prompt"  # never select-all/delete


# --- attach -------------------------------------------------------------------------------------

def test_attach_exactly_one_row_adds_thumb(make):
    fp, pg = make()
    fp.attach("ref_a")
    assert pg.locator("#thumbs img").count() == 1


def test_attach_does_not_deselect_an_auto_selected_row(make):
    fp, pg = make()
    pg.evaluate("window.pick(document.querySelector('.asset-item'))")  # live: lone hit is pre-selected
    fp.attach("ref_a")
    assert pg.locator("#thumbs img").count() == 1


def test_attach_slow_thumb_is_not_added_twice(make):
    fp, pg = make(add_delay=900)
    fp.attach("ref_a")
    assert pg.locator("#thumbs img").count() == 1 and pg.evaluate("window.adds") == 1


def test_attach_double_thumb_is_ref_not_attached(make):
    fp, pg = make(add_thumbs=2)
    with pytest.raises(FlowError) as e:
        fp.attach("ref_a")
    assert e.value.code == "REF_NOT_ATTACHED"


def test_attach_ambiguous_is_ref_not_attached(make):
    fp, pg = make()
    with pytest.raises(FlowError) as e:
        fp.attach("ref_")
    assert e.value.code == "REF_NOT_ATTACHED"


def test_attach_missing_is_ref_not_attached(make):
    fp, pg = make()
    with pytest.raises(FlowError) as e:
        fp.attach("nope")
    assert e.value.code == "REF_NOT_ATTACHED"


def test_attach_many_two_refs_two_thumbs(make):
    fp, pg = make()
    fp.attach_many(["ref_a", "ref_b"])
    assert pg.locator("#thumbs img").count() == 2 and pg.evaluate("window.adds") == 2


def test_attach_prefix_name_ignores_longer_names(make):
    # LIVE 2026-09-30: searching "ref" also lists ref2.png (substring filter), first hit pre-selected
    fp, pg = make(assets=("ref2.png", "ref.png"))
    pg.evaluate("window.pick(document.querySelector('.asset-item'))")  # ref2.png pre-selected
    fp.attach_many(["ref", "ref2"])
    assert pg.locator("#thumbs img").count() == 2
    assert pg.evaluate("window.picked").startswith("ref2.png")  # last pick = ref2, so ref was picked first


def test_attach_prefix_name_with_no_exact_row_is_ref_not_attached(make):
    fp, pg = make(assets=("ref2.png", "ref3.png"))
    with pytest.raises(FlowError) as e:
        fp.attach("ref")
    assert e.value.code == "REF_NOT_ATTACHED"


def test_image_mode_waits_for_add_ingredients_not_a_leftover_frame(make):
    # review fix 5: a filled frame chip is NOT the Image-mode ready signal
    fp, pg = make(box="late_attach", assets=("ref_a",))
    fp.mode = "image"
    fp.attach("ref_a")
    assert pg.evaluate("window.adds") == 1 and not pg.evaluate("window.frameClicks")


def test_attach_many_in_frames_mode_is_ref_not_attached(make):
    fp, pg = make(box="frames")
    fp.mode = "frames"
    with pytest.raises(FlowError) as e:
        fp.attach_many(["ref_a", "ref_b"])
    assert e.value.code == "REF_NOT_ATTACHED" and "one start frame" in str(e.value)


def test_attach_many_stuck_at_one_thumb_is_ref_not_attached(make):
    fp, pg = make(max_thumbs=1)
    with pytest.raises(FlowError) as e:
        fp.attach_many(["ref_a", "ref_b"])
    assert e.value.code == "REF_NOT_ATTACHED"


def test_attach_many_final_count_must_equal_names(make, monkeypatch):
    fp, pg = make()
    monkeypatch.setattr(fp, "attach", lambda name: None)  # every attach "succeeds", no thumbs land
    with pytest.raises(FlowError) as e:
        fp.attach_many(["ref_a", "ref_b"])
    assert e.value.code == "REF_NOT_ATTACHED"


def test_attach_many_refuses_bad_lengths(make):
    fp, pg = make()
    for names in ([], ["ref_a"] * 4):
        with pytest.raises(FlowError) as e:
            fp.attach_many(names)
        assert e.value.code == "REF_NOT_ATTACHED"
    assert pg.locator("#thumbs img").count() == 0


def test_upload_waits_until_row_leaves_uploading(make, tmp_path):
    fp, pg = make()
    fp.reset = lambda: None  # the static fake has no /project/ url; its row list survives "reloads"
    f = tmp_path / "ref_up.png"; f.write_bytes(b"\x89PNG\r\n\x1a\n")
    assert fp.upload(f) == "ref_up"
    assert pg.locator(".asset-item").filter(has_text="ref_up").inner_text().startswith("ref_up.png")


# --- state / bytes ------------------------------------------------------------------------------

def test_state_banner_alone_is_not_out_of_credits(make):
    fp, pg = make(banner=True, fire=True)
    st = fp.state()
    assert st["captcha"] is False and st["out_of_credits"] is False and st["signed_in"] is True
    assert "out of Google Flow" in st["banner"]


def test_state_banner_and_no_fire_button_is_out_of_credits(make):
    fp, pg = make(banner=True, fire=False)
    st = fp.state()
    assert st["out_of_credits"] is True


def test_state_no_banner(make):
    fp, pg = make()
    assert fp.state()["banner"] is None


def test_fetch_bytes_prefers_signed_url(make, monkeypatch):
    fp, pg = make()

    class R:
        ok, status, headers = True, 200, {"content-type": "image/jpeg"}
        def body(self): return b"\xff\xd8\xff signed-original"
    monkeypatch.setattr(pg.request, "get", lambda url, **kw: R())
    m = Media("m1", "p", None, "image", 1.0, "https://flow-content.google/image/m1?Signature=x", "wf")
    assert fp.fetch_bytes(m) == b"\xff\xd8\xff signed-original"


def _no_ui_download(fp, pg, monkeypatch):
    opened = []
    monkeypatch.setattr(fp, "open_detail", lambda wf: opened.append(wf))
    return opened


def test_fetch_bytes_non_media_is_no_media_url_never_a_ui_download(make, monkeypatch):
    fp, pg = make()

    class R:
        ok, status, headers = True, 200, {"content-type": "text/html"}
        def body(self): return b"<html>sign in</html>"
    monkeypatch.setattr(pg.request, "get", lambda url, **kw: R())
    opened = _no_ui_download(fp, pg, monkeypatch)
    m = Media("m1", "p", None, "image", 1.0, "https://flow-content.google/image/m1?Signature=x", "wf-1")
    with pytest.raises(FlowError) as e:
        fp.fetch_bytes(m)
    assert e.value.code == "NO_MEDIA_URL" and opened == []
    assert pg.evaluate("document.querySelector('#menu').style.display") == "none"  # Download never clicked


def test_fetch_bytes_expired_or_missing_url_is_no_media_url(make, monkeypatch):
    fp, pg = make()

    class R:
        ok, status, headers = False, 403, {}
        def body(self): return b""
    monkeypatch.setattr(pg.request, "get", lambda url, **kw: R())
    opened = _no_ui_download(fp, pg, monkeypatch)
    for url in ("https://flow-content.google/image/m1?Signature=x", None):
        with pytest.raises(FlowError) as e:
            fp.fetch_bytes(Media("m1", "p", None, "image", 1.0, url, "wf-1"))
        assert e.value.code == "NO_MEDIA_URL"
    assert opened == [] and not hasattr(fp, "_download_original")


def test_state_waits_for_a_late_account_control(make):
    fp, pg = make()
    pg.evaluate("""() => { const a = document.querySelector('[aria-label="Account details"]'); a.remove();
                      setTimeout(() => document.body.appendChild(a), 700); }""")
    assert fp.state()["signed_in"] is True


def test_state_signed_out_after_the_wait(make, monkeypatch):
    import flow_mcp.flowpage as fpm
    monkeypatch.setattr(fpm, "ACCOUNT_WAIT_S", 0.5)
    fp, pg = make()
    pg.evaluate("""() => document.querySelector('[aria-label="Account details"]').remove()""")
    assert fp.state()["signed_in"] is False


def test_pending_tiles_only_badged_pending_tiles_with_identity(make):
    fp, pg = make()
    assert fp.pending_tiles() == []
    pg.evaluate("""() => { const g = document.createElement('div');
      g.innerHTML = '<flow-grid-tile-container aria-label="done"></flow-grid-tile-container>'
        + '<flow-pending-tile data-media-id="a1"><div class="loading-percentage">42%</div></flow-pending-tile>'
        + '<flow-pending-tile><div class="loading-percentage">7%</div></flow-pending-tile>'
        + '<flow-pending-tile id="noBadge"></flow-pending-tile>'
        + '<div>99%</div><span class="loading-percentage">5%</span>';
      document.body.appendChild(g); }""")
    assert fp.pending_tiles() == ["a1", None]  # stray NN% text and badge-less tiles never count


def test_prepare_in_place_refuses_a_non_empty_editor(make):
    fp, pg = make(editor_text="left over prompt")
    with pytest.raises(FlowError) as e:
        fp.prepare_in_place()
    assert e.value.code == "UI_CHANGED"


def test_prepare_in_place_removes_leftover_chips(make):
    fp, pg = make(box="late_attach")
    pg.evaluate("""() => { document.querySelector('#frame').onclick = e => e.currentTarget.remove();
      const b = document.createElement('button'); b.setAttribute('aria-label', 'Ingredient');
      b.innerHTML = '<img class="chip-image" src="data:,">'; b.onclick = () => b.remove();
      document.querySelector('flow-base-prompt-box').appendChild(b); }""")
    fp.prepare_in_place()
    assert pg.locator("flow-base-prompt-box img.chip-image").count() == 0


def test_prepare_in_place_fails_closed_when_a_chip_will_not_clear(make):
    fp, pg = make(box="late_attach")  # the fake frame chip counts clicks but never goes away
    with pytest.raises(FlowError) as e:
        fp.prepare_in_place()
    assert e.value.code == "UI_CHANGED"


# --- fix round 1: idempotent upload, fail-closed open_project ---------------------------------

def _rows(pg):
    return pg.locator(".asset-item").count()


def test_upload_existing_exact_stem_returns_without_uploading(make, tmp_path):
    fp, pg = make(assets=("ref.png", "ref_old.png"))
    fp.reset = lambda: None
    f = tmp_path / "ref.png"; f.write_bytes(b"\x89PNG\r\n\x1a\n")
    before = _rows(pg)
    pg.on("filechooser", lambda fc: pg.evaluate("window.chooser = 1"))
    assert fp.upload(f) == "ref"
    assert _rows(pg) == before and pg.evaluate("window.chooser") is None


def test_upload_substring_is_not_an_existing_match(make, tmp_path):
    fp, pg = make(assets=("ref_old.png", "myref.png"))
    fp.reset = lambda: None
    f = tmp_path / "ref.png"; f.write_bytes(b"\x89PNG\r\n\x1a\n")
    before = _rows(pg)
    assert fp.upload(f) == "ref"
    assert _rows(pg) == before + 1


def test_row_is_exact_stem_only():
    assert FlowPage._row_is("image\nref.png\nImage", "ref")
    assert not FlowPage._row_is("ref_old.png\nImage", "ref")
    assert not FlowPage._row_is("myref.png\nImage", "ref")
    assert not FlowPage._row_is("Red apple on wooden table\nImage", "ref")


def test_open_project_unreadable_grid_fails_closed(browser, monkeypatch):
    from types import SimpleNamespace
    from flow_mcp import flowpage
    pg = browser.new_page()
    html = ('<div aria-label="Account details">a</div><button>New project</button>'
            '<script>document.querySelector("button").onclick = () => window.created = 1</script>')
    pg.route("https://flow.test/**", lambda r: r.fulfill(content_type="text/html", body=html))
    pg.goto("https://flow.test/")
    fb = FakeBrowser(pg); fb.cfg = SimpleNamespace(flow_url="https://flow.test/")
    monkeypatch.setattr(flowpage, "GRID_WAIT_MS", 300)
    fp = FlowPage(fb); fp.ui_timeout_ms = 400
    try:
        with pytest.raises(FlowError) as e:
            fp.open_project("_bridge_smoke")
        assert e.value.code == "UI_CHANGED" and pg.evaluate("window.created") is None
    finally:
        pg.close()
