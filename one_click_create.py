#!/usr/bin/env python3
"""
可灵 AI 抖音广告视频 - 一键成片

使用方法：
    python one_click_create.py

功能：
    输入产品信息，自动完成：
    1. 生成角色定妆照（调用可灵图片 API）
    2. 生成 5 个分镜片段（调用可灵视频 API）
    3. 自动拼接 + 转场 + 字幕 + BGM（ffmpeg）
    4. 输出最终成片

前置条件：
    - 已在 config.py 中配置 KLING_API_KEY
    - 已安装 ffmpeg（brew install ffmpeg）
    - 已安装依赖：pip install requests
"""

import sys
import json
import time
import base64
import argparse
import subprocess
import math
import shutil
import hashlib
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime
from threading import Lock
from typing import Optional, List, Dict, Tuple
from urllib.parse import urlparse

from config import (
    PROJECT_ROOT,
    OUTPUT_DIR,
    KLING_API_KEY,
    KLING_PRICING,
    DEFAULT_VIDEO_DURATION,
    DEFAULT_ASPECT_RATIO,
    DEFAULT_MODE,
    DEFAULT_IMAGE_FIDELITY,
    DEFAULT_HUMAN_FIDELITY,
    KLING_IMAGE_MODEL,
    KLING_VIDEO_MODEL,
    DEFAULT_TRANSITIONS,
    DEFAULT_SUBTITLE_TEMPLATE,
    BGM_PATH,
    BGM_VOLUME_VOICEOVER,
    CONSISTENCY_TEMPLATES,
    PRODUCT_PRESETS,
    CINEMATIC_STYLES,
    DEFAULT_CINEMATIC_STYLE,
    BRAND_CONFIG,
    HOOK_TEMPLATES,
    DEFAULT_HOOK_TYPE,
    SCENE_CONTINUITY_CONFIG,
    MAX_REF_IMAGES,
    NEGATIVE_PROMPT,
    get_preset,
    ensure_dirs,
)
from cinematic_language import (
    build_cinematic_prompt_elements,
    DEEP_CINEMATIC_STYLES,
)
from kling_client import KlingClient
# P0-1 修复：导入 JWT 鉴权所需的 Key，用于 main() 鉴权检查
try:
    from config import KLING_ACCESS_KEY, KLING_SECRET_KEY
except ImportError:
    KLING_ACCESS_KEY = ""
    KLING_SECRET_KEY = ""
from bgm_client import (
    pick_bgm_for_product,
    _detect_pace_from_clips,
    get_bgm_copyright_info,
    print_bgm_copyright_warning,
    BGM_COPYRIGHT_DISCLAIMER,
)
from douyin_adapter import (
    DOUYIN_CONFIG,
    get_douyin_config,
    optimize_subtitles_for_douyin,
    get_rhythm_template,
    compute_segment_timeline,
)
from compliance_checker import (
    check_script_compliance,
    print_compliance_report,
)
from ad_script import (
    generate_ad_script,
    script_to_clip_prompts,
    script_to_subtitles,
    script_to_voiceover,
    generate_title_options,
    generate_hashtag_options,
    SCRIPT_STYLES,
    DEFAULT_SCRIPT_STYLE,
)
from tts_client import (
    generate_voiceover_script,
    generate_full_voiceover,
    align_subtitles_to_voiceover,
    VOICEOVER_TEMPLATES,
    VOICE_PRESETS,
    DEFAULT_VOICEOVER_STYLE,
    DEFAULT_VOICE,
)
from video_merger import (
    merge_clips_ffmpeg,
    add_subtitles_ffmpeg,
    add_fancy_subtitles,
    add_bgm_ffmpeg,
    add_sfx_to_video,
    generate_sfx_timings,
    align_subtitles_to_beats,
    align_sfx_to_beats,
    apply_color_grading,
    get_color_grading_for_style,
    export_final_video,
    convert_to_aspect_ratio,
    check_ffmpeg,
    extract_last_frame,
    extract_keyframes,
    extract_frame,
    generate_cover_image,
    add_logo_watermark,
    auto_trim_black_frames,
    auto_trim_video,
    detect_freeze_frames,
    generate_fallback_audio,
    color_match_clips,
    run_ffmpeg,
    _get_clip_duration,
    adjust_clip_duration,
    generate_transition_sequence,
)
from quality_checker import (
    check_video_quality,
    print_quality_report,
)


def _safe_output_stem(value: str) -> str:
    """生成安全文件名前缀。"""
    return "".join(c for c in value if c.isalnum() or c in "-_").strip() or "product"


def build_stable_output_name(product_info: dict, args: argparse.Namespace) -> str:
    """
    基于产品信息和关键生成参数生成稳定输出名，用于 --resume 命中片段缓存。
    """
    relevant = {
        "product_info": product_info,
        "style": getattr(args, "style", DEFAULT_CINEMATIC_STYLE),
        "duration": getattr(args, "duration", DEFAULT_VIDEO_DURATION),
        "mode": getattr(args, "mode", DEFAULT_MODE),
        "aspect_ratio": getattr(args, "aspect_ratio", DEFAULT_ASPECT_RATIO),
        "product_image": str(getattr(args, "product_image", "") or ""),
        "hook": getattr(args, "hook", DEFAULT_HOOK_TYPE),
        "script_style": getattr(args, "script_style", DEFAULT_SCRIPT_STYLE),
        "target_duration": getattr(args, "target_duration", None),
        "rhythm_style": getattr(args, "rhythm_style", "moderate"),
        "seed": getattr(args, "seed", None),
        "kling_model": getattr(args, "kling_model", None) or KLING_VIDEO_MODEL,
        "multi_shot": getattr(args, "multi_shot", False),
    }
    digest = hashlib.sha256(
        json.dumps(relevant, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:12]
    return f"{_safe_output_stem(product_info.get('name', 'product'))}_{digest}"


def _hash_reference_images(ref_images: list[str]) -> list[str]:
    """把参考图内容压缩成短哈希，避免幂等键包含大段 base64。"""
    hashes = []
    for img in ref_images:
        hashes.append(hashlib.sha256(str(img).encode("utf-8")).hexdigest()[:16])
    return hashes


def _ref_image_values(ref_images: list) -> list[str]:
    """兼容旧字符串列表和带 role 的参考图条目。"""
    values = []
    for item in ref_images:
        if isinstance(item, dict):
            img = item.get("image", "")
        else:
            img = item
        if img:
            values.append(img)
    return values


def _clip_manifest_path(video_path: Path) -> Path:
    """返回片段对应的 manifest 路径。"""
    return video_path.with_name(f"{video_path.stem}.manifest.json")


def _build_clip_manifest(
    *,
    final_prompt: str,
    ref_images: list,
    idx: int,
    model: Optional[str],
    mode: str,
    duration: int,
    aspect_ratio: str,
    seed: Optional[int],
    negative_prompt: str,
) -> dict:
    """构建片段缓存契约，只有契约一致才能复用旧片段。"""
    ref_values = _ref_image_values(ref_images)
    ref_roles = [
        item.get("role", "unknown") if isinstance(item, dict) else "unknown"
        for item in ref_images
    ]
    return {
        "version": 1,
        "clip_index": idx,
        "prompt_sha256": hashlib.sha256(final_prompt.encode("utf-8")).hexdigest(),
        "reference_hashes": _hash_reference_images(ref_values),
        "reference_roles": ref_roles,
        "model": model,
        "mode": mode,
        "duration": duration,
        "aspect_ratio": aspect_ratio,
        "seed": seed,
        "negative_prompt_sha256": hashlib.sha256(negative_prompt.encode("utf-8")).hexdigest(),
    }


def _manifest_matches(video_path: Path, expected_manifest: dict) -> bool:
    """检查片段 manifest 是否与当前生成契约一致。"""
    manifest_path = _clip_manifest_path(video_path)
    if not manifest_path.exists():
        return False
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            actual = json.load(f)
    except Exception:
        return False
    return actual == expected_manifest


def _write_clip_manifest(video_path: Path, manifest: dict) -> None:
    """写入片段 manifest。"""
    manifest_path = _clip_manifest_path(video_path)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2, sort_keys=True)


PRODUCT_REQUIRED_NARRATIVES = {
    "showcase", "cta", "demonstration", "product", "result",
    "review", "proof", "demo", "detail", "reason", "effect",
    "highlight", "solution", "good_choice", "product_intro",
    "effect_show", "compare_result", "reason_1", "reason_2",
    "reason_3", "cta_summary", "cta_choose",
}


def _is_product_required_narrative(narrative: str) -> bool:
    """判断某个叙事段是否必须强约束产品露出。"""
    normalized = (narrative or "").lower().strip()
    return normalized in PRODUCT_REQUIRED_NARRATIVES


def _score_candidate_video_quality(
    video_path: Path,
    *,
    quality_frames: int,
    product_reference_image: Optional[Path] = None,
    character_reference_image: Optional[Path] = None,
) -> Tuple[float, List[str]]:
    """候选片段择优评分；语义门禁失败的候选不能被选中。"""
    quality_result = check_video_quality(
        video_path,
        num_frames=int(quality_frames or 12),
        content_focus="center" if product_reference_image else "default",
        product_reference_image=product_reference_image,
        character_reference_image=character_reference_image,
        require_semantic_alignment=bool(character_reference_image),
    )
    score = float(quality_result.overall_score or 0) if quality_result.passed else 0.0
    return score, list(quality_result.issues or [])


def _validate_product_image_file(image_path: Path) -> None:
    """发布级商品参考图预检，避免损坏/低质素材污染生成。"""
    from PIL import Image, ImageStat

    if not image_path.exists():
        raise FileNotFoundError(f"商品参考图不存在：{image_path}")
    if image_path.stat().st_size < 1024:
        raise RuntimeError(f"商品参考图文件过小，可能不是有效图片：{image_path}")

    try:
        with Image.open(image_path) as src:
            src.verify()
        with Image.open(image_path) as src:
            img = src.convert("RGBA")
    except Exception as e:
        raise RuntimeError(f"商品参考图不可读取或格式损坏：{image_path}") from e

    width, height = img.size
    min_side = min(width, height)
    if min_side < 256:
        raise RuntimeError(
            f"商品参考图分辨率过低（{width}x{height}），建议最短边至少 512px"
        )

    alpha = img.getchannel("A")
    alpha_stat = ImageStat.Stat(alpha)
    opaque_ratio = sum(1 for p in alpha.getdata() if p > 16) / max(1, width * height)
    if alpha_stat.mean[0] < 8 or opaque_ratio < 0.05:
        raise RuntimeError("商品参考图几乎全透明，无法约束产品露出")

    rgb = img.convert("RGB")
    stat = ImageStat.Stat(rgb)
    channel_std = sum(stat.stddev) / max(1, len(stat.stddev))
    brightness = sum(stat.mean) / max(1, len(stat.mean))
    if channel_std < 3:
        raise RuntimeError("商品参考图几乎是纯色/空白图，无法作为产品参考")
    if brightness < 8 or brightness > 247:
        raise RuntimeError("商品参考图整体过暗或过曝，无法稳定约束产品露出")


def _check_segment_semantic_quality(
    *,
    clip_paths: List[Path],
    successful_clip_indices: List[int],
    ad_script: dict,
    product_image_path: Optional[Path],
    main_char_path: Optional[Path],
    quality_frames: int,
) -> None:
    """按分镜检查关键段语义质量，避免整片抽帧漏掉产品/CTA 段问题。"""
    segments = ad_script.get("segments", []) if isinstance(ad_script, dict) else []
    issues = []

    for pos, clip_path in enumerate(clip_paths):
        if pos >= len(successful_clip_indices):
            continue
        seg_idx = successful_clip_indices[pos]
        seg = segments[seg_idx] if 0 <= seg_idx < len(segments) else {}
        narrative = str(seg.get("narrative") or seg.get("type") or "").lower().strip()
        product_ref = product_image_path if product_image_path and _is_product_required_narrative(narrative) else None
        character_ref = main_char_path if main_char_path and narrative in {"hook", "turning", "result", "review"} else None

        if not product_ref and not character_ref:
            continue

        result = check_video_quality(
            clip_path,
            num_frames=max(6, int(quality_frames or 12)),
            content_focus="center" if product_ref else "default",
            product_reference_image=product_ref,
            character_reference_image=character_ref,
            require_semantic_alignment=bool(character_ref),
        )
        if not result.passed:
            first_issue = result.issues[0] if result.issues else "未知质量问题"
            issues.append(
                f"段 {seg_idx + 1}（{narrative or 'unknown'}）未通过分段语义质检：{first_issue}"
            )

    if issues:
        detail = "；".join(issues[:3])
        if len(issues) > 3:
            detail += f"；另有 {len(issues) - 3} 个问题"
        raise RuntimeError(f"分段语义质检未通过，已阻断不可发布成片：{detail}")


def _build_character_manifest(
    *,
    product_info: dict,
    character: dict,
    prompt: str,
) -> dict:
    """构建角色定妆照缓存契约，避免同名输出复用旧人设。"""
    character_contract = {
        "name": character.get("name", "Character A"),
        "description": character.get("description", ""),
    }
    return {
        "version": 1,
        "product_info_sha256": hashlib.sha256(
            json.dumps(product_info, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest(),
        "character_sha256": hashlib.sha256(
            json.dumps(character_contract, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest(),
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "model": KLING_IMAGE_MODEL,
        "aspect_ratio": "2:3",
        "resolution": "2k",
    }


def _build_video_idempotency_key(
    prompt: str,
    ref_images: list,
    idx: int,
    target_path: Path,
    *,
    model: Optional[str],
    mode: str,
    duration: int,
    aspect_ratio: str,
    seed: Optional[int],
) -> str:
    """为单个候选视频生成稳定幂等键。"""
    payload = {
        "prompt": prompt,
        "refs": _hash_reference_images(_ref_image_values(ref_images)),
        "idx": idx,
        "target": target_path.name,
        "model": model,
        "mode": mode,
        "duration": duration,
        "aspect_ratio": aspect_ratio,
        "seed": seed,
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:24]
    return f"kaa-{digest}"


def _bind_reference_tags_to_prompt(prompt: str, ref_images: list, narrative: str = "") -> str:
    """
    用结构化段落绑定参考图语义，避免靠泛关键词把 tag 插错位置。
    """
    if not ref_images:
        return prompt
    lines = []
    for i, item in enumerate(ref_images):
        tag = f"<<<image_{i + 1}>>>"
        role = item.get("role", "unknown") if isinstance(item, dict) else "unknown"
        if role == "product":
            label = "Product reference"
            focus = "match packaging, shape, color, and logo placement"
        elif role == "character":
            label = "Character reference"
            focus = "match identity, face, hairstyle, outfit, and body proportion"
        else:
            label = "Continuity frame"
            focus = "match scene layout, camera angle, lighting, and temporal continuity"
        lines.append(f"{label}: {tag} ({focus}).")
    reference_block = "Reference image binding:\n" + "\n".join(lines)
    return f"{reference_block}\n\n{prompt}"


def cleanup_output(output_name: str, output_dir: Path = OUTPUT_DIR):
    """
    清理本次运行产生的所有中间文件（递归清理子目录）

    Args:
        output_name: 本次运行的输出文件名前缀
        output_dir: 输出根目录，默认 OUTPUT_DIR
    """
    dirs_to_clean = [
        output_dir / "character_ref",
        output_dir / "clips",
        output_dir / "final",
    ]

    cleaned = []
    for d in dirs_to_clean:
        if not d.exists():
            continue
        for f in d.rglob(f"*{output_name}*"):
            if f.is_file():
                try:
                    f.unlink()
                    cleaned.append(f.name)
                except Exception as e:
                    print(f"  ⚠️ 清理失败 {f.name}: {e}")
        # 清理空的关键帧子目录
        for subdir in d.iterdir():
            if subdir.is_dir() and output_name in subdir.name:
                try:
                    subdir.rmdir()
                except OSError:
                    pass

    if cleaned:
        print(f"🧹 已清理 {len(cleaned)} 个文件")



def _cleanup_intermediate_files(
    final_dir: Path,
    output_name: str,
    final_path: Path,
    wide_path: Optional[Path],
) -> None:
    """
    清理流水线产生的所有中间文件，只保留：
    - {output_name}_final.mp4（最终竖版成片）
    - {output_name}_16x9_final.mp4（横版，如有）
    - {output_name}_cover.jpg（封面图，如有）
    - {output_name}_发布文案.txt（发布文案）

    中间文件后缀：_merged / _subtitled / _voiced / _sfx /
                  _graded / _watermarked / _trimmed / _cover_base
    """
    if not final_dir.exists():
        return

    # 需要保留的文件名集合
    keep = set()
    keep.add(final_path.name)
    if wide_path:
        keep.add(wide_path.name)
    # 封面和文案也保留
    keep.add(f"{output_name}_cover.jpg")
    keep.add(f"{output_name}_发布文案.txt")

    # 中间文件特征后缀
    # _cover_c：封面帧候选临时图（格式 {name}_cover_c{i}_{ratio}.jpg）
    intermediate_suffixes = (
        "_merged", "_subtitled", "_voiced", "_sfx",
        "_graded", "_watermarked", "_trimmed", "_cover_base", "_cover_c",
        "_trimmed_pre",
    )

    removed = []
    for f in final_dir.iterdir():
        if not f.is_file():
            continue
        if f.name in keep:
            continue
        # 只清理本次 output_name 相关的中间文件
        stem = f.stem  # 不含扩展名
        if not stem.startswith(output_name):
            continue
        suffix_part = stem[len(output_name):]  # e.g. "_merged", "_graded"
        if any(suffix_part.startswith(s) for s in intermediate_suffixes):
            try:
                f.unlink()
                removed.append(f.name)
            except Exception as e:
                print(f"  ⚠️ 清理中间文件失败 {f.name}: {e}")

    if removed:
        print(f"🧹 已清理 {len(removed)} 个中间文件")



def _quick_quality_check(
    clip_path: Path,
    expected_duration: float,
    idx: int,
    min_size_kb: int = 100,
    max_black_ratio: float = 0.75,
    duration_tolerance: float = 0.4,
) -> Optional[str]:
    """
    对单个片段做轻量质检，发现问题时返回问题描述字符串，正常返回 None。

    检查项：
    1. 文件大小：< min_size_kb KB 视为空文件/损坏
    2. 时长偏差：实际时长与 expected_duration 偏差超过 duration_tolerance 秒
    3. 黑帧比例：超过 max_black_ratio 视为全黑片段

    Args:
        clip_path: 片段文件路径
        expected_duration: 期望时长（秒）
        idx: 片段索引（用于日志）
        min_size_kb: 最小有效文件大小（KB）
        max_black_ratio: 允许的最大黑帧比例
        duration_tolerance: 时长容差（秒）

    Returns:
        问题描述字符串 or None（正常）
    """
    import shutil as _shutil

    # 1. 文件大小检查
    if not clip_path.exists():
        return "文件不存在"
    size_kb = clip_path.stat().st_size / 1024
    if size_kb < min_size_kb:
        return f"文件过小（{size_kb:.0f} KB < {min_size_kb} KB），可能损坏"

    # ffprobe 不可用则跳过时长/黑帧检查
    if not _shutil.which("ffprobe"):
        return None

    # 2. 时长检查
    actual_dur = expected_duration  # 备用：作为黑帧比例的分母
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(clip_path),
            ],
            capture_output=True, text=True, timeout=15,
        )
        actual_dur = float(result.stdout.strip())
        if abs(actual_dur - expected_duration) > duration_tolerance:
            return f"时长异常（期望 {expected_duration}s，实际 {actual_dur:.1f}s）"
    except Exception:
        pass  # ffprobe 失败不阻断流程

    # 3. 黑帧比例检查（用 blackdetect 滤镜）
    # P2-2：复用上方已查询的时长，无需再调一次 ffprobe
    try:
        total_dur = actual_dur

        bd_result = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-nostats", "-loglevel", "info",
                "-i", str(clip_path),
                # P2-1：修正参数名 pic_th → pix_th，阈值与 video_merger.py 保持一致
                "-vf", "blackdetect=d=0.1:pix_th=0.10",
                "-an", "-f", "null", "-",
            ],
            capture_output=True, text=True, timeout=30,
        )
        # 统计黑帧总时长
        import re as _re
        black_durs = _re.findall(r"black_duration:([\d.]+)", bd_result.stderr)
        total_black = sum(float(d) for d in black_durs)
        if total_dur > 0 and total_black / total_dur > max_black_ratio:
            return f"黑帧过多（{total_black:.1f}s / {total_dur:.1f}s = {total_black/total_dur:.0%}）"
    except Exception:
        pass  # 黑帧检查失败不阻断

    # F1 修复：Laplacian 方差清晰度检测（5 帧均值 < 50 视为模糊/闪烁片段）
    # 可灵 API 偶发返回低频闪、运动模糊片段，拉普拉斯方差是最轻量的清晰度指标
    try:
        _lap_scores = []
        _sample_count = 5
        for _fi in range(_sample_count):
            _ft = actual_dur * (_fi + 1) / (_sample_count + 1)  # 均匀采样，避免首尾帧
            _frame_cmd = [
                "ffmpeg", "-ss", f"{_ft:.2f}", "-i", str(clip_path),
                "-frames:v", "1", "-f", "rawvideo",
                "-pix_fmt", "gray", "-vf", "scale=320:180",
                "-",
            ]
            _fr = subprocess.run(_frame_cmd, capture_output=True, timeout=10)
            if _fr.returncode == 0 and _fr.stdout:
                import array as _arr, math as _math
                _pixels = _arr.array("B", _fr.stdout)
                _n = len(_pixels)
                if _n > 4:
                    # 近似 Laplacian：差分代替卷积，速度更快
                    _mean = sum(_pixels) / _n
                    _variance = sum((p - _mean) ** 2 for p in _pixels) / _n
                    _lap_scores.append(_variance)
        if _lap_scores:
            _avg_lap = sum(_lap_scores) / len(_lap_scores)
            if _avg_lap < 50.0:  # 经验阈值：<50 为明显模糊/低质量
                return f"清晰度不足（Laplacian 均值 {_avg_lap:.1f} < 50，可能模糊或低频闪）"
    except Exception:
        pass  # 清晰度检测失败不阻断流程

    # P0-A：人脸变形语义检测（复用 quality_checker._analyze_face_quality）
    # 抽取首/中/尾 3 帧，≥2 帧检测到肤色形态异常则视为坏片段
    try:
        import tempfile as _tmpfile
        from quality_checker import _analyze_face_quality as _face_check
        _face_issue_count = 0
        _face_sample_times = [
            actual_dur * 0.15,
            actual_dur * 0.50,
            actual_dur * 0.85,
        ]
        with _tmpfile.TemporaryDirectory(prefix="face_qc_") as _fqc_dir:
            for _fqi, _fqt in enumerate(_face_sample_times):
                _fq_frame = Path(_fqc_dir) / f"face_{_fqi}.png"
                _fq_cmd = [
                    "ffmpeg", "-y",
                    "-ss", f"{_fqt:.2f}", "-i", str(clip_path),
                    "-frames:v", "1", "-q:v", "3",
                    str(_fq_frame),
                ]
                _fqr = subprocess.run(_fq_cmd, capture_output=True, timeout=10)
                if _fqr.returncode == 0 and _fq_frame.exists():
                    _fq_issues = _face_check(_fq_frame)
                    if _fq_issues:
                        _face_issue_count += 1
        if _face_issue_count >= 2:
            return f"人脸变形检测：{_face_issue_count}/3 帧异常（肤色区域形状/填充率超限），疑似人脸崩坏"
    except Exception:
        pass  # 人脸检测失败不阻断流程

    return None


