"""Network consent and Windows Firewall rule management (supports all Windows 10/11 Wi-Fi profiles cleanly)."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QLabel, QVBoxLayout

from app.services.config import cfg
from app.services.i18n import _t
from app.services.paths import executable_command

log = logging.getLogger(__name__)
RULE_NAME = "MyOffice Collaboration"
_NO_WIN = 0x08000000 if os.name == "nt" else 0

FEATURES = {
    "update": "Check for updates (downloads version.json)",
    "sync": "Cloud Sync (Gmail / Google Drive / Router USB / S3 / Supabase)",
    "collab": "Real-time collaboration & Team Chat (WebSocket / LAN)",
    "templates": "Online template catalogue",
}


def consent() -> bool | None:
    return cfg().get("firewall.consent", None)


def set_consent(value: bool) -> None:
    cfg().set("firewall.consent", bool(value), save=True)


class ConsentDialog(QDialog):
    def __init__(self, parent=None, feature: str | None = None):
        super().__init__(parent)
        self.setWindowTitle(_t("Network access"))
        self.setMinimumWidth(460)
        v = QVBoxLayout(self)
        intro = QLabel(_t("MyOffice works fully offline. The following optional features need network access:"))
        intro.setWordWrap(True)
        v.addWidget(intro)
        lst = QLabel("".join(f"\u2022 {_t(t)}<br>" for t in FEATURES.values()))
        lst.setTextFormat(Qt.TextFormat.RichText)
        v.addWidget(lst)
        if feature:
            req = QLabel(_t("Requested now: {0}").format(_t(FEATURES.get(feature, feature))))
            req.setObjectName("Muted")
            v.addWidget(req)
        self.rule = QCheckBox(
            _t("Also allow MyOffice real-time LAN collaboration through Windows Firewall (recommended)")
        )
        self.rule.setChecked(sys.platform == "win32")
        self.rule.setVisible(sys.platform == "win32")
        v.addWidget(self.rule)
        note = QLabel(_t("You can change this anytime in Settings."))
        note.setObjectName("Muted")
        v.addWidget(note)
        bb = QDialogButtonBox()
        self.allow = bb.addButton(_t("Allow"), QDialogButtonBox.AcceptRole)
        bb.addButton(_t("Stay offline"), QDialogButtonBox.RejectRole)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)


def ensure_consent(parent=None, feature: str | None = None, ask_again: bool = True) -> bool:
    c = consent()
    if c is True:
        return True
    if c is False and not ask_again:
        return False
    d = ConsentDialog(parent, feature)
    ok = bool(d.exec())
    set_consent(ok)
    if ok and d.rule.isChecked():
        ok_rule, msg = add_firewall_rule(int(cfg().get("collab.port", 8765)))
        log.info("Firewall rule: %s %s", ok_rule, msg)
    return ok


def add_firewall_rule(port: int = 8765) -> tuple[bool, str]:
    """Allow inbound TCP traffic for ``MyOffice.exe`` across ALL Windows network profiles (including Public Wi-Fi!)."""
    if sys.platform == "win32":  # pragma: no cover - Windows only
        import ctypes

        program = executable_command()[0]
        # Try direct execution first if running elevated; otherwise invoke UAC `runas` cleanly
        direct = subprocess.run(
            ["netsh", "advfirewall", "firewall", "add", "rule", f"name={RULE_NAME}",
             "dir=in", "action=allow", f"program={program}", "enable=yes", "profile=any"],
            capture_output=True, text=True, timeout=15, creationflags=_NO_WIN, errors="replace",
        )
        if direct.returncode == 0:
            return True, "Windows Firewall rule added across all network profiles."
        cmdline = (
            f'/c netsh advfirewall firewall delete rule name="{RULE_NAME}" & '
            f'netsh advfirewall firewall add rule name="{RULE_NAME}" dir=in action=allow '
            f'program="{program}" enable=yes profile=any'
        )
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", "cmd.exe", cmdline, None, 0)
        return rc > 32, "Windows Firewall updated with administrator permission." if rc > 32 else f"ShellExecute failed ({rc})"
    if shutil.which("ufw"):
        return False, f"Run: sudo ufw allow {int(port)}/tcp"
    if shutil.which("firewall-cmd"):
        return False, f"Run: sudo firewall-cmd --add-port={int(port)}/tcp"
    return False, "No supported firewall tool found; incoming connections are usually allowed."


def remove_firewall_rule() -> tuple[bool, str]:
    if sys.platform == "win32":  # pragma: no cover - Windows only
        import ctypes

        direct = subprocess.run(
            ["netsh", "advfirewall", "firewall", "delete", "rule", f"name={RULE_NAME}"],
            capture_output=True, text=True, timeout=15, creationflags=_NO_WIN, errors="replace",
        )
        if direct.returncode == 0:
            return True, ""
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", "netsh",
                                                 f'advfirewall firewall delete rule name="{RULE_NAME}"', None, 0)
        return rc > 32, ""
    return False, "Nothing to remove on this platform."


def firewall_rule_present() -> bool:
    if sys.platform != "win32":
        return False
    try:  # pragma: no cover - Windows only
        out = subprocess.run(["netsh", "advfirewall", "firewall", "show", "rule", f"name={RULE_NAME}"],
                             capture_output=True, text=True, timeout=15, creationflags=_NO_WIN)
        return RULE_NAME in out.stdout
    except (OSError, subprocess.SubprocessError):
        return False


def ensure_firewall_rule_silent(port: int = 8766) -> bool:
    """Silently ensure inbound rule when already elevated on Windows without prompting UAC."""
    if sys.platform != "win32":
        return False
    if firewall_rule_present():
        return True
    try:  # pragma: no cover - Windows only
        program = executable_command()[0]
        res = subprocess.run(
            [
                "netsh", "advfirewall", "firewall", "add", "rule",
                f"name={RULE_NAME}", "dir=in", "action=allow",
                f"program={program}", "enable=yes", "profile=any",
            ],
            capture_output=True, text=True, timeout=8, creationflags=_NO_WIN, errors="replace",
        )
        return res.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# Convenience aliases used by v1.3+ Collaboration & Mobile Sync UI
add_rule = lambda exe_path=None, port=8765: add_firewall_rule(port or 8765)
add_rule_elevated = lambda exe_path=None, port=8765: add_firewall_rule(port or 8765)
remove_rule = remove_firewall_rule
rule_present = firewall_rule_present
