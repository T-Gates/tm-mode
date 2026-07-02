---
name: tm-manage-memory
description: Use when adding, updating, or deleting memory in the team memory base. Triggers on "메모리 추가해", "메모리 수정", "메모리 삭제", "KB 업데이트", "메모리에 저장".
---

# tm-manage-memory — 팀 메모리 베이스 CRUD

## Overview

`memory/` 하위 메모리 파일의 추가/수정/삭제를 처리한다.
**판단은 이 스킬이, 기계는 엔진 `memory` 동사가** 담당한다(구현 깊이 B).
스킬은 직접 파일을 쓰거나 INDEX를 편집하지 않는다 — 반드시 엔진 동사를 경유한다.

## When to Use

- "메모리 추가해", "KB에 저장", "메모리에 저장"
- "~ 수정해", "~ 업데이트해" (메모리 파일 대상)
- "~ 삭제해", "~ 지워" (메모리 파일 대상)
- 대화에서 도출된 내용을 메모리 베이스에 반영할 때

## 대상 범위

| 폴더 | 대상 | 비고 |
|------|------|------|
| `product/` | O | 제품 관련 메모리 |
| `team/` | O | 컨벤션, 그라운드룰, 멤버 |
| `team/decisions/` | O | 팀 결정사항 |
| `soma/` | O | 소마 과정 관련 정보 |
| `team/sessions/` | X | 자동 누적 (훅) |
| `team/meeting/` | X | → tm-context |

## 메타데이터 규약

모든 메모리 파일에 YAML frontmatter(엔진이 자동 스탬프):

```yaml
---
created_at: 2026-06-18
updated_at: 2026-06-18
author: eunsu
weight: 🔥          # 🔥 핵심 / 📌 중요 / 📎 참고
---
```

**weight 규약 (핵심)**:
- `🔥 핵심`: 팀이 자주 참조하는 안정 메모리
- `📌 중요`: 작업에 영향을 주는 정보
- `📎 참고`: 배경 메모리 / 이력
- **에이전트가 임의로 추측해 박지 않는다** — 반드시 사용자에게 확인.
  사용자가 "알아서 해"라고 하면 그때만 맥락 기반 자동 추정 가능.

## 절차

### 1. 의도 판단

사용자 메시지에서 **동작**과 **대상**을 파악한다.

| 패턴 | 동작 |
|------|------|
| "~에 ~추가해", "KB에 저장", 파일명 없이 내용 제공 | **추가** |
| "~ 업데이트해", "~ 수정해", 기존 파일명 언급 + 변경 내용 | **수정** |
| "~ 삭제해", "~ 지워" | **삭제** |

대상이 모호하면 `memory/INDEX.md`를 읽어 폴더 목록을 제시하고 선택받는다.

### 2. 폴더 라우팅 (추가 시)

INDEX.md의 폴더 설명을 대조하여 적절한 폴더를 자동 추천한다.

```
📂 이 내용은 team/decisions/ 에 넣으면 적절해 보여요.
   맞으면 "ㅇ", 다른 폴더면 알려주세요.
```

대상 폴더는 아래 중 하나여야 한다:
- `product/`, `team/`, `team/decisions/`, `soma/`

### 3. 파일명 + 가중치 결정 (추가 시)

파일명은 kebab-case, 내용을 요약하는 이름으로 제안 후 확인.
가중치는 **반드시 사용자에게 물어 확정** — 추측 금지.

```
📄 파일명: api-auth-flow.md
   가중치: 📌 중요 (🔥 핵심 / 📌 중요 / 📎 참고)
   맞으면 "ㅇ", 바꿀 게 있으면 알려주세요.
```

### 4. 엔진 동사 호출

확인 완료 후 엔진 동사를 호출한다.

> **unlock 플래그에 대한 정직한 설명**: 엔진 `memory` 동사는 별도 프로세스 `open()` 이라
> PreToolUse 훅 대상이 아니므로 엔진 경유 자체에는 unlock 이 필요 없다. 스킬이 직접
> `Write`/`Edit` 도구로 `memory/` 를 건드리는 경우에만 unlock 이 필요하다 — 이 스킬은
> 그런 경우가 없으므로 unlock 창을 열지 않는 것이 원칙이다(직접 편집 창 불필요).
> 아래 4-0/4-2 단계는 예외적 수동 편집이 필요한 경우를 위한 참조 구현이다.
> **정상 흐름(엔진 경유)에서는 4-0/4-2 를 실행하지 않는다.**

#### 4-0. unlock 플래그 touch (시작)

