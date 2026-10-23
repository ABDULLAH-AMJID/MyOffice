"""Password protection ("password to open") for DOCX, XLSX and PPTX - the same ECMA-376 Agile Encryption
(AES-256, SHA-512) that Microsoft Office uses, so Word, Excel and PowerPoint open the files after asking for the
password.  Implemented with msoffcrypto-tool (MIT)."""
from __future__ import annotations

import io
import os
import tempfile

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
OOXML_EXTS = {".docx", ".docm", ".dotx", ".xlsx", ".xlsm", ".xltx", ".pptx", ".pptm", ".potx", ".ppsx"}


class WrongPassword(ValueError):
    pass


def is_encrypted(path: str | os.PathLike) -> bool:
    """An encrypted OOXML file is an OLE container with EncryptionInfo/EncryptedPackage streams."""
    p = str(path)
    if os.path.splitext(p)[1].lower() not in OOXML_EXTS:
        return False
    try:
        with open(p, "rb") as f:
            if f.read(8) != OLE_MAGIC:
                return False
        import olefile

        with olefile.OleFileIO(p) as ole:
            return ole.exists("EncryptionInfo") and ole.exists("EncryptedPackage")
    except Exception:
        return False


def decrypt_to_temp(path: str | os.PathLike, password: str) -> str:
    """Decrypt into a temporary file (same extension) and return its path."""
    import msoffcrypto
    from msoffcrypto.exceptions import InvalidKeyError

    with open(path, "rb") as f:
        office = msoffcrypto.OfficeFile(f)
        try:
            office.load_key(password=password, verify_password=True)
        except InvalidKeyError as exc:
            raise WrongPassword("The password is not correct.") from exc
        out = io.BytesIO()
        try:
            office.decrypt(out)
        except InvalidKeyError as exc:
            raise WrongPassword("The password is not correct.") from exc
    fd, tmp = tempfile.mkstemp(prefix="myoffice_dec_", suffix=os.path.splitext(str(path))[1].lower())
    with os.fdopen(fd, "wb") as f:
        f.write(out.getvalue())
    return tmp


def encrypt_file(plain: str | os.PathLike, target: str | os.PathLike, password: str) -> None:
    """Write ``plain`` (an ordinary OOXML file) to ``target`` encrypted with ``password``."""
    if not password:
        raise ValueError("A password is required.")
    from msoffcrypto.format.ooxml import OOXMLFile

    data = io.BytesIO()
    with open(plain, "rb") as f:
        OOXMLFile(f).encrypt(password, data)
    tmp = str(target) + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data.getvalue())
    os.replace(tmp, target)
