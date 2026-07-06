#!/usr/bin/env python3
"""
AI 口播配音模块

功能：
- 生成口播音频（优先火山引擎大模型 TTS，自动降级到 macOS say）
- 根据字幕生成口播文案
- 自动对齐字幕和语音时间轴
- 支持多种音色选择

依赖：
- 火山引擎 TTS（推荐）：需在 .env 中配置 VOLC_APP_ID + VOLC_ACCESS_TOKEN
- macOS say 命令（离线 fallback，免费）
- pyttsx3（非 macOS fallback，需 pip install pyttsx3）

音色优先级：火山引擎 TTS → macOS say → pyttsx3
"""

import subprocess
import shutil
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from datetime import datetime


# ============================================================
# 口播文案模板
# ============================================================

VOICEOVER_TEMPLATES = {
    "standard": {
        "name": "标准带货",
        "script": [
            {"segment": 0, "text": "你是不是也有这样的烦恼？"},
            {"segment": 1, "text": "直到我发现了这个宝藏好物！"},
            {"segment": 2, "text": "{selling_point}，效果真的好！"},
            {"segment": 3, "text": "用完之后整个人都不一样了！"},
            {"segment": 4, "text": "赶紧点击左下角，值得入手！"},
        ],
    },
    "emotional": {
        "name": "情感共鸣",
        "script": [
            {"segment": 0, "text": "有没有人和我一样，一直在寻找...？"},
            {"segment": 1, "text": "终于，让我遇到了它。"},
            {"segment": 2, "text": "{selling_point}，这就是我想要的。"},
            {"segment": 3, "text": "那种满足感，真的无法用语言形容。"},
            {"segment": 4, "text": "相信我，你也会喜欢它的。"},
        ],
    },
    "energetic": {
        "name": "激情喊麦",
        "script": [
            {"segment": 0, "text": "家人们！今天给你们分享一个好物！"},
            {"segment": 1, "text": "就是这个！真的很不错！"},
            {"segment": 2, "text": "{selling_point}！大家快来看！"},
            {"segment": 3, "text": "我跟你说，用过之后真的很好！"},
            {"segment": 4, "text": "费用有限，先到先得！点击左下角！"},
        ],
    },
    "professional": {
        "name": "专业测评",
        "script": [
            {"segment": 0, "text": "今天给大家测评一款最近很受关注的产品。"},
            {"segment": 1, "text": "说实话，一开始我是持怀疑态度的。"},
            {"segment": 2, "text": "但是用了之后，{selling_point}，确实有点东西。"},
            {"segment": 3, "text": "综合来看，表现超出预期。"},
            {"segment": 4, "text": "感兴趣的朋友可以了解一下。"},
        ],
    },
    "storytelling": {
        "name": "故事叙述",
        "script": [
            {"segment": 0, "text": "那段时间，我真的很困扰。"},
            {"segment": 1, "text": "偶然的机会，朋友推荐了这个给我。"},
            {"segment": 2, "text": "没想到，{selling_point}，改变了我的生活。"},
            {"segment": 3, "text": "现在的我，每天都很开心。"},
            {"segment": 4, "text": "分享给你们，希望也能帮到你。"},
        ],
    },
}

# 默认口播风格
DEFAULT_VOICEOVER_STYLE = "standard"


# ============================================================
# 音色配置
# ============================================================

