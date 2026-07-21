#!/usr/bin/env python3
"""
抖音平台适配配置

抖音短视频的核心特征：
- 3秒黄金钩子（前3秒必须抓住注意力）
- 快节奏（每5秒一个信息点/反转）
- 大字幕（占画面 1/10 ~ 1/8 高度）
- 口播语速快（200-220 字/分钟）
- 强 CTA 引导（点赞/关注/点击小黄车）
- 花字动效（重点词变色放大）
- BGM 卡点
"""

import copy
from typing import Dict, Any, List, Optional


# ============================================================
# 抖音平台默认配置
# ============================================================

DOUYIN_CONFIG = {
    # 视频规格（抖音官方推荐）
    "aspect_ratio": "9:16",
    "resolution": "1080x1920",  # 抖音推荐 1080p 竖屏
    "fps": 30,
    "bitrate": "6M",  # 1080p 推荐 4-8 Mbps
    "video_codec": "libx264",
    "audio_codec": "aac",
    "audio_bitrate": "160k",
    "audio_sample_rate": 44100,
    "gop_seconds": 2,  # GOP 大小（秒），抖音推荐 2 秒

    # 时长建议（秒）
    "ideal_duration": 15,  # 最佳时长 15-30 秒
    "min_duration": 7,
    "max_duration": 60,

    # 钩子配置
    "hook": {
        "golden_seconds": 3,  # 黄金 3 秒
        "hook_types": ["question", "shocking", "pain_point", "demonstration"],
        "subtitle_font_size_ratio": 0.06,  # 字号占画面高度比例
    },

    # 字幕配置
    "subtitle": {
        "font_size_ratio": 0.055,  # 字号占画面高度比例（比普通视频大）
        "stroke_width_ratio": 0.008,  # 描边宽度比例
        "position": "bottom",  # 底部居中
        "bottom_margin_ratio": 0.22,  # 底部边距比例（留出小黄车/购物车位置，约 422px @ 1920h）
        "animation": "pop",  # 默认花字动效
        "highlight_keywords": True,  # 关键词高亮
    },

    # 口播配置
    "voiceover": {
        "rate": 200,  # 语速（字/分钟），比正常快
        "voice": "energetic_female",  # 默认活力女声
        "bgm_ducking_volume": 0.25,  # BGM 闪避后音量（人声更突出）
    },

    # BGM 配置
    "bgm": {
        "volume": 0.3,  # BGM 基础音量（比人声低很多）
        "beat_sync": True,  # 卡点对齐
    },

    # 节奏配置
    "pacing": {
        "info_interval": 5,  # 每 5 秒一个信息点
        "transition_duration": 0.4,  # 转场快
        "shot_duration": 3,  # 单镜头平均时长
    },

    # CTA 配置
    "cta": {
        "end_card_duration": 2,  # 结尾留 2 秒 CTA
        "elements": ["product", "arrow", "text"],
    },
}


# ============================================================
# 抖音内容节奏模板（5段式，ratio 分配）
#
# 模板字段说明：
#   name:              模板名称
#   total_duration:    目标总时长（秒，含转场重叠前的原始时长）
#   segments:          5 段节奏定义
#     - index:         段索引（0-based）
#     - type:          段落类型（hook/pain/showcase/result/cta 等）
#     - ratio:         该段占总时长的比例
#     - purpose:       叙事目的（用于 prompt/字幕生成参考）
#   transition_duration: 转场时长（秒）
#   pace_style:        节奏风格：fast / moderate / cinematic
#
# 段时长计算：segment_duration = total_duration * ratio
# （实际最终成片时长 ≈ sum(segment_durations) - (n-1) * transition_duration）
# ============================================================

# 10秒硬广节奏：快节奏高密度，每句都在推信息
RHYTHM_10S_HARD_SALE = {
    "name": "10秒硬广快剪",
    "total_duration": 10,
    "segments": [
        {"index": 0, "type": "hook", "ratio": 0.18, "purpose": "抓注意力"},
        {"index": 1, "type": "pain", "ratio": 0.18, "purpose": "戳痛点"},
        {"index": 2, "type": "showcase", "ratio": 0.24, "purpose": "产品展示"},
        {"index": 3, "type": "result", "ratio": 0.20, "purpose": "效果展示"},
        {"index": 4, "type": "cta", "ratio": 0.20, "purpose": "行动号召"},
    ],
    "transition_duration": 0.2,
    "pace_style": "fast",
}

