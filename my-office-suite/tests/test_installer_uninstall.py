"""Uninstall (in-app + installer), taskbar pinning, private OCR data and installer-script consistency."""
import os
import re
import sys
from pathlib import Path, PureWindowsPath

import pytest

ROOT = Path(__file__).resolve().parents[1]
ISS = (ROOT / "installer" / "setup.iss").read_text(encoding="utf-8")


# ----------------------------------------------------------------------------- uninstall service
def test_parse_uninstall_string():
    from app.services.uninstall import parse_command

    assert parse_command(r'"C:\Program Files\MyOffice\unins000.exe"') == Path(r"C:\Program Files\MyOffice\unins000.exe")
    assert parse_command(r'"C:\Program Files\MyOffice\unins000.exe" /SILENT') == Path(
        r"C:\Program Files\MyOffice\unins000.exe")
    assert parse_command(r"C:\MyOffice\unins000.exe /x") == Path(r"C:\MyOffice\unins000.exe")
    assert parse_command("") is None


def test_find_uninstaller_only_for_this_copy(tmp_path):
    from app.services.uninstall import find_uninstaller

    prog = tmp_path / "MyOffice"
    other = tmp_path / "Other"
    prog.mkdir()
    other.mkdir()
    (prog / "unins000.exe").write_bytes(b"MZ")
    (other / "unins000.exe").write_bytes(b"MZ")
    assert find_uninstaller(prog) == prog / "unins000.exe"
    # a registered uninstaller of another installation is ignored
    assert find_uninstaller(prog, candidates=[other / "unins000.exe"]) is None
    assert find_uninstaller(other / "missing") is None


def test_plan_is_portable_for_source_tree(user_home):
    from app.services import uninstall

    plan = uninstall.make_plan()
    assert plan.mode == "portable" and not plan.installed and plan.uninstaller is None
    assert plan.program_dir == ROOT
    assert plan.data_dir == Path(os.environ["MYOFFICE_HOME"])


def test_custom_data_dir_keeps_foreign_files(tmp_path, monkeypatch):
    from app.services.uninstall import remove_data_dir

    data = tmp_path / "portable-data"
    (data / "logs").mkdir(parents=True)
    (data / "logs" / "myoffice.log").write_text("log")
    (data / "config.json").write_text("{}")
    (data / "backup").mkdir()
    (data / "my letter.docx").write_text("not ours")
    monkeypatch.setenv("MYOFFICE_HOME", str(data))
    removed = remove_data_dir(data)
    assert not (data / "logs").exists() and not (data / "config.json").exists() and not (data / "backup").exists()
    assert (data / "my letter.docx").read_text() == "not ours"  # only MyOffice's own entries are deleted
    assert str(data) not in removed


def test_default_data_dir_is_removed_completely(tmp_path, monkeypatch):
    import logging

    from app.services.uninstall import remove_data_dir

    data = tmp_path / "config" / "MyOffice"
    (data / "recovery").mkdir(parents=True)
    log_file = data / "logs" / "myoffice.log"
    log_file.parent.mkdir()
    handler = logging.FileHandler(log_file)
    logging.getLogger().addHandler(handler)
    monkeypatch.delenv("MYOFFICE_HOME", raising=False)
    try:
        assert remove_data_dir(data) == [str(data)]
        assert not data.exists()
        assert handler not in logging.getLogger().handlers  # open log file closed first
    finally:
        logging.getLogger().removeHandler(handler)


def test_remove_temp_files(tmp_path):
    from app.services.uninstall import remove_temp_files

    (tmp_path / "myoffice_conv_abc").mkdir()
    (tmp_path / "myoffice_conv_abc" / "x.pdf").write_text("x")
    (tmp_path / "myoffice_tpl_1.docx").write_text("x")
    (tmp_path / "unrelated.txt").write_text("keep")
    removed = remove_temp_files(tmp_path)
    assert len(removed) == 2
    assert sorted(p.name for p in tmp_path.iterdir()) == ["unrelated.txt"]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="freedesktop shortcuts")
