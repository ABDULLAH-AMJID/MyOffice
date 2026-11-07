# Phase 2 — Design: MyOffice Script for Presentations

**Project:** MyOffice · **Depends on:** `docs/SCRIPTING_RESEARCH.md` (Phase 1) · **Date:** 2026-11-08
**Status:** design complete — ready for Phase 3 implementation once approved

> Every codebase claim below was verified by reading the file in this session (anchors given as `file:line`).
> No file in `app/` has been modified yet. This document is the contract for Phase 3.

---

## 1. Decisions taken

You said "start", which I read as: proceed with the recommended options. Here they are, explicitly, with the cost
of changing them later — these are the only decisions in this document that would be expensive to reverse.

| # | Decision | Chosen | Why | Cost to reverse *after* Phase 3 |
| :-- | :-- | :-- | :-- | :-- |
| **D1** | Scripting language | **Python** — same engine as the existing sheet/writer macros | Zero new runtime, zero new language for users, and the presentation model *is* Python dataclasses (`app/slide/model.py`). A JS/Lua engine would be a second platform for no gain (`SCRIPTING_RESEARCH.md` §4) | High — the public API surface and all user scripts are Python |
| **D2** | Storage | **Sidecar file** `<deck>.<ext>.moscript` (JSON), written next to the deck when the deck is saved | `save_pptx()` rebuilds the OOXML package from scratch (`app/slide/pptx_io.py:42`), so in-file storage requires custom-part + content-type + relationship work in the save **and** load path. A sidecar keeps `.pptx` byte-clean and portable, is trivial to back up/inspect, and cannot corrupt a deck. Optional single-file bundle (`.mopkg`) is designed in §5.5 | Low — an in-file part can be added later without changing the user-facing API |
| **D3** | v1 security posture | **Documented trust model**: feature on, but *running* a script from a file always needs explicit consent once per file-content (TOFU hash approval); trusted folders bypass the prompt; global kill switch in settings. **No auto-run on open.** | Mirrors Microsoft's "disable with notification" (`SCRIPTING_RESEARCH.md` §5) without breaking day-to-day use. Honest documentation of the guardrail-vs-sandbox distinction | Low — policy is three config keys |

**Not decided by me, deliberately deferred to Phase 5/afterwards:** signing scripts, the isolated runner process,
event hooks (`on_open`), the in-file part. All four are designed-for but out of Phase 3 scope (§6.6, §9 M7+).

---

## 2. Two corrections to my own Phase 1 recommendation

I read the code more closely before designing. Two things in the Phase 1 report need adjusting — I would rather say
this now than discover it in Phase 3:

**(a) Journal/proxy → snapshot bracket + commit suppression.** Phase 1 proposed a transactional journal (queue
operations, apply on success). Reading `SlideDocument` (`app/slide/widget.py:346-372`), the existing undo mechanism
is `snapshot()` = `deepcopy(self.pres)`, `restore(snap)`, and `commit(text)` which pushes a `SnapshotCommand`.
Deep-copying the whole model twice per macro run is *cheap* (a deck is a few MB at most) and already battle-tested by
the editor. So the run is bracketed by:

```
before = doc.snapshot()
... script mutates the live model through the API ...
doc.commit("Macro: <name>")     # one undo entry
# on exception: doc.restore(before)   → nothing happened
```

The only real problem was that widget methods call `commit()` internally (`_insert_shape` at
`app/slide/widget.py:534-542`), which would push N undo entries. Fixed with a **single guard inside `commit()`**:
while a macro run is active the commit is suppressed and only `_sync_all()` runs. That is ~6 lines instead of a
journal/proxy layer, keeps the API calling the *real* widget methods (no behaviour drift), and gives exactly the
three guarantees we wanted: rollback on error, one undo entry, no UI flicker mid-run.

The journal remains the right design **when execution moves out of process** (§6.6) — at that point the operation
calls become a message protocol. That is a later, separate milestone.

**(b) One runner, not two.** The slide runner and the sheet runner must not be separate copies of the
`compile/exec/trace/timeout` plumbing (`app/macros/api.py:282-315`) — they would drift. Phase 3 extracts the
mechanism into `app/macros/engine.py` and leaves both APIs as thin wrappers (§8.1).

---

## 3. Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ PRESENTATION TAB                                                             │
│  app/slide/widget.py :: SlideDocument    ── ribbon "Scripts" ▸ Scripts (Alt+F8)│
│         ▲ commit() guard · snapshot()/restore() · rebuild_all()               │
└─────────┬────────────────────────────────────────────────────────────────────┘
          │ opens
