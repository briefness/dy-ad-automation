"""Hardware-accelerated and single-pass FFmpeg render backend for local editing."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_HW_LOCK = threading.Lock()
_HW_ENCODER_CACHE: Optional[Tuple[bool, Optional[str]]] = None

_SUPPORTED_HW_ENCODERS = (
    ("darwin", "h264_videotoolbox"),
    ("linux", "h264_nvenc"),
    ("win32", "h264_nvenc"),
    ("linux", "h264_qsv"),
    ("win32", "h264_qsv"),
    ("linux", "h264_vaapi"),
)


def _ffmpeg_escape_filter_path(path: Path | str) -> str:
    text = str(path).replace("\\", "/")
    for char in ("\\", ":", "'", ",", "[", "]", ";"):
        text = text.replace(char, f"\\{char}")
    return text


def detect_hardware_encoder(force_refresh: bool = False) -> Optional[str]:
    """Detect an available H.264 hardware encoder, honoring VIDEO_HW_ACCEL."""
    global _HW_ENCODER_CACHE
    env_mode = os.getenv("VIDEO_HW_ACCEL", "auto").strip().lower()
    if env_mode in {"0", "off", "false", "none", "cpu", "software"}:
        return None
    if env_mode not in {"", "auto", "1", "true", "on"}:
        return env_mode

    with _HW_LOCK:
        if _HW_ENCODER_CACHE is not None and not force_refresh:
            return _HW_ENCODER_CACHE[1]

        encoder: Optional[str] = None
        ffmpeg_bin = shutil.which("ffmpeg")
        if ffmpeg_bin:
            try:
                proc = subprocess.run(
                    [ffmpeg_bin, "-hide_banner", "-encoders"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                stdout = proc.stdout or ""
                platform = sys.platform
                candidates = [
                    name for plat, name in _SUPPORTED_HW_ENCODERS if plat == platform
                ] + [
                    name for plat, name in _SUPPORTED_HW_ENCODERS if plat != platform
                ]
                for candidate in dict.fromkeys(candidates):
                    if re.search(rf"\b{re.escape(candidate)}\b", stdout):
                        encoder = candidate
                        break
            except Exception:
                encoder = None

        _HW_ENCODER_CACHE = (True, encoder)
        return encoder


def _parse_bitrate_mbps(bitrate: str, default: float = 6.0) -> float:
    match = re.search(r"[\d.]+", str(bitrate or ""))
    if not match:
        return default
    value = float(match.group())
    lower = str(bitrate).lower()
    if "k" in lower:
        return max(0.5, value / 1000.0)
    return max(0.5, value)


def _vbv_args(bitrate: str = "6M") -> List[str]:
    mbps = _parse_bitrate_mbps(bitrate, 6.0)
    maxrate = f"{max(1, round(mbps * 1.5))}M"
    bufsize = f"{max(2, round(mbps * 2.0))}M"
    return ["-maxrate", maxrate, "-bufsize", bufsize]


def _color_range_args() -> List[str]:
    return [
        "-color_primaries", "bt709",
        "-color_trc", "bt709",
        "-colorspace", "bt709",
        "-color_range", "pc",
    ]


def video_encode_args(
    *,
    profile: str = "intermediate",
    bitrate: str = "6M",
    allow_hw: bool = True,
    fps: Optional[int] = None,
) -> List[str]:
    """Return FFmpeg H.264 video encoder flags tuned for speed and quality.

    - ``intermediate``: uses hardware acceleration when available (e.g. macOS
      ``h264_videotoolbox``) or fast near-lossless ``libx264 -preset veryfast -crf 15``
      so intermediate editing passes don't waste time on ``-preset slow``.
    - ``final``: uses strict VBV ABR encoding for platform delivery; uses
      hardware encoding only when explicitly requested via ``VIDEO_FINAL_HW_ACCEL=1``.
    """
    hw_encoder = detect_hardware_encoder() if allow_hw else None
    use_hw_final = os.getenv("VIDEO_FINAL_HW_ACCEL", "0").strip().lower() in {
        "1", "true", "yes", "on",
    }

    if profile == "intermediate":
        if hw_encoder == "h264_videotoolbox":
            args = [
                "-c:v", "h264_videotoolbox",
                "-b:v", "14M",
                "-allow_sw", "1",
                "-profile:v", "high",
                "-pix_fmt", "yuv420p",
                *_color_range_args(),
            ]
        elif hw_encoder == "h264_nvenc":
            args = [
                "-c:v", "h264_nvenc",
                "-preset", "p4",
                "-cq", "18",
                "-b:v", "14M",
                "-pix_fmt", "yuv420p",
                *_color_range_args(),
            ]
        elif hw_encoder == "h264_qsv":
            args = [
                "-c:v", "h264_qsv",
                "-global_quality", "18",
                "-b:v", "14M",
                "-pix_fmt", "yuv420p",
                *_color_range_args(),
            ]
        else:
            args = [
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "15",
                *_vbv_args(bitrate),
                "-pix_fmt", "yuv420p",
                *_color_range_args(),
            ]
    else:
        if hw_encoder == "h264_videotoolbox" and use_hw_final:
            args = [
                "-c:v", "h264_videotoolbox",
                "-b:v", bitrate,
                *_vbv_args(bitrate),
                "-allow_sw", "1",
                "-profile:v", "high",
                "-pix_fmt", "yuv420p",
                *_color_range_args(),
            ]
        elif hw_encoder == "h264_nvenc" and use_hw_final:
            args = [
                "-c:v", "h264_nvenc",
                "-preset", "p5",
                "-b:v", bitrate,
                *_vbv_args(bitrate),
                "-profile:v", "high",
                "-pix_fmt", "yuv420p",
                *_color_range_args(),
            ]
        else:
            preset = os.getenv("VIDEO_FINAL_PRESET", "medium").strip() or "medium"
            args = [
                "-c:v", "libx264",
                "-preset", preset,
                "-profile:v", "high",
                "-level", "4.0",
                "-b:v", bitrate,
                *_vbv_args(bitrate),
                "-pix_fmt", "yuv420p",
                *_color_range_args(),
            ]

    if fps is not None and int(fps) > 0:
        args.extend(["-r", str(int(fps)), "-g", str(int(fps) * 2)])
    return args


def probe_video_info(video_path: Path) -> Dict[str, Any]:
    """Probe dimensions, duration, and audio presence in one ffprobe call."""
    info: Dict[str, Any] = {
        "width": 0,
        "height": 0,
        "duration": 0.0,
        "has_audio": False,
    }
    try:
        proc = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "stream=codec_type,width,height:format=duration",
                "-of", "json",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return info
        data = json.loads(proc.stdout)
        for stream in data.get("streams") or []:
            if stream.get("codec_type") == "video" and not info["width"]:
                info["width"] = int(stream.get("width") or 0)
                info["height"] = int(stream.get("height") or 0)
            elif stream.get("codec_type") == "audio":
                info["has_audio"] = True
        raw_dur = (data.get("format") or {}).get("duration")
        if raw_dur is not None:
            info["duration"] = float(raw_dur)
    except Exception:
        pass
    return info


def build_color_grading_filters(
    preset: str = "none",
    custom_params: Optional[Dict[str, Any]] = None,
    brand_color_tint: bool = False,
) -> List[str]:
    """Build FFmpeg video filter clauses for unified color grading."""
    from config import BRAND_CONFIG, COLOR_GRADING_PRESETS, DEFAULT_COLOR_GRADING

    if preset == "none" and not custom_params and not brand_color_tint:
        return []
    params = COLOR_GRADING_PRESETS.get(
        preset,
        COLOR_GRADING_PRESETS.get(DEFAULT_COLOR_GRADING, {}),
    ).copy()
    if custom_params:
        params.update(custom_params)

    no_tint_presets = {"noir", "vintage", "moody", "none"}
    if brand_color_tint and preset not in no_tint_presets:
        brand_color = BRAND_CONFIG.get("primary_color", "#FF6B6B")
        params["color_overlay"] = (brand_color, 0.06)

    filters: List[str] = []
    eq_brightness = float(params.get("brightness", 1.0)) - 1.0
    eq_contrast = float(params.get("contrast", 1.0))
    eq_saturation = float(params.get("saturation", 1.0))
    eq_gamma = float(params.get("gamma", 1.0))
    eq_parts = []
    if abs(eq_brightness) > 0.001:
        eq_parts.append(f"brightness={eq_brightness:.3f}")
    if abs(eq_contrast - 1.0) > 0.001:
        eq_parts.append(f"contrast={eq_contrast:.3f}")
    if abs(eq_saturation - 1.0) > 0.001:
        eq_parts.append(f"saturation={eq_saturation:.3f}")
    if abs(eq_gamma - 1.0) > 0.001:
        eq_parts.append(f"gamma={eq_gamma:.3f}")
    if eq_parts:
        filters.append(f"eq={':'.join(eq_parts)}")

    temperature = float(params.get("temperature", 0))
    tint = float(params.get("tint", 0))
    temp_red = temperature * 0.15
    temp_blue = -temperature * 0.15
    tint_green = -tint * 0.1
    shadows_red = float(params.get("shadows_red", temp_red))
    shadows_green = float(params.get("shadows_green", tint_green))
    shadows_blue = float(params.get("shadows_blue", temp_blue))
    highlights_red = float(params.get("highlights_red", temp_red * 0.5))
    highlights_green = float(params.get("highlights_green", tint_green * 0.5))
    highlights_blue = float(params.get("highlights_blue", temp_blue * 0.5))
    if any(
        abs(val) > 0.001
        for val in (
            shadows_red, shadows_green, shadows_blue,
            highlights_red, highlights_green, highlights_blue,
        )
    ):
        filters.append(
            "colorbalance="
            f"rs={shadows_red:.3f}:gs={shadows_green:.3f}:bs={shadows_blue:.3f}:"
            f"rh={highlights_red:.3f}:gh={highlights_green:.3f}:bh={highlights_blue:.3f}"
        )

    color_overlay = params.get("color_overlay")
    if color_overlay and isinstance(color_overlay, (tuple, list)) and len(color_overlay) == 2:
        overlay_color, opacity = color_overlay
        hex_color = str(overlay_color).lstrip("#")
        if len(hex_color) == 6:
            r = int(hex_color[0:2], 16) / 255.0
            g = int(hex_color[2:4], 16) / 255.0
            b = int(hex_color[4:6], 16) / 255.0
            mix = float(opacity)
            filters.append(
                f"colorchannelmixer=rr={1 - mix + mix * r:.3f}:"
                f"gg={1 - mix + mix * g:.3f}:bb={1 - mix + mix * b:.3f}"
            )
    return filters


def _measure_two_pass_loudnorm(
    input_video: Path,
    audio_lufs: float,
    audio_peak: float,
    duration: float,
) -> Tuple[float, float, float, float, float]:
    measured_i = audio_lufs
    measured_lra = 7.0
    measured_tp = audio_peak
    measured_thresh = audio_lufs - 10.0
    offset = 0.0
    try:
        analyze_cmd = [
            "ffmpeg", "-y",
            "-i", str(input_video),
            "-af", (
                "acompressor=threshold=0.126:ratio=3:attack=10:release=150:makeup=1,"
                f"loudnorm=I={audio_lufs}:LRA=7:TP={audio_peak}:print_format=json"
            ),
            "-vn", "-f", "null", "-",
        ]
        timeout = max(120, int(max(duration, 1.0) * 5))
        proc = subprocess.run(analyze_cmd, capture_output=True, text=True, timeout=timeout)
        stderr = proc.stderr or ""
        start_idx = stderr.rfind("{")
        end_idx = stderr.rfind("}") + 1
        if start_idx >= 0 and end_idx > start_idx:
            data = json.loads(stderr[start_idx:end_idx])
            measured_i = float(data.get("input_i", audio_lufs))
            measured_lra = float(data.get("input_lra", 7.0))
            measured_tp = float(data.get("input_tp", audio_peak))
            measured_thresh = float(data.get("input_thresh", audio_lufs - 10.0))
            offset = float(data.get("target_offset", 0.0))
    except Exception:
        pass
    return measured_i, measured_lra, measured_tp, measured_thresh, offset


def render_single_pass_composite(
    input_video: Path,
    output: Path,
    *,
    resolution: str = "1080x1920",
    fps: int = 30,
    bitrate: str = "6M",
    grading_filters: Optional[Sequence[str]] = None,
    ass_path: Optional[Path] = None,
    fonts_dir: Optional[Path] = None,
    logo_config: Optional[Dict[str, Any]] = None,
    sticker_overlays: Optional[Sequence[Dict[str, Any]]] = None,
    audio_lufs: float = -14.0,
    audio_peak: float = -1.0,
    normalize_audio: bool = True,
    profile: str = "final",
    allow_hw: bool = True,
) -> Path:
    """Composite grading, ASS subtitles, logo, stickers, and final export in ONE encode pass.

    Eliminates generation loss and multi-pass re-encoding overhead compared to
    chaining separate FFmpeg invocations for each visual overlay.
    """
    input_video = Path(input_video)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    info = probe_video_info(input_video)
    target_w, target_h = (int(part) for part in resolution.split("x"))
    duration = float(info.get("duration") or 0.0)
    has_audio = bool(info.get("has_audio"))

    inputs: List[str] = ["-i", str(input_video)]
    filter_parts: List[str] = []

    base_chain: List[str] = []
    if info["width"] != target_w or info["height"] != target_h:
        base_chain.append(
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
            f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
        )
    for clause in grading_filters or ():
        if clause:
            base_chain.append(clause)
    if ass_path and Path(ass_path).is_file():
        escaped_ass = _ffmpeg_escape_filter_path(Path(ass_path))
        if fonts_dir and Path(fonts_dir).is_dir():
            escaped_fonts = _ffmpeg_escape_filter_path(Path(fonts_dir))
            base_chain.append(f"ass={escaped_ass}:fontsdir={escaped_fonts}")
        else:
            base_chain.append(f"ass={escaped_ass}")

    current_v = "[0:v]"
    if base_chain:
        filter_parts.append(f"[0:v]{','.join(base_chain)}[vbase]")
        current_v = "[vbase]"

    next_input_idx = 1

    if logo_config and logo_config.get("logo_path") and Path(logo_config["logo_path"]).is_file():
        logo_path = Path(logo_config["logo_path"])
        inputs.extend(["-i", str(logo_path)])
        logo_idx = next_input_idx
        next_input_idx += 1

        size_ratio = float(logo_config.get("size_ratio", 0.08))
        margin_ratio = float(logo_config.get("margin_ratio", 0.03))
        opacity = float(logo_config.get("opacity", 0.9))
        fade_in = float(logo_config.get("fade_in", 0.5))
        fade_out = float(logo_config.get("fade_out", 0.5))
        position = str(logo_config.get("position", "top_right"))
        logo_target_w = max(16, int(target_w * size_ratio))
        margin_x = int(target_w * margin_ratio)
        margin_y = int(target_h * margin_ratio)
        pos_map = {
            "top_left": (str(margin_x), str(margin_y)),
            "top_right": (f"W-w-{margin_x}", str(margin_y)),
            "bottom_left": (str(margin_x), f"H-h-{margin_y}"),
            "bottom_right": (f"W-w-{margin_x}", f"H-h-{margin_y}"),
        }
        x_expr, y_expr = pos_map.get(position, pos_map["top_right"])
        fade_out_start = max(0.0, duration - fade_out) if duration > 0 else 5.0
        filter_parts.append(
            f"[{logo_idx}:v]scale={logo_target_w}:-1,format=rgba,"
            f"colorchannelmixer=aa={opacity:.3f},"
            f"fade=t=in:st=0:d={fade_in:.3f}:alpha=1,"
            f"fade=t=out:st={fade_out_start:.3f}:d={fade_out:.3f}:alpha=1[vlogo];"
            f"{current_v}[vlogo]overlay=x={x_expr}:y={y_expr}:format=auto[vwithlogo]"
        )
        current_v = "[vwithlogo]"

    for idx, sticker in enumerate(sticker_overlays or ()):
        s_path = Path(str(sticker.get("path") or ""))
        if not s_path.is_file():
            continue
        inputs.extend(["-i", str(s_path)])
        s_idx = next_input_idx
        next_input_idx += 1
        x = int(sticker.get("x", 0))
        y = int(sticker.get("y", 0))
        st = max(0.0, float(sticker.get("start", 0.0)))
        en = max(st + 0.1, float(sticker.get("end", st + 1.5)))
        out_label = f"[vst{idx}]"
        filter_parts.append(
            f"{current_v}[{s_idx}:v]overlay=x={x}:y={y}:"
            f"enable='between(t,{st:.3f},{en:.3f})':format=auto{out_label}"
        )
        current_v = out_label

    cmd: List[str] = ["ffmpeg", "-y", *inputs]
    if filter_parts:
        cmd.extend(["-filter_complex", ";".join(filter_parts), "-map", current_v])
    else:
        cmd.extend(["-map", "0:v"])

    cmd.extend(video_encode_args(profile=profile, bitrate=bitrate, allow_hw=allow_hw, fps=fps))
    cmd.extend(["-movflags", "+faststart"])

    if has_audio:
        cmd.extend(["-map", "0:a"])
        if normalize_audio:
            mi, mlra, mtp, mthresh, moff = _measure_two_pass_loudnorm(
                input_video, audio_lufs, audio_peak, duration
            )
            audio_filter = (
                "acompressor=threshold=0.126:ratio=3:attack=10:release=150:makeup=1,"
                f"loudnorm=I={audio_lufs}:LRA=7:TP={audio_peak}"
                f":measured_I={mi:.2f}:measured_LRA={mlra:.2f}"
                f":measured_TP={mtp:.2f}:measured_thresh={mthresh:.2f}"
                f":offset={moff:.2f}:linear=true,"
                f"alimiter=limit={audio_peak}dB:level=false:attack=5:release=50,"
                "aformat=sample_rates=44100:sample_fmts=fltp:channel_layouts=stereo"
            )
            cmd.extend(["-af", audio_filter, "-c:a", "aac", "-b:a", "192k", "-ar", "44100"])
        else:
            cmd.extend(["-c:a", "copy"])
    else:
        cmd.append("-an")

    cmd.append(str(output))
    timeout = max(300, int(max(duration, 1.0) * 30))
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    if proc.returncode != 0:
        # Automatic software fallback if hardware encoder rejected parameters
        if allow_hw and detect_hardware_encoder() is not None:
            return render_single_pass_composite(
                input_video,
                output,
                resolution=resolution,
                fps=fps,
                bitrate=bitrate,
                grading_filters=grading_filters,
                ass_path=ass_path,
                fonts_dir=fonts_dir,
                logo_config=logo_config,
                sticker_overlays=sticker_overlays,
                audio_lufs=audio_lufs,
                audio_peak=audio_peak,
                normalize_audio=normalize_audio,
                profile=profile,
                allow_hw=False,
            )
        raise RuntimeError(
            f"单遍合成渲染失败 (code={proc.returncode}): {(proc.stderr or '')[-800:]}"
        )
    return output
