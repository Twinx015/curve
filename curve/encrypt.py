#!/usr/bin/env python3
"""Password-based authenticated encryption helpers.

This module only defines reusable functions; importing it has no side effects
and it does not parse command-line arguments. Use it from :mod:`curve.main`
like this::

    from curve.encrypt import decrypt, encrypt

    blob = encrypt("my secret", "a long passphrase")
    text = decrypt(blob, "a long passphrase").decode("utf-8")

For files, use :func:`encrypt_file` and :func:`decrypt_file`::

    encrypt_file("journal.txt", "a long passphrase")   # -> journal.txt.enc
    decrypt_file("journal.txt.enc", "a long passphrase")  # -> journal.txt

The format is self-contained (salt, nonce and KDF parameters are stored in an
authenticated header), so decryption only requires the password.
"""

from __future__ import annotations

import os
import struct
import tempfile
from pathlib import Path
from typing import TypeAlias

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

__all__ = [
    "MAGIC",
    "SALT_LEN",
    "NONCE_LEN",
    "SCRYPT_LOG2_N",
    "SCRYPT_R",
    "SCRYPT_P",
    "HEADER_FMT",
    "HEADER_LEN",
    "derive_key",
    "encrypt",
    "decrypt",
    "encrypt_bytes",
    "decrypt_bytes",
    "encrypt_file",
    "decrypt_file",
    "is_encrypted",
    "write_atomic",
]

MAGIC = b"ENCF1"  # file signature + format version
SALT_LEN = 16
NONCE_LEN = 12
SCRYPT_LOG2_N = 18  # N = 2**18 -> ~256 MB RAM, ~1 second per guess
SCRYPT_R = 8
SCRYPT_P = 1
# header = magic | salt | nonce | log2N | r | p
HEADER_FMT = f">{len(MAGIC)}s{SALT_LEN}s{NONCE_LEN}sBBB"
HEADER_LEN = struct.calcsize(HEADER_FMT)

BytesLike: TypeAlias = bytes | bytearray | memoryview
PathLike: TypeAlias = str | os.PathLike[str]


def _as_bytes(data: str | BytesLike) -> bytes:
    if isinstance(data, str):
        return data.encode("utf-8")
    if isinstance(data, bytes):
        return data
    if isinstance(data, (bytearray, memoryview)):
        return bytes(data)
    raise TypeError("data must be str or bytes-like")


def derive_key(
    password: str,
    salt: bytes,
    log2n: int = SCRYPT_LOG2_N,
    r: int = SCRYPT_R,
    p: int = SCRYPT_P,
) -> bytes:
    """Derive a 32-byte AES key from *password* using scrypt."""
    if not isinstance(password, str):
        raise TypeError("password must be a string")
    kdf = Scrypt(salt=salt, length=32, n=2**log2n, r=r, p=p)
    return kdf.derive(password.encode("utf-8"))


def encrypt(data: str | BytesLike, password: str) -> bytes:
    """Encrypt *data* with *password* and return a self-contained blob."""
    payload = _as_bytes(data)
    salt = os.urandom(SALT_LEN)
    nonce = os.urandom(NONCE_LEN)
    header = struct.pack(
        HEADER_FMT, MAGIC, salt, nonce, SCRYPT_LOG2_N, SCRYPT_R, SCRYPT_P
    )
    key = derive_key(password, salt, SCRYPT_LOG2_N, SCRYPT_R, SCRYPT_P)
    # The header is authenticated too, so changing it breaks decryption.
    ciphertext = AESGCM(key).encrypt(nonce, payload, header)
    return header + ciphertext


def decrypt(blob: BytesLike, password: str) -> bytes:
    """Decrypt a blob produced by :func:`encrypt` and return the raw bytes.

    Raises :class:`ValueError` when the blob is invalid, corrupted or the
    password is wrong.
    """
    data = _as_bytes(blob)
    if len(data) < HEADER_LEN + 16 or not data.startswith(MAGIC):
        raise ValueError("Not a file made by this program (or it is corrupted).")

    header = data[:HEADER_LEN]
    _, salt, nonce, log2n, r, p = struct.unpack(HEADER_FMT, header)
    if log2n > 22:  # refuse absurd memory requests from a tampered header
        raise ValueError("Unsafe key-derivation settings in file header.")

    key = derive_key(password, salt, log2n, r, p)
    try:
        return AESGCM(key).decrypt(nonce, data[HEADER_LEN:], header)
    except InvalidTag:
        raise ValueError("Wrong password, or the file was modified/corrupted.") from None


def is_encrypted(blob: str | BytesLike) -> bool:
    """Return ``True`` if *blob* looks like output from :func:`encrypt`."""
    try:
        data = _as_bytes(blob)
    except TypeError:
        return False
    return len(data) >= HEADER_LEN + 16 and data.startswith(MAGIC)


def write_atomic(
    path: PathLike,
    data: str | BytesLike,
    *,
    overwrite: bool = False,
    mode: int | None = None,
) -> Path:
    """Write *data* to *path* atomically.

    Raises :class:`FileExistsError` if *path* exists and *overwrite* is false.
    Returns the destination path.
    """
    destination = Path(path).expanduser()
    if destination.exists() and not overwrite:
        raise FileExistsError(
            f"'{destination}' already exists; pass overwrite=True to replace it"
        )

    payload = _as_bytes(data)
    fd, temp_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        if mode is not None:
            os.chmod(temp_path, mode)
        os.replace(temp_path, destination)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
    return destination


def encrypt_file(
    path: PathLike,
    password: str,
    *,
    out: PathLike | None = None,
    overwrite: bool = False,
) -> Path:
    """Encrypt a file and return the output path.

    By default the output is ``<path>.enc``. Pass *overwrite* to replace an
    existing output file.
    """
    source = Path(path).expanduser()
    destination = (
        Path(out).expanduser()
        if out is not None
        else source.with_name(source.name + ".enc")
    )
    with source.open("rb") as file:
        payload = file.read()
    return write_atomic(
        destination, encrypt(payload, password), overwrite=overwrite
    )


def decrypt_file(
    path: PathLike,
    password: str,
    *,
    out: PathLike | None = None,
    overwrite: bool = False,
) -> Path:
    """Decrypt a file and return the output path.

    By default ``foo.enc`` becomes ``foo``; other names get a ``.dec`` suffix.
    Pass *overwrite* to replace an existing output file.
    """
    source = Path(path).expanduser()
    if out is not None:
        destination = Path(out).expanduser()
    elif source.suffix == ".enc":
        destination = source.with_suffix("")
    else:
        destination = source.with_name(source.name + ".dec")

    with source.open("rb") as file:
        blob = file.read()
    return write_atomic(destination, decrypt(blob, password), overwrite=overwrite)


# Backwards-compatible names for the byte-level helpers.
encrypt_bytes = encrypt
decrypt_bytes = decrypt
