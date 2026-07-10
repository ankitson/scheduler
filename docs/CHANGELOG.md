# Scheduler Changelog

## 2026-07-05

### Linux systemd timers and AgentsView sync job
- Added Linux support to `scheduler.py` using systemd user service and timer units.
- Generated Linux service units now include the install-time PATH so scheduled job steps can find user-installed commands such as `uv`.
- Updated scheduler documentation and examples to list Linux as a supported backend.
- Added a local `jobs.toml` entry for `agentsview-transcript-sync`, running the devserver AgentsView remote transcript sync every 3 hours.
- Installed and smoke-tested the timer through the existing `just run`, `just install`, and `just status` workflows.
