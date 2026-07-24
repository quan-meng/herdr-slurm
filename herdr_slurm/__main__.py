from __future__ import annotations

import fcntl
import json
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
    if not managed_job(state_dir, workspace_id):
        print("current Space is not managed by herdr-slurm", file=sys.stderr)
        return 1
    Herdr(root, config).create_tab(workspace_id, mode)
    return 0


def tab_created(root: Path, config_dir: Path, state_dir: Path) -> int:
    event = json.loads(os.environ.get("HERDR_PLUGIN_EVENT_JSON", "{}"))
    tab = event.get("data", {}).get("tab", {})
    workspace_id = tab.get("workspace_id") or os.environ.get("HERDR_WORKSPACE_ID", "")
    pane_id = os.environ.get("HERDR_PANE_ID", "")
    found = managed_job(state_dir, workspace_id)
    if not found or not pane_id:
        return 0
    job, record = found
    if pane_id == record.get("pane_id"):
        return 0
    config = load_config(config_dir)
    label = str(tab.get("label", "")).lower()
    mode = label if label in config["agent_commands"] else config["new_tab_mode"]
    Herdr(root, config).attach(job, record, mode, pane_id)
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
    if command == "tab-created":
        return tab_created(root, config_dir, state_dir)
    print(f"unknown command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
