"""Timestamped speech understanding for original local-video audio."""

from __future__ import annotations

import base64
import hashlib
import re
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


MAX_NO_SPEECH_PROBABILITY = 0.65


@dataclass(frozen=True)
class AudioUnderstandingConfig:
    base_url: str
    api_key: str
    model: str
    provider: str = "openai"
    timeout: int = 90
    max_retries: int = 1
    language: str = "zh"

    @property
    def available(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)


def _default_config() -> AudioUnderstandingConfig:
    from config import (
        ASR_API_KEY,
        ASR_BASE_URL,
        ASR_LANGUAGE,
        ASR_MAX_RETRIES,
        ASR_MODEL,
        ASR_PROVIDER,
        ASR_TIMEOUT,
    )

    return AudioUnderstandingConfig(
        base_url=ASR_BASE_URL,
        api_key=ASR_API_KEY,
        model=ASR_MODEL,
        provider=ASR_PROVIDER,
        timeout=ASR_TIMEOUT,
        max_retries=ASR_MAX_RETRIES,
        language=ASR_LANGUAGE,
    )


def audio_understanding_signature(
    config: Optional[AudioUnderstandingConfig] = None,
) -> str:
    settings = config or _default_config()
    payload = (
        f"{settings.provider}|{settings.base_url.rstrip('/')}|{settings.model}|{settings.language}"
        if settings.available else
        "unavailable"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _empty_result(status: str, error: str = "") -> Dict[str, Any]:
    return {
        "status": status,
        "has_speech": False,
        "transcript": "",
        "segments": [],
        "speech_seconds": 0.0,
        "error": error,
    }


def _extract_audio(source: Path, output: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-b:a",
            "48k",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    if not output.exists() or output.stat().st_size == 0:
        raise RuntimeError("FFmpeg 未生成可转写音频")


def _normalize_segments(raw: Dict[str, Any], duration: float) -> List[Dict[str, Any]]:
    segments = []
    for value in raw.get("segments") or []:
        if not isinstance(value, dict):
            continue
        text = str(value.get("text") or "").strip()
        no_speech_probability = float(value.get("no_speech_prob") or 0.0)
        start = max(0.0, min(duration, float(value.get("start") or 0.0)))
        end = max(start, min(duration, float(value.get("end") or start)))
        if not text or end <= start or no_speech_probability > MAX_NO_SPEECH_PROBABILITY:
            continue
        segments.append({
            "start": round(start, 3),
            "end": round(end, 3),
            "text": text,
            "confidence": round(1.0 - no_speech_probability, 3),
        })
    return segments


def _transcribe_openai_compatible(
    audio_path: Path,
    settings: AudioUnderstandingConfig,
) -> Dict[str, Any]:
    with audio_path.open("rb") as audio:
        response = requests.post(
            f"{settings.base_url.rstrip('/')}/audio/transcriptions",
            headers={"Authorization": f"Bearer {settings.api_key}"},
            files={"file": (audio_path.name, audio, "audio/mpeg")},
            data={
                "model": settings.model,
                "language": settings.language,
                "response_format": "verbose_json",
                "timestamp_granularities[]": "segment",
            },
            timeout=settings.timeout,
        )
    response.raise_for_status()
    raw = response.json()
    return raw if isinstance(raw, dict) else {}


def _transcribe_volcengine_turbo(
    audio_path: Path,
    settings: AudioUnderstandingConfig,
) -> Dict[str, Any]:
    request_id = str(uuid.uuid4())
    response = requests.post(
        settings.base_url.rstrip("/"),
        headers={
            "Content-Type": "application/json",
            "X-Api-Key": settings.api_key,
            "X-Api-Resource-Id": settings.model,
            "X-Api-Request-Id": request_id,
            "X-Api-Sequence": "-1",
        },
        json={
            "audio": {
                "data": base64.b64encode(audio_path.read_bytes()).decode("ascii"),
                "format": "mp3",
                "language": {"zh": "zh-CN", "en": "en-US"}.get(
                    settings.language,
                    settings.language,
                ),
            },
            "request": {
                "model_name": "bigmodel",
                "enable_itn": True,
                "enable_punc": True,
                "show_utterances": True,
            },
        },
        timeout=settings.timeout,
    )
    response.raise_for_status()
    status_code = str(response.headers.get("X-Api-Status-Code") or "")
    if status_code == "20000003":
        return {"segments": []}
    if status_code != "20000000":
        message = str(response.headers.get("X-Api-Message") or "unknown error")
        log_id = str(response.headers.get("X-Tt-Logid") or "")
        raise RuntimeError(
            f"Volcengine ASR failed: status={status_code or 'missing'}, "
            f"message={message}, log_id={log_id or 'missing'}"
        )

    raw = response.json()
    result = raw.get("result") if isinstance(raw, dict) else {}
    result_parts = result if isinstance(result, list) else [result]
    utterances = [
        utterance
        for part in result_parts
        if isinstance(part, dict)
        for utterance in part.get("utterances") or []
        if isinstance(utterance, dict)
    ]
    segments = [
        {
            "start": float(utterance.get("start_time") or 0.0) / 1000.0,
            "end": float(utterance.get("end_time") or 0.0) / 1000.0,
            "text": str(utterance.get("text") or "").strip(),
            "no_speech_prob": 0.0,
        }
        for utterance in utterances
    ]
    if not segments:
        transcript = "".join(
            str(part.get("text") or "").strip()
            for part in result_parts
            if isinstance(part, dict)
        )
        if transcript:
            segments = [{
                "start": 0.0,
                "end": float((raw.get("audio_info") or {}).get("duration") or 0.0) / 1000.0,
                "text": transcript,
                "no_speech_prob": 0.0,
            }]
    return {"segments": segments}


def _transcribe_audio(
    audio_path: Path,
    settings: AudioUnderstandingConfig,
) -> Dict[str, Any]:
    provider = settings.provider.strip().lower()
    if provider in {"openai", "openai_compatible"}:
        return _transcribe_openai_compatible(audio_path, settings)
    if provider == "volcengine":
        return _transcribe_volcengine_turbo(audio_path, settings)
    raise ValueError(f"不支持的 ASR_PROVIDER：{settings.provider}")


def analyze_source_audio(
    source: Path,
    duration: float,
    has_audio: bool,
    *,
    config: Optional[AudioUnderstandingConfig] = None,
) -> Dict[str, Any]:
    """Transcribe original speech without making it a trusted factual source."""
    if not has_audio:
        return _empty_result("no_audio")
    settings = config or _default_config()
    if not settings.available:
        return _empty_result("unavailable")

    try:
        with tempfile.TemporaryDirectory(prefix="source-audio-") as temp_dir:
            audio_path = Path(temp_dir) / "source.mp3"
            _extract_audio(Path(source), audio_path)
            retry_count = max(0, int(settings.max_retries))
            for attempt in range(retry_count + 1):
                try:
                    raw = _transcribe_audio(audio_path, settings)
                    break
                except requests.RequestException:
                    if attempt >= retry_count:
                        raise
    except Exception as exc:
        return _empty_result("failed", f"{type(exc).__name__}: {exc}")

    segments = _normalize_segments(raw if isinstance(raw, dict) else {}, max(0.0, duration))
    if not segments:
        return _empty_result("no_speech")
    return {
        "status": "transcribed",
        "has_speech": True,
        "transcript": "".join(segment["text"] for segment in segments),
        "segments": segments,
        "speech_seconds": round(sum(segment["end"] - segment["start"] for segment in segments), 3),
        "error": "",
    }


def audio_context_for_window(
    profile: Dict[str, Any],
    start: float,
    end: float,
) -> Dict[str, Any]:
    """Return only speech that overlaps one source-video analysis window."""
    segments = [
        dict(segment)
        for segment in profile.get("segments") or []
        if float(segment.get("end") or 0.0) > start
        and float(segment.get("start") or 0.0) < end
    ]
    transcript = "".join(str(segment.get("text") or "").strip() for segment in segments)
    normalized = re.sub(r"\W+", "", transcript).lower()
    speech_seconds = sum(
        max(0.0, min(end, float(segment["end"])) - max(start, float(segment["start"])))
        for segment in segments
    )
    confidences = [float(segment.get("confidence") or 0.0) for segment in segments]
    return {
        "status": "transcribed" if transcript else "no_speech",
        "source_status": str(profile.get("status") or "unavailable"),
        "has_speech": bool(transcript),
        "transcript": transcript,
        "segments": segments,
        "speech_seconds": round(speech_seconds, 3),
        "speech_ratio": round(speech_seconds / max(end - start, 0.001), 3),
        "confidence": round(sum(confidences) / len(confidences), 3) if confidences else 0.0,
        "semantic_key": hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16] if normalized else "",
    }