def _cleanup_keyframes(clips_dir: Path, output_name: str):


    """
    清理片段生成过程中产生的关键帧临时文件和子目录

    Args:
        clips_dir: 片段目录
        output_name: 输出文件名前缀
    """
    if not clips_dir.exists():
        return
    removed = 0
    # 清理关键帧子目录和其中的文件
    for subdir in list(clips_dir.iterdir()):
        if subdir.is_dir() and output_name in subdir.name:
            for f in subdir.glob("*.png"):
                try:
                    f.unlink()
                    removed += 1
                except Exception:
                    pass
            try:
                subdir.rmdir()
            except OSError:
                pass
    # 清理 last_frame.png 等零散临时 PNG
    for f in clips_dir.glob(f"*_{output_name}_*.png"):
        try:
            f.unlink()
            removed += 1
        except Exception:
            pass
    if removed:
        print(f"🧹 已清理 {removed} 个关键帧临时文件")


def _extract_frame_b64(video_path: Path, time_sec: float) -> str | None:
    """
    从视频提取指定时间的帧，返回 base64 编码

    Args:
        video_path: 视频路径
        time_sec: 时间点（秒）

    Returns:
        base64 编码的 PNG，失败返回 None
    """
    import tempfile
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        extract_frame(video_path, tmp_path, time_sec=time_sec)
        b64 = base64.b64encode(tmp_path.read_bytes()).decode("utf-8")
        return f"data:image/png;base64,{b64}"
    except Exception:
        return None
    finally:
        if tmp_path and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def _is_http_url(value: object) -> bool:
    """判断参数是否为 HTTP/HTTPS URL。"""
    parsed = urlparse(str(value))
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _download_product_image_url(url: str, output_dir: Path) -> Path:
    """下载商品参考图 URL，并校验其为可读取图片。"""
    from PIL import Image

    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
        suffix = ".jpg"
    out_path = output_dir / f"product_reference_url{suffix}"
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")

    try:
        resp = requests.get(url, stream=True, timeout=30)
        resp.raise_for_status()
        with tmp_path.open("wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        if tmp_path.stat().st_size < 1024:
            raise RuntimeError("商品参考图下载结果过小，可能不是有效图片")
        with Image.open(tmp_path) as img:
            img.verify()
        tmp_path.replace(out_path)
        return out_path
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _validate_voiceover_audio(audio_path: Path) -> float:
    """校验口播音频可用，并返回时长。"""
    if not audio_path.exists():
        raise RuntimeError(f"口播文件不存在：{audio_path}")
    size = audio_path.stat().st_size
    if size < 1024:
        raise RuntimeError(f"口播文件过小（{size} bytes）")
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "a:0",
            "-show_entries", "stream=codec_type:format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if probe.returncode != 0 or "audio" not in probe.stdout:
        raise RuntimeError(f"口播文件不可解码：{audio_path}")
    durations = []
    for line in probe.stdout.splitlines():
        try:
            durations.append(float(line.strip()))
        except ValueError:
            pass
    duration = max(durations) if durations else 0.0
    if duration < 0.5:
        raise RuntimeError(f"口播时长异常（{duration:.2f}s）")
    return duration


def _build_scene_anchor(
    anchor_clip: Path,
    output_dir: Path,
    num_keyframes: int = 2,
) -> list[str]:
    """
    从锚点片段提取场景锚点关键帧（用于全局场景一致性）

    Args:
        anchor_clip: 锚点视频片段
        output_dir: 输出目录（用于临时文件）
        num_keyframes: 提取关键帧数量

    Returns:
        base64 编码的参考图列表
    """
    anchor_frames = []
    try:
        keyframes = extract_keyframes(
            anchor_clip,
            output_dir / "scene_anchor",
            count=num_keyframes,
        )
        for kf in keyframes:
            b64 = base64.b64encode(kf.read_bytes()).decode("utf-8")
            anchor_frames.append(f"data:image/png;base64,{b64}")
    except Exception as e:
        print(f"  ⚠️ 提取场景锚点失败：{e}")
        # fallback：提取中间帧
        mid_frame = _extract_frame_b64(anchor_clip, time_sec=2.0)
        if mid_frame:
            anchor_frames.append(mid_frame)
    return anchor_frames


def _inject_scene_consistency_prompt(
    base_prompt: str,
    is_first_clip: bool = False,
) -> str:
    """
    向 Prompt 中注入场景一致性描述

    注意：一致性描述放在 Prompt 开头，扩散模型对开头注意力最强。

    Args:
        base_prompt: 原始 Prompt
        is_first_clip: 是否是第一个片段

    Returns:
        增强后的 Prompt
    """
    scene_desc = CONSISTENCY_TEMPLATES["scene"]
    lighting_desc = CONSISTENCY_TEMPLATES["lighting"]

    if is_first_clip:
        # 第一个片段不需要"与前一段连续"的描述
        consistency = f"{lighting_desc}"
    else:
        consistency = f"{scene_desc}, {lighting_desc}, continuous shot"

    # 把一致性描述插入到 Prompt 开头（扩散模型注意力前重后轻）
    return f"{consistency}, {base_prompt}"


def input_with_default(prompt: str, default: str = "") -> str:
    """带默认值的输入"""
    if default:
        user_input = input(f"{prompt} [{default}]：").strip()
        return user_input if user_input else default
    return input(f"{prompt}：").strip()


def estimate_cost(
    mode: str = "std",
    duration_per_clip: int = 5,
    num_clips: int = 5,
    num_characters: int = 1,
    ab_versions: int = 1,
    best_of: int = 1,
) -> dict:
    """
    估算本次生成的 API 成本（仅供参考）

    Args:
        mode: 生成模式（std/pro/4k）
        duration_per_clip: 单片段时长（秒）
        num_clips: 片段数量
        num_characters: 角色数量（定妆照数量）
        ab_versions: A/B 版本数

    Returns:
        {
            "image_count": 图片生成次数,
            "video_seconds": 视频生成总秒数,
            "estimated_cost": 预估费用（元）,
            "breakdown": 明细列表,
        }
    """
    pricing = KLING_PRICING
    img_price = pricing["image"].get(mode, 0.05)
    vid_price = pricing["video"].get(mode, 0.30)

    best_of = max(1, int(best_of or 1))

    # 每个版本：角色定妆照 + 视频片段
    image_per_version = num_characters  # 角色定妆照
    video_seconds_per_version = duration_per_clip * num_clips * best_of

    total_images = image_per_version * ab_versions
    total_video_seconds = video_seconds_per_version * ab_versions

    image_cost = total_images * img_price
    video_cost = total_video_seconds * vid_price
    total_cost = image_cost + video_cost

    breakdown = [
        f"角色定妆照：{total_images} 张 × {img_price:.2f} 元 = {image_cost:.2f} 元",
        f"视频片段：{total_video_seconds:.0f} 秒 × {vid_price:.2f} 元/秒 = {video_cost:.2f} 元",
    ]

    if best_of > 1:
        breakdown.append(f"best-of 候选：每段 {best_of} 个候选（视频成本已按倍数计入）")
    if ab_versions > 1:
        breakdown.append(f"A/B 版本：{ab_versions} 个版本")

    return {
        "image_count": total_images,
        "video_seconds": total_video_seconds,
        "estimated_cost": round(total_cost, 2),
        "breakdown": breakdown,
    }


def print_cost_estimate(cost_info: dict):
    """打印成本估算"""
    print()
    print("💰 成本估算（仅供参考，以实际账单为准）")
    print("-" * 40)
    for line in cost_info["breakdown"]:
        print(f"  {line}")
    print("-" * 40)
    print(f"  预估总费用：约 {cost_info['estimated_cost']:.2f} 元")
    print()


def estimate_file_size(
    duration: float,
    bitrate: str = "6M",
    audio_bitrate: str = "160k",
) -> dict:
    """
    估算最终视频文件大小

    Args:
        duration: 视频时长（秒）
        bitrate: 视频码率（如 "6M"、"4M"）
        audio_bitrate: 音频码率（如 "160k"）

    Returns:
        {
            "video_size_mb": 视频部分大小（MB）,
            "audio_size_mb": 音频部分大小（MB）,
            "total_size_mb": 总大小（MB）,
            "warning": 预警信息（如果太大）,
        }
    """
    # 解析码率
    def _parse_bitrate(br_str: str) -> float:
        """解析码率字符串为 bps"""
        br_str = br_str.strip().upper()
        if br_str.endswith("M"):
            return float(br_str[:-1]) * 1000 * 1000
        elif br_str.endswith("K"):
            return float(br_str[:-1]) * 1000
        else:
            return float(br_str)

    video_bps = _parse_bitrate(bitrate)
    audio_bps = _parse_bitrate(audio_bitrate)

    # 文件大小 = 码率 × 时长 / 8（bit -> byte）
    video_bytes = video_bps * duration / 8
    audio_bytes = audio_bps * duration / 8
    total_bytes = video_bytes + audio_bytes

    # 加上容器开销（约 5-10%）
    total_bytes *= 1.08

    video_mb = video_bytes / (1024 * 1024)
    audio_mb = audio_bytes / (1024 * 1024)
    total_mb = total_bytes / (1024 * 1024)

    # 预警
    warning = ""
    if total_mb > 200:
        warning = f"文件较大（约 {total_mb:.0f} MB），上传可能较慢"
    elif total_mb > 100:
        warning = f"文件偏大（约 {total_mb:.0f} MB）"

    return {
        "video_size_mb": round(video_mb, 1),
        "audio_size_mb": round(audio_mb, 1),
        "total_size_mb": round(total_mb, 1),
        "warning": warning,
    }


def calc_duration_for_target(target_duration: int) -> tuple:
    """
    根据目标总时长，计算合适的片段数和单片段时长

    Args:
        target_duration: 目标总时长（秒）

    Returns:
        (num_segments, duration_per_clip, script_style_note)
    """
    # 转场总时长（估算：每个转场 0.5s）
    transition_total = lambda n: (n - 1) * 0.5

    # 预设：不同总时长对应的片段数和单段时长
    presets = {
        7:  (3, 3, "极短钩子版（3段）"),     # 3*3 - 2*0.5 = 8s，接近7s
        15: (5, 3.5, "15秒经典版（5段）"),   # 5*3.5 - 4*0.5 = 15.5s
        30: (5, 7, "30秒深度版（5段）"),     # 5*7 - 4*0.5 = 33s，稍长但可接受
        60: (7, 10, "60秒详细版（7段）"),    # 7*10 - 6*0.5 = 67s，稍长
    }

    if target_duration in presets:
        num_segs, dur, note = presets[target_duration]
        return num_segs, dur, note

    # 通用计算：默认 5 段，倒推单段时长
    num_segs = 5
    per_clip = max(2, (target_duration + transition_total(num_segs)) / num_segs)
    return num_segs, round(per_clip, 1), f"自定义 {target_duration}s（5段）"


def generate_character_prompt(product_info: dict) -> str:
    """生成角色定妆照 Prompt（主角色）"""
    preset = get_preset(product_info.get("type", "default"))
    age = product_info.get("age", "25")
    gender = product_info.get("gender", "女")
    style = product_info.get("style", preset["style"])
    outfit = product_info.get("outfit", "casual everyday clothes")
    brand = BRAND_CONFIG.get("name", "brand")

    prompt = (
        f"Character reference portrait for {product_info.get('name', 'product')} advertisement, "
        f"{age}-year-old {gender}, {style} style, "
        f"wearing {outfit}, "
        f"{preset['scene']}, "
        f"{preset['lighting']}, "
        f"half-body composition, high detail, clear facial features, "
        f"front-facing, neutral expression, 9:16 vertical, "
        f"{brand} brand aesthetic, {BRAND_CONFIG.get('primary_color', 'consistent brand colors')}"
    )
    return prompt


def generate_character_prompt_for_role(product_info: dict, description: str = "") -> str:
    """
    生成指定角色的定妆照 Prompt

    Args:
        product_info: 产品信息字典
        description: 角色外貌描述（如 "25-year-old Asian woman, long black hair"）

    Returns:
        Prompt 字符串
    """
    preset = get_preset(product_info.get("type", "default"))
    brand = BRAND_CONFIG.get("name", "brand")
    name = product_info.get("name", "product")

    # 如果没有提供描述，使用主角色默认
    if not description:
        return generate_character_prompt(product_info)

    prompt = (
        f"Character reference portrait for {name} advertisement, "
        f"{description}, "
        f"{preset['scene']}, "
        f"{preset['lighting']}, "
        f"half-body composition, high detail, clear facial features, "
        f"front-facing, neutral expression, 9:16 vertical, "
        f"{brand} brand aesthetic, {BRAND_CONFIG.get('primary_color', 'consistent brand colors')}"
    )
    return prompt


def _mix_voiceover_with_bgm(
    video: Path,
    voiceover: Path,
    output: Path,
    bgm_ducking_volume: float = 0.3,
) -> Path:
    """
    将口播与视频中的 BGM 混合（#16 修复：真正的 sidechain ducking）

    使用 FFmpeg sidechaincompress：
    - 人声作为 sidechain 信号触发 BGM 压缩
    - 有人声时 BGM 自动压低约 10dB，无人声时自动恢复
    - 比固定音量降低更自然、更专业

    Args:
        video: 带 BGM 的视频
        voiceover: 口播音频
        output: 输出视频
        bgm_ducking_volume: BGM 基础音量比例（sidechain 压缩在此基础上额外压低）

    Returns:
        输出文件路径
    """
    # P1-6 修复：检查视频是否有音轨；无音轨时直接叠加口播，跳过 sidechain
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error",
             "-select_streams", "a",
             "-show_entries", "stream=codec_type",
             "-of", "default=noprint_wrappers=1:nokey=1",
             str(video)],
            capture_output=True, text=True, timeout=10,
        )
        has_audio = probe.stdout.strip() != ""
    except Exception:
        has_audio = True  # 探测失败时保守地假设有音轨

    if not has_audio:
        # 无音轨：直接将口播合入，无需 sidechain
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video),
            "-i", str(voiceover),
            "-map", "0:v",
            "-map", "1:a",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            str(output),
        ]
        run_ffmpeg(cmd, timeout=120)
        return output

    # #16 修复：sidechain ducking
    # [0:a] = 视频原音轨（BGM），[1:a] = 口播音频
    # sidechaincompress: 人声触发时 BGM 压低约 8dB（ratio=3, threshold≈-26dBFS）
    # attack=10ms 响应快，release=300ms 释放更平滑自然
    filter_complex = (
        f"[0:a]volume={bgm_ducking_volume}[bgm_pre];"
        f"[1:a]volume=1.0[voice];"
        f"[bgm_pre][voice]sidechaincompress="
        f"threshold=0.05:ratio=3:attack=10:release=300:makeup=1[bgm_duck];"  # #3 修复：ratio 3，避免人声一出 BGM 就断
        f"[bgm_duck][voice]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]"
    )


    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(video),
        "-i", str(voiceover),
        "-filter_complex", filter_complex,
        "-map", "0:v",
        "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        str(output),
    ]

    run_ffmpeg(cmd, timeout=120)
    return output


def apply_cinematic_style(base_prompt: str, style_key: str, clip_type: str, narrative: str = "hook") -> str:
    """
    将电影风格注入基础 Prompt

    深度电影感版本：当风格在 DEEP_CINEMATIC_STYLES 中时，
    使用完整的镜头语言元素（景别、运镜、角度、光影、构图、景深、胶片、色彩）
    按优化后的结构重组 Prompt。

    Args:
        base_prompt: 基础 Prompt
        style_key: 电影风格键值（如 hitchcock/kubrick）
        clip_type: 片段类型（push/pull/orbit/match/light），向后兼容
        narrative: 叙事功能（hook/turning_point/showcase/result/cta），用于分镜节奏

    Returns:
        注入电影风格后的完整 Prompt
    """
    if style_key == DEFAULT_CINEMATIC_STYLE:
        return base_prompt

    # 优先使用深度风格库
    deep_style = DEEP_CINEMATIC_STYLES.get(style_key)
    if deep_style:
        elements = build_cinematic_prompt_elements(style_key, narrative)
        style_name = deep_style.get("name_en", "")

        # Bug 1 fix: static 运镜时跳过 signature_camera，两者语义矛盾会让 AI 混乱
        # （turning_point / cta 段运镜是 locked-off static，signature 是动态描述）
        is_static_shot = "static" in elements.get("camera_movement", "").lower()
        signature_camera = (
            "" if is_static_shot
            else _get_signature_camera_move(deep_style, clip_type)
        )

        # 从 base_prompt 中提取核心主体内容（剥离镜头描述和光影描述）
        subject_content = _extract_subject_from_prompt(base_prompt)

        # #2 修复：showcase/cta 段必须以产品内容为主，电影风格只作轻量装饰
        # 其他叙事段（hook/turning_point/result）维持深度电影感顺序不变
        _product_first_narratives = {"showcase", "cta"}

        if narrative in _product_first_narratives:
            # 产品内容优先：主体 → 镜头描述（轻量）→ 光影 → 色调
            all_parts = []
            if subject_content:
                all_parts.append(subject_content)
            # 只注入景别+运镜，去掉 visual_key/film_look/composition 等纯氛围词
            camera_parts = []
            if style_name:
                camera_parts.append(f"{style_name}-style")
            if elements["shot_size"]:
                camera_parts.append(elements["shot_size"])
            if elements["camera_movement"]:
                camera_parts.append(elements["camera_movement"])
            if signature_camera:
                camera_parts.append(signature_camera)
            if camera_parts:
                all_parts.append(", ".join(camera_parts))
            if elements["lighting"]:
                all_parts.append(elements["lighting"])
            if elements["color_grade"]:
                all_parts.append(elements["color_grade"])
        else:
            # 原有深度电影感顺序（hook/turning_point/result）
            all_parts = []
            # 0. visual_key 整体视觉基调前置
            if elements.get("visual_key"):
                all_parts.append(elements["visual_key"])
            # 1. 风格标识 + 景别 + 运镜 + 角度
            camera_parts = []
            if style_name:
                camera_parts.append(f"{style_name}-style")
            if elements["shot_size"]:
                camera_parts.append(elements["shot_size"])
            if elements["camera_movement"]:
                camera_parts.append(elements["camera_movement"])
            if signature_camera:
                camera_parts.append(signature_camera)
            if elements["camera_angle"]:
                camera_parts.append(elements["camera_angle"])
            if camera_parts:
                all_parts.append(", ".join(camera_parts))
            # 2. 主体 + 动作 + 场景
            if subject_content:
                all_parts.append(subject_content)
            # 3. 光影
            if elements["lighting"]:
                all_parts.append(elements["lighting"])
            # 4. 构图 + 景深
            comp_dof = []
            if elements["composition"]:
                comp_dof.append(elements["composition"])
            if elements["dof"]:
                comp_dof.append(elements["dof"])
            if comp_dof:
                all_parts.append(", ".join(comp_dof))
            # 5. 胶片质感
            if elements["film_look"]:
                all_parts.append(elements["film_look"])
            # 6. 色彩调性
            if elements["color_grade"]:
                all_parts.append(elements["color_grade"])

        # 全文去重（去除重复的短语，如 shallow depth of field 出现两次）
        result = ", ".join(all_parts)
        result = _deduplicate_phrases(result)

        return result

    # 回退到旧版风格库（向后兼容）
    style = CINEMATIC_STYLES.get(style_key)
    if not style:
        return base_prompt

    cinematic_map = {
        "push": style.get("camera_push", ""),
        "pull": style.get("camera_pull", ""),
        "orbit": style.get("camera_orbit", ""),
        "match": style.get("transition_match", ""),
        "light": style.get("transition_light", ""),
    }

    cinematic_desc = cinematic_map.get(clip_type, "")
    if not cinematic_desc:
        return base_prompt

    return f"{cinematic_desc}, {base_prompt}"


def _extract_subject_from_prompt(prompt: str) -> str:
    """从 base_prompt 中提取主体动作和场景内容，去掉镜头描述和光影描述

    策略：找到第一个非镜头/非光影描述性的词开始截取。
    去掉常见的镜头关键词和光影关键词开头，保留剩余内容。

    Args:
        prompt: 原始 prompt 字符串

    Returns:
        提取后的主体内容字符串
    """
    # 常见的镜头描述前缀（需要去掉的）
    shot_prefixes = [
        "static shot", "slow push in", "slow pull back",
        "close-up shot", "close up", "extreme close-up",
        "medium shot", "long shot", "wide shot",
        "handheld", "tracking shot", "camera orbit around subject",
        "camera orbit around", "camera orbit",
        "slow push", "slow pull", "camera pushes", "camera pulls",
        "split screen", "cinematic framing",
        "glamour shot", "dynamic action shot",
        "macro shot", "medium close-up",
        "close-up on", "slow zoom", "static wide shot",
    ]

    # 常见的光影描述前缀（深度风格模式下需要去掉，避免与风格光影冲突）
    lighting_prefixes = [
        "natural lighting", "natural indoor lighting", "natural outdoor lighting",
        "soft lighting", "warm lighting", "cool lighting",
        "bright lighting", "dramatic lighting", "moody lighting",
        "studio lighting", "golden hour lighting", "golden hour",
        "soft natural light", "warm ambient light",
    ]

    result = prompt.strip()

    # 尝试去掉开头的镜头描述（最多去掉前 5 个匹配项）
    for _ in range(5):
        lowered = result.lower()
        matched = False
        for prefix in shot_prefixes:
            if lowered.startswith(prefix):
                result = result[len(prefix):].lstrip(" ,")
                matched = True
                break
        if not matched:
            break

    # 再去掉光影描述（可能穿插在中间，用逗号分隔的短语级去重）
    phrases = [p.strip() for p in result.split(",") if p.strip()]
    filtered_phrases = []
    for phrase in phrases:
        lowered = phrase.lower()
        is_lighting_desc = any(
            kw in lowered for kw in [
                "lighting", "light ", "light,", "lit ", "illuminated",
                "golden hour", "chiaroscuro", "neon", "backlit",
                "rim light", "key light", "fill light",
            ]
        )
        # 保留主体短语，去掉纯光影描述短语（长度 < 5 个词且含 lighting 关键词的视为纯光影描述）
        word_count = len(lowered.split())
        if is_lighting_desc and word_count <= 5:
            continue
        filtered_phrases.append(phrase)

    if filtered_phrases:
        result = ", ".join(filtered_phrases)

    return result if result else prompt


