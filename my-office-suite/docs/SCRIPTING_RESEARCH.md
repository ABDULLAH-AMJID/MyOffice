# Phase 1 — Research Report: Scripting & Macro Automation for the Presentation Module

**Project:** MyOffice (this repository) · **Date:** 2026-11-08 · **Status:** Phase 1 of 5 — awaiting approval before design
**Prepared by:** engineering · **Audience:** project owner

> **Rule I followed:** nothing about the codebase below is assumed. Every MyOffice claim cites a file and line
> that I opened and read in this session. External claims carry a source link in §9. Anything I could not verify
> is marked **[ASSUMPTION]**.

---

## 0. Executive summary

1. Microsoft PowerPoint's macro system is an **in-process COM/VBA** engine: a rich synchronous object model
   (`Application → Presentation → Slide → Shapes → Shape → TextFrame → TextRange`), a built-in editor, a recorder,
   and event sinks, with macros stored in a separate binary part (`ppt/vbaProject.bin`) inside a `.pptm` package.
   It is powerful **and** it is the single most exploited document feature in Office history — which is why
   Microsoft has spent ten years building fences around it.
2. **MyOffice already has a scripting subsystem** — `app/macros/` — but only for **spreadsheets and writer**.
   It is a **Python** macro engine (`app/macros/api.py`), with an editor dialog, a run/save/import flow, a
   spreadsheet recorder, and a notion of trusted locations (`app/macros/manager.py`). The presentation module
   (`app/slide/`) has **zero** macro integration today: `grep -n "macro" app/slide/widget.py` returns nothing.
3. **Critical finding:** the current macro sandbox is **not a security boundary.** `run_macro()` executes user
   text with `exec()` and a reduced `__builtins__` (`app/macros/api.py:261-302`). That construction is escapable
   in one line via Python introspection (`().__class__.__base__.__subclasses__()…`), a fact demonstrated publicly
   and acknowledged by CPython's own position that application-level Python sandboxes are not security boundaries.
   Today that is *acceptable* because macros are only run by the user, from the user's own machine — but it must be
   stated plainly before we extend the same engine into a document format that people will share.
4. **Recommendation:** build the presentation scripting feature as a **Python object model** (`app.slide`) that
   matches the existing macro UX 1:1, executed through a **transactional command journal** (the script's operations
   are queued against a proxy, then applied to the live model in one undoable step), gated by a **disabled-by-default
   trust model** modelled on Office's Trust Center. Because the journal *is* a message protocol, the same code can be
   moved into an isolated worker process later without changing the macro API — that is the upgrade path to real
   containment (§7, option C).
5. Do **not** embed a second language (JS/Lua/DSL) in v1. It doubles the surface for no user benefit: the suite
   already teaches users Python macros, the presentation model is Python dataclasses, and Python is the only option
   that needs no bridge. A custom DSL is the weakest option here (high effort, low capability).

---

## 1. Scope, method, and what exists today

### 1.1 Method

* Read the presentation module end-to-end (6 files, 4,930 lines) and the macro subsystem (4 files, 579 lines).
* Read the models, the undo mechanism, the save path, and the settings/uninstall code that touches macros.
* Compared against Microsoft VBA/Office.js, LibreOffice UNO, Google Apps Script, python-pptx.
* Evaluated embedding candidates (CPython, QuickJS/V8, Lua/Luau, custom DSL) on security, performance, learning
  curve, and integration cost for a **PySide6 desktop app**.

### 1.2 The project, as read from the repository

| Template field | Actual value (evidence) |
| :-- | :-- |
| Project name | **MyOffice** (`app/version.py`, `BINARY_VERSION = "1.6.5"`) |
| Stack | **Python 3.11** target + **PySide6 6.12** (`requirements*.txt`, `app/main.py`) |
| Platforms | **Windows** desktop (Inno Setup, `installer/setup.iss`), plus a companion **Android** app (`my-office-android/`) that hosts the same web UI |
| Presentation module | **Exists and is mature** — `app/slide/` (`model.py` 286, `canvas.py` 847, `widget.py` 1416, `pptx_io.py` 763, `show.py` 629, `features.py` 989 lines) |
| Codebase | `/home/user/my-office-suite` (mirrored to `github.com/ABDULLAH-AMJID/MyOffice`) |

