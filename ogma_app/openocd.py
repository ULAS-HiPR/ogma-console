from __future__ import annotations

import re
import socket
import struct
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Iterable

from .paths import OPENOCD


def parse_words(text: str) -> list[int]:
    words: list[int] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("0x"):
            continue
        hex_words = re.findall(r"\b[0-9a-fA-F]{8}\b", stripped)
        if hex_words and stripped.startswith("0x" + hex_words[0]):
            hex_words = hex_words[1:]
        words.extend(int(word, 16) for word in hex_words)
    return words


def words_to_bytes(words: Iterable[int], wanted: int) -> bytes:
    data = bytearray()
    for word in words:
        data.extend(struct.pack("<I", word & 0xFFFFFFFF))
    return bytes(data[:wanted])


class OpenOCDSession:
    def __init__(
        self,
        cwd: Path,
        target_cfg: str,
        log: Callable[[str], None],
        adapter_serial: str | None = None,
    ) -> None:
        self.cwd = cwd
        self.target_cfg = target_cfg
        self.log = log
        self.adapter_serial = adapter_serial
        self.proc: subprocess.Popen[str] | None = None
        self.sock: socket.socket | None = None
        self.output: list[str] = []
        self.lock = threading.Lock()
        self.telnet_port, self.gdb_port, self.tcl_port = self._free_ports(3)

    @staticmethod
    def _free_ports(count: int) -> list[int]:
        sockets: list[socket.socket] = []
        try:
            ports: list[int] = []
            for _ in range(count):
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.bind(("127.0.0.1", 0))
                sockets.append(sock)
                ports.append(int(sock.getsockname()[1]))
            return ports
        finally:
            for sock in sockets:
                sock.close()

    def start(self) -> None:
        if self.sock is not None:
            return
        if not OPENOCD.exists():
            raise RuntimeError(f"OpenOCD not found: {OPENOCD}")

        cmd = [
            str(OPENOCD),
            "-f",
            "interface/stlink.cfg",
        ]
        if self.adapter_serial:
            cmd.extend(("-c", f"adapter serial {self.adapter_serial}"))
        cmd.extend(
            (
                "-f",
                self.target_cfg,
                "-c",
                f"telnet_port {self.telnet_port}",
                "-c",
                f"gdb_port {self.gdb_port}",
                "-c",
                f"tcl_port {self.tcl_port}",
                "-c",
                "init",
            )
        )
        self.log("$ " + " ".join(cmd))
        self.proc = subprocess.Popen(
            cmd,
            cwd=self.cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
        )
        threading.Thread(target=self._collect_output, daemon=True).start()

        deadline = time.time() + 8.0
        last_error = ""
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("OpenOCD exited: " + "\n".join(self.output[-12:]))
            try:
                self.sock = socket.create_connection(("127.0.0.1", self.tcl_port), timeout=0.5)
                self.sock.settimeout(4.0)
                self._raw_command("rbp all", timeout=3.0, tolerate_error=True)
                self._raw_command("resume", timeout=3.0, tolerate_error=True)
                self.log("OpenOCD connected")
                return
            except OSError as exc:
                last_error = str(exc)
                time.sleep(0.2)

        self.stop()
        raise RuntimeError(f"could not connect to OpenOCD TCL: {last_error}")

    def _collect_output(self) -> None:
        if self.proc is None or self.proc.stdout is None:
            return
        for line in self.proc.stdout:
            line = line.rstrip()
            self.output.append(line)
            if len(self.output) > 200:
                self.output = self.output[-100:]

    def _read_tcl_response(self, timeout: float = 4.0) -> str:
        assert self.sock is not None
        old_timeout = self.sock.gettimeout()
        self.sock.settimeout(timeout)
        chunks: list[bytes] = []
        try:
            while True:
                chunk = self.sock.recv(4096)
                if not chunk:
                    raise RuntimeError("OpenOCD socket closed")
                chunks.append(chunk)
                data = b"".join(chunks)
                if b"\x1a" in data:
                    return data.split(b"\x1a", 1)[0].decode("utf-8", errors="ignore")
        finally:
            self.sock.settimeout(old_timeout)

    def _raw_command(self, command: str, timeout: float = 8.0, tolerate_error: bool = False) -> str:
        assert self.sock is not None
        self.sock.settimeout(timeout)
        self.sock.sendall(command.encode("utf-8") + b"\x1a")
        text = self._read_tcl_response(timeout=timeout)
        if not tolerate_error and "Error:" in text:
            raise RuntimeError(text.strip())
        return text

    def command(self, command: str, timeout: float = 8.0, tolerate_error: bool = False) -> str:
        with self.lock:
            self.start()
            return self._raw_command(command, timeout=timeout, tolerate_error=tolerate_error)

    def read_words(self, address: int, count: int, timeout: float = 8.0) -> list[int]:
        if count <= 0:
            return []
        max_words_per_mdw = 8
        if count > max_words_per_mdw:
            words: list[int] = []
            current = address
            remaining = count
            while remaining > 0:
                chunk = min(max_words_per_mdw, remaining)
                words.extend(self.read_words(current, chunk, timeout=timeout))
                current += chunk * 4
                remaining -= chunk
            return words

        last_text = ""
        last_count = 0
        for _ in range(2):
            text = self.command(f"mdw 0x{address:08x} {count}", timeout=timeout)
            words = parse_words(text)
            if len(words) >= count:
                return words[:count]
            last_text = text
            last_count = len(words)
            time.sleep(0.05)
        raise RuntimeError(f"mdw returned {last_count} words, wanted {count}: {last_text}")

    def read_bytes(self, address: int, count: int, timeout: float = 8.0) -> bytes:
        word_count = (count + 3) // 4
        return words_to_bytes(self.read_words(address, word_count, timeout=timeout), count)

    def write_word(self, address: int, value: int, timeout: float = 8.0) -> None:
        self.command(f"mww 0x{address:08x} 0x{value & 0xFFFFFFFF:08x}", timeout=timeout)

    def halt(self) -> None:
        self.command("halt", timeout=5.0, tolerate_error=True)

    def resume(self) -> None:
        self.command("resume", timeout=5.0, tolerate_error=True)

    def stop(self) -> None:
        sock = self.sock
        self.sock = None
        if sock is not None:
            try:
                sock.sendall(b"shutdown\n")
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        if self.proc is not None:
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
            self.proc = None
