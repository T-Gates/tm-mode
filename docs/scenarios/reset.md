# Reset Scenario

This appendix covers host rollback(uninstall).

---

## Appendix: Reset (Direct `install.py --uninstall`)

Cleaning up a scratch test repo or rolling back the host is done by directly running **`python infra/install.py --uninstall --root . --yes`**. **Because this is destructive, get human confirmation first.** off(delete `.teammode-active` marker + sync off) → adapter uninstall(remove settings.json hooks while preserving others' hooks) → remove env lines(only our marker lines) → unregister Obsidian(only the relevant vault). All steps are idempotent and nonfatal. **Never delete `memory/`(team data).** Without `--root`, exit **2**; without either `--yes` or `--settings`, reject real-host changes with exit **2**. The scratch repo folder itself remains, so a person does full cleanup with `rm -rf <repo>`.