def _get_signature_camera_move(style: dict, clip_type: str) -> str:
    """从导演风格中提取标志性运镜特征词（简短版）

    用于在深度风格 Prompt 中保留导演的核心运镜辨识度，
    如希区柯克的 dolly zoom、库布里克的 one-point perspective 等。
    只返回真正独特的、不是通用运镜的特征词。

    Args:
        style: 导演风格字典（来自 DEEP_CINEMATIC_STYLES）
        clip_type: 片段类型（push/pull/orbit）

    Returns:
        标志性运镜特征词字符串（简短，2-6 个词），空串表示无独特特征
    """
    key_map = {
        "push": "camera_push",
        "pull": "camera_pull",
        "orbit": "camera_orbit",
    }
    desc_key = key_map.get(clip_type, "camera_push")
    full_desc = style.get(desc_key, "")

    if not full_desc:
        return ""

    # 通用运镜词（如果标志性特征只是这些，就跳过）
    generic_moves = {
        "push in", "push forward", "pull back", "pull out",
        "orbit", "tracking", "dolly in", "dolly out",
    }

    signature = ""
    # 策略：从冒号前的部分提取标志性特征（去掉导演名字）
    if ":" in full_desc:
        before_colon = full_desc.split(":", 1)[0].strip()
        style_name = style.get("name_en", "")
        if style_name and before_colon.lower().startswith(style_name.lower()):
            extracted = before_colon[len(style_name):].strip()
            if extracted and extracted.lower() not in generic_moves:
                signature = extracted
        elif before_colon and len(before_colon.split()) <= 6:
            if before_colon.lower() not in generic_moves:
                signature = before_colon

    # 如果没找到，从冒号后找第一个特征短语
    if not signature and ":" in full_desc:
        after_colon = full_desc.split(":", 1)[1].strip()
        if "," in after_colon:
            first_phrase = after_colon.split(",")[0].strip()
        else:
            first_phrase = after_colon.split(".")[0].strip()
        words = first_phrase.split()
        if len(words) > 6:
            first_phrase = " ".join(words[:6])
        # 检查是否是独特特征（不是通用运镜）
        if first_phrase and first_phrase.lower() not in generic_moves:
            signature = first_phrase

    # Bug 4 fix: 截断后若末尾是介词/冠词，说明句子不完整，向前裁到最近逗号或上一个完整词组
    _dangling_endings = {
        "to", "from", "with", "of", "in", "on", "at", "by", "for",
        "a", "an", "the", "and", "or", "as",
    }
    if signature:
        words = signature.split()
        while words and words[-1].lower().rstrip(",") in _dangling_endings:
            words.pop()
        signature = " ".join(words).rstrip(", ")

    # 最后过滤：如果 signature 太短（只有1个词且是通用词），跳过
    if len(signature.split()) <= 1 and signature.lower() in {
        "push", "pull", "orbit", "dolly", "track", "zoom",
    }:
        return ""

    return signature


def _deduplicate_phrases(text: str) -> str:
    """去除 Prompt 中重复的短语（不区分大小写）

    保留第一次出现的短语，移除后续重复项。
    按逗号分隔的短语级别去重。

    Args:
        text: 原始 Prompt 文本

    Returns:
        去重后的文本
    """
    phrases = [p.strip() for p in text.split(",")]
    seen = set()
    result = []

    for phrase in phrases:
        if not phrase:
            continue
        key = phrase.lower().strip()
        if key not in seen:
            seen.add(key)
            result.append(phrase)

    return ", ".join(result)


def generate_clip_prompts(
    product_info: dict,
    cinematic_style: str = DEFAULT_CINEMATIC_STYLE,
    clip_structure: Optional[list] = None,
    characters: Optional[list] = None,
    hook_type: str = DEFAULT_HOOK_TYPE,
) -> list:
    """
    生成分镜片段的 Prompts

    Args:
        product_info: 产品信息字典
        cinematic_style: 电影风格键值
        clip_structure: 分镜结构配置列表（可选，默认使用 config.CLIP_STRUCTURE）
        characters: 角色名称列表（用于 prompt 中固定指代角色）
        hook_type: 钩子类型（用于替换第一个分镜的 prompt）

    Returns:
        prompt 字符串列表
    """
    from config import CLIP_STRUCTURE

    preset = get_preset(product_info.get("type", "default"))
    name = product_info.get("name", "product")
    character = product_info.get("character", "same person from reference image")
    selling_point = product_info.get("selling_point", "amazing feature")
    brand = BRAND_CONFIG.get("name", "brand")
    brand_packaging = BRAND_CONFIG.get("packaging_description", "consistent packaging")
    brand_logo = BRAND_CONFIG.get("logo_description", "brand logo appears subtly")

    # 强一致性基础描述（所有片段共享）
    gender = product_info.get("gender", "person")
    primary_color = BRAND_CONFIG.get("primary_color", "consistent brand colors")
    character_consistency = CONSISTENCY_TEMPLATES["character"].format(gender=gender)
    product_consistency = CONSISTENCY_TEMPLATES["product"].format(
        name=name, brand_packaging=brand_packaging, brand=brand
    )
    brand_consistency = CONSISTENCY_TEMPLATES["brand"].format(
        brand=brand, primary_color=primary_color
    )

    # 多角色描述注入
    char_names = characters if characters else ["Character A"]
    if len(char_names) == 1:
        character_descriptions = f"{character}, {character_consistency}"
    else:
        # 多角色：为每个角色生成固定描述
        char_desc_parts = []
        for i, cname in enumerate(char_names, 1):
            char_desc_parts.append(f"{cname}: consistent appearance, same person from reference image")
        character_descriptions = ", ".join(char_desc_parts) + f", {character_consistency}"

    # 占位符替换上下文
    fmt_context = {
        "character": character,
        "name": name,
        "selling_point": selling_point,
        "preset_scene": preset["scene"],
        "preset_lighting": preset["lighting"],
        "demo_action": preset["demo_action"],
        "preset_result": preset["result"],
        "brand": brand,
        "brand_packaging": brand_packaging,
        "brand_logo": brand_logo,
        "character_consistency": character_descriptions,
        "product_consistency": product_consistency,
        "brand_consistency": brand_consistency,
        "product": name,
        "gender": product_info.get("gender", "person"),
    }

    # 构建基础 Prompts
    structure = clip_structure if clip_structure is not None else CLIP_STRUCTURE

    # 如果有钩子模板，替换第一个片段的 base_prompt
    hook_template = HOOK_TEMPLATES.get(hook_type)
    if hook_template and structure:
        structure = list(structure)  # 复制一份，避免修改原配置
        # #9 修复：同步写入 camera_type，不同 hook 类型触发不同镜头语言
        hook_camera = hook_template.get("camera_type", structure[0].get("camera", "push"))
        structure[0] = {
            **structure[0],
            "base_prompt": hook_template["hook_prompt"],
            "camera": hook_camera,
        }


    clips = []
    for clip_def in structure:
        base_prompt = clip_def["base_prompt"].format(**fmt_context)
        camera_type = clip_def["camera"]
        narrative = clip_def.get("narrative", "hook")
        clip_type = {
            "push": "push",
            "pull": "pull",
            "orbit": "orbit",
            # P1 修复：static 不应映射为 push，否则会向静止镜头段误射入 push-in 运镇描述
            "static": "static",
        }.get(camera_type, "push")
        final_prompt = apply_cinematic_style(base_prompt, cinematic_style, clip_type, narrative)
        clips.append(final_prompt)

    return clips


def generate_subtitles(
    product_info: dict,
    clip_duration: int = DEFAULT_VIDEO_DURATION,
    num_clips: int = 5,
    hook_type: str = DEFAULT_HOOK_TYPE,
    seg_indices: Optional[List[int]] = None,
) -> list:
    """
    生成字幕列表（模板化，旧版字幕生成器）

    .. deprecated::
        主流程已改用 ad_script.script_to_subtitles（支持 seg_indices 白名单）。
        此函数仅保留以兼容旧调用方，新代码不应再使用。

    Args:
        product_info: 产品信息字典
        clip_duration: 单片段时长（秒）
        num_clips: 片段数量
        hook_type: 钩子类型（用于替换第一个字幕）
        seg_indices: 实际成功的段索引白名单（0-based）。
            提供时按白名单过滤并按合并后顺序重新计算时间轴；
            None 表示不过滤（退化到旧行为）。

    Returns:
        字幕列表，每个元素包含 text/start/end
    """
    import warnings as _warnings
    _warnings.warn(
        "generate_subtitles 已废弃，请改用 ad_script.script_to_subtitles（支持 seg_indices 白名单）。",
        DeprecationWarning,
        stacklevel=2,
    )
    # 问题4：空列表保护——逻辑上不可能（success_count<2 已拦截），但防御性报错更清晰
    if seg_indices is not None and len(seg_indices) == 0:
        raise ValueError("seg_indices 不能为空列表，请传入有效的正整数段索引")

    selling_point = product_info.get("selling_point", "核心卖点")
    subtitles = []

    # 钩子字幕（第一个字幕）
    hook_template = HOOK_TEMPLATES.get(hook_type)
    hook_subtitle = hook_template.get("hook_subtitle", "你是不是也...？") if hook_template else "你是不是也...？"

    index_set = set(seg_indices) if seg_indices is not None else None
    pos_map = (
        {si: pos for pos, si in enumerate(sorted(seg_indices))}
        if seg_indices is not None
        else {}
    )

    for idx, tpl in enumerate(DEFAULT_SUBTITLE_TEMPLATE):
        seg_idx = tpl.get("segment", 0)
        if index_set is not None:
            if seg_idx not in index_set:
                continue
            pos = pos_map[seg_idx]
            seg_start = pos * clip_duration
        else:
            if seg_idx >= num_clips:
                continue
            seg_start = seg_idx * clip_duration
        text = tpl["text"].format(selling_point=selling_point)
        if idx == 0 and hook_template:
            text = hook_subtitle
        start = seg_start + clip_duration * tpl.get("ratio_start", 0)
        end = seg_start + clip_duration * tpl.get("ratio_end", 1.0)
        subtitles.append({"text": text, "start": start, "end": end})

    return subtitles


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="可灵 AI 抖音广告视频 - 一键成片",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
示例：
  python one_click_create.py
  python one_click_create.py --style hitchcock
  python one_click_create.py --style kubrick --duration 8
  python one_click_create.py --product-image product.png --seed 42