def test_remove_portable_traces_end_to_end(tmp_path, monkeypatch):
    """Portable copy: shortcuts + data folder + temp files are removed, nothing else."""
    from app.services import shortcuts, uninstall

    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmp"))
    monkeypatch.delenv("MYOFFICE_HOME", raising=False)
    monkeypatch.setattr(shortcuts, "desktop_dir", lambda: home / "Desktop")
    (tmp_path / "tmp").mkdir()
    import tempfile

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "tmp"))
    (tmp_path / "tmp" / "myoffice_pdf_1").mkdir()
    made = shortcuts.create_shortcuts()
    assert made and all(Path(p).exists() for p in made)
    data = home / ".config" / "MyOffice"
    (data / "macros").mkdir(parents=True)
    (home / "Documents").mkdir()
    (home / "Documents" / "report.docx").write_text("user document")

    plan = uninstall.make_plan()
    assert plan.data_dir == data and len(plan.shortcuts) == len(made)
    removed = uninstall.remove_portable_traces(plan)
    assert not any(Path(p).exists() for p in made)
    assert not data.exists() and not (tmp_path / "tmp" / "myoffice_pdf_1").exists()
    assert (home / "Documents" / "report.docx").read_text() == "user document"
    assert str(data) in removed


def test_uninstall_dialog(qapp, tmp_path):
    from PySide6.QtWidgets import QLabel

    from app.services.uninstall import UninstallPlan
    from app.shell.uninstall_dialog import UninstallDialog, removal_items

    installed = UninstallPlan("installer", tmp_path / "MyOffice", tmp_path / "data", tmp_path / "MyOffice" / "unins000.exe")
    portable = UninstallPlan("portable", tmp_path / "src", tmp_path / "data", shortcuts=["a.desktop"])
    assert any("taskbar" in t for t in removal_items(installed))
    assert any("Shortcuts created by MyOffice (1)" in t for t in removal_items(portable))
    for plan in (installed, portable):
        d = UninstallDialog(plan)
        text = " ".join(lab.text() for lab in d.findChildren(QLabel))
        assert str(plan.program_dir) in text and "Not deleted" in text
        assert d.cancel_btn.isDefault() and not d.uninstall_btn.isDefault()  # safe default
        assert ("delete its folder yourself" in text) == (plan.mode == "portable")
        d.deleteLater()


def test_cli_uninstall_switch():
    from app.services.cli import parse

    assert parse(["--uninstall"]).uninstall is True
    assert parse(["-uninstall"]).uninstall is True  # ksomisc-style alias
    assert parse([]).uninstall is False


# ----------------------------------------------------------------------------- taskbar
def test_taskbar_pinning_off_windows(qapp, tmp_path):
    from app.services import taskbar

    if sys.platform == "win32":
        pytest.skip("checks the non-Windows behaviour")
    assert taskbar.should_offer_pin("auto") is False
    results = []
    req = taskbar.PinRequest()
    req.finished.connect(lambda pinned, method: results.append((pinned, method)))
    req.start()
    assert results == [(False, "manual")]


def test_taskbar_helpers(tmp_path):
    from app.services import taskbar

    assert taskbar.pinned_shortcut(tmp_path) is None
    (tmp_path / "MyOffice.lnk").write_bytes(b"L")
    assert taskbar.pinned_shortcut(tmp_path) == tmp_path / "MyOffice.lnk"
    script = taskbar.legacy_pin_script(PureWindowsPath(r"C:\ProgramData\Start Menu\Programs\it's MyOffice.lnk"))
    assert "Windows.taskbarpin" in script and "InvokeVerb('{:}')" in script
    assert "'it''s MyOffice.lnk'" in script  # PowerShell quoting
    assert "Remove-Item -LiteralPath $k" in script  # the temporary verb is always removed again


# ----------------------------------------------------------------------------- private OCR data
def test_private_tessdata_selection(tmp_path, monkeypatch):
    from app.pdf import ops

    tess = tmp_path / "tessdata"
    tess.mkdir()
    for lang in ("eng", "urd", "hin"):
        (tess / f"{lang}.traineddata").write_bytes(b"x")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "MyOffice.exe"))
    monkeypatch.setattr(ops, "_ORIG_TESSDATA", None)
    monkeypatch.delenv("TESSDATA_PREFIX", raising=False)
    assert ops.private_tessdata() == tess
    ops._select_tessdata("urd+eng")
    assert os.environ["TESSDATA_PREFIX"] == str(tess)
    ops._select_tessdata("urd+ara")  # a language MyOffice does not have -> Tesseract's own data
    assert "TESSDATA_PREFIX" not in os.environ