┌─────────▼────────────────────────────────────────────────────────────────────┐
│ UI LAYER — app/slide/script/dialog.py :: ScriptDialog                        │
│  macro list (deck ▸ file, library ▸ user) │ editor + PythonHighlighter       │
│  Run / Stop / Save / New / Delete / Import / Export │ output pane            │
│  consent prompt (first run of a file-content) · "remove scripts from file"   │
└─────────┬───────────────────────────────┬────────────────────────────────────┘
          │ run_script()                  │ load/save
┌─────────▼───────────────────┐  ┌────────▼───────────────────────────────────┐
│ RUNNER                      │  │ STORE — app/slide/script/store.py          │
│ app/slide/script/runner.py  │  │ sidecar read/write · names · provenance ·  │
│ snapshot bracket · commit   │  │ TOFU approvals (config) · examples         │
│ suppression · stop flag     │  └────────┬───────────────────────────────────┘
└─────────┬───────────────────┘           │
          │ execute_python()              │ <deck>.pptx.moscript (JSON)
┌─────────▼───────────────────┐           │
│ ENGINE — app/macros/engine.py│           │
│ compile · exec · settrace    │           │
│ timeout · stop · stdout ·    │           │
│ error→"Line N: Type: msg"    │           │
└─────────┬───────────────────┘           │
          │ namespace {"app": AppAPI}     │
┌─────────▼────────────────────────────────────────────────────────────────────┐
│ OBJECT MODEL — app/slide/script/api.py                                        │
│  AppAPI.presentation ▸ PresentationAPI ▸ SlideListAPI ▸ SlideAPI             │
│        ▸ ShapeListAPI ▸ ShapeAPI ▸ ParagraphAPI ▸ RunAPI ▸ ThemeAPI          │
└─────────┬────────────────────────────────────────────────────────────────────┘
          │ reads/writes dataclasses + calls SlideDocument methods
┌─────────▼────────────────────────────────────────────────────────────────────┐
│ MODEL — app/slide/model.py (unchanged) · PERSISTENCE app/slide/pptx_io.py     │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Layering rule (enforced by a test):** `api.py` may import from `app.slide.model` and receive a `SlideDocument`;
`api.py` must **not** import `app.macros.*` except `errors`; `store.py` must not import Qt widgets; `dialog.py` is the
only file that may show UI.

### 3.1 Lifecycle of one run

1. User presses **Run** in `ScriptDialog`.
2. `store.is_trusted(path)` — if the sidecar is new/unknown, `store.approve()` shows the consent dialog; declining
   aborts with no side effects (§6.3).
3. `runner.run_script(code, doc, name, timeout, stop_flag)`:
   * `doc._macro_depth += 1` (commit suppression on), `before = doc.snapshot()`
   * build namespace `{"app": AppAPI(main), "math": math, "datetime": datetime, "re": re}`
   * `engine.execute_python(...)` → prints captured, timeout/stop enforced by `sys.settrace`
   * success → `down`: `doc._macro_depth -= 1`; `doc._sync_all()`; `doc.commit("Macro: <name>")`
   * failure → `doc.restore(before)` then re-raise
4. `dialog` prints output lines; on `MacroError` prints `✖ Line 12: ValueError: ...` and the document is untouched.

---

## 4. Public object model (the contract for Phase 3)

Design rules: VBA-familiar names where they cost nothing (`shapes`, `text`, `fill`, `line`, `add_picture`), Python
idioms where they help (`len()`, iteration, `==`, `in`). Everything is a **live proxy** — reads hit the real model,
writes mutate it immediately (the snapshot bracket is what makes that safe). Proxies hold the underlying dataclass
object, not an index, so they stay valid when slides/shapes are added or removed around them.

### 4.1 Entry points

```python
app.presentation      # PresentationAPI — the deck of the active slide document (primary name)
app.slide             # alias of app.presentation (consistency with app.sheet / app.writer)
app.emu(pixels)       # int   px → EMU          (model.px/emu, app/slide/model.py:21-27)
app.px(emu_value)     # float EMU → px
app.log(text)         # append to the output pane (does not open a dialog)
app.message(text)     # info dialog        (exists: app/macros/api.py:243)
app.confirm(text)     # yes/no → bool      (new)
app.input(prompt)     # text input         (exists as .input)
app.status(text)      # status bar         (exists)
app.sleep(ms)         # cooperative pause that also paints the UI; refuses > 5000 ms
```

### 4.2 `PresentationAPI` — `app.presentation`

