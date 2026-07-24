from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path
from typing import TextIO


def field(output: str, name: str) -> str:
    match = re.search(
        rf"(?:^|\s){re.escape(name)}=(.*?)(?=\s[A-Za-z][A-Za-z0-9_]*=|$)", output
    )
    return match.group(1).strip() if match else ""


def output_paths(output: str) -> list[Path]:
    workdir = Path(field(output, "WorkDir") or Path.home())
    paths = []
    for name in ("StdOut", "StdErr"):
        value = field(output, name)
        if not value or value in {"(null)", "N/A", "/dev/null"}:
            continue
        path = Path(value)
        path = path if path.is_absolute() else workdir / path
        if path not in paths:
            paths.append(path)
    return paths


def job_active(job_id: str) -> bool:
    result = subprocess.run(
        ["squeue", "--jobs", job_id, "--noheader", "--format=%i"],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        return "invalid job id" not in result.stderr.lower()
    return bool(result.stdout.strip())


def resolve_paths(job_id: str, interval: int) -> list[Path]:
    while True:
        result = subprocess.run(
            ["scontrol", "show", "job", "-o", job_id], capture_output=True, text=True
        )
        paths = output_paths(result.stdout) if result.returncode == 0 else []
        if paths:
            return paths
        if not job_active(job_id):
            return []
        print(f"Waiting for Slurm output paths for job {job_id}...", flush=True)
        time.sleep(interval)


def read_new(path: Path, handles: dict[Path, tuple[int, TextIO]]) -> bool:
    try:
        stat = path.stat()
    except FileNotFoundError:
        old = handles.pop(path, None)
        if old:
            old[1].close()
        return False
    current = handles.get(path)
    if current is None or current[0] != stat.st_ino:
        if current:
            current[1].close()
        handles[path] = (stat.st_ino, path.open(errors="replace"))
    handle = handles[path][1]
    if stat.st_size < handle.tell():
        handle.seek(0)
    emitted = False
    while chunk := handle.read(65536):
        sys.stdout.write(chunk)
        emitted = True
    if emitted:
        sys.stdout.flush()
    return emitted


def follow(job_id: str, paths: list[Path], interval: int) -> int:
    print(f"Following Slurm output for job {job_id}:", flush=True)
    for path in paths:
        print(f"  {path}", flush=True)
    handles: dict[Path, tuple[int, TextIO]] = {}
    quiet_after_end = 0
    while True:
        emitted = False
        for path in paths:
            emitted = read_new(path, handles) or emitted
        if job_active(job_id):
            quiet_after_end = 0
        else:
            quiet_after_end = 0 if emitted else quiet_after_end + 1
            if quiet_after_end >= 2:
                print(f"\nSlurm job {job_id} ended.", flush=True)
                return 0
        time.sleep(interval)


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: output.py JOB_ID POLL_SECONDS", file=sys.stderr)
        return 2
    job_id, interval = sys.argv[1:]
    seconds = max(1, int(interval))
    paths = resolve_paths(job_id, seconds)
    if not paths:
        print(f"No output files found for Slurm job {job_id}.", file=sys.stderr)
        return 1
    return follow(job_id, paths, seconds)


if __name__ == "__main__":
    raise SystemExit(main())
