# 백로그: KB 쓰기 거버넌스 (teammode L1 메모리 차별점)

착안: 2026-06-15 (acme-toolkit 실작업 중 체감). 상태: **설계 메모, 미구현 — 다음에 구현**.

## 문제
`memory/` 파일을 직접 `Edit`/`Write`하면 INDEX 갱신·커밋·알림 등 일관 절차가 누락된다. 실제로 acme 작업 중 서브에이전트·메인이 직접 Edit해서 매번 절차가 빠지는 일이 반복됨. → "팀 메모리는 동사(스킬)로만 쓴다"를 **강제**할 필요.

## 해법 (옵션 B = 플래그형, 채택)
`memory/` 직접 쓰기를 막고 **메모리 관리 스킬 경유 쓰기만** 허용.
- **PreToolUse 훅**: `Edit`/`Write` 타겟이 `memory/` 경로 + unlock 플래그 없으면 → `deny` + "관리 스킬을 쓰세요" 안내.
- **메모리 관리 스킬**: 시작 시 unlock 플래그 `touch` → 완료(커밋 후) `rm`.

## 검토한 대안 (접은 이유)
- **A. 경고형** (차단 X, 경고만): soft — 우회 가능, 강제력 없음. 기각.
- **C. 스크립트 단일화** (`memory/` 쓰기를 전용 동사 `kb-write`로만, Edit/Write는 무조건 차단): 가장 견고하나 관리 스킬 대수술 필요. 과함.
- **B 채택**: 의도(스킬로만) 충족 + 구현 가벼움(훅 1개 + 스킬에 touch/rm 2줄).

## ⚠️ 취약점 · 가드
- 핵심 약점: 스킬이 중간에 비정상 종료하면 unlock 플래그가 **잔류 → 영구 unlock**(이후 아무 직접 Edit이나 통과).
- 가드안:
  1. 플래그에 **세션 ID 매칭** 또는 **TTL**(예: 플래그 mtime이 N분 지나면 무효).
  2. (Claude Code 한정) PreToolUse 훅이 직전 `Skill` 호출을 transcript로 확인 — 단 크로스에이전트 이식성 떨어짐. teammode는 에이전트 무관이 원칙이라 **플래그+TTL/세션ID가 정답**.

## teammode 편입 위치
- L1 메모리 시스템(`spec/01-team-memory.md`)에 "쓰기 거버넌스" 절로 편입.
- 차별점 메시지: **"팀 메모리는 동사로만 쓴다"** = 여러 에이전트·팀원이 만져도 INDEX/커밋/알림 일관성이 코드로 보장됨. L1 delight 후보.

## 구현 시 체크
- [ ] PreToolUse 훅 추가 (`infra/hooks/` + install.sh 등록 + migration 1장)
- [ ] 메모리 관리 스킬에 unlock touch/rm + 플래그 TTL/세션ID 가드
- [ ] 직접 Edit 차단 시 안내문(스킬명·우회금지 사유)
- [ ] conformance 테스트(직접 Edit deny / 스킬경유 allow)