### 1.3 Presentation module structure (file-by-file, as it really is)

| File | Role | What a scripting layer needs from it |
| :-- | :-- | :-- |
| `app/slide/model.py` | Pure dataclasses: `Presentation`, `Slide`, `Shape`, `Run`, `Paragraph`, `Animation`, `Transition`, `Theme`; `make_slide()` factory; `*_to_dict` / `*_from_dict`; `clone_shape()` | **The object model already exists.** Units are EMU; helpers `px()` / `emu()` convert to pixels/screen coords |
| `app/slide/widget.py` | `SlideDocument` — the editor: thumbnails, canvas, notes, animations, ribbon; undo via `SnapshotCommand` (**full deep-copy** of the model), `commit(text)`, `snapshot()`/`restore()` | A macro run can be wrapped in exactly one `SnapshotCommand` → **one Ctrl+Z**, and a failed run can be rolled back with `restore()` |
| `app/slide/canvas.py` | Interactive canvas, shape items, text editing | Not needed by scripts (scripts act on the model) |
| `app/slide/pptx_io.py` | `save_pptx()` / `load_pptx()` via python-pptx; hand-written `<p:timing>` animation XML; per-shape-kind writers | **Save path rebuilds the package from scratch** — see §6.4 for the consequence on storing macros in-file |
| `app/slide/show.py` | Full-screen presenter, transitions, animations | Event hooks (`OnSlideShowPageChange` in VBA terms) would live here |
| `app/slide/features.py` | Extra tools (design ideas, etc.) | — |

### 1.4 The existing macro subsystem (must be extended, not replaced)

| File | What it does today |
| :-- | :-- |
| `app/macros/api.py` | `AppAPI` (`app.sheet`, `app.writer`, `app.message`, `app.input`, `app.status`, `app.open`), `SheetAPI` + `RangeAPI` (A1 refs, `format()`, `sort()`, `recalc()`), `WriterAPI`; and `run_macro(code, main, timeout=30)` which compiles + `exec()`s with `SAFE_BUILTINS`, `ALLOWED_IMPORTS`, a `sys.settrace` timeout, and one undo group |
| `app/macros/manager.py` | `MacroDialog`: list (bundled + user), edit with Python syntax highlighting, **Run / Save / New / Import / Export / Delete**, an output pane; `is_trusted()` against `macros.trusted_dirs` + the user macros folder |
| `app/macros/recorder.py` | `MacroRecorder` — turns spreadsheet actions into Python lines, then saves via `save_macro()` |
| `app/services/config.py:67` | defaults `"macros": {"trusted_dirs": []}` |
| `app/services/uninstall.py:38` | keeps the user `macros` folder on uninstall (user data is never deleted) |

**Where the presentation module stands:** the spreadsheet exposes `macros()` (`app/sheet/widget.py:2064`) and a
menu action with `Alt+F8` (`app/sheet/widget.py:2359`). `app/slide/widget.py` has **no** macro entry, no
recorder hook, and no `app.macros` import. That is the gap this feature fills — and the reason the design must
reuse the same dialog and the same `AppAPI` instead of inventing a parallel system.

---

## 2. How VBA works in PowerPoint (the benchmark)

### 2.1 Object model

The documented hierarchy is `Application` → `Presentations` → `Presentation` → `Slides` → `Slide` → `Shapes` →
`Shape` → `TextFrame` → `TextRange`, with collection patterns (`Shapes(1)`, `Shapes.Range()`, `Placeholders`) and
shape creation through `Shapes.AddShape(Type, Left, Top, Width, Height)`, `AddTextbox`, `AddPicture`, `AddTable`,
`AddChart`, `AddMediaObject`, etc. Coordinates are in **points**, and every mutation is synchronous and immediate.

* `Shapes` object — properties/methods and collection semantics: MicrosoftDocs VBA-Docs `PowerPoint.Shapes` [S1]
* `Shapes.AddShape` signature and behaviour: OfficeDev VBA-content [S2]
* Common shape creation methods (`AddPicture`, `AddTextbox`, `AddPolyline`, `AddMediaObject`) and text via
  `Shape.TextFrame.TextRange.Text`: [S3]

