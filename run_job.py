#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
"""Generic job runner: runs a command with retries, rotating logs, and a status file.

Intended to be invoked by Windows Task Scheduler so that each scheduled script gets
consistent logging and retry behaviour without baking it into the script itself.

Usage:
    uv run run_job.py --name <job> [options] -- <command> [args...]
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path


def _build_logger(
    name: str, log_dir: Path, max_bytes: int, backups: int
) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"run_job.{name}")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    fh = RotatingFileHandler(
        log_dir / f"{name}.log",
        maxBytes=max_bytes,
        backupCount=backups,
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    return logger


def _log_block(logger: logging.Logger, text: str, *, level: int = logging.INFO) -> None:
    for line in text.splitlines():
        if line.strip():
            logger.log(level, "| %s", line.rstrip())


def _run_once(
    command: list[str], timeout: float | None, logger: logging.Logger
) -> tuple[int, float]:
    logger.info("exec: %s", subprocess.list2cmdline(command))
    start = time.monotonic()
    try:
        cp = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        logger.error("timed out after %ss", timeout)
        if e.stdout:
            _log_block(logger, e.stdout if isinstance(e.stdout, str) else e.stdout.decode("utf-8", "replace"))
        if e.stderr:
            _log_block(logger, e.stderr if isinstance(e.stderr, str) else e.stderr.decode("utf-8", "replace"), level=logging.WARNING)
        return 124, time.monotonic() - start

    if cp.stdout:
        _log_block(logger, cp.stdout)
    if cp.stderr:
        _log_block(logger, cp.stderr, level=logging.WARNING)
    return cp.returncode, time.monotonic() - start


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Run a command with retries, rotating logs, and a status file."
    )
    parser.add_argument("--name", required=True, help="Job name (used for log + status filenames).")
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "logs",
        help="Directory for <name>.log and <name>.status.json (default: script_dir/logs).",
    )
    parser.add_argument("--retries", type=int, default=2, help="Retries after the first attempt (default: 2 => up to 3 attempts).")
    parser.add_argument("--retry-delay", type=float, default=10.0, help="Base delay between attempts in seconds (default: 10).")
    parser.add_argument("--backoff", type=float, default=2.0, help="Multiplier applied to the delay after each failed attempt (default: 2.0).")
    parser.add_argument("--timeout", type=float, default=None, help="Per-attempt timeout in seconds (default: none).")
    parser.add_argument("--max-log-bytes", type=int, default=2_000_000, help="Rotate the log once it exceeds this size (default: 2MB).")
    parser.add_argument("--log-backups", type=int, default=5, help="Number of rotated log files to keep (default: 5).")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="The command to run, after `--`.")
    args = parser.parse_args(argv)

    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("no command given; pass it after `--`, e.g. run_job.py --name x -- uv run script.py")

    logger = _build_logger(args.name, args.log_dir, args.max_log_bytes, args.log_backups)
    max_attempts = max(1, args.retries + 1)
    logger.info("=== job '%s' starting (max attempts: %d) ===", args.name, max_attempts)

    start_unix = int(time.time())
    rc = 1
    attempts_used = 0
    delay = args.retry_delay
    for attempt in range(1, max_attempts + 1):
        attempts_used = attempt
        logger.info("--- attempt %d/%d ---", attempt, max_attempts)
        rc, dur = _run_once(command, args.timeout, logger)
        logger.info("attempt %d finished: exit=%d (%.1fs)", attempt, rc, dur)
        if rc == 0:
            break
        if attempt < max_attempts:
            logger.warning("attempt failed; retrying in %.0fs", delay)
            time.sleep(delay)
            delay *= args.backoff

    ok = rc == 0
    end_unix = int(time.time())
    logger.info(
        "=== job '%s' %s: exit=%d, attempts=%d, total=%ds ===",
        args.name,
        "OK" if ok else "FAILED",
        rc,
        attempts_used,
        end_unix - start_unix,
    )

    status = {
        "name": args.name,
        "success": ok,
        "exitCode": rc,
        "attempts": attempts_used,
        "maxAttempts": max_attempts,
        "startUnix": start_unix,
        "endUnix": end_unix,
        "durationSec": end_unix - start_unix,
        "lastRunIso": datetime.fromtimestamp(end_unix, tz=timezone.utc).isoformat(),
        "command": command,
    }
    status_path = args.log_dir / f"{args.name}.status.json"
    status_path.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")

    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
