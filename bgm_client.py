"""
BGM 音乐客户端

封装 FreeToUse Music API 调用，支持：
- 搜索音乐
- 按分类获取音乐
- 下载音乐（本地缓存，避免重复下载）
- 按产品品类自动选曲

API 文档：https://freetouse.com/api
基础地址：https://api.freetouse.com/v3
无需 API Key，公开访问。
"""

import os
import re
import hashlib
import json
import random
import threading
import time
from pathlib import Path
from typing import Optional, List, Dict, Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import (
    PROJECT_ROOT,
    BGM_CACHE_DIR,
    BGM_VOLUME,
)


# ============================================================
# 产品品类 -> BGM 风格映射
# ============================================================
# 每个品类对应多个搜索关键词/分类，随机选一个增加多样性
# 分类名来自 freetouse 的 67 个分类（Genre + Mood + Video）

BGM_STYLE_MAP = {
    "美妆": {
        "keywords": ["upbeat", "happy", "aesthetic", "chill", "vlog"],
        "min_duration": 20,
        "max_duration": 120,
        "order": "plays",
    },
    "食品": {
        "keywords": ["happy", "fun", "upbeat", "cooking", "summer"],
        "min_duration": 20,
        "max_duration": 120,
        "order": "plays",
    },
    "科技": {
        "keywords": ["technology", "corporate", "electronic", "modern", "inspiring"],
        "min_duration": 20,
        "max_duration": 120,
        "order": "downloads",
    },
    "服装": {
        "keywords": ["fashion", "cool", "edm", "trap", "hype"],
        "min_duration": 20,
        "max_duration": 120,
        "order": "plays",
    },
    "app": {
        "keywords": ["technology", "corporate", "inspiring", "modern", "upbeat"],
        "min_duration": 20,
        "max_duration": 120,
        "order": "downloads",
    },
    "汽车": {
        "keywords": ["energetic", "epic", "rock", "electronic", "powerful"],
        "min_duration": 20,
        "max_duration": 120,
        "order": "plays",
    },
    "房产": {
        "keywords": ["inspiring", "corporate", "calm", "peaceful", "uplifting"],
        "min_duration": 20,
        "max_duration": 120,
        "order": "downloads",
    },
    "教育": {
        "keywords": ["inspiring", "uplifting", "corporate", "calm", "happy"],
        "min_duration": 20,
        "max_duration": 120,
        "order": "downloads",
    },
    "医疗": {
        "keywords": ["calm", "peaceful", "inspiring", "corporate", "emotional"],
        "min_duration": 20,
        "max_duration": 120,
        "order": "downloads",
    },
    "家居": {
        "keywords": ["calm", "peaceful", "cozy", "ambient", "acoustic"],
        "min_duration": 20,
        "max_duration": 120,
        "order": "plays",
    },
    "default": {
        "keywords": ["upbeat", "happy", "energetic", "vlog", "advertising"],
        "min_duration": 20,
        "max_duration": 120,
        "order": "plays",
    },
}


# ============================================================
# 节奏等级 -> BGM 节奏关键词
# ============================================================
# 根据视频运镜节奏选择对应 BPM 的音乐

PACE_KEYWORDS = {
    "fast": ["fast", "energetic", "high energy", "uptempo", "intense"],
    "medium": ["upbeat", "moderate", "groove", "steady", "rhythmic"],
    "slow": ["chill", "calm", "ambient", "mellow", "relaxing"],
}


def _track_descriptor(track: Dict[str, Any]) -> str:
    values: List[str] = []

    def collect(value: Any) -> None:
        if isinstance(value, str):
            values.append(value.lower())
        elif isinstance(value, dict):
            for nested in value.values():
                collect(nested)
        elif isinstance(value, (list, tuple)):
            for nested in value:
                collect(nested)

    for key in ("title", "tags", "categories", "description"):
        collect(track.get(key))
    return " ".join(values)


def _add_terms(target: Dict[str, float], terms: set[str], weight: float) -> None:
    for term in terms:
        target[term] = max(target.get(term, 0.0), weight)


