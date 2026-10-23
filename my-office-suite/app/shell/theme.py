"""Commercial Fluent-inspired Qt style sheets (Light & Dark modes with WCAG AA contrast)."""
from __future__ import annotations

LIGHT = {
    "bg": "#f3f5f9", "panel": "#ffffff", "border": "#d8dee9", "text": "#181d25", "muted": "#525b68",
    "tabbar": "#e6eaf2", "tab_active": "#ffffff", "hover": "#e8f0fe", "pressed": "#d2e3fc",
    "accent": "#2563eb", "accent_dark": "#1d4ed8", "canvas": "#e5e8ef", "input": "#ffffff", "selection": "#cfe0fc",
}
DARK = {
    "bg": "#181a20", "panel": "#222630", "border": "#343b4a", "text": "#edf2f7", "muted": "#a0aec0",
    "tabbar": "#13151a", "tab_active": "#222630", "hover": "#2d3342", "pressed": "#3a4256",
    "accent": "#60a5fa", "accent_dark": "#3b82f6", "canvas": "#111318", "input": "#2a2f3d", "selection": "#334463",
}


def palette_for(theme: str) -> dict:
    return DARK if theme == "dark" else LIGHT


def stylesheet(theme: str = "light") -> str:
    from app.services.paths import resource_path

    c = palette_for(theme if isinstance(theme, str) else ("dark" if theme else "light"))
    close_svg = resource_path("icons", "close.svg").as_posix()
    return f"""
QTabBar#DocTabBar::close-button {{ image: url({close_svg}); subcontrol-position: right; width: 12px; height: 12px;
    border-radius: 3px; padding: 1px; }}
QTabBar#DocTabBar::close-button:hover {{ background: {c['hover']}; }}
QMainWindow, QDialog {{ background: {c['bg']}; color: {c['text']}; }}
QWidget {{ color: {c['text']}; }}
QToolTip {{ background: {c['panel']}; color: {c['text']}; border: 1px solid {c['border']}; border-radius: 5px; padding: 5px 8px; }}
QTabWidget#DocTabs::pane {{ border: none; background: {c['bg']}; }}
QTabWidget#DocTabs > QTabBar {{ background: {c['tabbar']}; }}
QTabBar#DocTabBar {{ background: {c['tabbar']}; }}
QTabBar#DocTabBar::tab {{ background: transparent; color: {c['muted']}; padding: 7px 14px; margin: 4px 1px 0 1px;
    border-top-left-radius: 7px; border-top-right-radius: 7px; min-width: 90px; max-width: 240px; }}
QTabBar#DocTabBar::tab:selected {{ background: {c['tab_active']}; color: {c['text']}; font-weight: 600; }}
QTabBar#DocTabBar::tab:hover:!selected {{ background: {c['hover']}; }}
QWidget#Ribbon {{ background: {c['panel']}; border-bottom: 1px solid {c['border']}; }}
QTabWidget#RibbonTabs::pane {{ border: none; border-top: 1px solid {c['border']}; background: {c['panel']}; }}
QTabWidget#RibbonTabs > QTabBar::tab {{ background: transparent; padding: 5px 12px; color: {c['muted']}; border: none;
    border-bottom: 2px solid transparent; }}
QTabWidget#RibbonTabs > QTabBar::tab:selected {{ color: {c['text']}; border-bottom: 2px solid {c['accent']}; font-weight: 600; }}
QTabWidget#RibbonTabs > QTabBar::tab:hover {{ color: {c['text']}; }}
QFrame#RibbonGroup {{ border: none; border-right: 1px solid {c['border']}; }}
QLabel#RibbonGroupTitle {{ color: {c['muted']}; font-size: 11px; }}
QToolButton {{ border: 1px solid transparent; border-radius: 5px; padding: 3px; background: transparent; }}
QToolButton:hover {{ background: {c['hover']}; border-color: {c['border']}; }}
QToolButton:pressed, QToolButton:checked {{ background: {c['pressed']}; border-color: {c['accent']}; }}
QToolButton#RibbonLarge {{ padding: 3px 5px; min-width: 54px; }}
QToolButton::menu-indicator {{ subcontrol-position: bottom right; width: 8px; }}
QStatusBar {{ background: {c['panel']}; border-top: 1px solid {c['border']}; color: {c['muted']}; }}
QStatusBar QLabel {{ color: {c['muted']}; padding: 0 6px; }}
QGraphicsView#PageCanvas, QScrollArea#PdfCanvas {{ background: {c['canvas']}; border: none; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QDateEdit, QPlainTextEdit, QTextEdit, QKeySequenceEdit {{
    background: {c['input']}; border: 1px solid {c['border']}; border-radius: 5px; padding: 4px 7px;
    selection-background-color: {c['selection']}; selection-color: {c['text']}; }}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus, QKeySequenceEdit:focus {{ border-color: {c['accent']}; }}
QComboBox QAbstractItemView {{ background: {c['panel']}; selection-background-color: {c['selection']}; }}
QPushButton {{ background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 6px; padding: 6px 14px; }}
QPushButton:hover {{ background: {c['hover']}; }}
QPushButton:default, QPushButton#Primary, QPushButton[accent="true"] {{
    background: {c['accent']}; color: #ffffff; border-color: {c['accent']}; font-weight: 600; }}
QPushButton#Primary:hover, QPushButton[accent="true"]:hover {{ background: {c['accent_dark']}; }}
QPushButton:disabled {{ color: {c['muted']}; }}
QListWidget, QTreeWidget, QTableWidget {{ background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 6px;
    selection-background-color: {c['selection']}; selection-color: {c['text']}; outline: none; }}
QListWidget::item, QTreeWidget::item {{ padding: 4px 6px; border-radius: 4px; }}
QHeaderView::section {{ background: {c['bg']}; color: {c['muted']}; border: none; border-right: 1px solid {c['border']};
    border-bottom: 1px solid {c['border']}; padding: 5px 6px; font-weight: 600; }}
QTableView#SheetGrid {{ gridline-color: {c['border']}; background: {c['panel']};
    selection-background-color: {c['selection']}; selection-color: {c['text']}; }}
QWidget#HomePage {{ background: {c['bg']}; }}
QFrame#Card {{ background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 10px; }}
QLabel#H1 {{ font-size: 22px; font-weight: 600; }}
QLabel#H2 {{ font-size: 15px; font-weight: 600; }}
QLabel#Muted {{ color: {c['muted']}; }}
QToolButton#Tile {{ background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 10px; padding: 8px; }}
QToolButton#Tile:hover {{ background: {c['hover']}; border-color: {c['accent']}; }}
QToolButton#ToolTile {{ border-radius: 8px; padding: 6px; }}
QToolButton#ToolTile:hover {{ background: {c['hover']}; }}
QToolButton#AppMenuButton {{ font-weight: 600; padding: 4px 10px; color: {c['text']}; }}
QGroupBox {{ border: 1px solid {c['border']}; border-radius: 8px; margin-top: 12px; padding: 10px 8px 8px 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 5px; color: {c['muted']}; font-weight: 600; }}
QSplitter::handle {{ background: {c['border']}; }}
QMenu {{ background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 7px; padding: 4px; }}
QMenu::item {{ padding: 5px 24px 5px 24px; border-radius: 4px; }}
QMenu::item:selected {{ background: {c['hover']}; }}
QMenu::separator {{ height: 1px; background: {c['border']}; margin: 4px 8px; }}
QWidget#TopBar {{ background: {c['tabbar']}; }}
QWidget#StatusStrip {{ background: {c['panel']}; border-top: 1px solid {c['border']}; }}
QWidget#SidePanel, QFrame#CollabSidePanel {{ background: {c['panel']}; border-right: 1px solid {c['border']}; border-left: 1px solid {c['border']}; }}
QToolButton#NavButton {{ text-align: left; padding: 7px 12px; border-radius: 6px; }}
QToolButton#NavButton:checked {{ background: {c['selection']}; border-color: transparent; font-weight: 600; }}
QToolButton#NewTabButton {{ font-size: 16px; padding: 2px 8px; }}
"""