VOICE_PRESETS = {
    "female_young": {
        "name": "年轻女声",
        "voice": "Tingting",              # macOS fallback
        "volc_voice_type": "zh_female_wanwanxiaohe_moon_bigtts",  # 火山：暖暖小荷
        "rate": 180,
        "pitch": 1.0,
    },
    "female_warm": {
        "name": "温暖女声",
        "voice": "Meijia",                # macOS fallback
        "volc_voice_type": "zh_female_qingxin_emo_bigtts",        # 火山：清新情感女声
        "rate": 160,
        "pitch": 1.0,
    },
    "male_pro": {
        "name": "专业男声",
        "voice": "Yunyang",               # macOS fallback
        "volc_voice_type": "zh_male_chunhou_bigtts",              # 火山：醇厚男声
        "rate": 170,
        "pitch": 1.0,
    },
    "male_magnetic": {
        "name": "磁性男声",
        "voice": "Tingting",              # macOS fallback
        "volc_voice_type": "zh_male_jingqiangkanye_moon_bigtts",  # 火山：精强侃爷
        "rate": 150,
        "pitch": 0.95,
    },
    "energetic_female": {
        "name": "活力女声",
        "voice": "Tingting",              # macOS fallback
        "volc_voice_type": "zh_female_lively_bigtts",             # 火山：活力女声
        "rate": 200,
        "pitch": 1.05,
    },
}

# 默认音色
DEFAULT_VOICE = "female_young"

# 火山引擎 TTS V3 配置（从环境变量读取，未配置时自动降级）
# 接口文档：https://www.volcengine.com/docs/6561/1257544
_VOLC_API_URL: str = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
_VOLC_RESOURCE_ID_DEFAULT: str = "seed-tts-2.0"  # 豆包语音合成大模型 2.0


def _load_volc_credentials() -> tuple[str, str]:
    """
    从环境变量或 .env 文件加载火山引擎 TTS V3 凭据。

    Returns:
        (api_key, resource_id) — 任一为空则表示未配置，调用方应降级
    """
    import os
    api_key = os.getenv("VOLC_API_KEY", "")
    resource_id = os.getenv("VOLC_RESOURCE_ID", _VOLC_RESOURCE_ID_DEFAULT)
    if not api_key:
        # 尝试从项目根目录 .env 文件读取
        try:
            env_path = Path(__file__).parent / ".env"
            if env_path.exists():
                for line in env_path.read_text().splitlines():
                    line = line.strip()
                    if line.startswith("VOLC_API_KEY="):
                        api_key = line.split("=", 1)[1].strip()
                    elif line.startswith("VOLC_RESOURCE_ID="):
                        resource_id = line.split("=", 1)[1].strip()
        except Exception:
            pass
    return api_key, resource_id


def _validate_audio_file(audio_path: Path, min_size: int = 1024) -> None:
    """验证音频文件存在、非空且 ffprobe 可解码。"""
    if not audio_path.exists():
        raise RuntimeError(f"音频文件不存在：{audio_path}")
    if audio_path.stat().st_size < min_size:
        raise RuntimeError(f"音频文件过小，可能生成失败：{audio_path}")
    if not shutil.which("ffprobe"):
        return
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
        raise RuntimeError(f"音频文件不可解码：{audio_path}")


def generate_voiceover_script(
    product_info: dict,
    style: str = DEFAULT_VOICEOVER_STYLE,
    clip_duration: int = 5,
    num_clips: int = 5,
) -> List[Dict[str, any]]:
    """
    生成口播文案列表

    Args:
        product_info: 产品信息字典
        style: 口播风格
        clip_duration: 单片段时长（秒）
        num_clips: 片段数量

    Returns:
        口播列表，每个元素包含 text/start/end/segment
    """
    template = VOICEOVER_TEMPLATES.get(style, VOICEOVER_TEMPLATES[DEFAULT_VOICEOVER_STYLE])
    selling_point = product_info.get("selling_point", "核心卖点")

    lines = []
    for item in template["script"]:
        seg_idx = item.get("segment", 0)
        if seg_idx >= num_clips:
            continue
        text = item["text"].format(selling_point=selling_point)
        seg_start = seg_idx * clip_duration
        start = seg_start + clip_duration * 0.1  # 每段开头留 10% 空白
        end = seg_start + clip_duration * 0.85  # 每段结尾留 15% 空白
        lines.append({
            "text": text,
            "start": start,
            "end": end,
            "segment": seg_idx,
        })

    return lines