MyOffice's model maps almost 1:1 — `Slide.shapes` ↔ `Slide.Shapes`, `Shape.paragraphs/runs` ↔
`TextFrame.TextRange`, `Shape.x/y/w/h` ↔ `Left/Top/Width/Height` (EMU vs points; `px()`/`emu()` already convert).

### 2.2 The editor, the recorder, events, and `.pptm`

| Piece | How PowerPoint does it | Relevance to us |
| :-- | :-- | :-- |
| Editor | VBA IDE inside the app, with breakpoints, watch window, immediate window | We already have an editor + output pane; autocomplete/breakpoints are Phase 3 "optional" |
| Recorder | Records a subset of actions into a new macro | We already have `MacroRecorder` for sheets; a slide recorder is feasible because every edit funnels through `SlideDocument` methods |
| Events | `Application.PresentationOpen`, `SlideShowNextSlide`, `WindowSelectionChange`, etc. (class-module sinks) | We can offer a small, explicit set (`on_slide_added`, `on_open`, `on_slide_show_start`) rather than COM-style sinks [ASSUMPTION: an explicit hook list is enough for v1; full event parity is not a requirement you stated] |
| Storage | `.pptm` = same OOXML package as `.pptx` **plus** `ppt/vbaProject.bin` and a `macroEnabled.12` content type; `.pptx` is not *permitted* to declare it | If macros live in-file, we must define our own part + content type and be explicit that this is **MyOffice Script**, not VBA — see §6.4 [S4][S5] |

The storage detail matters commercially: `.pptm` is the signal that lets antivirus, mail gateways and users treat a
deck as *code*. Whatever we store must be equally explicit, never silently embedded in a `.pptx`.

---

## 3. Alternatives to VBA (and why none of them fits MyOffice as-is)

| Option | What it is | Strengths | Fatal-for-us weaknesses |
| :-- | :-- | :-- | :-- |
| **Office.js add-ins** | Web add-in running in a sandboxed webview, talking to Office through versioned requirement sets | Cross-platform, sandboxed by design, HTTPS-only, permission-declared; resource guards (5 s unresponsive → restart, 90 % CPU warning) [S6][S7] | It is *Microsoft's* extension model inside *Microsoft's* app. Useless for a third-party suite. It is also **far less capable than VBA** — practitioners report missing APIs: no shape reordering, no animation access, incomplete shape coverage [S6] |
| **LibreOffice UNO** | Remote/embedded object model over a socket (`uno://`), driven by Basic, Python, Java, JS | Genuinely scriptable, headless-capable, Python-friendly (`pyuno`) | Requires shipping/hosting LibreOffice or reimplementing the bridge; commands are `.uno:` dispatch strings — stringly-typed, fragile, poorly documentable for end users [S8][S9] |
| **Google Apps Script** | Server-side JS (V8) with `SlidesApp` | Zero install, good autocomplete | Cloud-only: needs Google login + network, 6-minute per-run cap (varies by account tier), daily quotas, and it runs *their* slides, not local `.pptx` files [S10]. Directly contradicts MyOffice's 100 % offline promise |
| **python-pptx** | A library, not a scripting runtime | Already used by `app/slide/pptx_io.py`; pure Python; deterministic | Not an engine and **not a macro format** — no animation API (we hand-write `<p:timing>` in `pptx_io.py` precisely because of this), no SmartArt authoring, no rendering, no events [S11][S12]. It is our *persistence* layer, not our automation layer |

**Conclusion:** none of these is a candidate. They either belong to another vendor's product (Office.js, Apps Script)
or require embedding a second office suite (UNO), or are not a runtime at all (python-pptx). The remaining question
is only *which language engine* we host inside MyOffice.

---

## 4. Choosing the engine

### 4.1 Comparison on the four criteria you set