| Member | Kind | Signature / meaning |
| :-- | :-- | :-- |
| `name` | property | File name or "Presentation N" (`doc.display_name()`) |
| `path` | property | `str` or `None` |
| `title` | property | `pres.title` (get/set) |
| `width`, `height` | property | EMU ints; settable as a pair through `set_size()` |
| `size_px` | property | `(w, h)` in pixels — `(960, 540)` for 16:9 |
| `count` / `len(p)` | property | number of slides |
| `slides` | property | `SlideListAPI` |
| `theme` | property | `ThemeAPI` |
| `current` | property | index of the slide shown in the editor (get/set) |
| `add_slide(layout="title_content", title="", body="", index=None)` | method | → `SlideAPI`; appends, or inserts at `index` |
| `duplicate(index)` | method | → `SlideAPI` (deep copy, `clone_shape` semantics) |
| `delete(index_or_slide)` | method | removes; refuses to delete the last slide |
| `move(from_i, to_i)` | method | reorder |
| `apply_theme(name)` | method | one of `THEMES` keys; returns the applied name |
| `set_size(preset)` | method | `"16:9"`, `"4:3"`, or `(w_px, h_px)` |
| `save()` | method | `bool`; `False` on failure (uses `DocumentWidget.save()`) |
| `save_as()` | method | `bool`; opens the Save-As dialog (`DocumentWidget.save_as()`) |
| `export_pdf(path)` | method | `doc.export_pdf_to(path)` |
| `find(needle)` | method | `list[ShapeAPI]` whose text contains `needle` (case-insensitive) |
| `refresh()` | method | repaint editor now (normally only at the end of the run) |

### 4.3 `SlideListAPI` — `app.presentation.slides`

| Member | Meaning |
| :-- | :-- |
| `len(slides)`, `iter`, `slides[i]` (negative indices allowed), `slides[1:3]` → `list[SlideAPI]` |
| `add(layout=..., title=..., body=..., index=None)` | same as `PresentationAPI.add_slide` |
| `remove(i_or_slide)` | same as `delete` |
| `index_of(slide)` | `int` |
| `current` | the slide the editor is showing |

### 4.4 `SlideAPI` — one slide

| Member | Kind | Meaning |
| :-- | :-- | :-- |
| `index` | property | live position in the deck |
| `layout` | property | `LAYOUTS` key (get/set; setting re-applies the layout) |
| `hidden` | property | bool — skipped in the show |
| `notes` | property | speaker notes text |
| `title` | property | text of the `placeholder == "title"` shape (get/set); `""` if none |
| `background` | property | colour hex or `None` |
| `transition` | property | `"none"`, `"fade"`, `"push"`, `"wipe"`, `"split"`, `"cover"`, `"zoom"` |
| `shapes` | property | `ShapeListAPI` |
| `is_current` | property | bool |
| `select()` | method | show this slide in the editor |
| `duplicate()` | method | → new `SlideAPI` |
| `delete()` | method | |
| `move(delta)` | method | ±n positions |
| `set_transition(kind=None, duration=None, all_slides=False)` | method | mirrors `SlideDocument.set_transition` (`widget.py:985`) |
| `set_background(color=None, image_path=None)` | method | mirrors `set_background` / `background_picture` (`widget.py:964-978`) |
| `clear()` | method | remove every shape |
| `texts()` | method | `list[str]` of all shape texts (read-only convenience) |

### 4.5 `ShapeListAPI` — `slide.shapes`

| Member | Meaning |
| :-- | :-- |
| `len(shapes)`, `iter`, `shapes[i]`, `shapes["Title"]` (by name), `shapes["{id}"]` |
| `add_textbox(x, y, w, h, text="", size=None, bold=False, color=None, align="left", fill=None, line=None, font=None)` | → `ShapeAPI` (kind `"text"`) |
| `add_shape(kind, x, y, w, h, text="", fill=None, line=None)` | `kind` ∈ `text`, `rect`, `round_rect`, `ellipse`, `triangle`, `diamond`, `star`, `arrow`, `line`, `arrow_line` (the same vocabulary `SlideDocument.add_shape_at` uses, `widget.py:516`) |
| `add_picture(source, x, y, w=None, h=None)` | `source` = path or `bytes`; keeps aspect ratio when only one of w/h is given. **Path reads are permission-gated** (§6.4) |
| `add_table(rows, x, y, w, h, header=True, color=None)` | `rows` = `list[list[str]]` |
| `add_chart(kind, categories, series, title="", x=0, y=0, w=0, h=0)` | `kind` ∈ `column`, `bar`, `line`, `pie`, `area`, `scatter` (the set `app/slide/widget.py` ChartDataDialog offers) |
| `remove(shape)` / `clear()` | |
| `find(name)` | first `ShapeAPI` with that name or `None` |
| `top()` / `bottom()` | z-order extremes |