def generate_tts_audio(
    text: str,
    output_path: Path,
    voice: str = DEFAULT_VOICE,
    rate: Optional[int] = None,
) -> Path:
    """
    生成单段 TTS 音频。

    优先级链：
    1. 火山引擎大模型 TTS V3（需在 .env 配置 VOLC_API_KEY）
    2. macOS say 命令（离线，仅 macOS）
    3. pyttsx3 跨平台引擎（需 pip install pyttsx3）

    Args:
        text: 要合成的文本
        output_path: 输出音频路径（.m4a / .wav）
        voice: 音色名称（VOICE_PRESETS 中的 key）
        rate: 语速（词/分钟），None 则使用预设值

    Returns:
        输出文件路径

    Raises:
        RuntimeError: 所有 TTS 引擎均不可用或生成失败
    """
    import sys

    voice_config = VOICE_PRESETS.get(voice, VOICE_PRESETS[DEFAULT_VOICE])
    actual_rate = rate if rate is not None else voice_config["rate"]
    # P1 修复：读取 pitch 字段（默认 1.0 = 正常音调）
    actual_pitch: float = voice_config.get("pitch", 1.0)

    # 优先：火山引擎大模型 TTS V3
    volc_api_key, volc_resource_id = _load_volc_credentials()
    if volc_api_key:
        try:
            return _generate_tts_volcengine(
                text=text,
                output_path=output_path,
                speaker=voice_config.get("volc_voice_type", "zh_female_wanwanxiaohe_moon_bigtts"),
                api_key=volc_api_key,
                resource_id=volc_resource_id,
                # speech_rate: V3 范围 -50~100，映射自 wpm
                # 180 wpm → 0，220 wpm → ~22，150 wpm → ~-17
                speech_rate=int((actual_rate - 180) / 2),
                # P1 修复：pitch 映射到 pitch_rate：1.0→0, 1.1→10, 0.9→-10
                pitch_rate=int((actual_pitch - 1.0) * 100),
            )
        except Exception as e:
            print(f"  ⚠️  火山 TTS 失败（{e}）")
            raise RuntimeError(
                f"火山 TTS 失败且无高质量 fallback 可用：{e}\n"
                "口播已跳过，请在 .env 中配置 VOLC_API_KEY 后重试。"
            ) from e

    # P1 修复：无火山 Token 时直接抛出，而非静默降级到 macOS say / pyttsx3
    # macOS say 和 pyttsx3 均为机械音，在正式成片中音质极差，会严重拉低成片观感。
    # 抛出异常让调用方（run_generation_pipeline）捕获后跳过口播，不阻断主流程。
    raise RuntimeError(
        "未配置火山引擎 TTS（VOLC_API_KEY 为空），口播功能不可用。\n"
        "请在 .env 中配置 VOLC_API_KEY，或不传 --voiceover 参数。\n"
        "macOS say / pyttsx3 降级已禁用（机械音会拉低成片质量）。"
    )