| Criterion | **CPython (in-process)** | **CPython (isolated subprocess)** | **QuickJS / V8 (embedded JS)** | **Lua / Luau** | **Custom DSL** |
| :-- | :-- | :-- | :-- | :-- | :-- |
| Security vs. hostile code | **None.** `exec` + restricted builtins is escapable in one line | **Real**, if the child gets a restricted token / no filesystem or network handles and a job-object CPU/memory cap | **Strong.** Separate engine; `setMemoryLimit` + interrupt handler; no fs/net unless the host injects them [S13][S14] | **Good** if you ship an empty environment and forbid bytecode; libraries like `io`, `os`, `dofile` must be removed; Luau does this by design [S15][S16] | **Best by construction** — no capabilities unless you implement them |
| Security vs. accidental damage (bugs, infinite loops) | Timeout via `sys.settrace`; rollback via snapshot | Same + process kill | Memory cap + interrupt handler (a `while(true)` does not allocate, so the *interrupt* is the only defence) [S13] | Debug hook timeout; memory is unbounded unless you patch the allocator [S16] | Trivial |
| Performance for document automation | Excellent; direct object access, no marshalling | Slight cost per call (IPC) | Fine, but every property crosses a binding layer | Fine | Fine |
| Learning curve for users | **Zero for this suite** — Python macros already exist for sheets | Same | New language for existing users; JS is common but the API is ours anyway | Unfamiliar to most office users | New language, new docs, new tooling |
| Integration cost in PySide6 | **Lowest** — objects are Python; `app.slide` is a thin wrapper over dataclasses | Medium — needs a JSON protocol + worker lifecycle | High — bundle a native library into PyInstaller, write a full binding layer, redefine every type | High — same as JS, plus a smaller ecosystem | Highest — you own the parser, semantics, errors, and docs |
| Licensing | PSF (fine) | PSF | QuickJS MIT / V8 BSD (fine) | MIT/Luau MIT (fine) | n/a |

### 4.2 The uncomfortable fact about Python sandboxes

Executing untrusted Python with a reduced `__builtins__` is **not** isolation. Any object reaches the whole class
tree — `().__class__.__base__.__subclasses__()` — and from there imports `os` and runs commands, with no builtins at
all [S17][S18]. Even `RestrictedPython`, which blocks dunder access at compile time, is described by its own
practitioners as "better, but not a real sandbox" [S17]. Guidance for 2026 is blunt: *"application-level Python
sandboxes have been broken so many times that CPython refuses to support them as a security boundary; use OS-level
isolation"* [S19].

**This applies to our existing `run_macro()` today.** It is a *guardrail against mistakes* (timeouts, import
whitelist, one undo step) — not a *security control*. Any design that runs macros from shared documents in-process
is therefore unacceptable, no matter how good the API is.

### 4.3 Why not add JavaScript, then?

Because it would not be a security improvement *of this feature* — it would be a second language platform:
a native QuickJS build inside PyInstaller, a complete binding layer (numbers, strings, arrays, objects, methods,
exceptions, property get/set semantics), a second set of docs, and a second mental model for users who already write
`app.sheet["A1"] = 1`. A JS engine's sandboxing only pays off if the engine is where untrusted code runs — which is
exactly what "option C" (isolated runner) achieves for Python too, with no new language [ASSUMPTION: your users are
more likely to reuse Python knowledge than to learn a new scripting language; if you expect to import third-party JS
automation, tell me and I will re-cost this].

---

## 5. Macro security: what the industry learned, and what we must copy

Microsoft's model is worth copying almost verbatim, because it is the result of ~25 years of incidents:

| Control | What Microsoft does | What MyOffice should do |
| :-- | :-- | :-- |
| **Off by default** | Default is "disable all macros with notification"; Windows blocks macros from the internet outright, with *no* Enable button [S20] | Macros **off** by default. A deck containing scripts opens read-only w.r.t. scripting until the user enables them for that document |
| **Provenance marking** | Mark of the Web (NTFS `ZoneId`) tags downloads; Office refuses to run macros in marked files unless trusted-location / trusted-publisher / previously approved [S20][S21] | Record where a script came from (created here, imported, downloaded) and treat "imported/downloaded" as untrusted |
| **Trusted locations** | Folders the admin declares safe bypass MOTW checks [S20] | Already half-built: `macros.trusted_dirs` in `app/services/config.py:67`, enforced by `is_trusted()` in `app/macros/manager.py` |
| **Digital signatures** | "Disable all except digitally signed macros" is the recommended enterprise baseline; signature ties code to a publisher [S20] | Optional Ed25519/RSA signing of a macro file + a known-publisher list. Cheap to add, and it composes with the OTA key we already ship |
| **Explicit user consent per file** | Trust Bar: user must click Enable Content; policy can remove the option entirely [S20] | A one-time, per-document "This deck wants to run scripts. Run?" prompt that names the file and shows the script names |
| **Isolation for the app itself** | Microsoft's own guidance for high-risk content is Protected View / Application Guard (a separate, restricted process) | Option C below — the isolated runner |
| **No silent auto-run on open** | Never automatic except trusted locations + policy | Same: **no** `on_open` hook until the document is trusted |