All creation methods take **EMU ints**; passing pixels is the obvious user error, so:
* the module exports `emu`/`px` on `app`, and
* if a coordinate is `< 1000` the API **assumes pixels** and converts, emitting a warning line to the output pane
  (`"x=120 looks like pixels; converted to EMU"`). This is a deliberate ergonomic choice, documented in Phase 5.

### 4.6 `ShapeAPI` — one shape

| Group | Members |
| :-- | :-- |
| Identity | `id`, `name` (get/set), `kind`, `slide` (owning `SlideAPI`) |
| Geometry | `x`, `y`, `w`, `h` (EMU ints, get/set) · `rotation` (deg) · `move_to(x, y)` · `resize(w, h)` · `offset(dx, dy)` · `rect_px` → `(x, y, w, h)` in pixels |
| Text | `text` (get/set, multi-line) · `set_text(text, size=None, bold=None, color=None, align="left", bullet=False, font=None)` (delegates to `Shape.set_text`, `model.py:100`) · `paragraphs` → `list[ParagraphAPI]` · `font`, `size`, `bold`, `italic`, `underline`, `color`, `align`, `valign`, `bullet` (apply to all runs for uniformity) |
| Style | `fill` (hex or `None`) · `line` (hex or `None`) · `line_width` (pt) · `opacity` (0–1) · `shadow` (bool) |
| Media | `image` → `bytes \| None` · `image_ext` · `set_image(path_or_bytes)` |
| Table | `table` → `list[list[str]]` (get/set) · `set_cell(r, c, text)` · `table_style` |
| Chart | `chart` → dict (`kind`, `categories`, `series`, `title`) · `set_chart(kind, categories, series, title="")` |
| Animation | `animate(effect, trigger="click", duration=0.5, delay=0.0, direction="bottom")` · `animation` → dict · `clear_animation()` (effects = the names `SlideDocument.set_animation` accepts, `widget.py:1007`) |
| Z-order | `bring_to_front()`, `send_to_back()` (`SlideDocument.arrange`, `widget.py:888`) |
| Lifecycle | `duplicate(dx=0, dy=0)` · `delete()` · `select()` |

`ParagraphAPI`: `text`, `runs` → `list[RunAPI]`, `add_run(text, **fmt)`, `align`, `level`, `bullet`.
`RunAPI`: `text`, `bold`, `italic`, `underline`, `size`, `color`, `font`.
`ThemeAPI`: `name`, `heading_font`, `body_font`, `accent`, `set(name)`.

### 4.7 The examples from your brief, written against this API

```python
# 1) create 5 slides in a loop
p = app.presentation
for i in range(5):
    s = p.add_slide(layout="title_only", title=f"Topic {i + 1}")
    s.shapes.add_textbox(app.emu(80), app.emu(200), app.emu(800), app.emu(120),
                         text=f"Body of slide {i + 1}", size=20)

# 2) change text on all slides
for s in app.presentation.slides:
    for sh in s.shapes:
        if sh.text.startswith("Topic"):
            sh.text = sh.text.upper()

# 3) add shapes + images
s = app.presentation.slides[0]
s.shapes.add_shape("round_rect", 60, 60, 400, 120, text="Hello", fill="#1F3864")
s.shapes.add_picture("logo.png", 40, 400, w=160)

# 4) a script that fails must not damage the deck
s.shapes.add_shape("ellipse", 10, 10, 100, 100)
raise ValueError("boom")        # → whole run rolled back, one error line, deck unchanged
```

---

## 5. Storage design

### 5.1 Sidecar layout

For `~/Docs/Report.pptx` the script file is `~/Docs/Report.pptx.moscript` (suffix appended to the **full** file name,
so `Report.pptx` and `Report.odp` never share scripts). It is UTF-8 JSON, human-readable and diffable:

```json
{
  "format": "myoffice-script",
  "version": 1,
  "engine": "python",
  "document": {"name": "Report.pptx", "sha256": "…"},
  "scripts": [
    {"name": "Add agenda", "description": "…", "shortcut": "Ctrl+Shift+A",
     "created": "2026-11-08T10:00:00Z", "modified": "2026-11-08T10:05:00Z",
     "code": "p = app.presentation\n…"}
  ]
}
```

* `document.sha256` is the hash of the `.pptx` **at the time scripts were last written** — informational only
  (renaming/moving a deck must not orphan its scripts; the *sidecar* is what is hashed for trust, §6.3).