一致性控制：
  --product-image  商品参考图路径（展示类片段自动使用）
  --image-fidelity 参考图 fidelity [0,1]，默认 {DEFAULT_IMAGE_FIDELITY}
  --human-fidelity 人物 fidelity [0,1]，默认 {DEFAULT_HUMAN_FIDELITY}
  --seed           随机种子基准（各片段自动递增）
        """,
    )
    parser.add_argument(
        "--style",
        default=DEFAULT_CINEMATIC_STYLE,
        choices=list(CINEMATIC_STYLES.keys()) + [DEFAULT_CINEMATIC_STYLE],
        help=f"电影风格（默认：{DEFAULT_CINEMATIC_STYLE}）",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=DEFAULT_VIDEO_DURATION,
        help=f"单片段时长（秒，默认：{DEFAULT_VIDEO_DURATION}）",
    )
    parser.add_argument(
        "--mode",
        default=DEFAULT_MODE,
        choices=["std", "pro", "4k"],
        help=f"生成模式（默认：{DEFAULT_MODE}）",
    )
    parser.add_argument(
        "--aspect-ratio",
        default=DEFAULT_ASPECT_RATIO,
        help=f"画面比例（默认：{DEFAULT_ASPECT_RATIO}）",
    )
    parser.add_argument(
        "--save",
        metavar="TEMPLATE.json",
        help="将当前产品信息和参数保存为模板 JSON",
    )
    parser.add_argument(
        "--load",
        metavar="TEMPLATE.json",
        help="从模板 JSON 加载产品信息和参数，跳过交互输入",
    )
    parser.add_argument(
        "--output-name",
        default=None,
        help="指定输出名前缀；配合 --resume 可复用已生成片段",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="断点续跑：使用稳定输出名并复用已生成的角色图/片段候选",
    )
    parser.add_argument(
        "--list-styles",
        action="store_true",
        help="列出所有可用的电影风格卡片",
    )
    parser.add_argument(
        "--dual-output",
        action="store_true",
        help="同时生成 9:16 和 16:9 两个版本",
    )
    parser.add_argument(
        "--target-duration",
        type=int,
        default=None,
        choices=[10, 15, 20, 25, 30, 60],
        help="目标总时长（秒），自动适配节奏模板（推荐：15/20/25/30）",
    )
    parser.add_argument(
        "--rhythm-style",
        default="moderate",
        choices=["fast", "moderate", "cinematic"],
        help="节奏风格：fast（快节奏）/ moderate（标准）/ cinematic（电影感），默认 moderate",
    )
    parser.add_argument(
        "--product-image",
        metavar="PATH",
        default=None,
        help="商品参考图路径（展示类片段自动使用，提升商品一致性）",
    )
    parser.add_argument(
        "--allow-no-product-image",
        action="store_true",
        help="允许不提供商品参考图继续生成（会降低产品露出质检可靠性）",
    )
    parser.add_argument(
        "--image-fidelity",
        type=float,
        default=DEFAULT_IMAGE_FIDELITY,
        help=f"参考图 fidelity [0,1]，默认 {DEFAULT_IMAGE_FIDELITY}",
    )
    parser.add_argument(
        "--human-fidelity",
        type=float,
        default=DEFAULT_HUMAN_FIDELITY,
        help=f"人物 fidelity [0,1]，默认 {DEFAULT_HUMAN_FIDELITY}",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="随机种子基准（各片段自动递增，保证一致性又有差异）",
    )
    parser.add_argument(
        "--hook",
        default=DEFAULT_HOOK_TYPE,
        choices=list(HOOK_TEMPLATES.keys()),
        help=f"钩子类型（默认：{DEFAULT_HOOK_TYPE}）",
    )
    parser.add_argument(
        "--list-hooks",
        action="store_true",
        help="列出所有可用的钩子模板",
    )
    parser.add_argument(
        "--voiceover",
        action="store_true",
        help="启用 AI 口播配音",
    )
    parser.add_argument(
        "--voiceover-style",
        default=DEFAULT_VOICEOVER_STYLE,
        choices=list(VOICEOVER_TEMPLATES.keys()),
        help=f"口播风格（默认：{DEFAULT_VOICEOVER_STYLE}）",
    )
    parser.add_argument(
        "--voice",
        default=DEFAULT_VOICE,
        choices=list(VOICE_PRESETS.keys()),
        help=f"音色（默认：{DEFAULT_VOICE}）",
    )
    parser.add_argument(
        "--list-voices",
        action="store_true",
        help="列出所有可用的音色预设",
    )
    parser.add_argument(
        "--script-style",
        default=DEFAULT_SCRIPT_STYLE,
        choices=list(SCRIPT_STYLES.keys()),
        help=f"广告脚本风格（默认：{DEFAULT_SCRIPT_STYLE}）",
    )
    parser.add_argument(
        "--list-script-styles",
        action="store_true",
        help="列出所有可用的广告脚本风格",
    )
    parser.add_argument(
        "--ab-versions",
        type=int,
        default=1,
        help="生成 A/B 测试版本数量（1-3，默认 1）",
    )
    parser.add_argument(
        "--strict",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="严格模式：关键步骤失败时抛出异常而非静默降级（默认开启，可用 --no-strict 关闭）",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="强制跳过 high 风险合规检测拦截（critical 级别始终拦截）",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="禁用 LLM 文案生成，强制走模板模式（覆盖 config.LLM_ENABLED）",
    )
    parser.add_argument(
        "--preview", "-p",
        action="store_true",
        help="快速预览模式：仅生成第 1 段（std 模式），跳过后期，用于快速试错",
    )
    parser.add_argument(
        "--serial",
        action="store_true",
        help="强制串行生成（默认并行；串行时每段用上一段尾帧，极致一致性）",
    )
    parser.add_argument(
        "--min-clips",
        type=int,
        default=3,
        help="最少成功片段数，低于此数则终止（默认 3，即 60%%）",
    )
    parser.add_argument(
        "--best-of",
        type=int,
        default=2,
        help="每个分镜生成候选数量（best-of），自动择优（默认 2）",
    )
    parser.add_argument(
        "--quality-frames",
        type=int,
        default=12,
        help="best-of 择优时的抽帧数量（默认 12）",
    )
    parser.add_argument(
        "--keep-candidates",
        action="store_true",
        help="保留 best-of 未被选中的候选片段（默认删除）",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=4,
        help="并行生成时的最大线程数（默认 4）",
    )
    # P1-A：视频稳定化 + 去闪烁
    parser.add_argument(
        "--stabilize",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="启用视频稳定化 + 去闪烁（默认开启，可用 --no-stabilize 关闭）",
    )
    # P2-A：品牌开场/收尾动画
    parser.add_argument(
        "--brand-intro-outro",
        action="store_true",
        help="在成片首尾加入品牌开场（2s）和收尾动画（1.5s）",
    )
    # P2-B：A/B 测试维度
    parser.add_argument(
        "--ab-dim",
        type=str,
        default=None,
        choices=["hook", "style", "script"],
        help="A/B 测试维度（hook/style/script），与 --ab-versions 配合使用",
    )
    # P2-C：可灵 API 高级参数
    parser.add_argument(
        "--kling-model",
        type=str,
        default=None,
        help="指定可灵模型版本（如 kling-v2-master），默认使用 config 中的 KLING_VIDEO_MODEL",
    )
    parser.add_argument(
        "--multi-shot",
        action="store_true",
        help="启用可灵多镜头模式（intelligence 分镜），提升场景连贯性",
    )
    parser.add_argument(
        "--manual",
        action="store_true",
        help="手动模式：逐字段填写产品信息（默认为主题模式，输入一句话自动展开）",
    )
    return parser.parse_args()


def save_template(product_info: dict, args: argparse.Namespace, output_path: Path):
    """保存模板到 JSON 文件"""
    template = {
        "product_info": product_info,
        "args": {
            "style": args.style,
            "duration": args.duration,
            "mode": args.mode,
            "aspect_ratio": args.aspect_ratio,
            "dual_output": getattr(args, "dual_output", False),
            "image_fidelity": getattr(args, "image_fidelity", DEFAULT_IMAGE_FIDELITY),
            "human_fidelity": getattr(args, "human_fidelity", DEFAULT_HUMAN_FIDELITY),
            "seed": getattr(args, "seed", None),
            "product_image": str(args.product_image) if getattr(args, "product_image", None) else None,
            "allow_no_product_image": getattr(args, "allow_no_product_image", False),
            "hook_type": getattr(args, "hook", DEFAULT_HOOK_TYPE),
            "script_style": getattr(args, "script_style", DEFAULT_SCRIPT_STYLE),
            "use_voiceover": getattr(args, "voiceover", False),
            "voice": getattr(args, "voice", DEFAULT_VOICE),
            "rhythm_style": getattr(args, "rhythm_style", "moderate"),
            "target_duration": getattr(args, "target_duration", None),
            "preview": getattr(args, "preview", False),
            "parallel": not getattr(args, "serial", False),
            "min_clips": getattr(args, "min_clips", 3),
            "best_of": getattr(args, "best_of", 2),
            "quality_frames": getattr(args, "quality_frames", 12),
            "keep_candidates": getattr(args, "keep_candidates", False),
            "max_workers": getattr(args, "max_workers", 4),
            "stabilize": getattr(args, "stabilize", True),
            "brand_intro_outro": getattr(args, "brand_intro_outro", False),
            "kling_model": getattr(args, "kling_model", None),
            "multi_shot": getattr(args, "multi_shot", False),
            "strict_mode": getattr(args, "strict", True),
            "force": getattr(args, "force", False),
            "no_llm": getattr(args, "no_llm", False),
            "output_name": getattr(args, "output_name", None),
            "resume": getattr(args, "resume", False),
        },
        "created_at": datetime.now().isoformat(),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(template, f, ensure_ascii=False, indent=2)
    print(f"✅ 模板已保存：{output_path}")


def load_template(template_path: Path) -> tuple:
    """
    从 JSON 加载模板

    Returns:
        (product_info, args_dict)
    """
    with open(template_path, "r", encoding="utf-8") as f:
        template = json.load(f)

    product_info = template.get("product_info", {})
    args_dict = template.get("args", {})

    # 兼容旧模板（只有 4 个字段）
    args_dict.setdefault("dual_output", False)
    args_dict.setdefault("image_fidelity", DEFAULT_IMAGE_FIDELITY)
    args_dict.setdefault("human_fidelity", DEFAULT_HUMAN_FIDELITY)
    args_dict.setdefault("seed", None)
    args_dict.setdefault("product_image", None)
    args_dict.setdefault("allow_no_product_image", False)
    args_dict.setdefault("hook_type", DEFAULT_HOOK_TYPE)
    args_dict.setdefault("script_style", DEFAULT_SCRIPT_STYLE)
    args_dict.setdefault("use_voiceover", False)
    args_dict.setdefault("voice", DEFAULT_VOICE)
    args_dict.setdefault("rhythm_style", "moderate")
    args_dict.setdefault("target_duration", None)
    args_dict.setdefault("preview", False)
    args_dict.setdefault("parallel", True)
    args_dict.setdefault("min_clips", 3)
    args_dict.setdefault("best_of", 2)
    args_dict.setdefault("quality_frames", 12)
    args_dict.setdefault("keep_candidates", False)
    args_dict.setdefault("max_workers", 4)
    args_dict.setdefault("stabilize", True)
    args_dict.setdefault("brand_intro_outro", False)
    args_dict.setdefault("kling_model", None)
    args_dict.setdefault("multi_shot", False)
    args_dict.setdefault("strict_mode", True)
    args_dict.setdefault("force", False)
    args_dict.setdefault("no_llm", False)
    args_dict.setdefault("output_name", None)
    args_dict.setdefault("resume", False)

    return product_info, args_dict


def run_generation_pipeline(
    product_info: dict,
    style: str = DEFAULT_CINEMATIC_STYLE,
    duration: int = DEFAULT_VIDEO_DURATION,
    mode: str = DEFAULT_MODE,
    aspect_ratio: str = DEFAULT_ASPECT_RATIO,
    output_name: str = None,
    dual_output: bool = False,
    product_image: Optional[Path] = None,
    allow_no_product_image: bool = False,
    image_fidelity: float = DEFAULT_IMAGE_FIDELITY,
    human_fidelity: float = DEFAULT_HUMAN_FIDELITY,
    seed: Optional[int] = None,
    characters: Optional[list] = None,
    output_dir: Path = OUTPUT_DIR,
    hook_type: str = DEFAULT_HOOK_TYPE,
    use_voiceover: bool = False,
    voiceover_style: str = DEFAULT_VOICEOVER_STYLE,
    voice: str = DEFAULT_VOICE,
    script_style: str = DEFAULT_SCRIPT_STYLE,
    strict_mode: bool = True,
    force: bool = False,
    target_duration: Optional[int] = None,
    rhythm_style: str = "moderate",
    parallel: bool = True,
    min_clips: int = 3,
    best_of: int = 2,
    quality_frames: int = 12,
    keep_candidates: bool = False,
    preview: bool = False,
    max_workers: int = 4,
    stabilize: bool = True,
    brand_intro_outro: bool = False,
    kling_model: Optional[str] = None,
    multi_shot: bool = False,
) -> dict:
    """
    核心生成流水线（无交互逻辑）

    Args:
        product_info: 产品信息字典
        style: 电影风格键值
        duration: 单片段生成时长（秒，传给可灵 API 的基础值）
        mode: 生成模式（std/pro/4k）
        aspect_ratio: 画面比例
        output_name: 输出文件名前缀（可选，自动生成时间戳）
        dual_output: 是否同时生成 16:9 版本
        product_image: 商品参考图路径（可选，展示类片段将使用）
        image_fidelity: 参考图 fidelity [0,1]，默认 0.9
        human_fidelity: 人物 fidelity [0,1]，默认 0.9
        seed: 随机种子基准（可选，各片段自动递增以保证一致性又有差异）
        characters: 角色列表，每个元素为 dict，包含：
            - name: 角色名称（用于 prompt 中固定指代）
            - description: 外貌描述
            - image_path: 可选，已有定妆照路径
        output_dir: 输出根目录，默认 OUTPUT_DIR
        hook_type: 钩子类型（question/shocking/before_after/demonstration/story/challenge/celeb_style/pain_point）
        use_voiceover: 是否启用 AI 口播配音
        voiceover_style: 口播风格（standard/emotional/energetic/professional/storytelling）
        voice: 音色（female_young/female_warm/male_pro/male_magnetic/energetic_female）
        script_style: 广告脚本风格（pain_point_solution/before_after/storytelling/demonstration/social_proof）
        force: 为 True 时跳过 high 风险合规拦截（默认 False；critical 风险始终拦截）
        target_duration: 目标总时长（秒），None 时使用 duration × 片段数 的默认计算
        rhythm_style: 节奏风格：fast / moderate / cinematic
        parallel: 是否并行生成第 2-N 段（默认 True，更快；设为 False 串行，极致一致性）
        min_clips: 最少成功片段数，低于此数则终止（默认 3，即 60%）
        best_of: 每个分镜生成候选数量（best-of），自动择优（默认 1）
        quality_frames: best-of 择优时的抽帧数量（默认 12）
        keep_candidates: 是否保留未被选中的候选片段（默认 False）
        preview: 预览模式：仅生成第 1 段（std 模式），跳过后期处理，用于快速试错
        max_workers: 并行生成时的最大线程数（默认 4）

    Returns:
        {
            "final_path": Path,           # 9:16 最终成片
            "wide_path": Path | None,     # 16:9 版本（dual_output=True 时）
            "output_name": str,           # 本次输出文件名前缀
        }

    Raises:
        RuntimeError: 任何步骤失败时抛出异常
    """
    if output_name is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = _safe_output_stem(product_info.get("name", "product"))
        output_name = f"{safe_name}_{timestamp}"

    output_dir = Path(output_dir)
    ensure_dirs(output_dir)
    char_ref_dir = output_dir / "character_ref"
    clips_dir = output_dir / "clips"
    final_dir = output_dir / "final"

    client = KlingClient()

    # ── 预览模式：强制 std + 仅 1 段 + 跳过后期 ──
    if preview:
        mode = "std"
    effective_kling_model = kling_model or KLING_VIDEO_MODEL

    # ============================================================
    # 第一步：生成所有角色定妆照
    # ============================================================
    char_refs = []  # List[dict]: {"name": str, "image_path": Path, "img_b64": str}

    # 主角色（从 product_info 提取）
    main_character = {
        "name": "Character A",
        "description": f"{product_info.get('gender', 'person')} ({product_info.get('age', '25')})",
        "image_path": None,
    }
    if characters:
        main_character = characters[0]

    # 生成主角色定妆照（带重试，失败则抛出——主角色是一致性的基础）
    char_prompt = generate_character_prompt_for_role(
        product_info, main_character.get("description", "")
    )
    print(f"主角色定妆照 Prompt: {char_prompt[:100]}...")

    main_char_path = None
    if main_character.get("image_path"):
        main_char_path = Path(main_character["image_path"])
    else:
        cached_char_path = char_ref_dir / f"{output_name}_charA_ref.png"
        char_manifest = _build_character_manifest(
            product_info=product_info,
            character=main_character,
            prompt=char_prompt,
        )
        if (
            cached_char_path.exists()
            and cached_char_path.stat().st_size > 1024
            and _manifest_matches(cached_char_path, char_manifest)
        ):
            main_char_path = cached_char_path
            print(f"✅ 主角色定妆照 manifest 缓存命中：{main_char_path.name}")
        else:
            max_retries = 3
            last_error = None
            for attempt in range(1, max_retries + 1):
                try:
                    main_char_path = client.generate_character_ref(
                        prompt=char_prompt,
                        save_path=cached_char_path,
                    )
                    _write_clip_manifest(main_char_path, char_manifest)
                    print(f"✅ 主角色定妆照已生成：{main_char_path.name}")
                    break
                except Exception as e:
                    last_error = e
                    if attempt < max_retries:
                        wait = 5 * attempt
                        print(f"⚠️  主角色定妆照第 {attempt} 次尝试失败：{e}")
                        print(f"   等待 {wait} 秒后重试...")
                        time.sleep(wait)
                    else:
                        print(f"❌ 主角色定妆照生成失败（已重试 {max_retries} 次）：{e}")
                        raise RuntimeError(f"主角色定妆照生成失败：{e}") from last_error

    if main_char_path and main_char_path.exists():
        char_refs.append({
            "name": main_character.get("name", "Character A"),
            "image_path": main_char_path,
            "img_b64": base64.b64encode(main_char_path.read_bytes()).decode("utf-8"),
        })

    # 生成额外角色定妆照
    if characters and len(characters) > 1:
        for idx, char in enumerate(characters[1:], 2):
            char_name = char.get("name", f"Character {chr(64 + idx)}")
            char_desc = char.get("description", "")
            char_img_path = char.get("image_path")

            char_prompt = generate_character_prompt_for_role(product_info, char_desc)
            print(f"角色 {char_name} 定妆照 Prompt: {char_prompt[:100]}...")

            char_path = None
            if char_img_path:
                char_path = Path(char_img_path)
            else:
                max_retries = 3
                last_error = None
                for attempt in range(1, max_retries + 1):
                    try:
                        char_path = client.generate_character_ref(
                            prompt=char_prompt,
                            save_path=char_ref_dir / f"{output_name}_{char_name.replace(' ', '_')}_ref.png",
                        )
                        print(f"✅ 角色 {char_name} 定妆照已生成：{char_path.name}")
                        break
                    except Exception as e:
                        last_error = e
                        if attempt < max_retries:
                            wait = 5 * attempt
                            print(f"⚠️  角色 {char_name} 定妆照第 {attempt} 次尝试失败：{e}")
                            print(f"   等待 {wait} 秒后重试...")
                            time.sleep(wait)
                        else:
                            print(f"❌ 角色 {char_name} 定妆照生成失败（已重试 {max_retries} 次）：{e}")
                            # 额外角色失败不终止，继续用主角色

            if char_path and char_path.exists():
                char_refs.append({
                    "name": char_name,
                    "image_path": char_path,
                    "img_b64": base64.b64encode(char_path.read_bytes()).decode("utf-8"),
                })

    print(f"\n✅ 共加载 {len(char_refs)} 个角色参考图")

    # 读取商品参考图（如果提供）
    product_img_b64 = None
    product_image_path = None
    if product_image:
        if _is_http_url(product_image):
            product_image_path = _download_product_image_url(str(product_image), final_dir / f"{output_name}_refs")
            product_image = product_image_path
            print(f"🖼️ 商品参考图 URL 已下载：{product_image_path.name}")
        else:
            product_image_path = Path(product_image)
        _validate_product_image_file(product_image_path)
        product_img_b64 = base64.b64encode(product_image_path.read_bytes()).decode("utf-8")
    elif not preview and not allow_no_product_image:
        raise RuntimeError(
            "发布级成片必须提供 --product-image，以便约束生成和质检产品露出。"
            "如仅调试或非商品视频，请显式传入 --allow-no-product-image。"
        )

    # ============================================================
    # 第二步：生成广告脚本 + 分镜片段
    # ============================================================

    # ── 节奏模板初始化 ──
    # 根据目标总时长 + 节奏风格选择最合适的节奏模板
    # 若 target_duration 未指定，用 duration × 5（默认5段）估算总时长
    _est_total = target_duration if target_duration is not None else duration * 5
    rhythm_template = get_rhythm_template(
        _est_total, style=rhythm_style, product_type=product_info.get("type", "default")
    )
    _rhythm_name = rhythm_template["name"]
    _rhythm_transition = rhythm_template["transition_duration"]
    _rhythm_pace = rhythm_template["pace_style"]
    print(f"⏱️  节奏模板：{_rhythm_name}（{_rhythm_pace}，转场 {_rhythm_transition}s）")
    for seg in rhythm_template["segments"]:
        print(f"    [{seg['index']}] {seg['duration']:>5.2f}s  {seg['type']:<10s}  {seg['purpose']}")

    # P1-2: 检查节奏模板段时长是否超过可灵变速能力上限
    # 可灵生成 duration 秒，变速下限 0.5x → 单段最多可延长至 duration * 2 秒
    # 超出部分只能硬截断，实际总时长会显著低于预期，0.5x 画面也会有卡顿感
    _kling_max_seg_dur = duration * 2
    _over_limit_segs = [
        seg for seg in rhythm_template["segments"]
        if seg["duration"] > _kling_max_seg_dur
    ]
    if _over_limit_segs:
        print("\n⚠️  [P1-2] 节奏模板时长警告：")
        print(f"   可灵生成时长：{duration}s，变速下限 0.5x，单段最多延长至 {_kling_max_seg_dur:.1f}s")
        for _s in _over_limit_segs:
            _gap = _s["duration"] - _kling_max_seg_dur
            print(
                f"   段 [{_s['index']}] {_s['type']}：目标 {_s['duration']:.1f}s "
                f"> 上限 {_kling_max_seg_dur:.1f}s（超出 {_gap:.1f}s）"
                f" → 实际将截断至 {_kling_max_seg_dur:.1f}s，慢速画质差"
            )
        _actual_max_total = sum(
            min(seg["duration"], _kling_max_seg_dur)
            for seg in rhythm_template["segments"]
        ) - (_rhythm_transition * (len(rhythm_template["segments"]) - 1))
        print(
            f"   预估实际总时长上限：约 {_actual_max_total:.1f}s"
            f"（目标 {rhythm_template['total_duration']}s）"
        )
        print(f"   建议：调小 --target-duration，或增大 --duration（如 --duration 10）")
        print()
        if strict_mode and not preview:
            raise RuntimeError(
                "节奏模板存在超过当前生成片段后期拉伸能力的段落，继续生成会导致截断、卡顿或字幕口播错位。"
                "请调小 --target-duration，或增大 --duration 后重新生成。"
            )

    # 生成完整广告脚本
    # Bug1 修复：clip_prompts 尚未赋值，先用 styled_prompts 计算段数
    # styled_prompts 在下方 L1910 赋值，_num_segs 用节奏模板段数兜底
    _num_segs = len(rhythm_template["segments"])
    if preview:
        _num_segs = 1  # 预览模式只生成 1 段
    ad_script = generate_ad_script(
        product_info,
        style=script_style,
        hook_type=hook_type,
        num_segments=_num_segs,
    )
    print(f"📝 广告脚本风格：{SCRIPT_STYLES.get(script_style, {}).get('name', script_style)}")
    print(f"    视频标题：{ad_script['title']}")
    print(f"    话题标签：{' '.join(ad_script['hashtags'])}")

    # 广告合规检测
    compliance_result = check_script_compliance(ad_script)
    if not compliance_result["passed"]:
        print_compliance_report(compliance_result)
        risk = compliance_result["risk_level"]
        if risk == "critical":
            raise RuntimeError(
                f"已包含最高级禁用词或敏感词（risk={risk}），"
                "请修改脚本后再运行。即使传入 --force 也无法跳过 critical 级别拦截。"
            )
        elif risk == "high":
            # 高风险：默认拦截，force=True 可跳过
            if not force:
                raise RuntimeError(
                    f"包含高风险词（risk={risk}），已中止。"
                    "如确认要发布，请修改词语或传入 --force 参数。"
                )
            print("⚠️  --force 模式：跳过高风险合规检测，请自行承担合规风险。")
        else:
            # medium 风险：提示但不拦截
            print("⚠️  检测到中风险合规问题，建议修改后再发布（当前继续处理）。")
    else:
        print("✅ 广告合规检测通过")

    # 从脚本生成分镜 Prompts
    # 修复：使用 generate_clip_prompts() 而非 script_to_clip_prompts()
    # generate_clip_prompts 通过 apply_cinematic_style() 将 --style 参数（如 hitchcock/kubrick）
    # 注入到每个 prompt 的运镜描述，script_to_clip_prompts 只用写死的通用 scene_prompt
    # 两套系统合并：以电影风格 prompt 为主，追加 ad_script 中的产品场景描述作为补充
    styled_prompts = generate_clip_prompts(
        product_info,
        cinematic_style=style,
        characters=[c["name"] for c in char_refs] if char_refs else None,
        hook_type=hook_type,
    )

    # 将 ad_script 的 scene_prompt 中的产品细节追加到对应片段（取最短列表长度）
    script_scenes = [seg.get("scene_prompt", "") for seg in ad_script.get("segments", [])]
    clip_prompts = []
    for i, styled in enumerate(styled_prompts):
        scene_detail = script_scenes[i] if i < len(script_scenes) else ""
        if scene_detail:
            # 去掉 scene_prompt 末尾的宽高比描述（styled_prompt 已包含）
            scene_detail = scene_detail.replace(", 9:16 vertical", "").replace("9:16 vertical", "").strip().rstrip(",")
            clip_prompts.append(f"{styled}, {scene_detail}")
        else:
            clip_prompts.append(styled)

    if style != DEFAULT_CINEMATIC_STYLE:
        style_name = CINEMATIC_STYLES.get(style, {}).get("name", style)
        print(f"🎬 电影风格注入：{style_name}（影响 {len(clip_prompts)} 个片段的运镜与光影）")

    # ── 预览模式：只保留第 1 段 ──
    if preview:
        clip_prompts = clip_prompts[:1]
        print(f"\n⚡ 预览模式：仅生成第 1 段（std 模式，快速试错）")
        print(f"   确认效果 OK 后，去掉 --preview 重新生成完整 pro 版本")

    # ── 成本预估 ──
    _cost_info = estimate_cost(
        mode=mode,
        duration_per_clip=duration,
        num_clips=len(clip_prompts),
        num_characters=len(char_refs) if char_refs else 1,
        best_of=best_of,
    )
    print_cost_estimate(_cost_info)

    clip_paths = []
    scene_anchor_frames = []  # 全局场景锚点（从第 1 段提取，所有后续段都参考）
    scene_cfg = dict(SCENE_CONTINUITY_CONFIG)  # 浅拷贝，避免污染全局配置

    # 节奏模板：转场时长从模板取（统一管理，不再分散计算）
    scene_cfg["transition_duration"] = _rhythm_transition

    total_clips = len(clip_prompts)
    generation_start = time.time()

    def _get_narrative_for_idx(idx: int) -> str:
        seg_i = idx - 1
        try:
            segs = ad_script.get("segments", []) if isinstance(ad_script, dict) else []
            if 0 <= seg_i < len(segs):
                v = segs[seg_i].get("narrative") or segs[seg_i].get("type") or segs[seg_i].get("purpose")
                if v:
                    return str(v)
        except Exception:
            pass
        if seg_i <= 0:
            return "hook"
        if seg_i >= 4:
            return "cta"
        return "showcase"

    def _build_ref_images(idx: int, prev_clip_path: Optional[Path] = None, prev_last_frame_b64: Optional[str] = None) -> list:
        """构建参考图列表（三层连续性保障，最多 3 张避免过载）

        策略：角色 + 产品（必选） + 场景锚点或前帧尾（二选一，串行用前帧尾更精准）
        并行模式下 prev_clip_path=None 但 prev_last_frame_b64 可传入第 1 段尾帧。
        """
        narrative = _get_narrative_for_idx(idx).lower().strip()
        product_first = _is_product_required_narrative(narrative)

        ref_images = []
        primary_char = char_refs[0] if char_refs else None

        if product_first:
            if product_img_b64:
                ref_images.append({"role": "product", "image": f"data:image/png;base64,{product_img_b64}"})
            if primary_char:
                ref_images.append({"role": "character", "image": f"data:image/png;base64,{primary_char['img_b64']}"})
        else:
            if primary_char:
                ref_images.append({"role": "character", "image": f"data:image/png;base64,{primary_char['img_b64']}"})
            if product_img_b64:
                ref_images.append({"role": "product", "image": f"data:image/png;base64,{product_img_b64}"})

        is_first = (idx == 1)
        # 第 2 层：全局场景锚点（仅并行模式或无前帧尾时使用）
        _has_prev_frame = bool(
            (prev_clip_path and scene_cfg.get("use_previous_last_frame", True))
            or prev_last_frame_b64
        )
        use_scene_anchor = (
            not is_first
            and scene_cfg.get("use_scene_anchor", True)
            and scene_anchor_frames
            and not _has_prev_frame
        )
        if use_scene_anchor:
            # 只取第 1 张锚点帧（避免过载，最多 3 张参考图）
            ref_images.append({"role": "continuity", "image": scene_anchor_frames[0]})
        # 第 3 层：前一段最后一帧
        if not is_first:
            # 串行模式：从前一段视频提取
            if prev_clip_path and scene_cfg.get("use_previous_last_frame", True):
                try:
                    last_frame_b64 = _extract_frame_b64(prev_clip_path, time_sec=duration - 0.1)
                    if last_frame_b64:
                        ref_images.append({"role": "continuity", "image": last_frame_b64})
                except Exception:
                    pass
            # P0-3 修复：并行模式下使用外部传入的尾帧（通常是第 1 段尾帧）
            elif prev_last_frame_b64 and scene_cfg.get("use_previous_last_frame", True):
                ref_images.append({"role": "continuity", "image": prev_last_frame_b64})

        if len(ref_images) < MAX_REF_IMAGES and len(char_refs) > 1:
            for extra in char_refs[1:]:
                if len(ref_images) >= MAX_REF_IMAGES:
                    break
                try:
                    ref_images.append({"role": "character", "image": f"data:image/png;base64,{extra['img_b64']}"})
                except Exception:
                    continue

        seen = set()
        deduped = []
        for item in ref_images:
            img = item.get("image", "") if isinstance(item, dict) else item
            if not img:
                continue
            if img in seen:
                continue
            seen.add(img)
            deduped.append(item)

        return deduped[:MAX_REF_IMAGES]

    def _generate_one_clip(idx: int, prompt: str, prev_clip_path: Optional[Path] = None, prev_last_frame_b64: Optional[str] = None) -> Path:
        """生成单个片段（含自动重试 + 缓存跳过），返回本地文件路径"""
        clip_path = clips_dir / f"clip_{idx:02d}_{output_name}.mp4"

        ref_images = _build_ref_images(idx, prev_clip_path, prev_last_frame_b64)
        final_prompt = prompt
        if scene_cfg.get("inject_scene_prompt", True):
            final_prompt = _inject_scene_consistency_prompt(prompt, is_first_clip=(idx == 1))

        if ref_images:
            narrative = _get_narrative_for_idx(idx).lower().strip()
            final_prompt = _bind_reference_tags_to_prompt(final_prompt, ref_images, narrative)

        clip_seed = (seed + idx - 1) if seed is not None else None
        ref_image_values = _ref_image_values(ref_images)
        base_manifest = _build_clip_manifest(
            final_prompt=final_prompt,
            ref_images=ref_images,
            idx=idx,
            model=effective_kling_model,
            mode=mode,
            duration=duration,
            aspect_ratio=aspect_ratio,
            seed=clip_seed,
            negative_prompt=NEGATIVE_PROMPT,
        )
        final_clip_manifest = dict(base_manifest)
        final_clip_manifest["target_name"] = clip_path.name

        def _valid_cached_clip(target_path: Path, manifest: dict) -> bool:
            if not target_path.exists() or target_path.stat().st_size <= 100 * 1024:
                return False
            if not _manifest_matches(target_path, manifest):
                return False
            try:
                import subprocess as _sp
                _probe = _sp.run(
                    ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", str(target_path)],
                    capture_output=True, text=True, timeout=5,
                )
                if _probe.returncode == 0 and _probe.stdout.strip():
                    _dur = float(_probe.stdout.strip())
                    return abs(_dur - duration) < 1.0
            except Exception:
                return False
            return False

        if _valid_cached_clip(clip_path, final_clip_manifest):
            print(f"  ✅ 片段 {idx} manifest 缓存命中，跳过生成")
            return clip_path

        def _generate_to_path(target_path: Path) -> Path:
            max_retries = 3
            last_error = None
            target_manifest = dict(base_manifest)
            target_manifest["target_name"] = target_path.name
            idempotency_key = _build_video_idempotency_key(
                final_prompt,
                ref_image_values,
                idx,
                target_path,
                model=effective_kling_model,
                mode=mode,
                duration=duration,
                aspect_ratio=aspect_ratio,
                seed=clip_seed,
            )
            if _valid_cached_clip(target_path, target_manifest):
                print(f"  ✅ 片段 {idx} 候选缓存命中：{target_path.name}")
                return target_path
            for attempt in range(1, max_retries + 1):
                try:
                    video_result = client.generate_video(
                        prompt=final_prompt,
                        aspect_ratio=aspect_ratio,
                        duration=duration,
                        mode=mode,
                        reference_images=ref_image_values if ref_image_values else None,
                        image_fidelity=image_fidelity,
                        human_fidelity=human_fidelity,
                        seed=clip_seed,
                        negative_prompt=NEGATIVE_PROMPT,
                        idempotency_key=idempotency_key,
                        # P2-C 高级参数
                        model=effective_kling_model,
                        multi_shot=multi_shot,
                    )
                    video_url = video_result.get("video_url") or video_result.get("url")
                    if not video_url:
                        raise RuntimeError(f"片段 {idx} 未返回视频 URL")
                    client.download_video(video_url, target_path)

                    _qc_issue = _quick_quality_check(target_path, expected_duration=duration, idx=idx)
                    if _qc_issue:
                        try:
                            target_path.unlink(missing_ok=True)
                            _clip_manifest_path(target_path).unlink(missing_ok=True)
                        except Exception:
                            pass
                        raise RuntimeError(f"片段 {idx} 质检失败：{_qc_issue}")

                    _write_clip_manifest(target_path, target_manifest)
                    return target_path
                except Exception as e:
                    last_error = e
                    if attempt < max_retries:
                        wait = 5 * attempt
                        print(f"  ⚠️ 片段 {idx} 第 {attempt} 次尝试失败：{e}，{wait}s 后重试...")
                        time.sleep(wait)
            raise RuntimeError(f"片段 {idx} 生成失败（已重试 {max_retries} 次）") from last_error

        best_of_n = max(1, int(best_of or 1))
        if best_of_n <= 1:
            return _generate_to_path(clip_path)

        candidates: List[Path] = []
        scores: Dict[Path, float] = {}
        issues: Dict[Path, List[str]] = {}

        for v in range(1, best_of_n + 1):
            cand_path = clips_dir / f"clip_{idx:02d}_{output_name}_cand{v}.mp4"
            candidates.append(cand_path)
            _generate_to_path(cand_path)

        for cand_path in candidates:
            try:
                candidate_roles = {
                    item.get("role", "unknown") for item in ref_images if isinstance(item, dict)
                }
                narrative = _get_narrative_for_idx(idx).lower().strip()
                product_ref_for_candidate = (
                    product_image_path
                    if product_image_path
                    and "product" in candidate_roles
                    and _is_product_required_narrative(narrative)
                    else None
                )
                character_ref_for_candidate = (
                    main_char_path
                    if main_char_path and "character" in candidate_roles
                    else None
                )
                score, candidate_issues = _score_candidate_video_quality(
                    cand_path,
                    quality_frames=int(quality_frames or 12),
                    product_reference_image=product_ref_for_candidate,
                    character_reference_image=character_ref_for_candidate,
                )
                scores[cand_path] = score
                issues[cand_path] = candidate_issues
            except Exception as e:
                scores[cand_path] = 0.0
                issues[cand_path] = [f"质量检测失败：{e}"]

        best_path = max(candidates, key=lambda p: scores.get(p, 0.0))
        best_score = scores.get(best_path, 0.0)
        # P1 修复：所有候选质量检测均未通过时直接失败，避免选中明显废片
        if best_score <= 0:
            raise RuntimeError(
                f"片段 {idx} 的 {best_of_n} 个候选全部未通过质量检测（最高分 {best_score:.0f}），"
                f"无法选出有效片段。主要问题：{issues.get(best_path, ['未知'])[:1]}"
            )
        print(f"  🏆 片段 {idx} best-of：{best_score:.0f} 分（候选 {best_of_n}）")
        if issues.get(best_path):
            print(f"     主要问题：{issues[best_path][0]}")

        if best_path != clip_path:
            try:
                clip_path.unlink(missing_ok=True)
                _clip_manifest_path(clip_path).unlink(missing_ok=True)
            except Exception:
                pass
            shutil.move(str(best_path), str(clip_path))
            try:
                _clip_manifest_path(best_path).unlink(missing_ok=True)
            except Exception:
                pass
        _write_clip_manifest(clip_path, final_clip_manifest)

        if not keep_candidates:
            for p in candidates:
                if p == best_path:
                    continue
                try:
                    p.unlink(missing_ok=True)
                    _clip_manifest_path(p).unlink(missing_ok=True)
                except Exception:
                    pass

        return clip_path

    # ── 第 1 段：串行生成，用于提取全局场景锚点 ──
    # P1 #2（v2）：用 1-based 的 i 追踪成功段，段索引 = i-1（0-based）
    successful_clip_indices = [0]  # 第 1 段固定在此初始化；若失败则下方会抛异常
    _sep = "=" * 50
    print(f"\n{_sep}")
    print(f"🎬 片段 1/{total_clips}（串行）：{clip_prompts[0][:60]}...")
    print(_sep)
    # 问题2修复：第1段失败时用清晰的 RuntimeError，而不是让异常向上抳潮成模糊的崩溃信息
    try:
        first_clip = _generate_one_clip(1, clip_prompts[0])
    except Exception as e:
        raise RuntimeError(
            f"第 1 段视频生成失败（该段为场景锚点来源，不可跳过）：{e}"
        ) from e
    clip_paths.append(first_clip)
    elapsed = time.time() - generation_start
    print(f"  ✅ 片段 1/{total_clips} 完成 | 已用 {int(elapsed)}s")

    # 提取全局场景锚点
    if scene_cfg.get("use_scene_anchor", True):
        print("  🎬 提取全局场景锚点...")
        scene_anchor_frames = _build_scene_anchor(
            first_clip,
            clips_dir,
            num_keyframes=scene_cfg.get("anchor_keyframes", 2),
        )
        if scene_anchor_frames:
            print(f"    ✅ 场景锚点已建立：{len(scene_anchor_frames)} 张关键帧")

    # ── 第 2-N 段：并行或串行生成 ──
    # 并行模式：所有段都用第 1 段尾帧 + 场景锚点作为 prev（场景锚点是主力，prev 是辅助）
    # 串行模式：每段动态传前一段路径，极致一致性
    remaining_prompts = clip_prompts[1:]
    if remaining_prompts:
        failed_indices = []
        # 第 1 段尾帧（用于并行模式下所有后续段的 prev 参考）
        first_clip_last_frame = None
        if parallel and scene_cfg.get("use_previous_last_frame", True):
            try:
                first_clip_last_frame = _extract_frame_b64(first_clip, time_sec=duration - 0.1)
            except Exception:
                pass

        if parallel:
            print(f"\n🎬 并行生成剩余 {len(remaining_prompts)} 个片段（最大并发 {max_workers}）...")
            print(f"   模式：第 1 段尾帧 + 全局场景锚点 作为一致性参考")

            # 构建并行任务参数（idx 是 1-based）
            tasks = []
            for i, prompt in enumerate(remaining_prompts, 2):
                tasks.append({"idx": i, "prompt": prompt})

            # 线程安全的进度追踪
            _status_lock = Lock()
            _clip_status: Dict[int, str] = {t["idx"]: "排队中" for t in tasks}
            _results: Dict[int, Optional[Path]] = {}

            def _parallel_worker(task: dict) -> tuple:
                idx = task["idx"]
                prompt = task["prompt"]
                with _status_lock:
                    _clip_status[idx] = "生成中"
                try:
                    # P0-3 修复：并行模式传入第 1 段尾帧，激活第 3 层连续性参考
                    # 各段没有真正的前一段视频，但可以用第 1 段尾帧作为统一参考
                    path = _generate_one_clip(idx, prompt, None, first_clip_last_frame)
                    with _status_lock:
                        _clip_status[idx] = "完成"
                        _results[idx] = path
                    elapsed = time.time() - generation_start
                    done = sum(1 for v in _results.values() if v is not None)
                    total_remaining = len(tasks) - done
                    # 打印单段完成进度
                    print(f"  ✅ 片段 {idx}/{total_clips} 完成 | 已用 {int(elapsed)}s | 还剩 {total_remaining} 段")
                    return (idx, path, None)
                except Exception as e:
                    with _status_lock:
                        _clip_status[idx] = f"失败: {e}"
                        _results[idx] = None
                    print(f"  ❌ 片段 {idx} 失败：{e}")
                    return (idx, None, e)

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(_parallel_worker, t) for t in tasks]
                for future in as_completed(futures):
                    idx, path, err = future.result()
                    # 结果已在 worker 中写入 _results

            # 按索引顺序收集成功的片段
            for i in range(2, total_clips + 1):
                path = _results.get(i)
                if path is not None:
                    clip_paths.append(path)
                    successful_clip_indices.append(i - 1)
                else:
                    failed_indices.append(i)

        else:
            # ── 串行模式（极致一致性）──
            print(f"\n🎬 串行生成剩余 {len(remaining_prompts)} 个片段（prev 动态更新）...")
            for i, prompt in enumerate(remaining_prompts, 2):
                prev = clip_paths[-1] if clip_paths else None
                print(f"\n{_sep}")
                print(f"🎬 片段 {i}/{total_clips}：{prompt[:60]}...")
                print(_sep)
                try:
                    path = _generate_one_clip(i, prompt, prev)
                    clip_paths.append(path)
                    successful_clip_indices.append(i - 1)  # i 是 1-based，段索引 = i-1
                    elapsed = time.time() - generation_start
                    done = len(clip_paths)
                    remaining_count = total_clips - done
                    avg = elapsed / done if done > 0 else 0
                    eta = avg * remaining_count
                    eta_str = f"{int(eta//60)}分{int(eta%60)}秒" if eta > 60 else f"{int(eta)}秒"
                    print(f"  ✅ 片段 {i}/{total_clips} 完成 | 已用 {int(elapsed)}s | 预计还需 {eta_str}")
                except Exception as e:
                    print(f"  ❌ 片段 {i} 失败：{e}")
                    failed_indices.append(i)
                    # 单段失败不立即崩溃，继续尝试后续片段

        if failed_indices:
            success_count = len(clip_paths)
            print(f"\n⚠️  {len(failed_indices)} 个片段生成失败：{failed_indices}")
            print(f"   成功片段数：{success_count}/{total_clips}")
            if strict_mode and not preview:
                raise RuntimeError(
                    f"发布级成片要求分镜完整，但片段 {failed_indices} 生成失败。"
                    "已阻断缺段合成，避免丢失产品展示、效果证明或 CTA。"
                )
            # 最少成功段数（默认 3，即 60%）
            min_required = max(2, min_clips)
            if success_count < min_required:
                raise RuntimeError(
                    f"片段生成失败过多（成功 {success_count}/{total_clips}，需要 ≥{min_required} 段），无法继续合成"
                )
            print(f"   成功片段 ≥{min_required}，继续后续合成流程（将跳过失败片段）")

    # 部分成功时，seg_indices 记录实际成功的段索引（用于字幕/口播/音效对齐）
    # 全部成功时设为 None，退化到原有逻辑，避免不必要的白名单过滤
    seg_indices_for_subtitles = (
        successful_clip_indices if len(successful_clip_indices) < total_clips else None
    )

    print(f"\n✅ 成功生成 {len(clip_paths)}/{total_clips} 个片段")

    # 清理关键帧临时文件
    _cleanup_keyframes(clips_dir, output_name)

    # ── 预览模式：跳过所有后期处理，直接返回第 1 段 ──
    if preview:
        total_elapsed = time.time() - generation_start
        print(f"\n⚡ 预览模式完成！总用时 {int(total_elapsed)}s")
        print(f"   预览片段：{clip_paths[0]}")
        print(f"   确认效果 OK 后，去掉 --preview 重新生成完整 pro 版本")
        return {
            "final_path": clip_paths[0],
            "wide_path": None,
            "output_name": output_name,
            "preview": True,
            "clip_paths": clip_paths,
            "ad_script": ad_script,
        }

    # ============================================================
    # 片段色调一致性匹配
    # ============================================================
    # 以第一段为参考，匹配后续片段的亮度和色偏，减少跳切感
    if len(clip_paths) > 1:
        print()
        print("🎨 片段色调一致性匹配...")
        color_match_dir = clips_dir / f"{output_name}_color_matched"
        matched_clips = color_match_clips(clip_paths, color_match_dir)
        if any(c != o for c, o in zip(matched_clips, clip_paths)):
            clip_paths = matched_clips
            print(f"✅ 色调匹配完成")
        else:
            print(f"   片段色调一致，无需调整")

        # P0-B：跨片段人物/肤色一致性校验（警告级，不阻断）
        # 以第 1 段为基准，比较每段中间帧最大肤色区域面积比，偏差 >40% 打印警告
        try:
            import tempfile as _cc_tmp
            from quality_checker import _detect_skin_regions as _cc_skin
            from PIL import Image as _cc_pil

            _cc_ref_ratio: float = -1.0
            _consistency_warnings: list = []

            with _cc_tmp.TemporaryDirectory(prefix="cc_qc_") as _cc_dir:
                for _cc_i, _cc_clip in enumerate(clip_paths):
                    try:
                        _cc_dur = _get_clip_duration(_cc_clip)
                        _cc_t = _cc_dur * 0.5
                        _cc_frame = Path(_cc_dir) / f"cc_{_cc_i}.jpg"
                        _cc_cmd = [
                            "ffmpeg", "-y",
                            "-ss", f"{_cc_t:.2f}", "-i", str(_cc_clip),
                            "-frames:v", "1", "-q:v", "4",
                            "-vf", "scale=320:-1",
                            str(_cc_frame),
                        ]
                        _ccr = subprocess.run(_cc_cmd, capture_output=True, timeout=10)
                        if _ccr.returncode != 0 or not _cc_frame.exists():
                            continue
                        _cc_img = _cc_pil.open(_cc_frame).convert("RGB")
                        _cc_regions = _cc_skin(_cc_img)
                        _cc_img_area = _cc_img.width * _cc_img.height
                        _cc_ratio = _cc_regions[0]["area"] / _cc_img_area if _cc_regions else 0.0
                        if _cc_i == 0:
                            _cc_ref_ratio = _cc_ratio
                        elif _cc_ref_ratio > 0.02 and _cc_ratio > 0.0:
                            _cc_diff = abs(_cc_ratio - _cc_ref_ratio) / _cc_ref_ratio
                            if _cc_diff > 0.40:
                                _msg = (
                                    f"片段 {_cc_i+1} 与片段 1 人物肤色区域面积差异 "
                                    f"{_cc_diff*100:.0f}%（>{40}%），人物/构图可能不一致"
                                )
                                _consistency_warnings.append(_msg)
                                print(f"   ⚠️  P0-B 一致性：{_msg}")
                    except Exception:
                        pass

            if not _consistency_warnings:
                print("   ✅ P0-B 跨片段一致性校验通过")
        except Exception as _cc_err:
            _consistency_warnings = []
            print(f"   P0-B 一致性校验跳过（{_cc_err}）")

        # F4 修复：拼接前对各片段做直方图均衡，保证相邻片段曝光接近。
        # 问题：色调匹配只做通道增益修正，无法消除片段间整体曝光差异（如室内偏暗 vs 室外偏亮），
        # xfade dissolve 转场时亮度突变会在过渡帧中明显可见（"闪一下"）。
        # 做法：对每个片段提取中间帧的直方图，计算相对于全片段均值的曝光偏差，
        # 偏差超阈值时用 ffmpeg eq 做轻量亮度补偿（不影响已做的色调匹配）。
        try:
            _histeq_dir = clips_dir / f"{output_name}_histeq"
            _histeq_dir.mkdir(parents=True, exist_ok=True)
            _brightness_vals = []
            for _cp in clip_paths:
                try:
                    # 用 ffprobe/ffmpeg 提取中间帧亮度均值
                    _mid_t = _get_clip_duration(_cp) / 2
                    _luma_cmd = [
                        "ffmpeg", "-ss", f"{_mid_t:.2f}", "-i", str(_cp),
                        "-frames:v", "1", "-f", "rawvideo",
                        "-pix_fmt", "gray", "-vf", "scale=160:90", "-"
                    ]
                    _lr = subprocess.run(_luma_cmd, capture_output=True, timeout=10)
                    if _lr.returncode == 0 and _lr.stdout:
                        import array as _arr2
                        _px = _arr2.array("B", _lr.stdout)
                        _brightness_vals.append(sum(_px) / max(len(_px), 1))
                    else:
                        _brightness_vals.append(None)
                except Exception:
                    _brightness_vals.append(None)

            _valid_br = [b for b in _brightness_vals if b is not None]
            if len(_valid_br) >= 2:
                # 取中位数作为目标亮度（比均值抗异常值）
                _sorted_br = sorted(_valid_br)
                _target_br = _sorted_br[len(_sorted_br) // 2]
                _histeq_clips = []
                for _ci2, (_cp2, _br) in enumerate(zip(clip_paths, _brightness_vals)):
                    if _br is None:
                        _histeq_clips.append(_cp2)
                        continue
                    _diff = _target_br - _br
                    # 偏差超过 15 灰度值（满幅 255）才做补偿，避免过度处理
                    if abs(_diff) < 15:
                        _histeq_clips.append(_cp2)
                        continue
                    _eq_brightness = max(-0.08, min(0.08, _diff / 255.0))
                    _heq_path = _histeq_dir / f"{output_name}_clip_{_ci2+1:02d}_heq.mp4"
                    try:
                        _heq_cmd = [
                            "ffmpeg", "-y", "-i", str(_cp2),
                            "-vf", f"eq=brightness={_eq_brightness:.3f}",
                            "-c:v", "libx264", "-preset", "fast", "-crf", "16",
                            "-c:a", "copy", str(_heq_path)
                        ]
                        subprocess.run(_heq_cmd, capture_output=True, timeout=120, check=True)
                        _histeq_clips.append(_heq_path)
                        print(f"   F4 直方图均衡：片段 {_ci2+1} 亮度补偿 {_diff:+.0f} ({_eq_brightness:+.3f})")
                    except Exception as _heq_err:
                        print(f"   F4 均衡失败（片段 {_ci2+1}）：{_heq_err}，跳过")
                        _histeq_clips.append(_cp2)
                clip_paths = _histeq_clips
                print(f"✅ 拼接前直方图均衡完成")
        except Exception as _f4_err:
            print(f"  ⚠️  F4 直方图均衡失败（{_f4_err}），跳过")

    # ============================================================
    # 片段时长适配节奏模板（裁切/变速到目标时长）
    # ============================================================
    # 策略：可灵 API 生成固定时长（默认 5s），后期裁到节奏模板指定的段时长
    # 部分成功时：按实际成功段的索引去节奏模板里找对应目标时长
    _adjusted_dir = clips_dir / f"{output_name}_rhythm_adjusted"
    _adjusted_dir.mkdir(parents=True, exist_ok=True)
    _seg_indices_list = sorted(successful_clip_indices)

    # P0-C 修复：_seg_dur_map 提前在节奏适配前初始化，这样 B4 in-place 更新才能生效。
    # 原来放在 L2715（字幕生成前）导致 B4 写入时触发 NameError 被静默吞掉，
    # 字幕始终用模板时长而非 ffprobe 实测时长。
    _seg_dur_map: dict = {s["index"]: s["duration"] for s in rhythm_template["segments"]}

    try:
        _rhythm_segs = {s["index"]: s for s in rhythm_template["segments"]}
        _adjusted_paths = []
        _any_adjusted = False

        for _pos, _clip_path in enumerate(clip_paths):
            _orig_idx = _seg_indices_list[_pos]  # 原始段索引
            _target_seg = _rhythm_segs.get(_orig_idx)
            if _target_seg is None:
                # 节奏模板里没有对应段（理论上不会发生，防御性跳过）
                _adjusted_paths.append(_clip_path)
                continue

            _target_dur = _target_seg["duration"]
            _adjusted_path = _adjusted_dir / f"{output_name}_clip_{_orig_idx:02d}_adjusted.mp4"

            # 检查是否需要调整（差异 > 0.2s 才调，避免不必要的重编码）
            try:
                _orig_dur = _get_clip_duration(_clip_path)
                if abs(_orig_dur - _target_dur) > 0.2:
                    # Bug #3 修复：透传叙事类型，让 hook 段从头裁保留开头精华帧
                    _narrative = _target_seg.get("narrative", _target_seg.get("type", "showcase"))
                    adjust_clip_duration(
                        clip_path=_clip_path,
                        output_path=_adjusted_path,
                        target_duration=_target_dur,
                        narrative=_narrative,
                    )
                    _adjusted_paths.append(_adjusted_path)
                    _any_adjusted = True
                else:
                    _adjusted_paths.append(_clip_path)
            except Exception as _adj_err:
                print(f"  ⚠️  段 {_orig_idx} 时长调整失败：{_adj_err}，使用原片段")
                _adjusted_paths.append(_clip_path)

        if _any_adjusted:
            clip_paths = _adjusted_paths
            print(f"✅ 节奏适配完成（片段已调整到节奏模板目标时长）")
            print(f"   策略：中间裁切保留核心画面，保持节奏精准")
        else:
            print(f"   片段时长与节奏模板一致，无需调整")

        # B4 修复：节奏适配后用 ffprobe 实测各片段实际时长，更新 _seg_dur_map。
        # P5 转场溢出保护原来用模板时长（_seg_dur_map），但适配后实际时长可能与模板有 ±0.1s 偏差，
        # 导致 xfade offset 超出实际片段时长，出现黑帧。
        # 用 ffprobe 实测值覆盖 _seg_dur_map，让 P5 保护基于真实时长做判断。
        try:
            for _pos2, _cp2 in enumerate(clip_paths):
                if _pos2 >= len(_seg_indices_list):
                    break
                _oidx2 = _seg_indices_list[_pos2]
                try:
                    _measured_dur = _get_clip_duration(_cp2)
                    if _measured_dur > 0:
                        _seg_dur_map[_oidx2] = _measured_dur
                except Exception:
                    pass
            print(f"   B4：已用 ffprobe 实测时长更新转场保护基准")
        except Exception as _b4_err:
            print(f"  ⚠️  B4 实测时长更新失败（{_b4_err}），继续使用模板时长")

    except Exception as e:
        print(f"⚠️  节奏适配失败：{e}，继续使用原始时长片段")

    if strict_mode and not preview:
        print("🔍 开始分段语义质量检测...")
        _check_segment_semantic_quality(
            clip_paths=clip_paths,
            successful_clip_indices=_seg_indices_list,
            ad_script=ad_script,
            product_image_path=product_image_path,
            main_char_path=main_char_path,
            quality_frames=quality_frames,
        )
        print("✅ 分段语义质量检测通过")

    # ============================================================
    # 第三步：拼接视频 + BGM
    # ============================================================
    merged_path = final_dir / f"{output_name}_merged.mp4"

    # 选择 BGM：优先从 freetouse 按品类+电影风格+节奏自动选曲，失败则 fallback 到本地文件
    # 用节奏模板计算实际总时长（考虑转场重叠
    _timeline_for_bgm = compute_segment_timeline(rhythm_template, seg_indices=seg_indices_for_subtitles)
    total_video_duration = _timeline_for_bgm[-1]["end"] if _timeline_for_bgm else len(clip_paths) * duration
    product_type = product_info.get("type", "default")

    # Bug4 修复：BGM pace 从节奏模板的 pace_style 推断，而非静态读 CLIP_STRUCTURE
    # _rhythm_pace 已在 L1834 从 rhythm_template 中取出，与画面节奏保持一致
    _pace_map = {"fast": "fast", "moderate": "medium", "cinematic": "slow"}
    _rhythm_pace_for_bgm = _pace_map.get(_rhythm_pace)
    if _rhythm_pace_for_bgm:
        pace = _rhythm_pace_for_bgm
    else:
        from config import CLIP_STRUCTURE
        pace = _detect_pace_from_clips(CLIP_STRUCTURE)

    bgm_file = pick_bgm_for_product(
        product_type,
        target_duration=total_video_duration,
        cinematic_style=style,
        pace=pace,
    )

    if not bgm_file:
        # fallback 到本地 BGM 文件
        local_bgm = PROJECT_ROOT / BGM_PATH
        if local_bgm.exists():
            bgm_file = local_bgm
            print(f"🎵 使用本地 BGM：{BGM_PATH}")
        else:
            print("⚠️  未找到 BGM，将生成无声视频")
            bgm_file = None  # 明确设为 None

    bgm_available = bgm_file is not None and Path(bgm_file).exists()
    fallback_audio_used = False

    # BGM 版权提示（B4 修复：加 try/except 防止 bgm_file 为 None 或文件不存在时 crash）
    if bgm_available:
        try:
            bgm_info = get_bgm_copyright_info(bgm_file)
            print(f"🎵 BGM：{bgm_info['title']}（{bgm_info['source']}）")
            if not bgm_info["is_commercial_safe"]:
                print(f"   ⚠️  {bgm_info['warning']}")
        except Exception as _bgm_info_err:
            print(f"🎵 BGM 已选定（版权信息读取失败：{_bgm_info_err}）")

    # #5 修复：兜底音频必须在拼接之前生成，否则 merge_clips_ffmpeg 收到 bgm=None
    if not bgm_available:
        if not use_voiceover:
            raise RuntimeError(
                "BGM 不可用且未启用口播，无法生成可发布音频；请配置 assets/bgm.mp3 或启用 --voiceover"
            )
        print()
        print("🚨 BGM 不可用，将生成极低音量占位音轨，仅用于后续口播混音")
        try:
            fallback_audio = final_dir / f"{output_name}_fallback_audio.m4a"
            # 兜底音频时长按节奏模板计算（考虑转场重叠和不等长段）
            _fb_timeline = compute_segment_timeline(rhythm_template, seg_indices=seg_indices_for_subtitles)
            total_dur = _fb_timeline[-1]["end"] if _fb_timeline else len(clip_paths) * duration
            generate_fallback_audio(fallback_audio, duration=total_dur)
            bgm_file = fallback_audio
            bgm_available = True
            fallback_audio_used = True
            print(f"✅ 口播混音占位音轨已生成（不可作为发布音频）")
        except Exception as e:
            print(f"❌ 兜底音频生成失败：{e}")
            raise RuntimeError("BGM 不可用且口播混音占位音轨生成失败，已阻断不可发布成片") from e

    try:
        # 内容感知转场：根据脚本叙事类型序列 + 节奏风格，智能选择转场
        # 从脚本中提取叙事类型序列
        _all_narratives = [
            seg.get("narrative", "showcase")
            for seg in ad_script.get("segments", [])
        ]
        # 按实际成功段过滤（对齐字幕/口播的 seg_indices 逻辑）
        if seg_indices_for_subtitles is not None:
            _success_narratives = [
                _all_narratives[i] if i < len(_all_narratives) else "showcase"
                for i in sorted(seg_indices_for_subtitles)
            ]
        else:
            _success_narratives = _all_narratives[:len(clip_paths)] if _all_narratives else ["showcase"] * len(clip_paths)

        # 节奏风格：从节奏模板取，不认识则回退到 moderate
        _transition_style = _rhythm_pace if _rhythm_pace in ("fast", "moderate", "cinematic") else "moderate"
        _base_transition_dur = scene_cfg.get("transition_duration", 0.3)

        try:
            scene_transitions = generate_transition_sequence(
                _success_narratives,
                style=_transition_style,
                base_duration=_base_transition_dur,
            )
            # 安全校验：转场数必须 = 片段数 - 1
            expected_count = len(clip_paths) - 1
            if len(scene_transitions) != expected_count:
                print(f"⚠️  转场数量不匹配（预期 {expected_count}，实际 {len(scene_transitions)}），回退到固定转场")
                raise ValueError("transition count mismatch")

            # P5 修复：转场时长溢出保护
            # xfade 要求转场时长 < 片段时长，超短段（<0.5s）时直接崩溃或产生黑帧
            # 对每个转场，限制其时长不超过相邻两段中较短者的 40%，且绝对值不超过 0.5s
            # （防止短段如 2s 时 40%=0.8s 转场，内容展示时间极少）
            _seg_dur_list = [_seg_dur_map.get(i, float(duration)) for i in sorted(_seg_indices_list)]
            for _ti, _t in enumerate(scene_transitions):
                _left_dur = _seg_dur_list[_ti] if _ti < len(_seg_dur_list) else float(duration)
                _right_dur = _seg_dur_list[_ti + 1] if _ti + 1 < len(_seg_dur_list) else float(duration)
                _max_trans = min(min(_left_dur, _right_dur) * 0.4, 0.5)  # 40% 且绝对不超 0.5s
                if _t["duration"] > _max_trans:
                    _t["duration"] = max(0.1, _max_trans)

            # 打印转场信息，方便了解
            print()
            print(f"🎬 内容感知转场序列（{_transition_style} 风格）：")
            for i, t in enumerate(scene_transitions):
                _from = _success_narratives[i] if i < len(_success_narratives) else "?"
                _to = _success_narratives[i + 1] if i + 1 < len(_success_narratives) else "?"
                print(f"   {i}→{i+1} ({_from} → {_to}): {t['type']} ({t['duration']:.2f}s)")
            print()
        except Exception as _te:
            # 兜底：回退到原来的固定转场列表
            print(f"⚠️  内容感知转场生成失败（{_te}），回退到固定转场")
            scene_transitions = []
            for i in range(len(clip_paths) - 1):
                scene_transitions.append({
                    "type": scene_cfg.get("transition_type", "dissolve"),
                    "duration": scene_cfg.get("transition_duration", 0.25),
                })

        # Q4 修复：计算节奏模板的实际段落边界时间点，传入 merge_clips_ffmpeg
        # 这些时间点会透传到 _build_bgm_audio_filter → _build_volume_envelope，
        # 让 BGM 音量包络精确跟随不等长段落，而非均分估算
        _timeline_for_bgm = compute_segment_timeline(rhythm_template, seg_indices=seg_indices_for_subtitles)
        _bgm_key_times = [s["start"] for s in _timeline_for_bgm] if _timeline_for_bgm else None

        merge_clips_ffmpeg(
            clips=clip_paths,
            output=merged_path,
            transitions=scene_transitions,
            bgm=bgm_file,
            envelope_key_times=_bgm_key_times,
        )
        print(f"✅ 视频拼接完成：{merged_path.name}")
    except Exception as e:
        print(f"❌ 视频拼接失败：{e}")
        raise RuntimeError("视频拼接失败") from e

    # ============================================================
    # 第四步：统一调色（提前到字幕烧录前）
    # ============================================================
    # 优化 #6 修复：调色必须在字幕烧录之前对整段视频执行一次
    # 原来放在第七步（字幕/音效之后），每次字幕/音效重编码都会引入非线性色调变化，
    # 打乱已经匹配好的片段间色调一致性。现在提前到拼接后直接对 merged_path 统一调色。
    graded_path = final_dir / f"{output_name}_graded.mp4"

    try:
        color_preset = get_color_grading_for_style(style)
        print(f"🎨 应用调色预设：{color_preset}（匹配电影风格：{style}）")

        apply_color_grading(
            video=merged_path,
            output=graded_path,
            preset=color_preset,
            brand_color_tint=True,
        )
        print(f"✅ 调色完成：{graded_path.name}")
    except Exception as e:
        print(f"⚠️  调色失败：{e}")
        graded_path = merged_path

    # ============================================================
    # P1-A：视频稳定化 + 去闪烁（--stabilize 启用时）
    # 放在调色后、预裁切前，确保稳定化基于已调色视频
    # ============================================================
    if stabilize:
        print()
        print("🎬 P1-A 视频稳定化 + 去闪烁...")
        _stab_path = final_dir / f"{output_name}_stabilized.mp4"
        try:
            from video_merger import stabilize_video as _stabilize_fn
            _stab_result = _stabilize_fn(graded_path, _stab_path, smoothing=10, deflicker=True)
            if _stab_result == _stab_path and _stab_path.exists():
                graded_path = _stab_path
                print(f"✅ 稳定化完成：{_stab_path.name}")
        except Exception as _stab_err:
            print(f"⚠️  稳定化跳过（{_stab_err}）")

    # ============================================================
    # P1-8：auto_trim 提前到字幕烧录前执行
    # 原来放在第八步（字幕之后），片头被裁后第一条钩子字幕会被切掉一部分。
    # 提前到调色后、字幕前，保证字幕时间轴始终相对于已裁好的视频。
    # 同时 auto_trim 使用 -c copy 流复制（速度快，不重编码），
    # 后续 export_final_video 统一做最终编码，消除原来 trim 的独立重编码。
    # ============================================================
    trimmed_for_subtitle = final_dir / f"{output_name}_trimmed_pre.mp4"
    _trim_start = 0.0
    _trim_end_amt = 0.0
    try:
        # 用 -c copy 流复制裁切（只切黑帧/冻结帧，不重编码）
        import re as _re_trim
        import subprocess as _sp_trim

        # 检测首尾异常帧时间量
        from video_merger import detect_freeze_frames as _detect_freeze
        _freeze_s, _freeze_e = _detect_freeze(graded_path)

        _black_cmd = [
            "ffmpeg", "-i", str(graded_path),
            "-vf", "blackdetect=d=0.3:pix_th=0.10",
            "-an", "-f", "null", "-",
        ]
        _black_res = _sp_trim.run(_black_cmd, capture_output=True, text=True, timeout=60)
        _black_matches = _re_trim.findall(
            r"black_start:([\d.]+)\s+black_end:([\d.]+)", _black_res.stderr
        )
        _graded_dur = _get_clip_duration(graded_path)
        _black_s = 0.0
        _black_e = 0.0
        for _bs, _be in _black_matches:
            _bsf, _bef = float(_bs), float(_be)
            if _bsf < 0.5:
                _black_s = max(_black_s, _bef)
            if _graded_dur > 0 and _bef > _graded_dur - 0.5:
                _black_e = max(_black_e, _bef - _bsf)

        _trim_start = max(_black_s, _freeze_s)
        _trim_end_amt = max(_black_e, _freeze_e)
        _trim_end = _graded_dur - _trim_end_amt if _graded_dur > 0 else _graded_dur

        if _trim_start > 0.08 or _trim_end_amt > 0.08:
            # P1 修复：改用 libx264 重编码裁切，消除 -c copy 在非关键帧处的花屏
            # -c copy 虽然快，但在 B/P 帧处裁切会产生 1~3 帧花屏，抖音开头可见
            _trim_has_audio = _has_audio_stream(graded_path)
            _recode_cmd = [
                "ffmpeg", "-y",
                "-ss", str(_trim_start),
                "-to", str(_trim_end),
                "-i", str(graded_path),
                "-c:v", "libx264", "-preset", "fast", "-crf", "16",
                "-pix_fmt", "yuv420p", "-color_range", "pc",
                "-movflags", "+faststart",
            ]
            if _trim_has_audio:
                _recode_cmd.extend(["-c:a", "copy"])
            _recode_cmd.append(str(trimmed_for_subtitle))
            run_ffmpeg(_recode_cmd, timeout=180)
            graded_path = trimmed_for_subtitle
            print(f"✂️  预裁切完成（开头 {_trim_start:.2f}s / 结尾 {_trim_end_amt:.2f}s，重编码消除花屏）")
        else:
            trimmed_for_subtitle = graded_path
    except Exception as _te:
        print(f"⚠️  预裁切失败（{_te}），继续使用原视频")
        trimmed_for_subtitle = graded_path
        _trim_start = 0.0
        _trim_end_amt = 0.0

    # ============================================================
    # 第五步：添加字幕 + 口播配音
    # ============================================================
    subtitled_path = final_dir / f"{output_name}_subtitled.mp4"

    # P1-8：字幕烧录源改为已经裁切好的 graded_path（pretrimed），
    # 保证字幕时间轴相对于已裁好的视频不会被后续 trim 切掉
    # 从广告脚本生成字幕（更丰富、更有节奏感）
    # 节奏模板驱动：每段时长从模板取，不再是均匀的 clip_duration
    actual_transition_dur = scene_cfg.get("transition_duration", 0.6)
    # P0-C 修复：_seg_dur_map 已在节奏适配前初始化，B4 已用 ffprobe 实测时长 in-place 更新。
    # 此处仅补充节奏模板中尚未被 B4 更新的 key（如节奏适配整体失败时的保底），不覆盖已有实测值。
    for _s in rhythm_template["segments"]:
        _seg_dur_map.setdefault(_s["index"], _s["duration"])
    # P1 #2（v2）：透传实际成功段索引，处理中间段失败的情况
    subtitles = script_to_subtitles(
        ad_script,
        clip_duration=duration,
        transition_duration=actual_transition_dur,
        num_clips=len(clip_paths),
        seg_indices=seg_indices_for_subtitles,
        segment_durations=_seg_dur_map,
    )

    # P1 修复：预裁切后字幕/口播时间轴需要同步偏移（减去裁掉的片头时长）
    if _trim_start > 0.001:
        for sub in subtitles:
            sub["start"] = max(0.0, sub["start"] - _trim_start)
            sub["end"] = max(0.0, sub["end"] - _trim_start)

    # 字幕时间对齐 BGM 节拍（卡点效果）
    if bgm_file and bgm_file.exists():
        # P0-3：仅在无口播时做 beat 对齐；有口播时字幕对齐交给 voiceover 接管
        # 两次对齐叠加会导致时间轴累计偏移 0.1~0.3s，口播场景字幕明显滞后
        _beat_align_needed = not use_voiceover
        if _beat_align_needed:
            subtitles = align_subtitles_to_beats(subtitles, bgm_file)

    # 生成口播配音（如果启用）
    # P0-3：有口播时，用 voiceover 对齐的字幕直接替代 beat 对齐结果（只做一次对齐），
    # 原来两次对齐叠加会导致字幕时间轴整体偏移 0.1~0.3s
    voiceover_enabled = False
    voiceover_audio = final_dir / f"{output_name}_voiceover.m4a"
    if use_voiceover:
        try:
            print(f"🎤 生成 AI 口播（脚本风格：{script_style}，音色：{voice}）")
            # 从广告脚本生成口播文案（比模板更丰富）
            # P1 #2（v2）：透传实际成功段索引，处理中间段失败的情况
            voiceover_script = script_to_voiceover(
                ad_script,
                clip_duration=duration,
                transition_duration=actual_transition_dur,
                num_clips=len(clip_paths),
                seg_indices=seg_indices_for_subtitles,
                segment_durations=_seg_dur_map,
                voiceover_style=voiceover_style,
            )
            # P1 修复：预裁切后口播时间轴同步偏移，并压缩总时长上限
            if _trim_start > 0.001 or _trim_end_amt > 0.001:
                for line in voiceover_script:
                    line["start"] = max(0.0, line["start"] - _trim_start)
                    line["end"] = max(0.0, line["end"] - _trim_start)
            # P1 #3：口播总时长按节奏模板计算（考虑转场重叠），并扣除裁切部分
            _vo_timeline = compute_segment_timeline(rhythm_template, seg_indices=seg_indices_for_subtitles)
            total_duration = _vo_timeline[-1]["end"] if _vo_timeline else len(clip_paths) * duration
            total_duration = max(0.5, total_duration - _trim_start - _trim_end_amt)
            voiceover_audio, voiceover_subs = generate_full_voiceover(
                voiceover_script,
                voiceover_audio,
                voice=voice,
                total_duration=total_duration,
            )
            _validate_voiceover_audio(voiceover_audio)
            # 用口播对齐的字幕替换原字幕（更精准）
            subtitles = align_subtitles_to_voiceover(subtitles, voiceover_subs)
            voiceover_enabled = True
            print(f"✅ 口播生成完成：{voiceover_audio.name}")
        except Exception as e:
            print(f"⚠️  口播生成失败：{e}")
            voiceover_enabled = False
            if strict_mode or fallback_audio_used:
                raise RuntimeError("请求了口播但未生成有效口播音频，已阻断不可发布成片") from e

    # ============================================================
    # 无声视频检测（兜底音频已在拼接前生成，此处仅处理口播补充场景）
    # ============================================================
    # fallback 音轨只允许作为口播混音占位，不能单独成为发布音频
    if fallback_audio_used and not voiceover_enabled:
        raise RuntimeError("BGM 不可用且口播无效，fallback 底噪不能作为可发布音频")

    # 抖音平台优化：大字幕 + 关键词高亮 + 安全区
    # 按节奏模板计算实际总时长
    _douyin_timeline = compute_segment_timeline(rhythm_template, seg_indices=seg_indices_for_subtitles)
    _douyin_total_dur = _douyin_timeline[-1]["end"] if _douyin_timeline else len(clip_paths) * duration
    douyin_cfg = get_douyin_config(int(_douyin_total_dur))
    video_height = 1920  # 9:16 1080x1920
    subtitles = optimize_subtitles_for_douyin(subtitles, video_height=video_height)
    bottom_margin_ratio = DOUYIN_CONFIG["subtitle"]["bottom_margin_ratio"]
    print(f"📱 抖音优化：字号放大 + 关键词高亮 + 安全区（底部 {int(bottom_margin_ratio*100)}%）")

    # Q5 修复：字幕动画按叙事类型自动选择，不再所有场景都用同一个动画
    # 口播模式强制 typewriter（字幕与人声同步）
    # 非口播：hook→pop（抓注意力）/ cta→slide（行动感）/ showcase→fade（稳重）
    if voiceover_enabled:
        sub_animation = "typewriter"
        print("📝 口播模式：启用打字机字幕动画（增强同步感）")
    else:
        _dominant_narrative = _success_narratives[0].lower() if _success_narratives else "showcase"
        _hook_set = {"hook", "intro", "opening", "attention"}
        _cta_set  = {"cta", "call_to_action", "outro"}
        _fade_set = {"showcase", "result", "proof", "demo", "reveal", "highlight", "solution"}
        if _dominant_narrative in _hook_set:
            sub_animation = "pop"
        elif _dominant_narrative in _cta_set:
            sub_animation = "slide"
        elif _dominant_narrative in _fade_set:
            sub_animation = "fade"
        else:
            sub_animation = DOUYIN_CONFIG["subtitle"]["animation"]
        print(f"📝 字幕动画：{sub_animation}（叙事类型：{_dominant_narrative}）")

    try:
        add_fancy_subtitles(
            video=graded_path,
            subtitles=subtitles,
            output=subtitled_path,
            font_size=int(video_height * DOUYIN_CONFIG["subtitle"]["font_size_ratio"]),
            primary_color=BRAND_CONFIG.get("primary_color", "#FFFFFF"),
            accent_color=BRAND_CONFIG.get("accent_color", "#FF6B6B"),
            animation=sub_animation,
            bottom_margin_ratio=bottom_margin_ratio,
        )
        print(f"✅ 花字字幕添加完成：{subtitled_path.name}")
    except Exception as e:
        print(f"⚠️  花字字幕添加失败：{e}")
        print("  回退到普通字幕...")
        try:
            add_subtitles_ffmpeg(
                video=graded_path,
                subtitles=subtitles,
                output=subtitled_path,
                bottom_margin=int(video_height * bottom_margin_ratio),
            )
            print(f"✅ 字幕添加完成：{subtitled_path.name}")
        except Exception as e2:
            if strict_mode:
                raise RuntimeError(f"字幕添加失败：{e2}") from e2
            print(f"⚠️  字幕添加失败：{e2}")
            print("  将继续导出无字幕版本...")
            subtitled_path = graded_path

    # 混合口播到视频音轨（BGM 自动闪避）；口播已在字幕烧录前完成严格校验
    if voiceover_enabled:
        try:
            voiced_path = final_dir / f"{output_name}_voiced.mp4"
            _mix_voiceover_with_bgm(
                video=subtitled_path,
                voiceover=voiceover_audio,
                output=voiced_path,
                bgm_ducking_volume=BGM_VOLUME_VOICEOVER,
            )
            subtitled_path = voiced_path
            print(f"✅ 口播混合完成（BGM 基础音量 {BGM_VOLUME_VOICEOVER}，sidechain 闪避）")
        except Exception as e:
            if strict_mode:
                raise RuntimeError(f"口播混合失败：{e}") from e
            print(f"⚠️  口播混合失败：{e}")

    # ============================================================
    # 第五步：添加音效（SFX）
    # ============================================================
    sfx_path = final_dir / f"{output_name}_sfx.mp4"

    try:
        # 节奏模板驱动：转场和段时长从模板取
        transition_dur = scene_cfg.get("transition_duration", 0.6)
        # 从 ad_script 提取叙事类型，按实际成功段过滤
        _all_narratives = [seg.get("narrative", "") for seg in ad_script.get("segments", [])]
        if seg_indices_for_subtitles is not None:
            _narratives = [_all_narratives[i] for i in sorted(seg_indices_for_subtitles) if i < len(_all_narratives)]
            # 按实际成功段顺序构建段时长列表
            _sfx_seg_durs = [_seg_dur_map.get(i, float(duration)) for i in sorted(seg_indices_for_subtitles) if i < len(_all_narratives)]
        else:
            _narratives = _all_narratives[:len(clip_paths)]
            # 按段顺序构建时长列表
            _sfx_seg_durs = [s["duration"] for s in rhythm_template["segments"][:len(clip_paths)]]
        sfx_list = generate_sfx_timings(
            num_clips=len(clip_paths),
            clip_duration=duration,
            transition_duration=transition_dur,
            narratives=_narratives if _narratives else None,
            segment_durations=_sfx_seg_durs,
        )

        # P1-B：帧间差分补充音效（detect_scene_cuts）
        # 用实际合并视频的场景切换点补充 whoosh，比叙事模板更精准
        try:
            from video_merger import detect_scene_cuts as _dsc
            _sc_cuts = _dsc(subtitled_path, threshold=0.35, max_cuts=15)
            _existing_times = {round(s["time"], 1) for s in sfx_list}
            for _cut_t in _sc_cuts:
                # 避免与已有音效时间点重叠（±0.15s 内跳过）
                _too_close = any(abs(_cut_t - _et) < 0.15 for _et in _existing_times)
                if not _too_close:
                    sfx_list.append({"time": _cut_t, "type": "whoosh", "volume": 0.18})
                    _existing_times.add(round(_cut_t, 1))
            sfx_list.sort(key=lambda s: s["time"])
            print(f"   P1-B 帧间差分：检测到 {len(_sc_cuts)} 个场景切换点")
        except Exception as _dsc_err:
            print(f"   P1-B 帧间差分跳过（{_dsc_err}）")

        # 音效时间对齐 BGM 节拍（卡点效果）
        if bgm_file and bgm_file.exists():
            sfx_list = align_sfx_to_beats(sfx_list, bgm_file)
        if sfx_list:
            # P1-5：音效时间点边界保护 —— 过滤超出视频实际时长的音效
            try:
                _sfx_vid_dur = _get_clip_duration(subtitled_path)
                sfx_list = [
                    s for s in sfx_list
                    if s.get("time", 0) < _sfx_vid_dur - 0.1
                ]
            except Exception:
                pass
            add_sfx_to_video(
                video=subtitled_path,
                output=sfx_path,
                sfx_list=sfx_list,
            )
            print(f"✅ 音效添加完成：{len(sfx_list)} 个音效")
        else:
            sfx_path = subtitled_path
    except Exception as e:
        print(f"⚠️  音效添加失败：{e}")
        sfx_path = subtitled_path

    # ============================================================
    # P1-4：第六步：生成封面图（从所有片段选最佳帧，而非固定第 1 段）
    # ============================================================
    cover_path = final_dir / f"{output_name}_cover.jpg"

    try:
        # P1-4：从所有片段中选最佳封面帧（而非固定第 1 段）
        # hook 段通常是近景特写，不一定包含产品全景或最佳视觉帧
        from PIL import Image as _PILImage, ImageFilter as _PILFilter
        import statistics as _stats

        _best_frame_path = None
        _best_score = -1.0
        _sample_ratios = [0.12, 0.25, 0.40, 0.55, 0.70, 0.85]
        # Q4 修复：缓存相邻帧用于计算帧间差（运动模糊惩罚）
        _prev_thumb_data: dict = {}  # key=(ci, ratio_idx-1) -> thumb pixel list

        for _ci, _cclip in enumerate(clip_paths):
            if not _cclip.exists():
                continue
            try:
                _cdur = _get_clip_duration(_cclip)
            except Exception:
                continue
            for _ri, _ratio in enumerate(_sample_ratios):
                _t = _cdur * _ratio
                _cand_path = final_dir / f"{output_name}_cover_c{_ci}_{int(_ratio*100)}.jpg"
                try:
                    extract_frame(_cclip, _cand_path, time_sec=_t)
                    if _cand_path.exists():
                        with _PILImage.open(_cand_path) as _src_img:
                            _img = _src_img.convert("L")
                        _tw = 320
                        _th = int(_img.height * _tw / _img.width)
                        _thumb = _img.resize((_tw, _th), _PILImage.LANCZOS)
                        _pixels = list(_thumb.getdata())
                        _edge_vals = list(_thumb.filter(_PILFilter.FIND_EDGES).getdata())
                        _lap_var = _stats.variance(_edge_vals) if len(_edge_vals) > 1 else 0
                        _contrast = _stats.stdev(_pixels) if _tw * _th > 1 else 0
                        _base_score = _lap_var * _contrast

                        # Q4 修复：帧间差惩罚——与上一候选帧差异过大说明是运动/转场帧
                        # 平均像素差 > 30（满幅 255）时认为是高运动帧，乘以惩罚系数
                        _motion_penalty = 1.0
                        _prev_key = (_ci, _ri - 1)
                        if _prev_key in _prev_thumb_data and _pixels:
                            _prev_pixels = _prev_thumb_data[_prev_key]
                            if len(_prev_pixels) == len(_pixels):
                                _mean_diff = sum(
                                    abs(a - b) for a, b in zip(_pixels, _prev_pixels)
                                ) / len(_pixels)
                                if _mean_diff > 30:
                                    # 差异越大惩罚越重，最多惩罚到 0.3x
                                    _motion_penalty = max(0.3, 1.0 - (_mean_diff - 30) / 100)
                        _prev_thumb_data[(_ci, _ri)] = _pixels

                        _score = _base_score * _motion_penalty

                        # P1-D：肤色语义加权（复用 quality_checker._detect_skin_regions）
                        # 合理肤色区域（面积比 0.05-0.45）→ ×1.3 提权（人物清晰可见）
                        # 异常肤色（>0.5 或 aspect_ratio 超限）→ ×0.5 降权（可能崩坏）
                        try:
                            from quality_checker import _detect_skin_regions as _cov_skin
                            with _PILImage.open(_cand_path) as _src_cov:
                                _cov_img = _src_cov.convert("RGB")
                            if _cov_img.width > 320:
                                _cov_img = _cov_img.resize(
                                    (320, int(_cov_img.height * 320 / _cov_img.width)),
                                    _PILImage.LANCZOS,
                                )
                            _cov_regions = _cov_skin(_cov_img)
                            if _cov_regions:
                                _cov_area = _cov_img.width * _cov_img.height
                                _cov_r = _cov_regions[0]["area"] / _cov_area
                                _cov_ar = _cov_regions[0]["aspect_ratio"]
                                if 0.05 <= _cov_r <= 0.45 and 0.2 <= _cov_ar <= 2.5:
                                    _score *= 1.3  # 人物清晰、肤色合理
                                elif _cov_r > 0.50 or _cov_ar > 2.5 or _cov_ar < 0.2:
                                    _score *= 0.5  # 异常肤色/变形，降权
                        except Exception:
                            pass

                        if _score > _best_score:
                            _best_score = _score
                            _best_frame_path = _cand_path
                except Exception:
                    pass

        # 兜底：回退到第 1 段中间帧
        if _best_frame_path is None or not _best_frame_path.exists():
            _best_frame_path = final_dir / f"{output_name}_cover_base.jpg"
            if clip_paths and clip_paths[0].exists():
                extract_frame(clip_paths[0], _best_frame_path, time_sec=duration / 2)

        mid_frame_path = _best_frame_path

        # 生成封面
        selling_point = product_info.get("selling_point", product_info.get("name", ""))
        product_name = product_info.get("name", "")
        brand_name = BRAND_CONFIG.get("name", "")
        primary_color = BRAND_CONFIG.get("primary_color", "#FF6B6B")

        # 从广告脚本中提取 hook 文案作为封面大标题
        hook_text = ""
        tag_text = ""
        if ad_script and ad_script.get("segments"):
            hook_seg = ad_script["segments"][0]  # 第一段是 hook
            hook_text = hook_seg.get("subtitle", "")
            # 生成顶部标签（增强信任）
            tag_text = "亲测有效"

        # Logo 路径
        logo_path_str = BRAND_CONFIG.get("logo_path", "")
        logo_path = None
        if logo_path_str:
            logo_p = Path(logo_path_str)
            logo_path = logo_p if logo_p.is_absolute() else PROJECT_ROOT / logo_p
            if not logo_path.exists():
                logo_path = None

        generate_cover_image(
            base_image=mid_frame_path,
            output_path=cover_path,
            title=selling_point,
            subtitle=product_name,
            hook_text=hook_text,
            tag_text=tag_text,
            brand_name=brand_name,
            primary_color=primary_color,
            aspect_ratio=aspect_ratio,
            logo_path=logo_path,
        )
        print(f"✅ 封面生成完成：{cover_path.name}")
    except Exception as e:
        print(f"⚠️  封面生成失败：{e}")
        cover_path = None

    # 第七步调色已提前到第四步（拼接后、字幕前）执行，此处不再重复

    # ============================================================
    # 第八步：品牌 Logo 水印
    # ============================================================
    logo_path_str = BRAND_CONFIG.get("logo_path", "")
    logo_cfg = BRAND_CONFIG.get("logo_watermark", {})
    logo_enabled = logo_cfg.get("enabled", False) and logo_path_str

    watermarked_path = final_dir / f"{output_name}_watermarked.mp4"
    if logo_enabled:
        logo_path = Path(logo_path_str)
        if logo_path.is_absolute():
            full_logo_path = logo_path
        else:
            full_logo_path = PROJECT_ROOT / logo_path

        if full_logo_path.exists():
            try:
                print(f"🏷️  添加品牌 Logo 水印...")
                add_logo_watermark(
                    video=sfx_path,
                    output=watermarked_path,
                    logo_path=full_logo_path,
                    position=logo_cfg.get("position", "top_right"),
                    size_ratio=logo_cfg.get("size_ratio", 0.08),
                    margin_ratio=logo_cfg.get("margin_ratio", 0.03),
                    opacity=logo_cfg.get("opacity", 0.9),
                    fade_in=logo_cfg.get("fade_in", 0.5),
                    fade_out=logo_cfg.get("fade_out", 0.5),
                )
                sfx_path = watermarked_path
            except Exception as e:
                print(f"⚠️  Logo 水印添加失败：{e}")
        else:
            print(f"⚠️  Logo 文件不存在：{full_logo_path}，跳过水印")

    # ============================================================
    # P2-A：品牌开场/收尾动画（--brand-intro-outro 启用时）
    # 插在水印之后、最终导出之前
    # ============================================================
    if brand_intro_outro:
        print()
        print("🎬 P2-A 添加品牌开场/收尾动画...")
        _bio_path = final_dir / f"{output_name}_with_brand.mp4"
        try:
            from video_merger import add_brand_intro_outro as _bio_fn
            _bio_result = _bio_fn(
                video=sfx_path,
                output=_bio_path,
                brand_name=BRAND_CONFIG.get("name", ""),
                product_name=product_info.get("name", ""),
                cta_text=BRAND_CONFIG.get("cta_text", "立即体验"),
                primary_color=BRAND_CONFIG.get("primary_color", "#FF6B6B"),
            )
            if _bio_result == _bio_path and _bio_path.exists():
                sfx_path = _bio_path
        except Exception as _bio_err:
            if strict_mode:
                raise RuntimeError(f"品牌开场/收尾生成失败：{_bio_err}") from _bio_err
            print(f"⚠️  品牌动画跳过（{_bio_err}）")

    # ============================================================
    # 导出最终成片
    # ============================================================
    final_path = final_dir / f"{output_name}_final.mp4"

    try:
        # P0-1：消除 trim 的独立重编码
        # auto_trim 已在字幕前提前执行（用 -c copy 流复制），此处直接对 sfx_path 做最终编码。
        # 不再额外调用 auto_trim_video，节省一次完整重编码。
        douyin_video_cfg = DOUYIN_CONFIG
        export_final_video(
            input_video=sfx_path,
            output=final_path,
            resolution=douyin_video_cfg["resolution"],
            fps=douyin_video_cfg["fps"],
            bitrate=douyin_video_cfg["bitrate"],
        )
        print(f"✅ 最终成片已导出：{final_path.name}")

        # #10 修复：输出文件完整性校验（存在 + 大小 + 时长）
        if not final_path.exists():
            raise RuntimeError(f"输出文件不存在：{final_path}")
        file_size = final_path.stat().st_size
        if file_size < 10_000:  # 小于 10KB 视为无效
            raise RuntimeError(f"输出文件过小（{file_size} bytes），疑似空文件：{final_path}")
        try:
            actual_dur = _get_clip_duration(final_path)
            # 按节奏模板计算预期总时长（考虑转场重叠和不等长段）
            _qc_timeline = compute_segment_timeline(rhythm_template, seg_indices=seg_indices_for_subtitles)
            _expected_total = _qc_timeline[-1]["end"] if _qc_timeline else len(clip_paths) * duration
            expected_min = _expected_total * 0.75  # 允许 25% 偏差（转场+裁切误差）
            if actual_dur < expected_min:
                raise RuntimeError(
                    f"输出视频时长异常（实际 {actual_dur:.1f}s，期望至少 {expected_min:.1f}s）"
                )
            print(f"✅ 完整性校验通过：{file_size/1024/1024:.1f} MB，{actual_dur:.1f}s")
        except RuntimeError:
            raise
        except Exception as dur_err:
            print(f"⚠️  时长校验跳过（ffprobe 不可用）：{dur_err}")
    except Exception as e:
        print(f"❌ 最终导出失败：{e}")
        raise RuntimeError("最终导出失败，已阻断中间文件被标记为成功成片") from e

    # ============================================================
    # 双版本输出（可选）
    # ============================================================
    wide_path = None
    if dual_output and aspect_ratio == "9:16":
        print()
        print("=" * 60)
        print("[+] 生成 16:9 版本...")
        print("=" * 60)

        wide_path = final_dir / f"{output_name}_16x9_final.mp4"
        try:
            convert_to_aspect_ratio(
                input_video=final_path,
                output=wide_path,
                target_aspect_ratio="16:9",
            )
            print(f"✅ 16:9 版本已生成：{wide_path.name}")
        except Exception as e:
            print(f"⚠️ 16:9 版本生成失败：{e}")
            raise RuntimeError("已请求 dual_output，但 16:9 版本生成失败") from e

    # ============================================================
    # 发布级质量门禁：放在 pipeline 内部，保证单条和批量入口都执行
    # ============================================================
    quality_result = None
    if not preview:
        print()
        print("🔍 开始发布级视频质量检测...")
        quality_result = check_video_quality(
            final_path,
            num_frames=15,
            content_focus="center" if product_image_path else "default",
            require_audio=True,
            product_reference_image=product_image_path if product_image_path else None,
            character_reference_image=main_char_path if main_char_path else None,
            require_semantic_alignment=True,
        )
        print_quality_report(quality_result, final_path.name)
        if not quality_result.passed:
            raise RuntimeError("最终成片质量检测未通过，已阻断输出为成功产物")
        if wide_path:
            print()
            print("🔍 开始 16:9 版本发布级质量检测...")
            wide_quality_result = check_video_quality(
                wide_path,
                num_frames=15,
                content_focus="center" if product_image_path else "default",
                require_audio=True,
                product_reference_image=product_image_path if product_image_path else None,
                character_reference_image=main_char_path if main_char_path else None,
                require_semantic_alignment=True,
            )
            print_quality_report(wide_quality_result, wide_path.name)
            if not wide_quality_result.passed:
                raise RuntimeError("16:9 成片质量检测未通过，已阻断输出为成功产物")

    # ============================================================
    # 清理中间文件（只保留 _final.mp4 / _16x9_final.mp4 / _cover.jpg）
    # ============================================================
    _cleanup_intermediate_files(final_dir, output_name, final_path, wide_path)

    return {
        "final_path": final_path,
        "wide_path": wide_path,
        "output_name": output_name,
        "ad_script": ad_script,
        "bgm_file": bgm_file,
        # P2：失败感知字段，让调用方知道口播/封面是否成功
        "voiceover_enabled": voiceover_enabled,
        "cover_path": cover_path,  # None 表示封面生成失败
        # P0-B：跨片段一致性警告列表（空列表 = 无问题）
        "consistency_warnings": locals().get("_consistency_warnings", []),
        "quality_result": quality_result,
    }


def run_one_click_create(
    product_info: dict,
    args: argparse.Namespace,
    output_name: str = None,
    output_dir: Path = OUTPUT_DIR,
    characters: Optional[list] = None,
    output_name_suffix: str = None,
) -> Path:
    """
    核心：执行一键成片全流程

    Args:
        product_info: 产品信息字典
        args: 命令行参数（包含 style/duration/mode/aspect_ratio）
        output_name: 输出文件名前缀（可选）
        output_dir: 输出根目录，默认 OUTPUT_DIR
        characters: 角色列表（可选，每个元素包含 name/description/image_path）
        output_name_suffix: 输出文件名后缀（用于 A/B 多版本）

    Returns:
        最终成片路径

    Raises:
        RuntimeError: 任何步骤失败时抛出异常
    """
    if output_name is None:
        if getattr(args, "output_name", None):
            output_name = _safe_output_stem(str(args.output_name))
        elif getattr(args, "resume", False):
            output_name = build_stable_output_name(product_info, args)
            print(f"🔁 断点续跑输出名：{output_name}")
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_name = _safe_output_stem(product_info.get("name", "product"))
            output_name = f"{safe_name}_{timestamp}"

    if output_name_suffix:
        output_name = f"{output_name}_{output_name_suffix}"

    product_image = args.product_image if getattr(args, "product_image", None) and _is_http_url(args.product_image) else (
        Path(args.product_image) if getattr(args, "product_image", None) else None
    )
    hook_type = getattr(args, "hook", DEFAULT_HOOK_TYPE)
    use_voiceover = getattr(args, "voiceover", False)
    voiceover_style = getattr(args, "voiceover_style", DEFAULT_VOICEOVER_STYLE)
    voice = getattr(args, "voice", DEFAULT_VOICE)
    script_style = getattr(args, "script_style", DEFAULT_SCRIPT_STYLE)

    # 目标总时长适配：通过节奏模板动态调整每段时长
    target_duration = getattr(args, "target_duration", None)
    rhythm_style = getattr(args, "rhythm_style", "moderate")
    # 传给可灵 API 的基础生成时长（统一生成 5s，后期裁切到节奏目标时长）
    actual_duration = args.duration
    if target_duration:
        print(f"⏱️  目标总时长：{target_duration}s，节奏风格：{rhythm_style}")
        print(f"   生成策略：可灵生成 {actual_duration}s 片段，后期裁切适配节奏模板")

    try:
        result = run_generation_pipeline(
            product_info=product_info,
            style=args.style,
            duration=actual_duration,
            mode=args.mode,
            aspect_ratio=args.aspect_ratio,
            output_name=output_name,
            dual_output=args.dual_output,
            product_image=product_image,
            allow_no_product_image=getattr(args, "allow_no_product_image", False),
            image_fidelity=getattr(args, "image_fidelity", DEFAULT_IMAGE_FIDELITY),
            human_fidelity=getattr(args, "human_fidelity", DEFAULT_HUMAN_FIDELITY),
            seed=getattr(args, "seed", None),
            characters=characters,
            output_dir=output_dir,
            hook_type=hook_type,
            use_voiceover=use_voiceover,
            voiceover_style=voiceover_style,
            voice=voice,
            script_style=script_style,
            strict_mode=getattr(args, "strict", True),
            force=getattr(args, "force", False),
            target_duration=target_duration,
            rhythm_style=rhythm_style,
            parallel=not getattr(args, "serial", False),
            min_clips=getattr(args, "min_clips", 3),
            best_of=getattr(args, "best_of", 2),
            quality_frames=getattr(args, "quality_frames", 12),
            keep_candidates=getattr(args, "keep_candidates", False),
            preview=getattr(args, "preview", False),
            max_workers=getattr(args, "max_workers", 4),
            stabilize=getattr(args, "stabilize", True),
            brand_intro_outro=getattr(args, "brand_intro_outro", False),
            kling_model=getattr(args, "kling_model", None),
            multi_shot=getattr(args, "multi_shot", False),
        )

        final_path = result["final_path"]
        print()
        print("=" * 60)
        print("🎉 一键成片完成！")
        print("=" * 60)
        print(f"📁 输出目录：{output_dir / 'final'}")
        print(f"🎬 最终成片：{final_path.name}")
        print(f"📊 文件大小：{final_path.stat().st_size / 1024 / 1024:.1f} MB")
        if result["wide_path"] and result["wide_path"].exists():
            print(f"🖥️ 16:9 版本：{result['wide_path'].name}")
        print()

        # 预览模式：跳过质量检测和发布文案，直接返回
        if result.get("preview"):
            print("⚡ 预览模式完成，跳过后期质量检测和发布文案生成")
            return final_path

        # 保存发布文案（标题 + 话题标签 + 脚本概要）
        ad_script = result.get("ad_script")
        if ad_script:
            caption_path = output_dir / "final" / f"{output_name}_发布文案.txt"
            title_options = generate_title_options(product_info, num_options=5)
            hashtag_options = generate_hashtag_options(product_info, num_options=3)

            with open(caption_path, "w", encoding="utf-8") as f:
                f.write(f"{'='*50}\n")
                f.write(f"📝 {product_info.get('name', '产品')} - 抖音发布文案\n")
                f.write(f"{'='*50}\n\n")

                f.write(f"【推荐标题】\n")
                f.write(f"{ad_script['title']}\n\n")

                f.write(f"【标题备选（{len(title_options)}个）】\n")
                for i, t in enumerate(title_options, 1):
                    f.write(f"  {i}. {t}\n")
                f.write("\n")

                f.write(f"【推荐话题标签】\n")
                f.write(f"{' '.join(ad_script['hashtags'])}\n\n")

                f.write(f"【话题标签备选（{len(hashtag_options)}组）】\n")
                for i, tags in enumerate(hashtag_options, 1):
                    f.write(f"  方案{i}: {' '.join(tags)}\n")
                f.write("\n")

                f.write(f"【脚本概要】\n")
                f.write(f"  脚本风格：{SCRIPT_STYLES.get(script_style, {}).get('name', script_style)}\n")
                for seg in ad_script["segments"]:
                    f.write(f"  [{seg['segment']+1}] {seg['narrative']}: {seg['subtitle']}\n")
                f.write("\n")

                # P0-B：跨片段一致性警告
                _cw = result.get("consistency_warnings", [])
                if _cw:
                    f.write(f"【⚠️  跨片段一致性警告（{len(_cw)} 条）】\n")
                    for _cwi, _cwmsg in enumerate(_cw, 1):
                        f.write(f"  {_cwi}. {_cwmsg}\n")
                    f.write("  建议：人工检查上述片段的人物/产品构图是否连贯\n")
                    f.write("\n")

                # BGM 版权信息
                bgm_info = get_bgm_copyright_info(result.get("bgm_file"))
                f.write(f"【BGM 信息】\n")
                f.write(f"  来源：{bgm_info['source']}\n")
                f.write(f"  标题：{bgm_info['title']}\n")
                f.write(f"  ⚠️  {bgm_info['warning']}\n")
                f.write("\n")

                f.write(f"{'='*50}\n")
                f.write(f"💡 提示：\n")
                f.write(f"   1. 标题控制在 20-30 字最佳\n")
                f.write(f"   2. 话题标签 5-7 个为宜\n")
                f.write(f"   3. 发布前建议人工复核合规性\n")
                f.write(f"   4. BGM 版权需自行核实，商用请确认授权\n")
                f.write(f"{'='*50}\n")

            print(f"📄 发布文案已保存：{caption_path.name}")
            print(f"   （含标题备选 + 话题标签 + 脚本概要）")

        if result.get("quality_result") and result["quality_result"].passed:
            print("\n✅ 视频质量检测通过")

        return final_path

    except Exception as e:
        print(f"\n❌ 生成失败：{e}")
        print(f"   💡 中间文件已保留在 {output_dir} 下，可用于排查问题")
        print(f"   输出目录：{output_dir / 'clips' /  'character_ref' / 'final'}")
        raise


def expand_theme_with_llm(theme: str, args) -> Optional[dict]:
    """
    主题模式核心：将一句主题描述交给 LLM 展开为完整的 product_info + args 参数包。

    返回格式：
        {
            "product_info": { name, type, selling_point, audience, style, age, gender, outfit },
            "args": { style, script_style, hook, rhythm_style, target_duration, voiceover, voice }
        }
    或 None（LLM 不可用 / 调用失败）。
    """
    from config import LLM_ENABLED
    if not LLM_ENABLED:
        return None

    try:
        from llm_client import generate_json
    except ImportError:
        return None

    system_prompt = """你是专业的短视频广告策划专家。用户会给你一句产品主题描述，
