# 0001 — KB 쓰기 거버넌스 (작업 E)

날짜: 2026-06-18  
PR/커밋: (작업 E 첫 릴리스)

## 요약

"팀 메모리는 동사로만 쓴다(teammode 차별점)"를 PreToolUse 훅으로 강제한다.

에이전트가 `Edit`/`Write` 도구로 `memory/` 하위를 **직접 편집**하려 하면 차단 →
반드시 `python infra/teammode.py knowledge write …` 동사를 경유해야 한다.

> **범위**: Write/Edit 직접 편집 가드만 이 훅의 보장 범위다.
> Bash 등 다른 경로를 통한 우회는 현 범위 밖(별도 정책 필요).

## 변경 내역

### 신규: `infra/hooks/kb-write-guard.py`

PreToolUse 훅 스크립트.

- `memory/` 하위 경로를 타겟으로 하는 `file_edit` 액션을 감지
- unlock 플래그가 없거나 TTL 만료 시 `decision:"block"` 반환 (exit 2)
- `.teammode-active` 가드 — teammode off 상태에서는 비활성

### 변경: `infra/hooks/manifest.json`

```json
{
  "event": "PreToolUse",
  "match": { "action": "file_edit" },
  "script": "kb-write-guard.py",
  "timeout": 2,
  "fallback": "runtime",
  "enforcement": "block",
  "mode": "on"
}
```

- `mode: "on"` — teammode 활성 시에만 동기화
- `enforcement: "block"` — claude 강제, codex 폴백(events.json PreToolUse=null)

### 변경: `infra/skills/core/tm-manage-knowledge/SKILL.md`

절차 4 (엔진 동사 호출) 전후에 unlock 플래그 touch/rm 추가.

## unlock 플래그 규약 (견고화 후)

| 항목 | 값 |
|---|---|
| 기본 경로 | `$XDG_STATE_HOME/teammode/kb-unlock-<root_hash>-<session_id>` |
| 폴백 경로 | `$TMPDIR/teammode-kb-unlock-$USER-<root_hash>-<session_id>` |
| root_hash | 팀루트 절대경로 SHA-1 앞 8자리 (레포별 격리) |
| TTL | 300초 (5분) |
| 세션ID | `CLAUDE_SESSION_ID` 없으면 deny(fail-closed). 파일명 격리이므로 내용 검사 불필요. |
| git 추적 | 없음 (팀 루트 밖 머신 상태) |
| strict | manifest `strict: true` — normalize 변환 실패 시 exit 1 |

## 크로스에이전트

| 에이전트 | 처리 |
|---|---|
| claude | Write/Edit 직접 편집 가드(PreToolUse block). Bash 등 우회는 현 범위 밖. |
| codex | events.json PreToolUse=null → 훅 미등록, adapter가 "차단 강제 상실" [warn] 출력 |

codex 강제는 codex hook 커버리지 안정화 후 별도 백로그.

## install 반영

`adapter.py sync --on` 재실행 시 새 훅이 자동 등록된다.
기존 설치에 반영하려면 `python infra/install.py --root . --member-name <이름> --yes` 재실행.

## 롤백

manifest.json 에서 `kb-write-guard.py` 엔트리 제거 후 `sync --on` 재실행.
`infra/hooks/kb-write-guard.py` 삭제.
