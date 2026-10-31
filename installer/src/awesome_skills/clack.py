"""A stdlib reimplementation of the clack prompt look used by codegraph.

Codegraph's installer renders through ``@clack/prompts``: a ``┌ │ └`` guide
rail, ``◆``/``◇`` step symbols, cyan active frame, green submit frame, and
a ``note`` box. This module reproduces that visual language without Node or
third-party packages.

Interactive prompts (select / multiselect / confirm / text) switch the
terminal to cbreak mode and read arrow keys, space, Enter, y/n, backspace
and Ctrl-C on POSIX. Windows falls back to ``msvcrt``. When stdin/stdout is
not a TTY, callers must use the plain path instead — the wizard only runs
on terminals, tests use ``ScriptedUI``.
"""

from __future__ import annotations

import os
import select as _select
import shutil
import sys
import textwrap
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence


class UIAbort(Exception):
    """The user cancelled the prompt (Ctrl-C / Ctrl-D)."""


@dataclass(frozen=True)
class Option:
    value: str
    label: str
    hint: str | None = None
    disabled: bool = False


# ---------------------------------------------------------------------------
# Colors and glyphs
# ---------------------------------------------------------------------------


def _tty(stream) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError, OSError):
        return False


def colors_enabled(stream) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    forced = os.environ.get("FORCE_COLOR", "")
    if forced:
        return forced not in ("0", "false", "False")
    return _tty(stream) and os.environ.get("TERM") != "dumb"


def unicode_enabled(stream) -> bool:
    if os.environ.get("AWESOME_SKILLS_ASCII") == "1":
        return False
    if os.environ.get("AWESOME_SKILLS_UNICODE") == "1":
        return True
    if os.name == "nt":
        env = os.environ
        return bool(
            env.get("CI")
            or env.get("WT_SESSION")
            or env.get("TERM") == "xterm-256color"
            or env.get("TERM_PROGRAM") == "vscode"
        )
    encoding = getattr(stream, "encoding", "") or ""
    return os.environ.get("TERM") != "linux" and "utf" in encoding.lower()


@dataclass(frozen=True)
class Glyphs:
    step_active: str
    step_submit: str
    step_cancel: str
    step_error: str
    bar_start: str
    bar: str
    bar_end: str
    radio_active: str
    radio_inactive: str
    checkbox_active: str
    checkbox_selected: str
    checkbox_inactive: str
    info: str
    success: str
    warn: str
    error: str
    hline: str
    corner_top_right: str
    connect_left: str
    corner_bottom_right: str


UNICODE_GLYPHS = Glyphs(
    step_active="◆",
    step_submit="◇",
    step_cancel="■",
    step_error="▲",
    bar_start="┌",
    bar="│",
    bar_end="└",
    radio_active="●",
    radio_inactive="○",
    checkbox_active="◻",
    checkbox_selected="◼",
    checkbox_inactive="◻",
    info="●",
    success="◆",
    warn="▲",
    error="■",
    hline="─",
    corner_top_right="╮",
    connect_left="├",
    corner_bottom_right="╯",
)

ASCII_GLYPHS = Glyphs(
    step_active="*",
    step_submit="o",
    step_cancel="x",
    step_error="x",
    bar_start="T",
    bar="|",
    bar_end="-",
    radio_active=">",
    radio_inactive=" ",
    checkbox_active="[•]",
    checkbox_selected="[+]",
    checkbox_inactive="[ ]",
    info="•",
    success="*",
    warn="!",
    error="x",
    hline="-",
    corner_top_right="+",
    connect_left="+",
    corner_bottom_right="+",
)


# ---------------------------------------------------------------------------
# The renderer
# ---------------------------------------------------------------------------


