"""A compact ribbon (tabbed toolbar) similar to WPS / MS Office."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QScrollArea, QSizePolicy, QTabWidget,
                               QToolButton, QVBoxLayout, QWidget)


class RibbonGroup(QFrame):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("RibbonGroup")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 2, 4, 1)
        outer.setSpacing(0)
        self._row = QHBoxLayout()
        self._row.setSpacing(2)
        self._row.setContentsMargins(0, 0, 0, 0)
        outer.addLayout(self._row, 1)
        lbl = QLabel(title.replace("&&", "&"))  # group titles are plain labels, not mnemonics
        lbl.setObjectName("RibbonGroupTitle")
        lbl.setAlignment(Qt.AlignCenter)
        outer.addWidget(lbl)
        self._col: QVBoxLayout | None = None
        self._col_count = 0

    # large button: icon above text
    def large(self, action: QAction, popup: bool = False) -> QToolButton:
        self._col = None
        b = QToolButton(self)
        b.setObjectName("RibbonLarge")
        b.setDefaultAction(action)
        b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        b.setIconSize(QSize(26, 26))
        b.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        if action.menu() is not None:
            b.setPopupMode(QToolButton.MenuButtonPopup if popup else QToolButton.InstantPopup)
        self._row.addWidget(b)
        return b

    def _column(self) -> QVBoxLayout:
        if self._col is None or self._col_count >= 3:
            self._col = QVBoxLayout()
            self._col.setSpacing(1)
            self._col.setContentsMargins(0, 0, 0, 0)
            self._row.addLayout(self._col)
            self._col_count = 0
        return self._col

    def small(self, action: QAction, text: bool = True, popup: bool = False) -> QToolButton:
        """``popup=True``: the whole button opens the action's menu (for actions that are only a menu)."""
        b = QToolButton(self)
        b.setDefaultAction(action)
        b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon if text else Qt.ToolButtonIconOnly)
        b.setIconSize(QSize(16, 16))
        if action.menu() is not None:
            b.setPopupMode(QToolButton.InstantPopup if popup else QToolButton.MenuButtonPopup)
        col = self._column()
        col.addWidget(b, 0, Qt.AlignLeft)
        self._col_count += 1
        return b

    def row(self, *widgets) -> QWidget:
        """Add a horizontal row (font combo + size...) into the small-button column."""
        w = QWidget(self)
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(2)
        for item in widgets:
            if isinstance(item, QAction):
                b = QToolButton(w)
                b.setDefaultAction(item)
                b.setIconSize(QSize(16, 16))
                b.setToolButtonStyle(Qt.ToolButtonIconOnly)
                if item.menu() is not None:
                    b.setPopupMode(QToolButton.MenuButtonPopup)
                h.addWidget(b)
            else:
                h.addWidget(item)
        h.addStretch(1)
        col = self._column()
        col.addWidget(w)
        self._col_count += 1
        return w

    def widget(self, w: QWidget) -> QWidget:
        self._col = None
        self._row.addWidget(w)
        return w

    def new_column(self) -> None:
        self._col = None


class RibbonPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(4, 2, 4, 2)
        self._lay.setSpacing(2)
        self._lay.addStretch(1)

    def group(self, title: str) -> RibbonGroup:
        g = RibbonGroup(title, self)
        self._lay.insertWidget(self._lay.count() - 1, g)
        return g


class Ribbon(QWidget):
    HEIGHT = 128

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Ribbon")
        self.setAttribute(Qt.WA_StyledBackground, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("RibbonTabs")
        self.tabs.setDocumentMode(True)
        self.tabs.setFixedHeight(self.HEIGHT)
        lay.addWidget(self.tabs)
        self._collapsed = False
        self.tabs.tabBarDoubleClicked.connect(self.toggle_collapsed)

    def page(self, title: str) -> RibbonPage:
        page = RibbonPage()
        scroll = QScrollArea()
        scroll.setWidget(page)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.tabs.addTab(scroll, title)
        return page

    def select_page(self, title: str) -> None:
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i).replace("&", "") == title.replace("&", ""):
                self.tabs.setCurrentIndex(i)
                return

    def toggle_collapsed(self, *_):
        self._collapsed = not self._collapsed
        self.tabs.setFixedHeight(self.tabs.tabBar().sizeHint().height() + 2 if self._collapsed else self.HEIGHT)
