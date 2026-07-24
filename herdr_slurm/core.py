from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

PLUGIN_ID = "io.github.quan-meng.herdr-slurm"
SEPARATOR = "\x1f"
ACTIVE_STATES = ("PENDING", "CONFIGURING", "RUNNING", "SUSPENDED")
JOB_ID = re.compile(r"^[0-9]+(?:_[0-9]+)?(?:\+[0-9]+)?$")
DEFAULT_CONFIG: dict[str, Any] = {
    "poll_interval_seconds": 5,
    "adopt_existing_jobs": False,
    "partitions": [],
    "job_name_pattern": ".*",
    "workspace_label": "{job_name} [{job_id}]",
    "output_tab_label": "output",
    "shell": ["zsh", "-l"],
    "srun_arguments": ["--pty", "--overlap"],
    "agent_commands": {
        "codex": ["zsh", "-lic", "exec codex"],
        "claude": ["zsh", "-lic", "exec claude"],
    },
    "focus_new_workspace": False,
    "notify": True,
}


@dataclass(frozen=True)
class Job:
    job_id: str
    name: str
    state: str
    workdir: str
    partition: str


def runtime_dirs() -> tuple[Path, Path, Path]:
    root = Path(
        os.environ.get("HERDR_PLUGIN_ROOT", Path(__file__).resolve().parents[1])
    )
    config = Path(os.environ.get("HERDR_PLUGIN_CONFIG_DIR", root / ".config"))
    state = Path(os.environ.get("HERDR_PLUGIN_STATE_DIR", root / ".state"))
    config.mkdir(parents=True, exist_ok=True)
    state.mkdir(parents=True, exist_ok=True)
    return root, config, state


def load_config(config_dir: Path) -> dict[str, Any]:
    path = config_dir / "config.json"
    if not path.exists():
        path.write_text(json.dumps(DEFAULT_CONFIG, indent=2) + "\n")
    raw = json.loads(path.read_text())
    config = DEFAULT_CONFIG.copy()
    config.update(raw)
    if not isinstance(config["partitions"], list) or not all(
        isinstance(value, str) for value in config["partitions"]
    ):
        raise ValueError("partitions must be a list of strings")
    if not isinstance(config["shell"], list) or not config["shell"]:
        raise ValueError("shell must be a non-empty argv list")
    if not isinstance(config["srun_arguments"], list):
        raise ValueError("srun_arguments must be an argv list")
    if not isinstance(config["agent_commands"], dict) or not all(
        isinstance(name, str) and isinstance(argv, list) and argv
        for name, argv in config["agent_commands"].items()
    ):
        raise ValueError("agent_commands must map agent names to non-empty argv lists")
    re.compile(config["job_name_pattern"])
    config["poll_interval_seconds"] = max(1, int(config["poll_interval_seconds"]))
    return config


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "initialized": False, "jobs": {}}
    state = json.loads(path.read_text())
    if state.get("version") != 1 or not isinstance(state.get("jobs"), dict):
        raise ValueError(f"unsupported state file: {path}")
    for record in state["jobs"].values():
        if "output_started" not in record:
            record["output_started"] = bool(record.get("attached"))
            if record.get("attached"):
                record["output_mode"] = "legacy_shell"
    return state