class Clack:
    """clack-style output and prompts on a stdlib-only terminal."""

    def __init__(
        self,
        stream=None,
        input_stream=None,
        *,
        color: bool | None = None,
        unicode: bool | None = None,
        max_items: int = 10,
    ) -> None:
        self.stream = stream or sys.stdout
        self.input = input_stream or sys.stdin
        self.color = colors_enabled(self.stream) if color is None else color
        self.glyphs = (
            UNICODE_GLYPHS if (unicode if unicode is not None else unicode_enabled(self.stream))
            else ASCII_GLYPHS
        )
        self.max_items = max_items
        self._painted = 0
        self._cursor_hidden = False

    # -- styling ----------------------------------------------------------

    def _style(self, code: str, reset: str, text: str) -> str:
        if not self.color or not text:
            return text
        return f"\x1b[{code}m{text}\x1b[{reset}m"

    def cyan(self, text: str) -> str:
        return self._style("36", "39", text)

    def green(self, text: str) -> str:
        return self._style("32", "39", text)

    def red(self, text: str) -> str:
        return self._style("31", "39", text)

    def yellow(self, text: str) -> str:
        return self._style("33", "39", text)

    def blue(self, text: str) -> str:
        return self._style("34", "39", text)

    def gray(self, text: str) -> str:
        return self._style("90", "39", text)

    def dim(self, text: str) -> str:
        return self._style("2", "22", text)

    def strike(self, text: str) -> str:
        return self._style("9", "29", text)

    # -- frame ------------------------------------------------------------

    def intro(self, title: str) -> None:
        self._write(f"{self.gray(self.glyphs.bar_start)}  {title}\n")

    def outro(self, message: str) -> None:
        self._write(f"{self.gray(self.glyphs.bar)}\n")
        self._write(f"{self.gray(self.glyphs.bar_end)}  {message}\n\n")

    def cancel(self, message: str) -> None:
        self._write(f"{self.gray(self.glyphs.bar_end)}  {self.red(message)}\n\n")

    # -- logs -------------------------------------------------------------

    def _log(self, symbol: str, message: str) -> None:
        lines = message.splitlines() or [""]
        self._write(f"{self.gray(self.glyphs.bar)}\n")
        self._write(f"{symbol}  {lines[0]}\n")
        for line in lines[1:]:
            self._write(f"{self.gray(self.glyphs.bar)}  {line}\n")

    def info(self, message: str) -> None:
        self._log(self.blue(self.glyphs.info), message)

    def success(self, message: str) -> None:
        self._log(self.green(self.glyphs.success), message)

    def step(self, message: str) -> None:
        self._log(self.green(self.glyphs.step_submit), message)

    def warn(self, message: str) -> None:
        self._log(self.yellow(self.glyphs.warn), message)

    def error(self, message: str) -> None:
        self._log(self.red(self.glyphs.error), message)

    # -- note -------------------------------------------------------------

    def note_lines(self, message: str, title: str = "") -> list[str]:
        width = max(self._columns() - 10, 20)
        content: list[str] = []
        for raw in (message or "").splitlines() or [""]:
            content.extend(textwrap.wrap(raw, width=width, break_long_words=False, break_on_hyphens=False) or [""])
        title = title or ""
        box_width = max([len(line) for line in content] + [len(title)]) + 2
        g = self.glyphs
        lines = [self.gray(g.bar)]
        rule = g.hline * max(box_width - len(title) - 1, 1) + g.corner_top_right
        lines.append(f"{self.green(g.step_submit)}  {title} {self.gray(rule)}")
        for line in content:
            lines.append(
                f"{self.gray(g.bar)}  {self.dim(line)}"
                f"{' ' * (box_width - len(line))}{self.gray(g.bar)}"
            )
        lines.append(
            self.gray(g.connect_left + g.hline * (box_width + 2) + g.corner_bottom_right)
        )
        return lines

    def note(self, message: str, title: str = "") -> None:
        self._write("\n".join(self.note_lines(message, title)) + "\n")

    def _answer_lines(self, text: str, *, dim: bool = False) -> list[str]:
        width = max(self._columns() - 6, 12)
        chunks = textwrap.wrap(text, width=width, break_long_words=False, break_on_hyphens=False) or [""]
        out = []
        for chunk in chunks:
            body = self.dim(chunk) if dim else chunk
            out.append(f"{self.gray(self.glyphs.bar)}  {body}")
        return out

    def _clip(self, text: str, width: int) -> str:
        if width <= 3 or len(text) <= width:
            return text
        return text[: width - 3] + "..."

    # -- prompt frames ----------------------------------------------------

    def _columns(self) -> int:
        try:
            return shutil.get_terminal_size((80, 24)).columns
        except OSError:
            return 80

    def _header(self, message: str, symbol: str, bar: str) -> list[str]:
        width = max(self._columns() - 6, 20)
        wrapped = textwrap.wrap(message, width=width, break_long_words=False, break_on_hyphens=False) or [""]
        lines = [self.gray(self.glyphs.bar)]
        lines.append(f"{symbol}  {wrapped[0]}")
        for line in wrapped[1:]:
            lines.append(f"{bar}  {line}")
        return lines

    def select_lines(
        self, message: str, options: Sequence[Option], cursor: int
    ) -> list[str]:
        g = self.glyphs
        lines = self._header(
            message,
            self.cyan(g.step_active),
            self.cyan(g.bar),
        )
        start, end, above, below = self._window(len(options), cursor)
        prefix = f"{self.cyan(g.bar)}  "
        if above:
            lines.append(prefix + self.dim("..."))
        usable = max(self._columns() - 8, 12)
        for index in range(start, end):
            option = options[index]
            label_text = self._clip(option.label, usable)
            hint = (
                f" {self.dim('(' + self._clip(option.hint, usable - 4) + ')')}"
                if option.hint and index == cursor and not option.disabled
                else ""
            )
            if option.disabled:
                radio = self.gray(g.radio_inactive)
                label = self.gray(label_text)
            elif index == cursor:
                radio = self.green(g.radio_active)
                label = label_text
            else:
                radio = self.dim(g.radio_inactive)
                label = self.dim(label_text)
            lines.append(f"{prefix}{radio} {label}{hint}")
        if below:
            lines.append(prefix + self.dim("..."))
        lines.append(self.cyan(g.bar_end))
        return lines

    def select_submit_lines(
        self, message: str, options: Sequence[Option], cursor: int
    ) -> list[str]:
        g = self.glyphs
        lines = self._header(
            message,
            self.green(g.step_submit),
            self.gray(g.bar),
        )
        lines.extend(self._answer_lines(options[cursor].label, dim=True))
        return lines

    def select_cancel_lines(
        self, message: str, options: Sequence[Option], cursor: int
    ) -> list[str]:
        g = self.glyphs
        lines = self._header(
            message,
            self.red(g.step_cancel),
            self.red(g.bar),
        )
        lines.append(f"{self.gray(g.bar)}  {self.strike(self.dim(options[cursor].label))}")
        lines.append(self.gray(g.bar))
        return lines

    def multiselect_lines(
        self,
        message: str,
        options: Sequence[Option],
        cursor: int,
        selected: set[str],
        error: str | None = None,
    ) -> list[str]:
        g = self.glyphs
        lines = self._header(
            message,
            self.cyan(g.step_active),
            self.cyan(g.bar),
        )
        start, end, above, below = self._window(len(options), cursor)
        prefix = f"{self.cyan(g.bar)}  "
        if above:
            lines.append(prefix + self.dim("..."))
        usable = max(self._columns() - 8, 12)
        for index in range(start, end):
            option = options[index]
            active = index == cursor
            chosen = option.value in selected
            label_text = self._clip(option.label, usable)
            hint = (
                f" {self.dim('(' + self._clip(option.hint, usable - 4) + ')')}"
                if option.hint and active
                else ""
            )
            if option.disabled:
                box = self.gray(g.checkbox_inactive)
                label = self.gray(label_text)
            elif chosen and active:
                box = self.green(g.checkbox_selected)
                label = label_text
            elif chosen:
                box = self.green(g.checkbox_selected)
                label = self.dim(label_text)
            elif active:
                box = self.cyan(g.checkbox_active)
                label = label_text
            else:
                box = self.dim(g.checkbox_inactive)
                label = self.dim(label_text)
            lines.append(f"{prefix}{box} {label}{hint}")
        if below:
            lines.append(prefix + self.dim("..."))
        if error:
            lines.append(f"{self.yellow(g.bar_end)}  {self.yellow(error)}")
        else:
            lines.append(self.cyan(g.bar_end))
        return lines

    def multiselect_submit_lines(
        self,
        message: str,
        options: Sequence[Option],
        selected: Sequence[str],
    ) -> list[str]:
        g = self.glyphs
        lines = self._header(
            message,
            self.green(g.step_submit),
            self.gray(g.bar),
        )
        labels = [option.label for option in options if option.value in selected]
        lines.extend(self._answer_lines(", ".join(labels) if labels else "none", dim=True))
        return lines

    def multiselect_cancel_lines(
        self,
        message: str,
        options: Sequence[Option],
        selected: Sequence[str],
    ) -> list[str]:
        g = self.glyphs
        lines = self._header(
            message,
            self.red(g.step_cancel),
            self.red(g.bar),
        )
        labels = [
            self.strike(self.dim(option.label))
            for option in options
            if option.value in selected
        ]
        if labels:
            lines.append(f"{self.gray(g.bar)}  {self.dim(', ').join(labels)}")
            lines.append(self.gray(g.bar))
        return lines

    def confirm_lines(self, message: str, value: bool) -> list[str]:
        g = self.glyphs
        lines = self._header(
            message,
            self.cyan(g.step_active),
            self.cyan(g.bar),
        )
        prefix = f"{self.cyan(g.bar)}  "
        yes = f"{self.green(g.radio_active)} Yes" if value else f"{self.dim(g.radio_inactive)} {self.dim('Yes')}"
        no = (
            f"{self.dim(g.radio_inactive)} {self.dim('No')}"
            if value
            else f"{self.green(g.radio_active)} No"
        )
        lines.append(f"{prefix}{yes} {self.dim('/')} {no}")
        lines.append(self.cyan(g.bar_end))
        return lines

    def confirm_submit_lines(self, message: str, value: bool) -> list[str]:
        g = self.glyphs
        lines = self._header(
            message,
            self.green(g.step_submit),
            self.gray(g.bar),
        )
        lines.append(f"{self.gray(g.bar)}  {self.dim('Yes' if value else 'No')}")
        return lines

    def confirm_cancel_lines(self, message: str, value: bool) -> list[str]:
        g = self.glyphs
        lines = self._header(
            message,
            self.red(g.step_cancel),
            self.red(g.bar),
        )
        lines.append(
            f"{self.gray(g.bar)}  {self.strike(self.dim('Yes' if value else 'No'))}"
        )
        lines.append(self.gray(g.bar))
        return lines

    def text_lines(
        self,
        message: str,
        buffer: str,
        placeholder: str = "",
        error: str | None = None,
        hint: str | None = None,
    ) -> list[str]:
        g = self.glyphs
        lines = self._header(
            message,
            self.cyan(g.step_active),
            self.cyan(g.bar),
        )
        if buffer:
            width = max(self._columns() - 10, 12)
            shown = buffer if len(buffer) <= width else "..." + buffer[-(width - 3):]
        else:
            shown = self.dim(placeholder) if placeholder else ""
        lines.append(f"{self.cyan(g.bar)}  {shown}{self.dim('█')}")
        if hint:
            lines.append(
                f"{self.gray(g.bar)}  {self.dim(self._clip(hint, max(self._columns() - 8, 12)))}"
            )
        if error:
            lines.append(f"{self.yellow(g.bar_end)}  {self.yellow(error)}")
        else:
            lines.append(self.cyan(g.bar_end))
        return lines

    def text_submit_lines(self, message: str, value: str) -> list[str]:
        g = self.glyphs
        lines = self._header(
            message,
            self.green(g.step_submit),
            self.gray(g.bar),
        )
        if value:
            lines.extend(self._answer_lines(value, dim=True))
        else:
            lines.append(self.gray(g.bar))
        return lines

    def text_cancel_lines(self, message: str, value: str) -> list[str]:
        g = self.glyphs
        lines = self._header(
            message,
            self.red(g.step_cancel),
            self.red(g.bar),
        )
        if value:
            lines.append(f"{self.gray(g.bar)}  {self.strike(self.dim(value))}")
            lines.append(self.gray(g.bar))
        return lines

    # -- interactive prompts ---------------------------------------------

    def select(
        self,
        message: str,
        options: Sequence[Option],
        *,
        initial: int = 0,
    ) -> str:
        options = list(options)
        cursor = max(0, min(initial, len(options) - 1))
        if options[cursor].disabled:
            cursor = self._move(options, cursor, 1)
        with self._raw():
            try:
                while True:
                    self._paint(self.select_lines(message, options, cursor))
                    key = self._read_key()
                    if key == "up":
                        cursor = self._move(options, cursor, -1)
                    elif key == "down":
                        cursor = self._move(options, cursor, 1)
                    elif key == "enter":
                        self._finish(self.select_submit_lines(message, options, cursor))
                        return options[cursor].value
                    elif key in ("cancel", "eof", "escape"):
                        self._finish(self.select_cancel_lines(message, options, cursor))
                        raise UIAbort()
            except KeyboardInterrupt:
                self._finish(self.select_cancel_lines(message, options, cursor))
                raise UIAbort() from None
            finally:
                self._show_cursor()

    def multiselect(
        self,
        message: str,
        options: Sequence[Option],
        *,
        initial: Iterable[str] = (),
        required: bool = False,
    ) -> list[str]:
        options = list(options)
        available = [index for index, option in enumerate(options) if not option.disabled]
        enabled = {o.value for o in options if not o.disabled}
        selected = {value for value in initial if value in enabled}
        cursor = next(
            (index for index, option in enumerate(options) if option.value in selected),
            available[0] if available else 0,
        )
        error: str | None = None
        with self._raw():
            try:
                while True:
                    self._paint(self.multiselect_lines(message, options, cursor, selected, error))
                    key = self._read_key()
                    error = None
                    if key == "up":
                        cursor = self._move(options, cursor, -1)
                    elif key == "down":
                        cursor = self._move(options, cursor, 1)
                    elif key in ("space", "right", "left"):
                        option = options[cursor]
                        if not option.disabled:
                            if option.value in selected:
                                selected.remove(option.value)
                            else:
                                selected.add(option.value)
                    elif key == "enter":
                        if required and not selected:
                            error = "Please select at least one option."
                            continue
                        self._finish(
                            self.multiselect_submit_lines(
                                message,
                                options,
                                [option.value for option in options if option.value in selected],
                            )
                        )
                        return [option.value for option in options if option.value in selected]
                    elif key in ("cancel", "eof", "escape"):
                        self._finish(
                            self.multiselect_cancel_lines(
                                message,
                                options,
                                [option.value for option in options if option.value in selected],
                            )
                        )
                        raise UIAbort()
            except KeyboardInterrupt:
                self._finish(
                    self.multiselect_cancel_lines(
                        message,
                        options,
                        [option.value for option in options if option.value in selected],
                    )
                )
                raise UIAbort() from None
            finally:
                self._show_cursor()

    def confirm(self, message: str, *, initial: bool = True) -> bool:
        value = initial
        with self._raw():
            try:
                while True:
                    self._paint(self.confirm_lines(message, value))
                    key = self._read_key()
                    if key in ("left", "right", "space", "tab"):
                        value = not value
                    elif key == "y":
                        value = True
                    elif key == "n":
                        value = False
                    elif key == "enter":
                        self._finish(self.confirm_submit_lines(message, value))
                        return value
                    elif key in ("cancel", "eof", "escape"):
                        self._finish(self.confirm_cancel_lines(message, value))
                        raise UIAbort()
            except KeyboardInterrupt:
                self._finish(self.confirm_cancel_lines(message, value))
                raise UIAbort() from None
            finally:
                self._show_cursor()

    def text(
        self,
        message: str,
        *,
        default: str = "",
        initial: str = "",
        placeholder: str = "",
        validate: Callable[[str], str | None] | None = None,
        complete: Callable[[str], tuple[str | None, str | None]] | None = None,
    ) -> str:
        buffer = initial
        error: str | None = None
        hint: str | None = None
        with self._raw():
            try:
                while True:
                    self._paint(self.text_lines(message, buffer, placeholder, error, hint))
                    key = self._read_key()
                    error = None
                    if key == "tab" and complete is not None:
                        new_buffer, hint = complete(buffer)
                        if new_buffer is not None:
                            buffer = new_buffer
                        continue
                    hint = None
                    if key == "enter":
                        value = buffer or default
                        problem = validate(value) if validate else None
                        if problem:
                            error = problem
                            continue
                        self._finish(self.text_submit_lines(message, value))
                        return value
                    if key in ("backspace", "delete"):
                        buffer = buffer[:-1]
                    elif key == "space":
                        buffer += " "
                    elif key in ("cancel", "eof"):
                        self._finish(self.text_cancel_lines(message, buffer))
                        raise UIAbort()
                    elif key in ("escape", "clear"):
                        buffer = ""
                    elif key == "tab":
                        buffer += "\t"
                    elif len(key) == 1 and key.isprintable():
                        buffer += key
                    elif len(key) > 1:
                        buffer += "".join(ch for ch in key if ch.isprintable())
            except KeyboardInterrupt:
                self._finish(self.text_cancel_lines(message, buffer))
                raise UIAbort() from None
            finally:
                self._show_cursor()

    # -- internals --------------------------------------------------------

    def _window(self, count: int, cursor: int) -> tuple[int, int, bool, bool]:
        limit = self.max_items
        if count <= limit:
            return 0, count, False, False
        start = 0
        if cursor >= limit - 3:
            start = min(cursor - limit + 3, count - limit)
        start = max(start, 0)
        end = min(start + limit, count)
        return start, end, start > 0, end < count

    @staticmethod
    def _move(options: Sequence[Option], cursor: int, step: int) -> int:
        if not options:
            return 0
        if not any(not option.disabled for option in options):
            return cursor
        index = cursor
        for _ in range(len(options)):
            index = (index + step) % len(options)
            if not options[index].disabled:
                return index
        return cursor

    def _write(self, text: str) -> None:
        try:
            self.stream.write(text)
            self.stream.flush()
        except BrokenPipeError:
            raise

    def _paint(self, lines: Sequence[str]) -> None:
        if self._painted:
            self._write(f"\x1b[{self._painted}A\r\x1b[J")
        else:
            self._write("\x1b[?25l")
            self._cursor_hidden = True
        self._write("\n".join(lines) + "\n")
        self._painted = len(lines)

    def _finish(self, lines: Sequence[str]) -> None:
        self._paint(lines)
        self._painted = 0
        self._show_cursor()

    def _show_cursor(self) -> None:
        if self._cursor_hidden:
            self._write("\x1b[?25h")
            self._cursor_hidden = False

    def _raw(self):
        return _RawMode(self)

    def _read_key(self) -> str:
        if os.name == "nt":
            return _read_key_windows()
        fd = self.input.fileno()
        first = os.read(fd, 1)
        if not first:
            return "eof"
        if first == b"\x03":
            return "cancel"
        if first == b"\x04":
            return "eof"
        if first == b"\x15":
            return "clear"
        if first in (b"\r", b"\n"):
            return "enter"
        if first == b" ":
            return "space"
        if first in (b"\x7f", b"\x08"):
            return "backspace"
        if first == b"\t":
            return "tab"
        if first == b"\x1b":
            if not _select.select([fd], [], [], 0.05)[0]:
                return "escape"
            second = os.read(fd, 1)
            if second == b"O":
                third = os.read(fd, 1)
                return {b"A": "up", b"B": "down", b"C": "right", b"D": "left"}.get(
                    third, "escape"
                )
            if second == b"[":
                return _read_csi(fd)
            return "escape"
        # A printable character, possibly multi-byte UTF-8.
        needed = 0
        if first[0] & 0xE0 == 0xC0:
            needed = 1
        elif first[0] & 0xF0 == 0xE0:
            needed = 2
        elif first[0] & 0xF8 == 0xF0:
            needed = 3
        data = first + (os.read(fd, needed) if needed else b"")
        return data.decode("utf-8", "replace")