def _build_contract_term_weights(contract: Dict[str, Any]) -> tuple[Dict[str, float], Dict[str, float]]:
    term_groups = {
        "acoustic": {"acoustic", "guitar", "folk", "organic", "instrumental"},
        "pop": {"pop", "upbeat", "positive", "rhythmic", "commercial"},
        "lofi": {"lofi", "chill", "mellow", "relaxing"},
        "electronic": {"electronic", "modern", "technology", "synth"},
        "orchestral": {"orchestral", "cinematic", "inspiring", "emotional"},
        "jazz": {"jazz", "moody", "smooth", "warm"},
        "warm": {"warm", "cozy", "gentle", "authentic", "delightful", "calm"},
        "calm": {"calm", "quiet", "relaxing", "gentle", "slow"},
        "cool": {"cool", "modern", "electronic", "stylish", "technology"},
        "grand": {"grand", "inspiring", "cinematic", "uplifting"},
        "upbeat": {"upbeat", "positive", "rhythmic", "uplifting", "fresh"},
        "natural_origin": {"organic", "folk", "acoustic", "gentle", "calm", "fields", "nature", "authentic"},
        "product_demo": {"advertising", "commercial", "promo", "upbeat", "rhythmic", "positive", "modern", "lifestyle"},
        "high": {"energetic", "uptempo", "rhythmic", "intense", "fast"},
        "medium": {"steady", "groove", "rhythmic", "guitar", "instrumental", "moderate"},
        "low": {"calm", "ambient", "mellow", "gentle", "slow"},
        "bright": {"bright", "happy", "fresh", "light", "upbeat"},
        "dark": {"moody", "cinematic", "ambient", "deep"},
        "soft": {"soft", "gentle", "warm", "acoustic"},
        "balanced": {"balanced", "steady", "clean", "modern"},
        "high_contrast": {"punchy", "dynamic", "bold", "rhythmic"},
    }
    role_groups = {
        "ingredient": {"organic", "acoustic", "folk", "warm", "authentic", "gentle"},
        "origin": {"nature", "fields", "organic", "documentary", "folk", "authentic"},
        "production": {"craft", "handmade", "organic", "steady", "documentary"},
        "finished_product": {"product", "commercial", "clean", "modern", "positive"},
        "usage": {"lifestyle", "upbeat", "positive", "warm", "modern"},
        "result": {"uplifting", "positive", "inspiring", "commercial"},
        "context": {"lifestyle", "ambient", "warm", "calm"},
    }

    positive: Dict[str, float] = {}
    for key, weight in (
        ("genre", 2.4),
        ("mood", 2.2),
        ("semantic_tone", 2.8),
        ("energy", 1.8),
        ("visual_brightness", 0.9),
        ("video_style_tone", 2.0),
        ("script_tone", 1.8),
    ):
        value = str(contract.get(key) or "").lower()
        if value:
            _add_terms(positive, term_groups.get(value, {value}), weight)

    contrast = str(contract.get("visual_contrast") or "").lower()
    if contrast == "high":
        _add_terms(positive, term_groups["high_contrast"], 0.9)
    elif contrast:
        _add_terms(positive, term_groups.get(contrast, {contrast}), 0.8)

    story_role_counts = contract.get("story_role_counts") or {}
    if isinstance(story_role_counts, dict):
        total_roles = sum(int(count or 0) for count in story_role_counts.values())
        for role, count in story_role_counts.items():
            role_weight = 0.8 + 1.2 * (int(count or 0) / max(total_roles, 1))
            _add_terms(positive, role_groups.get(str(role).lower(), set()), role_weight)

    negative: Dict[str, float] = {
        "kids": 3.0,
        "playful": 2.6,
        "christmas": 4.0,
        "vocal": 3.0,
        "song": 1.8,
        "singing": 2.4,
        "comedy": 2.2,
    }
    semantic_tone = str(contract.get("semantic_tone") or "").lower()
    energy = str(contract.get("energy") or "").lower()
    script_tone = str(contract.get("script_tone") or "").lower()
    video_style_tone = str(contract.get("video_style_tone") or "").lower()
    if semantic_tone == "natural_origin":
        negative.update({"corporate": 4.0, "epic": 3.2, "rock": 2.8, "intense": 2.4, "hype": 3.2, "edm": 2.4})
    if energy == "low":
        negative.update({"fast": 2.6, "uptempo": 2.6, "energetic": 2.4, "intense": 2.4, "hype": 3.0})
    elif energy == "high":
        negative.update({"sleep": 2.0, "sad": 2.0, "ambient": 1.4})
    if script_tone == "warm":
        negative.update({"cold": 1.2, "metal": 1.2})
    elif script_tone == "cool":
        negative.update({"warm": 1.0, "folk": 1.0, "acoustic": 0.8})
    elif script_tone == "cinematic":
        positive.update({"cinematic": max(positive.get("cinematic", 0.0), 2.0), "build": max(positive.get("build", 0.0), 1.6)})
    if video_style_tone == "direct_sales":
        positive.update({"commercial": max(positive.get("commercial", 0.0), 2.2), "promo": max(positive.get("promo", 0.0), 2.0)})
    elif video_style_tone == "personal_vlog":
        positive.update({"vlog": max(positive.get("vlog", 0.0), 2.4), "lifestyle": max(positive.get("lifestyle", 0.0), 1.8)})
    elif video_style_tone == "review":
        positive.update({"clean": max(positive.get("clean", 0.0), 2.0), "steady": max(positive.get("steady", 0.0), 1.8)})

    for keyword in contract.get("music_keywords") or []:
        keyword = str(keyword or "").strip().lower()
        if keyword:
            positive[keyword] = max(positive.get(keyword, 0.0), 1.4)

    for keyword in contract.get("avoid_keywords") or []:
        keyword = str(keyword or "").strip().lower()
        if keyword:
            negative[keyword] = max(negative.get(keyword, 0.0), 1.6)

    return positive, negative


