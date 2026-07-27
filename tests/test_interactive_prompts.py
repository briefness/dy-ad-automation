import os
import pty
import termios
from pathlib import Path

from interactive_prompts import ensure_interactive_terminal_sane


def test_interactive_terminal_restores_enter_and_canonical_input():
    master_fd, slave_fd = pty.openpty()
    try:
        broken = termios.tcgetattr(slave_fd)
        broken[0] &= ~termios.ICRNL
        broken[0] |= termios.IGNCR | termios.INLCR
        broken[3] &= ~(termios.ICANON | termios.ECHO | termios.ISIG)
        termios.tcsetattr(slave_fd, termios.TCSANOW, broken)

        with os.fdopen(os.dup(slave_fd), "r", encoding="utf-8") as stream:
            assert ensure_interactive_terminal_sane(stream) is True

        repaired = termios.tcgetattr(slave_fd)
        assert repaired[0] & termios.ICRNL
        assert not repaired[0] & termios.IGNCR
        assert not repaired[0] & termios.INLCR
        assert repaired[3] & termios.ICANON
        assert repaired[3] & termios.ECHO
        assert repaired[3] & termios.ISIG
    finally:
        os.close(master_fd)
        os.close(slave_fd)


def test_one_click_main_repairs_terminal_before_interactive_input():
    source = Path("one_click_create.py").read_text(encoding="utf-8")
    main_block = source[source.index("def main():"):]

    assert main_block.index("ensure_interactive_terminal_sane()") < main_block.index(
        "if args.load:"
    )
