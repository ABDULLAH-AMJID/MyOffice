"""Hardware-bound zero-knowledge encryption vault (``MOVAULT1`` format, AES-256-GCM + HKDF/Scrypt + Windows DPAPI).

Designed for untrusted physical & network media such as a USB drive plugged into a Wi-Fi router, a shared NAS folder,
or cloud/email backup storage:

* Every file's content **and** original filename/metadata are encrypted with 256-bit AES-GCM before leaving memory.
* Files on external storage receive random opaque names (``vault_<random>.movault``) so an attacker who unplugs the
  USB drive from the router cannot see document names, folder structure, or contents.
* The 256-bit Device Master Key lives **only** inside this computer's local protected keystore (sealed with Windows
  DPAPI ``CryptProtectData`` + machine GUID entropy on Windows, or ``0600`` machine-bound HKDF storage on POSIX) —
  never on the USB drive or router.
* The owner can access the exact same vault seamlessly over Wi-Fi (SMB / Router FTP) **and** offline by unplugging
  the USB drive from the router and plugging it straight into their laptop.
* Optional secondary passphrase slot + exportable encrypted Emergency Recovery Kit (``.movaultkey``) let the owner
  recover backups if their laptop hardware ever fails.
"""
from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes
import getpass
import hashlib
import json
import logging
import os
import socket
import struct
import sys
import uuid
import zlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from app.services.paths import user_subdir

log = logging.getLogger(__name__)

MAGIC = b"MOVAULT1"
KIT_MAGIC = b"MOVAULTK"
AAD_WRAP_DEV = b"MOVAULT1-WRAP-DEVICE"
AAD_WRAP_PASS = b"MOVAULT1-WRAP-PASSPHRASE"
AAD_META = b"MOVAULT1-METADATA"
AAD_DATA = b"MOVAULT1-PAYLOAD"
SECRET_PREFIX = "enc1:"


class VaultError(Exception):
    """Base error for encrypted vault operations."""


class VaultAccessDeniedError(VaultError):
    """Raised when attempting to read a vault file created by a different computer without authorization."""


class VaultTamperedError(VaultError):
    """Raised when ciphertext, metadata, or envelope integrity verification fails."""


@dataclass
class VaultEntry:
    path: Path
    vault_filename: str
    original_name: str
    original_path: str
    created_at: datetime
    original_size: int
    encrypted_size: int
    sha256: str
    key_id: str
    device_label: str
    locked_to_other_device: bool = False


# ============================================================================= OS / hardware binding helpers
def _windows_machine_guid() -> str:
    if sys.platform != "win32":
        return ""
    try:  # pragma: no cover - Windows registry
        import winreg

        for flag in (winreg.KEY_WOW64_64KEY, 0):
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0,
                                    winreg.KEY_READ | flag) as k:
                    val, _ = winreg.QueryValueEx(k, "MachineGuid")
                    if val:
                        return str(val).strip()
            except OSError:
                continue
    except Exception:
        pass
    return ""


def _posix_machine_id() -> str:
    for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            txt = Path(p).read_text(encoding="utf-8").strip()
            if txt:
                return txt
        except OSError:
            continue
    return str(uuid.getnode())


def _machine_binding_secret() -> bytes:
    user = ""
    try:
        user = getpass.getuser()
    except Exception:
        user = "user"
    hw = _windows_machine_guid() if sys.platform == "win32" else _posix_machine_id()
    uid = str(os.getuid()) if hasattr(os, "getuid") else user
    return hashlib.sha256(f"myoffice-hw-bind-v1|{sys.platform}|{hw}|{uid}|{user}".encode("utf-8")).digest()


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi_protect(raw: bytes, entropy: bytes) -> bytes | None:
    """Protect bytes using Windows CryptProtectData (user + machine DPAPI)."""
    if sys.platform != "win32":
        return None
    try:  # pragma: no cover - Windows native DPAPI
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        in_buf = ctypes.create_string_buffer(raw, len(raw))
        ent_buf = ctypes.create_string_buffer(entropy, len(entropy))
        in_blob = _DATA_BLOB(len(raw), ctypes.cast(in_buf, ctypes.POINTER(ctypes.c_char)))
        ent_blob = _DATA_BLOB(len(entropy), ctypes.cast(ent_buf, ctypes.POINTER(ctypes.c_char)))
        out_blob = _DATA_BLOB()
        CRYPTPROTECT_UI_FORBIDDEN = 0x1
        if not crypt32.CryptProtectData(ctypes.byref(in_blob), "MyOfficeVaultKey", ctypes.byref(ent_blob),
                                        None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out_blob)):
            return None
        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            kernel32.LocalFree(out_blob.pbData)
    except Exception as exc:
        log.debug("DPAPI protect fallback: %s", exc)
        return None