def _generate_tts_volcengine(
    text: str,
    output_path: Path,
    speaker: str,
    api_key: str,
    resource_id: str = _VOLC_RESOURCE_ID_DEFAULT,
    speech_rate: int = 0,
    pitch_rate: int = 0,  # P1 修复：新增 pitch_rate 参数
) -> Path:
    """
    使用火山引擎豆包大模型 TTS V3 单向流式接口生成音频。

    接口：POST https://openspeech.bytedance.com/api/v3/tts/unidirectional
    协议：HTTP Chunked 流式，音频数据在响应体中分块返回。

    Args:
        text: 合成文本
        output_path: 输出路径（.mp3 或 .m4a）
        speaker: 豆包音色 ID，详见控制台音色库
        api_key: 火山引擎 API Key（X-Api-Key 头）
        resource_id: 模型版本，默认 seed-tts-2.0
        speech_rate: 语速，范围 -50~100（0 为正常速，100 为 2x，-50 为 0.5x）

    Returns:
        输出文件路径

    Raises:
        RuntimeError: API 调用失败或返回非 2xx 状态码
    """
    import uuid
    try:
        import requests as _requests
    except ImportError:
        raise RuntimeError("火山 TTS 需要 requests 库：pip install requests")

    speech_rate = max(-50, min(speech_rate, 100))
    pitch_rate = max(-50, min(pitch_rate, 100))  # P1 修复：范围裁剪

    payload = {
        "req_params": {
            "text": text,
            "speaker": speaker,
            "audio_params": {
                "format": "mp3",
                "sample_rate": 24000,
                "bit_rate": 128000,
                "speech_rate": speech_rate,
                "pitch_rate": pitch_rate,  # P1 修复：将 pitch 传入 API
            },
        }
    }

    mp3_path = output_path.with_suffix(".mp3")
    last_error: Exception | None = None
    for attempt in range(1, 4):
        headers = {
            "X-Api-Key": api_key,
            "X-Api-Resource-Id": resource_id,
            "X-Api-Request-Id": str(uuid.uuid4()),
            "Content-Type": "application/json",
        }
        try:
            with _requests.post(
                _VOLC_API_URL,
                json=payload,
                headers=headers,
                stream=True,   # Chunked 流式响应
                timeout=60,
            ) as resp:
                if resp.status_code != 200:
                    try:
                        err = resp.json()
                        msg = f"code={err.get('code')} message={err.get('message')}"
                    except Exception:
                        msg = resp.text[:200]
                    error = RuntimeError(f"火山 TTS V3 请求失败 HTTP {resp.status_code}: {msg}")
                    if resp.status_code not in {429, 500, 502, 503, 504} or attempt == 3:
                        raise error
                    last_error = error
                    wait = 2 ** attempt
                    print(f"  ⚠️  火山 TTS 暂时失败，{wait}s 后重试（{attempt}/3）")
                    import time
                    time.sleep(wait)
                    continue

                # 消费流式响应，拼接音频数据
                with mp3_path.open("wb") as f:
                    for chunk in resp.iter_content(chunk_size=4096):
                        if chunk:
                            f.write(chunk)

            if mp3_path.stat().st_size == 0:
                mp3_path.unlink(missing_ok=True)
                raise RuntimeError("火山 TTS V3 返回空音频，请检查 API Key 和音色 ID")
            break
        except _requests.exceptions.RequestException as e:
            last_error = e
            mp3_path.unlink(missing_ok=True)
            if attempt == 3:
                raise RuntimeError(f"火山 TTS V3 请求失败，已重试 3 次：{e}") from e
            wait = 2 ** attempt
            print(f"  ⚠️  火山 TTS 请求异常，{wait}s 后重试（{attempt}/3）")
            import time
            time.sleep(wait)
    else:
        raise RuntimeError(f"火山 TTS V3 请求失败：{last_error}")

    _validate_audio_file(mp3_path)

    # 按需转码为 m4a
    if output_path.suffix != ".mp3" and shutil.which("ffmpeg"):
        cmd = [
            "ffmpeg", "-y", "-i", str(mp3_path),
            "-c:a", "aac", "-b:a", "128k",
            str(output_path),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=30)
        finally:
            if mp3_path.exists() and mp3_path != output_path:
                mp3_path.unlink()
    else:
        if mp3_path != output_path:
            shutil.move(str(mp3_path), str(output_path))

    _validate_audio_file(output_path)
    return output_path


def _generate_tts_macos(
    text: str,
    output_path: Path,
    voice_name: str,
    rate: int,
    pitch: float = 1.0,  # P1 修复：新增 pitch 参数
) -> Path:
    """使用 macOS say 命令生成 TTS 音频"""
    aiff_path = output_path.with_suffix(".aiff")
    # P1 修复：say -p 范围 30-65，默认约50。pitch=1.0→0偏移，每 0.1 对应 5点
    say_pitch = int(50 + (pitch - 1.0) * 50)
    say_pitch = max(30, min(65, say_pitch))
    cmd = ["say", "-v", voice_name, "-r", str(rate), "-p", str(say_pitch), "-o", str(aiff_path), text]

    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=30)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"macOS say 生成失败: {e.stderr.decode() if e.stderr else str(e)}")

    # 转码为 m4a（aac 编码，体积更小）
    if shutil.which("ffmpeg"):
        cmd_transcode = [
            "ffmpeg", "-y",
            "-i", str(aiff_path),
            "-c:a", "aac", "-b:a", "128k",
            str(output_path),
        ]
        try:
            subprocess.run(cmd_transcode, check=True, capture_output=True, timeout=30)
        finally:
            if aiff_path.exists():
                aiff_path.unlink()
    else:
        shutil.move(str(aiff_path), str(output_path))

    return output_path


