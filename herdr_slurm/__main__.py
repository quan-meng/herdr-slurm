from __future__ import annotations

import fcntl
import os
import subprocess
import sys
import time
from pathlib import Path

from .core import (
    Job,
    PLUGIN_ID,
    Herdr,
    load_config,
    load_state,
    query_jobs,
    reconcile,
    runtime_dirs,
    save_state,
)


def start_watcher() -> int:
    herdr = os.environ.get("HERDR_BIN_PATH", "herdr")
    result = subprocess.run(
        [
            herdr,
            "plugin",
            "pane",
            "open",
            "--plugin",
            PLUGIN_ID,
            "--entrypoint",
            "watcher",
            "--placement",
            "tab",
            "--no-focus",
        ],
        env=os.environ,
    )
    return result.returncode


def request_adoption(state_dir: Path) -> int:
    (state_dir / "adopt.request").touch()
    return start_watcher()


def managed_job(state_dir: Path, workspace_id: str) -> tuple[Job, dict] | None:
    state = load_state(state_dir / "state.json")
    for record in state["jobs"].values():
        if (
            record.get("managed")
            and record.get("state") != "ENDED"
            and record.get("workspace_id") == workspace_id
        ):
            return Job(
                record["job_id"],
                record["name"],
                record["state"],
                record["workdir"],
                record["partition"],
            ), record
    return None


def new_tab(root: Path, config_dir: Path, state_dir: Path, mode: str) -> int:
    config = load_config(config_dir)
    if mode != "shell" and mode not in config["agent_commands"]:
        print(f"unknown tab mode: {mode}", file=sys.stderr)
        return 2
    workspace_id = os.environ.get("HERDR_WORKSPACE_ID", "")
    found = managed_job(state_dir, workspace_id)
    if not found:
        print("current Space is not managed by herdr-slurm", file=sys.stderr)
        return 1
    job, record = found
    herdr = Herdr(root, config)
    created = herdr.create_tab(workspace_id, mode)
    pane_id = created["result"]["root_pane"]["pane_id"]
    herdr.attach(job, record, mode, pane_id)
    return 0


def recover_outputs(state: dict, herdr: Herdr) -> bool:
    changed = False
    for record in state["jobs"].values():
        if (
            record.get("managed")
            and record.get("output_started")
            and record.get("output_mode") != "legacy_shell"
            and record.get("state") != "ENDED"
        ):
            if not herdr.output_alive(record):
                record["output_started"] = False
                changed = True
    return changed


def watch(root: Path, config_dir: Path, state_dir: Path) -> int:
    lock_path = state_dir / "watcher.lock"
    lock = lock_path.open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("A Slurm watcher is already running; this pane can be closed.")
        return 0
    config = load_config(config_dir)
    state_path = state_dir / "state.json"
    state = load_state(state_path)
    herdr = Herdr(root, config)
    if recover_outputs(state, herdr):
        save_state(state_path, state)
    print("Watching Slurm allocations. Press Ctrl+C to stop.", flush=True)
    while True:
        request = state_dir / "adopt.request"
        adopt = request.exists()
        try:
            if reconcile(query_jobs(), state, herdr, config, adopt):
                save_state(state_path, state)
            if adopt:
                request.unlink(missing_ok=True)
        except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as error:
            print(f"herdr-slurm: {error}", file=sys.stderr, flush=True)
        time.sleep(config["poll_interval_seconds"])


def main() -> int:
    root, config_dir, state_dir = runtime_dirs()
    command = sys.argv[1] if len(sys.argv) > 1 else "watch"
    if command == "start":
        return start_watcher()
    if command == "adopt":
        return request_adoption(state_dir)
    if command == "watch":
        try:
            return watch(root, config_dir, state_dir)
        except KeyboardInterrupt:
            return 0
    if command == "new-tab" and len(sys.argv) == 3:
        return new_tab(root, config_dir, state_dir, sys.argv[2])
    print(f"unknown command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
