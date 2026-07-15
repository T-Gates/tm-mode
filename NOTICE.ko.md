<!-- NOTICE (Apache-2.0): tm-mode, Copyright 2026 T-Gates -->
[English](NOTICE.md) | **한국어**

# tm-mode 업데이트 공지

팀모드 관리자가 팀들에게 전달하는 최신 업데이트·공지 파일입니다.
`tm on` 시 upstream NOTICE 와 로컬이 다르면 자동으로 표시됩니다.

---

## 2026-07-15

- **더 안전한 자동 동기화**: 세션 로그 자동 커밋이 팀원의 동시 push를 먼저 reconcile한 뒤 제한된 범위에서 재시도하고, hook을 막는 대신 복구 가능한 pending 상태를 기록합니다.
- **SessionStart 부수효과 1회 실행**: 같은 root turn에서 반복되는 Codex `resume` 재구성과 대기 중인 `compact` 이벤트가 pull·relay·맥락 주입을 다시 실행하지 않습니다. Claude 세션과 실제 새 turn은 기존처럼 정상 실행됩니다.
- **동시 시작 race 수정**: 실제 동시 SessionStart에서도 claim lock이 한 번만 통과시켜, fail-open 복구를 유지하면서 hook 출력 중복을 막습니다.

## 2026-06-18

- **스킬 3계층(base/core/util)** 도입 + `tm-manage-utils`: util 스킬을 팀원별로 선택 설치·관리. `tm on` 시 core 자동 + 등록 util 심링크, `off` 시 제거(base 유지)
- **`tm` ON 자동 업데이트**: 팀모드 켤 때 upstream 엔진을 자동 동기화·커밋(`infra/`·`NOTICE.md`만, push는 수동). dirty면 skip
- **`tm-memory` 스킬**: 팀 메모리 INDEX 계층 로드(읽기 전용, 동적 발견)
- **`memory` 엔진 동사 + `tm-manage-memory` 스킬**: 메모리 추가·수정·삭제(frontmatter·INDEX·편집일 자동, 폴더 화이트리스트·traversal 가드)
- **KB 쓰기 거버넌스**: `memory/` 직접 편집(Write/Edit) 차단 — 메모리는 동사로만 (claude PreToolUse deny + unlock 플래그/TTL). ⚠️ Write/Edit 가드 한정 — Bash 우회는 막지 않음

## 2026-06-17

- `tm` 스킬 추가: 팀 모드 on/off 토글 (`infra/skills/base/tm/`)
- `tm-context` 스킬 추가: 팀 현황 빠른 조회 (`infra/skills/base/tm-context/`)
- cp949 한글 인코딩 P0 수정: Windows 환경에서 한글 출력 크래시 방지 (`io_encoding.py`)
- 배너 picker 추가: 6종 랜덤 배너 지원 (`infra/banners/`)
- `tm-mode update` 파일동기화: template unrelated histories 대응 (merge 대신 checkout 기반 동기화)
- hook hang 수정: 손자 프로세스(git-remote-https) killpg 로 일괄 종료
