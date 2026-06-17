# teammode 업데이트 공지

팀모드 관리자가 팀들에게 전달하는 최신 업데이트·공지 파일입니다.
`tm on` 시 upstream NOTICE 와 로컬이 다르면 자동으로 표시됩니다.

---

## 2026-06-17

- `tm` 스킬 추가: 팀 모드 on/off 토글 (`infra/skills/base/tm/`)
- `tm-context` 스킬 추가: 팀 현황 빠른 조회 (`infra/skills/base/tm-context/`)
- cp949 한글 인코딩 P0 수정: Windows 환경에서 한글 출력 크래시 방지 (`io_encoding.py`)
- 배너 picker 추가: 6종 랜덤 배너 지원 (`infra/banners/`)
- `teammode update` 파일동기화: template unrelated histories 대응 (merge 대신 checkout 기반 동기화)
- hook hang 수정: 손자 프로세스(git-remote-https) killpg 로 일괄 종료
