---
version: 3
affects: [hook, engine, settings, docs]
summary: kb-write-guard 철거 — "메모리는 동사로만 쓴다"를 강제에서 권장으로
---

`memory/` 직접 편집을 막던 PreToolUse 훅 `kb-write-guard` 와, 그 훅에만 딸려 있던
`memory unlock` 동사·SessionStart 세션 relay 를 전부 걷는다.

`0001-kb-write-governance.md` 가 세운 규칙(`memory/` 는 동사로만)과
`0002-session-log-freedom.md` 가 낸 예외(본인 세션로그)는 **둘 다 이 마이그레이션으로 끝난다.**
규칙 자체가 사라졌으니 예외도 필요 없다.

## 왜 걷었나

**결정적 이유는 편집 뮤텍스의 범위 결함이다.**

가드는 두 가지를 했다 — ① `memory/` 직접 편집 차단(경로로 판정) ② 팀 전역 편집 뮤텍스 획득.
그런데 ②가 쓰는 팀 루트는 **편집 대상 경로가 아니라 `__file__` 기준으로 정적 계산**됐다.

```python
root = _team_root()          # __file__ 기준. 무엇을 편집하는지 안 본다
...
acquired = _git_ops.acquire_edit_mutex(root, token)
```

결과: 팀 레포와 아무 상관 없는 파일(개인 문서 등)을 고칠 때도 팀 전역 뮤텍스를 잡았다.
세션 여러 개가 같은 팀 루트를 공유하면 서로를 `edit mutex busy` 로 튕겼고,
실패 경로에서 release 를 부르는 훅(`PostToolUseFailure`)이 배선돼 있지 않은 설치본에서는
TTL(15분)이 지날 때까지 **모든 세션의 모든 Edit/Write 가 멈췄다.**

부차적으로, 가드가 보장하던 것 자체가 좁았다 — `Bash` 경유 `memory/` 쓰기는 처음부터
범위 밖이었고(설계에 명시), Codex headless 는 Trust 미승인 시 훅을 건너뛸 수 있었다.
막을 수 있는 경로만 막는 강제였다.

## 무엇이 바뀌나

| | 이전 | 이후 |
|---|---|---|
| `memory/` 직접 Edit/Write | 차단(본인 세션로그만 예외) | **허용** |
| `memory unlock begin\|end` | 편집 창을 여는 동사 | **없음** (호출하면 unknown action) |
| 편집 뮤텍스 | 모든 Write/Edit 이 획득 | **아무도 획득하지 않음** |
| "동사로 쓴다" | 훅으로 강제되는 규칙 | `infra/guidelines.md` 의 **권장** |

동사(`tm-manage-memory` → `python infra/teammode.py memory write …`)를 거치면
frontmatter 스탬프·INDEX 행·커밋·백링크가 같이 간다는 사실은 그대로다.
직접 편집을 고르면 그 절차를 손으로 대신해야 한다.

## 인스턴스가 할 일

코드는 `tm-mode update`(pull)로 반영된다. 그 뒤 어댑터 재동기화로
`settings.json`(Claude) / `config.toml`(Codex) 에서 훅 등록이 빠진다.

수동으로 확인하려면 — Claude 는 `PreToolUse` 의 `Write|Edit` 매처가 사라졌는지,
Codex 는 `[[hooks.PreToolUse]]` 에 `kb-write-guard.py` 가 없는지 본다.

```bash
grep -c kb-write-guard ~/.claude/settings.json   # 0 이어야 한다
```

`$XDG_STATE_HOME/teammode/` 에 남은 `kb-unlock-*` 플래그와 `sessions/` relay 파일,
그리고 `edit-mutex-*` 마커는 아무도 읽지 않는다 — 지워도 되고 둬도 된다.

## 남긴 것

- `git_ops.py` 의 뮤텍스 배관(`acquire_edit_mutex` 등)과 `auto-commit`·`edit-lease-cleanup`
  의 release 호출은 그대로 뒀다. 획득자가 사라져 `edit-lease-cleanup` 은 사실상 무동작이며,
  걷는 범위가 auto-commit 까지 번지므로 이번 판에서 분리했다.
- `0001`·`0002` 마이그레이션 문서는 **역사 기록이라 손대지 않았다.**
- `tests/test_codex_trust_check.py` 의 `LIVE_*_KB_GUARD` 는 2026-07-03 실측 golden 벡터라
  그대로 둔다(합성 픽스처이므로 스크립트 실존이 필요 없다).

## 되돌리려면

이 커밋을 revert 하면 훅·동사·relay·테스트가 함께 돌아온다.
다만 되돌리기 전에 뮤텍스 범위부터 고쳐라 — `_begin_edit_mutex` 가 `__file__` 이 아니라
**편집 대상 경로**로 팀 루트를 판정해야 하고, 실패 경로 release 가 배선돼 있어야 한다.
