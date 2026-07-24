# Changelog

## 0.1.0

- Detect new pending and running Slurm allocations.
- Create one Herdr workspace and shell tab per allocation.
- Wait for pending jobs before attaching with an interactive `srun` step.
- Persist reconciliation state and avoid duplicate workspaces.
- Provide an explicit action for adopting jobs that predate the watcher.
- Automatically attach ordinary tabs created inside managed job Spaces.
- Provide shell, Codex, and Claude tab actions with Herdr agent hints.