def _dpapi_unprotect(protected: bytes, entropy: bytes) -> bytes | None:
    if sys.platform != "win32":
        return None
    try:  # pragma: no cover - Windows native DPAPI
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        in_buf = ctypes.create_string_buffer(protected, len(protected))
        ent_buf = ctypes.create_string_buffer(entropy, len(entropy))
        in_blob = _DATA_BLOB(len(protected), ctypes.cast(in_buf, ctypes.POINTER(ctypes.c_char)))
        ent_blob = _DATA_BLOB(len(entropy), ctypes.cast(ent_buf, ctypes.POINTER(ctypes.c_char)))
        out_blob = _DATA_BLOB()
        CRYPTPROTECT_UI_FORBIDDEN = 0x1
        if not crypt32.CryptUnprotectData(ctypes.byref(in_blob), None, ctypes.byref(ent_blob),
                                          None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out_blob)):
            return None
        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            kernel32.LocalFree(out_blob.pbData)
    except Exception as exc:
        log.debug("DPAPI unprotect fallback: %s", exc)
        return None


def _derive_hkdf(secret: bytes, salt: bytes, info: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=info).derive(secret)


def _derive_passphrase_key(passphrase: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=2**14, r=8, p=1).derive(passphrase.encode("utf-8"))


def compute_key_id(master_key: bytes) -> str:
    return hashlib.sha256(b"myoffice-vault-key-id-v1:" + master_key).hexdigest()[:16]