def save_state(path: Path, state: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def parse_squeue(output: str) -> list[Job]:
    jobs = []
    for line in output.splitlines():
        fields = line.split(SEPARATOR)
        if len(fields) != 5:
            continue
        job = Job(*(field.strip() for field in fields))
        if JOB_ID.fullmatch(job.job_id):
            jobs.append(job)
    return jobs


def query_jobs() -> list[Job]:
    format_string = SEPARATOR.join(("%i", "%j", "%T", "%Z", "%P"))
    result = subprocess.run(
        [
            "squeue",
            "--me",
            "--noheader",
            f"--states={','.join(ACTIVE_STATES)}",
            f"--format={format_string}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return parse_squeue(result.stdout)


def job_matches(job: Job, config: dict[str, Any]) -> bool:
    return (not config["partitions"] or job.partition in config["partitions"]) and bool(
        re.search(config["job_name_pattern"], job.name)
    )


def strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


class Herdr:
    def __init__(self, root: Path, config: dict[str, Any]):
        self.bin = os.environ.get("HERDR_BIN_PATH", "herdr")
        self.root = root
        self.config = config

    def call(self, *arguments: str) -> dict[str, Any]:
        result = subprocess.run(
            [self.bin, *arguments], capture_output=True, text=True, env=os.environ
        )
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip())
        return json.loads(result.stdout) if result.stdout.strip() else {}

    def create_workspace(self, job: Job) -> dict[str, Any]:
        label = self.config["workspace_label"].format(
            job_name=job.name, job_id=job.job_id, partition=job.partition
        )
        workdir = job.workdir if Path(job.workdir).is_dir() else str(Path.home())
        focus = "--focus" if self.config["focus_new_workspace"] else "--no-focus"
        created = self.call(
            "workspace", "create", "--cwd", workdir, "--label", label, focus
        )
        result = created["result"]
        record = {
            "workspace_id": result["workspace"]["workspace_id"],
            "tab_id": result["tab"]["tab_id"],
            "pane_id": result["root_pane"]["pane_id"],
        }
        self.call("tab", "rename", record["tab_id"], self.config["output_tab_label"])
        return record

    def follow_output(self, job: Job, record: dict[str, Any]) -> None:
        helper = self.root / "herdr_slurm" / "output.py"
        command = "exec " + shlex.join(
            [
                sys.executable,
                str(helper),
                job.job_id,
                str(self.config["poll_interval_seconds"]),
            ]
        )
        self.call("pane", "run", record["pane_id"], command)

    def attach(
        self,
        job: Job,
        record: dict[str, Any],
        mode: str = "shell",
        pane_id: str | None = None,
    ) -> None:
        remote = (
            self.config["shell"]
            if mode == "shell"
            else self.config["agent_commands"][mode]
        )
        argv = [
            "srun",
            f"--jobid={job.job_id}",
            *self.config["srun_arguments"],
            *remote,
        ]
        helper = self.root / "herdr_slurm" / "attach.py"
        helper_argv = [
            sys.executable,
            str(helper),
            job.job_id,
            str(self.config["poll_interval_seconds"]),
            json.dumps(argv),
        ]
        if mode != "shell":
            helper_argv = ["env", f"HERDR_AGENT={mode}", *helper_argv]
        self.call(
            "pane",
            "run",
            pane_id or record["pane_id"],
            "exec " + shlex.join(helper_argv),
        )

    def create_tab(self, workspace_id: str, mode: str) -> dict[str, Any]:
        return self.call(
            "tab", "create", "--workspace", workspace_id, "--label", mode, "--focus"
        )

    def report(self, record: dict[str, Any], state: str) -> None:
        self.call(
            "workspace",
            "report-metadata",
            record["workspace_id"],
            "--source",
            "herdr-slurm",
            "--token",
            f"slurm_job={record['job_id']}",
            "--token",
            f"slurm_state={state}",
        )

    def notify(self, title: str, body: str, sound: str = "none") -> None:
        if self.config["notify"]:
            self.call("notification", "show", title, "--body", body, "--sound", sound)

    def output_alive(self, record: dict[str, Any]) -> bool:
        try:
            info = self.call("pane", "process-info", record["pane_id"])
        except RuntimeError:
            return False
        haystack = "\n".join(strings(info))
        return record["job_id"] in haystack and "output.py" in haystack


def new_record(job: Job, managed: bool) -> dict[str, Any]:
    record = asdict(job)
    record.update({"managed": managed, "output_started": False})
    return record


def safely(action, message: str) -> bool:
    try:
        action()
        return True
    except (KeyError, OSError, RuntimeError, ValueError) as error:
        print(f"herdr-slurm: {message}: {error}", file=sys.stderr, flush=True)
        return False


def reconcile(
    jobs: list[Job],
    state: dict[str, Any],
    herdr: Herdr,
    config: dict[str, Any],
    adopt: bool = False,
) -> bool:
    changed = False
    records = state["jobs"]
    current = {job.job_id: job for job in jobs}
    first_run = not state["initialized"]
    state["initialized"] = True
    if first_run:
        changed = True

    for job in jobs:
        record = records.get(job.job_id)
        if record is None:
            record = records[job.job_id] = new_record(job, False)
            record["ignored_existing"] = first_run and not (
                adopt or config["adopt_existing_jobs"]
            )
            changed = True
        should_manage = job_matches(job, config) and (
            adopt
            or config["adopt_existing_jobs"]
            or not record.get("ignored_existing", False)
        )
        if not record["managed"] and should_manage:
            created: dict[str, Any] = {}
            if not safely(
                lambda: created.update(herdr.create_workspace(job)),
                f"create {job.job_id}",
            ):
                continue
            record.update(created)
            record.update({"managed": True, "output_started": False})
            changed = True
        previous = record["state"]
        record.update(asdict(job))
        if previous != job.state:
            changed = True
        if not record["managed"]:
            continue
        record["job_id"] = job.job_id
        if not record["output_started"] and safely(
            lambda: herdr.follow_output(job, record), f"follow output {job.job_id}"
        ):
            record["output_started"] = True
            record["output_mode"] = "output"
            changed = True
        if previous != job.state or not record.get("reported"):
            if safely(lambda: herdr.report(record, job.state), f"report {job.job_id}"):
                record["reported"] = True
                changed = True
        if job.state == "RUNNING" and previous != "RUNNING":
            safely(
                lambda: herdr.notify(
                    "Slurm job running", f"{job.name} [{job.job_id}]", "done"
                ),
                f"notify {job.job_id}",
            )

    for job_id, record in records.items():
        if job_id in current or record["state"] == "ENDED":
            continue
        record["state"] = "ENDED"
        record["ended_at"] = int(time.time())
        changed = True
        if record["managed"]:
            safely(lambda r=record: herdr.report(r, "ENDED"), f"report {job_id}")
            safely(
                lambda r=record: herdr.notify(
                    "Slurm job ended", f"{r['name']} [{r['job_id']}]", "done"
                ),
                f"notify {job_id}",
            )
    return changed