# 15秒经典节奏：钩子(2.25s) → 痛点(3s) → 产品(3.75s) → 效果(3s) → CTA(3s)
RHYTHM_15S_CLASSIC = {
    "name": "15秒经典带货",
    "total_duration": 15,
    "segments": [
        {"index": 0, "type": "hook", "ratio": 0.15, "purpose": "抓注意力"},
        {"index": 1, "type": "pain", "ratio": 0.20, "purpose": "戳痛点"},
        {"index": 2, "type": "showcase", "ratio": 0.25, "purpose": "产品展示"},
        {"index": 3, "type": "result", "ratio": 0.22, "purpose": "效果展示"},
        {"index": 4, "type": "cta", "ratio": 0.18, "purpose": "行动号召"},
    ],
    "transition_duration": 0.3,
    "pace_style": "fast",
}

# 20秒标准节奏：平衡型，信息密度适中
RHYTHM_20S_STANDARD = {
    "name": "20秒标准带货",
    "total_duration": 20,
    "segments": [
        {"index": 0, "type": "hook", "ratio": 0.15, "purpose": "抓注意力"},
        {"index": 1, "type": "pain", "ratio": 0.20, "purpose": "戳痛点"},
        {"index": 2, "type": "showcase", "ratio": 0.28, "purpose": "产品展示"},
        {"index": 3, "type": "result", "ratio": 0.22, "purpose": "效果展示"},
        {"index": 4, "type": "cta", "ratio": 0.15, "purpose": "行动号召"},
    ],
    "transition_duration": 0.4,
    "pace_style": "moderate",
}

# 25秒标准节奏：最接近现有 5×5s 的默认行为（向后兼容基准）
RHYTHM_25S_STANDARD = {
    "name": "25秒标准带货",
    "total_duration": 25,
    "segments": [
        {"index": 0, "type": "hook", "ratio": 0.16, "purpose": "抓注意力"},
        {"index": 1, "type": "pain", "ratio": 0.20, "purpose": "戳痛点"},
        {"index": 2, "type": "showcase", "ratio": 0.24, "purpose": "产品展示"},
        {"index": 3, "type": "result", "ratio": 0.22, "purpose": "效果展示"},
        {"index": 4, "type": "cta", "ratio": 0.18, "purpose": "行动号召"},
    ],
    "transition_duration": 0.5,
    "pace_style": "moderate",
}

# 30秒深度节奏：信息丰富，卖点更展开
RHYTHM_30S_DEEP = {
    "name": "30秒深度种草",
    "total_duration": 30,
    "segments": [
        {"index": 0, "type": "hook", "ratio": 0.12, "purpose": "抓注意力"},
        {"index": 1, "type": "pain", "ratio": 0.20, "purpose": "戳痛点+共鸣"},
        {"index": 2, "type": "showcase", "ratio": 0.26, "purpose": "产品展示+卖点"},
        {"index": 3, "type": "result", "ratio": 0.24, "purpose": "效果展示"},
        {"index": 4, "type": "cta", "ratio": 0.18, "purpose": "行动号召"},
    ],
    "transition_duration": 0.5,
    "pace_style": "moderate",
}

# 60秒深度节奏：舒展型，电影感叙事
RHYTHM_60S_CINEMATIC = {
    "name": "60秒电影感深度",
    "total_duration": 60,
    "segments": [
        {"index": 0, "type": "hook", "ratio": 0.12, "purpose": "场景建立+钩子"},
        {"index": 1, "type": "pain", "ratio": 0.20, "purpose": "痛点铺垫+故事"},
        {"index": 2, "type": "showcase", "ratio": 0.28, "purpose": "产品深度展示"},
        {"index": 3, "type": "result", "ratio": 0.26, "purpose": "效果+使用场景"},
        {"index": 4, "type": "cta", "ratio": 0.14, "purpose": "品牌+行动号召"},
    ],
    "transition_duration": 0.8,
    "pace_style": "cinematic",
}