# ----------------------------------------------------------------------------- installer script
def _defines() -> dict:
    return dict(re.findall(r'^#define\s+(\w+)\s+"([^"]*)"', ISS, re.M))


def test_installer_identity_matches_app():
    from app.version import APP_ID, APP_MUTEX, INNO_APP_ID

    d = _defines()
    assert "AppId={" + INNO_APP_ID + "\n" in ISS
    assert d["MyAppUserModelID"] == APP_ID and d["MyAppMutex"] == APP_MUTEX


def test_installer_associations_match_register():
    from app.services.register import ASSOCIATIONS

    exts = dict(re.findall(r'^#define AExt\[(\d+)\]\s+"([^"]+)"', ISS, re.M))
    progs = dict(re.findall(r'^#define AProg\[(\d+)\]\s+"([^"]+)"', ISS, re.M))
    table = {exts[i]: progs[i] for i in exts}
    assert table == {ext: v[0] for ext, v in ASSOCIATIONS.items()}


def test_installer_cleanup_matches_app_footprint():
    from app.services import firewall, shortcuts, uninstall

    d = _defines()
    assert d["FirewallRule"] == firewall.RULE_NAME
    for name, _args in shortcuts.APPS.values():  # shortcuts made by Tools > Create Desktop Shortcuts
        assert f"'{name}.lnk'" in ISS
    assert all(p.startswith("myoffice_") for p in uninstall.TEMP_PREFIXES) and "'myoffice_*'" in ISS
    assert r"'AppData\Roaming\MyOffice'" in ISS  # = paths.user_dir() on Windows
    assert "User Pinned\\TaskBar\\MyOffice.lnk" in ISS


def test_installer_downloads_are_pinned_and_verified():
    """The only thing Setup may download is the optional LibreOffice MSI - pinned and SHA-256 checked."""
    d = _defines()
    assert re.fullmatch(r"[0-9a-f]{64}", d["LOSha256"])
    assert "DownloadPage.Add(Url, LO_FILE, '{#LOSha256}')" in ISS
    for gone in ("VCRedistUrl", "TessUrl", "GSUrl", "TessDataUrl"):  # bundled since 1.1
        assert gone not in d, gone
    urls = re.findall(r'^#define\s+LOUrl2?\s+"(https://[^"]+)"', ISS, re.M)
    assert len(urls) == 2


def test_installer_assets_exist():
    for line in re.findall(r"^Wizard\w*ImageFile\w*=(.+)$", ISS, re.M):
        for f in line.split(","):
            assert (ROOT / "installer" / f.strip().replace("\\", "/")).is_file(), f


# ----------------------------------------------------------------------------- translations / config
def test_translation_extractor_handles_concatenated_literals():
    sys.path.insert(0, str(ROOT / "tools"))
    import build_translations

    strings = build_translations.extract()
    assert "Pin MyOffice to the taskbar to open it with one click." in strings
    assert any(s.startswith("On this version of Windows only you can pin programs") for s in strings)


def test_installer_language_only_on_windows():
    from app.services.config import installer_language

    if sys.platform != "win32":
        assert installer_language() is None


# ----------------------------------------------------------------------------- first-run dialogs
def test_first_run_consent_dialog_builds(qapp):
    """Regression: the first-run network-consent dialog passed an int to QLabel.setTextFormat, which
    PySide6 6.12 rejects - every fresh install crashed into the error handler on first start.  The
    automated runs never showed it (MYOFFICE_NO_FIRST_RUN), the Wine install test did."""
    from PySide6.QtWidgets import QLabel

    from app.services.firewall import ConsentDialog

    for feature in (None, "collab"):
        d = ConsentDialog(feature=feature)
        assert any("\u2022" in lab.text() for lab in d.findChildren(QLabel))
        d.deleteLater()