class _RawMode:
    def __init__(self, ui: Clack) -> None:
        self.ui = ui
        self.fd = None
        self.saved = None

    def __enter__(self):
        if os.name != "nt":
            import termios

            self.fd = self.ui.input.fileno()
            attrs = termios.tcgetattr(self.fd)
            # cbreak without ISIG: Ctrl-C arrives as a byte and is drawn
            # as a cancel frame, instead of depending on a controlling
            # terminal's foreground process group (which differs in
            # ssh/CI wrappers and inside test harnesses).
            attrs[3] &= ~(termios.ECHO | termios.ICANON | termios.ISIG)
            attrs[6][termios.VMIN] = 1
            attrs[6][termios.VTIME] = 0
            termios.tcsetattr(self.fd, termios.TCSADRAIN, attrs)
            self.saved = attrs
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.saved is not None:
            import termios

            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)


def complete_path(buffer: str) -> tuple[str | None, str | None]:
    """Tab-complete a directory path, preserving ``~`` and relative forms.

    Returns ``(new_buffer, hint)``: ``new_buffer`` is the completed text
    (``None`` when there is nothing to add), ``hint`` describes several
    matches or a miss. Only directories are offered because the prompt
    this serves (project directory) cannot use a file.
    """
    raw = buffer or ""
    if raw.endswith(("/", "\\")) and not raw.endswith("\\\\"):
        base_raw, prefix = raw, ""
    else:
        base_raw, prefix = os.path.split(raw)
        base_raw = base_raw + os.sep if base_raw.endswith(os.sep) else base_raw

    expanded = os.path.expanduser(raw)
    if raw and not raw.endswith(("/", "\\")) and os.path.isdir(expanded):
        return raw + "/", None

    base = os.path.expanduser(base_raw) if base_raw else os.getcwd()
    if not base:
        base = os.getcwd()
    try:
        names = [
            name
            for name in sorted(os.listdir(base))
            if name.startswith(prefix)
            and (prefix.startswith(".") or not name.startswith("."))
            and os.path.isdir(os.path.join(base, name))
        ]
    except OSError:
        return None, "no matching directory"

    if not names:
        return None, "no matching directory"

    def display(name: str) -> str:
        if base_raw and not base_raw.endswith(("/", "\\")):
            return f"{base_raw}/{name}"
        return f"{base_raw}{name}"

    if len(names) == 1:
        candidate = display(names[0])
        return candidate + "/", None

    common = os.path.commonprefix(names)
    if len(common) > len(prefix):
        return display(common), f"{len(names)} matches"
    shown = ", ".join(names[:6]) + ("..." if len(names) > 6 else "")
    return None, shown