* Unknown keys are preserved on rewrite (forward compatibility).
* A sidecar with `version > 1` is refused with a clear message rather than silently downgraded.

### 5.2 Lifecycle

| Event | Behaviour |
| :-- | :-- |
| Deck never saved (untitled) | Scripts live in memory on the document; closing an untitled deck with scripts asks to save |
| Save / Save As | `SlideDocument.save_to()` calls `store.save_sidecar(path, scripts)` **after** the `.pptx` is written; if a sidecar for the old path exists and the path changed, the old one is left alone (no destructive move) and the dialog shows the new sidecar name |
| Open | `store.load_sidecar(path)` runs after `load_pptx`; a malformed file is reported in the output pane and ignored — it can never block opening the deck |
| Deck has no scripts | No sidecar is written; an existing sidecar with zero scripts is deleted only on an explicit "Remove scripts from this file" |
| Portable copy | **Export bundle** (`.mopkg`, §5.5) |

### 5.3 Names & validation

`store.safe_name()` mirrors `save_macro()` (`app/macros/manager.py:34`): `[^\w\- ]+` → `_`, trimmed, non-empty.
Duplicate names get ` (2)`. One script per name; the name is the primary key.

### 5.4 Where scripts live besides the deck

* **This deck** — the sidecar (§5.1).
* **My scripts** — `user_subdir("slide_scripts")` (`app/services/paths.py:94`), global library available from any
  deck. Same file format, one script per `.moscript`/`.py` file (plain `.py` is accepted on import).
* **Examples** — `app/slide/script/examples/*.py`, read-only, shown under an "Examples" node, openable and copyable.

### 5.5 `.mopkg` bundle (designed now, built in M6)

`Report.mopkg` = ZIP containing `document.pptx`, `scripts.moscript`, `meta.json` (`{"format": "myoffice-package", "version": 1}`).
Opening a `.mopkg` extracts to a temp folder, opens the deck, and remembers to re-pack on save. Purpose: sending a deck
*with* its automation as one file without touching the OOXML container.

---

## 6. Security model

### 6.1 What the user is told, in the UI itself

> **Scripts run with the same rights as MyOffice itself.** They are not sandboxed. Only run scripts you trust,
> exactly as you would only open documents you trust.

That sentence is shown in the dialog's footer (one line, muted, always visible) and is section 1 of the Phase 5 guide.
Everything else in this section assumes it.

### 6.2 Trust levels

| Level | How a script reaches it | Effect |
| :-- | :-- | :-- |
| **Trusted (session)** | Written/edited by the user in this session | Runs with no prompt |
| **Trusted (location)** | Sidecar inside `scripts.trusted_dirs`, or in the user's own `slide_scripts` folder, or shipped examples | Runs with no prompt |
| **Approved (content)** | User accepted the consent prompt; SHA-256 of the sidecar recorded in `scripts.approved` | Runs until the file's content changes; any edit re-prompts |
| **Unknown** | New sidecar from anywhere else (download, email, USB, sync) | **Blocked** until the consent prompt is accepted |
| **Disabled** | `scripts.enabled == False` (global switch) | Nothing runs; the Scripts button shows why |

### 6.3 Consent prompt (the only gate that matters)

```
┌ Scripts in this presentation ─────────────────────────────────────┐
│ Report.pptx.moscript                                             │
│                                                                  │
│ This file contains 3 scripts and was not created on this device.  │
│    • Add agenda      • Fix titles      • Insert charts           │
│                                                                  │
│ Scripts can read and change your files and your computer, just   │
│ like MyOffice itself. Only continue if you trust this file.      │
│                                                                  │
│                    [ Don't run ]   [ Trust this file and run ]   │
└──────────────────────────────────────────────────────────────────┘
```

Shown **once per file content**, never for a file the user just created, and never bypassable by a script.

### 6.4 Capability matrix

| Capability | Available | Gate |
| :-- | :-- | :-- |
| Read/write the active deck's model | yes | — |
| Create/delete/save the deck itself | `save()`, `save_as()` | — (a script saving its own document is normal) |
| Read an image file for `add_picture` | yes | first path outside the document's folder asks once per run: *"This script wants to read C:\…\logo.png"* |
| Open another document (`app.open`) | yes | same read gate |
| Write files anywhere | **not exposed** in v1 | — |
| Network | **not exposed** in v1 | — |
| Subprocess / system commands | **not exposed** in v1 | — |
| `import` | whitelist only | `ALLOWED_IMPORTS` in `app/macros/api.py:271-273` (math, statistics, datetime, re, random, json, string, itertools, collections, decimal, fractions, calendar, textwrap, functools, operator) |
| Print to stdout | captured into the output pane | — |
| Modal dialogs | `app.message`, `app.input`, `app.confirm` only | — |

