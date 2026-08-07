from __future__ import annotations

import contextlib
import io
import os
import traceback
from argparse import Namespace
from pathlib import Path


STAGE_PATTERNS = (
    (("开始分析本地素材", "本地素材分析"), "asset_analysis", 18),
    (("广告脚本", "脚本预检"), "script", 34),
    (("本地素材选片", "本地素材裁片", "剪辑决策报告"), "clip_selection", 50),
    (("口播", "字幕"), "audio_and_subtitles", 64),
    (("视频拼接", "调色", "稳定化"), "render", 78),
    (("质量检测", "质量门"), "quality", 90),
    (("最终成片已导出", "一键成片完成"), "finishing", 96),
)


def _emit(queue, kind, **payload):
    queue.put({"kind": kind, **payload})


def _stage_for_line(line: str):
    for needles, stage, progress in STAGE_PATTERNS:
        if any(needle in line for needle in needles):
            return stage, progress
    return None


def run_worker(kind: str, payload: dict, queue) -> None:
    if os.name == "posix":
        try: os.setsid()
        except OSError: pass
    class Tee(io.TextIOBase):
        def write(self, value):
            if value.strip():
                line = value.rstrip()
                _emit(queue, "log", stream="stdout", line=line)
                stage = _stage_for_line(line)
                if stage:
                    _emit(queue, "stage", stage=stage[0], progress=stage[1])
            return len(value)
        def flush(self): pass
    try:
        if kind == "preflight":
            from .services import build_preflight
            _emit(queue, "stage", stage="preflight", progress=20)
            with contextlib.redirect_stdout(Tee()), contextlib.redirect_stderr(Tee()):
                result = build_preflight(**payload)
            public_result = {
                key: value for key, value in result.items()
                if key not in {"index", "contract"}
            }
            _emit(queue, "result", result=public_result)
            return
        from one_click_create import run_one_click_create
        output_dir = Path(payload.get("output_dir"))
        args = Namespace(style="auto", video_style=payload.get("video_style", "auto"), duration=int(payload.get("duration") or 5), mode="pro", aspect_ratio="9:16", output_name=payload.get("output_name"), dual_output=False, product_image=None, voiceover=bool(payload.get("voiceover", True)), voiceover_style="standard", voice=payload.get("voice", "auto"), script_style="pain_point_solution", strict=True, resume=bool(payload.get("resume", True)), target_duration=payload.get("target_duration"), rhythm_style=payload.get("rhythm_style", "moderate"), local_assets=str(payload["asset_path"]), stickers=payload.get("stickers", "auto"), _explicit_args=set(), hook="demonstration", serial=False, min_clips=3, best_of=1, quality_frames=12, keep_candidates=False, preview=False, max_workers=4, stabilize=True, brand_intro_outro=False, kling_model=None, multi_shot=False, preflight_keyframe=True, image_first=True, image_first_mode="standard", image_first_variants=2, reference_video=None, allow_no_product_image=True, force=False, seed=None, image_fidelity=0.9, human_fidelity=0.9, output_dir=str(output_dir))
        with contextlib.redirect_stdout(Tee()), contextlib.redirect_stderr(Tee()):
            _emit(queue, "stage", stage="running", progress=10)
            final = run_one_click_create(payload["product"], args, output_dir=output_dir)
        _emit(queue, "result", result={"final_path": str(final), "output_dir": str(output_dir)})
    except BaseException as exc:
        _emit(queue, "error", error=str(exc), traceback=traceback.format_exc())