def _generate_tts_pyttsx3(
    text: str,
    output_path: Path,
    rate: int,
    pitch: float = 1.0,  # P1 修复：新增 pitch 参数
) -> Path:
    """
    使用 pyttsx3 跨平台引擎生成 TTS 音频（Windows / Linux fallback）。

    输出为 WAV 格式（pyttsx3 原生支持），如有 ffmpeg 则转为 m4a。
    """
    try:
        import pyttsx3  # pip install pyttsx3
    except ImportError:
        raise RuntimeError(
            "非 macOS 环境需要安装 pyttsx3 才能使用 TTS 口播功能。\n"
            "安装命令：pip install pyttsx3\n"
            "或关闭口播功能：去掉 --voiceover 参数"
        )

    # pyttsx3 只能输出 WAV，先写到临时文件
    wav_path = output_path.with_suffix(".wav")

    try:
        engine = pyttsx3.init()
        # rate: pyttsx3 单位是词/分钟，与 say 一致
        engine.setProperty("rate", rate)
        # P1 修复：设置 pitch（pyttsx3 的 pitch 属性对部分引擎有效）
        try:
            engine.setProperty("pitch", pitch)
        except Exception:
            pass  # 引擎不支持 pitch 时静默跳过
        engine.save_to_file(text, str(wav_path))
        engine.runAndWait()
        engine.stop()
    except Exception as e:
        raise RuntimeError(f"pyttsx3 TTS 生成失败: {e}")

    if not wav_path.exists() or wav_path.stat().st_size == 0:
        raise RuntimeError("pyttsx3 未生成音频文件，请检查系统 TTS 引擎是否已安装")

    # 转码为 m4a
    if shutil.which("ffmpeg") and output_path.suffix != ".wav":
        cmd_transcode = [
            "ffmpeg", "-y",
            "-i", str(wav_path),
            "-c:a", "aac", "-b:a", "128k",
            str(output_path),
        ]
        try:
            subprocess.run(cmd_transcode, check=True, capture_output=True, timeout=30)
        finally:
            if wav_path.exists() and wav_path != output_path:
                wav_path.unlink()
    else:
        shutil.move(str(wav_path), str(output_path))

    return output_path


def split_sentences(text: str, max_chars: int = 20) -> List[str]:
    """
    智能断句：将长文本按标点切成短句

    优先按句末标点（。！？；）切分，过长的句子再按逗号细分，
    确保每句字数适中，TTS 发音更自然，字幕显示更有节奏感。

    Args:
        text: 原始文本
        max_chars: 单句最大字数（超过则继续按逗号细分）

    Returns:
        短句列表
    """
    if not text or not text.strip():
        return []

    import re

    # 第一步：按句末标点切分（保留标点）
    sentences = []
    parts = re.split(r'([。！？；])', text.strip())
    current = ""
    for part in parts:
        if part in '。！？；':
            current += part
            if current.strip():
                sentences.append(current.strip())
            current = ""
        else:
            current += part
    if current.strip():
        sentences.append(current.strip())

    # 第二步：过长的句子按逗号/顿号再细分
    result = []
    for sent in sentences:
        if len(sent) <= max_chars:
            result.append(sent)
        else:
            sub_parts = re.split(r'([，、：])', sent)
            sub_sent = ""
            for p in sub_parts:
                if p in '，、：':
                    sub_sent += p
                    if len(sub_sent) >= max_chars * 0.6 and sub_sent.strip():
                        result.append(sub_sent.strip())
                        sub_sent = ""
                else:
                    sub_sent += p
            if sub_sent.strip():
                result.append(sub_sent.strip())

    # 过滤空句
    return [s for s in result if s.strip()]


