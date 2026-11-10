"""Project-relative paths with no symlink traversal, including during reads."""

import os
import hashlib
import uuid
import stat
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from .errors import ScsError


class Paths:
    def __init__(self, root: str | Path):
        raw = Path(root).expanduser()
        if ".." in raw.parts:
            raise ScsError("PATH_INVALID", "Parent navigation is not allowed.")
        self.root = raw.resolve(strict=True)
        if not self.root.is_dir():
            raise ScsError("PATH_INVALID", "Project root must be a directory.")

    def parts(self, relative: str) -> tuple[str, ...]:
        p = PurePosixPath(relative)
        if not relative or "\x00" in relative or "\\" in relative or p.is_absolute() or ".." in p.parts:
            raise ScsError("PATH_INVALID", "Use a project-relative path without parent navigation.")
        if not p.parts or p.parts[0] in {".git", ".scs"}:
            raise ScsError("PATH_INVALID", "Reserved or empty path.")
        return p.parts

    @contextmanager
    def directory(self, parts: tuple[str, ...], create: bool = False, missing_ok: bool = False):
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in parts:
                if create:
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=fd)
                    except FileExistsError:
                        pass
                try:
                    nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                except FileNotFoundError:
                    if missing_ok:
                        yield None
                        return
                    raise
                os.close(fd)
                fd = nxt
            yield fd
        except OSError:
            raise ScsError("PATH_UNSAFE", "Path is absent, inaccessible, or traverses a symlink.") from None
        finally:
            os.close(fd)

    def validate(self, relative: str) -> str:
        parts = self.parts(relative)
        # Missing suffixes are legal for declared scopes, but existing symlinks are not.
        current = self.root
        for part in parts:
            current /= part
            if current.is_symlink():
                raise ScsError("PATH_UNSAFE", "Symlink navigation is not allowed.")
        if not current.resolve().is_relative_to(self.root):
            raise ScsError("PATH_UNSAFE", "Path escapes the project root.")
        return "/".join(parts)

    def mkdir(self, relative: str):
        with self.directory(self.parts(relative), create=True):
            pass

    def create_once(self, relative: str, content: str) -> bool:
        parts = self.parts(relative)
        with self.directory(parts[:-1], create=True) as fd:
            try:
                out = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            except FileExistsError:
                info = os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode):
                    raise ScsError("PATH_UNSAFE", "Existing record must be a regular file.") from None
                return False
            with os.fdopen(out, "w", encoding="utf-8") as stream:
                stream.write(content)
            return True

    def read(self, relative: str, limit: int = 2_000_000) -> bytes:
        parts = self.parts(relative)
        with self.directory(parts[:-1]) as fd:
            try:
                handle = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                with os.fdopen(handle, "rb") as stream:
                    info = os.fstat(stream.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                        raise ScsError("EVIDENCE_INVALID", "Evidence must be a bounded regular file.")
                    data = stream.read(limit + 1)
                    if len(data) > limit:
                        raise ScsError("EVIDENCE_INVALID", "Evidence is too large.")
                    return data
            except OSError:
                raise ScsError("PATH_UNSAFE", "Evidence is absent, inaccessible, or a symlink.") from None

    def read_optional(self, relative: str, limit: int = 2_000_000) -> bytes | None:
        parts = self.parts(relative)
        with self.directory(parts[:-1], missing_ok=True) as fd:
            if fd is None:
                return None
            try:
                handle = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            except FileNotFoundError:
                return None
            with os.fdopen(handle, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                    raise ScsError("PATH_UNSAFE", "Managed files must be bounded regular files.")
                data = stream.read(limit + 1)
                if len(data) > limit:
                    raise ScsError("PATH_UNSAFE", "Managed file exceeds the size limit.")
                return data

    def replace_checked(self, relative: str, data: bytes, expected_sha256: str | None):
        """Optimistic content check and atomic replacement; not an external editor lock."""
        parts = self.parts(relative)
        with self.directory(parts[:-1], create=True) as fd:
            def current_hash():
                try:
                    handle = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                except FileNotFoundError:
                    return None
                with os.fdopen(handle, "rb") as stream:
                    info = os.fstat(stream.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_size > 2_000_000:
                        raise ScsError("PATH_UNSAFE", "Managed target must be a bounded regular file.")
                    value = stream.read(2_000_001)
                    if len(value) > 2_000_000:
                        raise ScsError("PATH_UNSAFE", "Managed target is too large.")
                    return hashlib.sha256(value).hexdigest()
            if current_hash() != expected_sha256:
                raise ScsError("VIEW_CONFLICT", "A file changed during refresh; no overwrite attempted.", {"path": relative})
            temporary = ".scs-write-" + uuid.uuid4().hex
            handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            try:
                with os.fdopen(handle, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                if current_hash() != expected_sha256:
                    raise ScsError("VIEW_CONFLICT", "A file changed during refresh; generated replacement was discarded.", {"path": relative})
                os.replace(temporary, parts[-1], src_dir_fd=fd, dst_dir_fd=fd)
            finally:
                try:
                    os.unlink(temporary, dir_fd=fd)
                except FileNotFoundError:
                    pass

    def docs_root(self) -> str:
        for name in ("00_doc", "00_docs", "docs", "doc"):
            p = self.root / name
            if p.is_symlink():
                raise ScsError("PATH_UNSAFE", "Documentation root cannot be a symlink.")
            if p.is_dir():
                return name
        return "docs"
