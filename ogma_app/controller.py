from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .boards import PROFILES, PROFILES_BY_IDENTITY, BoardProfile, profile_for
from .identity import IDENTITY_SIZE, IDENTITY_SYMBOL, BoardIdentity
from .lamh_config import (
    LamhSafetyConfig,
    save_lamh_safety_record,
    write_lamh_safety_header,
)
from .mission_config import (
    LoggingPolicy,
    MissionConfig,
    RecoveryFallbackConfig,
    save_mission_flash_record,
    write_croi_mission_header,
)
from .flight_manifest import RadioPolicy
from .openocd import OpenOCDSession
from .paths import RUNS_ROOT
from .toolchain import build_env, flash_env, symbol_addresses
from .teachtaire_config import save_teachtaire_radio_record, write_teachtaire_radio_header
from .verification import (
    ConfigVerification,
    verify_croi_mission,
    verify_lamh_safety,
    verify_teachtaire_radio,
)

OGMA_FLASH_MAILBOX_MAGIC = 0x4F47464D
OGMA_FLASH_COMMAND_READ = 1
OGMA_FLASH_STATE_IDLE = 0
OGMA_FLASH_STATE_DONE = 2
OGMA_FLASH_STATE_ERROR = 3
OGMA_FLASH_RESULT_OK = 0
OGMA_FLASH_RESULT_LOCKED = 4
OGMA_FLASH_RESULT_UNSAFE_STATE = 5
OGMA_FLASH_HEADER_WORDS = 12
OGMA_FLASH_BUFFER_OFFSET = OGMA_FLASH_HEADER_WORDS * 4
OGMA_FLASH_CHUNK_BYTES = 512
OGMA_DEBUG_SYMBOL = "ogma_debug_control"
OGMA_DEBUG_MAGIC = 0x4F474442
OGMA_DEBUG_VERSION = 1
OGMA_DEBUG_UNLOCK_KEY = 0x0BEE11E5
OGMA_DEBUG_LEASE_MS = 60000
MIN_LAMH_DEBUG_LEASE_FIRMWARE = 20260708


@dataclass(frozen=True)
class DetectionResult:
    profile: BoardProfile | None
    status: dict[str, Any] | None
    reason: str
    identity: BoardIdentity | None = None


@dataclass(frozen=True)
class FlashDetectedResult:
    detection: DetectionResult
    env: str


@dataclass(frozen=True)
class CroiWipeRestoreResult:
    wipe_env: str
    flight_env: str
    status: dict[str, Any]


@dataclass(frozen=True)
class LamhSafetyFlashResult:
    config: LamhSafetyConfig
    env: str
    header_path: Path
    record_path: Path
    status: dict[str, Any]
    verification: ConfigVerification


@dataclass(frozen=True)
class CroiMissionFlashResult:
    config: MissionConfig
    recovery: RecoveryFallbackConfig
    logging: LoggingPolicy
    env: str
    header_path: Path
    record_path: Path
    status: dict[str, Any]
    verification: ConfigVerification


@dataclass(frozen=True)
class TeachtaireRadioFlashResult:
    config: RadioPolicy
    env: str
    header_path: Path
    record_path: Path
    status: dict[str, Any]
    verification: ConfigVerification