def generate_full_voiceover(
    script_lines: List[Dict[str, any]],
    output_path: Path,
    voice: str = DEFAULT_VOICE,
    total_duration: float = 25.0,
    pause_between_sentences: float = 0.15,
    max_rate_multiplier: float = 1.6,
) -> Tuple[Path, List[Dict[str, any]]]:
    """
    生成完整的口播音频（智能断句 + 多段拼接 + 自动时间对齐）

    Args:
        script_lines: 口播文案列表（含 text/start/end）
        output_path: 输出音频路径
        voice: 音色
        total_duration: 总时长（秒），用于生成空白底噪
        pause_between_sentences: 句子间停顿时间（秒）
        max_rate_multiplier: 最大语速倍率（溢出时加速，默认 1.6 倍）

    Returns:
        (输出文件路径, 对齐后的字幕列表)
    """
    import tempfile

    tmp_dir = Path(tempfile.mkdtemp(prefix="tts_"))
    voice_config = VOICE_PRESETS.get(voice, VOICE_PRESETS[DEFAULT_VOICE])
    base_rate = voice_config["rate"]

    try:
        # ============== 第一轮：正常语速生成，检测是否溢出 ==============
        audio_segments = []
        aligned_subtitles = []
        seg_counter = 0
        overflow_ratio = 1.0  # 溢出比例（>1 表示需要加速）
        has_ffmpeg = shutil.which("ffmpeg") is not None

        for line_idx, line in enumerate(script_lines):
            text = line["text"]
            target_start = line["start"]
            seg_num = line.get("segment", line_idx)

            sentences = split_sentences(text)
            if not sentences:
                continue

            current_time = target_start

            for sent_idx, sentence in enumerate(sentences):
                seg_path = tmp_dir / f"seg_{seg_counter:03d}.m4a"
                seg_counter += 1

                generate_tts_audio(sentence, seg_path, voice=voice)
                duration = _get_audio_duration(seg_path)
                actual_end = current_time + duration

                audio_segments.append({
                    "path": seg_path,
                    "start": current_time,
                    "duration": duration,
                    "sentence": sentence,
                })

                aligned_subtitles.append({
                    "text": sentence,
                    "start": current_time,
                    "end": actual_end,
                    "segment": seg_num,
                })

                if sent_idx < len(sentences) - 1:
                    current_time = actual_end + pause_between_sentences
                else:
                    current_time = actual_end

            # P1 修复：增加单段边界检查，防止口播超出该段分配的时间窗口
            _line_end = line.get("end", total_duration * 0.95)
            if _line_end > 0 and current_time > _line_end:
                _seg_ratio = current_time / _line_end
                overflow_ratio = max(overflow_ratio, _seg_ratio)

            if current_time > total_duration * 0.95:
                needed_ratio = current_time / (total_duration * 0.95)
                overflow_ratio = max(overflow_ratio, needed_ratio)

        # ============== 如果溢出：用 ffmpeg atempo 加速已有音频（比重新生成快很多） ==============
        if overflow_ratio > 1.0 and has_ffmpeg:
            actual_rate = min(base_rate * overflow_ratio, base_rate * max_rate_multiplier)
            rate_ratio = actual_rate / base_rate
            atempo = rate_ratio  # atempo = 输出时长/输入时长，加速时 >1

            print(f"  ⚡ 口播时长溢出，用 ffmpeg atempo 加速到 {rate_ratio:.2f}x")

            new_segments = []
            new_subtitles = []
            # P0 修复：atempo 加速后必须重新计算时间轴，基于前一段实际结束时间顺序排列，
            # 否则使用原始 seg["start"] 会导致段间出现与压缩比例成正比的累积空隙，
            # 字幕与音频严重错位。
            current_start = float(audio_segments[0]["start"]) if audio_segments else 0.0

            for i, seg in enumerate(audio_segments):
                fast_path = tmp_dir / f"fast_{i:03d}.m4a"
                try:
                    cmd = [
                        "ffmpeg", "-y", "-i", str(seg["path"]),
                        "-filter:a", f"atempo={atempo}",
                        "-vn", "-c:a", "aac", "-b:a", "128k",
                        str(fast_path),
                    ]
                    subprocess.run(cmd, capture_output=True, timeout=10, check=True)
                    new_duration = _get_audio_duration(fast_path)
                except Exception:
                    fast_path = seg["path"]
                    new_duration = seg["duration"]

                sub = aligned_subtitles[i]
                new_start = current_start
                new_end = new_start + new_duration
                if new_end > total_duration - 0.1:
                    new_end = total_duration - 0.1
                    new_duration = new_end - new_start
                    if new_duration < 0.1:
                        # P1 修复：跳过当前过短片段，保留后续内容，而非截断全部
                        continue

                new_segments.append({
                    "path": fast_path,
                    "start": new_start,
                    "duration": new_duration,
                })
                new_subtitles.append({
                    "text": sub["text"],
                    "start": new_start,
                    "end": new_end,
                    "segment": sub.get("segment", 0),
                })

                current_start = new_end

                if new_end >= total_duration - 0.1:
                    break

            audio_segments = new_segments
            aligned_subtitles = new_subtitles

        if shutil.which("ffmpeg") and audio_segments:
            _mix_audio_segments(audio_segments, output_path, total_duration)
        else:
            _simple_concat(audio_segments, output_path)

        return output_path, aligned_subtitles

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _get_audio_duration(audio_path: Path) -> float:
    """获取音频时长（秒），带 LRU 缓存"""
    from utils_ffprobe import get_audio_duration
    dur = get_audio_duration(str(audio_path))
    return dur if dur > 0 else 2.0  # 失败时默认 2 秒（兼容旧行为）


