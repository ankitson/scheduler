#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
"""Publish a directory to a pickup/landing zone for a downstream data pipeline.

Copies <src> into <dest>/<uuid>/ and then creates a `_READY` sentinel file. The
consumer should only pick up drops that contain the sentinel, so it never reads a
partial copy. Generic -- usable by any exporter job, not just playnite-export.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import uuid
from pathlib import Path


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        description="Copy <src> into <dest>/<uuid>/ and touch a _READY sentinel when done."
    )
    p.add_argument("--src", type=Path, required=True, help="Source directory to publish.")
    p.add_argument("--dest", type=Path, required=True, help="Landing root; a <uuid> subdir is created under it.")
    p.add_argument("--ready-file", default="_READY", help="Sentinel filename created after the copy (default: _READY).")
    args = p.parse_args(argv)

    src: Path = args.src
    if not src.is_dir():
        raise SystemExit(f"Source directory not found: {src}")

    args.dest.mkdir(parents=True, exist_ok=True)
    drop = args.dest / str(uuid.uuid4())

    try:
        shutil.copytree(src, drop)
        file_count = sum(1 for f in drop.rglob("*") if f.is_file())
        # Sentinel last: the pipeline keys off this, so it must only appear once the copy is complete.
        (drop / args.ready_file).touch()
    except Exception:
        # Never leave a partial drop without a sentinel for the pipeline to trip over.
        if drop.exists():
            shutil.rmtree(drop, ignore_errors=True)
        raise

    print(f"Published {src} -> {drop} ({file_count} files, sentinel: {args.ready_file})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