class OgmaController:
    def __init__(
        self,
        root: Path,
        log: Callable[[str], None],
        stlink_serial: str | None = None,
    ) -> None:
        self.root = root
        self.log = log
        self.stlink_serial = stlink_serial
        self.session: OpenOCDSession | None = None
        self.session_key: tuple[Path, str, str | None] | None = None
        self.servo_request_seq = 1
        self.debug_request_seq = 1

    def close(self) -> None:
        if self.session is not None:
            self.session.stop()
            self.session = None
            self.session_key = None

    def _session_for(self, profile: BoardProfile) -> OpenOCDSession:
        if profile.firmware_dir is None:
            raise RuntimeError(f"{profile.display_name} has no firmware dir")
        key = (profile.firmware_dir, profile.target_cfg, self.stlink_serial)
        if self.session is not None and self.session_key != key:
            self.close()
        if self.session is None:
            self.session = OpenOCDSession(
                profile.firmware_dir,
                profile.target_cfg,
                self.log,
                self.stlink_serial,
            )
            self.session_key = key
        self.session.start()
        return self.session

    def build(self, board_id: str, env: str) -> Path:
        profile = profile_for(board_id)
        if profile.firmware_dir is None:
            raise RuntimeError(f"{profile.display_name} has no firmware dir")
        return build_env(profile.firmware_dir, env, self.log)

    def flash(self, board_id: str, env: str) -> None:
        profile = profile_for(board_id)
        if profile.firmware_dir is None:
            raise RuntimeError(f"{profile.display_name} has no firmware dir")
        self.close()
        flash_env(
            profile.firmware_dir,
            env,
            self.log,
            self.stlink_serial,
            profile.target_cfg,
        )

    def flash_detected(self, env: str | None = None) -> FlashDetectedResult:
        detection = self.detect(enrich_status=False)
        if detection.profile is None:
            raise RuntimeError(f"could not detect board: {detection.reason}")
        selected_env = env or detection.profile.default_env
        if selected_env is None:
            raise RuntimeError(f"{detection.profile.display_name} has no default env")
        self.flash(detection.profile.board_id, selected_env)
        return FlashDetectedResult(detection, selected_env)

    def wipe_croi_flash_restore(
        self,
        wipe_env: str = "stm32f072c8t6_flash_wipe",
        flight_env: str = "stm32f072c8t6",
        timeout_s: float = 2400.0,
    ) -> CroiWipeRestoreResult:
        profile = profile_for("croi")
        if profile.firmware_dir is None:
            raise RuntimeError("croi has no firmware dir")
        self.log(f"croi wipe: building restore image env={flight_env}")
        self.build("croi", flight_env)

        latest_status: dict[str, Any] | None = None
        wipe_error: Exception | None = None
        try:
            self.log(f"croi wipe: flashing erase image env={wipe_env}")
            self.flash("croi", wipe_env)
            self.log("croi wipe: waiting for erase image to report clean logger")
            deadline = time.time() + timeout_s
            last_progress_bucket: int | None = None
            last_wait_log = 0.0
            while time.time() < deadline:
                try:
                    latest_status = self._read_profile_status(profile, wipe_env, build_if_missing=False)
                    if self._croi_wipe_complete(latest_status):
                        self.log("croi wipe: erase image reports clean logger")
                        break
                    version = int(latest_status.get("version", 0))
                    progress = int(latest_status.get("flash_wipe_progress_percent", 0))
                    now = time.time()
                    if version >= 3 and 0 <= progress <= 100:
                        bucket = min(100, (progress // 5) * 5)
                        if bucket != last_progress_bucket:
                            last_progress_bucket = bucket
                            self.log(
                                "croi wipe progress: "
                                f"{progress}% "
                                f"state={latest_status.get('flash_wipe_state')} "
                                f"addr=0x{int(latest_status.get('flash_wipe_address', 0)):08x}"
                            )
                    elif now - last_wait_log >= 10.0:
                        last_wait_log = now
                        self.log(
                            "croi wipe waiting: "
                            f"init={latest_status.get('flash_init_ok')} "
                            f"fault={latest_status.get('logger_fault')} "
                            f"status={latest_status.get('logger_flash_status')}"
                        )
                except Exception as exc:
                    self.log(f"croi wipe waiting: {exc}")
                    self.close()
                time.sleep(2.0)
            else:
                raise RuntimeError("croi wipe image did not report clean logger before timeout")
        except Exception as exc:
            wipe_error = exc
        finally:
            self.close()

        self.log(f"croi wipe: restoring flight image env={flight_env}")
        try:
            self.flash("croi", flight_env)
        except Exception as restore_error:
            if wipe_error is not None:
                raise RuntimeError(
                    f"croi wipe failed and flight restore failed: {restore_error}"
                ) from restore_error
            raise

        if wipe_error is not None:
            raise RuntimeError("croi wipe failed; flight image restored") from wipe_error
        if latest_status is None:
            raise RuntimeError("croi wipe completed without a status report; flight image restored")
        return CroiWipeRestoreResult(wipe_env, flight_env, latest_status)

    @staticmethod
    def _croi_wipe_complete(status: dict[str, Any]) -> bool:
        healthy = (
            int(status.get("flash_init_ok", 0)) == 1
            and int(status.get("logger_fault_latched", 1)) == 0
            and int(status.get("logger_flash_status", 1)) == 0
        )
        if not healthy:
            return False
        if int(status.get("version", 0)) >= 3:
            return (
                int(status.get("flash_wipe_state", 0)) == 2
                and int(status.get("flash_wipe_progress_percent", 0)) == 100
            )
        return True

    def read_status(self, board_id: str, env: str | None = None) -> dict[str, Any]:
        profile = profile_for(board_id)
        return self._read_profile_status(profile, env, build_if_missing=True)

    def _next_debug_seq(self) -> int:
        seq = self.debug_request_seq
        self.debug_request_seq = (self.debug_request_seq + 1) & 0xFFFFFFFF
        if self.debug_request_seq == 0:
            self.debug_request_seq = 1
        return seq

    def _write_debug_lease(
        self,
        session: OpenOCDSession,
        control_base: int,
        reason: str,
        *,
        log: bool = True,
    ) -> None:
        seq = self._next_debug_seq()
        session.write_word(control_base + 0, OGMA_DEBUG_MAGIC)
        session.write_word(control_base + 4, OGMA_DEBUG_VERSION)
        session.write_word(control_base + 12, OGMA_DEBUG_UNLOCK_KEY)
        session.write_word(control_base + 16, OGMA_DEBUG_LEASE_MS)
        session.write_word(control_base + 8, seq)
        if log:
            self.log(f"bench lease: {reason} seq={seq} ttl={OGMA_DEBUG_LEASE_MS}ms")

    def _read_profile_status(
        self,
        profile: BoardProfile,
        env: str | None = None,
        build_if_missing: bool = True,
    ) -> dict[str, Any]:
        if profile.status_block is None:
            raise RuntimeError(f"{profile.display_name} has no SRAM status block profile")
        if profile.firmware_dir is None:
            raise RuntimeError(f"{profile.display_name} has no firmware dir")
        env = env or profile.default_env
        if env is None:
            raise RuntimeError(f"{profile.display_name} has no default env")
        symbols = symbol_addresses(
            profile.firmware_dir,
            env,
            {profile.status_block.symbol},
            self.log,
            build_if_missing=build_if_missing,
        )
        address = symbols[profile.status_block.symbol]
        for attempt in range(2):
            try:
                session = self._session_for(profile)
                data = session.read_bytes(address, profile.status_block.size)
                return profile.status_block.parse(data)
            except Exception as exc:
                self.close()
                if attempt == 1:
                    raise
                self.log(
                    f"{profile.display_name} status read failed; reconnecting once: {exc}"
                )
                time.sleep(0.1)
        raise RuntimeError(f"{profile.display_name} status read failed")

    def detect(self, enrich_status: bool = True) -> DetectionResult:
        reasons: list[str] = []
        identity = self.detect_identity()
        if identity is not None:
            profile = PROFILES_BY_IDENTITY.get(identity.board_id)
            if profile is not None:
                if enrich_status and profile.can_read_status():
                    try:
                        status = self._read_profile_status(profile, profile.default_env, build_if_missing=False)
                        return DetectionResult(
                            profile,
                            status,
                            "matched ogma_board_identity + status block",
                            identity,
                        )
                    except Exception as exc:
                        return DetectionResult(
                            profile,
                            None,
                            f"matched ogma_board_identity; status unread: {exc}",
                            identity,
                        )
                return DetectionResult(
                    profile,
                    None,
                    "matched ogma_board_identity",
                    identity,
                )
            reasons.append(f"unknown identity board_id={identity.board_id}")

        for profile in PROFILES:
            block = profile.status_block
            if block is None or profile.firmware_dir is None or profile.default_env is None:
                continue
            try:
                status = self._read_profile_status(profile, profile.default_env, build_if_missing=False)
                return DetectionResult(profile, status, f"matched {block.symbol}")
            except Exception as exc:
                reasons.append(f"{profile.board_id}: {exc}")
        return DetectionResult(None, None, "; ".join(reasons) if reasons else "no readable profiles")

    def detect_identity(self) -> BoardIdentity | None:
        reasons: list[str] = []
        for profile in PROFILES:
            if profile.firmware_dir is None or profile.default_env is None:
                continue
            try:
                symbols = symbol_addresses(
                    profile.firmware_dir,
                    profile.default_env,
                    {IDENTITY_SYMBOL},
                    self.log,
                    build_if_missing=False,
                )
                session = self._session_for(profile)
                data = session.read_bytes(symbols[IDENTITY_SYMBOL], IDENTITY_SIZE)
                return BoardIdentity.parse(data)
            except Exception as exc:
                reasons.append(f"{profile.board_id}: {exc}")
        self.log("identity detect fallback: " + "; ".join(reasons))
        return None

    def _read_profile_identity(self, profile: BoardProfile, env: str) -> BoardIdentity:
        if profile.firmware_dir is None:
            raise RuntimeError(f"{profile.display_name} has no firmware dir")
        symbols = symbol_addresses(
            profile.firmware_dir,
            env,
            {IDENTITY_SYMBOL},
            self.log,
            build_if_missing=False,
        )
        session = self._session_for(profile)
        data = session.read_bytes(symbols[IDENTITY_SYMBOL], IDENTITY_SIZE)
        return BoardIdentity.parse(data)

    def send_lamh_servo_command(self, channel: int, angle_deg: int, env: str | None = None) -> None:
        profile = profile_for("lamh")
        if profile.firmware_dir is None:
            raise RuntimeError("lamh has no firmware dir")
        env = env or profile.default_env
        if env is None:
            raise RuntimeError("lamh has no default env")
        identity = self._read_profile_identity(profile, env)
        if identity.firmware_version < MIN_LAMH_DEBUG_LEASE_FIRMWARE:
            raise RuntimeError(
                "lamh servo command blocked: firmware lacks bench-lease guard; flash latest Lámh first"
            )
        symbol = "ogma_servo_command"
        symbols = symbol_addresses(profile.firmware_dir, env, {symbol, OGMA_DEBUG_SYMBOL}, self.log, build_if_missing=False)
        base = symbols[symbol]
        session = self._session_for(profile)
        self._write_debug_lease(session, symbols[OGMA_DEBUG_SYMBOL], "lamh servo command")
        seq = self.servo_request_seq
        self.servo_request_seq = (self.servo_request_seq + 1) & 0xFFFFFFFF
        if self.servo_request_seq == 0:
            self.servo_request_seq = 1
        session.write_word(base + 0, 0x4F475356)
        session.write_word(base + 4, seq)
        session.write_word(base + 8, channel & 0xFFFFFFFF)
        session.write_word(base + 12, angle_deg & 0xFFFFFFFF)
        deadline = time.time() + 1.0
        while time.time() < deadline:
            ack_seq, result = session.read_words(base + 16, 2, timeout=3.0)
            if ack_seq == seq:
                if result != 0:
                    if result == 4:
                        raise RuntimeError("lamh servo command blocked: Croí heartbeat is active")
                    raise RuntimeError(f"lamh servo command failed result={result}")
                self.log(f"lamh servo command applied: seq={seq} channel={channel} angle={angle_deg}")
                return
            time.sleep(0.02)
        self.log(f"lamh servo command written: seq={seq} channel={channel} angle={angle_deg}; ack pending")

    def flash_lamh_safety_config(
        self,
        config: LamhSafetyConfig,
        env: str | None = None,
    ) -> LamhSafetyFlashResult:
        profile = profile_for("lamh")
        if profile.firmware_dir is None or profile.default_env is None:
            raise RuntimeError("lamh flight firmware is unavailable")
        selected_env = env or profile.default_env
        if selected_env != profile.default_env:
            raise RuntimeError("Lámh safety angles can only be flashed with the flight firmware")

        detection = self.detect(enrich_status=False)
        if detection.profile is None:
            raise RuntimeError(f"Lámh safety flash blocked: could not identify connected board: {detection.reason}")
        if detection.profile.board_id != "lamh":
            raise RuntimeError(
                f"Lámh safety flash blocked: connected board is {detection.profile.display_name}"
            )

        header_path = profile.firmware_dir / "include" / "lamh_safety_config.h"
        header_path = write_lamh_safety_header(header_path, config)
        self.log(
            "lamh safety config saved: "
            + ", ".join(
                f"PWM{index + 1}={angle}deg" for index, angle in enumerate(config.angles_deg)
            )
        )

        stage = "build"
        try:
            self.build("lamh", selected_env)
            stage = "flash"
            self.flash("lamh", selected_env)
            stage = "verify"
            status = self.read_status("lamh", selected_env)
        except Exception:
            if stage == "build":
                self.log("lamh safety config build failed; board firmware was not changed")
            elif stage == "flash":
                self.log("lamh safety config built; flash did not complete, board configuration is unconfirmed")
            else:
                self.log("lamh safety image flashed; SWD readback did not complete")
            raise

        verification = verify_lamh_safety(config, status)
        verification.require_ok()

        record_path = save_lamh_safety_record(
            RUNS_ROOT / "lamh_safety",
            config,
            selected_env,
            header_path,
            status,
        )
        self.log(f"lamh safety config verified; audit record: {record_path}")
        return LamhSafetyFlashResult(
            config, selected_env, header_path, record_path, status, verification
        )

    def flash_croi_mission_config(
        self,
        config: MissionConfig,
        env: str | None = None,
        recovery: RecoveryFallbackConfig | None = None,
        logging: LoggingPolicy | None = None,
    ) -> CroiMissionFlashResult:
        recovery = recovery or RecoveryFallbackConfig()
        logging = logging or LoggingPolicy()
        profile = profile_for("croi")
        if profile.firmware_dir is None or profile.default_env is None:
            raise RuntimeError("croi flight firmware is unavailable")
        selected_env = env or profile.default_env
        if selected_env != profile.default_env:
            raise RuntimeError("Croí mission config can only be flashed with the flight firmware")

        detection = self.detect(enrich_status=False)
        if detection.profile is None:
            raise RuntimeError(f"Croí mission flash blocked: could not identify connected board: {detection.reason}")
        if detection.profile.board_id != "croi":
            raise RuntimeError(
                f"Croí mission flash blocked: connected board is {detection.profile.display_name}"
            )

        header_path = write_croi_mission_header(
            profile.firmware_dir / "include" / "croi_mission_config.h",
            config,
            recovery,
            logging,
        )
        self.log(f"croi mission config saved: crc=0x{config.crc32(recovery, logging):08x}")

        stage = "build"
        try:
            self.build("croi", selected_env)
            stage = "flash"
            self.flash("croi", selected_env)
            stage = "verify"
            status = self.read_status("croi", selected_env)
        except Exception:
            if stage == "build":
                self.log("croi mission build failed; board firmware was not changed")
            elif stage == "flash":
                self.log("croi mission built; flash did not complete, board mission is unconfirmed")
            else:
                self.log("croi mission image flashed; SWD readback did not complete")
            raise

        verification = verify_croi_mission(config, status, recovery, logging)
        verification.require_ok()

        record_path = save_mission_flash_record(
            RUNS_ROOT / "missions",
            config,
            selected_env,
            header_path,
            status,
            recovery,
            logging,
        )
        self.log(f"croi mission config verified; audit record: {record_path}")
        return CroiMissionFlashResult(
            config, recovery, logging, selected_env, header_path, record_path, status, verification
        )

    def flash_teachtaire_radio_config(
        self,
        config: RadioPolicy,
        env: str | None = None,
    ) -> TeachtaireRadioFlashResult:
        config.validate()
        profile = profile_for("teachtaire")
        if profile.firmware_dir is None or profile.default_env is None:
            raise RuntimeError("Teachtaire flight firmware is unavailable")
        selected_env = env or profile.default_env
        if selected_env != profile.default_env:
            raise RuntimeError("radio config can only be flashed with Teachtaire flight firmware")

        detection = self.detect(enrich_status=False)
        if detection.profile is None:
            raise RuntimeError(
                f"Teachtaire radio flash blocked: could not identify connected board: {detection.reason}"
            )
        if detection.profile.board_id != "teachtaire":
            raise RuntimeError(
                f"Teachtaire radio flash blocked: connected board is {detection.profile.display_name}"
            )

        header_path = write_teachtaire_radio_header(
            profile.firmware_dir / "include" / "teachtaire_radio_config.h",
            config,
        )
        stage = "build"
        try:
            self.build("teachtaire", selected_env)
            stage = "flash"
            self.flash("teachtaire", selected_env)
            stage = "verify"
            status = self.read_status("teachtaire", selected_env)
        except Exception:
            if stage == "build":
                self.log("Teachtaire radio build failed; board firmware was not changed")
            elif stage == "flash":
                self.log("Teachtaire radio image built; flash did not complete")
            else:
                self.log("Teachtaire radio image flashed; SWD readback did not complete")
            raise

        verification = verify_teachtaire_radio(config, status)
        verification.require_ok()
        record_path = save_teachtaire_radio_record(
            RUNS_ROOT / "teachtaire_radio",
            config,
            selected_env,
            header_path,
            status,
        )
        self.log(f"Teachtaire radio config verified; audit record: {record_path}")
        return TeachtaireRadioFlashResult(
            config, selected_env, header_path, record_path, status, verification
        )

    def read_croi_flash_dump(
        self,
        env: str | None = None,
        max_bytes: int | None = None,
        start_offset: int = 0,
    ) -> bytes:
        profile = profile_for("croi")
        if profile.firmware_dir is None:
            raise RuntimeError("croi has no firmware dir")
        env = env or profile.default_env
        if env is None:
            raise RuntimeError("croi has no default env")
        status = self._read_profile_status(profile, env, build_if_missing=False)
        if int(status.get("version", 0)) < 3:
            raise RuntimeError(
                "croi flash read blocked: firmware status is older than v3 bench-lease guard; flash latest Croí first"
            )
        symbol = "ogma_flash_mailbox"
        symbols = symbol_addresses(profile.firmware_dir, env, {symbol, OGMA_DEBUG_SYMBOL}, self.log, build_if_missing=False)
        base = symbols[symbol]
        session = self._session_for(profile)
        debug_base = symbols[OGMA_DEBUG_SYMBOL]

        header = session.read_words(base, OGMA_FLASH_HEADER_WORDS)
        if header[0] != OGMA_FLASH_MAILBOX_MAGIC:
            raise RuntimeError(f"croi flash mailbox not ready: 0x{header[0]:08x}")
        log_start = header[10]
        log_length = header[11]
        flash_capacity = 0x01000000
        if start_offset < 0 or start_offset >= flash_capacity:
            raise ValueError(f"croi flash offset outside device: 0x{start_offset:x}")
        available = flash_capacity - start_offset
        if max_bytes is None:
            read_total = min(max(0, log_length - start_offset), available)
        else:
            if max_bytes < 0:
                raise ValueError("croi flash byte count must be non-negative")
            read_total = min(max_bytes, available)
        read_start = log_start + start_offset
        self.log(
            f"croi flash mailbox: start=0x{read_start:08x} "
            f"reported={log_length} reading={read_total}"
        )
        data = bytearray()
        if read_total == 0:
            return bytes(data)
        seq = 1
        address = read_start
        self._write_debug_lease(session, debug_base, "croi flash read")
        while len(data) < read_total:
            length = min(OGMA_FLASH_CHUNK_BYTES, read_total - len(data))
            self._write_debug_lease(session, debug_base, "croi flash read", log=False)
            session.write_word(base + 12, OGMA_FLASH_STATE_IDLE)
            session.write_word(base + 32, 0)
            session.write_word(base + 36, 0)
            session.write_word(base + 24, address)
            session.write_word(base + 28, length)
            session.write_word(base + 16, seq)
            session.write_word(base + 8, OGMA_FLASH_COMMAND_READ)

            deadline = time.time() + 5.0
            words: list[int] | None = None
            while time.time() < deadline:
                words = session.read_words(base, OGMA_FLASH_HEADER_WORDS)
                if words[5] == seq and words[3] in (OGMA_FLASH_STATE_DONE, OGMA_FLASH_STATE_ERROR):
                    break
                time.sleep(0.02)
            else:
                raise RuntimeError(f"croi flash mailbox timeout seq={seq}")

            assert words is not None
            if words[3] != OGMA_FLASH_STATE_DONE or words[9] != OGMA_FLASH_RESULT_OK:
                if words[9] == OGMA_FLASH_RESULT_LOCKED:
                    raise RuntimeError("croi flash mailbox locked: bench lease was not accepted")
                if words[9] == OGMA_FLASH_RESULT_UNSAFE_STATE:
                    raise RuntimeError("croi flash read blocked: flight state is active")
                raise RuntimeError(f"croi flash mailbox failed seq={seq} state={words[3]} result={words[9]}")
            bytes_read = words[8]
            if bytes_read == 0:
                break
            data.extend(session.read_bytes(base + OGMA_FLASH_BUFFER_OFFSET, bytes_read))
            address += bytes_read
            seq = (seq + 1) & 0xFFFFFFFF
            if seq == 0:
                seq = 1
            if len(data) % (64 * 1024) == 0:
                self.log(f"croi flash read {len(data)}/{read_total} bytes")
        return bytes(data)
