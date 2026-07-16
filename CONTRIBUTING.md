**English** | [한국어](CONTRIBUTING.ko.md)

# Contributing to tm-mode

Thanks for considering a contribution to **tm-mode** — a cross-agent team collaboration toolkit for AI coding agents (Claude Code · Codex). Start with [README.md](README.md) for what the project does and how it's laid out; this document covers how to contribute to it.

## 1. Two contribution paths

- **You're working inside a team instance** (a repo created from this template, e.g. via `tm-mode init`/`join`): the fastest path for a bug or improvement is the **`tm-contribute` skill** (`infra/skills/core/tm-contribute/SKILL.md`). Ask your agent to "report this upstream" — it diagnoses whether the problem is really a `tm-mode` bug (reproducible against `upstream/main`) or local instance drift, then files a GitHub issue on your behalf after you approve the draft. It does not open PRs; only issues. A team instance may also carry its own local files or directories on top of the template (extra scripts, integration servers, drafts, and so on) — those belong to that team and are never part of an upstream PR.
- **You want to contribute code directly**: fork the product repo, `T-Gates/tm-mode` (not a team instance's own `origin` — team instances typically track this repo as a git remote named `upstream`), and follow the flow below.

## 2. File an issue first (recommended)

Before writing code, open an issue on [T-Gates/tm-mode](https://github.com/T-Gates/tm-mode/issues) so the direction is agreed before a PR goes stale. Use the issue forms:

- **Bug report** (`.github/ISSUE_TEMPLATE/bug_report.yml`) — scoped to `tm-mode` itself (`infra/`); problems in a team's own `memory/` data are out of scope. It asks for environment, symptom, repro steps, expected vs. actual behavior, and your diagnosis (say "not yet diagnosed" rather than guessing).
- **Feature request** (`.github/ISSUE_TEMPLATE/feature_request.yml`) — problem/proposal/alternatives, plus a checkbox confirming the idea is implementable stdlib-only.

`.github/ISSUE_TEMPLATE/` is the single source for both forms — the title prefix (`[Bug]`/`[Feature]`), label (`bug`/`enhancement`), and field structure. The web UI applies these automatically; if you file via the `gh` CLI or an agent, read the template file first and match the same prefix, label, and fields yourself, since `gh issue create --body` doesn't render the form.

## 3. Dev environment

```bash
git clone https://github.com/<you>/tm-mode && cd tm-mode
pip install pytest                    # pytest isn't stdlib and isn't pinned by a lockfile here
python -m pytest -q                   # instance-distributed runtime/validation suite under tests/
python -m pytest -q maintainer_tests  # upstream-only release/docs/package contract suite; run both before submission
```

- Python **3.9+**. The product itself has zero runtime dependencies (`dependencies = []` in `pyproject.toml`) — git/gh are host prerequisites, not pip packages.
- The package doesn't need to be installed to develop or test: each test file manages its own `sys.path` (e.g. adding `infra/`, or `infra/hooks/` where a hook module is under test), so there's no `pip install -e .`, `uv sync`, or virtualenv step required.
- Commands above use bare `python`, matching this repo's own CI (`.github/workflows/test.yml`) and PR template. If your machine has no `python` alias (common on a plain Homebrew/python.org install on macOS), use `python3` instead.
- `gh` (GitHub CLI) is only needed if you're also exercising flows that create repos or issues (`tm-mode init`, `tm-contribute`).

## 4. Repository layout

**Product code** — what an upstream PR touches:

| Path | What it is |
|---|---|
| `src/teammode/cli.py` | Launcher — thin stdlib entry point shipped via pip/curl/npx (`tm-mode init`/`join`) |
| `infra/teammode.py` | Engine — verb dispatcher (`on`/`off`/`log`/`context`/`pull`/`commit`/`update`/`issue`/`memory`/`util`) |
| `infra/install.py` + `infra/install_lib.py` | Bootstrap — hook wiring, skill deploy, env injection; gated by `--dry-run`/`--yes` |
| `infra/git_ops.py` | Shared git operations + sync planning |
| `infra/agents/<name>/` | Per-agent adapters (Claude `settings.json`, Codex `config.toml`) |
| `infra/hooks/` | Shared hooks — session-start, auto-commit, push-worker, kb-write-guard, and others |
| `infra/skills/{base,core,util}/` | Skills in three tiers — see §6 for the activation rules |
| `infra/mcp/` | MCP OAuth helper code supporting L2 service connections |
| `infra/credentials.py`, `infra/i18n.py`, `infra/io_encoding.py`, `infra/providers.py`, `infra/workday.py` | Supporting engine modules |
| `infra/guidelines.md`, `infra/guidelines.en.md` | The "team-mode operating guidelines" text injected into agent sessions (Korean/English) |
| `infra/banners/`, `infra/migrations/`, `infra/scaffolds/` | Banner art, migration notes, and scaffold templates used when a new team repo is set up |
| `tests/` | Instance-distributed runtime/validation pytest suite, one `test_*.py` per feature/fix |
| `maintainer_tests/` | Upstream-only release/docs/package contract pytest suite |
| `conformance/check.py` | The `lint` / `verify` / `conform` checker |
| `conformance/scenarios/*.json` | 5 golden scenarios — the executable spec `verify`/`conform` run against |
| `docs/spec/` | **Single source of truth** for behavior (SPEC v0.4, English) |
| `docs/BACKLOG.md`, `docs/archive/`, `docs/scenarios/` | Design backlog, archived design notes, narrative onboarding scenarios |
| `providers/*.json` | L2 provider packs (issues/chat/docs/calendar) — data only, no code change needed to add one |
| `npm/` | npm publish shim (`npx tm-mode`) over the pinned `cli.py` |
| `.github/` | `CODEOWNERS`, CI workflows, PR template, issue templates |
| `LICENSE`, `NOTICE.md` | Apache-2.0 license; `NOTICE.md` is the maintainers' running update-announcement feed, diffed against your instance on `tm on` |
| `install.sh`, `INSTALL.md` | Human-facing curl installer and install reference |
| `team.config.example.json` | Template for the per-instance `team.config.json`, which is generated at setup and is itself instance-local |

**Instance-local** — a team's own data, never part of an upstream PR: `memory/` (that team's session logs and decisions — no product code path ever syncs or deletes it), `team.config.json`, `.teammode-active`. A team instance may also add its own local files or directories on top of the template for its own purposes; those aren't upstream code either.

For the full architecture map (component table, session data flow, design principles), see the "Architecture" section of [README.md](README.md).

## 5. Running tests and conformance checks

```bash
python -m pytest -q                                                          # instance-distributed runtime/validation suite under tests/
python -m pytest -q maintainer_tests                                         # upstream-only release/docs/package contract suite
python conformance/check.py lint    --root .                                 # static: manifest / events.json shape, no engine run
python conformance/check.py verify  --root . --engine "python infra/teammode.py"   # dynamic: run the 5 golden scenarios against our own engine
python conformance/check.py conform --root . --engine "<some other implementation>" # same scenarios, against a third-party engine, for advisory Tier scoring
```

Upstream contributors must run both pytest commands before submission. The default command intentionally collects only `tests/`, whose runtime/validation checks are distributed to team instances; `maintainer_tests/` stays upstream-only and checks release, docs, and package contracts. Each conformance flag is documented by `python conformance/check.py --help`; the golden scenario format is documented in `conformance/scenarios/README.md`. Add a test for any new behavior; for a bug fix, write the reproducing test first (red), then make it pass.

The `tests/` suite takes a few minutes, not seconds — budget for that rather than assuming a hang. CI tests both suites against Python 3.9 and 3.12 (`.github/workflows/test.yml`); if you're on a much newer interpreter and see many unrelated failures, try one of those versions before assuming a product bug. If `tests/test_install_l1b.py::test_bootstrap_exit3_when_no_name_resolvable` is the *only* test that fails for you, that's expected on a machine with a global `git config user.name` set (see the warning comment at the top of `.github/workflows/test.yml`) — it's an environment precondition, not a product bug.

Before pushing a PR, make the CI contract explicit:

- If the change touches runtime syntax, annotations, test helpers, CI, packaging, or git fixture setup, verify it under Python 3.9 as well as your local default. The project supports Python 3.9+, so `str | None`, `list[str]`-adjacent runtime behavior, `match`/`case`, and newer stdlib APIs are not acceptable unless they are guarded or avoided.
- If a test asserts "missing git config", color output, terminal behavior, `$HOME`, XDG paths, or environment variables, isolate that state inside the test. Do not rely on the developer machine being clean; contributors often have global `git user.name`, `NO_COLOR`, `TERM=dumb`, custom `HOME`, or shell-specific aliases.
- If a test creates a fake git remote, set the branch name explicitly (`git checkout -B main` and, for bare repos, `git symbolic-ref HEAD refs/heads/main`). GitHub Actions runners do not promise the same default branch name as a local machine.
- If a fixture needs a GitHub URL, keep it inside the allowed public vocabulary and run `tests/test_no_identity_leaks.py`. Case changes can matter: a string that looks like `USER@example.com` may still match the identity-leak guard.
- After merging or rebasing `origin/main`, rerun both pytest suites before pushing again. A previously green PR can become `DIRTY` or fail on the merge commit if another PR touched the same tests, docs, workflows, or shared helpers.

Release publishing is a separate gate from PR testing. `.github/workflows/publish.yml` runs only on `v*` tags and requires registry-side Trusted Publishing / package ownership for PyPI and npm. A PR is not considered broken just because an old tag publish failed, but before cutting a release tag the maintainer must confirm the PyPI publisher, npm package access, and tag/package versions match.

## 6. Code style and conventions

- **stdlib-only is an iron law**: `pyproject.toml`'s `dependencies = []` is a rule, not a default — don't add a runtime pip dependency. `git`/`gh` are host prerequisites, not pip deps. If an external library seems necessary, open an issue to discuss it before writing code.
- **Design principles**: the engine never judges — verbs are idempotent mechanics, summarizing/classifying is skills'/agents' job; hooks never kill a session — no raises, timeouts with `killpg` down to grandchild processes, failures are non-fatal and surfaced later; tests never touch the real host — never `~/.claude` or real remotes, only tmp + `--settings` isolation and faked remotes; instance data is inviolable — no product code path syncs or deletes `memory/`/`team.config.json`; distribution artifacts (`install.sh`, `cli.py`, the npx shim) are pinned to release tags so `main` stays free to move.
- **Skill tiers** (`infra/skills/{base,core,util}/`): `base` is always installed; `core` activates automatically whenever team mode is on and is inactive when `off` — it isn't something a team opts into; `util` is the actual opt-in layer, chosen per member.
- Stick to Python 3.9+ syntax (check `requires-python` in `pyproject.toml` before reaching for newer-only syntax like `match`/`case`).
- Note: a team instance's `memory/team/code-conventions.md`, if you find one, documents that *team's own product* (e.g. a separate backend it builds) — it has nothing to do with tm-mode's own code and doesn't apply here.

## 7. Code language — write code content in English

**Write the content of new or changed code — comments, docstrings, test code, identifiers, and assert messages — in English.** Migrating existing Korean comments to English when you touch that code is welcome, but it isn't a mandatory blanket pass.

- **User-facing runtime strings** (install output, hook-injected text, CLI messages) follow the **i18n (locale) system** instead of this policy — no hardcoded language switching.
- Docs default to English too: front-door docs are English-default with Korean coverage (a section or a sibling file, depending on the doc), and `docs/` — including the spec — is written and maintained in English. Korean inside code blocks (example configs, error strings) and quoted runtime strings are left as-is.

## 8. Public-repo hygiene — no real-environment identifiers (required)

This repo is a public product. **Don't put real people's, teams', or machines' identifiers in any file — including test fixtures, doc examples, and comments.** A pre-publish audit on 2026-07-07 found real member names, home paths, and team-instance repo references had leaked into fixtures in several places, requiring a full anonymization pass plus history cleanup. Don't repeat it.

**Forbidden**: real names or member handles, real email addresses, hardcoded home paths (`/Users/<real-account>/...`), references to team-instance repos or orgs (except the product repo `T-Gates/tm-mode` itself), and real team/product/business domain names.

**Allowed vocabulary (use only this)** — people: `alice`, `bob`, `jane-doe`; an edit-distance pair for near-duplicate testing: `jonathan`/`jonathon`; team/org: `acme`/`Acme`/`ACME`; repo: `acme/acme-team`; email: `user@example.com` (the personal-email-detection fixture uses `me@gmail.com`); paths: pytest's `tmp_path`, `~/...`, or `/Users/alice/...`.

The CI guard `tests/test_no_identity_leaks.py` mechanically blocks the general patterns (hardcoded home paths, emails, non-product-org repo references). If it flags something, swap the real value for the allowed vocabulary above — don't add an exception to the guard (exceptions are limited to extending the allowed vocabulary itself).

**Review checklist**: before committing real measured values pulled from dogfooding (paths, commands, hashes, logs), confirm you've swapped identifiers for the allowed vocabulary. Re-derive measured goldens (e.g. hash vectors) with the same algorithm after the substitution.

## 9. Commit style

Follow the existing history: `<type>(<scope>): <description>`. Scope is optional. Types in use: `feat` / `fix` / `docs` / `chore`. Description in Korean or English is both fine.

```
feat(memory): add `memory route {upsert|remove}` verb
fix(codex): pass TEAMMODE_MEMBER to Codex hooks
chore: switch license to Apache 2.0
```

## 10. Pull requests

1. Fork → branch (`fix/...`, `feat/...`) → PR against `main` of `T-Gates/tm-mode`.
2. Fill out `.github/PULL_REQUEST_TEMPLATE.md`: what/why, change list, test evidence (paste output from both `python -m pytest -q` and `python -m pytest -q maintainer_tests`), and the checklist (stdlib-only maintained, both suites pass). The web UI applies this template automatically; `gh pr create --body` does not, so match the same structure yourself if you file that way.
3. Keep the PR mergeable. If GitHub shows `DIRTY`, merge or rebase `origin/main`, resolve conflicts without dropping either side's behavior, rerun both pytest suites, and push a normal follow-up commit.
4. A maintainer reviews and merges, per `.github/CODEOWNERS`. Respond to review comments with follow-up commits rather than force-pushing over history mid-review.

## 11. Docs and i18n

- Front-door docs (`README.md`, `CONTRIBUTING.md`) are **English-canonical**. `README.md` carries its Korean translation as a section in the same file (anchor-linked at the top); `CONTRIBUTING.md` carries it as a same-structure sibling file, `CONTRIBUTING.ko.md`, cross-linked at the top of each — a deliberate, intentionally different i18n layout from README's, chosen for this file.
- **Sync rule**: a PR that changes `CONTRIBUTING.md` must update `CONTRIBUTING.ko.md` in the same PR (and vice versa).
- Internal specs (`docs/spec/`) and scenario docs (`docs/scenarios/`) are **English-default** — that migration is complete. A team instance's local copy may still lag in Korean; that's the instance's own translation debt, not upstream policy.

## License

tm-mode is distributed under the Apache License 2.0 — see [LICENSE](LICENSE). By submitting a contribution you agree it is licensed under the same terms (standard inbound=outbound; this repo has no separate CLA). `NOTICE.md` isn't a legal notices file so much as the maintainers' running update log; your instance shows you the diff against it automatically when you run `tm on`.
