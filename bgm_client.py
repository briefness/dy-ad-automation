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

    # 从前 15 首里找没听过的
    top_candidates = valid_tracks[:15]
    fresh_tracks = [t for t in top_candidates if t["id"] not in used_ids]

    if fresh_tracks:
        if random_pick:
            # 从未听过的里面随机选（从 top 10 的"新鲜"里选）
            pool = fresh_tracks[:10] if len(fresh_tracks) > 10 else fresh_tracks
            return random.choice(pool)
        else:
            return fresh_tracks[0]

    # 都听过了，从 top 10 随机选
    top_tracks = valid_tracks[:10]
    if random_pick:
        return random.choice(top_tracks)
    return top_tracks[0]


def pick_bgm_for_product(
    product_type: str,
    target_duration: float = 25,
    random_pick: bool = True,
    cinematic_style: Optional[str] = None,
    pace: Optional[str] = None,
) -> Optional[Path]:
    """
    根据产品品类 + 电影风格 + 节奏等级自动选择并下载一首 BGM

    选曲优先级：
    1. 电影风格关键词（保证视听风格统一）
    2. 节奏关键词（匹配视频运镜节奏）
    3. 产品品类关键词（匹配产品调性）
    4. default 兜底

    Args:
        product_type: 产品品类（如 "美妆"、"科技" 等）
        target_duration: 目标视频时长（秒），用于筛选 BGM 长度
        random_pick: 是否随机选择（True=随机增加多样性，False=取最热门的）
        cinematic_style: 电影风格键值（如 "hitchcock"、"miyazaki"），有则优先匹配风格
        pace: 节奏等级（fast/medium/slow），有则匹配对应 BPM 的音乐

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

    # 构建关键词搜索列表（优先级从高到低）
    all_keywords = []

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

        # 选一首（优先选没听过的，增加多样性）
        chosen = _pick_unique_track(valid_tracks, random_pick)
        if not chosen:
            continue

        track_id = chosen["id"]
        track_title = chosen.get("title", track_id)
        track_duration = chosen.get("duration", 0)
        track_mp3_url = chosen.get("files", {}).get("mp3")

        print(f"🎵 选中 BGM：{track_title}（{int(track_duration)}s）")

        # 下载
        local_path = download_track(track_id, track_title, download_url=track_mp3_url)
        if local_path:
            # P1 修复：BPM 校验，节奏不匹配则同一 keyword 内重试其他候选
            if pace:
                try:
                    from video_merger import _detect_beats, _estimate_bpm
                    beats = _detect_beats(local_path)
                    detected_bpm = _estimate_bpm(beats)
                    if detected_bpm > 0:
                        bpm_ok = (
                            (pace == "fast" and detected_bpm >= 110)
                            or (pace == "slow" and detected_bpm <= 90)
                            or (pace == "medium" and 80 <= detected_bpm <= 120)
                        )
                        if not bpm_ok:
                            print(f"  ⚠️  BPM {detected_bpm:.0f} 不符合 {pace} 节奏，换下一首")
                            # 不加入历史，删除下载的文件
                            try:
                                local_path.unlink()
                            except Exception:
                                pass
                            # 从 valid_tracks 中移除当前这首，同一 keyword 内继续选其他候选
                            valid_tracks = [t for t in valid_tracks if t["id"] != track_id]
                            while valid_tracks:
                                chosen = _pick_unique_track(valid_tracks, random_pick)
                                if not chosen:
                                    break
                                track_id = chosen["id"]
                                track_title = chosen.get("title", track_id)
                                track_duration = chosen.get("duration", 0)
                                track_mp3_url = chosen.get("files", {}).get("mp3")
                                local_path = download_track(track_id, track_title, download_url=track_mp3_url)
                                if not local_path:
                                    valid_tracks = [t for t in valid_tracks if t["id"] != track_id]
                                    continue
                                beats = _detect_beats(local_path)
                                detected_bpm = _estimate_bpm(beats)
                                if detected_bpm > 0:
                                    bpm_ok = (
                                        (pace == "fast" and detected_bpm >= 110)
                                        or (pace == "slow" and detected_bpm <= 90)
                                        or (pace == "medium" and 80 <= detected_bpm <= 120)
                                    )
                                    if bpm_ok:
                                        print(f"  ✅ BPM {detected_bpm:.0f} 符合 {pace} 节奏")
                                        break
                                    print(f"  ⚠️  BPM {detected_bpm:.0f} 不符合 {pace} 节奏，继续换")
                                    try:
                                        local_path.unlink()
                                    except Exception:
                                        pass
                                valid_tracks = [t for t in valid_tracks if t["id"] != track_id]
                            else:
                                # 当前 keyword 所有候选都不符合，跳到下一个 keyword
                                continue
                        else:
                            print(f"  ✅ BPM {detected_bpm:.0f} 符合 {pace} 节奏")
                except Exception as e:
                    print(f"  ⚠️  BPM 检测失败，跳过校验：{e}")

            # 加入历史记录，避免下次重复
            _add_bgm_history(track_id, track_title)
            return local_path

    # 所有关键词都失败了，用 default 兜底再试一次
    if product_type != "default":
        print(f"⚠️ 品类「{product_type}」未找到合适 BGM，尝试默认风格...")
        result = pick_bgm_for_product(
            "default", target_duration, random_pick,
            cinematic_style=None, pace=pace,
        )
        if result:
            return result

    # 最终 fallback：扫描 assets/ 目录下的本地 BGM 文件
    return _pick_local_bgm_fallback()


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
        return {
            "source": "freetouse_api",
            "title": track_id,
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