### 6.5 What this model is *not*

`exec` + reduced `__builtins__` is a **guardrail, not a boundary** (`SCRIPTING_RESEARCH.md` §4.2). A deliberately
hostile script can reach `os` through class introspection and the gate in §6.3 is then the only protection that ever
existed. This is why:

1. the consent prompt is per **file content** and cannot be triggered by any script;
2. no hook runs automatically on open (`on_open` is deferred and will be gated by Trusted Location only);
3. the roadmap below exists, with a date-independent trigger: **before any in-file storage ships**, the isolated
   runner must ship first, because in-file scripts are the ones that travel inside decks.

### 6.6 Roadmap to real containment (M7, designed-for now)

Replace in-process `exec` with a worker process: same code, same API, communicated as a journal of operations
(`{"op": "shapes.add_textbox", "args": {...}}`), file reads proxied back to the parent for the permission check.
The parent owns the model; the child owns only the interpreter. A crash, an infinite loop, or a hostile script kills
the child and leaves the document untouched. Trigger conditions for starting it, in priority order: (a) in-file
storage, (b) a request for `open`/`write` capabilities, (c) any report of a shared deck behaving badly.

---

## 7. UI design

### 7.1 Entry points (all real, all work)

| Where | Control | Shortcut |
| :-- | :-- | :-- |
| Slide ribbon | new **Scripts** group → *Scripts* button (`macro` icon, same icon the sheet uses) | `Alt+F8` |
| Slide menu / tab context menu | *Scripts…* | `Alt+F8` |
| Presentation thumbnail context menu | *Scripts for this deck…* | — |

`Alt+F8` matches the spreadsheet (`app/sheet/widget.py:2359`), so one shortcut for "macros" across the suite.

### 7.2 Dialog (`QDialog`, 980×660, resizable)

```
┌ Scripts — Report.pptx ───────────────────────────────────────────────┐
│ [This deck]  [My scripts]  [Examples]            [+] [Import]        │
│ ┌─────────────┬───────────────────────────────────────────────────┐  │
│ │ Add agenda  │  1  p = app.presentation                          │  │
│ │ Fix titles  │  2  for i in range(5):                            │  │
│ │ Insert chart│  3      p.add_slide(title_only, f"Topic {i}")    │  │
│ │             │                                                   │  │
│ │ (read-only) │                                                   │  │
│ └─────────────┴───────────────────────────────────────────────────┘  │
│ [▶ Run  Ctrl+Enter] [■ Stop] [Save  Ctrl+S] [Delete] [Export] [API]  │
│ ┌───────────────────────────────────────────────────────────────────┐ │
│ │ ▶ Add agenda                                                      │ │
│ │ created 5 slides                                                  │ │
│ │ ✔ finished in 0.42 s                                              │ │
│ └───────────────────────────────────────────────────────────────────┘ │
│ Scripts run with the same rights as MyOffice itself. Only run what you trust.│
└──────────────────────────────────────────────────────────────────────┘
```

Behaviour details that matter:

* **Run** — commits any in-progress text edit on the canvas first (`canvas.commit_text_edits()`), runs, prints
  captured output, then refreshes thumbnails/canvas once.
* **Stop** — sets the stop flag; the tracer aborts at the next line and the deck is rolled back
  (`MacroStopped: stopped by user`). Button is enabled only while running.
* **Errors** — `✖ Line 3: TypeError: unsupported operand …`, output pane tinted, deck untouched. No modal box.
* **Unsaved edits** to the script when closing the dialog → "Save changes to <name>?"
* **API** button → a scrollable reference generated from the API's own docstrings (single source of truth: the doc
  strings in `api.py`), not a hand-written HTML page that can rot.
* Untrusted state shows a badge on the tab: `This deck  ⚠ not trusted on this device`.

### 7.3 Editor

`QPlainTextEdit` + the existing `PythonHighlighter` (`app/macros/manager.py:50`) — reused, not copied. Tab = 4 spaces,
`Ctrl+Enter` runs, line numbers in the output pane's error strings. Autocomplete is **not** in v1 (Phase 3 optional
list); the `API` panel covers discoverability.

---

## 8. File-by-file plan

### 8.1 New files