def rank_tracks_for_contract(
    tracks: List[Dict[str, Any]],
    music_contract: Optional[dict],
    query: str = "",
) -> List[Dict[str, Any]]:
    """Deterministically rank track metadata against the selected-footage contract."""
    contract = music_contract or {}
    wanted, negative = _build_contract_term_weights(contract)

    ranked = []
    for position, track in enumerate(tracks):
        descriptor = _track_descriptor(track)
        score = sum(weight for term, weight in wanted.items() if term and term in descriptor)
        score -= sum(weight for term, weight in negative.items() if term and term in descriptor)
        if query and query.lower() in descriptor:
            score += 0.5
        item = dict(track)
        item["material_fit_score"] = round(score, 3)
        ranked.append((score, -position, item))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item for _, _, item in ranked]


def _bpm_matches_contract(detected_bpm: float, music_contract: Optional[dict], pace: Optional[str]) -> bool:
    if detected_bpm <= 0:
        return str((music_contract or {}).get("energy") or "") == "low"
    bpm_min = float((music_contract or {}).get("bpm_min") or 0)
    bpm_max = float((music_contract or {}).get("bpm_max") or 0)
    if bpm_min > 0 and bpm_max >= bpm_min:
        perceived = [detected_bpm, detected_bpm * 2.0, detected_bpm / 2.0]
        return any(bpm_min <= value <= bpm_max for value in perceived)
    return (
        (pace == "fast" and detected_bpm >= 110)
        or (pace == "slow" and detected_bpm <= 90)
        or (pace == "medium" and 80 <= detected_bpm <= 120)
        or pace is None
    )


def _score_bgm_audio_candidate(
    bgm_path: Path,
    target_duration: float,
    music_contract: Optional[dict],
    pace: Optional[str],
    metadata_score: float = 0.0,
) -> Optional[Dict[str, Any]]:
    """Score an actual downloaded BGM file against the current video contract."""
    if not bgm_path.exists() or bgm_path.stat().st_size <= 8192:
        return None

    try:
        from video_merger import (
            _analyze_loudness,
            _detect_beats,
            _estimate_bpm,
            _get_audio_duration,
            select_bgm_segment,
        )
    except Exception:
        return {
            "score": float(metadata_score),
            "detected_bpm": 0.0,
            "strategy": "metadata_only",
            "reject": False,
            "reason": "audio_analysis_unavailable",
        }

    try:
        duration = float(_get_audio_duration(bgm_path))
        if duration <= 0:
            return None
        selected = select_bgm_segment(
            duration,
            max(float(target_duration or 0.0), 1.0),
            bgm_path,
            music_contract=music_contract,
        )
        loudness = _analyze_loudness(bgm_path, 1.0)
        beats = _detect_beats(bgm_path)
        detected_bpm = _estimate_bpm(beats)
    except Exception:
        return {
            "score": float(metadata_score),
            "detected_bpm": 0.0,
            "strategy": "metadata_only",
            "reject": False,
            "reason": "audio_analysis_failed",
        }

    if not _bpm_matches_contract(detected_bpm, music_contract, pace):
        return {
            "score": -100.0,
            "detected_bpm": detected_bpm,
            "strategy": selected.get("strategy", "unknown"),
            "reject": True,
            "reason": "bpm_mismatch",
        }

    score = float(metadata_score)
    score += 2.0 if selected.get("strategy") in {
        "contract_energy_window",
        "loudest_chorus",
        "structural_main_section",
    } else 0.5

    segment_level = selected.get("average_loudness_db")
    segment_range = selected.get("loudness_range_db")
    levels = [float(v) for v in loudness if isinstance(v, (int, float))]
    if segment_level is None and levels:
        segment_level = sum(levels) / len(levels)
    if segment_range is None and levels:
        segment_range = max(levels) - min(levels)

    energy = str((music_contract or {}).get("energy") or "medium")
    target_level = {"low": -26.0, "medium": -22.0, "high": -18.0}.get(energy, -22.0)
    if segment_level is not None:
        distance = abs(float(segment_level) - target_level)
        score += max(0.0, 3.0 - distance / 2.5)
        if float(segment_level) > -12.0:
            score -= 4.0
        elif float(segment_level) < -38.0:
            score -= 3.0

    if segment_range is not None:
        dynamic_range = float(segment_range)
        if 4.0 <= dynamic_range <= 18.0:
            score += 1.5
        elif dynamic_range > 28.0:
            score -= 2.0
        elif dynamic_range < 1.5:
            score -= 1.0

    if detected_bpm > 0:
        bpm_min = float((music_contract or {}).get("bpm_min") or 0)
        bpm_max = float((music_contract or {}).get("bpm_max") or 0)
        if bpm_min > 0 and bpm_max >= bpm_min:
            center = (bpm_min + bpm_max) / 2.0
            half_width = max((bpm_max - bpm_min) / 2.0, 1.0)
            score += max(0.0, 2.0 - abs(detected_bpm - center) / half_width)
        else:
            score += 0.8

    silent_windows = sum(1 for level in levels if level < -45.0)
    if levels and silent_windows / len(levels) > 0.35:
        score -= 3.0

    return {
        "score": round(score, 3),
        "detected_bpm": round(detected_bpm, 3),
        "strategy": selected.get("strategy", "unknown"),
        "reject": False,
        "reason": "ok",
        "average_loudness_db": (
            round(float(segment_level), 3) if segment_level is not None else None
        ),
        "loudness_range_db": (
            round(float(segment_range), 3) if segment_range is not None else None
        ),
        "segment_selection": selected,
    }


