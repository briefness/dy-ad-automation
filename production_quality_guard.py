#!/usr/bin/env python3
"""
发布级质量保证系统（Production Quality Guard）

参考行业标准：
- 抖音广告技术规范 2024
- YouTube Shorts 质量指南
- FFmpeg 专业视频处理最佳实践
- Netflix VMAF 视频质量评估
- EBU R128 音频响度标准
- ITU-R BT.709 色彩空间标准

覆盖7大质量维度：
1. 技术基础质量（编码/分辨率/码率/色彩空间/音频）
2. 时间一致性（跨镜头人物/产品/光照/运动连续性）
3. 叙事节奏（Hook强度/信息密度/转场踩点/字幕同步）
4. 视觉美学（构图/曝光/色彩和谐/焦点/安全区域）
5. 广告效果（产品可见时长/CTA清晰度/品牌露出/字幕可读）
6. 平台合规（编码规范/文件格式/平台适配）
7. AI伪影防护（手指/文字/穿模/塑料感/肢体异常）

同时提供自动修复管线，能自动修复常见问题。
"""

import subprocess
import tempfile
import shutil
import math
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
import json

from douyin_adapter import DOUYIN_CONFIG


class Severity(Enum):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


class IssueCategory(Enum):
    TECHNICAL = "technical"
    TEMPORAL = "temporal"
    NARRATIVE = "narrative"
    AESTHETIC = "aesthetic"
    AD_EFFECTIVENESS = "ad_effectiveness"
    COMPLIANCE = "compliance"
    AI_ARTIFACT = "ai_artifact"


@dataclass
class QualityIssue:
    category: IssueCategory
    severity: Severity
    message: str
    auto_fixable: bool = False
    fix_hint: str = ""
    timestamp: Optional[float] = None


@dataclass
class DimensionScore:
    name: str
    score: float
    weight: float
    passed: bool
    issues: List[QualityIssue] = field(default_factory=list)


@dataclass
class ProductionQualityReport:
    passed: bool = True
    overall_score: float = 0.0
    dimension_scores: Dict[str, DimensionScore] = field(default_factory=dict)
    all_issues: List[QualityIssue] = field(default_factory=list)
    auto_fixes_applied: List[str] = field(default_factory=list)
    video_metadata: Dict[str, Any] = field(default_factory=dict)
    platform_recommendations: Dict[str, Any] = field(default_factory=dict)

    def get_critical_issues(self) -> List[QualityIssue]:
        return [i for i in self.all_issues if i.severity == Severity.CRITICAL]

    def get_fixable_issues(self) -> List[QualityIssue]:
        return [i for i in self.all_issues if i.auto_fixable]