# P1 修复：新增 60 秒 moderate 模板，解决 style="moderate" 时只能匹配 30s 模板的问题
RHYTHM_60S_MODERATE = {
    "name": "60秒标准带货",
    "total_duration": 60,
    "segments": [
        {"index": 0, "type": "hook", "ratio": 0.10, "purpose": "快速抓注意力"},
        {"index": 1, "type": "pain", "ratio": 0.20, "purpose": "痛点共鸣"},
        {"index": 2, "type": "showcase", "ratio": 0.30, "purpose": "产品展示+卖点拆解"},
        {"index": 3, "type": "result", "ratio": 0.25, "purpose": "效果展示+证明"},
        {"index": 4, "type": "cta", "ratio": 0.15, "purpose": "行动号召"},
    ],
    "transition_duration": 0.6,
    "pace_style": "moderate",
}

# 所有已注册的节奏模板（按总时长升序）
_RHYTHM_TEMPLATES = [
    RHYTHM_10S_HARD_SALE,
    RHYTHM_15S_CLASSIC,
    RHYTHM_20S_STANDARD,
    RHYTHM_25S_STANDARD,
    RHYTHM_30S_DEEP,
    RHYTHM_60S_MODERATE,   # P1 修复：moderate 60s 模板（新增）
    RHYTHM_60S_CINEMATIC,
]


# P2-4: 风格-时长偏差安全阈值（倍数）
# 例：目标 10s、候选最近模板 60s，偏差 6x > 3x → 触发跨风格回退
_MAX_DURATION_RATIO = 3.0