def _read_csi(fd) -> str:
    """Read the rest of a CSI sequence (after ``ESC [``).

    Handles arrows plus bracketed paste (``ESC[200~``), which terminals
    keep enabled and which otherwise leaves ``200~`` in the input buffer
    and silently corrupts pasted paths.
    """
    tail = bytearray()
    while True:
        if not _select.select([fd], [], [], 0.05)[0]:
            return "escape"
        char = os.read(fd, 1)
        if not char:
            return "escape"
        tail += char
        if 0x40 <= char[0] <= 0x7E:
            break
        if len(tail) > 16:
            return "escape"
    text = tail.decode("ascii", "replace")
    mapping = {
        "A": "up",
        "B": "down",
        "C": "right",
        "D": "left",
        "H": "home",
        "F": "end",
        "3~": "delete",
        "1~": "home",
        "4~": "end",
        "7~": "home",
        "8~": "end",
    }
    if text == "200~":
        return _read_paste(fd)
    return mapping.get(text, "escape")


def _read_paste(fd) -> str:
    data = bytearray()
    while True:
        char = os.read(fd, 1)
        if not char:
            break
        data += char
        if data.endswith(b"\x1b[201~"):
            data = data[:-6]
            break
    return data.decode("utf-8", "replace").replace("\r", "").replace("\n", "")


def _read_key_windows() -> str:
    import msvcrt

    char = msvcrt.getwch()
    if char in ("\x00", "\xe0"):
        code = msvcrt.getwch()
        return {
            "H": "up",
            "P": "down",
            "K": "left",
            "M": "right",
        }.get(code, "escape")
    if char == "\r":
        return "enter"
    if char == " ":
        return "space"
    if char == "\x08":
        return "backspace"
    if char == "\x03":
        return "cancel"
    if char == "\x04":
        return "eof"
    if char == "\x15":
        return "clear"
    if char == "\x1b":
        return "escape"
    return char
