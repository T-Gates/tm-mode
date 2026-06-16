# teammode 문서 빌드 지시문 (무인 실행용 — 2026-06-12 04:30 예약)

너는 teammode 프로젝트의 문서 작성 에이전트다. 아래 산출물 2종을 만들고, 각각 에이전틱 검증 루프를 거쳐 완성하라. 모든 문서는 한국어.

## 필독 소스 (작업 전 전부 읽어라)

1. `/home/jane-doe/work/extras/acme/teammode/toolkit-oss-separation-plan.md` — 프로젝트 전체 계획
2. `/home/jane-doe/work/extras/acme/teammode/teammode-adapter-spec-draft.md` — 어댑터 스펙 초안 (오늘 밤 설계의 본체)
3. `/home/jane-doe/work/extras/acme/teammode/team.config.example-draft.json` — config 스키마 초안
4. `/home/jane-doe/work/extras/acme/acme-toolkit/memory/team/sessions/jane-doe/2026-06-11.md` — 설계 세션 로그 (특히 "teammode" 관련 항목 전부)
5. `/home/jane-doe/work/extras/acme/acme-toolkit/CLAUDE.md` — 현행 툴킷 구조
6. `/home/jane-doe/work/extras/acme/acme-toolkit/infra/hooks/manifest.json` + `sync.py` — 현행 훅 실물

## 산출물 A — 스펙 문서 3장 (`teammode/spec/` 폴더에)

1. **`spec/01-team-memory.md`** — 팀 메모리 표준
   - 세션로그 포맷: frontmatter(author/date/**summary 한 줄 — 신규 필드**), 하루 1파일, 06시 컷, 상세도 기준
   - memory/ 폴더 구조와 각 폴더의 의미 (toolkit memory/INDEX.md 참조)
   - 스케일 규칙: ~4인 전문 주입 / 5인+ summary 주입, groups 예약어
   - 버저닝: spec_version 0.1, 변경 절차
2. **`spec/02-hook-manifest.md`** — 훅·어댑터 표준 (draft를 정식판으로 재구성)
   - draft의 §0~§8을 다듬어 정식 스펙 체재로 (용어→선언 포맷→번역표→어댑터 계약→normalize 계약→폴백→스킬 오버라이드→서비스 추상화)
   - draft의 §11.x(구현 노트)는 스펙이 아니므로 별도 부록이나 제외
3. **`spec/03-conformance.md`** — 호환 선언 절차
   - 독립 구현이 "teammode 호환"을 선언하는 조건, conformance kit 구상(acme-lint 일반화), Implementations 등재 절차, 배지

## 산출물 B — 영업 문서 (`teammode/pitch-draft.md`)

**대상**: 스타트업 운영하는 친구 (개발팀 리더, 내일 대면 미팅). 목적: teammode 도입 설득.
**분량**: A4 2장 이내. 표와 불릿 중심, 산문 최소화.

반드시 담을 강점 (Jane 지정):
1. **쉽고 친절한 도입** — 온보딩 = 설정 마법사 (대화로 슬롯 채움, config 손 편집 0, 토큰 심부름 ~10분만 사람 몫)
2. **크로스에이전트** — 팀원마다 다른 에이전트(Claude Code/Codex/Hermes)여도 한 팀 메모리 공유 (혼성 팀)
3. **정량적으로 산정 가능한 작업량** — 세션로그가 자동 누적되므로 누가 뭘 얼마나 했는지가 데이터로 남음 (주의: 감시 도구 뉘앙스가 되지 않게 — "보고 비용 제로" 프레임으로)
4. **의사결정 과정 확인** — 결정의 근거·접은 대안까지 추적 가능 ("그 결정 왜 했더라"의 종말)
5. **도구 통합** — issues/chat/docs/calendar 슬롯에 자기 도구 꽂기 (Linear든 Jira든), 빈 슬롯 허용

추가로: 정직한 기대치(첫 주는 귀찮음, 2주차부터 복리 — Acme fixture 6주 스토리), 우리가 매일 쓰는 물건이라는 점(독푸딩), 설치 흐름 3줄 예시.
하지 말 것: 과장("혁신적인", "게임체인저" 류 금지), 아직 없는 기능을 있는 것처럼 쓰기 (대시보드·충돌 레이더는 "로드맵"으로 명시).

## 에이전틱 검증 루프 (문서별 필수)

각 문서에 대해:
1. 작성 완료 후 **검증 서브에이전트**(Task 도구) 1기를 띄운다. 검증 관점:
   - 사실 정합성: 소스 문서(스펙 draft·세션로그)와 어긋나는 서술, 없는 기능을 있다고 쓴 곳
   - 영업 문서는 추가로: 설득력(친구 입장에서 도입 결심이 서는가), 분량, 과장 여부
   - 스펙 문서는 추가로: 모호한 규정(구현자가 다르게 해석할 수 있는 문장), 누락
2. 지적사항을 반영해 수정한다.
3. 재검증 → 반영 → … **문서당 최대 3라운드**. 지적 0이 되면 조기 종료.
4. 라운드별 지적·반영 내역을 `teammode/build-report.md`에 누적 기록.

## 모델

- 메인·검증 서브에이전트 전부 **모델 지정 금지** — 세션 기본값(Fable)을 그대로 상속한다 (Jane 지정: "전부 페이블").

## 마감 규칙

- 파일 작성은 Read/Write/Edit만 사용. git 커밋 금지(Jane가 검토 후 직접).
- 완료 후 `teammode/build-report.md` 맨 위에 결과 요약(산출 파일 목록, 라운드 수, 남은 우려)을 쓴다.
- 총 작업이 산출물 기준 2시간을 넘기지 않게 — 검증 루프가 수렴 안 하면 3라운드에서 끊고 우려사항만 기록.
