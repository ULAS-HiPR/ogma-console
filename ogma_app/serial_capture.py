from __future__ import annotations

import os
import select
import termios
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


DEFAULT_BAUD = 115200
DEFAULT_MAX_BYTES = 1024 * 1024


@dataclass(frozen=True)
class FdCaptureResult:
    text: str
    bytes_read: int
    duration_s: float


@dataclass(frozen=True)
class SerialCaptureResult:
    device: str
    baud: int
    text: str
    bytes_read: int
    duration_s: float


def capture_serial_text(
    device: str,
    baud: int = DEFAULT_BAUD,
    duration_s: float = 30.0,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> SerialCaptureResult:
    fd = os.open(device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    old_attrs = None
    try:
        old_attrs = _configure_serial_fd(fd, baud)
        result = read_fd_text(fd, duration_s, max_bytes=max_bytes)
        return SerialCaptureResult(
            device=device,
            baud=baud,
            text=result.text,
            bytes_read=result.bytes_read,
            duration_s=result.duration_s,
        )
    finally:
        if old_attrs is not None:
            termios.tcsetattr(fd, termios.TCSANOW, old_attrs)
        os.close(fd)


def stream_serial_text(
    device: str,
    stop_event: threading.Event,
    on_text: Callable[[str], None],
    baud: int = DEFAULT_BAUD,
    raw_path: Path | None = None,
) -> SerialCaptureResult:
    """Read one serial session until stopped, forwarding decoded chunks."""
    fd = os.open(device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    old_attrs = None
    start = time.monotonic()
    chunks: list[bytes] = []
    bytes_read = 0
    raw_handle = None
    try:
        old_attrs = _configure_serial_fd(fd, baud)
        if raw_path is not None:
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_handle = raw_path.open("wb")
        while not stop_event.is_set():
            readable, _, _ = select.select([fd], [], [], 0.1)
            if not readable:
                continue
            try:
                chunk = os.read(fd, 4096)
            except BlockingIOError:
                continue
            if not chunk:
                break
            chunks.append(chunk)
            bytes_read += len(chunk)
            if raw_handle is not None:
                raw_handle.write(chunk)
                raw_handle.flush()
            on_text(chunk.decode("utf-8", errors="replace"))
        return SerialCaptureResult(
            device=device,
            baud=baud,
            text=b"".join(chunks).decode("utf-8", errors="replace"),
            bytes_read=bytes_read,
            duration_s=time.monotonic() - start,
        )
    finally:
        if raw_handle is not None:
            raw_handle.close()
        if old_attrs is not None:
            termios.tcsetattr(fd, termios.TCSANOW, old_attrs)
        os.close(fd)


def read_fd_text(fd: int, duration_s: float, max_bytes: int = DEFAULT_MAX_BYTES) -> FdCaptureResult:
    start = time.monotonic()
    deadline = start + max(0.0, duration_s)
    chunks: list[bytes] = []
    bytes_read = 0
    while time.monotonic() < deadline and bytes_read < max_bytes:
        timeout = min(0.1, max(0.0, deadline - time.monotonic()))
        readable, _, _ = select.select([fd], [], [], timeout)
        if not readable:
            continue
        try:
            chunk = os.read(fd, min(4096, max_bytes - bytes_read))
        except BlockingIOError:
            continue
        if not chunk:
            break
        chunks.append(chunk)
        bytes_read += len(chunk)
    text = b"".join(chunks).decode("utf-8", errors="replace")
    return FdCaptureResult(text=text, bytes_read=bytes_read, duration_s=time.monotonic() - start)


def serial_capture_summary(result: SerialCaptureResult) -> dict[str, int | float | str]:
    return {
        "capture_device": result.device,
        "capture_baud": result.baud,
        "capture_bytes": result.bytes_read,
        "capture_duration_s": round(result.duration_s, 3),
    }


def _configure_serial_fd(fd: int, baud: int) -> list[Any]:
    speed = _baud_to_termios(baud)
    old_attrs = termios.tcgetattr(fd)
    attrs = termios.tcgetattr(fd)
    attrs[0] = 0
    attrs[1] = 0
    attrs[2] = _clear_flag(attrs[2], termios.CSIZE | termios.PARENB | termios.CSTOPB)
    attrs[2] = _set_flag(attrs[2], termios.CS8 | termios.CREAD | termios.CLOCAL)
    if hasattr(termios, "CRTSCTS"):
        attrs[2] = _clear_flag(attrs[2], termios.CRTSCTS)
    attrs[3] = 0
    attrs[4] = speed
    attrs[5] = speed
    attrs[6][termios.VMIN] = 0
    attrs[6][termios.VTIME] = 1
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    termios.tcflush(fd, termios.TCIFLUSH)
    return old_attrs


def _baud_to_termios(baud: int) -> int:
    name = f"B{baud}"
    if not hasattr(termios, name):
        raise ValueError(f"unsupported baud: {baud}")
    return int(getattr(termios, name))


def _clear_flag(value: int, flag: int) -> int:
    return value & ~flag


def _set_flag(value: int, flag: int) -> int:
    return value | flag
