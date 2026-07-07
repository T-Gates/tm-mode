# tm-mode Scenarios

This folder is the tm-mode functional specification, organized as user journeys by action.

Each scenario is both the target flow and the specification. Implementation should be shaped against these documents so the dream state and current behavior converge.

| Action | Role | File | Status |
|---|---|---|---|
| Introducer onboarding | introducer | [onboard-introducer.md](onboard-introducer.md) | L1 setup + L2 service connection |
| Member onboarding | member | [onboard-member.md](onboard-member.md) | L1 setup + L2 service connection |
| Reset | introducer/member | [reset.md](reset.md) | Direct `install.py --uninstall` |

## Original Preface

# tm-mode Scenarios (Functional Specification)

> Skill naming note: this document calls the L1 setup skill **`tm-join`**. **In the current code, the skill directory and name are `tm-onboard`(`infra/skills/base/tm-onboard/`), and the rename to `tm-join` has been confirmed and will be applied soon.** Commands, output strings, and exit codes are all based on the current code(`infra/install.py`, `infra/install_lib.py`, `infra/teammode.py`).

## Role of This Document

This document is the **central functional specification** for tm-mode development. It specifies, from the user's perspective, "when the user acts this way, the system responds this way," and becomes the **reference point** for checking whether the implementation behaves according to the spec. If the goldens in `conformance/` provide machine verification for exact output strings and exit codes, this document is their **human counterpart**: a narrative that one person can follow from start to finish and judge whether the flow is correct. To keep planning, implementation, and QA aligned on the same picture, it records only behavior that is actually written in the code and docs, without guessing or invention.

The entry point is always **natural language**. Users do not memorize slash commands. When they say something like "이 레포 셋업해줘", the agent selects the appropriate skill(`tm-join`/`tm-connect`), calls `install.py`(the deterministic machine) on their behalf, and translates the result back into human language.

### Scenario Index

| # | Scenario | Core idea | Role determination |
|---|---|---|---|
| 1 | [Introducer](#시나리오-1--도입자) | The first person creating the team. No `team.config.json` → writes a new config | `introducer` |
| 2 | [Member](#시나리오-2--팀원) | Joins an already-created team repo. Valid config → read only, upsert only their own entry | `member` |

Each scenario has 3 phases:
- **Phase ① Repo clone** — the point where the user gets the repo and talks to the agent
- **Phase ② `tm-join` L1 setup** — `install.py` bootstrap(memory + hook wiring + verify)
- **Phase ③ `tm-connect` L2 service connection** — attaching services to role slots(issues/chat/docs/calendar)

Each step is written in a **four-beat** form: (a) exactly what the user types/runs → (b) what the agent says and what command it runs internally → (c) what actually appears in the terminal/screen(actual output string and exit code) → (d) what the user sees and what they do next.

> Notation: bracketed tags such as `[plan]`, `[scaffold]`, `[wire]`, `[env]`, `[verify]`, and `[done]` are prefixes that `install.py` actually prints to stdout. `[error]` and `[warn]` go to stderr.
