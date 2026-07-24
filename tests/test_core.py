from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from herdr_slurm.__main__ import tab_created
from herdr_slurm.core import Job, Herdr, load_state, parse_squeue, reconcile
from herdr_slurm.output import job_active, output_paths, read_new


class FakeHerdr:
    def __init__(self):
        self.created = []
        self.attached = []
        self.followed = []
        self.reported = []
        self.notifications = []

    def create_workspace(self, job):
        self.created.append(job.job_id)
        return {"workspace_id": f"w-{job.job_id}", "tab_id": "t1", "pane_id": "p1"}

    def attach(self, job, record):
        self.attached.append(job.job_id)

    def follow_output(self, job, record):
        self.followed.append(job.job_id)

    def report(self, record, state):
        self.reported.append((record["job_id"], state))

    def notify(self, title, body, sound="none"):
        self.notifications.append((title, body, sound))


CONFIG = {
    "adopt_existing_jobs": False,
    "partitions": [],
    "job_name_pattern": ".*",
}


def job(job_id="42", state="PENDING", partition="gpu"):
    return Job(job_id, "interactive", state, "/work", partition)


class CoreTests(unittest.TestCase):
    def test_parse_squeue_uses_control_separator_and_skips_array_ranges(self):
        output = (
            "42\x1finteractive\x1fRUNNING\x1f/work dir\x1fgpu\n"
            "43_[1-4]\x1farray\x1fPENDING\x1f/work\x1fgpu\n"
        )
        self.assertEqual(
            parse_squeue(output),
            [Job("42", "interactive", "RUNNING", "/work dir", "gpu")],
        )

    def test_first_poll_baselines_existing_jobs(self):
        state = {"version": 1, "initialized": False, "jobs": {}}
        herdr = FakeHerdr()
        self.assertTrue(reconcile([job()], state, herdr, CONFIG))
        self.assertFalse(state["jobs"]["42"]["managed"])
        self.assertEqual(herdr.created, [])
        reconcile([job()], state, herdr, CONFIG)
        self.assertEqual(herdr.created, [])

    def test_new_job_creates_workspace_and_output_follower_once(self):
        state = {"version": 1, "initialized": True, "jobs": {}}
        herdr = FakeHerdr()
        reconcile([job()], state, herdr, CONFIG)
        reconcile([job(state="RUNNING")], state, herdr, CONFIG)
        self.assertEqual(herdr.created, ["42"])
        self.assertEqual(herdr.followed, ["42"])
        self.assertIn(("42", "RUNNING"), herdr.reported)

    def test_adopt_promotes_baselined_job(self):
        state = {"version": 1, "initialized": False, "jobs": {}}
        herdr = FakeHerdr()
        reconcile([job()], state, herdr, CONFIG)
        reconcile([job()], state, herdr, CONFIG, adopt=True)
        self.assertTrue(state["jobs"]["42"]["managed"])
        self.assertEqual(herdr.created, ["42"])

    def test_partition_filter_is_respected(self):
        state = {"version": 1, "initialized": True, "jobs": {}}
        herdr = FakeHerdr()
        reconcile([job()], state, herdr, CONFIG | {"partitions": ["cpu"]})
        self.assertEqual(herdr.created, [])

    def test_failed_workspace_creation_is_retried(self):
        state = {"version": 1, "initialized": True, "jobs": {}}
        herdr = FakeHerdr()
        original = herdr.create_workspace
        herdr.create_workspace = lambda unused: (_ for _ in ()).throw(
            RuntimeError("busy")
        )
        reconcile([job()], state, herdr, CONFIG)
        herdr.create_workspace = original
        reconcile([job()], state, herdr, CONFIG)
        self.assertTrue(state["jobs"]["42"]["managed"])
        self.assertEqual(herdr.created, ["42"])

    def test_missing_job_is_marked_ended(self):
        state = {"version": 1, "initialized": True, "jobs": {}}
        herdr = FakeHerdr()
        reconcile([job()], state, herdr, CONFIG)
        reconcile([], state, herdr, CONFIG)
        self.assertEqual(state["jobs"]["42"]["state"], "ENDED")
        self.assertIn(("42", "ENDED"), herdr.reported)

    def test_load_state_rejects_unknown_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text('{"version": 2, "jobs": {}}')
            with self.assertRaises(ValueError):
                load_state(path)

    def test_load_state_preserves_legacy_initial_shell(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "initialized": True,
                        "jobs": {"42": {"attached": True}},
                    }
                )
            )
            record = load_state(path)["jobs"]["42"]
        self.assertTrue(record["output_started"])
        self.assertEqual(record["output_mode"], "legacy_shell")

    def test_scontrol_output_paths_include_distinct_stdout_and_stderr(self):
        output = (
            "JobId=42 WorkDir=/work StdErr=logs/error.log StdIn=/dev/null "
            "StdOut=/work/output.log TresPerNode=gres/gpu:1"
        )
        self.assertEqual(
            output_paths(output),
            [Path("/work/output.log"), Path("/work/logs/error.log")],
        )

    def test_output_paths_deduplicate_merged_streams(self):
        output = "JobId=42 WorkDir=/work StdErr=/work/job.log StdOut=/work/job.log"
        self.assertEqual(output_paths(output), [Path("/work/job.log")])

    def test_output_reader_follows_appends(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "job.log"
            path.write_text("first\n")
            handles = {}
            with patch("sys.stdout") as stdout:
                self.assertTrue(read_new(path, handles))
                path.write_text("first\nsecond\n")
                self.assertTrue(read_new(path, handles))
            for _, handle in handles.values():
                handle.close()
        self.assertEqual(
            [call.args[0] for call in stdout.write.call_args_list],
            ["first\n", "second\n"],
        )

    @patch("herdr_slurm.output.subprocess.run")
    def test_invalid_job_is_not_active(self, run):
        run.return_value.returncode = 1
        run.return_value.stderr = "slurm_load_jobs error: Invalid job id specified"
        run.return_value.stdout = ""
        self.assertFalse(job_active("999999999"))

    def test_codex_attachment_sets_host_visible_agent_hint(self):
        config = {
            "poll_interval_seconds": 5,
            "srun_arguments": ["--pty", "--overlap"],
            "shell": ["zsh", "-l"],
            "agent_commands": {"codex": ["zsh", "-lic", "exec codex"]},
        }
        with tempfile.TemporaryDirectory() as directory:
            client = Herdr(Path(directory), config)
            calls = []
            client.call = lambda *arguments: calls.append(arguments) or {}
            client.attach(job(), {"pane_id": "w1:p1"}, "codex")
        command = calls[0][-1]
        self.assertIn("HERDR_AGENT=codex", command)
        self.assertIn("--jobid=42", command)
        self.assertIn("exec codex", command)

    def test_tab_created_uses_agent_label_as_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_dir, state_dir = root / "config", root / "state"
            config_dir.mkdir()
            state_dir.mkdir()
            record = {
                "job_id": "42",
                "name": "interactive",
                "state": "RUNNING",
                "workdir": "/work",
                "partition": "gpu",
                "managed": True,
                "workspace_id": "w1",
                "pane_id": "w1:p1",
            }
            (state_dir / "state.json").write_text(
                json.dumps({"version": 1, "initialized": True, "jobs": {"42": record}})
            )
            event = {"data": {"tab": {"workspace_id": "w1", "label": "codex"}}}
            environment = {
                "HERDR_PLUGIN_EVENT_JSON": json.dumps(event),
                "HERDR_PANE_ID": "w1:p2",
            }
            with (
                patch.dict(os.environ, environment),
                patch.object(Herdr, "attach") as attach,
            ):
                self.assertEqual(tab_created(root, config_dir, state_dir), 0)
        self.assertEqual(attach.call_args.args[2:], ("codex", "w1:p2"))


if __name__ == "__main__":
    unittest.main()
