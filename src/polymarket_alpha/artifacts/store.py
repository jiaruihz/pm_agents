"""Filesystem primitives for immutable, confined Alpha artifacts.

``ArtifactStore`` is the only public owner of locator validation and the
``openat``/``O_NOFOLLOW`` filesystem boundary.  Callers that need a mutable
append-only protocol may use :meth:`ArtifactStore.open_parent`, but retain
ownership of their own locking and mutation rules.
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import stat

from ..contracts import bytes_sha256


class ArtifactPathError(ValueError):
    """An artifact root or locator cannot be used safely."""


class ArtifactConflictError(ValueError):
    """An immutable locator already contains different or unsafe bytes."""


def normalize_locator(locator: str) -> str:
    """Return one canonical relative POSIX locator or fail closed."""

    if not isinstance(locator, str) or not locator.strip() or "\\" in locator:
        raise ArtifactPathError("locator must be a non-empty POSIX relative path")
    path = PurePosixPath(locator)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ArtifactPathError("locator must not be absolute or traverse directories")
    return str(path)


class ArtifactStore:
    """A caller-selected absolute artifact root with safe immutable I/O."""

    def __init__(self, root: Path) -> None:
        root = Path(root)
        if not root.is_absolute():
            raise ArtifactPathError("artifact_root must be an explicit absolute path")
        if root.is_symlink() or not root.exists() or not root.is_dir():
            raise ArtifactPathError("artifact_root must be an existing non-symlink directory")
        self.root = root

    def normalize_locator(self, locator: str) -> str:
        return normalize_locator(locator)

    def open_parent(self, locator: str, *, create_parents: bool) -> tuple[int, str]:
        """Open a locator parent from owned dirfds without following links.

        The returned fd belongs to the caller and must be closed by it.
        """

        relative = PurePosixPath(normalize_locator(locator))
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
        try:
            current_fd = os.open(self.root, directory_flags)
        except OSError as error:
            raise ArtifactPathError("artifact_root cannot be opened safely") from error
        try:
            for part in relative.parts[:-1]:
                try:
                    next_fd = os.open(part, directory_flags, dir_fd=current_fd)
                except FileNotFoundError:
                    if not create_parents:
                        raise ArtifactPathError("locator parent does not exist")
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=current_fd)
                    except FileExistsError:
                        pass
                    try:
                        next_fd = os.open(part, directory_flags, dir_fd=current_fd)
                    except OSError as error:
                        raise ArtifactPathError("locator parent must be a real directory") from error
                except OSError as error:
                    raise ArtifactPathError("locator parent must be a real directory") from error
                os.close(current_fd)
                current_fd = next_fd
            return current_fd, relative.name
        except Exception:
            os.close(current_fd)
            raise

    def read(self, locator: str) -> bytes:
        """Read exactly one regular, non-symlink file below this store."""

        parent_fd, name = self.open_parent(locator, create_parents=False)
        try:
            return self._read_regular_at(parent_fd, name)
        finally:
            os.close(parent_fd)

    def read_verified(self, locator: str, *, expected_sha256: str) -> bytes:
        """Read an allowed regular file and verify its SHA-256 binding."""

        data = self.read(locator)
        if bytes_sha256(data) != expected_sha256:
            raise ArtifactConflictError(f"artifact hash conflict: {normalize_locator(locator)}")
        return data

    def write_immutable(self, locator: str, data: bytes) -> None:
        """Create an immutable artifact, allowing only byte-identical replay."""

        if not isinstance(data, bytes):
            raise TypeError("immutable artifact data must be bytes")
        normalized = normalize_locator(locator)
        parent_fd, name = self.open_parent(normalized, create_parents=True)
        try:
            try:
                fd = os.open(
                    name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=parent_fd,
                )
            except FileExistsError:
                try:
                    existing = self._read_regular_at(parent_fd, name)
                except ArtifactPathError as error:
                    raise ArtifactConflictError(f"immutable locator conflict: {normalized}") from error
                if existing != data:
                    raise ArtifactConflictError(f"immutable locator conflict: {normalized}")
                return
            except OSError as error:
                raise ArtifactPathError("immutable locator cannot be opened safely") from error
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            os.close(parent_fd)

    @staticmethod
    def _read_regular_at(parent_fd: int, name: str) -> bytes:
        try:
            fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent_fd)
        except OSError as error:
            raise ArtifactPathError("allowlisted file is absent or unsafe") from error
        try:
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode):
                raise ArtifactPathError("allowlisted path must be a regular file")
            with os.fdopen(fd, "rb") as handle:
                fd = -1
                return handle.read()
        finally:
            if fd >= 0:
                os.close(fd)
