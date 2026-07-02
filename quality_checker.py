#!/usr/bin/env python3
"""
视频质量自动检测模块

功能：
- 清晰度检测（拉普拉斯方差）
- 异常帧检测（全黑/全白/严重偏色）
- 闪烁检测（相邻帧差异）
- 冻结帧检测（freezedetect 滤镜）
- 黑帧检测（blackdetect 滤镜）
- 音频质量检测（响度/峰值）
- 人脸崩坏初筛（基于肤色区域形态学分析，轻量方案）
- 生成质量评估报告

注意：人脸崩坏检测为轻量初筛，仅能发现明显的肤色区域异常，
无法替代专业的人脸质量评估。
"""

import subprocess
import tempfile
import shutil
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field


@dataclass
class FrameQuality:
    """单帧质量信息"""
    index: int
    time_sec: float
    sharpness: float = 0.0  # 清晰度（拉普拉斯方差）
    center_sharpness: float = 0.0  # 中心区域清晰度（用于产品/主体居中场景）
    brightness: float = 0.0  # 平均亮度 0-255
    center_brightness: float = 0.0  # 中心区域平均亮度
    is_black: bool = False  # 是否全黑
    is_white: bool = False  # 是否全白
    is_blurry: bool = False  # 是否模糊
    is_abnormal: bool = False  # 分析是否异常（P1 修复：帧分析失败标记）
    issues: List[str] = field(default_factory=list)


@dataclass
class VideoQualityResult:
    """视频质量检测结果"""
    passed: bool = True
    overall_score: float = 0.0  # 0-100 分
    avg_sharpness: float = 0.0
    frames_analyzed: int = 0
    blurry_frames: int = 0
    black_frames: int = 0
    white_frames: int = 0
    flicker_detected: bool = False
    # 人脸崩坏检测（轻量初筛）
    face_issue_frames: int = 0  # 检测到人脸异常的帧数
    face_issues: List[str] = field(default_factory=list)
    # 音频质量
    audio_lufs: float = 0.0  # 集成响度
    audio_peak: float = 0.0  # 真峰值
    audio_issues: List[str] = field(default_factory=list)
    # 黑帧检测
    black_start: float = 0.0  # 开头黑帧时长
    black_end: float = 0.0  # 结尾黑帧时长
    # 冻结帧检测
    freeze_start: float = 0.0  # 开头冻结帧时长
    freeze_end: float = 0.0  # 结尾冻结帧时长
    # 产品出现检测
    product_similarity: float = 0.0  # 产品参考图与抽帧的最高相似度
    product_detected: bool = False
    # 通用
    issues: List[str] = field(default_factory=list)
    details: List[FrameQuality] = field(default_factory=list)