플래그 파일명에 **root_hash(팀루트 SHA-1 앞 8자리) + session_id** 를 포함해 레포별·세션별 격리.
`CLAUDE_SESSION_ID`(또는 `CLAUDE_CODE_SESSION_ID`) 가 없으면 훅이 fail-closed 로 deny 하므로, 세션ID 없이 스킬을 실행하면 안 된다.

```python
# 플래그 경로 결정 (kb-write-guard.py 와 동일 규약)
import hashlib, os, pathlib

team_root = os.environ.get("TEAMMODE_HOME", "")
if not team_root:
    raise RuntimeError("TEAMMODE_HOME 이 설정되지 않음 — unlock 불가")

session_id = os.environ.get("CLAUDE_SESSION_ID") or os.environ.get("CLAUDE_CODE_SESSION_ID", "")
if not session_id:
    raise RuntimeError("CLAUDE_SESSION_ID / CLAUDE_CODE_SESSION_ID 가 없음 — 훅이 deny 하므로 unlock 불가")

rh = hashlib.sha1(team_root.encode()).hexdigest()[:8]
suffix = f"{rh}-{session_id}"

xdg = os.environ.get("XDG_STATE_HOME")
if xdg:
    flag = pathlib.Path(xdg) / "teammode" / f"kb-unlock-{suffix}"
else:
    tmpdir = os.environ.get("TMPDIR") or os.environ.get("TMP") or "/tmp"
    user = os.environ.get("USER") or os.environ.get("USERNAME") or "user"
    flag = pathlib.Path(tmpdir) / f"teammode-kb-unlock-{user}-{suffix}"

flag.parent.mkdir(parents=True, exist_ok=True)
flag.write_text("", encoding="utf-8")  # 내용 불필요 — 파일명으로 격리
```

또는 bash 한 줄:

```bash
python - <<'EOF'
import hashlib, os, pathlib
team_root = os.environ.get("TEAMMODE_HOME", "")
session_id = os.environ.get("CLAUDE_SESSION_ID") or os.environ.get("CLAUDE_CODE_SESSION_ID", "")
if not team_root or not session_id:
    raise RuntimeError("TEAMMODE_HOME 또는 CLAUDE_SESSION_ID / CLAUDE_CODE_SESSION_ID 가 없음")
rh = hashlib.sha1(team_root.encode()).hexdigest()[:8]
suffix = f"{rh}-{session_id}"
xdg = os.environ.get("XDG_STATE_HOME")
flag = (pathlib.Path(xdg) / "teammode" / f"kb-unlock-{suffix}") if xdg else \
       (pathlib.Path(os.environ.get("TMPDIR", "/tmp")) / f"teammode-kb-unlock-{os.environ.get('USER','user')}-{suffix}")
flag.parent.mkdir(parents=True, exist_ok=True)
flag.write_text("", encoding="utf-8")
EOF
```

#### 4-1. 엔진 동사 호출

**추가/수정:**
```bash
python infra/teammode.py memory write \
  --root . \
  --folder <폴더> \
  --filename <파일명.md> \
  --content "<내용>" \
  --author <현재사용자> \
  --weight "<가중치>"
```

**삭제 (삭제 전 사용자 재확인 필수):**
```bash
python infra/teammode.py memory delete \
  --root . \
  --path <memory/상대경로> \
  --author <현재사용자>
```

엔진이 처리하는 것(스킬이 직접 하면 안 됨):
- frontmatter 스탬프 (created_at/updated_at/author/weight)
- 파일 write/delete
- INDEX.md 행 upsert/제거
- 편집일 계산 (메타 커밋 제외한 본문 커밋 기준)
- do_commit(paths 한정, push=False)

#### 4-2. unlock 플래그 rm (커밋 완료 후)

엔진 동사가 정상 완료(커밋 포함)된 **직후** 플래그를 제거한다.
(4-0에서 생성한 `flag` 변수를 재사용하거나 동일 규약으로 경로를 재계산한다.)

```python
try:
    flag.unlink()
except OSError:
    pass  # 이미 없으면 무시
```

또는:

```bash
python - <<'EOF'
import hashlib, os, pathlib
team_root = os.environ.get("TEAMMODE_HOME", "")
session_id = os.environ.get("CLAUDE_SESSION_ID") or os.environ.get("CLAUDE_CODE_SESSION_ID", "")
if not team_root or not session_id:
    raise RuntimeError("TEAMMODE_HOME 또는 CLAUDE_SESSION_ID / CLAUDE_CODE_SESSION_ID 가 없음")
rh = hashlib.sha1(team_root.encode()).hexdigest()[:8]
suffix = f"{rh}-{session_id}"
xdg = os.environ.get("XDG_STATE_HOME")
flag = (pathlib.Path(xdg) / "teammode" / f"kb-unlock-{suffix}") if xdg else \
       (pathlib.Path(os.environ.get("TMPDIR", "/tmp")) / f"teammode-kb-unlock-{os.environ.get('USER','user')}-{suffix}")
try:
    flag.unlink()
except OSError:
    pass
EOF
```

