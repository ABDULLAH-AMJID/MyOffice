"""Shared pytest fixtures: headless Qt, an isolated user directory, sample files."""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_LOGGING_RULES", "qt.text.font.db=false")
os.environ.setdefault("MYOFFICE_NO_FIRST_RUN", "1")


@pytest.fixture(scope="session", autouse=True)
def user_home(tmp_path_factory):
    home = tmp_path_factory.mktemp("myoffice_home")
    os.environ["MYOFFICE_HOME"] = str(home)
    from app.services.config import reset_config_for_tests

    reset_config_for_tests(home / "config.json")
    return home


@pytest.fixture(scope="session")
def qapp(user_home):
    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication.instance() or QApplication([])
    from app.services import fonts

    fonts.load_bundled_fonts()
    fonts.install_substitutions()
    # never block on modal message boxes during tests
    QMessageBox.exec = lambda self: 0
    for name in ("critical", "warning", "information"):
        setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.Ok))
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    yield app


@pytest.fixture
def sample_pdfs(tmp_path):
    import pymupdf

    paths = []
    for name, n in (("a.pdf", 3), ("b.pdf", 2)):
        d = pymupdf.open()
        for i in range(n):
            p = d.new_page()
            p.insert_text((72, 100), f"{name} page {i + 1}", fontsize=18)
            p.insert_text((72, 140), "The quick brown fox jumps over the lazy dog.", fontsize=11)
        path = tmp_path / name
        d.save(str(path))
        d.close()
        paths.append(str(path))
    return paths