def get_rhythm_template(
    total_duration: float,
    style: str = "moderate",
    product_type: str = "default",
) -> Dict[str, Any]:
    """
    根据总时长和节奏风格选择最合适的节奏模板（返回深拷贝）。

    选择策略：
      1. 对 total_duration <= 0 提前报错
      2. 按 pace_style 筛选候选模板（style="moderate" 时 fast/moderate 均可，
         cinematic 仅匹配 cinematic）
      3. 在候选中选择 total_duration 最接近的模板
      4. 若最近模板与目标偏差超过 _MAX_DURATION_RATIO 倍，跨风格回退到全部模板
      5. 若找不到匹配风格，回退到全部模板中选最接近的

    Args:
        total_duration: 目标总时长（秒），必须 > 0
        style: 节奏风格："fast" / "moderate" / "cinematic"
        product_type: 产品品类（影响节奏微调），如 "beauty" / "food" / "default"

    Returns:
        节奏模板字典（深拷贝，可安全修改），结构见模块顶部注释。
        模板 segments 中会额外注入每个段的实际 duration（秒），
        方便调用方直接使用，无需再乘 ratio。
    """
    # P3-11: 显式校验，不再静默选最近模板
    if total_duration <= 0:
        raise ValueError(
            f"total_duration 必须 > 0，收到：{total_duration}。"
            "请检查 --target-duration 参数或 duration 配置。"
        )

    if not _RHYTHM_TEMPLATES:
        raise RuntimeError("节奏模板列表为空，请检查 douyin_adapter.py")

    # 风格筛选
    if style == "fast":
        candidates = [t for t in _RHYTHM_TEMPLATES if t["pace_style"] == "fast"]
    elif style == "cinematic":
        candidates = [t for t in _RHYTHM_TEMPLATES if t["pace_style"] == "cinematic"]
    else:  # moderate：接受 moderate，fast 作为备选
        candidates = [t for t in _RHYTHM_TEMPLATES if t["pace_style"] in ("moderate", "fast")]

    if not candidates:
        # 风格无匹配时回退到全部模板
        candidates = _RHYTHM_TEMPLATES

    # P2-4: 选风格内最近模板，若偏差过大则跨风格回退
    best_in_style = min(candidates, key=lambda t: abs(t["total_duration"] - total_duration))
    ratio = max(
        best_in_style["total_duration"] / total_duration,
        total_duration / best_in_style["total_duration"],
    )
    if ratio > _MAX_DURATION_RATIO:
        # 跨风格回退：在全部模板中选最接近的
        best = min(_RHYTHM_TEMPLATES, key=lambda t: abs(t["total_duration"] - total_duration))
        import warnings
        warnings.warn(
            f"[douyin_adapter] 节奏风格 '{style}' 内最近模板 "
            f"'{best_in_style['name']}'（{best_in_style['total_duration']}s）"
            f" 与目标 {total_duration}s 偏差 {ratio:.1f}x > {_MAX_DURATION_RATIO}x，"
            f"已跨风格回退到 '{best['name']}'（{best['total_duration']}s）。"
            "建议调整 --target-duration 或 --rhythm-style 参数。",
            stacklevel=2,
        )
    else:
        best = best_in_style

    # 深拷贝，防止外部修改污染全局模板
    template = copy.deepcopy(best)

    # 预计算每段实际时长（秒），注入到 segments 中
    total = template["total_duration"]
    for seg in template["segments"]:
        seg["duration"] = round(total * seg["ratio"], 2)

    # P0-4 修复：把超过可灵 API 单段上限（15s）的段 cap 住，
    # 并重新归一化 ratio 和 total_duration，避免拉伸超过 0.5x 导致画质崩坏
    _API_MAX_CLIP = 15.0
    _any_capped = False
    for seg in template["segments"]:
        if seg["duration"] > _API_MAX_CLIP:
            import warnings
            warnings.warn(
                f"[douyin_adapter] 节奏模板段 [{seg['index']}] {seg['type']} "
                f"时长 {seg['duration']:.1f}s 超过可灵 API 上限 {_API_MAX_CLIP}s，"
                f"已自动 cap 到 {_API_MAX_CLIP}s。"
                "建议减小 --target-duration 或增大 --duration。",
                stacklevel=2,
            )
            seg["duration"] = _API_MAX_CLIP
            _any_capped = True

    if _any_capped:
        # 重新计算 total_duration 和各段 ratio
        new_total = sum(seg["duration"] for seg in template["segments"])
        template["total_duration"] = round(new_total, 2)
        for seg in template["segments"]:
            seg["ratio"] = round(seg["duration"] / new_total, 4)

    # P1-C：品类节奏微调
    # 在现有模板时长基础上做 ±10-15% 缩放，不新增模板，不改变段数量。
    # 美妆/护肤/香水 → cinematic 节奏偏好：showcase/reveal 段稍长（×1.10），hook 段维持
    # 快消/零食/饮料 → fast 节奏偏好：所有段稍短（×0.90），让视频更紧凑
    _SLOW_TYPES = {"beauty", "skincare", "fragrance", "luxury", "cosmetics"}
    _FAST_TYPES = {"food", "snack", "drink", "beverage", "fmcg", "daily"}
    _pt = (product_type or "default").lower()

    if _pt in _SLOW_TYPES:
        # showcase/reveal/highlight/solution 段拉长 10%，hook/cta 维持不变
        _slow_segs = {"showcase", "reveal", "highlight", "solution", "demo", "proof"}
        for _seg in template["segments"]:
            _narrative = _seg.get("narrative", _seg.get("type", ""))
            if _narrative in _slow_segs:
                _seg["duration"] = round(_seg["duration"] * 1.10, 2)
        # 重新归一化 total_duration
        template["total_duration"] = round(
            sum(_s["duration"] for _s in template["segments"]), 2
        )
        print(f"   P1-C 节奏微调：{_pt} 品类，showcase 类段 +10%")
    elif _pt in _FAST_TYPES:
        # 所有段缩短 10%
        for _seg in template["segments"]:
            _seg["duration"] = round(_seg["duration"] * 0.90, 2)
        template["total_duration"] = round(
            sum(_s["duration"] for _s in template["segments"]), 2
        )
        print(f"   P1-C 节奏微调：{_pt} 品类，所有段 -10%")

    return template