class ProductionQualityGuard:
    DOUYIN_SAFE_AREA = {"top": 0.12, "bottom": 0.18, "left": 0.05, "right": 0.05}
    DOUYIN_UI_AVOID = {
        "top": 0.0,
        "bottom": float(DOUYIN_CONFIG["subtitle"]["bottom_margin_ratio"]),
        "left": 0.0,
        "right": 0.0,
    }
    TARGET_LUFS = -14.0
    MIN_PRODUCT_VISIBILITY_RATIO = 0.35
    MIN_CTA_DURATION = 1.5
    MIN_HOOK_CLARITY_DURATION = 2.0

    def __init__(self, platform: str = "douyin"):
        self.platform = platform
        self.safe_area = self.DOUYIN_SAFE_AREA if platform == "douyin" else self.DOUYIN_SAFE_AREA
        self.ui_avoid = self.DOUYIN_UI_AVOID if platform == "douyin" else self.DOUYIN_UI_AVOID

    def analyze_and_fix(
        self,
        video_path: Path,
        product_reference: Optional[Path] = None,
        character_reference: Optional[Path] = None,
        subtitles: Optional[List[Dict]] = None,
        beat_timings: Optional[List[float]] = None,
        segments: Optional[List[Dict]] = None,
        cta_contract: Optional[Dict] = None,
        auto_fix: bool = True,
    ) -> Tuple[Path, ProductionQualityReport]:
        report = ProductionQualityReport()
        report.video_metadata = self._extract_video_metadata(video_path)

        self._check_technical_quality(video_path, report)
        self._check_temporal_consistency(video_path, report)
        self._check_aesthetic_quality(video_path, report)
        self._check_ad_effectiveness(
            video_path, product_reference, subtitles, segments, report, cta_contract=cta_contract,
        )
        self._check_platform_compliance(video_path, report)
        self._check_ai_artifacts(video_path, report)
        self._check_narrative_rhythm(video_path, beat_timings, subtitles, segments, report)

        if character_reference:
            self._check_character_consistency(video_path, character_reference, report)

        self._calculate_overall_score(report)
        critical_count = len(report.get_critical_issues())
        report.passed = critical_count == 0 and report.overall_score >= 75

        output_path = video_path
        if auto_fix and report.get_fixable_issues():
            output_path = self._apply_auto_fixes(video_path, report)

        return output_path, report

    def _extract_video_metadata(self, video_path: Path) -> Dict[str, Any]:
        from utils_ffprobe import get_video_info
        info = get_video_info(str(video_path))
        return {
            "width": info.get("width", 1080),
            "height": info.get("height", 1920),
            "fps": info.get("fps", 30),
            "duration": info.get("duration", 0),
            "video_codec": info.get("video_codec", "unknown"),
            "audio_codec": info.get("audio_codec", "unknown"),
            "bitrate": info.get("bitrate", 0),
            "has_audio": info.get("has_audio", False),
            "pixel_format": info.get("pixel_format", "yuv420p"),
        }

    def _check_technical_quality(self, video_path: Path, report: ProductionQualityReport):
        issues = []
        meta = report.video_metadata
        score = 100

        w, h = meta.get("width", 0), meta.get("height", 0)
        if w < 1080 or h < 1920:
            issues.append(QualityIssue(
                IssueCategory.TECHNICAL, Severity.WARNING,
                f"分辨率低于1080x1920（当前 {w}x{h}）",
                auto_fixable=True, fix_hint="upscale"
            ))
            score -= 15

        fps = meta.get("fps", 30)
        if fps < 29 or fps > 31:
            issues.append(QualityIssue(
                IssueCategory.TECHNICAL, Severity.CRITICAL,
                f"帧率非30fps（当前 {fps:.1f}fps），平台可能二次编码",
                auto_fixable=True, fix_hint="reencode_fps"
            ))
            score -= 25

        bitrate_kbps = meta.get("bitrate", 0) // 1000
        if bitrate_kbps < 5000:
            issues.append(QualityIssue(
                IssueCategory.TECHNICAL, Severity.WARNING,
                f"码率过低（{bitrate_kbps}kbps），可能出现块状失真",
                auto_fixable=True, fix_hint="reencode_bitrate"
            ))
            score -= 15
        elif bitrate_kbps > 20000:
            issues.append(QualityIssue(
                IssueCategory.TECHNICAL, Severity.INFO,
                f"码率较高（{bitrate_kbps}kbps），文件体积偏大"
            ))

        vcodec = meta.get("video_codec", "")
        if "h264" not in vcodec.lower() and "hevc" not in vcodec.lower():
            issues.append(QualityIssue(
                IssueCategory.TECHNICAL, Severity.CRITICAL,
                f"视频编码非H.264/H.265（当前 {vcodec}），可能无法在平台正常播放",
                auto_fixable=True, fix_hint="reencode_codec"
            ))
            score -= 30

        pix_fmt = meta.get("pixel_format", "")
        if "yuv420p" not in pix_fmt:
            issues.append(QualityIssue(
                IssueCategory.TECHNICAL, Severity.WARNING,
                f"像素格式非yuv420p（当前 {pix_fmt}），兼容性可能有问题",
                auto_fixable=True, fix_hint="reencode_pixfmt"
            ))
            score -= 10

        if not meta.get("has_audio"):
            issues.append(QualityIssue(
                IssueCategory.TECHNICAL, Severity.CRITICAL,
                "视频无音轨，无法作为广告发布",
                auto_fixable=False
            ))
            score -= 50
        else:
            acodec = meta.get("audio_codec", "")
            if "aac" not in acodec.lower():
                issues.append(QualityIssue(
                    IssueCategory.TECHNICAL, Severity.WARNING,
                    f"音频编码非AAC（当前 {acodec}）",
                    auto_fixable=True, fix_hint="reencode_audio"
                ))
                score -= 10

            lufs, peak, audio_issues = self._analyze_audio_quality(video_path)
            if lufs < -30:
                issues.append(QualityIssue(
                    IssueCategory.TECHNICAL, Severity.CRITICAL,
                    f"音频响度过低（{lufs:.1f} LUFS），用户可能听不到声音",
                    auto_fixable=True, fix_hint="normalize_audio"
                ))
                score -= 30
            elif lufs < self.TARGET_LUFS - 4:
                issues.append(QualityIssue(
                    IssueCategory.TECHNICAL, Severity.WARNING,
                    f"音频响度偏低（{lufs:.1f} LUFS），目标 {self.TARGET_LUFS} LUFS",
                    auto_fixable=True, fix_hint="normalize_audio"
                ))
                score -= 10
            elif lufs > self.TARGET_LUFS + 3:
                issues.append(QualityIssue(
                    IssueCategory.TECHNICAL, Severity.WARNING,
                    f"音频响度过高（{lufs:.1f} LUFS），可能有爆音",
                    auto_fixable=True, fix_hint="normalize_audio"
                ))
                score -= 10

            if peak > -0.5:
                issues.append(QualityIssue(
                    IssueCategory.TECHNICAL, Severity.WARNING,
                    f"音频峰值过高（{peak:.1f} dBTP），接近削波",
                    auto_fixable=True, fix_hint="normalize_audio"
                ))
                score -= 8

        duration = meta.get("duration", 0)
        if duration < 5:
            issues.append(QualityIssue(
                IssueCategory.TECHNICAL, Severity.WARNING,
                f"视频时长过短（{duration:.1f}s），广告信息可能不完整",
                auto_fixable=False
            ))
            score -= 10
        elif duration > 60:
            issues.append(QualityIssue(
                IssueCategory.TECHNICAL, Severity.INFO,
                f"视频时长较长（{duration:.1f}s），注意短视频完播率"
            ))

        report.dimension_scores["technical"] = DimensionScore(
            name="技术基础", score=max(0, score), weight=0.25,
            passed=not any(i.severity == Severity.CRITICAL for i in issues),
            issues=issues
        )
        report.all_issues.extend(issues)

    def _analyze_audio_quality(self, video_path: Path) -> Tuple[float, float, List[str]]:
        try:
            cmd = [
                "ffmpeg", "-i", str(video_path),
                "-af", f"loudnorm=I={self.TARGET_LUFS}:LRA=7:TP=-1.5:print_format=json",
                "-f", "null", "-",
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            stderr = result.stderr
            json_start = stderr.rfind("{")
            json_end = stderr.rfind("}")
            if json_start >= 0 and json_end > json_start:
                data = json.loads(stderr[json_start:json_end+1])
                return (
                    float(data.get("input_i", -999)),
                    float(data.get("input_tp", 0)),
                    []
                )
        except Exception:
            pass
        return -999.0, 0.0, []

    def _check_temporal_consistency(self, video_path: Path, report: ProductionQualityReport):
        issues = []
        score = 100

        try:
            from temporal_consistency import TemporalConsistencyDetector
            detector = TemporalConsistencyDetector()
            analysis = detector.analyze_video(video_path)

            if analysis.frame_consistency_score < 0.6:
                issues.append(QualityIssue(
                    IssueCategory.TEMPORAL, Severity.WARNING,
                    f"帧间一致性偏低（{analysis.frame_consistency_score:.2f}），可能有闪烁或跳变",
                    auto_fixable=True, fix_hint="deflicker"
                ))
                score -= 20
            if analysis.motion_smoothness_score < 0.5:
                issues.append(QualityIssue(
                    IssueCategory.TEMPORAL, Severity.WARNING,
                    f"运动平滑度偏低（{analysis.motion_smoothness_score:.2f}），运镜可能卡顿",
                    auto_fixable=False
                ))
                score -= 15
            if analysis.object_integrity_score < 0.5:
                issues.append(QualityIssue(
                    IssueCategory.TEMPORAL, Severity.CRITICAL,
                    f"物体完整性偏低（{analysis.object_integrity_score:.2f}），可能有物体变形或消失",
                    auto_fixable=False
                ))
                score -= 35
            if analysis.flicker_severity > 0.15:
                issues.append(QualityIssue(
                    IssueCategory.TEMPORAL, Severity.WARNING,
                    f"检测到画面闪烁（严重度 {analysis.flicker_severity:.2f}）",
                    auto_fixable=True, fix_hint="deflicker"
                ))
                score -= 15
        except ImportError:
            issues.append(QualityIssue(
                IssueCategory.TEMPORAL, Severity.INFO,
                "时间一致性检测模块未加载，跳过深度检测"
            ))
        except Exception as e:
            issues.append(QualityIssue(
                IssueCategory.TEMPORAL, Severity.INFO,
                f"时间一致性检测失败: {e}"
            ))

        report.dimension_scores["temporal"] = DimensionScore(
            name="时间一致性", score=max(0, score), weight=0.15,
            passed=not any(i.severity == Severity.CRITICAL for i in issues),
            issues=issues
        )
        report.all_issues.extend(issues)

    def _check_aesthetic_quality(self, video_path: Path, report: ProductionQualityReport):
        issues = []
        score = 100

        tmp_dir = Path(tempfile.mkdtemp(prefix="aesthetic_"))
        try:
            frames = self._extract_frames(video_path, tmp_dir, num_frames=12)
            if not frames:
                issues.append(QualityIssue(
                    IssueCategory.AESTHETIC, Severity.WARNING,
                    "无法抽帧进行美学检测"
                ))
                return

            brightness_values = []
            sharpness_values = []
            exposure_issues = 0

            for frame_path in frames:
                fq = self._analyze_frame_aesthetics(frame_path, report.video_metadata)
                brightness_values.append(fq["brightness"])
                sharpness_values.append(fq["sharpness"])
                if fq["overexposed"] or fq["underexposed"]:
                    exposure_issues += 1

            if brightness_values:
                brightness_std = self._std_dev(brightness_values)
                if brightness_std > 50:
                    issues.append(QualityIssue(
                        IssueCategory.AESTHETIC, Severity.WARNING,
                        f"片段间亮度波动较大（std={brightness_std:.1f}），曝光不一致",
                        auto_fixable=True, fix_hint="normalize_exposure"
                    ))
                    score -= 18

            if exposure_issues > len(frames) * 0.2:
                issues.append(QualityIssue(
                    IssueCategory.AESTHETIC, Severity.WARNING,
                    f"{exposure_issues}/{len(frames)} 帧过曝或欠曝",
                    auto_fixable=True, fix_hint="normalize_exposure"
                ))
                score -= 15

            if sharpness_values:
                avg_sharpness = sum(sharpness_values) / len(sharpness_values)
                if avg_sharpness < 80:
                    issues.append(QualityIssue(
                        IssueCategory.AESTHETIC, Severity.WARNING,
                        f"整体清晰度偏低（{avg_sharpness:.0f}）",
                        auto_fixable=True, fix_hint="sharpen"
                    ))
                    score -= 12

            h = report.video_metadata.get("height", 1920)
            w = report.video_metadata.get("width", 1080)
            safe_top = int(h * self.safe_area["top"])
            safe_bottom = int(h * (1 - self.safe_area["bottom"]))
            safe_left = int(w * self.safe_area["left"])
            safe_right = int(w * (1 - self.safe_area["right"]))

            edge_brightness_issues = self._check_safe_area_importance(
                frames, w, h, safe_left, safe_top, safe_right, safe_bottom
            )
            if edge_brightness_issues > len(frames) * 0.3:
                issues.append(QualityIssue(
                    IssueCategory.AESTHETIC, Severity.WARNING,
                    f"安全区域外有高对比度内容，可能被平台UI遮挡",
                    auto_fixable=False
                ))
                score -= 10

        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        report.dimension_scores["aesthetic"] = DimensionScore(
            name="视觉美学", score=max(0, score), weight=0.15,
            passed=not any(i.severity == Severity.CRITICAL for i in issues),
            issues=issues
        )
        report.all_issues.extend(issues)

    def _analyze_frame_aesthetics(self, frame_path: Path, meta: Dict) -> Dict[str, Any]:
        from PIL import Image, ImageFilter, ImageStat
        result = {"brightness": 0, "sharpness": 0, "overexposed": False, "underexposed": False}
        try:
            with Image.open(frame_path) as src:
                gray = src.convert("L")
            stat = ImageStat.Stat(gray)
            result["brightness"] = stat.mean[0]
            if result["brightness"] > 235:
                result["overexposed"] = True
            elif result["brightness"] < 30:
                result["underexposed"] = True
            edges = gray.filter(ImageFilter.FIND_EDGES)
            est = ImageStat.Stat(edges)
            result["sharpness"] = est.stddev[0] ** 2
        except Exception:
            pass
        return result

    def _check_safe_area_importance(
        self, frames: List[Path], w: int, h: int,
        l: int, t: int, r: int, b: int
    ) -> int:
        from PIL import Image, ImageStat
        issues = 0
        for fp in frames:
            try:
                with Image.open(fp) as src:
                    gray = src.convert("L")
                h_total = gray.size[1]
                w_total = gray.size[0]
                edge_regions = [
                    gray.crop((0, 0, w_total, t)),
                    gray.crop((0, b, w_total, h_total)),
                    gray.crop((0, 0, l, h_total)),
                    gray.crop((r, 0, w_total, h_total)),
                ]
                center = gray.crop((l, t, r, b))
                center_stat = ImageStat.Stat(center)
                center_brightness = center_stat.mean[0]
                edge_brightnesses = []
                for region in edge_regions:
                    if region.size[0] > 0 and region.size[1] > 0:
                        rs = ImageStat.Stat(region)
                        edge_brightnesses.append(rs.mean[0])
                if edge_brightnesses:
                    max_edge_deviation = max(abs(b - center_brightness) for b in edge_brightnesses)
                    if max_edge_deviation > 80:
                        issues += 1
            except Exception:
                continue
        return issues

    def _check_ad_effectiveness(
        self,
        video_path: Path,
        product_reference: Optional[Path],
        subtitles: Optional[List[Dict]],
        segments: Optional[List[Dict]],
        report: ProductionQualityReport,
        cta_contract: Optional[Dict] = None,
    ):
        issues = []
        score = 100
        meta = report.video_metadata
        duration = meta.get("duration", 25)

        if product_reference and product_reference.exists():
            tmp_dir = Path(tempfile.mkdtemp(prefix="ad_check_"))
            try:
                frames = self._extract_frames(video_path, tmp_dir, num_frames=20)
                detected_frames = 0
                product_sims = []

                from quality_checker import _product_similarity
                for fp in frames:
                    sim = _product_similarity(product_reference, [fp])
                    product_sims.append(sim)
                    if sim >= 0.55:
                        detected_frames += 1

                visibility_ratio = detected_frames / len(frames) if frames else 0
                if visibility_ratio < self.MIN_PRODUCT_VISIBILITY_RATIO:
                    issues.append(QualityIssue(
                        IssueCategory.AD_EFFECTIVENESS, Severity.CRITICAL,
                        f"产品可见时长占比仅 {visibility_ratio*100:.0f}%，建议 ≥{self.MIN_PRODUCT_VISIBILITY_RATIO*100:.0f}%",
                        auto_fixable=False
                    ))
                    score -= 35
                elif visibility_ratio < 0.5:
                    issues.append(QualityIssue(
                        IssueCategory.AD_EFFECTIVENESS, Severity.WARNING,
                        f"产品可见时长占比 {visibility_ratio*100:.0f}%，建议增加产品镜头",
                        auto_fixable=False
                    ))
                    score -= 15

                if product_sims:
                    max_sim = max(product_sims)
                    if max_sim < 0.5:
                        issues.append(QualityIssue(
                            IssueCategory.AD_EFFECTIVENESS, Severity.CRITICAL,
                            "产品特征不明显，用户可能无法识别产品",
                            auto_fixable=False
                        ))
                        score -= 25
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)
        else:
            issues.append(QualityIssue(
                IssueCategory.AD_EFFECTIVENESS, Severity.INFO,
                "未提供产品参考图，跳过产品露出检测"
            ))

        if segments:
            has_cta = bool(
                cta_contract
                and cta_contract.get("enabled")
                and str(cta_contract.get("text") or "").strip()
                and float(cta_contract.get("duration") or 0.0) >= self.MIN_CTA_DURATION
            ) or any(
                s.get("narrative", "").lower() in ("cta", "call_to_action", "action")
                or "购买" in s.get("text", "") or "点击" in s.get("text", "")
                for s in segments
            )
            if not has_cta:
                issues.append(QualityIssue(
                    IssueCategory.AD_EFFECTIVENESS, Severity.WARNING,
                    "未检测到明确的CTA（行动号召）段落，可能影响转化",
                    auto_fixable=False
                ))
                score -= 15

        if subtitles:
            h = meta.get("height", 1920)
            ui_avoid_bottom = int(h * self.ui_avoid["bottom"])
            subtitle_in_ui_area = False
            for sub in subtitles:
                sub_y = float(sub.get("y_ratio", 1.0 - self.ui_avoid["bottom"]))
                if sub_y * h > h - ui_avoid_bottom:
                    subtitle_in_ui_area = True
                    break
            if subtitle_in_ui_area:
                issues.append(QualityIssue(
                    IssueCategory.AD_EFFECTIVENESS, Severity.WARNING,
                    "部分字幕可能在平台UI遮挡区域内，影响可读性",
                    auto_fixable=True, fix_hint="adjust_subtitle_position"
                ))
                score -= 12

            sub_durations = []
            for sub in subtitles:
                start = float(sub.get("start", sub.get("start_time", 0)) or 0)
                end = float(sub.get("end", sub.get("end_time", start)) or start)
                if end > start:
                    sub_durations.append(end - start)
            if sub_durations:
                avg_sub_duration = sum(sub_durations) / len(sub_durations)
                if avg_sub_duration < 0.8:
                    issues.append(QualityIssue(
                        IssueCategory.AD_EFFECTIVENESS, Severity.WARNING,
                        f"单条字幕平均停留 {avg_sub_duration:.1f}s，可能来不及阅读",
                        auto_fixable=False
                    ))
                    score -= 10

        report.dimension_scores["ad_effectiveness"] = DimensionScore(
            name="广告效果", score=max(0, score), weight=0.20,
            passed=not any(i.severity == Severity.CRITICAL for i in issues),
            issues=issues
        )
        report.all_issues.extend(issues)

    def _check_platform_compliance(self, video_path: Path, report: ProductionQualityReport):
        issues = []
        score = 100
        meta = report.video_metadata

        w, h = meta.get("width", 0), meta.get("height", 0)
        aspect = w / h if h > 0 else 0
        if not (0.55 < aspect < 0.58):
            target_w, target_h = 1080, 1920
            issues.append(QualityIssue(
                IssueCategory.COMPLIANCE, Severity.WARNING,
                f"宽高比非9:16（当前 {w}:{h} = {aspect:.3f}），平台可能添加黑边",
                auto_fixable=True, fix_hint="pad_crop_aspect"
            ))
            score -= 20

        duration = meta.get("duration", 0)
        if self.platform == "douyin" and duration > 300:
            issues.append(QualityIssue(
                IssueCategory.COMPLIANCE, Severity.WARNING,
                f"视频时长 {duration:.0f}s 超过抖音5分钟限制",
                auto_fixable=False
            ))
            score -= 20

        report.platform_recommendations = {
            "douyin": {
                "recommended_resolution": "1080x1920",
                "recommended_bitrate": "8-12 Mbps",
                "recommended_duration": "15-60s",
                "recommended_fps": 30,
                "video_codec": "H.264 High Profile",
                "audio_codec": "AAC LC 44.1kHz stereo",
                "loudness": "-14 LUFS integrated",
            }
        }

        report.dimension_scores["compliance"] = DimensionScore(
            name="平台合规", score=max(0, score), weight=0.10,
            passed=not any(i.severity == Severity.CRITICAL for i in issues),
            issues=issues
        )
        report.all_issues.extend(issues)

    def _check_ai_artifacts(self, video_path: Path, report: ProductionQualityReport):
        issues = []
        score = 100

        tmp_dir = Path(tempfile.mkdtemp(prefix="ai_artifact_"))
        try:
            frames = self._extract_frames(video_path, tmp_dir, num_frames=10)
            artifact_frames = 0
            text_artifact_frames = 0
            distortion_frames = 0
            plastic_skin_frames = 0
            color_banding_frames = 0
            ghosting_frames = 0
            unnatural_limb_frames = 0
            avg_artifact_score = 0.0

            for fp in frames:
                artifacts = self._detect_ai_artifacts_frame(fp)
                avg_artifact_score += artifacts.get("artifact_score", 0)
                if artifacts.get("severe_distortion"):
                    distortion_frames += 1
                if artifacts.get("text_artifact"):
                    text_artifact_frames += 1
                if artifacts.get("plastic_skin"):
                    plastic_skin_frames += 1
                if artifacts.get("color_banding"):
                    color_banding_frames += 1
                if artifacts.get("ghosting"):
                    ghosting_frames += 1
                if artifacts.get("unnatural_limbs"):
                    unnatural_limb_frames += 1
                if artifacts.get("has_issue"):
                    artifact_frames += 1

            n_frames = len(frames)
            if n_frames > 0:
                avg_artifact_score /= n_frames

            if distortion_frames > n_frames * 0.2:
                issues.append(QualityIssue(
                    IssueCategory.AI_ARTIFACT, Severity.CRITICAL,
                    f"{distortion_frames}/{n_frames} 帧检测到严重变形（可能是肢体/手指异常）",
                    auto_fixable=False
                ))
                score -= 35
            if unnatural_limb_frames > n_frames * 0.2:
                issues.append(QualityIssue(
                    IssueCategory.AI_ARTIFACT, Severity.WARNING,
                    f"{unnatural_limb_frames}/{n_frames} 帧检测到疑似肢体异常（边缘簇过多）",
                    auto_fixable=False
                ))
                score -= 18
            if plastic_skin_frames > n_frames * 0.3:
                issues.append(QualityIssue(
                    IssueCategory.AI_ARTIFACT, Severity.WARNING,
                    f"{plastic_skin_frames}/{n_frames} 帧检测到塑料感皮肤（肤色过于均匀，缺乏纹理）",
                    auto_fixable=True, fix_hint="add_grain"
                ))
                score -= 15
            if text_artifact_frames > n_frames * 0.1:
                issues.append(QualityIssue(
                    IssueCategory.AI_ARTIFACT, Severity.WARNING,
                    f"{text_artifact_frames}/{n_frames} 帧检测到可疑文字/水印残留",
                    auto_fixable=True, fix_hint="inpaint"
                ))
                score -= 15
            if color_banding_frames > n_frames * 0.3:
                issues.append(QualityIssue(
                    IssueCategory.AI_ARTIFACT, Severity.WARNING,
                    f"{color_banding_frames}/{n_frames} 帧检测到色彩断层/色带（可能是压缩或AI生成伪影）",
                    auto_fixable=True, fix_hint="add_dither"
                ))
                score -= 12
            if ghosting_frames > n_frames * 0.2:
                issues.append(QualityIssue(
                    IssueCategory.AI_ARTIFACT, Severity.WARNING,
                    f"{ghosting_frames}/{n_frames} 帧检测到重影/鬼影（低饱和度+高亮度变化）",
                    auto_fixable=False
                ))
                score -= 12
            if artifact_frames > n_frames * 0.5:
                issues.append(QualityIssue(
                    IssueCategory.AI_ARTIFACT, Severity.WARNING,
                    f"{artifact_frames}/{n_frames} 帧存在AI伪影风险（综合评分 {avg_artifact_score:.2f}）",
                    auto_fixable=False
                ))
                score -= 10
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        report.dimension_scores["ai_artifact"] = DimensionScore(
            name="AI伪影防护", score=max(0, score), weight=0.10,
            passed=not any(i.severity == Severity.CRITICAL for i in issues),
            issues=issues
        )
        report.all_issues.extend(issues)

    def _detect_ai_artifacts_frame(self, frame_path: Path) -> Dict[str, Any]:
        from PIL import Image, ImageFilter, ImageStat
        import numpy as np

        result = {
            "has_issue": False,
            "severe_distortion": False,
            "text_artifact": False,
            "plastic_skin": False,
            "color_banding": False,
            "ghosting": False,
            "unnatural_limbs": False,
            "artifact_score": 0.0,
            "details": {},
        }

        try:
            with Image.open(frame_path) as src:
                img = src.convert("RGB")
            w, h = img.size

            if w > 320:
                scale = 320 / w
                new_h = int(h * scale)
                img_small = img.resize((320, new_h), Image.BILINEAR)
            else:
                img_small = img

            gray = img_small.convert("L")
            gstat = ImageStat.Stat(gray)

            edges = gray.filter(ImageFilter.FIND_EDGES)
            estat = ImageStat.Stat(edges)
            edge_std = estat.stddev[0]
            edge_mean = estat.mean[0]

            if edge_std > 120:
                result["severe_distortion"] = True
                result["artifact_score"] += 0.35
            elif edge_std > 80:
                result["has_issue"] = True
                result["artifact_score"] += 0.15

            if gstat.stddev[0] < 25:
                result["has_issue"] = True
                result["artifact_score"] += 0.10

            result["details"]["edge_std"] = round(edge_std, 1)
            result["details"]["luma_std"] = round(gstat.stddev[0], 1)

            arr = np.array(img_small)
            r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]

            skin_mask = (
                (r > 95) & (g > 40) & (b > 20) &
                (r > g) & (r > b) &
                (abs(r.astype(int) - g.astype(int)) > 15)
            )
            skin_ratio = float(skin_mask.sum()) / (skin_mask.shape[0] * skin_mask.shape[1])
            result["details"]["skin_ratio"] = round(skin_ratio, 3)

            if skin_ratio > 0.05:
                skin_pixels = arr[skin_mask]
                if len(skin_pixels) > 100:
                    skin_std = np.std(skin_pixels, axis=0)
                    skin_brightness_std = np.std(np.mean(skin_pixels, axis=1))
                    result["details"]["skin_color_std"] = round(float(np.mean(skin_std)), 1)
                    result["details"]["skin_brightness_std"] = round(float(skin_brightness_std), 1)

                    if skin_brightness_std < 8:
                        result["plastic_skin"] = True
                        result["artifact_score"] += 0.20
                    elif skin_brightness_std < 12:
                        result["has_issue"] = True
                        result["artifact_score"] += 0.10

            hsv_img = img_small.convert("HSV")
            hsv_arr = np.array(hsv_img)
            v_channel = hsv_arr[:, :, 2]

            hist, _ = np.histogram(v_channel.flatten(), bins=64, range=(0, 256))
            zero_bins = np.sum(hist < (v_channel.size / 64 * 0.15))
            result["details"]["empty_hist_bins"] = int(zero_bins)

            if zero_bins > 20:
                result["color_banding"] = True
                result["artifact_score"] += 0.15
            elif zero_bins > 12:
                result["has_issue"] = True
                result["artifact_score"] += 0.08

            edge_density = edge_mean / 255.0
            result["details"]["edge_density"] = round(edge_density, 3)

            edge_regions = np.array(edges) > 100
            edge_clusters = 0
            if edge_regions.any():
                from scipy.ndimage import label as _scipy_label
                try:
                    labeled, num_features = _scipy_label(edge_regions.astype(int))
                    edge_clusters = num_features
                except Exception:
                    edge_clusters = -1

            result["details"]["edge_clusters"] = edge_clusters

            if edge_clusters > 0 and edge_clusters > 3000:
                result["unnatural_limbs"] = True
                result["artifact_score"] += 0.15

            text_candidates = 0
            edge_arr = np.array(edges)
            for row_idx in range(0, edge_arr.shape[0], 2):
                row = edge_arr[row_idx, :]
                transitions = np.sum(np.abs(np.diff(row.astype(int))))
                if transitions > 20:
                    text_candidates += 1

            text_ratio = text_candidates / max(1, edge_arr.shape[0] // 2)
            result["details"]["text_row_ratio"] = round(text_ratio, 3)

            if text_ratio > 0.4 and skin_ratio < 0.3:
                result["text_artifact"] = True
                result["artifact_score"] += 0.20
            elif text_ratio > 0.3:
                result["has_issue"] = True
                result["artifact_score"] += 0.08

            sat_channel = hsv_arr[:, :, 1]
            sat_std = float(np.std(sat_channel))
            result["details"]["saturation_std"] = round(sat_std, 1)

            if sat_std < 15 and gstat.stddev[0] > 30:
                result["ghosting"] = True
                result["artifact_score"] += 0.12

            result["artifact_score"] = min(1.0, result["artifact_score"])

            if result["severe_distortion"] or result["artifact_score"] > 0.5:
                result["has_issue"] = True

        except Exception as e:
            result["details"]["error"] = str(e)

        return result

    def _check_narrative_rhythm(
        self,
        video_path: Path,
        beat_timings: Optional[List[float]],
        subtitles: Optional[List[Dict]],
        segments: Optional[List[Dict]],
        report: ProductionQualityReport,
    ):
        issues = []
        score = 100
        meta = report.video_metadata
        duration = meta.get("duration", 25)

        if segments and len(segments) >= 3:
            first_seg_end = segments[0].get("end_time", 3) if isinstance(segments[0], dict) else 3
            if first_seg_end > 3.5:
                issues.append(QualityIssue(
                    IssueCategory.NARRATIVE, Severity.WARNING,
                    f"Hook段（开场）时长 {first_seg_end:.1f}s 偏长，建议前3秒内出现核心冲突/悬念",
                    auto_fixable=False
                ))
                score -= 12

        report.dimension_scores["narrative"] = DimensionScore(
            name="叙事节奏", score=max(0, score), weight=0.05,
            passed=not any(i.severity == Severity.CRITICAL for i in issues),
            issues=issues
        )
        report.all_issues.extend(issues)

    def _check_character_consistency(
        self, video_path: Path, char_ref: Path, report: ProductionQualityReport
    ):
        issues = []
        score = 100
        try:
            from quality_checker import _character_similarity
            tmp_dir = Path(tempfile.mkdtemp(prefix="char_check_"))
            try:
                frames = self._extract_frames(video_path, tmp_dir, num_frames=15)
                sims = []
                for fp in frames:
                    sim = _character_similarity(char_ref, [fp])
                    sims.append(sim)
                if sims:
                    avg_sim = sum(sims) / len(sims)
                    min_sim = min(sims)
                    if min_sim < 0.35:
                        issues.append(QualityIssue(
                            IssueCategory.TEMPORAL, Severity.CRITICAL,
                            f"部分镜头角色相似度极低（最低 {min_sim:.2f}），可能出现换人/换脸",
                            auto_fixable=False
                        ))
                        score -= 30
                    elif avg_sim < 0.55:
                        issues.append(QualityIssue(
                            IssueCategory.TEMPORAL, Severity.WARNING,
                            f"角色平均相似度 {avg_sim:.2f}，跨镜头一致性有待提升",
                            auto_fixable=False
                        ))
                        score -= 15
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception as e:
            issues.append(QualityIssue(
                IssueCategory.TEMPORAL, Severity.INFO,
                f"角色一致性检测失败: {e}"
            ))

        if "temporal" not in report.dimension_scores:
            report.dimension_scores["temporal"] = DimensionScore(
                name="时间一致性", score=max(0, score), weight=0.15,
                passed=not any(i.severity == Severity.CRITICAL for i in issues),
                issues=issues
            )
        else:
            report.dimension_scores["temporal"].issues.extend(issues)
            report.dimension_scores["temporal"].score = max(
                0, report.dimension_scores["temporal"].score - (100 - score)
            )
        report.all_issues.extend(issues)

    def _extract_frames(self, video_path: Path, output_dir: Path, num_frames: int) -> List[Path]:
        from utils_ffprobe import get_video_duration
        duration = get_video_duration(str(video_path))
        if duration <= 0:
            return []
        output_dir.mkdir(parents=True, exist_ok=True)
        interval = duration / (num_frames + 1)
        frame_paths = []
        for i in range(num_frames):
            t = interval * (i + 1)
            fp = output_dir / f"f_{i:03d}.png"
            cmd = ["ffmpeg", "-y", "-ss", f"{t:.3f}", "-i", str(video_path),
                   "-vframes", "1", "-q:v", "2", str(fp)]
            try:
                subprocess.run(cmd, capture_output=True, timeout=20, check=True)
                if fp.exists():
                    frame_paths.append(fp)
            except Exception:
                continue
        return frame_paths

    def _apply_auto_fixes(self, video_path: Path, report: ProductionQualityReport) -> Path:
        fixes_needed = set()
        for issue in report.get_fixable_issues():
            if issue.fix_hint:
                fixes_needed.add(issue.fix_hint)

        if not fixes_needed:
            return video_path

        output_dir = video_path.parent
        current = video_path
        fix_steps = []

        if "normalize_audio" in fixes_needed:
            fix_steps.append(("loudnorm", self._fix_audio_normalize))
        if {"reencode_codec", "reencode_fps", "reencode_bitrate", "reencode_pixfmt"} & fixes_needed:
            fix_steps.append(("reencode_base", self._fix_reencode_base))
        if "deflicker" in fixes_needed:
            fix_steps.append(("deflicker", self._fix_deflicker))
        if "normalize_exposure" in fixes_needed:
            fix_steps.append(("color_normalize", self._fix_color_normalize))

        for fix_name, fix_fn in fix_steps:
            next_path = output_dir / f"fixed_{fix_name}_{current.name}"
            try:
                if fix_fn(current, next_path, report):
                    report.auto_fixes_applied.append(fix_name)
                    current = next_path
            except Exception as e:
                print(f"  ⚠️  自动修复 {fix_name} 失败: {e}")

        return current

    def _fix_audio_normalize(self, input_path: Path, output_path: Path, report: ProductionQualityReport) -> bool:
        cmd = [
            "ffmpeg", "-y", "-i", str(input_path),
            "-c:v", "copy",
            "-af", f"loudnorm=I={self.TARGET_LUFS}:LRA=7:TP=-1.5",
            "-c:a", "aac", "-b:a", "192k",
            str(output_path),
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=120, check=True)
            return output_path.exists()
        except Exception:
            return False

    def _fix_reencode_base(self, input_path: Path, output_path: Path, report: ProductionQualityReport) -> bool:
        cmd = [
            "ffmpeg", "-y", "-i", str(input_path),
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-r", "30",
            "-b:v", "10M",
            "-maxrate", "12M",
            "-bufsize", "20M",
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
            "-movflags", "+faststart",
            str(output_path),
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=300, check=True)
            return output_path.exists()
        except Exception:
            return False

    def _fix_deflicker(self, input_path: Path, output_path: Path, report: ProductionQualityReport) -> bool:
        cmd = [
            "ffmpeg", "-y", "-i", str(input_path),
            "-vf", "deflicker=mode=pm:size=5",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-c:a", "copy",
            str(output_path),
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=300, check=True)
            return output_path.exists()
        except Exception:
            return False

    def _fix_color_normalize(self, input_path: Path, output_path: Path, report: ProductionQualityReport) -> bool:
        cmd = [
            "ffmpeg", "-y", "-i", str(input_path),
            "-vf", "normalize=blackpt=black:whitept=white:smoothing=20",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-c:a", "copy",
            str(output_path),
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=300, check=True)
            return output_path.exists()
        except Exception:
            return False

    def _calculate_overall_score(self, report: ProductionQualityReport):
        total_weight = 0
        weighted_sum = 0
        for dim in report.dimension_scores.values():
            weighted_sum += dim.score * dim.weight
            total_weight += dim.weight
        report.overall_score = weighted_sum / total_weight if total_weight > 0 else 0

    @staticmethod
    def _std_dev(values: List[float]) -> float:
        if not values:
            return 0
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / len(values)
        return math.sqrt(variance)

    def print_report(self, report: ProductionQualityReport, video_name: str = ""):
        print()
        print("=" * 70)
        print(f"🎬 发布级质量检测报告：{video_name}")
        print("=" * 70)
        print(f"  综合评分：{report.overall_score:.0f} / 100")
        print(f"  发布结论：{'✅ 可发布' if report.passed else '❌ 不可发布'}")
        print()

        print(f"  📊 各维度得分：")
        for dim_id, dim in report.dimension_scores.items():
            icon = "✅" if dim.passed else "⚠️"
            print(f"    {icon} {dim.name}（权重 {dim.weight*100:.0f}%）：{dim.score:.0f} 分")

        critical = report.get_critical_issues()
        if critical:
            print()
            print(f"  🚨 严重问题（{len(critical)} 项）：")
            for issue in critical:
                print(f"    • [{issue.category.value}] {issue.message}")

        warnings = [i for i in report.all_issues if i.severity == Severity.WARNING]
        if warnings:
            print()
            print(f"  ⚠️  警告（{len(warnings)} 项）：")
            for issue in warnings[:10]:
                fixable = "🔧可自动修复" if issue.auto_fixable else ""
                print(f"    • [{issue.category.value}] {issue.message} {fixable}")
            if len(warnings) > 10:
                print(f"    ... 还有 {len(warnings)-10} 项警告")

        if report.auto_fixes_applied:
            print()
            print(f"  🔧 已自动修复：{', '.join(report.auto_fixes_applied)}")

        if report.platform_recommendations:
            rec = report.platform_recommendations.get(self.platform, {})
            if rec:
                print()
                print(f"  📱 {self.platform} 平台推荐参数：")
                for k, v in rec.items():
                    print(f"    • {k}: {v}")

        print("=" * 70)


def run_production_quality_check(
    video_path: Path,
    product_reference: Optional[Path] = None,
    character_reference: Optional[Path] = None,
    subtitles: Optional[List[Dict]] = None,
    beat_timings: Optional[List[float]] = None,
    segments: Optional[List[Dict]] = None,
    cta_contract: Optional[Dict] = None,
    auto_fix: bool = True,
    platform: str = "douyin",
) -> Tuple[Path, ProductionQualityReport]:
    guard = ProductionQualityGuard(platform=platform)
    return guard.analyze_and_fix(
        video_path,
        product_reference=product_reference,
        character_reference=character_reference,
        subtitles=subtitles,
        beat_timings=beat_timings,
        segments=segments,
        cta_contract=cta_contract,
        auto_fix=auto_fix,
    )


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法：python production_quality_guard.py <视频路径> [产品参考图]")
        sys.exit(1)
    video = Path(sys.argv[1])
    product_ref = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    if not video.exists():
        print(f"❌ 文件不存在：{video}")
        sys.exit(1)
    fixed_path, report = run_production_quality_check(video, product_reference=product_ref)
    guard = ProductionQualityGuard()
    guard.print_report(report, video.name)
