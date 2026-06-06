#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["tomli-w"]
# ///
"""Config-driven native job scheduler.

Reads jobs.toml and runs / schedules each job via run_job.py (logging + retries)
and the host OS scheduler. Consuming projects need know nothing about this tool --
a project is simply one entry in jobs.toml.
"""

from __future__ import annotations

import argparse
import os
import plistlib
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


def _job_steps(job: dict) -> list[list[str]]:
    # A job has either a single `command` or a list of `steps` (run in sequence).
    if job.get("steps"):
        return [list(s) for s in job["steps"]]
    if job.get("command"):
        return [list(job["command"])]
    raise SystemExit(f"Job '{job.get('name')}' has neither 'command' nor 'steps'")


def _run_job_remainder(job: dict) -> list[str]:
    # Flatten steps into run_job.py's command tail, joined by the `;;` step separator.
    remainder: list[str] = []
    for i, step in enumerate(_job_steps(job)):
        if i:
            remainder.append(";;")
        remainder += step
    return remainder


def _schedule_args(schedule: str) -> list[str]:
    # "HH:MM" -> daily at that time; "every Nh" / "every Nm" -> repeating interval.
    kind, value = _schedule_kind(schedule)
    if kind == "interval":
        seconds = int(value)
        unit = "h" if seconds % 3600 == 0 else "m"
        n = str(seconds // (3600 if unit == "h" else 60))
        return ["-IntervalHours", n] if unit == "h" else ["-IntervalMinutes", n]
    hour, minute = value
    return ["-At", f"{hour:02d}:{minute:02d}"]


def _schedule_kind(schedule: str) -> tuple[str, int | tuple[int, int]]:
    # Returns ("interval", seconds) or ("daily", (hour, minute)).
    s = schedule.strip().lower()
    m = re.fullmatch(r"every\s+(\d+)\s*([hm])", s)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        return "interval", n * (3600 if unit == "h" else 60)
    if re.fullmatch(r"\d{1,2}:\d{2}", s):
        hour_s, minute_s = s.split(":", 1)
        hour, minute = int(hour_s), int(minute_s)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return "daily", (hour, minute)
    raise SystemExit(
        f"Bad schedule '{schedule}'. Use 'HH:MM' (daily) or 'every Nh' / 'every Nm'."
    )


def _run_job_argv(job: dict) -> list[str]:
    argv = ["run", str(RUN_JOB), "--name", job["name"]]
    if job.get("log_dir"):
        argv += ["--log-dir", str(_log_dir(job))]
    return argv + ["--"] + _run_job_remainder(job)


def cmd_list(data: dict, args: argparse.Namespace) -> int:
    jobs = data["job"]
    if not jobs:
        print(f"No jobs configured in {CONFIG}")
        return 0
    print(f"Jobs in {CONFIG}  (task folder: {data['task_folder']}):")
    for j in jobs:
        print(f"  - {j['name']}  @ {j.get('schedule', '(no schedule)')}  cwd={j.get('workdir', '.')}")
        for step in _job_steps(j):
            print(f"      {subprocess.list2cmdline(step)}")
    return 0


def cmd_run(data: dict, args: argparse.Namespace) -> int:
    job = _find(data, args.name)
    argv = ["uv"] + _run_job_argv(job)
    return subprocess.run(argv, cwd=str(_resolve(job.get("workdir", ".")))).returncode


def _install_one_windows(data: dict, job: dict) -> int:
    # Build the scheduled action ourselves: uv run run_job.py --name X [--log-dir L] -- <command>.
    # register_task.ps1 receives it as one already-quoted string, so PowerShell never has to
    # parse the command tokens (notably the `--` separator).
    arg_string = subprocess.list2cmdline(_run_job_argv(job))

    ps = [
        "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(REGISTER),
        "-Name", job["name"], "-ExePath", UV, "-ArgString", arg_string,
        "-TaskFolder", data["task_folder"], "-WorkDir", str(_resolve(job.get("workdir", "."))),
    ]
    ps += _schedule_args(job["schedule"])
    return subprocess.run(ps).returncode


def _launchd_domain() -> str:
    return f"gui/{os.getuid()}"


def _launchd_label(data: dict, job_name: str) -> str:
    parts = [data["task_folder"], job_name]
    safe = ".".join(re.sub(r"[^A-Za-z0-9_-]+", "-", p).strip("-") for p in parts)
    safe = re.sub(r"-{2,}", "-", safe).strip(".-") or "scheduler"
    return f"local.scheduler.{safe}"


def _launchd_plist_path(label: str) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"


def _launchd_program_arguments(job: dict) -> list[str]:
    if shutil.which("uv") is None:
        raise SystemExit("Cannot install launchd job: uv was not found on PATH.")
    return [str(Path(UV).resolve())] + _run_job_argv(job)


def _install_one_macos(data: dict, job: dict) -> int:
    label = _launchd_label(data, job["name"])
    plist_path = _launchd_plist_path(label)
    log_dir = _log_dir(job)
    log_dir.mkdir(parents=True, exist_ok=True)
    plist_path.parent.mkdir(parents=True, exist_ok=True)

    schedule_kind, schedule_value = _schedule_kind(job["schedule"])
    plist: dict[str, object] = {
        "Label": label,
        "ProgramArguments": _launchd_program_arguments(job),
        "WorkingDirectory": str(_resolve(job.get("workdir", "."))),
        "StandardOutPath": str(log_dir / f"{job['name']}.launchd.out.log"),
        "StandardErrorPath": str(log_dir / f"{job['name']}.launchd.err.log"),
    }
    if schedule_kind == "interval":
        plist["StartInterval"] = schedule_value
    else:
        hour, minute = schedule_value
        plist["StartCalendarInterval"] = {"Hour": hour, "Minute": minute}

    with plist_path.open("wb") as f:
        plistlib.dump(plist, f, sort_keys=False)

    subprocess.run(["launchctl", "bootout", _launchd_domain(), str(plist_path)], stderr=subprocess.DEVNULL)
    rc = subprocess.run(["launchctl", "bootstrap", _launchd_domain(), str(plist_path)]).returncode
    if rc == 0:
        print(f"Registered launchd agent: {label} ({job['schedule']})")
        print(f"Plist: {plist_path}")
        print(f"Working dir: {plist['WorkingDirectory']}")
        print(f"Executes: {subprocess.list2cmdline(plist['ProgramArguments'])}")
    return rc


def _install_one(data: dict, job: dict) -> int:
    if sys.platform == "win32":
        return _install_one_windows(data, job)
    if sys.platform == "darwin":
        return _install_one_macos(data, job)
    raise SystemExit(f"Install is only supported on Windows and macOS, not {sys.platform}.")


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
    if sys.platform == "darwin":
        names = [j["name"] for j in data["job"]] if args.all else [args.name]
        rc = 0
        for n in names:
            label = _launchd_label(data, n)
            plist_path = _launchd_plist_path(label)
            bootout = subprocess.run(
                ["launchctl", "bootout", _launchd_domain(), str(plist_path)],
                stderr=subprocess.DEVNULL,
            )
            if plist_path.exists():
                plist_path.unlink()
                print(f"Removed launchd agent: {label}")
            else:
                print(f"Not found: {plist_path}")
            rc |= 0 if bootout.returncode in (0, 3, 36) else bootout.returncode
        return rc
    if sys.platform != "win32":
        raise SystemExit(f"Uninstall is only supported on Windows and macOS, not {sys.platform}.")

    folder = data["task_folder"]
    names = [j["name"] for j in data["job"]] if args.all else [args.name]
    rc = 0
    for n in names:
        # Unregister-ScheduledTask needs -TaskPath separately (it won't parse "folder\name").
        rc |= subprocess.run([
            "powershell", "-NoProfile", "-Command",
            f"try {{ Unregister-ScheduledTask -TaskPath '\\{folder}\\' -TaskName '{n}' "
            f"-Confirm:$false -ErrorAction Stop; Write-Output 'Removed {folder}\\{n}' }} "
            f"catch {{ Write-Output 'Not found: {folder}\\{n}' }}",
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
    p = argparse.ArgumentParser(description="Config-driven native job scheduler (jobs.toml).")
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