# ============================================================================= Device KeyStore
class DeviceKeyStore:
    """Manages this computer's hardware-sealed 256-bit Vault Master Key + any imported recovery keys."""

    def __init__(self, store_dir: Path | None = None, binding_secret: bytes | None = None,
                 device_label: str | None = None):
        self.dir = Path(store_dir) if store_dir is not None else user_subdir("security")
        self.dir.mkdir(parents=True, exist_ok=True)
        self.file = self.dir / "device_vault.json"
        self._binding = binding_secret if binding_secret is not None else _machine_binding_secret()
        self.device_label = device_label or self._default_label()
        self._primary_key: bytes | None = None
        self._extra_keys: dict[str, bytes] = {}
        self._load_or_create()

    @staticmethod
    def _default_label() -> str:
        try:
            return socket.gethostname() or "MyOffice-PC"
        except Exception:
            return "MyOffice-PC"

    def _seal_blob(self, raw: bytes) -> dict:
        dpapi = _dpapi_protect(raw, self._binding)
        if dpapi is not None:
            return {"mode": "dpapi", "blob": base64.b64encode(dpapi).decode("ascii")}
        salt = os.urandom(16)
        nonce = os.urandom(12)
        kek = _derive_hkdf(self._binding, salt, b"myoffice-local-keystore-v1")
        ct = AESGCM(kek).encrypt(nonce, raw, b"myoffice-keystore")
        return {"mode": "hkdf", "salt": salt.hex(), "nonce": nonce.hex(), "ct": ct.hex()}

    def _unseal_blob(self, entry: dict) -> bytes:
        mode = entry.get("mode")
        if mode == "dpapi":
            out = _dpapi_unprotect(base64.b64decode(entry["blob"]), self._binding)
            if out is None:
                raise VaultAccessDeniedError("Hardware DPAPI key could not be unsealed on this Windows account/PC.")
            return out
        salt = bytes.fromhex(entry["salt"])
        nonce = bytes.fromhex(entry["nonce"])
        kek = _derive_hkdf(self._binding, salt, b"myoffice-local-keystore-v1")
        try:
            return AESGCM(kek).decrypt(nonce, bytes.fromhex(entry["ct"]), b"myoffice-keystore")
        except InvalidTag as exc:
            raise VaultAccessDeniedError("Keystore belongs to a different computer or OS user.") from exc

    def _load_or_create(self) -> None:
        if self.file.exists():
            try:
                doc = json.loads(self.file.read_text(encoding="utf-8"))
                payload = json.loads(self._unseal_blob(doc["sealed"]).decode("utf-8"))
                self._primary_key = bytes.fromhex(payload["primary"])
                self.device_label = doc.get("device_label") or self.device_label
                self._extra_keys = {k: bytes.fromhex(v) for k, v in payload.get("extra", {}).items()}
                return
            except Exception as exc:
                log.warning("Creating fresh device key (%s)", exc)
        self._primary_key = os.urandom(32)
        self._save()

    def _save(self) -> None:
        assert self._primary_key is not None
        inner = json.dumps({
            "primary": self._primary_key.hex(),
            "extra": {k: v.hex() for k, v in self._extra_keys.items()},
        }).encode("utf-8")
        outer = {
            "version": 1,
            "key_id": self.key_id,
            "device_label": self.device_label,
            "created": datetime.now().isoformat(timespec="seconds"),
            "sealed": self._seal_blob(inner),
        }
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(outer, indent=2), encoding="utf-8")
        os.replace(tmp, self.file)
        try:
            os.chmod(self.file, 0o600)
        except OSError:
            pass

    @property
    def master_key(self) -> bytes:
        assert self._primary_key is not None
        return self._primary_key

    @property
    def key_id(self) -> str:
        return compute_key_id(self.master_key)

    def key_for(self, key_id: str) -> bytes | None:
        if key_id == self.key_id:
            return self.master_key
        return self._extra_keys.get(key_id)

    def known_key_ids(self) -> list[str]:
        return [self.key_id, *sorted(k for k in self._extra_keys if k != self.key_id)]

    def export_recovery_kit(self, dest: str | os.PathLike, recovery_password: str) -> Path:
        """Export this laptop's vault keys encrypted with a strong user recovery password (``.movaultkey``)."""
        if len(recovery_password) < 6:
            raise VaultError("Choose a recovery password of at least 6 characters.")
        salt = os.urandom(16)
        nonce = os.urandom(12)
        kek = _derive_passphrase_key(recovery_password, salt)
        all_keys = {self.key_id: self.master_key.hex(), **{k: v.hex() for k, v in self._extra_keys.items()}}
        body = json.dumps({
            "device_label": self.device_label,
            "primary_id": self.key_id,
            "keys": all_keys,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
        }).encode("utf-8")
        ct = AESGCM(kek).encrypt(nonce, body, KIT_MAGIC)
        blob = KIT_MAGIC + salt + nonce + ct
        out = Path(dest)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(blob)
        return out

    def import_recovery_kit(self, src: str | os.PathLike, recovery_password: str) -> list[str]:
        """Authorize this computer to open backups encrypted by another/previous laptop of the owner."""
        blob = Path(src).read_bytes()
        if len(blob) < 40 or blob[:8] != KIT_MAGIC:
            raise VaultTamperedError("Not a valid MyOffice Vault Recovery Key file (.movaultkey).")
        salt, nonce, ct = blob[8:24], blob[24:36], blob[36:]
        kek = _derive_passphrase_key(recovery_password, salt)
        try:
            body = json.loads(AESGCM(kek).decrypt(nonce, ct, KIT_MAGIC).decode("utf-8"))
        except InvalidTag as exc:
            raise VaultAccessDeniedError("Incorrect recovery key password.") from exc
        added = []
        for kid, hexkey in body.get("keys", {}).items():
            raw = bytes.fromhex(hexkey)
            real_id = compute_key_id(raw)
            if real_id != self.key_id:
                self._extra_keys[real_id] = raw
                added.append(real_id)
        self._save()
        return added


_default_store: DeviceKeyStore | None = None


def keystore() -> DeviceKeyStore:
    global _default_store
    if _default_store is None:
        _default_store = DeviceKeyStore()
    return _default_store


