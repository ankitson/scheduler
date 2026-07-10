# Scheduler Notes

## 2026-07-05

### AgentsView transcript sync timer
#### Goal
- Use the local scheduler project to run the AgentsView remote transcript pull periodically on Linux.
#### Discovery
- The scheduler already had Windows Task Scheduler and macOS launchd backends, but this host needs a Linux backend.
- Jobs are launched through `run_job.py`, so the same logging, retry, and status files can be reused for systemd user timers.
- systemd user services do not inherit the interactive shell PATH, so generated services need an explicit PATH for job steps such as `uv`.
#### Decision
- Added Linux systemd user timer support to `scheduler.py`.
- Generated Linux service units now preserve the install-time PATH through an `Environment=PATH=...` line.
- Added `jobs.toml` with an `agentsview-transcript-sync` job that runs from `/home/ankit/hroot/devserver` every 3 hours.
- Installed the job as `local.scheduler.Scheduler.agentsview-transcript-sync.timer` under the current user's systemd instance.
#### Verification
- `uv run scheduler.py list` shows the configured job.
- `just run agentsview-transcript-sync` completed successfully through the logging wrapper.
- `just install agentsview-transcript-sync` enabled and started the systemd user timer.
- `systemctl --user start local.scheduler.Scheduler.agentsview-transcript-sync.service` completed successfully after reinstalling the unit.
- `systemctl --user list-timers local.scheduler.Scheduler.agentsview-transcript-sync.timer --all` shows the timer active and waiting for the next run.
