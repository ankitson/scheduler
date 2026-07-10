# scheduler

Run scripts on a schedule via the native OS scheduler, with logging and retries. Jobs
are defined in `jobs.toml`; the projects being scheduled need know nothing about this tool.

Supported schedulers:

- Windows: Task Scheduler
- macOS: launchd LaunchAgents
- Linux: systemd user timers

## How it works

- **`jobs.toml`** — declares each job (name, schedule, working dir, and either a single
  `command` or a list of `steps` run in sequence). A multi-step job is one logged, retried
  unit and stops at the first failing step (e.g. export → publish).
- **`scheduler.py`** — reads the config and orchestrates everything.
- **`run_job.py`** — wraps each run with a rotating log + retries and writes a status file.
- **`register_task.ps1`** — Windows Task Scheduler backend (daily or repeating).
- **`publish.py`** — bundled helper for exporter jobs: copies a directory into
  `<dest>/<uuid>/` and touches a `_READY` sentinel so a downstream pipeline only picks
  up complete drops. Use it as a job command: `uv run publish.py --src <export> --dest <landing>`.

The native scheduler simply runs `run_job.py`, which runs the job's command. Logs and
`<name>.status.json` land in `logs/` (override per-job with `log_dir`).

## Quick start

```sh
cp jobs.example.toml jobs.toml      # then edit your jobs
just jobs                           # list configured jobs
just run <name>                     # run once now (logged + retried)
just install <name>                 # register the scheduled task (or: install-all)
just status <name>                  # last-run status + log tail
just uninstall <name>               # remove the task (or: uninstall-all)
```

Add/remove jobs from the CLI (rewrites `jobs.toml`, dropping comments):

```sh
just add-job <name> <schedule> <workdir> -- <command...>
just remove-job <name>
```

## Schedule formats

- `"every 3h"` / `"every 30m"` — repeating interval, indefinitely.
- `"03:30"` — daily at that time.

On Windows, tasks use `StartWhenAvailable`, so a slot missed while the PC is asleep runs
at the next wake (it does not wake the PC). On macOS, installed jobs are written to
`~/Library/LaunchAgents/local.scheduler.<task_folder>.<job>.plist` and loaded into the
current user's `launchd` GUI session. On Linux, installed jobs are written to
`~/.config/systemd/user/local.scheduler.<task_folder>.<job>.{service,timer}` and enabled
with `systemctl --user`. Windows repeating intervals are aligned to midnight; macOS and
Linux repeating intervals are counted from when the agent/timer is loaded.

## Requirements

- Windows, macOS, or Linux + [`uv`](https://docs.astral.sh/uv/) on PATH
- [`just`](https://github.com/casey/just) (optional; recipes wrap `scheduler.py`)
