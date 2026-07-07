---
name: tm-contribute
description: Use when the user wants to report a tm-mode product bug or improvement to the upstream repo (T-Gates/tm-mode) as a GitHub issue — diagnosing first whether it is a real upstream bug or the instance's own local breakage (avoids false issues). Triggers on "본레포에 올려", "이슈로 올려", "tm-mode에 기여", "버그 신고", "contribute", "업스트림에 알려", "report to upstream", "file an issue", "contribute to tm-mode", "report a bug".
---

# tm-contribute — Contribute to the tm-mode upstream repo (issue submission)

## Overview

Submit **product (infra/) problems or improvements** found while this instance
(this team) uses tm-mode to the upstream repo `T-Gates/tm-mode` as a
**GitHub issue**. Do not open a code PR directly — the instance team reports
"this problem + cause + our local fix" without permission or merge conflicts,
and the upstream maintainers decide whether and how to officially apply it.

**Key distinction**: this is not just issue submission. It **filters whether the
problem is truly an upstream repo problem**. It prevents false issues caused by
mistaking the instance's own broken local environment for an upstream bug.

## When to Use

- "이거 본레포에 올려", "이슈로 올려", "tm-mode 에 기여", "버그 신고", "업스트림에 알려"
- "report to upstream", "file an issue", "contribute to tm-mode", "report a bug"
- When a bug or improvement is found in tm-mode **itself (infra/)**
  (`memory/` team data issues are out of scope — resolve those inside this team)

## 0. What to contribute — summarize from the conversation context

Extract the following from the conversation and work so far (ask the user if
anything is missing):

- **Symptom**: what happened (error, malfunction, inconvenience)
- **Cause**: if analyzed, record it; if not, leave it as "unknown" — **do not guess**
- **Reproduction/context**: what task was underway, environment (agent, OS,
  instance), and reproduction steps

## 1. ★ Compare against upstream (core step — avoid false issues)

**Before** filing an issue, determine whether the problem comes from upstream or
from this instance.

```bash
git -C <팀루트> fetch upstream                  # Fetch latest upstream
git -C <팀루트> diff upstream/main -- infra/     # Local infra changes made by the instance
```

Decision:

| Observation | Decision | Action |
|------|------|------|
| The problem code is identical to the original in `upstream/main` (the instance did not touch it) | **Upstream bug** | Continue to step 2 |
| The problem also reproduces on the latest `upstream/main` | **Upstream bug** | Continue to step 2 |
| The instance broke it through local changes to `infra/` | **Instance fault** | Recommend `tm on` (sync) and **stop** |

- Compare the problem code directly with the upstream original:
  `git -C <팀루트> show upstream/main:infra/<파일>`.
- **Present the decision evidence to the user in the user's language and get
  consent before proceeding**
  (example: "This code is also present unchanged in upstream, so it appears to
  be an upstream bug — should I proceed?").
- If it is the instance's fault, do not file an issue; recommend sync instead.
  This is the reason this skill exists.

## 2. Draft the issue — the upstream template is the single source

**Read the upstream repo's issue template first and use that structure** (do not
invent a format):

```bash
git -C <팀루트> show upstream/main:.github/ISSUE_TEMPLATE/bug_report.yml       # Bug
git -C <팀루트> show upstream/main:.github/ISSUE_TEMPLATE/feature_request.yml  # Feature request
```

Use these from the template: **title prefix** (bug `[Bug] ` / feature request
`[Feature] `), **label** (`bug` / `enhancement`), and **body field structure**.
Current `bug_report` fields (if the template changes, the template wins):

```
Title: [Bug] <one-line summary — what is wrong>

### Environment
Agent (Claude Code/Codex), OS, Python, tm-mode version/commit

### Symptom
Describe exactly what happened (raw error message/log text).

### Reproduction steps
1) ... 2) ... 3) ...

### Expected behavior / actual behavior
(Use separate sections for each.)

### Cause / diagnostic evidence
If analyzed, include code-line evidence. If unknown, honestly write "unknown".
Also include the upstream comparison result from step 1 (whether there were local
changes).

### Applied fix (optional)
If fixed locally, clean up and attach the git diff from step 1; otherwise omit.
```

## 3. Human confirmation gate (required)

Issues are **public externally**. Before submitting, show the user the **full
draft** in the user's language and get approval. Do not submit without approval.

## 4. Create the issue with gh

> ⚠️ `gh issue create --body` **does not apply yml issue templates** (they are
> for the web form only) — use the title prefix and body structure matched to
> the template in §2, and **set the label manually**.

```bash
gh issue create --repo T-Gates/tm-mode \
  --title "[Bug] <제목>" --label bug --body "<§2 템플릿 구조의 본문>"
```

Report the created issue URL to the user in the user's language.

## Non-goals / Boundaries

- **Do not open a code PR directly** — only file an issue. Applying code is the
  upstream maintainers' responsibility.
- **Do not submit without diagnosis** — reporting instance fault (local
  contamination) as an upstream bug creates a false issue.
- **`memory/` (team data) issues are out of scope** — only `infra/` (product).
- Duplicate issue search is v2 (for now, the human decides before submission).

## Common Mistakes

- Skipping diagnosis and misreporting local contamination as an upstream bug →
  false issue.
- Writing only "doesn't work" and omitting symptom, cause, context, and
  environment → upstream cannot reproduce it.
- Stating a guessed cause as fact → honestly leave it as "unknown".
- **Submitting in an arbitrary format without reading the upstream issue
  templates (`.github/ISSUE_TEMPLATE/`)** → title prefix ([Bug]/[Feature]),
  label, and field structure drift, forcing maintainers to rewrite it.
  Remember that the gh CLI does not automatically apply yml templates (§2, §4).
