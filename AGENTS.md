# AGENTS.md

## Commit Message Rule

Always write Git commit messages in English.

## Test Workflow Note

Until `1.0.0`, for test runs on Home Assistant, always do a clean plugin reinstall before verification:

1. Uninstall the `Loxone` integration/plugin from HA.
2. Install the integration/plugin again.
3. Only then run validation checks for entities, states, and behavior.

Do not skip this reinstall step, especially after entity mapping, discovery, or runtime-state changes.