def _detect_pace_from_clips(clip_structure: list) -> str:
    """
    根据分镜结构推断视频节奏等级

    运镜越快、动态越强 → 节奏越快
    静态镜头、慢推慢拉 → 节奏越慢

    Args:
        clip_structure: 分镜结构列表，每个元素包含 camera 字段

    Returns:
        节奏等级：fast / medium / slow
    """
    if not clip_structure:
        return "medium"

    # 运镜类型 -> 节奏权重
    camera_pace = {
        "push": 1,      # 推镜：中等
        "pull": 1,      # 拉镜：中等
        "orbit": 2,     # 环绕：较快
        "static": 0,    # 静态：慢
    }

    total_pace = 0
    for clip in clip_structure:
        camera = clip.get("camera", "static")
        total_pace += camera_pace.get(camera, 1)

    avg_pace = total_pace / len(clip_structure)

    if avg_pace >= 1.3:
        return "fast"
    elif avg_pace >= 0.5:
        return "medium"
    else:
        return "slow"


# ============================================================
# FreeToUse API 客户端
# ============================================================

FREETOUSE_BASE_URL = "https://api.freetouse.com/v3"
FREETOUSE_DOWNLOAD_URL = "https://data.freetouse.com/music/tracks/{track_id}/file/mp3/file.mp3"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}


def _create_session() -> requests.Session:
    """创建带重试的 requests Session"""
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(DEFAULT_HEADERS)
    return session


_thread_local = threading.local()


def _get_session() -> requests.Session:
    """获取线程本地 Session（懒加载，线程安全）"""
    if not hasattr(_thread_local, "session"):
        _thread_local.session = _create_session()
    return _thread_local.session


