"""Font Manager dialog: browse fonts by script, preview Latin/Urdu/Hindi text, inspect and install fonts."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QFontDatabase
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPushButton, QSlider, QSplitter, QTextBrowser, QVBoxLayout, QWidget)

from app.services import fonts
from app.services.i18n import _t
from app.shell.dialogs import confirm, get_open_files, handle_exception, show_info

SAMPLES = {
    "latin": "The quick brown fox jumps over the lazy dog 0123456789",
    "arabic": "\u06cc\u06c1 \u0627\u0631\u062f\u0648 \u0645\u062a\u0646 \u06c1\u06d2 \u2014 \u0646\u0633\u062a\u0639\u0644\u06cc\u0642 \u0641\u0648\u0646\u0679 \u06a9\u0627 \u0646\u0645\u0648\u0646\u06c1 \u06f1\u06f2\u06f3",
    "devanagari": "\u092f\u0939 \u0939\u093f\u0928\u094d\u0926\u0940 \u092a\u093e\u0920 \u0939\u0948 \u2014 \u0926\u0947\u0935\u0928\u093e\u0917\u0930\u0940 \u0967\u0968\u0969",
}


class FontManagerDialog(QDialog):
    def __init__(self, main):
        super().__init__(main)
        self.setWindowTitle(_t("Font Manager"))
        self.resize(900, 600)
        v = QVBoxLayout(self)
        top = QHBoxLayout()
        self.script = QComboBox()
        for label, key in ((_t("All fonts"), "any"), (_t("Latin"), "latin"), (_t("Urdu / Arabic"), "arabic"),
                           (_t("Hindi / Devanagari"), "devanagari"), (_t("Installed by MyOffice"), "user"),
                           (_t("Bundled with MyOffice"), "bundled")):
            self.script.addItem(label, key)
        self.script.currentIndexChanged.connect(self.refresh)
        top.addWidget(self.script)
        self.search = QLineEdit()
        self.search.setPlaceholderText(_t("Search fonts"))
        self.search.textChanged.connect(self.refresh)
        top.addWidget(self.search, 1)
        v.addLayout(top)
        split = QSplitter()
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._show)
        split.addWidget(self.list)
        right = QWidget()
        rv = QVBoxLayout(right)
        self.sample = QLineEdit(SAMPLES["latin"])
        rv.addWidget(self.sample)
        srow = QHBoxLayout()
        srow.addWidget(QLabel(_t("Size:")))
        self.size = QSlider(Qt.Horizontal)
        self.size.setRange(8, 96)
        self.size.setValue(28)
        srow.addWidget(self.size, 1)
        for key, label in (("latin", "Aa"), ("arabic", "\u0627\u0631\u062f\u0648"), ("devanagari", "\u0939\u093f\u0902")):
            b = QPushButton(label)
            b.setFixedWidth(64)
            b.clicked.connect(lambda _=False, k=key: self.sample.setText(SAMPLES[k]))
            srow.addWidget(b)
        rv.addLayout(srow)
        self.preview = QTextBrowser()
        rv.addWidget(self.preview, 2)
        self.info = QTextBrowser()
        rv.addWidget(self.info, 1)
        split.addWidget(right)
        split.setSizes([300, 600])
        v.addWidget(split, 1)
        self.sample.textChanged.connect(lambda *_: self._show(self.list.currentItem()))
        self.size.valueChanged.connect(lambda *_: self._show(self.list.currentItem()))
        row = QHBoxLayout()
        for text, fn in ((_t("Install Fonts\u2026"), self.install), (_t("Remove"), self.remove),
                         (_t("Open Fonts Folder"), lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(fonts.user_fonts_dir()))))):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        self.count = QLabel()
        self.count.setObjectName("Muted")
        row.addWidget(self.count)
        v.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self.refresh()

    def _files_by_family(self, paths):
        out = {}
        for p in paths:
            try:
                for face in fonts.font_info(str(p))["faces"]:
                    out.setdefault(face["family"], p)
            except Exception:
                continue
        return out

    def refresh(self):
        key = self.script.currentData()
        q = self.search.text().strip().lower()
        self.file_map = self._files_by_family(fonts.installed_user_fonts() + fonts.bundled_fonts())
        if key == "user":
            fams = sorted(self._files_by_family(fonts.installed_user_fonts()))
        elif key == "bundled":
            fams = sorted(self._files_by_family(fonts.bundled_fonts()))
        else:
            fams = fonts.families_for_script(key)
        self.list.clear()
        for f in fams:
            if q and q not in f.lower():
                continue
            it = QListWidgetItem(f)
            if f in self.file_map:
                it.setForeground(Qt.darkBlue)
            self.list.addItem(it)
        self.count.setText(_t("{0} font families").format(self.list.count()))
        if self.list.count():
            self.list.setCurrentRow(0)

    def _show(self, it, _prev=None):
        if it is None:
            return
        fam = it.text()
        size = self.size.value()
        text = self.sample.text()
        rtl = any("\u0600" <= ch <= "\u06ff" for ch in text)
        styles = QFontDatabase.styles(fam)
        rows = "".join(f"<p dir='{'rtl' if rtl else 'ltr'}' style=\"font-family:'{fam}'; font-size:{size}px; "
                       f"{'font-weight:bold;' if 'Bold' in s else ''}{'font-style:italic;' if 'Italic' in s else ''}\">"
                       f"{text}</p><p style='color:#80868b;font-size:11px'>{s}</p>" for s in (styles or ["Regular"])[:6])
        self.preview.setHtml(rows)
        systems = [QFontDatabase.writingSystemName(ws) for ws in QFontDatabase.writingSystems(fam)][:12]
        html = f"<h3>{fam}</h3><p><b>{_t('Styles')}:</b> {', '.join(styles)}<br><b>{_t('Scripts')}:</b> {', '.join(systems)}</p>"
        path = self.file_map.get(fam)
        if path:
            try:
                info = fonts.font_info(str(path))
                face = info["faces"][0]
                html += (f"<p><b>{_t('File')}:</b> {path}<br><b>{_t('Version')}:</b> {face['version']}<br>"
                         f"<b>{_t('Designer')}:</b> {face['designer']}<br><b>{_t('Glyphs')}:</b> {face['glyphs']}<br>"
                         f"<b>{_t('Format')}:</b> {face['format']}{' (variable: ' + ', '.join(face['axes']) + ')' if face['variable'] else ''}"
                         f"<br><b>{_t('Licence')}:</b> {face['license'][:300]}</p>")
            except Exception as exc:
                html += f"<p>{exc}</p>"
        self.info.setHtml(html)

    def install(self):
        paths = get_open_files(self, _t("Install Fonts"), "Fonts (*.ttf *.otf *.ttc *.otc)")
        done, errors = [], []
        for p in paths:
            try:
                done += fonts.install_user_font(p)
            except Exception as exc:
                errors.append(f"{Path(p).name}: {exc}")
        if done or errors:
            show_info(self, _t("Install Fonts"), (_t("Installed: {0}").format(", ".join(done)) if done else "") +
                      ("\n\n" + "\n".join(errors) if errors else ""))
        self.refresh()

    def remove(self):
        it = self.list.currentItem()
        if it is None:
            return
        path = self.file_map.get(it.text())
        if path is None or Path(path).parent != fonts.user_fonts_dir():
            show_info(self, _t("Remove Font"), _t("Only fonts installed with MyOffice can be removed here."))
            return
        if confirm(self, _t("Remove Font"), _t("Remove {0}?").format(it.text())):
            try:
                fonts.remove_user_font(str(path))
            except Exception as exc:
                handle_exception(self, _t("Remove Font"), exc)
            self.refresh()


def open_dialog(main):
    FontManagerDialog(main).exec()


__all__ = ["FontManagerDialog", "open_dialog", "QFont"]