def _mix_audio_segments(
    segments: List[Dict[str, any]],
    output_path: Path,
    total_duration: float,
):
    """
    使用 ffmpeg 将多段音频混合到时间轴上

    Args:
        segments: 音频片段列表（path/start/duration）
        output_path: 输出路径
        total_duration: 总时长
    """
    # 生成极轻微的底噪（防止完全静音的起始）
    noise_path = output_path.parent / "_base_noise.m4a"
    cmd_base = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        # c=white 是白噪声，a=0.005 是极低音量（几乎听不到，仅用于填充静音）
        "-i", f"anoisesrc=d={total_duration}:c=white:r=44100:a=0.005",
        "-c:a", "aac",
        "-b:a", "128k",
        str(noise_path),
    ]
    result = subprocess.run(cmd_base, capture_output=True, timeout=30)
    noise_ok = result.returncode == 0 and noise_path.exists()

    # 构建滤镜：在指定时间点插入各段语音
    # 使用 adelay 滤镜延迟每段音频，然后 amix 混合
    filter_parts = []
    inputs = []

    if noise_ok:
        inputs.extend(["-i", str(noise_path)])
        noise_label = "[0:a]"
        mix_offset = 1
    else:
        # 底噪生成失败就不用，直接混合各段语音
        noise_label = ""
        mix_offset = 0

    for i, seg in enumerate(segments):
        inputs.extend(["-i", str(seg["path"])])
        delay_ms = int(seg["start"] * 1000)
        filter_parts.append(f"[{i+mix_offset}:a]adelay={delay_ms}:all=1[a{i+1}]")

    # 混合所有音轨
    mix_inputs = "".join(f"[a{i+1}]" for i in range(len(segments)))
    total_inputs = len(segments) + (1 if noise_ok else 0)
    # P1 修复：duration=longest 防止第一段不是最长时后续语音被截断
    filter_parts.append(
        f"{noise_label}{mix_inputs}amix=inputs={total_inputs}:duration=longest:dropout_transition=0:normalize=0[aout]"
    )

    filter_complex = ";".join(filter_parts)

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[aout]",
        "-c:a", "aac",
        "-b:a", "192k",
        str(output_path),
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, timeout=60)
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="ignore")[:500] if result.stderr else ""
            raise RuntimeError(f"口播混音失败：{stderr}")
        _validate_audio_file(output_path)
    finally:
        if noise_path.exists():
            noise_path.unlink()