def reset_keystore_for_tests(store: DeviceKeyStore | None = None) -> None:
    global _default_store
    _default_store = store


# ============================================================================= Credential sealing for config.json
def seal_secret(secret: str, store: DeviceKeyStore | None = None) -> str:
    """Encrypt an API/app password with this machine's hardware key so ``config.json`` holds no cleartext secrets."""
    if not secret:
        return ""
    if secret.startswith(SECRET_PREFIX):
        return secret
    st = store or keystore()
    salt = os.urandom(12)
    nonce = os.urandom(12)
    key = _derive_hkdf(st.master_key, salt, b"myoffice-config-secret-v1")
    ct = AESGCM(key).encrypt(nonce, secret.encode("utf-8"), b"config-secret")
    return SECRET_PREFIX + base64.urlsafe_b64encode(salt + nonce + ct).decode("ascii")


def unseal_secret(token: str, store: DeviceKeyStore | None = None) -> str:
    """Decrypt a secret produced by :func:`seal_secret` (plain legacy strings pass through cleanly)."""
    if not token:
        return ""
    if not token.startswith(SECRET_PREFIX):
        return token
    st = store or keystore()
    try:
        raw = base64.urlsafe_b64decode(token[len(SECRET_PREFIX):].encode("ascii"))
        salt, nonce, ct = raw[:12], raw[12:24], raw[24:]
        key = _derive_hkdf(st.master_key, salt, b"myoffice-config-secret-v1")
        return AESGCM(key).decrypt(nonce, ct, b"config-secret").decode("utf-8")
    except Exception as exc:
        log.warning("Could not unseal stored credential: %s", exc)
        return ""


# ============================================================================= MOVAULT1 file encryption
def encrypt_bytes(data: bytes, original_name: str, original_path: str = "",
                  store: DeviceKeyStore | None = None, passphrase: str = "",
                  created_at: datetime | None = None) -> bytes:
    """Encrypt ``data`` + filename/metadata into a self-contained ``MOVAULT1`` binary envelope."""
    st = store or keystore()
    dek = os.urandom(32)
    salt = os.urandom(16)
    wrap_nonce = os.urandom(12)
    kek = _derive_hkdf(st.master_key, salt, AAD_WRAP_DEV)
    wrapped_dek = AESGCM(kek).encrypt(wrap_nonce, dek, AAD_WRAP_DEV)

    now = (created_at or datetime.now()).isoformat(timespec="seconds")
    sha = hashlib.sha256(data).hexdigest()
    compressed = zlib.compress(data, level=6)
    meta_plain = json.dumps({
        "original_name": original_name,
        "original_path": original_path,
        "created_at": now,
        "size": len(data),
        "sha256": sha,
        "compressed": True,
    }, ensure_ascii=False).encode("utf-8")

    meta_nonce = os.urandom(12)
    meta_ct = AESGCM(dek).encrypt(meta_nonce, meta_plain, AAD_META)
    data_nonce = os.urandom(12)
    data_ct = AESGCM(dek).encrypt(data_nonce, compressed, AAD_DATA)

    outer = {
        "v": 1,
        "key_id": st.key_id,
        "device_label": st.device_label,
        "salt": salt.hex(),
        "wrap_nonce": wrap_nonce.hex(),
        "wrapped_dek": wrapped_dek.hex(),
        "meta_nonce": meta_nonce.hex(),
        "meta_ct": meta_ct.hex(),
        "data_nonce": data_nonce.hex(),
    }
    if passphrase:
        psalt = os.urandom(16)
        pnonce = os.urandom(12)
        pkek = _derive_passphrase_key(passphrase, psalt)
        outer["pass_salt"] = psalt.hex()
        outer["pass_nonce"] = pnonce.hex()
        outer["pass_wrapped_dek"] = AESGCM(pkek).encrypt(pnonce, dek, AAD_WRAP_PASS).hex()

    header_bytes = json.dumps(outer, separators=(",", ":")).encode("utf-8")
    return MAGIC + struct.pack(">I", len(header_bytes)) + header_bytes + data_ct


