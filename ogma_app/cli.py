from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

from .boards import PROFILES, profile_for
from .controller import OgmaController
from .diagnostics import collect_diagnostics
from .groundstation import parse_groundstation_file, parse_groundstation_text, save_groundstation_bundle
from .health import evaluate_health
from .paths import OGMA_ROOT, RUNS_ROOT
from .probe import probe_stlink
from .snapshots import make_status_sample, make_status_snapshot, save_status_series, save_status_snapshot


def log(text: str) -> None:
    print(text)


def print_status(status: dict[str, Any]) -> None:
    for key, value in status.items():
        print(f"{key}: {value}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ogma-console")
    parser.add_argument("--stlink-serial", help="select one ST-Link when multiple probes are connected")
    parser.add_argument("--list", action="store_true", help="list board profiles")
    parser.add_argument("--probe", action="store_true", help="probe ST-Link/target with st-info")
    parser.add_argument("--doctor", nargs="?", const="all", metavar="BOARD", help="check tools/repos/build artifacts without hardware")
    parser.add_argument("--detect", action="store_true", help="detect board via known SRAM status blocks")
    parser.add_argument("--validate", nargs="?", const="detected", metavar="BOARD", help="probe/detect/read health and save a bench validation report")
    parser.add_argument("--build", metavar="BOARD", help="build board firmware")
    parser.add_argument("--flash", metavar="BOARD", help="flash board firmware")
    parser.add_argument("--flash-detected", action="store_true", help="detect connected board, then flash its default firmware env")
    parser.add_argument("--status", metavar="BOARD", help="read board status block")
    parser.add_argument("--health", metavar="BOARD", help="read status and evaluate board health")
    parser.add_argument("--poll-status", metavar="BOARD", help="poll status and save a time-series bundle")
    parser.add_argument("--teachtaire-test", choices=("flight", "lora_tx", "lora_rx"), help="flash Teachtaire test env and poll status/health")
    parser.add_argument("--lamh-servo-test", metavar="OUTPUT", type=int, help="command Lámh PWM output 1-4 and save status/health")
    parser.add_argument("--foinse-monitor", action="store_true", help="sample Foinse current sensors and save min/max/avg")
    parser.add_argument("--angles", default="0,45,90,135,180", help="comma-separated servo angles for --lamh-servo-test")
    parser.add_argument("--dwell", type=float, default=0.25, help="seconds to wait after each servo command")
    parser.add_argument("--skip-flash", action="store_true", help="do not flash before --teachtaire-test")
    parser.add_argument("--status-out", metavar="PATH", help="save --status result as JSON or CSV")
    parser.add_argument("--duration", type=float, default=30.0, help="poll duration seconds")
    parser.add_argument("--interval", type=float, default=0.5, help="poll interval seconds")
    parser.add_argument("--poll-out", help="output root for --poll-status")
    parser.add_argument("--import-telemetry", metavar="PATH", help="import mixed groundstation GPS + CAN telemetry text")
    parser.add_argument("--import-groundstation", metavar="PATH", help="import groundstation GPS telemetry text/JSONL")
    parser.add_argument("--groundstation-serial", metavar="DEVICE", help="capture groundstation telemetry from USB serial")
    parser.add_argument("--telemetry-serial", metavar="DEVICE", help="capture mixed GPS + CAN telemetry from USB serial")
    parser.add_argument("--baud", type=int, default=115200, help="serial baud for USB capture")
    parser.add_argument("--decode-can-log", metavar="PATH", help="decode a text/CANdump log using Payload Layouts.csv")
    parser.add_argument("--can-out", metavar="PATH", help="write --decode-can-log JSON output")
    parser.add_argument("--parse-croi-flash", metavar="PATH", help="parse a Croí FlashLogger binary dump")
    parser.add_argument("--read-croi-flash", action="store_true", help="read Croí external flash over SWD mailbox")
    parser.add_argument("--wipe-croi-flash", action="store_true", help="erase Croí external flash, then restore flight firmware")
    parser.add_argument("--bytes", type=int, help="limit bytes for --read-croi-flash")
    parser.add_argument(
        "--offset",
        type=lambda value: int(value, 0),
        default=0,
        help="start offset for --read-croi-flash; accepts decimal or 0x hex",
    )
    parser.add_argument("--env", help="PlatformIO environment")
    parser.add_argument("--no-gui", action="store_true", help="do not start GUI when no action is given")
    args = parser.parse_args(argv)

    controller = OgmaController(OGMA_ROOT, log, args.stlink_serial)
    try:
        if args.list:
            for profile in PROFILES:
                envs = ", ".join(profile.env_names()) or "no pio env"
                print(f"{profile.board_id:13} {profile.display_name:14} {envs}")
            return 0

        if args.probe:
            result = probe_stlink()
            for line in result.lines():
                print(line)
            return 0 if result.connected else 2

        if args.doctor:
            board = None if args.doctor == "all" else args.doctor
            report = collect_diagnostics(board)
            for line in report.lines():
                print(line)
            return 0 if report.ok else 1

        if args.detect:
            result = controller.detect()
            if result.profile is None:
                print("no board matched")
                print(result.reason)
                return 2
            print(f"{result.profile.board_id}: {result.profile.display_name} ({result.reason})")
            if result.identity is not None:
                print(f"identity_board_id: {result.identity.board_id}")
                print(f"firmware_version: {result.identity.firmware_version}")
                print(f"capabilities: {', '.join(result.identity.capability_names())}")
            if result.status:
                print_status(result.status)
            return 0

        if args.validate:
            from .validation import run_bench_validation

            expected = None if args.validate == "detected" else args.validate
            result = run_bench_validation(controller, expected, RUNS_ROOT / "validation")
            print_status(
                {
                    "ok": result.report["ok"],
                    "expected_board_id": result.report.get("expected_board_id"),
                    "detected_board_id": (result.report.get("detection") or {}).get("board_id"),
                    "errors": result.report["errors"],
                    "warnings": result.report["warnings"],
                }
            )
            print(f"validation: {result.out}")
            return 0 if result.report["ok"] else 1

        if args.build:
            profile = profile_for(args.build)
            env = args.env or profile.default_env
            if env is None:
                raise RuntimeError(f"{profile.board_id} has no default env")
            print(controller.build(profile.board_id, env))
            return 0

        if args.flash:
            profile = profile_for(args.flash)
            env = args.env or profile.default_env
            if env is None:
                raise RuntimeError(f"{profile.board_id} has no default env")
            controller.flash(profile.board_id, env)
            return 0

        if args.flash_detected:
            result = controller.flash_detected(args.env)
            profile = result.detection.profile
            assert profile is not None
            print(f"flashed: {profile.board_id} env={result.env} ({result.detection.reason})")
            return 0

        if args.status:
            profile = profile_for(args.status)
            env = args.env or profile.default_env
            status = controller.read_status(profile.board_id, env)
            print_status(status)
            if args.status_out:
                from pathlib import Path

                out = save_status_snapshot(make_status_snapshot(profile, env, status), Path(args.status_out))
                print(f"snapshot: {out}")
            return 0

        if args.health:
            profile = profile_for(args.health)
            env = args.env or profile.default_env
            status = controller.read_status(profile.board_id, env)
            report = evaluate_health(profile.board_id, status)
            for line in report.lines():
                print(line)
            return 0 if report.ok else 1

        if args.poll_status:
            from pathlib import Path

            profile = profile_for(args.poll_status)
            env = args.env or profile.default_env
            if env is None:
                raise RuntimeError(f"{profile.board_id} has no default env")
            samples: list[dict[str, Any]] = []
            start = time.monotonic()
            deadline = start + max(0.0, args.duration)
            while time.monotonic() <= deadline or not samples:
                elapsed = time.monotonic() - start
                status = controller.read_status(profile.board_id, env)
                samples.append(make_status_sample(elapsed, status))
                print(f"sample {len(samples)} elapsed={elapsed:.2f}s")
                if time.monotonic() >= deadline:
                    break
                time.sleep(max(0.05, args.interval))
            out_root = Path(args.poll_out) if args.poll_out else RUNS_ROOT / "status"
            out = save_status_series(profile, env, samples, out_root)
            print(f"series: {out}")
            return 0

        if args.teachtaire_test:
            from .board_tests import run_teachtaire_test

            result = run_teachtaire_test(
                controller,
                args.teachtaire_test,
                args.duration,
                args.interval,
                RUNS_ROOT / "board_tests",
                flash=not args.skip_flash,
            )
            print_status(result.summary)
            print(f"bundle: {result.out}")
            return 0

        if args.lamh_servo_test is not None:
            from .board_tests import parse_angle_list, run_lamh_servo_test

            result = run_lamh_servo_test(
                controller,
                args.lamh_servo_test,
                parse_angle_list(args.angles),
                args.dwell,
                RUNS_ROOT / "board_tests",
                env=args.env,
            )
            print_status(result.summary)
            print(f"bundle: {result.out}")
            return 0

        if args.foinse_monitor:
            from .board_tests import run_foinse_monitor

            result = run_foinse_monitor(
                controller,
                args.duration,
                args.interval,
                RUNS_ROOT / "board_tests",
                env=args.env,
            )
            print_status(result.summary)
            print(f"bundle: {result.out}")
            return 0

        if args.import_telemetry:
            from pathlib import Path

            from .telemetry import load_default_can_frames, parse_mixed_telemetry_file, save_mixed_telemetry_bundle

            source = Path(args.import_telemetry)
            parsed = parse_mixed_telemetry_file(source, load_default_can_frames())
            out = save_mixed_telemetry_bundle(parsed, source, RUNS_ROOT / "telemetry")
            print_status(parsed["summary"])
            print(f"bundle: {out}")
            return 0

        if args.import_groundstation:
            from pathlib import Path

            source = Path(args.import_groundstation)
            parsed = parse_groundstation_file(source)
            out = save_groundstation_bundle(parsed, source, RUNS_ROOT / "groundstation")
            print_status(parsed["summary"])
            print(f"bundle: {out}")
            return 0

        if args.groundstation_serial:
            from .serial_capture import capture_serial_text, serial_capture_summary

            capture = capture_serial_text(args.groundstation_serial, args.baud, args.duration)
            parsed = parse_groundstation_text(capture.text)
            parsed["summary"].update(serial_capture_summary(capture))
            source = f"serial:{capture.device}@{capture.baud}"
            out = save_groundstation_bundle(parsed, source, RUNS_ROOT / "groundstation", raw_text=capture.text)
            print_status(parsed["summary"])
            print(f"bundle: {out}")
            return 0

        if args.telemetry_serial:
            from .serial_capture import capture_serial_text, serial_capture_summary
            from .telemetry import load_default_can_frames, parse_mixed_telemetry_text, save_mixed_telemetry_bundle

            capture = capture_serial_text(args.telemetry_serial, args.baud, args.duration)
            parsed = parse_mixed_telemetry_text(capture.text, load_default_can_frames())
            parsed["summary"].update(serial_capture_summary(capture))
            source = f"serial:{capture.device}@{capture.baud}"
            out = save_mixed_telemetry_bundle(parsed, source, RUNS_ROOT / "telemetry", raw_text=capture.text)
            print_status(parsed["summary"])
            print(f"bundle: {out}")
            return 0

        if args.decode_can_log:
            from pathlib import Path

            from .can_decoder import decode_can_log_file, save_can_decode_bundle
            from .telemetry import load_default_can_frames

            source = Path(args.decode_can_log)
            decoded = decode_can_log_file(source, load_default_can_frames())
            print_status(decoded["summary"])
            if args.can_out:
                out = save_can_decode_bundle(decoded, source, Path(args.can_out))
                print(f"can_decode: {out}")
            else:
                print(json.dumps(decoded["frames"][:20], indent=2))
            return 0

        if args.parse_croi_flash:
            from pathlib import Path

            from .croi_flash import parse_croi_flash_dump, save_croi_flash_bundle

            source = Path(args.parse_croi_flash)
            parsed = parse_croi_flash_dump(source.read_bytes())
            out = save_croi_flash_bundle(parsed, source, RUNS_ROOT / "croi_flash")
            print_status(parsed["summary"])
            print(f"bundle: {out}")
            return 0

        if args.read_croi_flash:
            from .croi_flash import parse_croi_flash_dump, save_croi_flash_bundle

            dump = controller.read_croi_flash_dump(
                args.env,
                max_bytes=args.bytes,
                start_offset=args.offset,
            )
            suffix = "" if args.offset == 0 else f"_{args.offset:08x}"
            source = RUNS_ROOT / "croi_flash" / f"latest_swd_dump{suffix}.bin"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(dump)
            if args.offset != 0:
                print(f"raw_dump: {source}")
                print(f"offset: 0x{args.offset:08x}")
                print(f"bytes: {len(dump)}")
                return 0
            parsed = parse_croi_flash_dump(dump)
            out = save_croi_flash_bundle(parsed, source, RUNS_ROOT / "croi_flash")
            print_status(parsed["summary"])
            print(f"bundle: {out}")
            return 0

        if args.wipe_croi_flash:
            result = controller.wipe_croi_flash_restore()
            print(f"wiped: croi env={result.wipe_env}")
            print(f"restored: croi env={result.flight_env}")
            print_status(result.status)
            return 0

        if args.no_gui:
            parser.print_help()
            return 0

        from .ui import run

        run()
        return 0
    finally:
        controller.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
