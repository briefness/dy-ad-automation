"""Interactive CLI prompts."""

import argparse
import sys

from video_style import _recommend_video_style, apply_video_style_to_args


def ensure_interactive_terminal_sane(stream=None) -> bool:
    """Restore the terminal flags required by Python's line-based input()."""
    stream = stream or sys.stdin
    if not getattr(stream, "isatty", lambda: False)():
        return False
    try:
        import termios

        fd = stream.fileno()
        current = termios.tcgetattr(fd)
        repaired = current.copy()
        repaired[0] = (repaired[0] | termios.ICRNL) & ~(
            termios.IGNCR | termios.INLCR
        )
        repaired[3] |= termios.ICANON | termios.ECHO | termios.ISIG
        if repaired == current:
            return False
        termios.tcsetattr(fd, termios.TCSANOW, repaired)
        return True
    except (AttributeError, ImportError, OSError, ValueError):
        return False


def input_with_default(prompt: str, default: str = "") -> str:
    """带默认值的输入"""
    if default:
        user_input = input(f"{prompt} [{default}]：").strip()
        return user_input if user_input else default
    return input(f"{prompt}：").strip()


def prompt_video_style_if_needed(args: argparse.Namespace, product_info: dict) -> dict:
    """交互模式下展示智能推荐的视频风格，并允许用户改成最终值。"""
    explicit = set(getattr(args, "_explicit_args", set()) or set())
    if "video_style" in explicit and str(getattr(args, "video_style", "") or "").strip() != "auto":
        args.video_style_source = "user"
        return apply_video_style_to_args(args, product_info)

    recommended = _recommend_video_style(product_info, args)
    chosen = input_with_default("视频风格（影响脚本/字幕/口播）", recommended).strip()
    args.video_style = chosen or recommended
    args.video_style_source = "auto" if args.video_style == recommended else "user"
    if args.video_style_source == "user":
        args._explicit_args = explicit | {"video_style"}
    else:
        args._explicit_args = explicit - {"video_style"}
    return apply_video_style_to_args(args, product_info)
