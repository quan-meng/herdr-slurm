from __future__ import annotations

import json
import os
import subprocess
import sys
import time


def state(job_id: str) -> str | None:
    result = subprocess.run(
        ["squeue", "--jobs", job_id, "--noheader", "--format=%T"],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise RuntimeError(
            result.stderr.strip() or f"squeue exited {result.returncode}"
        )
    states = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return states[0] if states else None


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: attach.py JOB_ID POLL_SECONDS SRUN_ARGV_JSON", file=sys.stderr)
        return 2
    job_id, interval, encoded = sys.argv[1:]
    argv = json.loads(encoded)
    while True:
        current = state(job_id)
        if current == "RUNNING":
            print(f"Attaching to Slurm job {job_id}...", flush=True)
            os.execvp(argv[0], argv)
        if current is None:
            print(f"Slurm job {job_id} is no longer active.", file=sys.stderr)
            return 1
        print(f"Waiting for Slurm job {job_id} ({current})...", flush=True)
        time.sleep(max(1, int(interval)))


if __name__ == "__main__":
    raise SystemExit(main())