Also worth stating in the user guide: PowerPoint for the web and mobile and Google Slides refuse to run VBA at all
[S5][S4] — a well-known interoperability cliff. If we store scripts in a file, other tools will simply ignore them,
and we must never pretend otherwise.

---

## 6. What MyOffice has today — and the three gaps

### 6.1 Gap 1 — no presentation API surface

There is no `app.slide` in `app/macros/api.py`. The `AppAPI` exposes `.sheet` and `.writer` only. A macro that wants
to touch a deck has no entry point, and the slide widget has no macro menu, no recorder hook, and no "run on open".

### 6.2 Gap 2 — the security posture is documented nowhere and enforced weakly

`run_macro()` is a guardrail, not a boundary (§4.2). `is_trusted()` exists, but trust currently only changes whether
the dialog *asks*; it does not restrict what the script can do. There is no per-document consent, no provenance, and
no signature concept.

### 6.3 Gap 3 — no transactional application to the model

The sheet API mutates the live document as it runs, then relies on Qt's undo stack for reversal. For slides this is
workable because `SlideDocument` already supports `snapshot()`/`restore()` and a single `SnapshotCommand`. But a script
that *writes* through a proxy and is applied only on success gives us three things for free: rollback on error, a
single undo entry, and a replayable journal (which is also the recorder's output format, and later the IPC format).

### 6.4 A storage constraint you must know about before Phase 2

`save_pptx()` (`app/slide/pptx_io.py:42`) **constructs a brand-new python-pptx `Presentation`** and writes our model
into it. It does not open the original package and patch it — which means:

* Any custom part written into a saved file (macros, scratch data, our own metadata) **must be added explicitly**
  during save, or it will vanish on the next save.
* Precedent for doing this correctly exists in the spreadsheet: `app/sheet/model.py:409` keeps
  `vba_source: bytes | None` — the original `.xlsm` package — precisely so foreign macro parts survive a round trip.

So "macros inside the document" is *possible* but is real work in `pptx_io` (write part + content-type override +
relationship + preserve unknown parts on load). The alternative — a **sidecar** (`Deck.pptx` + `Deck.moscript`)
— is dramatically simpler, keeps `.pptx` clean and portable, and still gives one logical bundle if we offer
"Export as .mopkg (zip)". This is decision **D2** below.

---

## 7. Recommendation

### 7.1 Object model shape (preview only — full spec in Phase 2)

Deliberately VBA-familiar, Python-natural, and small enough to document completely:

```python
p = app.slide.presentation
p.title = "Q3 Review"

s = p.add_slide(layout="title_content", title="Welcome", body="Agenda")
for i in range(5):                                  # "create 5 slides from a loop"
    s = p.slides.add(layout="title_only", title=f"Topic {i+1}")

for slide in p.slides:                              # "change text in all slides"
    for sh in slide.shapes:
        if sh.placeholder == "title":
            sh.text = sh.text.upper()

box = p.slides[1].shapes.add_textbox(60, 120, 480, 80, text="Hello")
box.fill, box.line, box.rotation = "#1F3864", "#FFFFFF", 0
p.slides[1].shapes.add_picture(app.path("logo.png"), 40, 40, 120, 120)   # permission-gated
app.message("Done")
```

Methods mirror what `SlideDocument` already does (`new_slide`, `add_shape_at`, `insert_picture`, `insert_table`,
`insert_chart`, `duplicate_slide`, `delete_slide`, `move_slide`, `apply_theme`) so behaviour cannot drift from the UI.

### 7.2 Execution options (costed honestly)

| | **A. In-process, as today** | **B. In-process + journal + policy** *(recommended v1)* | **C. Isolated runner process** *(recommended v2)* | **D. Embedded JS** |
| :-- | :-- | :-- | :-- | :-- |
| User-facing power | Full | Full | Full | Full |
| Crash/timeout containment | Weak (a C-level crash kills the app; loop timeout only) | Good (rollback + timeout + single undo) | **Strong** (kill the child) | Good |
| Hostile-code containment | **None** | **None** (defaults + consent only) | **Strong** (OS-level) | Strong |
| Effort | S | **M** | L | L–XL |
| Fits existing UX | Yes | Yes | Yes | No (new language) |
| Reuses `SnapshotCommand` | Partly | **Yes** | Yes | Yes |

**Recommendation: B now, C next.** B is a genuine, testable improvement in *reliability* and in *policy* (off by
default, trusted locations, per-document consent, provenance, optional signature) while keeping the macro API stable.
C then reuses the same journal as its wire protocol — meaning C is an engine swap behind a flag, not a rewrite.
Until C ships, the docs and UI must say plainly: **MyOffice Script runs with the same rights as MyOffice itself;
only run scripts you trust.**

### 7.3 What I would need from you to start Phase 2

Three decisions (asked in chat, not assumed):

* **D1 — language:** Python (reuse `app/macros`) **[my recommendation]** / embedded JS / Lua / DSL.
* **D2 — storage:** sidecar file next to the deck **[recommendation: simplest, keeps `.pptx` clean]** / in-file
  custom part (requires `pptx_io` work) / both (sidecar by default, "bundle into file" as an option).
* **D3 — security posture for v1:** documented-trust model (off by default + consent + trusted locations)
  **[recommendation]** / additionally require signatures for imported scripts / go straight to the isolated runner
  (delays the feature).

---

## 8. Risks (ranked)

| # | Risk | Likelihood | Impact | Mitigation |
| :-- | :-- | :-- | :-- | :-- |
| 1 | A user runs a shared deck's script and it does something harmful; we are blamed | Medium (as soon as in-file storage exists) | High (trust + security) | Off by default, per-document consent, provenance, signature, and the isolated runner on the roadmap. Never auto-run on open |
| 2 | The journal/proxy diverges from the UI's own edit paths | Medium | Medium (silent behaviour differences) | The API calls the **same** `SlideDocument` methods; a parity test asserts both paths produce identical models |
| 3 | Python API churn breaks saved scripts | Medium | Medium | Version the API (`app.api_version`), keep old names as aliases, test a frozen fixture deck per release |
| 4 | In-file storage corrupts decks for other readers | Low if implemented correctly | High | Only write a custom part + content type we own; validate with python-pptx reopen + `zipfile` part inspection in tests; never modify `.pptx` content types of foreign packages |
| 5 | Scripts make decks slow (e.g. 200 slides × shapes) | Medium | Low/Medium | Journal applies one model change + one rebuild; add a step/time cap and a progress indicator |
| 6 | Undo granularity surprises users | Medium | Low | One macro run = one undo entry, named "Macro: <name>" (already the sheet convention) |
| 7 | Cross-platform drift (Android has its own presenter) | Certain if ignored | Medium | v1 is Windows-desktop only; scripts are stored as plain text so Android can *view* them later |

---

## 9. Sources

* [S1] MicrosoftDocs VBA-Docs — `PowerPoint.Shapes` object (collections, `Shapes.Range`, `AddShape` usage, placeholders) — https://github.com/MicrosoftDocs/VBA-Docs/blob/main/api/PowerPoint.Shapes.md
* [S2] OfficeDev VBA-content — `Shapes.AddShape` method signature and remarks — https://github.com/OfficeDev/VBA-content/blob/master/VBA/PowerPoint-VBA/articles/shapes-addshape-method-powerpoint.md
* [S3] "Dealing with Shape Objects" — shape creation methods (`AddPicture`, `AddTextbox`, `AddPolyline`, `AddMediaObject`), `Shape.TextFrame.TextRange.Text` — https://flylib.com/books/en/2.837.1.78/1/
* [S4] PPTM format — `.pptx` + `ppt/vbaProject.bin`, `macroEnabled.12` content type, MIME `application/vnd.ms-powerpoint.presentation.macroEnabled.12`; saving as `.pptx` strips macros — https://fileformer.com/en/formats-explained/what-is-pptm · https://www.file-extension.info/format/pptm
* [S5] PPTX format notes — plain `.pptx` cannot carry a VBA project; browsers and web apps refuse to run VBA — https://file-extensions.com/docs/pptx
* [S6] Microsoft Q&A — PowerPoint add-in missing APIs (no shape reordering, no animations, limited shape coverage vs VBA) — https://learn.microsoft.com/en-us/answers/questions/5250556/powerpoint-office-addin-missing-api
* [S7] Office Add-ins vs VSTO — requirement sets, sandboxing, resource limits (5 s unresponsive restart, CPU/memory guards) — https://www.progressiverobot.com/2026/09/13/vsto-add-ins-vs-microsoft-office-add-ins-key-differences/
* [S8] LibreOffice UNO — dispatch commands, `executeDispatch`, headless scripting — https://ask.libreoffice.org/t/dispatcher-executedispatch-with-headless-pyuno-for-wrap-in-background/95344
* [S9] LibreOffice UNO macro languages overview — https://christopher5106.github.io/office/2015/12/06/openoffice-libreoffice-automate-your-office-tasks-with-python-macros.html
* [S10] Google Apps Script quotas — 6 min per execution, daily caps, cloud-only runtime — https://modelmonkey.io/blog/apps-script-execution-time-limit-6-minutes · https://automationatlas.io/tools/google-apps-script/
* [S11] python-pptx limitations — no animation API (open request since 2018), no rendering, no SmartArt authoring — https://slideforge.dev/blog/python-pptx-limitations-we-solved
* [S12] python-pptx animations — `<p:timing>` has no object model; raw XML injection is fragile — https://slideforge.dev/blog/python-pptx-animations
* [S13] QuickJS sandboxing — `setMemoryLimit`, `setInterruptHandler` (a CPU loop allocates nothing, so the interrupt is the real defence), no fs/net by construction — https://foundrysoft.co/blog/wasm-sandbox-ai-code-pyodide-quickjs
* [S14] quickjs-emscripten API — `runtime.setMemoryLimit`, `setMaxStackSize`, `setInterruptHandler` — https://www.npmjs.com/package/quickjs-emscripten
* [S15] Luau sandboxing — removal of `io`/`os`/`dofile`, bytecode ban, read-only globals — https://luau.org/sandbox/
* [S16] Lua sandboxing — `setfenv`/`_ENV` environments, unsafe functions to strip, need for bytecode refusal and external CPU/memory limits — http://lua-users.org/wiki/SandBoxes · https://stackoverflow.com/questions/5134287/embedded-lua-timing-out-rogue-scripts-e-g-infinite-loop-an-example-anyone
* [S17] Python `exec` sandbox escape in one line; `RestrictedPython` as a partial mitigation — https://pyobfuscate.com/blog/python-sandbox-run-untrusted-code
* [S18] HackTricks — bypassing Python sandboxes via class traversal — https://hacktricks.wiki/en/generic-methodologies-and-resources/python/bypass-python-sandboxes/index.html
* [S19] "Application-level Python sandboxes are not a security boundary; use OS-level isolation" — https://chs.us/guides/python/
* [S20] Microsoft — "Macros from the internet are blocked by default in Office": MOTW, Trusted Locations, trusted publisher, `VBA Macro Notification Settings` policy matrix — https://learn.microsoft.com/en-us/microsoft-365-apps/security/internet-macros-blocked
* [S21] NCSC (UK) — Macro Security for Microsoft Office: recommended baselines, block-from-internet, trusted locations, AMSI — https://www.ncsc.gov.uk/sites/default/files/2026-08/Macro-Security-for-Microsoft-Office.pdf
* [S22] Office Watch — how Mark of the Web blocks macros and what unblocking requires — https://office-watch.com/2024/mark-of-the-web/

---

## 10. Phase 1 report card

**Done:** codebase review (presentation + macro subsystem, undo, save path, config), competitor analysis
(VBA, Office.js, UNO, Apps Script, python-pptx), engine evaluation with the Python-sandbox reality check, security
model research, storage constraint discovered in `pptx_io`, recommendation with costed options and risks.

**Pending:** your three decisions (D1 language, D2 storage, D3 v1 security posture) — then Phase 2 design
(architecture, full object-model spec, security design, file-by-file plan with milestones).

**Problems found:** (1) no presentation macro surface at all; (2) the existing `run_macro` is a guardrail, not a
sandbox, and that is currently undocumented; (3) `save_pptx` rebuilds the package, so in-file script storage is
genuinely more work than it looks, and the spreadsheet's `vba_source` round-trip is the precedent to follow.