def _cache_dir() -> Path:
    """获取 BGM 缓存目录"""
    cache_dir = PROJECT_ROOT / BGM_CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def search_tracks(
    query: str,
    limit: int = 20,
    offset: int = 0,
    order: str = "plays",
    sort: str = "desc",
) -> List[Dict[str, Any]]:
    """
    搜索音乐曲目

    Args:
        query: 搜索关键词
        limit: 返回数量
        offset: 分页偏移
        order: 排序字段（release_date/views/plays/downloads/staff_order/random）
        sort: 排序方向（desc/asc）

    Returns:
        曲目列表
    """
    session = _get_session()
    params = {
        "query": query,
        "limit": limit,
        "offset": offset,
        "order": order,
        "sort": sort,
    }
    try:
        resp = session.get(
            f"{FREETOUSE_BASE_URL}/music/tracks/search",
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("ok"):
            return data.get("data", [])
    except Exception as e:
        print(f"⚠️ BGM 搜索失败（{query}）：{e}")
    return []


def get_category_tracks(
    category_id: str,
    limit: int = 20,
    order: str = "plays",
    sort: str = "desc",
) -> List[Dict[str, Any]]:
    """
    按分类获取音乐曲目

    Args:
        category_id: 分类 ID
        limit: 返回数量
        order: 排序字段
        sort: 排序方向

    Returns:
        曲目列表
    """
    session = _get_session()
    params = {
        "limit": limit,
        "order": order,
        "sort": sort,
    }
    try:
        resp = session.get(
            f"{FREETOUSE_BASE_URL}/music/categories/{category_id}/tracks",
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("ok"):
            return data.get("data", [])
    except Exception as e:
        print(f"⚠️ BGM 分类获取失败（{category_id}）：{e}")
    return []


def download_track(
    track_id: str,
    track_title: str = "",
    download_url: Optional[str] = None,
) -> Optional[Path]:
    """
    下载音乐 MP3 文件（带本地缓存）

    Args:
        track_id: 曲目 ID
        track_title: 曲目标题（用于缓存文件名，可选）
        download_url: 下载 URL（优先使用，否则用默认模板拼接）

    Returns:
        本地文件路径，失败返回 None
    """
    cache_dir = _cache_dir()

    # P0 修复：对 track_id 做 sanitize，防止路径遍历攻击
    safe_track_id = re.sub(r"[^a-zA-Z0-9_-]", "_", track_id)
    # 缓存文件：用 safe_track_id 做文件名，避免重复下载
    cache_file = cache_dir / f"{safe_track_id}.mp3"
    if cache_file.exists() and cache_file.stat().st_size > 8192:
        return cache_file
    if cache_file.exists():
        cache_file.unlink(missing_ok=True)

    url = download_url or FREETOUSE_DOWNLOAD_URL.format(track_id=track_id)
    session = _get_session()

    try:
        print(f"🎵 正在下载 BGM：{track_title or track_id}...")
        resp = session.get(url, stream=True, timeout=30)
        resp.raise_for_status()
        expected_size = int(resp.headers.get("Content-Length", "0") or 0)

        # P1 #7 修复：用唯一临时文件名，防止并发下载同一曲目时多线程写同一 .tmp 损坏
        import tempfile
        tmp_fd, tmp_path_str = tempfile.mkstemp(
            suffix=".mp3.tmp", prefix=f"bgm_{track_id}_", dir=cache_dir
        )
        tmp_file = Path(tmp_path_str)
        try:
            with os.fdopen(tmp_fd, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            actual_size = tmp_file.stat().st_size
            if actual_size <= 8192:
                raise RuntimeError(f"BGM 文件过小（{actual_size} bytes），可能下载失败")
            if expected_size > 0 and actual_size != expected_size:
                raise RuntimeError(f"BGM 文件不完整（{actual_size}/{expected_size} bytes）")
            # rename 在同一文件系统上是原子的
            tmp_file.replace(cache_file)
        except Exception:
            try:
                tmp_file.unlink(missing_ok=True)
            except Exception:
                pass
            raise

        print(f"✅ BGM 下载完成：{cache_file.name}")
        return cache_file

    except Exception as e:
        print(f"❌ BGM 下载失败（{track_id}）：{e}")
        return None


# ============================================================
# 选曲历史记录（避免重复）
# ============================================================

HISTORY_FILE = "bgm_history.json"
MAX_HISTORY = 20  # 最多记录最近 20 首，避免重复


def _load_bgm_history() -> list:
    """加载 BGM 选曲历史记录"""
    cache_dir = PROJECT_ROOT / BGM_CACHE_DIR
    hist_file = cache_dir / HISTORY_FILE
    if hist_file.exists():
        try:
            with open(hist_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return []
    return []


def _save_bgm_history(history: list) -> None:
    """保存 BGM 选曲历史记录"""
    cache_dir = PROJECT_ROOT / BGM_CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    hist_file = cache_dir / HISTORY_FILE
    try:
        with open(hist_file, "w", encoding="utf-8") as f:
            json.dump(history[-MAX_HISTORY:], f, ensure_ascii=False, indent=2)
    except IOError:
        pass


def _add_bgm_history(track_id: str, title: str = "") -> None:
    """添加一首 BGM 到历史记录"""
    history = _load_bgm_history()
    history.append({
        "track_id": track_id,
        "title": title,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    _save_bgm_history(history)


def _pick_unique_track(valid_tracks: list, random_pick: bool = True) -> Optional[dict]:
    """
    从候选列表中选一首没听过的 BGM（增加多样性）

    策略：
    1. 从前 15 首里过滤掉历史记录中的
    2. 如果过滤后还有候选，随机选一首
    3. 如果都听过了，从 top 10 里随机选（总不能没歌用）

    Args:
        valid_tracks: 有效曲目列表（已按热度排序）
        random_pick: 是否随机选择

    Returns:
        选中的曲目 dict，列表为空返回 None
    """
    if not valid_tracks:
        return None

    history = _load_bgm_history()
    used_ids = {h["track_id"] for h in history}

    best_score = float(valid_tracks[0].get("material_fit_score") or 0.0)
    score_floor = best_score - (1.0 if best_score > 0 else 0.0)

    # 从前 15 首里找没听过的，同时保留和最高分同一档的曲目。
    top_candidates = [
        track
        for track in valid_tracks[:15]
        if float(track.get("material_fit_score") or 0.0) >= score_floor
    ] or valid_tracks[:3]
    fresh_tracks = [t for t in top_candidates if t["id"] not in used_ids]

    if fresh_tracks:
        if random_pick:
            # Keep variety without dropping to a visibly worse fit.
            pool = fresh_tracks[:5] if len(fresh_tracks) > 5 else fresh_tracks
            return random.choice(pool)
        else:
            return fresh_tracks[0]

    # 都听过了，从同一档匹配池里随机选
    top_tracks = top_candidates[:5] if top_candidates else valid_tracks[:3]
    if random_pick:
        return random.choice(top_tracks)
    return top_tracks[0]


def pick_bgm_for_product(
    product_type: str,
    target_duration: float = 25,
    random_pick: bool = True,
    cinematic_style: Optional[str] = None,
    pace: Optional[str] = None,
    music_contract: Optional[dict] = None,
) -> Optional[Path]:
    """
    根据产品品类 + 电影风格 + 节奏等级 + 音乐合同自动选择并下载一首 BGM

    选曲优先级：
    1. 音乐合同关键词（genre + mood，脚本阶段确定的音乐策略）
    2. 电影风格关键词（保证视听风格统一）
    3. 节奏关键词（匹配视频运镜节奏）
    4. 产品品类关键词（匹配产品调性）
    5. default 兜底

    Args:
        product_type: 产品品类（如 "美妆"、"科技" 等）
        target_duration: 目标视频时长（秒），用于筛选 BGM 长度
        random_pick: 是否随机选择（True=随机增加多样性，False=取最热门的）
        cinematic_style: 电影风格键值（如 "hitchcock"、"miyazaki"），有则优先匹配风格
        pace: 节奏等级（fast/medium/slow），有则匹配对应 BPM 的音乐
        music_contract: 音乐合同字典，含 genre/mood/energy/bpm_min/bpm_max 等

    Returns:
        BGM 本地文件路径，失败返回 None
    """
    from config import CINEMATIC_STYLES

    # P1 #6 修复：config.py 品类 key 与 BGM_STYLE_MAP key 不一致，在此统一规范化
    _CATEGORY_ALIAS = {
        "数码": "科技",
        "个护": "美妆",
        "服饰": "服装",
        "家居": "家居",
        "房产": "房产",
        "医疗": "医疗",
        "教育": "教育",
    }
    normalized_type = _CATEGORY_ALIAS.get(product_type, product_type)
    style_config = BGM_STYLE_MAP.get(normalized_type, BGM_STYLE_MAP["default"])
    product_keywords = style_config["keywords"]
    min_duration = style_config.get("min_duration", 20)
    max_duration = style_config.get("max_duration", 120)
    order = style_config.get("order", "plays")
    material_driven = str((music_contract or {}).get("source") or "") == "selected_local_assets"
    if material_driven:
        random_pick = False

    # 构建关键词搜索列表（优先级从高到低）
    all_keywords = []

    # 0. 音乐合同关键词（最高优先级：脚本阶段确定的音乐策略）
    contract_keywords = []
    if music_contract:
        contract_keywords = [
            music_contract.get("genre", ""),
            music_contract.get("mood", ""),
            music_contract.get("energy", ""),
        ]
        contract_keywords = [k for k in contract_keywords if k]
        if contract_keywords:
            all_keywords.extend(contract_keywords)
            print(f"  🎵 音乐合同关键词：{contract_keywords}")
        music_keywords = [str(k).strip() for k in (music_contract.get("music_keywords") or []) if str(k).strip()]
        if music_keywords:
            all_keywords.extend(music_keywords)
            print(f"  🎵 视频风格/脚本关键词：{music_keywords}")

    # 1. 电影风格关键词
    if cinematic_style and cinematic_style in CINEMATIC_STYLES:
        style_kw = CINEMATIC_STYLES[cinematic_style].get("bgm_keywords", [])
        if style_kw:
            all_keywords.extend(style_kw)

    # 2. 节奏关键词
    pace_keywords = []
    if pace and pace in PACE_KEYWORDS:
        pace_keywords = PACE_KEYWORDS[pace]
        all_keywords.extend(pace_keywords)

    # 3. 品类关键词
    all_keywords.extend(product_keywords)

    if not all_keywords:
        all_keywords = BGM_STYLE_MAP["default"]["keywords"]

    # 实际需要的 BGM 最短时长（视频时长的 80%，因为可以循环）
    min_needed = max(min_duration, target_duration * 0.8)

    # 尝试多个关键词，直到找到合适的
    shuffled_keywords = all_keywords.copy()
    if random_pick:
        # 分层打乱：风格层、节奏层、品类层各自打乱，但优先级顺序不变
        style_count = len(all_keywords) - len(pace_keywords) - len(product_keywords)
        idx = 0
        if style_count > 0:
            style_part = shuffled_keywords[idx:idx+style_count]
            random.shuffle(style_part)
            shuffled_keywords[idx:idx+style_count] = style_part
            idx += style_count
        if pace_keywords:
            pace_part = shuffled_keywords[idx:idx+len(pace_keywords)]
            random.shuffle(pace_part)
            shuffled_keywords[idx:idx+len(pace_keywords)] = pace_part
            idx += len(pace_keywords)
        product_part = shuffled_keywords[idx:]
        random.shuffle(product_part)
        shuffled_keywords[idx:] = product_part

    # 打印匹配信息
    parts = []
    if cinematic_style and cinematic_style in CINEMATIC_STYLES:
        parts.append(f"{CINEMATIC_STYLES[cinematic_style]['name']}风格")
    if pace:
        pace_names = {"fast": "快节奏", "medium": "中节奏", "slow": "慢节奏"}
        parts.append(pace_names.get(pace, pace))
    if parts:
        print(f"🎬 BGM 匹配 {'+'.join(parts)}...")

    for keyword in shuffled_keywords:
        print(f"🔍 搜索 BGM：{keyword}...")
        tracks = search_tracks(keyword, limit=30, order=order)

        if not tracks:
            continue

        # 筛选：非 premium、时长合适、有 mp3 文件
        valid_tracks = [
            t for t in tracks
            if not t.get("is_premium", False)
            and t.get("duration", 0) >= min_needed
            and t.get("duration", 0) <= max_duration
            and t.get("files", {}).get("mp3")
        ]

        if not valid_tracks:
            # 放宽时长限制再试一次
            valid_tracks = [
                t for t in tracks
                if not t.get("is_premium", False)
                and t.get("duration", 0) >= min_duration
                and t.get("files", {}).get("mp3")
            ]

        if not valid_tracks:
            continue

        valid_tracks = rank_tracks_for_contract(valid_tracks, music_contract, query=keyword)
        if music_contract:
            viable_tracks = [
                track for track in valid_tracks
                if float(track.get("material_fit_score") or 0.0) >= -1.0
            ]
            if not viable_tracks:
                print("  ⚠️  候选曲目与当前视频调性冲突，继续换关键词")
                continue
            valid_tracks = viable_tracks

        audition_pool = list(valid_tracks[:8])
        if random_pick:
            audition_pool = list(dict.fromkeys(
                [track["id"] for track in audition_pool]
                + [track["id"] for track in valid_tracks[:15]]
            ))
            track_by_id = {track["id"]: track for track in valid_tracks}
            audition_pool = [track_by_id[track_id] for track_id in audition_pool if track_id in track_by_id]

        auditioned = []
        while audition_pool and len(auditioned) < 5:
            chosen = _pick_unique_track(audition_pool, random_pick)
            if not chosen:
                break
            audition_pool = [track for track in audition_pool if track["id"] != chosen["id"]]
            track_id = chosen["id"]
            track_title = chosen.get("title", track_id)
            track_duration = chosen.get("duration", 0)
            track_mp3_url = chosen.get("files", {}).get("mp3")
            print(
                f"🎵 候选 BGM：{track_title}（{int(track_duration)}s，"
                f"素材匹配 {chosen.get('material_fit_score', 0):.1f}）"
            )
            local_path = download_track(track_id, track_title, download_url=track_mp3_url)
            if not local_path:
                continue

            audio_score = _score_bgm_audio_candidate(
                local_path,
                target_duration,
                music_contract,
                pace,
                metadata_score=float(chosen.get("material_fit_score") or 0.0),
            )
            if not audio_score:
                if material_driven:
                    local_path.unlink(missing_ok=True)
                continue
            if audio_score.get("reject"):
                print(
                    f"  ⚠️  试听拒绝：{audio_score.get('reason')}，"
                    f"BPM {float(audio_score.get('detected_bpm') or 0):.0f}"
                )
                if material_driven:
                    local_path.unlink(missing_ok=True)
                continue
            chosen = {
                **chosen,
                "local_path": local_path,
                "audio_fit_score": float(audio_score.get("score") or 0.0),
                "audio_score": audio_score,
            }
            auditioned.append(chosen)
            print(
                f"  🎧 试听评分 {chosen['audio_fit_score']:.1f}："
                f"BPM {float(audio_score.get('detected_bpm') or 0):.0f}，"
                f"{audio_score.get('strategy')}"
            )

        if auditioned:
            auditioned.sort(
                key=lambda item: (
                    float(item.get("audio_fit_score") or 0.0),
                    float(item.get("material_fit_score") or 0.0),
                ),
                reverse=True,
            )
            best = auditioned[0]
            for loser in auditioned[1:]:
                loser_path = Path(loser["local_path"])
                if material_driven and loser_path != Path(best["local_path"]):
                    loser_path.unlink(missing_ok=True)
            print(
                f"✅ 试听级 BGM 定稿：{best.get('title', best['id'])} "
                f"（综合 {float(best.get('audio_fit_score') or 0):.1f}）"
            )
            _add_bgm_history(best["id"], best.get("title", ""))
            return Path(best["local_path"])

    # 所有关键词都失败了，用 default 兜底再试一次
    if product_type != "default" and not material_driven:
        print(f"⚠️ 品类「{product_type}」未找到合适 BGM，尝试默认风格...")
        result = pick_bgm_for_product(
            "default", target_duration, random_pick,
            cinematic_style=None, pace=pace,
        )
        if result:
            return result

    # 最终 fallback：扫描 assets/ 目录下的本地 BGM 文件
    return None if material_driven else _pick_local_bgm_fallback()


def _pick_local_bgm_fallback() -> Optional[Path]:
    """
    本地 BGM fallback：扫描 assets/ 目录中的 .mp3/.wav/.m4a 文件。

    优先选 bgm.mp3，其次按文件名字母序选第一个。
    找不到时打印引导说明，返回 None。
    """
    assets_dir = PROJECT_ROOT / "assets"
    if not assets_dir.exists():
        _print_bgm_missing_hint()
        return None

    # 优先精确匹配 bgm.mp3
    preferred = assets_dir / "bgm.mp3"
    if preferred.exists() and preferred.stat().st_size > 0:
        print(f"🎵 使用本地 fallback BGM：{preferred.name}")
        return preferred

    # 扫描其他音频文件
    audio_exts = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
    candidates = sorted(
        [p for p in assets_dir.iterdir() if p.suffix.lower() in audio_exts and p.stat().st_size > 0]
    )
    if candidates:
        chosen = candidates[0]
        print(f"🎵 使用本地 fallback BGM：{chosen.name}")
        return chosen

    _print_bgm_missing_hint()
    return None


def _print_bgm_missing_hint() -> None:
    """打印 BGM 缺失提示及解决方案"""
    print(
        "\n⚠️  未找到可用 BGM，视频将无背景音乐。\n"
        "   解决方案（任选一种）：\n"
        "   1. 检查网络连接——FreeToUse API 可能暂时不可用\n"
        "   2. 将免版税 BGM 放到 assets/bgm.mp3（本地 fallback）\n"
        "      推荐来源：https://pixabay.com/music/  或  https://mixkit.co/free-music/\n"
        "   3. 继续生成无 BGM 版本，后期手动添加音乐\n"
    )


# ============================================================
# 版权信息
# ============================================================

# BGM 版权免责声明（每次生成视频时都要提醒用户）
BGM_COPYRIGHT_DISCLAIMER = """
⚠️  BGM 版权免责声明
----------------------------------------
本工具使用的 BGM 来源：
  1. FreeToUse Music API（免费版曲目）
  2. 本地 assets/bgm.mp3（用户自行提供）

重要提示：
  • FreeToUse 免费版曲目仅供个人/非商业用途参考
  • 商业广告使用前请务必前往 freetouse.com 核实授权范围
  • 本地 BGM 文件的版权由用户自行负责
  • 如不确定版权，请使用免版税音乐平台（如 Pixabay、Mixkit 等）
  • 因版权问题导致的任何后果由使用者承担
----------------------------------------
"""


def get_bgm_copyright_info(bgm_path: Optional[Path]) -> dict:
    """
    获取 BGM 的版权信息

    Args:
        bgm_path: BGM 文件路径

    Returns:
        包含 source / title / is_commercial_safe / warning 的字典
    """
    if not bgm_path or not Path(bgm_path).exists():
        return {
            "source": "none",
            "title": "无",
            "is_commercial_safe": False,
            "warning": "无 BGM，视频可能无声",
        }

    bgm_path = Path(bgm_path)

    # 判断来源
    if "bgm_cache" in str(bgm_path):
        # 来自 FreeToUse API
        track_id = bgm_path.stem
        title = next(
            (
                str(item.get("title") or track_id)
                for item in reversed(_load_bgm_history())
                if str(item.get("track_id") or "") == track_id
            ),
            track_id,
        )
        return {
            "source": "freetouse_api",
            "title": title,
            "is_commercial_safe": False,  # 免费版不保证商用
            "warning": "FreeToUse 免费版曲目，商用前请核实授权",
        }
    elif bgm_path.name == "bgm.mp3" and "assets" in str(bgm_path):
        # 本地 fallback
        return {
            "source": "local_assets",
            "title": "本地 BGM (bgm.mp3)",
            "is_commercial_safe": False,
            "warning": "用户自行提供的本地 BGM，版权责任自负",
        }
    else:
        return {
            "source": "unknown",
            "title": bgm_path.name,
            "is_commercial_safe": False,
            "warning": "BGM 来源未知，版权责任自负",
        }


def print_bgm_copyright_warning() -> None:
    """打印 BGM 版权免责声明"""
    print(BGM_COPYRIGHT_DISCLAIMER)
