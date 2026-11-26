"""Acoustic envelope DP forced alignment and optional local Whisper ASR backend."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _speech_units(text: str) -> float:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]+", "", str(text or ""))
    if not cleaned:
        return 1.0
    return sum(1.0 if ord(ch) > 127 else 0.45 for ch in cleaned)


def _extract_pcm_envelope(
    audio_path: Path,
    *,
    sample_rate: int = 8000,
    hop_ms: int = 20,
) -> Tuple[List[float], float]:
    """Decode mono PCM via FFmpeg and compute RMS energy per ``hop_ms`` frame."""
    if not audio_path.exists() or shutil.which("ffmpeg") is None:
        return [], hop_ms / 1000.0
    import numpy as np

    proc = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-i", str(audio_path),
            "-ac", "1", "-ar", str(sample_rate),
            "-f", "s16le", "-",
        ],
        capture_output=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout:
        return [], hop_ms / 1000.0
    samples = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    hop_samples = max(1, int(sample_rate * hop_ms / 1000.0))
    num_frames = len(samples) // hop_samples
    if num_frames <= 0:
        return [], hop_ms / 1000.0
    trimmed = samples[:num_frames * hop_samples].reshape(num_frames, hop_samples)
    rms = np.sqrt(np.mean(trimmed * trimmed, axis=1))
    return rms.tolist(), hop_samples / float(sample_rate)


def _try_local_whisper_words(audio_path: Path, language: str = "zh") -> Optional[List[Dict[str, Any]]]:
    """Extract word-level timestamps via mlx-whisper or faster-whisper if installed."""
    audio_str = str(audio_path)
    try:
        import mlx_whisper  # type: ignore

        res = mlx_whisper.transcribe(audio_str, language=language, word_timestamps=True)
        words = [
            {
                "word": str(w.get("word") or "").strip(),
                "start": float(w.get("start") or 0.0),
                "end": float(w.get("end") or 0.0),
            }
            for seg in res.get("segments") or []
            for w in seg.get("words") or []
            if str(w.get("word") or "").strip()
        ]
        if words:
            return words
    except Exception:
        pass

    try:
        from faster_whisper import WhisperModel  # type: ignore

        model = WhisperModel("small", device="auto", compute_type="int8")
        segments, _ = model.transcribe(audio_str, language=language, word_timestamps=True)
        words = [
            {
                "word": str(getattr(w, "word", "") or "").strip(),
                "start": float(getattr(w, "start", 0.0)),
                "end": float(getattr(w, "end", 0.0)),
            }
            for seg in segments
            for w in (getattr(seg, "words", None) or [])
            if str(getattr(w, "word", "") or "").strip()
        ]
        if words:
            return words
    except Exception:
        pass

    return None


def transcribe_local_whisper(
    audio_path: Path,
    *,
    language: str = "zh",
    model_name: str = "small",
) -> Dict[str, Any]:
    """Local zero-API-cost ASR backend for ``source_audio_understanding.py``."""
    audio_str = str(audio_path)
    try:
        import mlx_whisper  # type: ignore

        raw = mlx_whisper.transcribe(
            audio_str,
            path_or_hf_repo=model_name if "/" in model_name else "mlx-community/whisper-small-mlx",
            language=language,
            word_timestamps=True,
        )
        segments = [
            {
                "start": float(seg.get("start") or 0.0),
                "end": float(seg.get("end") or 0.0),
                "text": str(seg.get("text") or "").strip(),
                "no_speech_prob": float(seg.get("no_speech_prob") or 0.0),
                "words": [
                    {
                        "word": str(w.get("word") or "").strip(),
                        "start": float(w.get("start") or 0.0),
                        "end": float(w.get("end") or 0.0),
                    }
                    for w in seg.get("words") or []
                    if str(w.get("word") or "").strip()
                ],
            }
            for seg in raw.get("segments") or []
        ]
        return {"segments": segments, "backend": "mlx_whisper"}
    except Exception:
        pass

    try:
        from faster_whisper import WhisperModel  # type: ignore

        model = WhisperModel(model_name or "small", device="auto", compute_type="int8")
        seg_iter, _ = model.transcribe(audio_str, language=language, word_timestamps=True)
        segments = [
            {
                "start": float(seg.start),
                "end": float(seg.end),
                "text": str(seg.text or "").strip(),
                "no_speech_prob": float(getattr(seg, "no_speech_prob", 0.0) or 0.0),
                "words": [
                    {
                        "word": str(w.word or "").strip(),
                        "start": float(w.start),
                        "end": float(w.end),
                    }
                    for w in (getattr(seg, "words", None) or [])
                    if str(w.word or "").strip()
                ],
            }
            for seg in seg_iter
        ]
        return {"segments": segments, "backend": "faster_whisper"}
    except Exception as exc:
        raise RuntimeError(
            "本地 ASR 需要安装 mlx-whisper 或 faster-whisper（当前环境未安装）"
        ) from exc


def align_lines_acoustic_dp(
    lines: List[Dict[str, Any]],
    audio_path: Path,
    duration: float,
    *,
    preserve_container_edges: bool = False,
    enable_whisper: bool = True,
) -> Optional[List[Dict[str, Any]]]:
    """Align authored continuous-TTS cues onto real acoustic speech valleys.

    Never alters authored copy; only refines ``start``/``end`` boundaries so
    the single continuous TTS master and shot-locked subtitles stay tightly synced.
    """
    if not lines:
        return []
    container_start = float(lines[0].get("start", 0.0))
    total_span = max(0.1, float(duration) - container_start)

    observed_words = _try_local_whisper_words(audio_path) if enable_whisper else None
    if observed_words and len(observed_words) >= len(lines):
        aligned = _align_with_whisper_words(
            lines, observed_words, container_start, float(duration), preserve_container_edges
        )
        if aligned:
            return aligned

    rms, hop_sec = _extract_pcm_envelope(audio_path)
    if len(rms) < 10:
        return None

    peak = max(rms) or 1.0
    norm_rms = [v / peak for v in rms]
    speech_thresh = 0.055

    active_indices = [i for i, v in enumerate(norm_rms) if v >= speech_thresh]
    if active_indices:
        meas_start = active_indices[0] * hop_sec
        meas_end = min(total_span, (active_indices[-1] + 1) * hop_sec)
    else:
        meas_start, meas_end = 0.0, total_span

    start = container_start if preserve_container_edges else container_start + meas_start
    end = (
        max(container_start + 0.1, float(duration))
        if preserve_container_edges
        else max(start + 0.1, container_start + meas_end)
    )

    if len(lines) == 1:
        item = dict(lines[0])
        item.update({
            "start": round(start, 4),
            "end": round(end, 4),
            "alignment_precision": "acoustic_envelope_dp",
        })
        return [item]

    weights = [_speech_units(line.get("text", "")) for line in lines]
    total_weight = sum(weights) or 1.0
    expected_cuts: List[float] = []
    acc = 0.0
    for weight in weights[:-1]:
        acc += weight
        expected_cuts.append(start + (end - start) * (acc / total_weight))

    smoothed = [
        sum(norm_rms[max(0, i - 1):min(len(norm_rms), i + 2)]) /
        max(1, min(len(norm_rms), i + 2) - max(0, i - 1))
        for i in range(len(norm_rms))
    ]

    search_radius = 0.85
    boundaries: List[Tuple[float, str]] = []
    prev = start
    num_cuts = len(expected_cuts)
    for cut_idx, exp_t in enumerate(expected_cuts):
        min_t = prev + 0.12
        max_t = end - 0.12 * (num_cuts - cut_idx)
        lo_t = max(min_t, exp_t - search_radius)
        hi_t = min(max_t, exp_t + search_radius)
        lo_frame = max(0, int((lo_t - container_start) / hop_sec))
        hi_frame = min(len(smoothed) - 1, int((hi_t - container_start) / hop_sec))

        best_t = max(min_t, min(max_t, exp_t))
        precision = "speech_weight_estimate"
        if hi_frame > lo_frame:
            best_cost = float("inf")
            for f_idx in range(lo_frame, hi_frame + 1):
                cand_t = container_start + f_idx * hop_sec
                if cand_t < min_t or cand_t > max_t:
                    continue
                dist_norm = abs(cand_t - exp_t) / search_radius
                cost = smoothed[f_idx] * 2.2 + 0.35 * (dist_norm ** 2)
                if cost < best_cost:
                    best_cost = cost
                    best_t = cand_t
            if best_cost < 0.75:
                precision = "acoustic_envelope_dp"

        boundaries.append((best_t, precision))
        prev = best_t

    aligned: List[Dict[str, Any]] = []
    cursor = start
    for idx, line in enumerate(lines):
        cut_t, precision = boundaries[idx] if idx < len(boundaries) else (end, "acoustic_envelope_end")
        aligned.append({
            **line,
            "start": round(cursor, 4),
            "end": round(cut_t, 4),
            "alignment_precision": precision,
        })
        cursor = cut_t
    return aligned


def _align_with_whisper_words(
    lines: List[Dict[str, Any]],
    words: List[Dict[str, Any]],
    container_start: float,
    duration: float,
    preserve_container_edges: bool,
) -> Optional[List[Dict[str, Any]]]:
    total_chars = sum(max(1, len(re.sub(r"\W+", "", str(w["word"])))) for w in words)
    line_lens = [max(1, len(re.sub(r"\W+", "", str(line.get("text") or "")))) for line in lines]
    total_line_chars = sum(line_lens) or 1

    start_edge = container_start if preserve_container_edges else container_start + float(words[0]["start"])
    end_edge = (
        max(container_start + 0.1, duration)
        if preserve_container_edges
        else min(duration, container_start + float(words[-1]["end"]))
    )

    aligned: List[Dict[str, Any]] = []
    word_cursor = 0
    acc_target = 0.0
    acc_words = 0
    prev_t = start_edge

    for idx, line in enumerate(lines):
        acc_target += (line_lens[idx] / total_line_chars) * total_chars
        if idx == len(lines) - 1:
            end_t = max(prev_t + 0.1, end_edge)
        else:
            start_w = word_cursor
            while word_cursor < len(words) - (len(lines) - idx - 1) and acc_words < acc_target:
                acc_words += max(1, len(re.sub(r"\W+", "", str(words[word_cursor]["word"]))))
                word_cursor += 1
            seg_words = words[start_w:max(start_w + 1, word_cursor)]
            raw_end = container_start + float(seg_words[-1]["end"])
            end_t = max(prev_t + 0.1, min(end_edge - 0.1 * (len(lines) - idx - 1), raw_end))

        aligned.append({
            **line,
            "start": round(prev_t, 4),
            "end": round(end_t, 4),
            "alignment_precision": "whisper_word_aligned",
        })
        prev_t = end_t
    return aligned