def adapt_rhythm_template_to_segments(
    template: Dict[str, Any],
    target_segments: int,
) -> Dict[str, Any]:
    """
    将节奏模板适配到目标段数（通过合并或拆分段落，保持叙事弧完整）。

    核心原则：
    - 保持 hook → turning → showcase → result → cta 的叙事弧完整性
    - 段数不足时合并相邻功能，段数过多时拆分展示类段落
    - 总时长和转场时长保持不变，重新分配各段时长比例

    Args:
        template: 原始节奏模板（5段）
        target_segments: 目标段数（3-7）

    Returns:
        适配后的节奏模板（深拷贝）
    """
    if target_segments <= 0:
        raise ValueError(f"target_segments 必须 > 0，收到：{target_segments}")

    current_segs = len(template["segments"])
    if current_segs == target_segments:
        return copy.deepcopy(template)

    result = copy.deepcopy(template)
    segs = result["segments"]
    transition = result.get("transition_duration", 0.3)

    # 叙事功能优先级：hook(必须) → cta(必须) → showcase(重要) → turning(重要) → result(重要)
    # 段数 < 5 时，按优先级从低到高合并

    if target_segments < current_segs:
        # 合并策略：从低优先级功能开始合并到相邻段
        # 优先级从低到高：turning/pain (可合并到 hook) → result (可合并到 showcase)
        while len(segs) > target_segments and len(segs) > 3:
            # 找到可合并的最低优先级段
            merge_candidates = []

            # turning/pain 段可合并到前一段（hook）
            _turning_keywords = ("turning", "turn", "pain", "pain_amplify", "problem", "conflict")
            _result_keywords = ("result", "after", "effect", "outcome", "compare_result", "transformation")
            for i, seg in enumerate(segs):
                n = seg.get("narrative", seg.get("type", ""))
                if any(k in n for k in _turning_keywords) and i > 0 and i < len(segs) - 1:
                    merge_candidates.append((i, "turning"))  # 合并 turning 类到前一段
                elif any(k in n for k in _result_keywords) and i > 0 and i < len(segs) - 1:
                    merge_candidates.append((i, "result"))  # result 可合并到 showcase

            if not merge_candidates:
                break

            # 选择最优先合并的（turning 优先于 result）
            merge_candidates.sort(key=lambda x: 0 if x[1] == "turning" else 1)
            merge_idx, _ = merge_candidates[0]

            # 合并：前一段时长 += 当前段时长，当前段删除
            if merge_idx > 0:
                segs[merge_idx - 1]["duration"] = round(
                    segs[merge_idx - 1]["duration"] + segs[merge_idx]["duration"], 2
                )
                # 合并叙事类型
                prev_type = segs[merge_idx - 1].get("narrative", segs[merge_idx - 1].get("type", ""))
                curr_type = segs[merge_idx].get("narrative", segs[merge_idx].get("type", ""))
                segs[merge_idx - 1]["narrative"] = f"{prev_type}+{curr_type}"
                segs[merge_idx - 1]["type"] = segs[merge_idx - 1].get("narrative", prev_type)
                # purpose 合并
                prev_purpose = segs[merge_idx - 1].get("purpose", "")
                curr_purpose = segs[merge_idx].get("purpose", "")
                segs[merge_idx - 1]["purpose"] = f"{prev_purpose} & {curr_purpose}"
                del segs[merge_idx]

    elif target_segments > current_segs:
        # 拆分策略：优先拆分 showcase 段，其次 turning 段
        # 在中间位置拆分成两段，时长比例约 1:1
        while len(segs) < target_segments and len(segs) < 8:
            # 找到可拆分的最高优先级段
            split_idx = -1
            for i, seg in enumerate(segs):
                n = seg.get("narrative", seg.get("type", ""))
                if "showcase" in n or n in ("showcase", "show", "demo"):
                    split_idx = i
                    break

            if split_idx < 0:
                # 没有 showcase 可拆，拆 turning
                for i, seg in enumerate(segs):
                    n = seg.get("narrative", seg.get("type", ""))
                    if "turning" in n or n in ("turning", "turn"):
                        split_idx = i
                        break

            if split_idx < 0 or split_idx >= len(segs) - 1:
                break

            # 拆分成两段，时长按 45:55 分配（前短后长，符合节奏递进）
            orig_seg = segs[split_idx]
            orig_dur = orig_seg["duration"]
            dur1 = round(orig_dur * 0.45, 2)
            dur2 = round(orig_dur - dur1, 2)

            # 第一段
            seg1 = copy.deepcopy(orig_seg)
            seg1["duration"] = dur1
            seg1["index"] = split_idx

            # 第二段（加个后缀区分）
            seg2 = copy.deepcopy(orig_seg)
            seg2["duration"] = dur2
            seg2["index"] = split_idx + 1

            # 更新叙事类型
            narrative = orig_seg.get("narrative", orig_seg.get("type", ""))
            seg1["narrative"] = f"{narrative}_1"
            seg2["narrative"] = f"{narrative}_2"
            seg1["type"] = seg1["narrative"]
            seg2["type"] = seg2["narrative"]

            # 替换原位置
            segs[split_idx] = seg1
            segs.insert(split_idx + 1, seg2)

    # 重新计算索引、比例和总时长
    new_total = sum(s["duration"] for s in segs)
    for i, seg in enumerate(segs):
        seg["index"] = i
        seg["ratio"] = round(seg["duration"] / new_total, 4)

    result["total_duration"] = round(new_total, 2)
    result["segments"] = segs

    # 如果段数变化了，重新计算转场影响下的实际总时长
    if len(segs) > 1:
        actual_total = new_total - transition * (len(segs) - 1)
        result["actual_total_duration"] = round(actual_total, 2)

    return result