| File | Purpose | Est. LOC |
| :-- | :-- | :-- |
| `app/macros/errors.py` | `MacroError`, `MacroTimeout`, `MacroStopped` — moved out of `api.py` so `app/slide/script` can import them without a cycle; `api.py` re-exports them (backwards compatible) | 15 |
| `app/macros/engine.py` | `execute_python(code, namespace, timeout, name, stop_flag=None) -> str` — the *only* place that calls `compile`/`exec`/`settrace`; maps exceptions to `Line N: Type: msg` | 90 |
| `app/slide/script/__init__.py` | Exports `run_script`, `SlideScriptStore`, `ScriptDialog` | 10 |
| `app/slide/script/api.py` | All proxies from §4 + capability gates (§6.4) | 780 |
| `app/slide/script/store.py` | Sidecar read/write, safe names, provenance, TOFU approvals, `.py` import, examples listing | 220 |
| `app/slide/script/runner.py` | Snapshot bracket, commit suppression, stop flag, output capture | 120 |
| `app/slide/script/dialog.py` | `ScriptDialog` (§7.2), consent prompt, API reference panel | 340 |
| `app/slide/script/examples/*.py` | 4 read-only examples: `add_slides.py`, `fix_titles.py`, `add_pictures.py`, `build_agenda.py` | 60 |

### 8.2 Modified files (small, surgical)

| File | Change | Anchor |
| :-- | :-- | :-- |
| `app/macros/api.py` | import from `errors.py`; `run_macro()` delegates to `engine.execute_python()` (same behaviour, same undo group); `AppAPI` gains `presentation`, `slide`, `emu`, `px`, `log`, `confirm`, `sleep` | `:261-315` (runner), `:199-251` (`AppAPI`) |
| `app/slide/widget.py` | `commit()` suppression guard (`self._macro_depth`); `scripts()` action + ribbon group + menu action; `script_store` created in `__init__`; `save_to()` writes the sidecar; `load()` reads it; `refresh_after_script()` helper | `:346-372` (commit), `:280-300` (save_to), `:253-280` (load), `:1245` (ribbon), `:1116` (actions) |
| `app/services/config.py` | defaults `"scripts": {"enabled": True, "timeout": 30, "trusted_dirs": [], "approved": [], "autosave_with_document": True}` | `:67` |
| `app/services/i18n.py` | new strings if the file enumerates keys (checked: `_t()` falls through to the English text, so nothing is *required*) | — |
| `tests/test_script_api.py`, `tests/test_script_store.py`, `tests/test_script_runner.py` | Phase 4 tests | new |

**Untouched on purpose:** `app/slide/model.py`, `app/slide/canvas.py`, `app/slide/pptx_io.py`, `app/slide/show.py`,
`app/slide/features.py`, `app/macros/manager.py` (the sheet dialog keeps working exactly as it does today),
`installer/setup.iss`, `app/version.py` (version bump decided by you, not silently by me).

---

## 9. Milestones (Phase 3 execution plan)

| # | Milestone | Content | Done when |
| :-- | :-- | :-- | :-- |
| **M0** | Groundwork | `errors.py`, `engine.py`, refactor `run_macro` onto it | Full existing suite still green (313 passed / 1 skipped); sheet macros behave identically |
| **M1** | Read-only API | `PresentationAPI`/`SlideAPI`/`ShapeAPI` reads, `app.presentation` wiring | A script can print deck structure; test proves it |
| **M2** | Write API | slides/shapes/text/style/table/chart/animation creation + mutation | The three brief examples (§4.7, without images) run and change the deck |
| **M3** | Runner | snapshot bracket, commit suppression, stop flag, error mapping, one undo entry | A failing script leaves the deck byte-identical; `Ctrl+Z` undoes one run completely |
| **M4** | Storage | sidecar save/load on open/save, deck tab, my-scripts tab, examples, import/export | Save→reopen→scripts are there; malformed sidecar never blocks opening |
| **M5** | UI | dialog, ribbon/menu/`Alt+F8`, consent prompt, API panel, output pane | Every button in the dialog does real work; no placeholder |
| **M6** | Images & permissions | `add_picture` path gate, `app.open` gate, `.mopkg` bundle export/import | Reading an outside path prompts once; bundle round-trips |
| **M7** | Hardening | save/load edge cases, unicode paths, big decks (200 slides), timeout/stop, docs in code | Phase 4 test matrix green |

Commit discipline: one commit per milestone, message format `slide scripts: M2 write API`, tests included in the same
commit. Rollback of any milestone = revert one commit.

---

## 10. Phase 4 test plan (written now so M-milestones can be judged)

**Functional (must all pass, real model assertions — no mocks):**