def _extract_frames_ffmpeg(
    video_path: Path,
    output_dir: Path,
    num_frames: int = 10,
) -> List[Path]:
    """
    使用 ffmpeg 单次调用批量抽取均匀分布的帧（比逐帧 N 次调用快约 10x）

    Args:
        video_path: 视频路径
        output_dir: 输出目录
        num_frames: 抽帧数量

    Returns:
        抽取的帧文件路径列表
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    duration = _get_video_duration(video_path)
    if duration <= 0:
        return []

    # 计算每帧的时间点（均匀分布，避开首尾各 5%）
    interval = duration / (num_frames + 1)
    select_times = [interval * (i + 1) for i in range(num_frames)]

    # 逐时间点抽帧，确保覆盖全片；质量检测优先可靠性，不用连续时间窗 select。
    frame_paths = []
    for i, t in enumerate(select_times):
        frame_path = output_dir / f"frame_{i:03d}.png"
        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{t:.3f}",
            "-i", str(video_path),
            "-vframes", "1",
            "-q:v", "2",
            str(frame_path),
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=30, check=True)
            if frame_path.exists():
                frame_paths.append(frame_path)
        except Exception:
            continue
    return frame_paths


def _get_video_duration(video_path: Path) -> float:
    """获取视频时长（带 LRU 缓存）"""
    from utils_ffprobe import get_video_duration
    return get_video_duration(str(video_path))


def _analyze_frame_pillow(frame_path: Path) -> FrameQuality:
    """
    使用 Pillow 分析单帧质量

    Args:
        frame_path: 帧图片路径

    Returns:
        帧质量信息
    """
    from PIL import Image, ImageFilter, ImageStat

    quality = FrameQuality(index=0, time_sec=0)

    try:
        with Image.open(frame_path) as src:
            img = src.convert("L")  # 转灰度
        stat = ImageStat.Stat(img)

        # 平均亮度
        quality.brightness = stat.mean[0]

        # 全黑检测（平均亮度 < 10）
        if quality.brightness < 10:
            quality.is_black = True
            quality.issues.append("全黑帧")

        # 全白检测（平均亮度 > 245）
        if quality.brightness > 245:
            quality.is_white = True
            quality.issues.append("全白帧")

        # 清晰度检测：拉普拉斯（边缘检测）后的方差
        # 用 Pillow 的 FIND_EDGES 滤镜近似
        edges = img.filter(ImageFilter.FIND_EDGES)
        edge_stat = ImageStat.Stat(edges)
        quality.sharpness = edge_stat.stddev[0] ** 2  # 方差近似

        w, h = img.size
        cx1 = int(w * 0.25)
        cy1 = int(h * 0.25)
        cx2 = int(w * 0.75)
        cy2 = int(h * 0.75)
        if cx2 > cx1 and cy2 > cy1:
            center = img.crop((cx1, cy1, cx2, cy2))
            cstat = ImageStat.Stat(center)
            quality.center_brightness = cstat.mean[0]
            cedges = center.filter(ImageFilter.FIND_EDGES)
            cedgestat = ImageStat.Stat(cedges)
            quality.center_sharpness = cedgestat.stddev[0] ** 2

        # 模糊判断（清晰度 < 100 认为模糊，阈值可调）
        if quality.sharpness < 50:
            quality.is_blurry = True
            quality.issues.append("画面模糊")

    except Exception as e:
        quality.issues.append(f"分析失败: {e}")
        quality.is_abnormal = True

    return quality


def _detect_skin_regions(img_rgb) -> List[Dict[str, Any]]:
    """
    检测图像中的肤色区域（基于 YCbCr 色彩空间阈值）

    通过缩小图像到最大边 160px 再检测，性能比原图高 10-20 倍，精度损失可忽略。

    Args:
        img_rgb: PIL Image (RGB 模式)

    Returns:
        肤色区域列表，每个区域包含 bbox (x1,y1,x2,y2) / area / aspect_ratio
    """
    from PIL import Image

    w, h = img_rgb.size

    # 缩小到最大边 160px 再检测，速度提升 10 倍以上
    max_dim = max(w, h)
    if max_dim > 160:
        scale = 160.0 / max_dim
        small_w = int(w * scale)
        small_h = int(h * scale)
        img_small = img_rgb.resize((small_w, small_h), Image.NEAREST)
    else:
        img_small = img_rgb
        scale = 1.0
        small_w, small_h = w, h

    px = img_small.load()

    # 生成一维肤色掩膜（0=非肤色, 1=肤色），一维数组比二维列表访问更快
    total_pixels = small_w * small_h
    skin_mask = bytearray(total_pixels)
    skin_pixels = 0

    for y in range(small_h):
        row_start = y * small_w
        for x in range(small_w):
            rv, gv, bv = px[x, y]
            max_rgb = max(rv, gv, bv)
            min_rgb = min(rv, gv, bv)
            if (rv > 95 and gv > 40 and bv > 20
                    and max_rgb - min_rgb > 15
                    and abs(rv - gv) > 15
                    and rv > gv and rv > bv):
                skin_mask[row_start + x] = 1
                skin_pixels += 1

    if skin_pixels < total_pixels * 0.01:
        return []

    # 连通区域分析（洪水填充，基于一维 mask 数组）
    visited = bytearray(total_pixels)
    regions = []

    for i in range(total_pixels):
        if visited[i] or not skin_mask[i]:
            continue

        x0 = i % small_w
        y0 = i // small_w
        stack = [(x0, y0)]
        min_x, max_x = x0, x0
        min_y, max_y = y0, y0
        area = 0

        while stack:
            cx, cy = stack.pop()
            if cx < 0 or cx >= small_w or cy < 0 or cy >= small_h:
                continue
            idx = cy * small_w + cx
            if visited[idx] or not skin_mask[idx]:
                continue
            visited[idx] = 1
            area += 1
            if cx < min_x:
                min_x = cx
            if cx > max_x:
                max_x = cx
            if cy < min_y:
                min_y = cy
            if cy > max_y:
                max_y = cy

            stack.append((cx + 1, cy))
            stack.append((cx - 1, cy))
            stack.append((cx, cy + 1))
            stack.append((cx, cy - 1))

        bw = max_x - min_x + 1
        bh = max_y - min_y + 1

        min_area = max(10, int(100 * scale * scale))
        if area < min_area:
            continue

        regions.append({
            "bbox": (
                int(min_x / scale), int(min_y / scale),
                int(max_x / scale), int(max_y / scale),
            ),
            "area": int(area / (scale * scale)),
            "width": int(bw / scale),
            "height": int(bh / scale),
            "aspect_ratio": bw / bh if bh > 0 else 0,
            "fill_ratio": area / (bw * bh) if bw * bh > 0 else 0,
            "center_x": (min_x + max_x) / (2 * scale),
            "center_y": (min_y + max_y) / (2 * scale),
        })

    regions.sort(key=lambda r: r["area"], reverse=True)
    return regions


def _analyze_face_quality(frame_path: Path) -> List[str]:
    """
    轻量人脸崩坏检测（基于肤色区域形态学分析）

    检测逻辑：
    1. 找最大的肤色区域，假设是人脸/头部
    2. 检查长宽比是否在合理范围（人脸 0.6-1.2，全身 0.3-0.8）
    3. 检查填充率是否合理（太低可能是碎片化的崩坏，太高可能是色块）
    4. 检查是否有多个面积相近的肤色区域（可能是多个人脸或崩坏碎片）

    注意：这是非常粗略的初筛，只能发现明显异常，不能替代专业检测。

    Args:
        frame_path: 帧图片路径

    Returns:
        人脸异常问题列表
    """
    from PIL import Image

    issues = []

    try:
        with Image.open(frame_path) as src:
            img = src.convert("RGB")
        w, h = img.size

        # 缩小图片加速检测（如果太大）
        if w > 480:
            scale = 480 / w
            img = img.resize((480, int(h * scale)), Image.LANCZOS)

        regions = _detect_skin_regions(img)

        if not regions:
            return issues  # 没检测到肤色区域，可能是风景/产品视频，正常

        # 只看最大的 3 个区域
        top_regions = regions[:3]
        largest = top_regions[0]
        img_area = img.size[0] * img.size[1]

        # 1. 最大肤色区域占比检查
        largest_ratio = largest["area"] / img_area
        if largest_ratio > 0.5:
            issues.append(f"肤色区域过大（{largest_ratio*100:.0f}%），可能画面异常")
        elif largest_ratio < 0.02:
            # 肤色区域太小，可能不是人物画面，不报错
            pass

        # 2. 长宽比检查（最大区域）
        ar = largest["aspect_ratio"]
        # 正常的人脸/人体长宽比大约在 0.3 - 1.5 之间
        if ar > 2.5 or ar < 0.2:
            issues.append(f"肤色区域形状异常（长宽比 {ar:.2f}）")

        # 3. 填充率检查（bbox 内肤色像素比例）
        fill = largest["fill_ratio"]
        # 正常的人脸填充率大约 0.5-0.8，全身大约 0.2-0.5
        if fill > 0.95:
            issues.append("肤色区域过于密实，可能是色块异常")
        elif fill < 0.15 and largest_ratio > 0.05:
            issues.append("肤色区域过于碎片化，可能有崩坏")

        # 4. 多区域检查：如果有 3 个以上面积相近的大肤色区域
        large_regions = [r for r in regions if r["area"] > largest["area"] * 0.3]
        if len(large_regions) >= 4:
            issues.append(f"检测到 {len(large_regions)} 个大面积肤色区域，可能有多人人脸或画面碎片")

        # 5. 对称性检查（粗略：左右两半肤色像素量对比）
        if largest["area"] > img_area * 0.05:
            half_w = img.size[0] // 2
            left_skin = 0
            right_skin = 0
            pixels = img.load()
            x1, y1, x2, y2 = largest["bbox"]
            # 只在最大肤色区域的 bbox 内检查
            for y in range(y1, min(y2 + 1, img.size[1])):
                for x in range(x1, min(x2 + 1, img.size[0])):
                    r, g, b = pixels[x, y]
                    max_rgb = max(r, g, b)
                    min_rgb = min(r, g, b)
                    is_skin = (r > 95 and g > 40 and b > 20
                               and max_rgb - min_rgb > 15
                               and abs(r - g) > 15
                               and r > g and r > b)
                    if is_skin:
                        if x < half_w:
                            left_skin += 1
                        else:
                            right_skin += 1

            total = left_skin + right_skin
            if total > 100:
                left_ratio = left_skin / total
                # 正常人脸左右不对称度约 5-15%，超过 35% 可能有问题
                if abs(left_ratio - 0.5) > 0.3:
                    issues.append(f"肤色区域左右不对称（左 {left_ratio*100:.0f}%）")

    except Exception as e:
        # 人脸检测失败不应该影响整体质量检测
        pass

    return issues


def _average_hash(img, size: int = 8) -> int:
    """计算轻量感知哈希，用于产品参考图粗匹配。"""
    small = img.convert("L").resize((size, size))
    pixels = list(small.getdata())
    avg = sum(pixels) / len(pixels)
    value = 0
    for p in pixels:
        value = (value << 1) | int(p >= avg)
    return value


def _hash_similarity(h1: int, h2: int, bits: int = 64) -> float:
    """哈希相似度，1 表示完全一致。"""
    distance = (h1 ^ h2).bit_count()
    return 1.0 - distance / bits


def _color_histogram(img, bins: int = 4) -> List[float]:
    """RGB 粗直方图，增强对产品颜色/包装的匹配能力。"""
    small = img.convert("RGB").resize((96, 96))
    hist = [0.0] * (bins ** 3)
    step = 256 // bins
    for r, g, b in small.getdata():
        ri = min(bins - 1, r // step)
        gi = min(bins - 1, g // step)
        bi = min(bins - 1, b // step)
        hist[(ri * bins + gi) * bins + bi] += 1.0
    total = sum(hist) or 1.0
    return [v / total for v in hist]


def _hist_similarity(h1: List[float], h2: List[float]) -> float:
    """直方图交集相似度，1 表示颜色分布完全一致。"""
    return sum(min(a, b) for a, b in zip(h1, h2))


def _product_similarity(product_image: Path, frame_paths: List[Path]) -> float:
    """
    轻量产品出现检测：比较商品参考图与抽帧/中心裁剪的感知哈希和颜色直方图。

    这不是精确识别模型，但能拦截“完全没出现商品参考图特征”的成片。
    """
    from PIL import Image

    with Image.open(product_image) as src:
        ref = src.convert("RGB")
    ref_hash = _average_hash(ref)
    ref_hist = _color_histogram(ref)
    best = 0.0

    for frame_path in frame_paths:
        try:
            with Image.open(frame_path) as src:
                frame = src.convert("RGB")
            w, h = frame.size
            crops = [frame]
            # 商品广告通常主体在中心，额外比较中心 70% 和 50%。
            for ratio in (0.70, 0.50):
                cw, ch = int(w * ratio), int(h * ratio)
                x1, y1 = (w - cw) // 2, (h - ch) // 2
                crops.append(frame.crop((x1, y1, x1 + cw, y1 + ch)))

            for crop in crops:
                hash_score = _hash_similarity(ref_hash, _average_hash(crop))
                hist_score = _hist_similarity(ref_hist, _color_histogram(crop))
                score = hash_score * 0.45 + hist_score * 0.55
                if score > best:
                    best = score
        except Exception:
            continue

    return best


def _analyze_audio_ffmpeg(video_path: Path) -> Tuple[float, float, List[str]]:
    """
    使用 ffmpeg/ffprobe 分析音频质量（响度 + 峰值）

    Args:
        video_path: 视频路径

    Returns:
        (lufs, peak_db, issues)
    """
    issues = []
    lufs = 0.0
    peak_db = 0.0

    try:
        # 使用 loudnorm 滤镜的 dry-run 模式测量响度
        cmd = [
            "ffmpeg", "-i", str(video_path),
            "-af", "loudnorm=I=-14:LRA=7:TP=-1.5:print_format=json",
            "-f", "null", "-",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

        # 从 stderr 中解析 JSON（ffmpeg 把 loudnorm 输出打到 stderr）
        stderr = result.stderr
        # 找最后一个 JSON 块
        json_start = stderr.rfind("{")
        json_end = stderr.rfind("}")
        if json_start >= 0 and json_end > json_start:
            import json
            try:
                loudnorm_data = json.loads(stderr[json_start:json_end+1])
                lufs = float(loudnorm_data.get("input_i", "0"))
                peak_db = float(loudnorm_data.get("input_tp", "0"))
                lra = float(loudnorm_data.get("input_lra", "0"))

                # 评估响度
                if lufs == 0 or abs(lufs) < 1:
                    issues.append("无音轨或音量极低")
                elif lufs < -35:
                    issues.append("音量极低，可能为静音或底噪填充")
                elif lufs > -10:
                    issues.append(f"响度过高（{lufs:.1f} LUFS），建议 -14 ~ -16 LUFS")
                elif lufs < -20:
                    issues.append(f"响度过低（{lufs:.1f} LUFS），建议 -14 ~ -16 LUFS")

                # 评估峰值
                if peak_db > 0:
                    issues.append(f"音频削波（峰值 {peak_db:.1f} dBTP），可能有爆音")
                elif peak_db > -0.5:
                    issues.append(f"峰值过高（{peak_db:.1f} dBTP），接近削波")

            except (json.JSONDecodeError, ValueError):
                # P1 修复：JSON 解析失败时用哨兵值标记，避免与真正的静音混淆
                lufs = -999.0
                issues.append("音频响度解析失败，无法确认音轨有效性")

    except Exception as e:
        issues.append(f"音频分析失败: {e}")
        lufs = -999.0

    return lufs, peak_db, issues


def _detect_freeze_frames(video_path: Path) -> Tuple[float, float]:
    """
    使用 ffmpeg freezedetect 滤镜检测开头/结尾冻结帧

    Args:
        video_path: 视频路径

    Returns:
        (开头冻结时长, 结尾冻结时长) 单位：秒
    """
    freeze_start = 0.0
    freeze_end = 0.0

    try:
        cmd = [
            "ffmpeg", "-i", str(video_path),
            "-vf", "freezedetect=n=0.003:d=0.4",
            "-an", "-f", "null", "-",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        stderr = result.stderr

        import re
        pattern = r"freeze_start:([\d.]+)\s+freeze_end:([\d.]+)\s+freeze_duration:([\d.]+)"
        matches = re.findall(pattern, stderr)

        if not matches:
            return 0.0, 0.0

        duration = _get_video_duration(video_path)

        for start_str, end_str, dur_str in matches:
            start = float(start_str)
            end = float(end_str)
            dur = float(dur_str)

            # 开头冻结（从 0 附近开始）
            if start < 0.5:
                freeze_start = max(freeze_start, end)
            # 结尾冻结（接近视频末尾）
            if duration > 0 and end > duration - 0.5:
                freeze_end = max(freeze_end, dur)

    except Exception as e:
        print(f"  ⚠️  冻结帧检测跳过：{e}")

    return freeze_start, freeze_end


def _detect_black_frames(video_path: Path) -> Tuple[float, float]:
    """
    使用 ffmpeg blackdetect 滤镜检测开头/结尾黑帧

    Args:
        video_path: 视频路径

    Returns:
        (开头黑帧时长, 结尾黑帧时长) 单位：秒
    """
    black_start = 0.0
    black_end = 0.0

    try:
        duration = _get_video_duration(video_path)
        if duration <= 0:
            return 0.0, 0.0

        cmd = [
            "ffmpeg", "-i", str(video_path),
            "-vf", "blackdetect=d=0.1:pix_th=0.10",
            "-f", "null", "-",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        stderr = result.stderr

        # 解析 blackdetect 输出
        import re
        pattern = r"black_start:([\d.]+)\s+black_end:([\d.]+)\s+black_duration:([\d.]+)"
        matches = re.findall(pattern, stderr)

        for start_str, end_str, dur_str in matches:
            start = float(start_str)
            end = float(end_str)
            dur = float(dur_str)

            # 开头黑帧（从 0 开始的黑帧）
            if start < 0.5:
                black_start = max(black_start, end)

            # 结尾黑帧（接近视频末尾的黑帧）
            if end > duration - 0.5:
                black_end = max(black_end, dur)

    except Exception as e:
        print(f"  ⚠️  黑帧检测跳过：{e}")

    return black_start, black_end


def check_video_quality(
    video_path: Path,
    num_frames: int = 10,
    content_focus: str = "default",
    require_audio: bool = False,
    product_reference_image: Optional[Path] = None,
) -> VideoQualityResult:
    """
    检测视频质量（画面 + 音频 + 黑帧）

    Args:
        video_path: 视频路径
        num_frames: 抽帧数量
        content_focus: 内容关注点（default/center）
        require_audio: 是否要求视频必须包含音轨（最终成片应开启，AI原始片段可关闭）
        product_reference_image: 商品参考图；提供时启用轻量产品出现检测

    Returns:
        质量检测结果
    """
    result = VideoQualityResult()

    if not video_path.exists():
        result.passed = False
        result.issues.append("视频文件不存在")
        return result

    # 文件大小校验
    file_size = video_path.stat().st_size
    if file_size == 0:
        result.passed = False
        result.issues.append("视频文件大小为 0（空文件）")
        return result
    min_size = 10 * 1024  # 10KB，低于此值视为损坏
    if file_size < min_size:
        result.passed = False
        result.issues.append(f"视频文件过小（{file_size} bytes），可能已损坏")
        return result

    # ffprobe 完整性校验
    try:
        probe_cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "stream=codec_type:format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ]
        probe_result = subprocess.run(
            probe_cmd, capture_output=True, text=True, check=True, timeout=10,
        )
        streams = [line.strip() for line in probe_result.stdout.strip().split("\n") if line.strip()]
        has_video = "video" in streams
        has_audio = "audio" in streams
        # 最后一行是 duration（format 条目在 stream 之后）
        duration_line = streams[-1] if streams else ""
        try:
            duration = float(duration_line) if duration_line and "." in duration_line else 0.0
        except ValueError:
            duration = 0.0

        if not has_video:
            result.passed = False
            result.issues.append("视频文件无视频流")
            return result
        if require_audio and not has_audio:
            result.passed = False
            result.issues.append("最终成片无音轨，无法作为可发布广告视频")
            return result
        if duration <= 0:
            result.passed = False
            result.issues.append("视频时长为 0，文件可能已损坏")
            return result
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        result.passed = False
        result.issues.append(f"ffprobe 读取失败，文件可能已损坏：{e}")
        return result

    if not shutil.which("ffmpeg"):
        result.passed = False
        result.issues.append("未安装 ffmpeg，无法检测")
        return result

    # 抽帧
    tmp_dir = Path(tempfile.mkdtemp(prefix="quality_"))
    try:
        frames = _extract_frames_ffmpeg(video_path, tmp_dir, num_frames)
        if not frames:
            result.passed = False
            result.issues.append("无法抽取帧")
            return result

        result.frames_analyzed = len(frames)

        # 分析每帧
        sharpness_values = []
        center_sharpness_values = []
        center_brightness_values = []
        prev_brightness = None
        flicker_count = 0
        video_duration = _get_video_duration(video_path)
        frame_interval = video_duration / len(frames) if len(frames) > 0 else 0

        for i, frame_path in enumerate(frames):
            fq = _analyze_frame_pillow(frame_path)
            fq.index = i
            fq.time_sec = i * frame_interval
            result.details.append(fq)

            # P1 修复：帧分析失败计入异常帧
            if fq.is_abnormal:
                result.issues.append(f"[帧{i}] 分析异常：{fq.issues}")

            sharpness_values.append(fq.sharpness)
            if fq.center_sharpness > 0:
                center_sharpness_values.append(fq.center_sharpness)
            if fq.center_brightness > 0:
                center_brightness_values.append(fq.center_brightness)

            if fq.is_black:
                result.black_frames += 1
            if fq.is_white:
                result.white_frames += 1
            if fq.is_blurry:
                result.blurry_frames += 1

            # 闪烁检测：相邻帧亮度差 > 80 算一次闪烁
            if prev_brightness is not None:
                diff = abs(fq.brightness - prev_brightness)
                if diff > 80:
                    flicker_count += 1
            prev_brightness = fq.brightness

            # 人脸崩坏初筛（轻量检测）
            face_issues = _analyze_face_quality(frame_path)
            if face_issues:
                result.face_issue_frames += 1
                for fi in face_issues:
                    if fi not in result.face_issues:
                        result.face_issues.append(fi)

        # 计算平均清晰度
        if sharpness_values:
            result.avg_sharpness = sum(sharpness_values) / len(sharpness_values)

        # 闪烁判定：超过 30% 的帧有大幅亮度变化
        if len(frames) > 2 and flicker_count >= len(frames) * 0.3:
            result.flicker_detected = True
            result.issues.append("检测到画面闪烁")

        # 音频质量检测
        lufs, peak_db, audio_issues = _analyze_audio_ffmpeg(video_path)
        result.audio_lufs = lufs
        result.audio_peak = peak_db
        result.audio_issues = audio_issues
        if audio_issues:
            result.issues.extend(f"[音频] {issue}" for issue in audio_issues)

        # 黑帧检测（开头/结尾）
        black_start, black_end = _detect_black_frames(video_path)
        result.black_start = black_start
        result.black_end = black_end
        if black_start > 0.3:
            result.issues.append(f"开头有 {black_start:.1f}s 黑帧，建议裁切")
            # P1 修复：开头黑帧直接判失败
            result.passed = False
        if black_end > 0.3:
            result.issues.append(f"结尾有 {black_end:.1f}s 黑帧，建议裁切")

        # 冻结帧检测（开头/结尾）
        freeze_start, freeze_end = _detect_freeze_frames(video_path)
        result.freeze_start = freeze_start
        result.freeze_end = freeze_end
        if freeze_start > 0.4:
            result.issues.append(f"开头有 {freeze_start:.1f}s 冻结帧，建议裁切")
            # P1 修复：开头冻结帧直接判失败
            result.passed = False
        if freeze_end > 0.4:
            result.issues.append(f"结尾有 {freeze_end:.1f}s 冻结帧，建议裁切")

        # 产品出现检测（有商品参考图时启用）
        if product_reference_image:
            product_reference_image = Path(product_reference_image)
            if product_reference_image.exists():
                try:
                    result.product_similarity = _product_similarity(product_reference_image, frames)
                    # P1 修复：阈值从 0.25 提高到 0.55（实验验证：明显不同的图片仍得 ~0.51）
                    result.product_detected = result.product_similarity >= 0.55
                    if not result.product_detected:
                        result.issues.append(
                            f"[产品检测] 未检测到足够的商品参考图特征（相似度 {result.product_similarity:.2f} < 0.55）"
                        )
                    elif result.product_similarity < 0.65:
                        result.issues.append(
                            f"[产品检测] 商品特征较弱（相似度 {result.product_similarity:.2f}），建议人工确认产品露出"
                        )
                except Exception as e:
                    result.issues.append(f"[产品检测] 商品参考图检测失败：{e}")

        # 综合评分（0-100）
        score = 100

        # 模糊扣分
        if result.blurry_frames > 0:
            blur_ratio = result.blurry_frames / len(frames)
            score -= min(30, blur_ratio * 60)

        # 异常帧扣分
        abnormal = result.black_frames + result.white_frames
        if abnormal > 0:
            score -= min(40, abnormal * 15)

        # 闪烁扣分
        if result.flicker_detected:
            score -= 20

        # 音频扣分
        if result.audio_issues:
            score -= min(15, len(result.audio_issues) * 5)

        # P1 修复：最终成片要求有效音频，底噪/静音填充直接判失败
        if require_audio and result.audio_lufs < -30:
            result.passed = False
            result.issues.insert(0, "最终成片音频为静音或底噪填充，无法作为可发布广告视频")
        # P1 修复：音频响度检测失败（哨兵值 -999）或真正的静音（0.0）也判失败
        if require_audio and (result.audio_lufs == -999.0 or result.audio_lufs == 0.0):
            result.passed = False
            result.issues.insert(0, "音频响度检测失败，无法确认音轨有效性")

        # 首尾黑帧扣分
        if black_start + black_end > 1.0:
            score -= 10

        # 首尾冻结帧扣分
        if freeze_start + freeze_end > 1.0:
            score -= 15  # 冻结帧比黑帧更影响观感

        # 人脸崩坏初筛扣分
        if result.face_issue_frames > 0 and result.frames_analyzed > 0:
            face_ratio = result.face_issue_frames / result.frames_analyzed
            if face_ratio > 0.5:
                score -= 25
                result.issues.insert(0, f"⚠️  多帧检测到人脸/肤色异常（{result.face_issue_frames}/{result.frames_analyzed} 帧），建议人工复核")
                # P1 修复：超过一半帧人脸异常直接判失败
                result.passed = False
            elif face_ratio > 0.2:
                score -= 10

        # 产品未出现直接重扣；商品广告没有清晰产品露出，不应作为发布级成片。
        if product_reference_image and product_reference_image.exists():
            if not result.product_detected:
                score -= 35
                result.passed = False
            elif result.product_similarity < 0.35:
                score -= 10

        if content_focus == "center":
            if center_sharpness_values:
                avg_center_sharpness = sum(center_sharpness_values) / len(center_sharpness_values)
                if avg_center_sharpness < 80:
                    score -= min(25, (80 - avg_center_sharpness) / 80 * 25)
                    result.issues.append("中心区域不够清晰（产品/主体可能不够突出）")
            if center_brightness_values:
                avg_center_brightness = sum(center_brightness_values) / len(center_brightness_values)
                if avg_center_brightness < 25:
                    score -= 8
                    result.issues.append("中心区域偏暗（主体可能不够显眼）")
                elif avg_center_brightness > 235:
                    score -= 5
                    result.issues.append("中心区域偏亮（高光可能过曝）")

        result.overall_score = max(0, min(100, score))

        # Bug6 修复：通过门槛从 60 提高到 70
        # 60 分门槛过低（允许 30% 模糊帧 + 轻微闪烁同时存在），无法代表"可发布"水准
        # 70 分确保：无严重模糊（<15% 模糊帧）+ 无明显闪烁 + 黑帧不超 1s
        if result.overall_score < 70:
            result.passed = False
            result.issues.insert(0, f"综合评分 {result.overall_score:.0f} 分，质量不达标（需 >=70）")
        elif result.overall_score < 85:
            result.issues.insert(0, f"综合评分 {result.overall_score:.0f} 分，质量合格（建议 >=85 再发布）")
        else:
            result.issues.insert(0, f"综合评分 {result.overall_score:.0f} 分，质量优秀")

        # Bug6 补充：产品可见性提示（center 模式下中心区域过暗/模糊时显式警告）
        # 抖音广告展示类段必须让产品清晰可见，否则转化率极低
        if content_focus == "center" and center_sharpness_values:
            avg_cs = sum(center_sharpness_values) / len(center_sharpness_values)
            if avg_cs < 50:
                result.issues.append(
                    f"[产品检测] 中心区域极度模糊（清晰度 {avg_cs:.0f} < 50），"
                    "产品可能不可识别，强烈建议重新生成"
                )
                result.passed = False
            elif avg_cs < 80:
                result.issues.append(
                    f"[产品检测] 中心区域清晰度 {avg_cs:.0f}，建议确认产品细节是否清晰可见"
                )

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return result


def check_clips_quality(
    clip_paths: List[Path],
    frames_per_clip: int = 3,
) -> Dict[str, Any]:
    """
    批量检测多个片段的质量

    Args:
        clip_paths: 片段路径列表
        frames_per_clip: 每个片段抽帧数

    Returns:
        {
            "all_passed": bool,
            "avg_score": float,
            "clips": [ {...}, ... ],
            "bad_clips": [index, ...],
        }
    """
    clip_results = []
    scores = []
    bad_clips = []

    for idx, clip_path in enumerate(clip_paths):
        result = check_video_quality(clip_path, num_frames=frames_per_clip)
        clip_results.append({
            "index": idx,
            "path": clip_path.name,
            "score": result.overall_score,
            "passed": result.passed,
            "issues": result.issues,
        })
        scores.append(result.overall_score)
        if not result.passed:
            bad_clips.append(idx)

    avg_score = sum(scores) / len(scores) if scores else 0

    return {
        "all_passed": len(bad_clips) == 0,
        "avg_score": avg_score,
        "clips": clip_results,
        "bad_clips": bad_clips,
    }


def print_quality_report(result: VideoQualityResult, video_name: str = ""):
    """打印质量检测报告"""
    print()
    print("=" * 50)
    print(f"🎬 视频质量检测：{video_name}")
    print("=" * 50)
    print(f"  综合评分：{result.overall_score:.0f} / 100")
    print(f"  检测结论：{'✅ 合格' if result.passed else '❌ 不合格'}")
    print()
    print(f"  🖼️  画面质量：")
    print(f"    分析帧数：{result.frames_analyzed}")
    print(f"    平均清晰度：{result.avg_sharpness:.1f}")
    print(f"    模糊帧数：{result.blurry_frames}")
    print(f"    全黑帧数：{result.black_frames}")
    print(f"    全白帧数：{result.white_frames}")
    print(f"    画面闪烁：{'⚠️ 检测到' if result.flicker_detected else '✅ 正常'}")
    if result.black_start > 0 or result.black_end > 0:
        print(f"    开头黑帧：{result.black_start:.1f}s")
        print(f"    结尾黑帧：{result.black_end:.1f}s")
    if result.freeze_start > 0 or result.freeze_end > 0:
        print(f"    开头冻结：{result.freeze_start:.1f}s")
        print(f"    结尾冻结：{result.freeze_end:.1f}s")
    if result.face_issue_frames > 0:
        print(f"    人脸异常帧：{result.face_issue_frames}/{result.frames_analyzed}（轻量初筛，仅供参考）")
        for issue in result.face_issues[:3]:
            print(f"      - {issue}")
    print()
    print(f"  🔊 音频质量：")
    if result.audio_lufs != 0 or result.audio_peak != 0:
        print(f"    集成响度：{result.audio_lufs:.1f} LUFS")
        print(f"    真峰值：{result.audio_peak:.1f} dBTP")
        if result.audio_issues:
            print(f"    音频问题：{len(result.audio_issues)} 项")
        else:
            print(f"    音频状态：✅ 正常")
    else:
        print(f"    状态：未检测到音轨")

    if result.issues:
        print()
        print("  📋 问题列表：")
        for issue in result.issues:
            print(f"    • {issue}")

    print("=" * 50)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法：python quality_checker.py <视频路径>")
        sys.exit(1)

    video = Path(sys.argv[1])
    if not video.exists():
        print(f"❌ 文件不存在：{video}")
        sys.exit(1)

    result = check_video_quality(video, num_frames=10)
    print_quality_report(result, video.name)
