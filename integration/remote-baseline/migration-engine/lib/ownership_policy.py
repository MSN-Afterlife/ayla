from __future__ import annotations

import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path

from .adapters import AdapterError

CHECK_NAME = "OWNERSHIP_INTEGRITY_CHECK"


@dataclass(frozen=True)
class OwnershipPolicy:
    root: Path
    uid: int
    gid: int

    @classmethod
    def detect(cls, root: Path) -> "OwnershipPolicy":
        root = Path(root)
        st = root.stat()
        return cls(root=root, uid=st.st_uid, gid=st.st_gid)

    @classmethod
    def explicit(cls, root: Path, uid: int, gid: int) -> "OwnershipPolicy":
        return cls(root=Path(root), uid=int(uid), gid=int(gid))

    def check_path(self, path: Path, *, must_exist: bool = True) -> bool:
        path = Path(path)
        if not path.exists():
            if must_exist:
                raise AdapterError(f"{CHECK_NAME}: missing path: {path}")
            return True
        st = path.stat()
        if st.st_uid != self.uid or st.st_gid != self.gid:
            raise AdapterError(
                f"{CHECK_NAME}: {path} owner {st.st_uid}:{st.st_gid} != expected {self.uid}:{self.gid}"
            )
        return True

    def check_tree(self, root: Path | None = None) -> bool:
        root = Path(root or self.root)
        self.check_path(root)
        for path in root.rglob("*"):
            self.check_path(path)
        return True

    def normalize_path(self, path: Path, *, recursive: bool = False) -> None:
        path = Path(path)
        if not path.exists():
            raise AdapterError(f"{CHECK_NAME}: cannot normalize missing path: {path}")
        paths = [path]
        if recursive and path.is_dir():
            paths.extend(path.rglob("*"))
        for item in paths:
            os.chown(item, self.uid, self.gid)
        for item in paths:
            self.check_path(item)

    def ensure_dir(self, path: Path, *, mode: int | None = None) -> Path:
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        candidates = []
        root_resolved = self.root.resolve()
        for item in [path, *path.parents]:
            try:
                if not item.resolve().is_relative_to(root_resolved):
                    break
            except FileNotFoundError:
                continue
            candidates.append(item)
            if item.resolve() == root_resolved:
                break
        for item in candidates:
            os.chown(item, self.uid, self.gid)
        if mode is not None:
            os.chmod(path, stat.S_IMODE(mode))
        self.check_path(path)
        return path

    def copy2(self, src: Path, dst: Path) -> Path:
        src = Path(src)
        dst = Path(dst)
        self.ensure_dir(dst.parent)
        shutil.copy2(src, dst)
        os.chmod(dst, stat.S_IMODE(src.stat().st_mode))
        self.normalize_path(dst)
        return dst

    def copytree(self, src: Path, dst: Path, **kwargs) -> Path:
        src = Path(src)
        dst = Path(dst)
        shutil.copytree(src, dst, copy_function=shutil.copy2, **kwargs)
        for path in [dst, *dst.rglob("*")]:
            source = src / path.relative_to(dst)
            if source.exists():
                os.chmod(path, stat.S_IMODE(source.stat().st_mode))
            os.chown(path, self.uid, self.gid)
        self.check_tree(dst)
        return dst

    def remove_file(self, path: Path) -> None:
        path = Path(path)
        if path.exists():
            self.check_path(path)
            path.unlink()


def mode_for_publish(destination: Path, source: Path | None = None, default: int = 0o640) -> int:
    destination = Path(destination)
    if destination.exists():
        return stat.S_IMODE(destination.stat().st_mode)
    if source is not None and Path(source).exists():
        return stat.S_IMODE(Path(source).stat().st_mode)
    return default
