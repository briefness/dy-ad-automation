from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".mts", ".m2ts"}


class AssetPathError(ValueError):
    pass


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def validate_asset_directory(path: str | Path, root: str | Path | None = None, output_dir: str | Path | None = None) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.exists():
        raise AssetPathError("素材文件夹不存在")
    if not candidate.is_dir():
        raise AssetPathError("素材路径必须是文件夹")
    resolved = candidate.resolve(strict=True)
    if not os.access(resolved, os.R_OK | os.X_OK):
        raise AssetPathError("素材文件夹不可读")
    output = Path(output_dir or _default_output_dir()).expanduser().resolve()
    if _inside(resolved, output) or _inside(output, resolved):
        raise AssetPathError("素材文件夹不能是输出目录或其父目录")
    videos = [p for p in resolved.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS]
    if not videos:
        raise AssetPathError("素材文件夹中没有支持的视频文件")
    unreadable = next((video for video in videos if not os.access(video, os.R_OK)), None)
    if unreadable:
        raise AssetPathError(f"视频文件不可读：{unreadable.name}")
    return resolved


def _default_output_dir() -> Path:
    try:
        from config import OUTPUT_DIR
        return Path(OUTPUT_DIR)
    except Exception:
        return Path(__file__).resolve().parents[1] / "output"


def is_safe_artifact(path: str | Path, output_dir: str | Path | None = None, cache_roots: Iterable[str | Path] = ()) -> bool:
    candidate = Path(path).expanduser().resolve()
    roots = [Path(output_dir or _default_output_dir()).expanduser().resolve()]
    roots.extend(Path(item).expanduser().resolve() for item in cache_roots)
    return any(_inside(candidate, root) for root in roots) and candidate.is_file()


def safe_artifact_path(relative_path: str, output_dir: str | Path | None = None, cache_roots: Iterable[str | Path] = ()) -> Path:
    if not relative_path or Path(relative_path).is_absolute():
        raise AssetPathError("非法产物路径")
    candidate = Path(output_dir or _default_output_dir()).expanduser().resolve() / relative_path
    if not is_safe_artifact(candidate, output_dir, cache_roots):
        raise AssetPathError("产物路径不在允许目录内")
    return candidate
