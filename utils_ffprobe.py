"""
FFprobe 工具函数（带 LRU 缓存，避免重复调用 subprocess）
"""
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Tuple, Optional


@lru_cache(maxsize=128)
def get_video_duration(video_path: str) -> float:
    """
    获取视频时长（秒），带 LRU 缓存

    Args:
        video_path: 视频文件路径（字符串，用于 lru_cache 哈希）

    Returns:
        时长（秒），失败返回 0.0
    """
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except (subprocess.TimeoutExpired, ValueError):
        pass
    return 0.0


@lru_cache(maxsize=128)
def get_video_resolution(video_path: str) -> Tuple[int, int]:
    """
    获取视频分辨率 (width, height)，带 LRU 缓存

    Args:
        video_path: 视频文件路径（字符串，用于 lru_cache 哈希）

    Returns:
        (width, height)，失败返回 (0, 0)
    """
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0 and result.stdout.strip():
            lines = result.stdout.strip().split("\n")
            if len(lines) >= 2:
                return int(lines[0]), int(lines[1])
    except (subprocess.TimeoutExpired, ValueError):
        pass
    return 0, 0


@lru_cache(maxsize=128)
def get_audio_duration(audio_path: str) -> float:
    """
    获取音频时长（秒），带 LRU 缓存

    Args:
        audio_path: 音频文件路径（字符串，用于 lru_cache 哈希）

    Returns:
        时长（秒），失败返回 0.0
    """
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        audio_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except (subprocess.TimeoutExpired, ValueError):
        pass
    return 0.0