def compute_segment_timeline(
    template: Dict[str, Any],
    seg_indices: Optional[list] = None,
    segment_durations: Optional[Dict[int, float]] = None,
    transitions: Optional[List[Dict[str, Any]]] = None,
) -> list[dict]:
    """
    根据节奏模板计算每段在最终成片中的实际时间轴（考虑转场重叠）。

    支持部分成功段过滤：传入 seg_indices 白名单后，仅返回这些段，
    并按合并后的顺序重新计算 start/end。

    Args:
        template: 节奏模板（来自 get_rhythm_template）
        seg_indices: 实际成功的段索引白名单（0-based），None 表示全部成功
        segment_durations: ffprobe 实测片段时长映射；提供后覆盖模板时长
        transitions: 按实际合并顺序排列的转场决策；duration=0 表示真实无转场

    Returns:
        时间轴列表，每项包含：
            - index:    原始段索引
            - start:    成片中的开始时间（秒）
            - end:      成片中的结束时间（秒）
            - duration: 段时长（秒，不含转场重叠部分的净时长）
            - type:     段落类型
            - purpose:  叙事目的
    """
    default_transition = float(template.get("transition_duration", 0.3))
    segments = template["segments"]

    # 白名单过滤
    if seg_indices is not None:
        index_set = set(seg_indices)
        segments = [s for s in segments if s["index"] in index_set]

    if transitions is not None and len(transitions) != max(0, len(segments) - 1):
        raise ValueError(
            f"转场数量必须等于镜头数减一：收到 {len(transitions)} 个转场 / {len(segments)} 个镜头"
        )

    timeline = []
    current_start = 0.0
    for i, seg in enumerate(segments):
        dur = float((segment_durations or {}).get(seg["index"], seg["duration"]))
        if dur <= 0:
            raise ValueError(f"镜头 {seg['index']} 的实测时长必须大于 0：{dur}")
        # 除第一段外，每段与前一段有 transition 秒的重叠
        if i > 0:
            decision = transitions[i - 1] if transitions is not None else None
            raw_transition = decision.get("duration") if decision is not None else None
            transition = default_transition if raw_transition is None else float(raw_transition)
            if transition < 0 or transition >= min(float(timeline[-1]["duration"]), dur):
                raise ValueError(
                    f"边界 {i - 1} 的转场时长无效：{transition}s；"
                    "必须大于等于 0 且小于相邻镜头时长"
                )
            current_start -= transition
        end = current_start + dur
        timeline.append({
            "index": seg["index"],
            "start": round(current_start, 3),
            "end": round(end, 3),
            "duration": dur,
            "type": seg.get("type", ""),
            "purpose": seg.get("purpose", ""),
        })
        current_start = end

    return timeline


