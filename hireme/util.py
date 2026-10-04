from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


class Blocked(Exception):
    def __init__(self, reason: str, detail: str = ""):
        self.reason, self.detail = reason, detail
        super().__init__(reason + (": " + detail if detail else ""))


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def company_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def company_normalizer(aliases):
    mapping = {company_key(key): company_key(value) for key, value in aliases.items()}
    def normalize(value):
        key = company_key(value)
        return mapping.get(key, key)
    return normalize


def private_dir(path: Path) -> Path:
    if path.is_symlink():
        raise ValueError("Private storage must not be a symlink")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)
    return path


def atomic_json(path: Path, value) -> None:
    private_dir(path.parent)
    if path.is_symlink():
        raise ValueError("Refusing symlink output")
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".write-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(value, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def canonical_url(url: str) -> str:
    p = urlsplit(url)
    if p.scheme != "https" or not p.hostname or p.username or p.password or p.port not in (None, 443):
        raise ValueError("An HTTPS URL without credentials is required")
    host=p.hostname.lower()
    if host in {"boards.greenhouse.io","job-boards.greenhouse.io"}:host="job-boards.greenhouse.io"
    elif host in {"boards.eu.greenhouse.io","job-boards.eu.greenhouse.io"}:host="job-boards.eu.greenhouse.io"
    path=p.path.rstrip("/") or "/"
    if host in {"jobs.lever.co","jobs.eu.lever.co"} and path.endswith("/apply"):path=path[:-6]
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
         if not k.lower().startswith("utm_") and k.lower() not in {"gh_src", "lever-source", "ref"}]
    return urlunsplit(("https", host, path, urlencode(q), ""))


def public_host(host: str) -> bool:
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        return bool(addresses) and all(ipaddress.ip_address(a[4][0]).is_global for a in addresses)
    except (OSError, ValueError):
        return False


def safe_document(path: Path, root: Path) -> Path:
    if not re.fullmatch(r"[a-f0-9]{64}\.pdf", path.name) or path.is_symlink():
        raise ValueError("Unapproved document")
    resolved = path.resolve(strict=True)
    if resolved.parent != root.resolve() or not resolved.is_file():
        raise ValueError("Document outside approved storage")
    if not resolved.read_bytes().startswith(b"%PDF-"):
        raise ValueError("Not a PDF")
    return resolved


def write_private_blob(path: Path, data: bytes) -> None:
    """Immutable content-addressed bytes, durable before their database reference."""
    private_dir(path.parent)
    if path.is_symlink():raise ValueError('Unsafe private file')
    if path.exists():
        if path.read_bytes()!=data:raise ValueError('Private file was modified')
        return
    fd,tmp=tempfile.mkstemp(dir=path.parent,prefix='.blob-')
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(data);f.flush();os.fsync(f.fileno())
        try:os.link(tmp,path)
        except FileExistsError:
            if path.is_symlink() or path.read_bytes()!=data:raise ValueError('Private file changed concurrently')
        directory_fd=os.open(path.parent,os.O_RDONLY)
        try:os.fsync(directory_fd)
        finally:os.close(directory_fd)
    finally:
        Path(tmp).unlink(missing_ok=True)


def restore_imported_blob(path: Path, data: bytes) -> bool:
    """Only an explicit import may restore the exact bytes for a hash-named source."""
    expected = hashlib.sha256(data).hexdigest()
    if not re.fullmatch(re.escape(expected) + r'\.(?:pdf|docx|pptx|txt|md)', path.name):
        raise ValueError('Unsafe imported source destination')
    private_dir(path.parent)
    if path.is_symlink(): raise ValueError('Unsafe imported source destination')
    if not path.exists():
        write_private_blob(path, data)
        return False
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as existing:
            info = os.fstat(existing.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ValueError('Choose a regular imported source destination')
            if existing.read(len(data) + 1) == data and stat.S_IMODE(info.st_mode) == 0o600: return False
    except PermissionError:
        if path.is_symlink() or not path.is_file():
            raise ValueError('Unsafe imported source destination') from None
    except OSError: raise ValueError('Stored source could not be checked') from None
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix='.source-repair-')
    try:
        with os.fdopen(descriptor, 'wb') as repaired:
            repaired.write(data); repaired.flush(); os.fsync(repaired.fileno())
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError('Unsafe imported source destination')
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(directory_fd)
        finally: os.close(directory_fd)
    finally: Path(temporary).unlink(missing_ok=True)
    return True
