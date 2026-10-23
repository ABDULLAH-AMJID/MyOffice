"""Home tab: new documents, recent files, template gallery and the tools gallery."""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMenu, QPushButton, QScrollArea, QStackedWidget,
                               QToolButton, QVBoxLayout, QWidget)

from app.services.config import cfg
from app.services.i18n import _t
from app.shell.icons import badge, badge_pixmap, icon
from app.shell.tools_registry import CATEGORIES, TOOLS, TOOLS_BY_ID
from app.version import APP_NAME, APP_VERSION

KIND_OF_EXT = {}
for _k, _exts in {
    "writer": (".docx", ".docm", ".dotx", ".doc", ".wpt", ".rtf", ".odt", ".txt", ".md", ".html", ".htm", ".wps"),
    "sheet": (".xlsx", ".xlsm", ".xls", ".csv", ".tsv", ".ett", ".ods", ".xltx"),
    "slide": (".pptx", ".ppt", ".pps", ".ppsx", ".dpt", ".odp", ".potx"),
    "pdf": (".pdf",),
    "photos": (".png", ".jpg", ".jpeg", ".jfif", ".webp", ".bmp", ".tif", ".tiff", ".gif", ".ico", ".heic", ".heif"),
    "vault": (".movault",),
}.items():
    for _e in _exts:
        KIND_OF_EXT[_e] = _k


def tile(text: str, ic: QIcon, slot, tip: str = "", size=(118, 100), obj="Tile") -> QToolButton:
    b = QToolButton()
    b.setObjectName(obj)
    b.setText(text.replace("&", "&&"))  # "Backup & Recovery": a single & would be a mnemonic marker
    b.setIcon(ic)
    b.setIconSize(QSize(38, 38) if obj == "Tile" else QSize(28, 28))
    b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
    b.setFixedSize(*size)
    b.setToolTip(tip or text)
    b.setCursor(Qt.PointingHandCursor)
    b.clicked.connect(slot)
    return b


def card() -> QFrame:
    f = QFrame()
    f.setObjectName("Card")
    return f


class RecentList(QListWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.setIconSize(QSize(30, 30))
        self.setSpacing(1)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.itemActivated.connect(lambda it: self.main.open_path(it.data(Qt.UserRole)))
        self.setFrameShape(QFrame.NoFrame)

    def refresh(self, filt: str = ""):
        self.clear()
        for p in cfg().recent_files(existing_only=False):
            name = os.path.basename(p)
            if filt and filt.lower() not in p.lower():
                continue
            kind = KIND_OF_EXT.get(Path(p).suffix.lower(), "writer")
            exists = os.path.exists(p)
            when = datetime.fromtimestamp(os.path.getmtime(p)).strftime("%Y-%m-%d %H:%M") if exists else _t("missing")
            it = QListWidgetItem(badge(kind), f"{name}\n{os.path.dirname(p)}   \u2022   {when}")
            it.setData(Qt.UserRole, p)
            it.setToolTip(p)
            if not exists:
                it.setForeground(Qt.gray)
            self.addItem(it)
        if self.count() == 0:
            it = QListWidgetItem(_t("No recent documents yet \u2014 create a new one or open a file."))
            it.setFlags(Qt.NoItemFlags)
            self.addItem(it)

    def _menu(self, pos):
        it = self.itemAt(pos)
        if not it or not it.data(Qt.UserRole):
            return
        path = it.data(Qt.UserRole)
        m = QMenu(self)
        m.addAction(icon("open"), _t("Open"), lambda: self.main.open_path(path))
        m.addAction(icon("folder"), _t("Open Containing Folder"),
                    lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path))))
        m.addAction(icon("delete"), _t("Remove from List"), lambda: self._remove(path))
        m.exec(self.mapToGlobal(pos))

    def _remove(self, path):
        rec = [p for p in cfg().get("recent_files", []) if p != path]
        cfg().set("recent_files", rec, save=True)
        self.refresh()


