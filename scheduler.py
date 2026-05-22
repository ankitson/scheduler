#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["tomli-w"]
# ///
"""Config-driven Windows job scheduler.

Reads jobs.toml and runs / schedules each job via run_job.py (logging + retries)
and register_task.ps1 (Task Scheduler). Consuming projects need know nothing about
this tool -- a project is simply one entry in jobs.toml.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import tomli_w

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG = SCRIPT_DIR / "jobs.toml"
RUN_JOB = SCRIPT_DIR / "run_job.py"
REGISTER = SCRIPT_DIR / "register_task.ps1"
DEFAULT_LOG_DIR = SCRIPT_DIR / "logs"
UV = shutil.which("uv") or "uv"


def _resolve(p: str) -> Path:
    # Paths in jobs.toml are relative to the scheduler dir unless absolute.
    path = Path(p)
    return path if path.is_absolute() else (SCRIPT_DIR / path).resolve()


def _load() -> dict:
    if not CONFIG.exists():
        return {"task_folder": "Scheduler", "job": []}
    with CONFIG.open("rb") as f:
        data = tomllib.load(f)
    data.setdefault("task_folder", "Scheduler")
    data.setdefault("job", [])
    return data


def _save(data: dict) -> None:
    with CONFIG.open("wb") as f:
        tomli_w.dump(data, f)


def _find(data: dict, name: str) -> dict:
    for j in data["job"]:
        if j.get("name") == name:
            return j
    raise SystemExit(f"No job named '{name}' in {CONFIG}")


def _log_dir(job: dict) -> Path:
    return _resolve(job["log_dir"]) if job.get("log_dir") else DEFAULT_LOG_DIR


def _schedule_args(schedule: str) -> list[str]:
    # "HH:MM" -> daily at that time; "every Nh" / "every Nm" -> repeating interval.
    s = schedule.strip().lower()
    m = re.fullmatch(r"every\s+(\d+)\s*([hm])", s)
    if m:
        n, unit = m.group(1), m.group(2)
        return ["-IntervalHours", n] if unit == "h" else ["-IntervalMinutes", n]
    if re.fullmatch(r"\d{1,2}:\d{2}", s):
        return ["-At", schedule.strip()]
    raise SystemExit(
        f"Bad schedule '{schedule}'. Use 'HH:MM' (daily) or 'every Nh' / 'every Nm'."
    )


def cmd_list(data: dict, args: argparse.Namespace) -> int:
    jobs = data["job"]
    if not jobs:
        print(f"No jobs configured in {CONFIG}")
        return 0
    print(f"Jobs in {CONFIG}  (task folder: {data['task_folder']}):")
    for j in jobs:
        print(f"  - {j['name']}  @ {j.get('schedule', '(no schedule)')}  cwd={j.get('workdir', '.')}")
        print(f"      {subprocess.list2cmdline(list(j['command']))}")
    return 0


def cmd_run(data: dict, args: argparse.Namespace) -> int:
    job = _find(data, args.name)
    argv = ["uv", "run", str(RUN_JOB), "--name", job["name"]]
    if job.get("log_dir"):
        argv += ["--log-dir", str(_log_dir(job))]
    argv += ["--"] + list(job["command"])
    return subprocess.run(argv, cwd=str(_resolve(job.get("workdir", ".")))).returncode


def _install_one(data: dict, job: dict) -> int:
    # Build the scheduled action ourselves: uv run run_job.py --name X [--log-dir L] -- <command>.
    # register_task.ps1 receives it as one already-quoted string, so PowerShell never has to
    # parse the command tokens (notably the `--` separator).
    run_job_argv = ["run", str(RUN_JOB), "--name", job["name"]]
    if job.get("log_dir"):
        run_job_argv += ["--log-dir", str(_log_dir(job))]
    run_job_argv += ["--"] + list(job["command"])
    arg_string = subprocess.list2cmdline(run_job_argv)

    ps = [
        "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(REGISTER),
        "-Name", job["name"], "-ExePath", UV, "-ArgString", arg_string,
        "-TaskFolder", data["task_folder"], "-WorkDir", str(_resolve(job.get("workdir", "."))),
    ]
    ps += _schedule_args(job["schedule"])
    return subprocess.run(ps).returncode


def cmd_install(data: dict, args: argparse.Namespace) -> int:
    if not args.all and args.name is None:
        raise SystemExit("Specify a job name or --all")
    jobs = data["job"] if args.all else [_find(data, args.name)]
    rc = 0
    for j in jobs:
        if not j.get("schedule"):
            print(f"skip '{j['name']}': no schedule set", file=sys.stderr)
            continue
        rc |= _install_one(data, j)
    return rc


def cmd_uninstall(data: dict, args: argparse.Namespace) -> int:
    if not args.all and args.name is None:
        raise SystemExit("Specify a job name or --all")
    folder = data["task_folder"]
    names = [j["name"] for j in data["job"]] if args.all else [args.name]
    rc = 0
    for n in names:
        rc |= subprocess.run([
            "powershell", "-NoProfile", "-Command",
            f"Unregister-ScheduledTask -TaskName '{folder}\\{n}' -Confirm:$false; "
            f"Write-Output 'Removed {folder}\\{n}'",
        ]).returncode
    return rc


def cmd_status(data: dict, args: argparse.Namespace) -> int:
    job = _find(data, args.name)
    ld = _log_dir(job)
    status = ld / f"{job['name']}.status.json"
    log = ld / f"{job['name']}.log"
    print(status.read_text(encoding="utf-8") if status.exists() else f"(no status file at {status})")
    print("--- log tail (30) ---")
    if log.exists():
        print("\n".join(log.read_text(encoding="utf-8").splitlines()[-30:]))
    else:
        print(f"(no log at {log})")
    return 0


def cmd_add(data: dict, args: argparse.Namespace) -> int:
    if any(j.get("name") == args.name for j in data["job"]):
        raise SystemExit(f"Job '{args.name}' already exists; remove it first or edit {CONFIG}")
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise SystemExit("Provide the command to run after `--`")
    job = {"name": args.name, "schedule": args.schedule, "workdir": args.workdir, "command": command}
    if args.log_dir:
        job["log_dir"] = args.log_dir
    data["job"].append(job)
    _save(data)
    print(f"Added job '{args.name}' to {CONFIG}")
    return 0


def cmd_remove(data: dict, args: argparse.Namespace) -> int:
    before = len(data["job"])
    data["job"] = [j for j in data["job"] if j.get("name") != args.name]
    if len(data["job"]) == before:
        raise SystemExit(f"No job named '{args.name}'")
    _save(data)
    print(f"Removed job '{args.name}' from {CONFIG}")
    return 0


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Config-driven Windows job scheduler (jobs.toml).")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="Show configured jobs.")
    r = sub.add_parser("run", help="Run a job now via the logging+retry wrapper.")
    r.add_argument("name")
    i = sub.add_parser("install", help="Register the scheduled task for a job (or --all).")
    i.add_argument("name", nargs="?")
    i.add_argument("--all", action="store_true")
    u = sub.add_parser("uninstall", help="Remove the scheduled task for a job (or --all).")
    u.add_argument("name", nargs="?")
    u.add_argument("--all", action="store_true")
    s = sub.add_parser("status", help="Show a job's last-run status and tail its log.")
    s.add_argument("name")
    a = sub.add_parser("add-job", help="Add a job to jobs.toml.")
    a.add_argument("--name", required=True)
    a.add_argument("--schedule", required=True, help="Daily time, e.g. 03:30")
    a.add_argument("--workdir", required=True, help="Working dir (relative to scheduler dir, or absolute)")
    a.add_argument("--log-dir")
    a.add_argument("command", nargs=argparse.REMAINDER, help="Command after `--`")
    rm = sub.add_parser("remove-job", help="Remove a job from jobs.toml.")
    rm.add_argument("--name", required=True)

    args = p.parse_args(argv)
    data = _load()
    dispatch = {
        "list": cmd_list,
        "run": cmd_run,
        "install": cmd_install,
        "uninstall": cmd_uninstall,
        "status": cmd_status,
        "add-job": cmd_add,
        "remove-job": cmd_remove,
    }
    return dispatch[args.cmd](data, args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