def _parse_header(blob_or_stream) -> tuple[dict, int]:
    if isinstance(blob_or_stream, (bytes, bytearray, memoryview)):
        buf = bytes(blob_or_stream[:12])
        if len(buf) < 12 or buf[:8] != MAGIC:
            raise VaultTamperedError("Not a valid MyOffice encrypted vault file (missing MOVAULT1 header).")
        hlen = struct.unpack(">I", buf[8:12])[0]
        if hlen <= 2 or 12 + hlen > len(blob_or_stream):
            raise VaultTamperedError("Vault file header is truncated or damaged.")
        try:
            header = json.loads(bytes(blob_or_stream[12:12 + hlen]).decode("utf-8"))
        except Exception as exc:
            raise VaultTamperedError("Vault file header JSON is corrupted.") from exc
        return header, 12 + hlen
    buf = blob_or_stream.read(12)
    if len(buf) < 12 or buf[:8] != MAGIC:
        raise VaultTamperedError("Not a valid MyOffice encrypted vault file (missing MOVAULT1 header).")
    hlen = struct.unpack(">I", buf[8:12])[0]
    if hlen <= 2 or hlen > 4 * 1024 * 1024:
        raise VaultTamperedError("Vault file header length is invalid.")
    raw = blob_or_stream.read(hlen)
    if len(raw) < hlen:
        raise VaultTamperedError("Vault file header is truncated.")
    try:
        header = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise VaultTamperedError("Vault file header JSON is corrupted.") from exc
    return header, 12 + hlen


def _unwrap_dek(header: dict, store: DeviceKeyStore, passphrase: str = "") -> bytes:
    kid = header.get("key_id", "")
    owner = header.get("device_label", "Unknown Computer")
    mk = store.key_for(kid)
    if mk is not None:
        try:
            kek = _derive_hkdf(mk, bytes.fromhex(header["salt"]), AAD_WRAP_DEV)
            return AESGCM(kek).decrypt(bytes.fromhex(header["wrap_nonce"]),
                                       bytes.fromhex(header["wrapped_dek"]), AAD_WRAP_DEV)
        except (InvalidTag, ValueError, KeyError) as exc:
            raise VaultTamperedError("Vault key envelope was tampered with.") from exc
    if passphrase and "pass_wrapped_dek" in header:
        try:
            pkek = _derive_passphrase_key(passphrase, bytes.fromhex(header["pass_salt"]))
            return AESGCM(pkek).decrypt(bytes.fromhex(header["pass_nonce"]),
                                        bytes.fromhex(header["pass_wrapped_dek"]), AAD_WRAP_PASS)
        except InvalidTag as exc:
            raise VaultAccessDeniedError("Access denied: wrong recovery passphrase for this encrypted vault file.") from exc
    hint = (" Enter the recovery passphrase or import the owner's .movaultkey file."
            if "pass_wrapped_dek" in header
            else " Strict Hardware Lock is active — only the owner's laptop can unlock this file.")
    raise VaultAccessDeniedError(
        f"Access denied: this file is encrypted and hardware-locked to \"{owner}\" (Device ID: {kid}).{hint}"
    )


def _decrypt_meta(header: dict, dek: bytes) -> dict:
    try:
        raw = AESGCM(dek).decrypt(bytes.fromhex(header["meta_nonce"]),
                                  bytes.fromhex(header["meta_ct"]), AAD_META)
        return json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise VaultTamperedError("Encrypted vault metadata failed authentication.") from exc


def decrypt_bytes(blob: bytes, store: DeviceKeyStore | None = None, passphrase: str = "") -> tuple[bytes, dict]:
    """Verify authenticity and decrypt ``blob`` -> ``(plaintext_bytes, metadata_dict)``."""
    st = store or keystore()
    header, offset = _parse_header(blob)
    dek = _unwrap_dek(header, st, passphrase=passphrase)
    meta = _decrypt_meta(header, dek)
    try:
        comp = AESGCM(dek).decrypt(bytes.fromhex(header["data_nonce"]), blob[offset:], AAD_DATA)
        plain = zlib.decompress(comp) if meta.get("compressed") else comp
    except InvalidTag as exc:
        raise VaultTamperedError("Vault payload failed AES-256-GCM integrity verification (file modified or damaged).") from exc
    except Exception as exc:
        raise VaultTamperedError(f"Vault payload decompression failed: {exc}") from exc
    if hashlib.sha256(plain).hexdigest() != meta.get("sha256"):
        raise VaultTamperedError("Plaintext SHA-256 mismatch after decryption.")
    meta["key_id"] = header.get("key_id", "")
    meta["device_label"] = header.get("device_label", "")
    return plain, meta


