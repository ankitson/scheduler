# windows-scheduler

Run scripts on a schedule via Windows Task Scheduler, with logging and retries. Jobs
are defined in `jobs.toml`; the projects being scheduled need know nothing about this tool.

## How it works

- **`jobs.toml`** — declares each job (name, schedule, working dir, and either a single
  `command` or a list of `steps` run in sequence). A multi-step job is one logged, retried
  unit and stops at the first failing step (e.g. export → publish).
- **`scheduler.py`** — reads the config and orchestrates everything.
- **`run_job.py`** — wraps each run with a rotating log + retries and writes a status file.
- **`register_task.ps1`** — registers the Windows scheduled task (daily or repeating).
- **`publish.py`** — bundled helper for exporter jobs: copies a directory into
  `<dest>/<uuid>/` and touches a `_READY` sentinel so a downstream pipeline only picks
  up complete drops. Use it as a job command: `uv run publish.py --src <export> --dest <landing>`.

A scheduled task simply runs `run_job.py`, which runs the job's command. Logs and
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

- `"every 3h"` / `"every 30m"` — repeating interval, aligned to midnight, indefinitely.
- `"03:30"` — daily at that time.

Tasks use `StartWhenAvailable`, so a slot missed while the PC is asleep runs at the next
wake (it does not wake the PC).

## Requirements

- Windows + [`uv`](https://docs.astral.sh/uv/) on PATH
- [`just`](https://github.com/casey/just) (optional; recipes wrap `scheduler.py`)