你需要根据主题自动推断出最适合的广告参数，以严格的 JSON 格式输出，不要有任何额外说明。

输出字段说明：
product_info:
  name: 产品名称（简洁，2-8字）
  type: 产品类型，从以下选择：美妆 食品 科技 服装 app 家居 健康 母婴 宠物 运动 default
  selling_point: 核心卖点（一句话，10-25字，突出用户利益）
  audience: 目标人群（如 18-30岁都市女性）
  style: 广告风格（如 温暖治愈、科技感、青春活力、极简高端）
  age: 主角年龄（数字，如 25）
  gender: 主角性别（女 或 男）
  outfit: 服装描述（英文，符合场景和受众，如 casual cozy sweater）

args:
  style: 电影风格，从以下选一个最合适的：hitchcock kubrick spielberg aronofsky scorsese nolan anderson wong-kar-wai tarkovsky zhang-yimou koreeda tarantino jia-zhangke hou-hsiao-hsien bong-joon-ho denis-villeneuve luc-besson miyazaki
  script_style: 脚本风格，从以下选一个：pain_point_solution before_after storytelling demonstration social_proof
  hook: 开场钩子，从以下选一个：question shocking before_after demonstration story challenge celeb_style pain_point
  rhythm_style: 节奏风格，从以下选一个：fast moderate cinematic
  target_duration: 目标总时长（秒，整数，建议 15/25/30/60 之一）
  voiceover: 是否需要口播旁白（true 或 false）
  voice: 音色，从以下选一个：female_young female_warm male_pro male_magnetic energetic_female