def random_vault_filename() -> str:
    return f"vault_{os.urandom(10).hex()}.movault"


def encrypt_file_to_dir(src: str | os.PathLike, target_dir: str | os.PathLike,
                        store: DeviceKeyStore | None = None, passphrase: str = "") -> Path:
    """Encrypt ``src`` into ``target_dir/vault_<random>.movault`` atomically."""
    src_path = Path(src)
    d = Path(target_dir)
    d.mkdir(parents=True, exist_ok=True)
    blob = encrypt_bytes(src_path.read_bytes(), src_path.name, str(src_path.resolve()),
                         store=store, passphrase=passphrase)
    dest = d / random_vault_filename()
    tmp = d / (dest.name + ".tmp")
    tmp.write_bytes(blob)
    os.replace(tmp, dest)
    return dest


def decrypt_vault_file(vault_path: str | os.PathLike, dest_path: str | os.PathLike,
                       store: DeviceKeyStore | None = None, passphrase: str = "") -> tuple[Path, dict]:
    """Decrypt ``vault_path`` to ``dest_path`` atomically after full GCM + SHA-256 verification."""
    plain, meta = decrypt_bytes(Path(vault_path).read_bytes(), store=store, passphrase=passphrase)
    out = Path(dest_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_bytes(plain)
    os.replace(tmp, out)
    return out, meta


def inspect_vault_file(vault_path: str | os.PathLike, store: DeviceKeyStore | None = None,
                       passphrase: str = "") -> VaultEntry:
    """Read ONLY the header block (fast over Wi-Fi/SMB!) to display metadata on authorized devices."""
    p = Path(vault_path)
    st = store or keystore()
    size = p.stat().st_size
    with p.open("rb") as fh:
        header, _ = _parse_header(fh)
    try:
        dek = _unwrap_dek(header, st, passphrase=passphrase)
        meta = _decrypt_meta(header, dek)
        ts = datetime.fromisoformat(meta["created_at"])
        return VaultEntry(
            path=p,
            vault_filename=p.name,
            original_name=meta["original_name"],
            original_path=meta.get("original_path", ""),
            created_at=ts,
            original_size=int(meta.get("size", 0)),
            encrypted_size=size,
            sha256=meta.get("sha256", ""),
            key_id=header.get("key_id", ""),
            device_label=header.get("device_label", ""),
            locked_to_other_device=False,
        )
    except VaultAccessDeniedError:
        return VaultEntry(
            path=p,
            vault_filename=p.name,
            original_name=f"[Locked to {header.get('device_label', 'Another PC')}]",
            original_path="",
            created_at=datetime.fromtimestamp(p.stat().st_mtime),
            original_size=0,
            encrypted_size=size,
            sha256="",
            key_id=header.get("key_id", ""),
            device_label=header.get("device_label", ""),
            locked_to_other_device=True,
        )


def list_vault_dir(folder: str | os.PathLike, store: DeviceKeyStore | None = None,
                   passphrase: str = "", include_locked: bool = True) -> list[VaultEntry]:
    """List all ``.movault`` backups in ``folder`` (works identically over SMB/router share or local USB)."""
    d = Path(folder)
    if not d.exists() or not d.is_dir():
        return []
    entries: list[VaultEntry] = []
    for p in d.iterdir():
        if p.is_file() and p.suffix.lower() == ".movault":
            try:
                entry = inspect_vault_file(p, store=store, passphrase=passphrase)
                if not entry.locked_to_other_device or include_locked:
                    entries.append(entry)
            except VaultError as exc:
                log.warning("Skipping invalid vault file %s: %s", p, exc)
    entries.sort(key=lambda e: e.created_at, reverse=True)
    return entries