class HomeView(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        outer = QHBoxLayout(self)
        outer.setContentsMargins(28, 22, 28, 22)
        outer.setSpacing(20)
        left = QVBoxLayout()
        hour = datetime.now().hour
        greet = _t("Good morning") if hour < 12 else _t("Good afternoon") if hour < 18 else _t("Good evening")
        author = cfg().get("author", "")
        h1 = QLabel(f"{greet}{', ' + author if author else ''}")
        h1.setObjectName("H1")
        left.addWidget(h1)
        from PySide6.QtCore import QDate, QLocale

        sub = QLabel(QLocale().toString(QDate.currentDate(), QLocale.LongFormat))
        sub.setObjectName("Muted")
        left.addWidget(sub)
        left.addSpacing(12)
        h = QLabel(_t("Create new"))
        h.setObjectName("H2")
        left.addWidget(h)
        self.create_tiles = [
            tile(_t("Document"), badge("writer"), main.new_writer, _t("New Writer document (Ctrl+N)")),
            tile(_t("Spreadsheet"), badge("sheet"), main.new_sheet),
            tile(_t("Presentation"), badge("slide"), main.new_slide),
            tile(_t("PDF"), badge("pdf"), main.open_pdf, _t("Open a PDF to view or edit")),
            tile(_t("Photos"), badge("photos"), main.open_photos, _t("Open a picture to view or edit")),
            tile(_t("From Template"), icon("templates", "#7c3aed"), main.show_templates),
            tile(_t("Open File"), icon("open", "#0f766e"), main.open_dialog, _t("Open (Ctrl+O)")),
        ]
        row = QHBoxLayout()
        self.create_grid = QGridLayout()
        self.create_grid.setSpacing(10)
        row.addLayout(self.create_grid)
        row.addStretch(1)
        left.addLayout(row)
        left.addSpacing(16)
        rh = QHBoxLayout()
        h = QLabel(_t("Recent"))
        h.setObjectName("H2")
        rh.addWidget(h)
        rh.addStretch(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText(_t("Search recent files"))
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(260)
        rh.addWidget(self.search)
        clear = QPushButton(_t("Clear"))
        clear.clicked.connect(self._clear_recent)
        rh.addWidget(clear)
        left.addLayout(rh)
        rc = card()
        rl = QVBoxLayout(rc)
        rl.setContentsMargins(6, 6, 6, 6)
        self.recent = RecentList(main)
        rl.addWidget(self.recent)
        left.addWidget(rc, 1)
        self.search.textChanged.connect(self.recent.refresh)
        outer.addLayout(left, 3)

        right = QVBoxLayout()
        qc = card()
        ql = QVBoxLayout(qc)
        h = QLabel(_t("Quick tools"))
        h.setObjectName("H2")
        ql.addWidget(h)
        self.quick_grid = QGridLayout()
        self.quick_grid.setSpacing(4)
        self.quick_tiles = []
        for tid in ("pdf_merge", "pdf_compress", "pdf_convert", "pdf_ocr", "pdf_split", "pdf_protect",
                    "collab", "cloud_sync", "backup", "vault", "shortcuts", "capture"):
            t = TOOLS_BY_ID[tid]
            self.quick_tiles.append(tile(_t(t.title), icon(t.icon, "#3b5bdb"), lambda _=False, x=tid: main.run_tool(x),
                                         _t(t.description), (126, 70), "ToolTile"))
        ql.addLayout(self.quick_grid)
        more = QPushButton(_t("All {0} tools \u2192").format(len(TOOLS)))
        more.clicked.connect(lambda: main.home.show_page("tools"))
        ql.addWidget(more)
        right.addWidget(qc)
        right.addStretch(1)
        outer.addLayout(right, 2)
        self._columns = None
        self.fit_width(10_000)

    # responsive layout: 1366-px laptops and smaller screens must not need horizontal scrolling
    WIDE, MEDIUM = 1290, 1160
    QUICK_TILE_WIDTH = {3: 140, 2: 150}

    def fit_width(self, available: int) -> None:
        """Arrange the tiles for *available* pixels: 6 + 3 columns, 6 + 2, or 3 + 2."""
        cols = (6, 3) if available >= self.WIDE else (6, 2) if available >= self.MEDIUM else (3, 2)
        if cols == self._columns:
            return
        self._columns = cols
        for t in self.quick_tiles:
            t.setFixedWidth(self.QUICK_TILE_WIDTH[cols[1]])
        for grid, tiles, n in ((self.create_grid, self.create_tiles, cols[0]),
                               (self.quick_grid, self.quick_tiles, cols[1])):
            for t in tiles:
                grid.removeWidget(t)
            for i, t in enumerate(tiles):
                grid.addWidget(t, i // n, i % n)
        self.updateGeometry()

    def refresh(self):
        self.recent.refresh(self.search.text())

    def _clear_recent(self):
        cfg().set("recent_files", [], save=True)
        self.refresh()


class TemplatesView(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        v = QVBoxLayout(self)
        v.setContentsMargins(28, 22, 28, 22)
        h = QLabel(_t("Templates"))
        h.setObjectName("H1")
        v.addWidget(h)
        bar = QHBoxLayout()
        self.kind = QComboBox()
        for label, k in ((_t("All"), ""), (_t("Writer"), "writer"), (_t("Spreadsheets"), "sheet"),
                         (_t("Presentation"), "slide")):
            self.kind.addItem(label, k)
        self.kind.currentIndexChanged.connect(self.refresh)
        bar.addWidget(self.kind)
        self.search = QLineEdit()
        self.search.setPlaceholderText(_t("Search templates"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh)
        bar.addWidget(self.search, 1)
        for text, fn in ((_t("Use Template"), self._use), (_t("Import\u2026"), self._import),
                         (_t("Delete"), self._delete), (_t("Get Online Templates"), self._download)):
            b = QPushButton(text)
            if fn == self._use:
                b.setObjectName("Primary")
            b.clicked.connect(fn)
            bar.addWidget(b)
        v.addLayout(bar)
        self.list = QListWidget()
        self.list.setViewMode(QListWidget.IconMode)
        self.list.setIconSize(QSize(150, 200))
        self.list.setGridSize(QSize(180, 250))
        self.list.setResizeMode(QListWidget.Adjust)
        self.list.setMovement(QListWidget.Static)
        self.list.setWordWrap(True)
        self.list.setSpacing(8)
        self.list.itemActivated.connect(lambda it: self._use())
        v.addWidget(self.list, 1)
        self._templates = []

    def refresh(self):
        from app.services.templates import list_templates

        kind = self.kind.currentData() or None
        q = self.search.text().strip().lower()
        self._templates = [t for t in list_templates(kind)
                           if not q or q in t.name.lower() or q in t.category.lower() or q in t.description.lower()]
        self.list.clear()
        for i, t in enumerate(self._templates):
            pm = QPixmap(t.thumbnail) if t.thumbnail else QPixmap()
            if pm.isNull():
                pm = badge_pixmap(t.kind, 120)
            it = QListWidgetItem(QIcon(pm), f"{t.name}\n{_t(t.category)}")
            it.setData(Qt.UserRole, i)
            it.setToolTip(t.description or t.name)
            self.list.addItem(it)

    def _current(self):
        it = self.list.currentItem()
        return self._templates[it.data(Qt.UserRole)] if it else None

    def _use(self):
        t = self._current()
        if t:
            self.main.new_from_template(t)

    def _import(self):
        from app.services.templates import EXT_KIND, save_user_template
        from app.shell.dialogs import get_open_file, handle_exception

        path = get_open_file(self, _t("Import Template"),
                             "Templates (*.wpt *.ett *.dpt *.dotx *.xltx *.potx *.docx *.xlsx *.pptx)")
        if not path:
            return
        ext = Path(path).suffix.lower()
        kind = EXT_KIND.get(ext) or KIND_OF_EXT.get(ext)
        if kind not in ("writer", "sheet", "slide"):
            return
        try:
            save_user_template(kind, Path(path).stem, path)
        except Exception as exc:
            handle_exception(self, _t("Import Template"), exc)
        self.refresh()

    def _delete(self):
        from app.services.templates import delete_user_template
        from app.shell.dialogs import confirm, show_info

        t = self._current()
        if not t:
            return
        if t.builtin:
            show_info(self, _t("Templates"), _t("Built-in templates cannot be deleted."))
            return
        if confirm(self, _t("Delete Template"), _t("Delete the template \u201c{0}\u201d?").format(t.name)):
            delete_user_template(t)
            self.refresh()

    def _download(self):
        from app.services import firewall
        from app.services.templates import download_catalog
        from app.shell.dialogs import show_info
        from app.shell.tasks import TaskError, run_task

        if not firewall.ensure_consent(self, "templates"):
            return
        try:
            n = run_task(self, _t("Downloading templates\u2026"), download_catalog)
            show_info(self, _t("Templates"), _t("{0} template(s) downloaded.").format(n))
        except TaskError as exc:
            from app.shell.dialogs import show_error

            show_error(self, _t("Templates"), str(exc.original))
        self.refresh()


class ToolsView(QScrollArea):
    def __init__(self, main):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        host = QWidget()
        host.setObjectName("HomePage")
        v = QVBoxLayout(host)
        v.setContentsMargins(28, 22, 28, 22)
        h = QLabel(_t("All tools"))
        h.setObjectName("H1")
        v.addWidget(h)
        colors = {"Core Suite": "#2b6be6", "AI Tools": "#9334e6", "PDF Power Tools": "#d92d20",
                  "Writer Add-ons": "#2b6be6", "Sheet Add-ons": "#159a4b", "Photos & Media": "#c2185b",
                  "System & Cloud": "#7c3aed"}
        for cat in CATEGORIES:
            lab = QLabel(_t(cat))
            lab.setObjectName("H2")
            v.addWidget(lab)
            c = card()
            g = QGridLayout(c)
            g.setSpacing(6)
            tools = [t for t in TOOLS if t.category == cat]
            for i, t in enumerate(tools):
                b = tile(f"{t.number}. {_t(t.title)}", badge(t.id) if t.id in ("writer", "sheet", "slide", "pdf", "photos")
                         else icon(t.icon, colors[cat]), lambda _=False, x=t.id: main.run_tool(x),
                         _t(t.description), (150, 88), "ToolTile")
                g.addWidget(b, i // 6, i % 6)
            g.setColumnStretch(6, 1)
            v.addWidget(c)
        v.addStretch(1)
        self.setWidget(host)


class PinBanner(QFrame):
    """Subtle, dismissible 'Pin MyOffice to the taskbar' tip (shown once, if Setup asked for it)."""

    def __init__(self, main):
        super().__init__()
        self.setObjectName("PinBanner")
        self.setStyleSheet("#PinBanner { background: rgba(26,115,232,0.09); border: 1px solid rgba(26,115,232,0.30);"
                           " border-radius: 8px; margin: 12px 16px 0 16px; }"
                           "#PinBanner QLabel { background: transparent; border: none; }")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 8, 8, 8)
        ic = QLabel()
        ic.setPixmap(icon("pin", "#1a73e8").pixmap(18, 18))
        lay.addWidget(ic)
        txt = QLabel(_t("Pin MyOffice to the taskbar to open it with one click."))
        txt.setWordWrap(True)
        lay.addWidget(txt, 1)
        self.pin_button = QPushButton(_t("Pin to taskbar"))
        self.pin_button.clicked.connect(main.pin_to_taskbar)
        lay.addWidget(self.pin_button)
        close = QToolButton()
        close.setIcon(icon("close"))
        close.setToolTip(_t("Not now"))
        close.setAutoRaise(True)
        close.clicked.connect(self.dismiss)
        lay.addWidget(close)

    @staticmethod
    def wanted() -> bool:
        from app.services import taskbar

        try:
            return taskbar.should_offer_pin(cfg().get("taskbar.pin_prompt", "auto"))
        except Exception:
            return False

    def dismiss(self):
        cfg().set("taskbar.pin_prompt", "dismissed", save=True)
        self.hide()


class HomePage(QWidget):
    def __init__(self, main):
        super().__init__()
        self.setObjectName("HomePage")
        self.main = main
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        side = QWidget()
        side.setObjectName("SidePanel")
        side.setFixedWidth(196)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(10, 16, 10, 12)
        logo = QHBoxLayout()
        lg = QLabel()
        lg.setPixmap(badge_pixmap("app", 34, "M"))
        logo.addWidget(lg)
        name = QLabel(f"<b>{APP_NAME}</b><br><span style='color:#80868b'>{APP_VERSION}</span>")
        logo.addWidget(name, 1)
        sv.addLayout(logo)
        sv.addSpacing(14)
        self.stack = QStackedWidget()
        self.views = {"home": HomeView(main), "templates": TemplatesView(main), "tools": ToolsView(main)}
        for key, vw in self.views.items():
            if key == "home":  # scrolls instead of forcing a minimum window width
                self.home_scroll = QScrollArea()
                self.home_scroll.setObjectName("HomeScroll")
                self.home_scroll.setWidgetResizable(True)
                self.home_scroll.setFrameShape(QFrame.NoFrame)
                self.home_scroll.setWidget(vw)
                self.home_scroll.setStyleSheet("#HomeScroll { background: transparent; }")
                self.home_scroll.viewport().setAutoFillBackground(False)
                self.home_scroll.viewport().installEventFilter(self)
                self.stack.addWidget(self.home_scroll)
            else:
                self.stack.addWidget(vw)
        self.group = QButtonGroup(self)
        self.nav = {}
        for key, text, ic in (("home", _t("Home"), "home"), ("templates", _t("Templates"), "templates"),
                              ("tools", _t("All Tools"), "addins")):
            b = QToolButton()
            b.setObjectName("NavButton")
            b.setText("  " + text)
            b.setIcon(icon(ic))
            b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            b.setCheckable(True)
            b.setSizePolicy(b.sizePolicy().horizontalPolicy().Expanding, b.sizePolicy().verticalPolicy())
            b.clicked.connect(lambda _=False, k=key: self.show_page(k))
            self.group.addButton(b)
            self.nav[key] = b
            sv.addWidget(b)
        for text, ic, fn in ((_t("PDF Toolbox"), "pdf", main.open_toolbox), (_t("Open\u2026"), "open", main.open_dialog)):
            b = QToolButton()
            b.setObjectName("NavButton")
            b.setText("  " + text)
            b.setIcon(icon(ic))
            b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            b.setSizePolicy(b.sizePolicy().horizontalPolicy().Expanding, b.sizePolicy().verticalPolicy())
            b.clicked.connect(fn)
            sv.addWidget(b)
        sv.addStretch(1)
        for text, ic, fn in ((_t("Settings"), "settings", main.open_settings), (_t("About"), "info", main.show_about)):
            b = QToolButton()
            b.setObjectName("NavButton")
            b.setText("  " + text)
            b.setIcon(icon(ic))
            b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            b.setSizePolicy(b.sizePolicy().horizontalPolicy().Expanding, b.sizePolicy().verticalPolicy())
            b.clicked.connect(fn)
            sv.addWidget(b)
        h.addWidget(side)
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(0)
        self.pin_banner = PinBanner(main)
        rv.addWidget(self.pin_banner)
        rv.addWidget(self.stack, 1)
        h.addWidget(right, 1)
        self.pin_banner.setVisible(PinBanner.wanted())
        self.show_page("home")

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Resize and obj is self.home_scroll.viewport():
            self.views["home"].fit_width(obj.width())
        return super().eventFilter(obj, ev)

    def show_page(self, key: str):
        self.nav[key].setChecked(True)
        self.stack.setCurrentWidget(self.home_scroll if key == "home" else self.views[key])
        if hasattr(self.views[key], "refresh"):
            self.views[key].refresh()

    def refresh(self):
        w = self.stack.currentWidget()
        if w is getattr(self, "home_scroll", None):
            w = self.views["home"]
        if hasattr(w, "refresh"):
            w.refresh()