选择原则：
- 温暖/情感类产品 → miyazaki / wong-kar-wai + storytelling + story
- 科技/功能类产品 → nolan / denis-villeneuve + demonstration + shocking
- 美妆/时尚类产品 → luc-besson / zhang-yimou + pain_point_solution + pain_point
- 食品/生活类产品 → spielberg / koreeda + before_after + question
- 快节奏产品 → rhythm_style=fast；沉浸式产品 → rhythm_style=cinematic
- 需要讲解的功能性产品开启 voiceover=true，纯视觉类 voiceover=false"""

    prompt = f"请根据以下主题展开广告参数：\n\n{theme}"

    result = generate_json(prompt, system_prompt=system_prompt)
    if not result or "product_info" not in result:
        return None
    return result


def main():
    """主函数：一键成片"""
    args = parse_args()

    # --no-llm：禁用 LLM，强制走模板模式
    if args.no_llm:
        import config
        config.LLM_ENABLED = False
        print("🔧  已禁用 LLM 文案生成，使用模板模式")

    # 如果指定了 --list-styles，列出所有风格后退出
    if args.list_styles:
        print("=" * 80)
        print("🎬 可灵 AI 抖音广告视频 - 电影风格卡片库")
        print("=" * 80)
        print()
        print(f"共 {len(CINEMATIC_STYLES)} 种风格，使用方式：python one_click_create.py --style <风格名>")
        print()

        # 按中文名排序
        sorted_styles = sorted(CINEMATIC_STYLES.items(), key=lambda x: x[1]["name"])

        for key, style in sorted_styles:
            print(f"┌─ {style['name']} ({style['name_en']}) ─────────────")
            print(f"│  {style['description']}")
            print(f"│  运镜：")
            print(f"│    推进：{style['camera_push'][:80]}...")
            print(f"│    拉远：{style['camera_pull'][:80]}...")
            print(f"│    环绕：{style['camera_orbit'][:80]}...")
            print(f"│  转场：{style['transition_match'][:80]}...")
            print(f"│  光线：{style['lighting'][:80]}...")
            print(f"│  色调：{style['color'][:80]}...")
            print(f"│  情绪：{style['mood'][:80]}...")
            print(f"│")
            print(f"│  用法：python one_click_create.py --style {key}")
            print(f"└{'─' * 60}")
            print()

        sys.exit(0)

    if args.list_hooks:
        print("=" * 80)
        print("🪝 可灵 AI 抖音广告视频 - 钩子模板库")
        print("=" * 80)
        print()
        print(f"共 {len(HOOK_TEMPLATES)} 种钩子，使用方式：python one_click_create.py --hook <钩子名>")
        print()

        for key, hook in HOOK_TEMPLATES.items():
            best_for = "、".join(hook.get("best_for", []))
            print(f"┌─ {hook['name']} ({key}) ─────────────")
            print(f"│  {hook['description']}")
            print(f"│  开头文案：{hook['hook_subtitle']}")
            print(f"│  适用品类：{best_for}")
            print(f"│  情绪基调：{hook['tone']}")
            print(f"│")
            print(f"│  用法：python one_click_create.py --hook {key}")
            print(f"└{'─' * 60}")
            print()

        sys.exit(0)

    if args.list_voices:
        print("=" * 80)
        print("🎤 可灵 AI 抖音广告视频 - 音色预设库")
        print("=" * 80)
        print()
        print(f"共 {len(VOICE_PRESETS)} 种音色，使用方式：python one_click_create.py --voiceover --voice <音色名>")
        print()

        for key, voice in VOICE_PRESETS.items():
            print(f"┌─ {voice['name']} ({key}) ─────────────")
            print(f"│  系统语音：{voice['voice']}")
            print(f"│  语速：{voice['rate']} 词/分钟")
            print(f"│  音调：{voice['pitch']}")
            print(f"│")
            print(f"│  用法：python one_click_create.py --voiceover --voice {key}")
            print(f"└{'─' * 60}")
            print()

        print("\n🎬 口播风格：")
        for key, style in VOICEOVER_TEMPLATES.items():
            print(f"  - {key}: {style['name']}")
        print(f"\n用法：python one_click_create.py --voiceover --voiceover-style energetic")

        sys.exit(0)

    if args.list_script_styles:
        print("=" * 80)
        print("📝 可灵 AI 抖音广告视频 - 广告脚本风格库")
        print("=" * 80)
        print()
        print(f"共 {len(SCRIPT_STYLES)} 种脚本风格，使用方式：python one_click_create.py --script-style <风格名>")
        print()

        for key, style in SCRIPT_STYLES.items():
            print(f"┌─ {style['name']} ({key}) ─────────────")
            print(f"│  {style['description']}")
            print(f"│")
            print(f"│  用法：python one_click_create.py --script-style {key}")
            print(f"└{'─' * 60}")
            print()

        sys.exit(0)

    # 如果指定了 --load，直接从模板加载
    if args.load:
        template_path = Path(args.load)
        if not template_path.exists():
            print(f"❌ 错误：模板文件不存在：{template_path}")
            sys.exit(1)

        print("=" * 60)
        print("🎬 可灵 AI 抖音广告视频 - 一键成片（模板模式）")
        print("=" * 60)
        print(f"📄 加载模板：{template_path}")
        print()

        product_info, args_dict = load_template(template_path)
        args.style = args_dict.get("style", DEFAULT_CINEMATIC_STYLE)
        args.duration = args_dict.get("duration", DEFAULT_VIDEO_DURATION)
        args.mode = args_dict.get("mode", DEFAULT_MODE)
        args.aspect_ratio = args_dict.get("aspect_ratio", DEFAULT_ASPECT_RATIO)
        args.dual_output = args_dict.get("dual_output", False)
        args.image_fidelity = args_dict.get("image_fidelity", DEFAULT_IMAGE_FIDELITY)
        args.human_fidelity = args_dict.get("human_fidelity", DEFAULT_HUMAN_FIDELITY)
        args.seed = args_dict.get("seed", None)
        args.product_image = args_dict.get("product_image", None)
        # P1 修复：补全模板参数透传（之前只存了 9 个基础参数）
        if "hook_type" in args_dict:
            args.hook = args_dict["hook_type"]
        if "script_style" in args_dict:
            args.script_style = args_dict["script_style"]
        if "use_voiceover" in args_dict:
            args.voiceover = args_dict["use_voiceover"]
        if "voice" in args_dict:
            args.voice = args_dict["voice"]
        if "rhythm_style" in args_dict:
            args.rhythm_style = args_dict["rhythm_style"]
        if "target_duration" in args_dict:
            args.target_duration = args_dict["target_duration"]
        if "preview" in args_dict:
            args.preview = args_dict["preview"]
        if "parallel" in args_dict:
            args.serial = not args_dict["parallel"]
        if "min_clips" in args_dict:
            args.min_clips = args_dict["min_clips"]
        if "best_of" in args_dict:
            args.best_of = args_dict["best_of"]
        if "quality_frames" in args_dict:
            args.quality_frames = args_dict["quality_frames"]
        if "keep_candidates" in args_dict:
            args.keep_candidates = args_dict["keep_candidates"]
        if "max_workers" in args_dict:
            args.max_workers = args_dict["max_workers"]
        if "stabilize" in args_dict:
            args.stabilize = args_dict["stabilize"]
        if "brand_intro_outro" in args_dict:
            args.brand_intro_outro = args_dict["brand_intro_outro"]
        if "kling_model" in args_dict:
            args.kling_model = args_dict["kling_model"]
        if "multi_shot" in args_dict:
            args.multi_shot = args_dict["multi_shot"]
        if "strict_mode" in args_dict:
            args.strict = args_dict["strict_mode"]
        if "force" in args_dict:
            args.force = args_dict["force"]
        if "no_llm" in args_dict:
            args.no_llm = args_dict["no_llm"]
        if "output_name" in args_dict:
            args.output_name = args_dict["output_name"]
        if "resume" in args_dict:
            args.resume = args_dict["resume"]
        if "allow_no_product_image" in args_dict:
            args.allow_no_product_image = args_dict["allow_no_product_image"]

        print("📋 已加载的参数：")
        for k, v in product_info.items():
            print(f"  {k}: {v}")
        print()
        print(f"🎥 电影风格：{args.style}")
        print(f"⏱️ 片段时长：{args.duration}s")
        print(f"🎞️ 生成模式：{args.mode}")
        if args.seed is not None:
            print(f"🌱 随机种子：{args.seed}")
        if args.product_image:
            print(f"🖼️ 商品参考图：{args.product_image}")
        print()

    else:
        # 交互式输入产品信息
        print("=" * 60)
        print("🎬 可灵 AI 抖音广告视频 - 一键成片")
        print("=" * 60)
        print()

        # 显示电影风格
        if args.style != DEFAULT_CINEMATIC_STYLE:
            style_info = CINEMATIC_STYLES.get(args.style, {})
            print(f"🎥 电影风格：{style_info.get('name', args.style)}")
            print(f"   {style_info.get('description', '')}")
            print()

        # 检查环境
        # P0-1 修复：同时支持 API Key 和 JWT（AccessKey+SecretKey）两种鉴权方式
        _has_api_key = bool(KLING_API_KEY and KLING_API_KEY not in ("your_kling_api_key_here", ""))
        _has_jwt = bool(
            KLING_ACCESS_KEY and KLING_SECRET_KEY
            and KLING_ACCESS_KEY not in ("your_access_key_here", "")
            and KLING_SECRET_KEY not in ("your_secret_key_here", "")
        )
        if not _has_api_key and not _has_jwt:
            print("❌ 错误：未配置可灵 API 鉴权")
            print("  请在 .env 或 config.py 中配置以下任意一种：")
            print("  方式一（推荐）：KLING_ACCESS_KEY=ak-xxx 和 KLING_SECRET_KEY=sk-xxx")
            print("  方式二（兼容）：KLING_API_KEY=your_api_key_here")
            sys.exit(1)

        if not check_ffmpeg():
            print("❌ 错误：未安装 ffmpeg")
            print("  请先安装：brew install ffmpeg")
            sys.exit(1)

        ensure_dirs()
        print("✅ 环境检查通过")
        print()

        # ── 主题模式（默认）vs 手动模式（--manual）──────────────────
        _use_manual = getattr(args, "manual", False)

        if not _use_manual:
            print("💡 主题模式：输入一句话描述你的产品，其余参数由 AI 自动决定")
            print("   （例：一款帮助上班族缓解颈椎疼痛的按摩枕）")
            print("   输入 'm' 切换到手动填写模式")
            print()
            _theme_input = input("请输入产品主题：").strip()
            if _theme_input.lower() == "m":
                _use_manual = True
            elif not _theme_input:
                print("⚠️  未输入主题，切换到手动模式")
                _use_manual = True

        if not _use_manual:
            print()
            print("🤖 AI 正在解析主题，生成最佳参数配置...")
            _expanded = expand_theme_with_llm(_theme_input, args)
            if _expanded is None:
                print("⚠️  LLM 不可用（未配置或调用失败），切换到手动填写模式")
                print("   提示：在 config.py 中配置 LLM_API_KEY 和 LLM_BASE_URL 以启用主题模式")
                print()
                _use_manual = True
            else:
                product_info = _expanded["product_info"]
                # 将 LLM 推荐的 args 参数回写到 args 对象
                _llm_args = _expanded.get("args", {})
                _VALID_STYLES = {
                    "hitchcock", "kubrick", "spielberg", "aronofsky", "scorsese",
                    "nolan", "anderson", "wong-kar-wai", "tarkovsky", "zhang-yimou",
                    "koreeda", "tarantino", "jia-zhangke", "hou-hsiao-hsien",
                    "bong-joon-ho", "denis-villeneuve", "luc-besson", "miyazaki",
                }
                _VALID_SCRIPT_STYLES = {
                    "pain_point_solution", "before_after", "storytelling",
                    "demonstration", "social_proof",
                }
                _VALID_HOOKS = {
                    "question", "shocking", "before_after", "demonstration",
                    "story", "challenge", "celeb_style", "pain_point",
                }
                _VALID_RHYTHMS = {"fast", "moderate", "cinematic"}
                _VALID_VOICES = {
                    "female_young", "female_warm", "male_pro",
                    "male_magnetic", "energetic_female",
                }
                if _llm_args.get("style") in _VALID_STYLES:
                    args.style = _llm_args["style"]
                if _llm_args.get("script_style") in _VALID_SCRIPT_STYLES:
                    args.script_style = _llm_args["script_style"]
                if _llm_args.get("hook") in _VALID_HOOKS:
                    args.hook = _llm_args["hook"]
                if _llm_args.get("rhythm_style") in _VALID_RHYTHMS:
                    args.rhythm_style = _llm_args["rhythm_style"]
                if isinstance(_llm_args.get("target_duration"), int):
                    args.target_duration = _llm_args["target_duration"]
                if isinstance(_llm_args.get("voiceover"), bool):
                    args.voiceover = _llm_args["voiceover"]
                if _llm_args.get("voice") in _VALID_VOICES:
                    args.voice = _llm_args["voice"]
                print("✅ AI 参数配置完成")
                # 主题模式下询问商品参考图（可选）
                print()
                _img_input = input("🖼️  商品参考图路径或 URL（直接回车跳过）：").strip()
                if _img_input:
                    args.product_image = _img_input
                else:
                    args.allow_no_product_image = True

        if _use_manual:
            print("请输入产品信息（直接回车使用默认值）：")
            print()
            product_name = input_with_default("产品名称", "我的产品")
            product_type = input_with_default("产品类型（美妆/食品/科技/服装/app）", "default")
            selling_point = input_with_default("核心卖点", "卓越品质，值得拥有")
            audience = input_with_default("目标人群", "18-35岁")
            style = input_with_default("广告风格", "现代简约")
            character_age = input_with_default("角色年龄", "25")
            character_gender = input_with_default("角色性别（女/男）", "女")
            outfit = input_with_default("服装描述", "casual everyday clothes")
            product_info = {
                "name": product_name,
                "type": product_type,
                "selling_point": selling_point,
                "audience": audience,
                "style": style,
                "age": character_age,
                "gender": character_gender,
                "outfit": outfit,
            }

        print()
        print("=" * 60)
        print("📋 产品信息确认")
        print("=" * 60)
        for k, v in product_info.items():
            print(f"  {k}: {v}")
        print()

        # 成本估算提示
        ab_count = max(1, min(getattr(args, "ab_versions", 1), 3))
        _preview_mode = getattr(args, "preview", False)
        _est_mode = "std" if _preview_mode else args.mode
        _est_clips = 1 if _preview_mode else 5
        # P2 修复：从 args 动态读取角色数，不应硬编码为 1
        _est_num_chars = len(getattr(args, "characters", None) or []) or 1
        cost_info = estimate_cost(
            mode=_est_mode,
            duration_per_clip=args.duration,
            num_clips=_est_clips,
            num_characters=_est_num_chars,
            ab_versions=ab_count,
            best_of=getattr(args, "best_of", 1),
        )
        if _preview_mode:
            print("⚡ 预览模式：使用 std 模式，仅生成 1 段快速试错")
        print_cost_estimate(cost_info)

        # 文件大小预估
        from douyin_adapter import DOUYIN_CONFIG
        est_size = estimate_file_size(
            duration=getattr(args, "target_duration", None) or 25,
            bitrate=DOUYIN_CONFIG["bitrate"],
            audio_bitrate=DOUYIN_CONFIG.get("audio_bitrate", "160k"),
        )
        print(f"📦 预估文件大小：约 {est_size['total_size_mb']:.1f} MB")
        if est_size["warning"]:
            print(f"   ⚠️  {est_size['warning']}")
        print()

        confirm = input("确认开始生成？(y/n) [y]：").strip().lower()
        if confirm and confirm != "y":
            print("已取消")
            sys.exit(0)

    # ── 公共参数校验（交互模式 + 模板模式共用）──────────────────────
    if not product_info.get("name", "").strip():
        print("❌ 错误：产品名称不能为空")
        sys.exit(1)
    if not (3 <= args.duration <= 10):
        print(f"❌ 错误：--duration 必须在 3-10 秒之间（当前：{args.duration}）")
        sys.exit(1)
    if getattr(args, "best_of", 1) < 1:
        print(f"❌ 错误：--best-of 必须 ≥ 1（当前：{args.best_of}）")
        sys.exit(1)
    # ───────────────────────────────────────────────────────────────

    # 如果指定了 --save，保存当前配置模板后退出
    if args.save:
        try:
            save_template(product_info, args, Path(args.save))
            print(f"✅ 模板已保存到：{args.save}")
        except Exception as e:
            print(f"❌ 模板保存失败：{e}")
            sys.exit(1)
        sys.exit(0)

    # 执行核心流程
    try:
        ab_count = max(1, min(getattr(args, "ab_versions", 1), 3))  # 限制 1-3 个版本

        if ab_count == 1:
            run_one_click_create(product_info, args)
        else:
            # P2-B：A/B 多版本生成，支持 hook / style / script 三个维度
            import random

            ab_dim = getattr(args, "ab_dim", None) or "script"

            # ── 各维度变体候选 ──
            if ab_dim == "hook":
                from ad_script import HOOK_TYPES
                all_variants = list(HOOK_TYPES.keys()) if hasattr(__import__("ad_script"), "HOOK_TYPES") else [
                    "question", "shocking", "before_after", "demonstration",
                    "story", "challenge", "pain_point",
                ]
                # Bug7 修复：args 中 hook 维度字段名是 args.hook（parse_args 定义），不是 args.hook_type
                base_variant = getattr(args, "hook", DEFAULT_HOOK_TYPE)
                _dim_label = "hook 类型"
            elif ab_dim == "style":
                from config import CINEMATIC_STYLES
                all_variants = list(CINEMATIC_STYLES.keys()) if "CINEMATIC_STYLES" in dir(__import__("config")) else [
                    "warm_cinematic", "cool_cinematic", "vintage", "moody",
                ]
                base_variant = getattr(args, "style", "warm_cinematic")
                _dim_label = "电影风格"
            else:  # script（默认）
                all_variants = list(SCRIPT_STYLES.keys())
                base_variant = args.script_style
                _dim_label = "脚本风格"

            selected_variants = [base_variant]
            remaining = [v for v in all_variants if v != base_variant]
            random.shuffle(remaining)
            selected_variants.extend(remaining[:ab_count - 1])

            print("=" * 60)
            print(f"🔬 A/B 测试模式（维度：{_dim_label}）：将生成 {len(selected_variants)} 个版本")
            print(f"   变体列表：{', '.join(selected_variants)}")
            print("=" * 60)

            results = []

            for i, variant in enumerate(selected_variants, 1):
                version_label = f"v{i}_{ab_dim}_{variant}"
                print(f"\n\n{'='*60}")
                print(f"🎬 版本 {i}/{len(selected_variants)}（{_dim_label}：{variant}）")
                print(f"{'='*60}")

                # 临时修改对应维度参数
                # Bug7 修复：hook 维度字段名是 args.hook，与 parse_args 保持一致
                if ab_dim == "hook":
                    _orig = getattr(args, "hook", DEFAULT_HOOK_TYPE)
                    args.hook = variant
                elif ab_dim == "style":
                    _orig = args.style
                    args.style = variant
                else:
                    _orig = args.script_style
                    args.script_style = variant

                try:
                    final_path = run_one_click_create(
                        product_info,
                        args,
                        output_name_suffix=version_label,
                    )
                    results.append({
                        "version": i,
                        "dim": ab_dim,
                        "variant": variant,
                        "path": final_path,
                    })
                finally:
                    # 还原参数（Bug7 修复：hook 字段名与上方保持一致）
                    if ab_dim == "hook":
                        args.hook = _orig
                    elif ab_dim == "style":
                        args.style = _orig
                    else:
                        args.script_style = _orig

            # 汇总结果
            print("\n\n" + "=" * 60)
            print("🏆 A/B 测试生成完成！")
            print("=" * 60)
            print(f"   测试维度：{_dim_label}")
            for r in results:
                print(f"  版本 {r['version']}（{r['variant']}）→ {r['path'].name}")
            print(f"\n共生成 {len(results)} 个版本，挑最喜欢的发吧！")

    except Exception as e:
        print(f"\n❌ 生成失败：{e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
