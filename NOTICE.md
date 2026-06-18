# teammode 업데이트 공지

팀모드 관리자가 팀들에게 전달하는 최신 업데이트·공지 파일입니다.
`tm on` 시 upstream NOTICE 와 로컬이 다르면 자동으로 표시됩니다.

---

## 2026-06-18

- **스킬 3계층(base/core/util)** 도입 + `tm-manage-utils`: util 스킬을 팀원별로 선택 설치·관리. `tm on` 시 core 자동 + 등록 util 심링크, `off` 시 제거(base 유지)
- **`tm` ON 자동 업데이트**: 팀모드 켤 때 upstream 엔진을 자동 동기화·커밋(`infra/`·`NOTICE.md`만, push는 수동). dirty면 skip
- **`tm-knowledge` 스킬**: 팀 지식 INDEX 계층 로드(읽기 전용, 동적 발견)
- **`knowledge` 엔진 동사 + `tm-manage-knowledge` 스킬**: 지식 추가·수정·삭제(frontmatter·INDEX·편집일 자동, 폴더 화이트리스트·traversal 가드)
- **KB 쓰기 거버넌스**: `memory/` 직접 편집(Write/Edit) 차단 — 지식은 동사로만 (claude PreToolUse deny + unlock 플래그/TTL). ⚠️ Write/Edit 가드 한정 — Bash 우회는 막지 않음

## 2026-06-17

- `tm` 스킬 추가: 팀 모드 on/off 토글 (`infra/skills/base/tm/`)
- `tm-context` 스킬 추가: 팀 현황 빠른 조회 (`infra/skills/base/tm-context/`)
- cp949 한글 인코딩 P0 수정: Windows 환경에서 한글 출력 크래시 방지 (`io_encoding.py`)
- 배너 picker 추가: 6종 랜덤 배너 지원 (`infra/banners/`)
- `teammode update` 파일동기화: template unrelated histories 대응 (merge 대신 checkout 기반 동기화)
- hook hang 수정: 손자 프로세스(git-remote-https) killpg 로 일괄 종료
