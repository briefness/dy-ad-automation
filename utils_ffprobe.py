"""
FFprobe 工具函数（带 LRU 缓存，避免重复调用 subprocess）
"""
import subprocess
import json
from functools import lru_cache
from pathlib import Path
from typing import Tuple, Optional, Dict, Any


@lru_cache(maxsize=128)
def get_video_info(video_path: str) -> Dict[str, Any]:
    """
    获取完整视频信息（分辨率、帧率、时长、编码等），带 LRU 缓存

    Args:
        video_path: 视频文件路径（字符串，用于 lru_cache 哈希）

    Returns:
        视频信息字典
    """
    info = {
        "width": 1080,
        "height": 1920,
        "fps": 30.0,
        "duration": 0.0,
        "video_codec": "unknown",
        "audio_codec": "unknown",
        "bitrate": 0,
        "has_audio": False,
        "pixel_format": "yuv420p",
    }
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format", "-show_streams",
        video_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            fmt = data.get("format", {})
            info["duration"] = float(fmt.get("duration", 0))
            info["bitrate"] = int(fmt.get("bit_rate", 0))

            for stream in data.get("streams", []):
                codec_type = stream.get("codec_type", "")
                if codec_type == "video":
                    info["width"] = int(stream.get("width", 1080))
                    info["height"] = int(stream.get("height", 1920))
                    info["video_codec"] = stream.get("codec_name", "unknown")
                    info["pixel_format"] = stream.get("pix_fmt", "yuv420p")
                    fps_str = stream.get("r_frame_rate", "30/1")
                    if "/" in fps_str:
                        num, den = fps_str.split("/")
                        try:
                            info["fps"] = float(num) / float(den) if float(den) > 0 else 30.0
                        except (ValueError, ZeroDivisionError):
                            info["fps"] = 30.0
                elif codec_type == "audio":
                    info["has_audio"] = True
                    info["audio_codec"] = stream.get("codec_name", "unknown")
    except (subprocess.TimeoutExpired, json.JSONDecodeError, ValueError, KeyError):
        pass
    return info


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