def get_douyin_config(duration: int = 15) -> Dict[str, Any]:
    """
    根据视频时长获取抖音优化配置

    Args:
        duration: 视频总时长（秒）

    Returns:
        抖音优化配置字典
    """
    config = DOUYIN_CONFIG.copy()

    # 根据时长调整节奏
    if duration <= 15:
        config["pacing"]["info_interval"] = 3
        config["pacing"]["transition_duration"] = 0.3
        # P2-2: 10s 口播约 30 字，200字/分钟 = 3.3字/秒，符合抖音推荐（3~3.5字/秒）
        # 原 220字/分钟(3.7字/秒) 偏快，短视频听不清
        config["voiceover"]["rate"] = 200
    elif duration <= 30:
        config["pacing"]["info_interval"] = 5
        config["pacing"]["transition_duration"] = 0.4
        config["voiceover"]["rate"] = 200
    else:
        config["pacing"]["info_interval"] = 7
        config["pacing"]["transition_duration"] = 0.5
        config["voiceover"]["rate"] = 180

    return config


def optimize_subtitles_for_douyin(
    subtitles: list,
    video_height: int = 1280,
) -> list:
    """
    优化字幕以适配抖音风格

    - 字号放大
    - 重点词高亮标记
    - 底部留出小黄车位置（通过返回 margin_v 字段）
    - 左右留出安全区（防止被右侧点赞栏遮挡）

    Args:
        subtitles: 原始字幕列表
        video_height: 视频高度（用于计算字号）

    Returns:
        优化后的字幕列表（每条字幕包含 font_size / highlight / margin_v 等字段）
    """
    sub_config = DOUYIN_CONFIG["subtitle"]
    font_size = int(video_height * sub_config["font_size_ratio"])
    bottom_margin = int(video_height * sub_config["bottom_margin_ratio"])

    optimized = []
    for sub in subtitles:
        new_sub = sub.copy()
        new_sub["font_size"] = font_size
        new_sub["bottom_margin"] = bottom_margin

        # 标记关键词高亮（简单规则：数字、感叹号、"绝了"、"太"等词附近）
        text = sub.get("text", "")
        highlight_words = _extract_highlight_words(text)
        if highlight_words:
            new_sub["highlight"] = highlight_words

        optimized.append(new_sub)

    return optimized


def _extract_highlight_words(text: str) -> list[str]:
    """
    从文案中提取需要高亮的关键词（带边界检测，减少误匹配）

    Args:
        text: 文案文本

    Returns:
        高亮词列表
    """
    # 多字词高亮（不易误匹配）
    multi_char_keywords = [
        "绝了", "超级", "真的", "居然", "竟然",
        "99%", "100%", "免费", "福利", "限量", "秒杀",
        "赶紧", "立刻", "马上", "现在",
        "必看", "必入", "必买", "必备",
        "宝藏", "神器", "黑科技", "太好", "太绝",
        "最强", "最棒", "最好", "超快", "超级好用",
    ]

    # 单字/短词需要边界检测（避免"太"匹配"太阳"、"最"匹配"最近"等）
    short_keywords = ["太", "最", "超"]
    # 中文语境中的词边界：标点符号 + 空格 + 常见语气词
    import re
    boundary_chars = r"，。！？、；：""''（）【】《》 \n\r\t"

    found = []
    for kw in multi_char_keywords:
        if kw in text:
            found.append(kw)

    for kw in short_keywords:
        pattern = re.compile(
            rf"(?:^|[{re.escape(boundary_chars)}]){re.escape(kw)}[好棒强绝快多厉害香]"
        )
        if pattern.search(text):
            found.append(kw)

    # 最多高亮 2 个词（优先级：多字词 > 短词）
    return found[:2]


if __name__ == "__main__":
    print("🎵 抖音平台适配配置")
    print("=" * 50)
    print(f"默认比例：{DOUYIN_CONFIG['aspect_ratio']}")
    print(f"最佳时长：{DOUYIN_CONFIG['ideal_duration']} 秒")
    print(f"默认字号比例：{DOUYIN_CONFIG['subtitle']['font_size_ratio']}")
    print(f"默认语速：{DOUYIN_CONFIG['voiceover']['rate']} 字/分钟")
    print()

    print("⏱️  可用节奏模板：")
    for tpl in _RHYTHM_TEMPLATES:
        t = get_rhythm_template(tpl["total_duration"], style=tpl["pace_style"])
        print(f"\n  📌 {t['name']}（{t['pace_style']}，转场 {t['transition_duration']}s）")
        for seg in t["segments"]:
            print(f"     [{seg['index']}] {seg['duration']:>5.2f}s  {seg['type']:<10s}  {seg['purpose']}")
