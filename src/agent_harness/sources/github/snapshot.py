"""In-memory snapshot of a whole repository, built from one tarball download.

Nothing is written to disk, so archive paths cannot escape anywhere; sizes are bounded to
defuse tar bombs.
"""

from __future__ import annotations

import io
import tarfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from agent_harness.domain.errors import ToolFailed

IGNORED_DIRS = ("node_modules/", "vendor/", ".git/", "dist/", "build/", ".venv/", "venv/", "__pycache__/",
                ".next/", ".tox/", ".mypy_cache/", ".pytest_cache/", "target/", "coverage/")  # fmt: skip
MAX_FILE_BYTES = 512_000
MAX_TOTAL_BYTES = 200_000_000
MAX_FILES = 20_000


def is_ignored(path: str) -> bool:
    return any(path.startswith(d) or f"/{d}" in path for d in IGNORED_DIRS)


@dataclass(slots=True)
class RepositorySnapshot:
    ref: str
    #: Text files by repo-relative path.
    files: dict[str, str] = field(default_factory=dict)
    #: Every non-ignored file with its size, including binaries and oversized files.
    sizes: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)
    ignored_count: int = 0

    @classmethod
    def from_tarball(cls, data: bytes, ref: str, *, max_file_bytes: int = MAX_FILE_BYTES) -> RepositorySnapshot:
        snapshot = cls(ref=ref)
        total = 0
        try:
            archive = tarfile.open(fileobj=io.BytesIO(data), mode="r:*")
        except tarfile.TarError as exc:
            raise ToolFailed(f"cannot read repository archive: {exc}") from exc
        with archive:
            for member in archive:
                if not member.isfile():
                    continue
                parts = PurePosixPath(member.name).parts
                if len(parts) < 2:
                    continue
                path = "/".join(parts[1:])  # drop the "<owner>-<repo>-<sha>/" prefix
                if is_ignored(path):
                    snapshot.ignored_count += 1
                    continue
                if len(snapshot.sizes) >= MAX_FILES:
                    raise ToolFailed(f"repository has more than {MAX_FILES} files")
                snapshot.sizes[path] = member.size
                if member.size > max_file_bytes:
                    snapshot.skipped[path] = f"larger than {max_file_bytes} bytes"
                    continue
                total += member.size
                if total > MAX_TOTAL_BYTES:
                    raise ToolFailed("repository content exceeds the snapshot size limit")
                extracted = archive.extractfile(member)
                raw = extracted.read() if extracted else b""
                if b"\x00" in raw[:8192]:
                    snapshot.skipped[path] = "binary"
                    continue
                snapshot.files[path] = raw.decode("utf-8", errors="replace")
        return snapshot

    def tree(self) -> list[dict]:
        """Same shape as the GitHub git/trees API: directories and blobs."""
        dirs = {"/".join(p.split("/")[:i]) for p in self.sizes for i in range(1, p.count("/") + 1)}
        return [{"path": d, "type": "tree", "size": None} for d in sorted(dirs)] + [
            {"path": p, "type": "blob", "size": s} for p, s in sorted(self.sizes.items())
        ]