1. create 5 slides in a loop → `len(pres.slides) == 6` (1 existing + 5), titles `Topic 1..5`.
2. change text on all slides → every title uppercased; formatting (size/colour) preserved.
3. add shapes + images → shape count/kinds correct; image bytes stored; `image_ext` correct.
4. table + chart via script → cell values and chart series round-trip through `save_pptx`/`load_pptx`.
5. a script raising an error → `MacroError` with `Line N`, and `pres_to_dict(before) == pres_to_dict(after)`.
6. script editing 40 slides, then `Ctrl+Z` → model returns to the pre-run state in one undo entry.
7. timeout: `while True: pass` → `MacroTimeout` within ±1 s, deck unchanged.
8. stop: stop flag set mid-run → `MacroStopped`, deck unchanged.
9. save → reopen → scripts present, code identical, order preserved; unicode names intact.
10. malformed sidecar (bad JSON, wrong version, missing keys) → deck still opens, error surfaced.
11. `add_picture` with a path outside the document → permission callback invoked exactly once per run; denial raises
    `MacroError` and changes nothing.
12. `.mopkg` export → import on a clean profile → identical deck + scripts.

**Security:**

13. `import os` inside a script → `ImportError` with the whitelist message.
14. `__import__("subprocess")` → same.
15. `open("x.txt","w")` is not defined → `NameError` (documented in the guide as "not available").
16. consent: an unknown sidecar runs only after approval; editing one byte of it forces re-approval.
17. `scripts.enabled = False` → run is refused with an explanatory message, no partial edits.

**Isolation/layering:**

18. `app/slide/script/api.py` imports nothing from `app.macros` except `errors` (AST check).
19. no `QDialog`/`QMessageBox` import inside `api.py`/`store.py` (AST check).

**Regression:** the whole existing suite (313 passed / 1 skipped) must stay green at every milestone.

---

## 11. Risks (design-level, updated)

| # | Risk | Mitigation in this design |
| :-- | :-- | :-- |
| 1 | User runs a hostile shared sidecar | §6.2/§6.3 TOFU consent, no auto-run, honest banner, isolated runner before any in-file storage |
| 2 | API drifts from editor behaviour | API calls `SlideDocument` methods (`_insert_shape`, `commit`, `arrange`, `set_transition`…), not a parallel implementation; parity tests in Phase 4 |
| 3 | Undo granularity surprises | one run = one `SnapshotCommand` named `Macro: <name>`; test #6 |
| 4 | Model mutation during iteration (user deletes a slide in a loop) | proxies resolve by object identity; `SlideAPI.index` raises a clear `MacroError` if the slide was removed |
| 5 | Big decks slow (200 slides × rebuild) | rebuild once per run, not per operation (commit suppression); thumbnail timer already debounced 250 ms (`widget.py:230`) |
| 6 | Sidecar invisible to users → "my scripts vanished" | dialog shows the sidecar name; saving a deck with scripts writes it; `File ▸ Properties` style info line in the dialog |
| 7 | Python version drift on user machines | engine only uses stdlib `compile`/`exec`/`sys.settrace`, valid on 3.9+ |
| 8 | Translation coverage | all new strings go through `_t()` like the rest of the app |

---

## 12. Items I need you to confirm (all cheap to change, none blocking M0–M3)

1. **Timeout default 30 s** (same as sheet macros) — or 60 s for slide-heavy scripts?
2. **Shortcut `Alt+F8`** for the slide Scripts dialog (identical to the spreadsheet) — confirmed?
3. **Sidecar written automatically** whenever a deck with scripts is saved — or should saving scripts be an explicit
   action each time? (I recommend automatic: a suite that silently forgets your macros is worse than one that writes
   a small sibling file.)
4. **`.moscript` extension** for the sidecar — fine, or prefer `.pyscript` / `.momacro`?

If you have no preference, I take the recommended option and start M0.

---

## 13. Phase 2 report card

**Done:** all three decisions fixed with reversal costs · architecture and layering rules · complete public object
model (names, properties, methods, semantics) · the four brief examples written against it · storage schema and
lifecycle · trust/permission model with the honest "not a sandbox" statement and a dated containment roadmap ·
UI spec · file-by-file plan with line anchors and LOC estimates · 7 milestones with acceptance criteria · 19-test
Phase 4 matrix · updated risk register.

**Pending:** your confirmation on §12 (4 items, all defaults recommended) → then Phase 3 M0.

**Problems / things I deliberately did not do:** nothing in `app/` was modified; no version bump; no in-file storage
(needs `pptx_io` work + the isolated runner first); unchanged files listed explicitly in §8.2 so the blast radius is
visible before a single line is written.
