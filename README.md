# herdr-slurm

Create a [Herdr](https://herdr.dev/) Space for each new Slurm allocation, show its batch
output, and provide explicit shell or agent actions for the allocated compute node.

```text
squeue detects <job-name> [<job-id>]
             │
             └── Herdr Space: <job-name> [<job-id>]
                              └── output  (live sbatch stdout/stderr)
```

## Requirements

- Linux login node with `squeue` and `srun`
- Herdr 0.7.0 or newer
- Python 3.8 or newer

No Python packages are required.

## Install

Once published, install directly from GitHub:

```bash
herdr plugin install quan-meng/herdr-slurm
```

For local development:

```bash
git clone https://github.com/quan-meng/herdr-slurm.git
cd herdr-slurm
herdr plugin link "$PWD"
```

The startup hook opens one managed watcher tab after Herdr restores its session. To start it
immediately after `plugin link`, use:

```bash
herdr plugin pane open \
  --plugin io.github.quan-meng.herdr-slurm \
  --entrypoint watcher \
  --placement tab
```

Only one watcher runs at a time. An accidentally opened duplicate watcher prints a message
and exits.

## Behavior

On its first poll, the watcher records existing jobs without changing Herdr. Every new matching
job after that creates a Space named `<job-name> [<job-id>]`. The values come from Slurm's job
record and are not hard-coded. The initial `output` tab follows the stdout and stderr paths that
Slurm reports for the batch job, including output produced by the submitted script.

Ordinary tabs and split panes remain login-node shells. Attach one manually when you want a
compute-node shell:

```bash
srun --jobid=<job-id> --pty --overlap zsh -l
```

Choose a static Herdr agent hint when that pane will run an agent behind `srun`:

```bash
HERDR_AGENT=codex srun --jobid=<job-id> --pty --overlap zsh -l
HERDR_AGENT=claude srun --jobid=<job-id> --pty --overlap zsh -l
```

The allocation remains owned by the original `sbatch` job. Exiting the attached shell only ends
the overlapping job step.

To create Spaces for jobs that were already active when the watcher started:

```bash
herdr plugin action invoke \
  io.github.quan-meng.herdr-slurm.adopt-current
```

Generated Spaces remain open after allocations end. The plugin reports `slurm_job` and
`slurm_state` workspace metadata and sends Herdr notifications for running and ended jobs.

### Optional compute-node tab actions

Creating an ordinary tab or split pane never runs `srun` automatically. This lets you decide
whether the pane stays on the login node, attaches without an agent hint, or attaches with
`HERDR_AGENT=codex` or `HERDR_AGENT=claude`.

The plugin exposes three explicit opt-in actions in a managed job Space:

```bash
herdr plugin action invoke io.github.quan-meng.herdr-slurm.new-shell-tab
herdr plugin action invoke io.github.quan-meng.herdr-slurm.new-codex-tab
herdr plugin action invoke io.github.quan-meng.herdr-slurm.new-claude-tab
```

The agent actions launch the agent directly on the compute node. For example, Codex uses:

```bash
HERDR_AGENT=codex srun --jobid=<job-id> --pty --overlap zsh -lic 'exec codex'
```

`HERDR_AGENT` is applied to the login-node `srun` process, allowing Herdr to identify the remote
agent and monitor its terminal state. Rename/customize agent commands through `agent_commands`.

## Configure

Find the per-user configuration directory:

```bash
herdr plugin config-dir io.github.quan-meng.herdr-slurm
```

The watcher creates `config.json` there on first start. Defaults:

```json
{
  "poll_interval_seconds": 5,
  "adopt_existing_jobs": false,
  "partitions": [],
  "job_name_pattern": ".*",
  "workspace_label": "{job_name} [{job_id}]",
  "output_tab_label": "output",
  "shell": ["zsh", "-l"],
  "srun_arguments": ["--pty", "--overlap"],
  "agent_commands": {
    "codex": ["zsh", "-lic", "exec codex"],
    "claude": ["zsh", "-lic", "exec claude"]
  },
  "focus_new_workspace": false,
  "notify": true
}
```

For only the A6000 submission partition:

```json
{
  "partitions": ["rtx_a6000_submit"]
}
```

Configuration is read when the watcher starts. Restart its tab after editing.

## Test

The test suite never contacts Slurm or Herdr:

```bash
python3 -m unittest discover -s tests -v
```

Then validate with a short real allocation:

```bash
sbatch --job-name=herdr-plugin-test \
  -p rtx_a6000_submit \
  --gres=gpu:1 \
  ~/interactive.sh
```

Use a short time limit for the first cluster test. The plugin will not adopt allocations that
were active before its first poll unless you invoke `adopt-current`.

## Limitations

- Compressed pending job-array ranges are ignored; concrete array tasks are supported when
  `squeue` exposes a concrete job ID.
- One watcher is supported per Unix account. With multiple named Herdr sessions, the first
  watcher to acquire the plugin lock owns reconciliation.
- The initial `output` tab follows files reported by Slurm. If a job redirects all output inside
  its script or uses `/dev/null`, Slurm may not expose content for the plugin to display.
- Manual `srun` shells are not labeled as coding agents unless `HERDR_AGENT` is set on the
  login-node `srun` process; the agent-specific actions set it automatically.

## Publish

The repository root contains `herdr-plugin.toml`, so tagged releases can be installed directly.
Add the GitHub topic `herdr-plugin` to make the repository discoverable in Herdr's marketplace.
