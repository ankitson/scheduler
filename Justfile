# scheduler: config-driven native scheduled jobs with logging + retries.
# All jobs live in jobs.toml. Consuming projects need know nothing about this tool.

set windows-shell := ["powershell.exe", "-NoProfile", "-Command"]

# List available recipes
default:
    @just --list

# Show configured jobs.
jobs:
    uv run scheduler.py list

# Run a configured job now (through the logging + retry wrapper).
run name:
    uv run scheduler.py run {{name}}

# Register the native scheduled task for a job.
install name:
    uv run scheduler.py install {{name}}

# Register scheduled tasks for every configured job.
install-all:
    uv run scheduler.py install --all

# Remove the scheduled task for a job.
uninstall name:
    uv run scheduler.py uninstall {{name}}

# Remove scheduled tasks for every configured job.
uninstall-all:
    uv run scheduler.py uninstall --all

# Show a job's last-run status and tail its log.
status name:
    uv run scheduler.py status {{name}}

# Add a job to jobs.toml:  just add-job <name> <schedule> <workdir> -- <command...>
add-job name schedule workdir +command:
    uv run scheduler.py add-job --name {{name}} --schedule {{schedule}} --workdir {{workdir}} {{command}}

# Remove a job from jobs.toml.
remove-job name:
    uv run scheduler.py remove-job --name {{name}}
