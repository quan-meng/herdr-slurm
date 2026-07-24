# Changelog

## 0.1.4

- Leave ordinary new tabs and split panes on the login node so users can choose their own `srun` and optional `HERDR_AGENT` hint.
- Keep shell, Codex, and Claude plugin actions as explicit opt-in compute-node attachments.

## 0.1.3

- Automatically attach split panes in managed job Spaces to the job's compute node with `srun`.
- Preserve agent-specific behavior for new Codex and Claude tabs without double-launching them.

## 0.1.2

- Support the Python 3.8 runtime provided by older cluster login nodes.

## 0.1.1

- Show the batch task's live Slurm stdout and stderr in each job Space's initial `output` tab.
- Keep automatic `srun` attachment for every additional shell, Codex, or Claude tab.

## 0.1.0

- Detect new pending and running Slurm allocations.
- Create one Herdr workspace and shell tab per allocation.
- Wait for pending jobs before attaching with an interactive `srun` step.
- Persist reconciliation state and avoid duplicate workspaces.
- Provide an explicit action for adopting jobs that predate the watcher.
- Automatically attach ordinary tabs created inside managed job Spaces.
- Provide shell, Codex, and Claude tab actions with Herdr agent hints.