def _simple_concat(segments: List[Dict[str, any]], output_path: Path):
    """简单拼接（fallback）"""
    if not segments:
        return
    # 直接用第一段
    shutil.copy2(segments[0]["path"], output_path)


def align_subtitles_to_voiceover(
    subtitles: List[Dict[str, any]],
    voiceover_subs: List[Dict[str, any]],
) -> List[Dict[str, any]]:
    """
    将字幕与口播时间轴对齐（以口播为准，保留原字幕样式信息）。

    策略：
    - 按 text 内容做双指针模糊匹配（去标点对比），找到对应关系后
      将 voiceover_sub 的 start/end 覆盖到原字幕条目，
      其余字段（highlight / style_override 等）保留原值。
    - voiceover_subs 多出的条目直接追加。
    - 匹配不上的原字幕保留原时间，避免字幕消失。

    Args:
        subtitles: 原始字幕列表（含 highlight / style 等字段）
        voiceover_subs: 口播对齐后的字幕列表（含准确 start/end）

    Returns:
        对齐后的字幕列表
    """
    if not voiceover_subs:
        return subtitles
    if not subtitles:
        return voiceover_subs

    import re as _re

    def _normalize(text: str) -> str:
        """去掉标点和空格，便于模糊比较"""
        return _re.sub(r"[\s\W]", "", text or "")

    # 建立 voiceover_subs 的 normalized text → index 映射
    vo_norm = [_normalize(v.get("text", "")) for v in voiceover_subs]
    orig_norm = [_normalize(s.get("text", "")) for s in subtitles]

    matched: List[Dict[str, any]] = []
    vo_idx = 0  # 滑动游标

    for i, sub in enumerate(subtitles):
        orig_key = orig_norm[i]
        found = False
        # 在 voiceover_subs 的剩余部分中找最近匹配
        for j in range(vo_idx, len(voiceover_subs)):
            vo_key = vo_norm[j]
            # 精确匹配或包含关系（口播断句可能更短）
            if orig_key == vo_key or (orig_key and orig_key in vo_key) or (vo_key and vo_key in orig_key):
                merged = dict(sub)  # 保留原字幕所有字段（highlight, style 等）
                merged["start"] = voiceover_subs[j]["start"]
                merged["end"] = voiceover_subs[j]["end"]
                matched.append(merged)
                vo_idx = j + 1
                found = True
                break
        if not found:
            # 匹配不上：保留原字幕原时间，不丢弃
            matched.append(dict(sub))

    # voiceover_subs 中多余的条目（原字幕没有的新断句）直接追加
    for j in range(vo_idx, len(voiceover_subs)):
        matched.append(dict(voiceover_subs[j]))

    # 按 start 排序，确保时间轴有序
    matched.sort(key=lambda s: s.get("start", 0))
    return matched



def list_available_voices() -> List[Dict[str, str]]:
    """列出系统可用的中文语音"""
    voices = []
    try:
        result = subprocess.run(
            ["say", "-v", "?"],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.strip().split("\n"):
            if "zh_" in line:
                parts = line.split("#")
                name_part = parts[0].strip() if parts else ""
                sample = parts[1].strip() if len(parts) > 1 else ""
                voices.append({
                    "name": name_part,
                    "sample": sample,
                })
    except Exception:
        pass
    return voices


if __name__ == "__main__":
    # 测试
    print("🎤 可用的中文语音：")
    for v in list_available_voices():
        print(f"  - {v['name']}: {v['sample']}")

    print("\n🎬 口播风格：")
    for key, style in VOICEOVER_TEMPLATES.items():
        print(f"  - {key}: {style['name']}")

    print("\n🎵 音色预设：")
    for key, voice in VOICE_PRESETS.items():
        print(f"  - {key}: {voice['name']} ({voice['voice']})")