> 비정상 종료(오류·인터럽트)로 플래그가 잔류해도 TTL(5분) 이후 자동 만료된다.

### 5. 양방향 백링크 (엔진 자동 — 확인만)

엔진이 memory 변경 직후 **기계적으로** 양방향 링크를 건다(스킬은 아무것도 안 함):

- **세션로그 → 문서**: 현재 author 의 오늘 세션로그에 `📝 생성/✏️ 수정/🗑️ 삭제: [[<경로>]]` 한 줄 append.
- **문서 → 세션로그**: 쓰는 문서 frontmatter 에 `session: team/sessions/<author>/<작업일>.md` 필드 추가.

멱등(재수정 시 중복 줄·필드 없음)·비차단(백링크 실패해도 memory 변경은 유지). 스킬은 결과만 확인한다.

### 6. chat 통지 (chat 슬롯 연결 시)

memory 변경이 **정상 완료된 후**, `team.config.json` 의 `services.chat` 가 연결돼 있으면
**AI 가 chat 슬롯의 벤더 MCP 도구를 직접 호출**해 팀에 통지한다(A안 — 엔진은 MCP 호출 안 함).

- 엔진은 통지용 한 줄 요약을 stdout 에 `[chat-notify] memory 추가/수정/삭제: <경로> · weight=… · author=… · 요약=…` 형태로 출력한다.
  AI 는 이 줄을 받아 통지 메시지(작업·파일경로·weight·작성자·첫줄 요약)를 구성한다.
- 모든 변경(추가/수정/삭제)을 통지한다(필터 없음).
- chat 슬롯이 연결돼 있지 않으면(`services.chat` 없음) 통지를 건너뛴다.
- **비차단(advisory)**: 통지 호출이 실패해도 memory 변경은 그대로 유지한다 — 오류만 보고하고 멈추지 않는다.

> chat 슬롯의 실제 벤더 MCP 도구명·채널 지정은 `tm-connect` 가 연결할 때 config 에 기록된 provider 를 따른다.

### 7. 완료 보고

```
✅ team/decisions/api-auth-decision.md 추가 완료
   INDEX 갱신 · 커밋 완료 (push는 별도)
   세션로그 백링크 · chat 통지 완료
```

## 새 최상위 폴더 등재 — 루트 라우팅 맵 (route)

루트 `memory/INDEX.md`(2열 라우팅 맵)는 세션마다 주입되는 단일 진입점 —
**새 최상위 폴더를 만들면 여기 등재가 필수**다. 등재/해제도 엔진 동사를 경유한다:

```bash
python infra/teammode.py memory route upsert \
  --root . --path soma/ --desc "소마 과정 관련 정보" --author <현재사용자>
# 해제: memory route remove --root . --path soma/ --author <현재사용자>
```

- `memory write` 가 미등재 폴더를 감지하면 `[hint] '...'가 루트 INDEX에 미등재 — 등록: ...`
  한 줄을 출력한다 — 그 명령을 그대로 따라 하면 된다(`--desc` 한 줄만 사용자와 확정).
- `--desc` 는 추측 금지 — 라우팅 맵 품질이 매 세션 주입 품질이므로 사용자에게 확인한다.

## 안 하는 것 (L1)

- verification-sources 매니페스트 등록 (선택, 후속)
- 세션 로그 관리 (→ 훅 자동)
- 회의록 관리 (→ tm-context)
- 메모리 읽기/로드 (→ tm-memory)
- 이슈 트래커 / 문서 도구 / 일정 관리 API 호출
- 파일 직접 편집 (반드시 memory 동사 경유)

## INDEX 포맷 (참고)

엔진이 자동 유지하는 형식:

```
> 가중치: 🔥 핵심 · 📌 중요 · 📎 참고

| 가중치 | 경로 | 내용 | 편집일 |
|--------|------|------|--------|
| 📌 | `memory/team/decisions/api-auth-decision.md` | API 인증 방식 결정 | 2026-06-18 |
```

- **편집일**: 본문이 실제로 바뀐 마지막 커밋 날짜 (메타/INDEX/weight 커밋 제외)
- tm-memory 스킬이 이 INDEX로 "무엇이 어디 있는지"를 파악한다
