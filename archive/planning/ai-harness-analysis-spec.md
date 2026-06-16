# AI 하네스 OSS 매커니즘 흡수 분석 — 실행 스펙 (02:40 KST 예약 실행용)

작성: 2026-06-16 02:13 KST. 실행 예정: 2026-06-16 02:40 KST (로컬 세션 깨움, Codex).

## 목적
유명 AI 하네스/메모리 OSS를 Codex로 분석 → **teammode 레포(`/home/jane-doe/work/extras/acme/teammode-repo`)에 반영 가능한 매커니즘** + **독특한 하네스 설계** 추출 → 개인 Notion "Research" DB에 정리.

## 분석 대상 (GitHub)
**클러스터 A — 메모리 시스템** (무엇을 저장/주입/검색하나):
- basic-memory — github.com/basicmachines-co/basic-memory  ← 우리랑 가장 닮음(md+git+MCP)
- Letta (MemGPT) — github.com/letta-ai/letta  (자기편집 티어드 메모리)
- mem0 — github.com/mem0ai/mem0  (추출/검색 메모리 레이어)

**클러스터 B — 하네스/git/확장** (어떻게 배선하나):
- Aider — github.com/Aider-AI/aider  ← git-native auto-commit·repo-map·충돌처리 (우리 auto-commit/git_ops 약점 직결)
- Goose — github.com/block/goose  (MCP-native 확장 모델)
- Continue — github.com/continuedev/continue  (rules + context providers)

**참조 표준** (클론 작게/문서만):
- AGENTS.md 표준 — agents.md (크로스에이전트 지시 파일 수렴 표준)

## 실행 절차
1. 작업 디렉터리: `/home/jane-doe/work/extras/acme/teammode-repo/.codex-ref/` (gitignore에 추가, 분석 후 삭제).
2. 대상 레포 **shallow clone (depth 1)**. 큰 레포는 메커니즘 파일에 집중(메모리 레이어, git ops, 훅, MCP 배선, 컨텍스트 주입) — 전체 정독 금지(토큰 폭발 방지).
3. Codex 실행을 **2클러스터로 분리** (각각 한 번씩, high reasoning, read-only). teammode-repo를 타겟으로 매핑.
   - 프롬프트 핵심: 각 매커니즘에 대해 `SOURCE(파일:라인) → teammode TARGET(파일/경로) → DELTA(팀툴 적응) → EFFORT(S/M/L)`. + "독특한 하네스 설계" 별도 섹션(우리가 안 떠올린 발상). 정직 규칙: 읽은 파일만 인용, 외부검증 필요한 건 명시.
   - 특히 **Aider의 auto-commit/충돌처리**는 우리 `infra/hooks/auto-commit.py`·`infra/git_ops.py`의 ff-only 정책과 대조해 개선점 뽑기.
4. 2클러스터 결과 합쳐 **종합 우선순위(흡수 1순위 N개)** + **DO NOT PORT** 정리.
5. 임시 클론 삭제, .gitignore 원복.

## 검수: 데브사이클 패턴 (토큰 팍팍)
산출물은 문서/분석이므로 dev-cycle의 **정합성 검수** 변형을 적용 (참조: feedback_dev_cycle_pattern).
1. **생산**: Codex 2클러스터 분석 초안.
2. **적대적 검수**: 별도 Codex/서브에이전트가 초안을 깐다 — (a) 인용한 SOURCE 파일·라인이 실재하는가(환각 점검), (b) "흡수 가능"이 과장 아닌가(실제로 teammode에 안 맞는데 우긴 건 없나), (c) DELTA가 팀툴 맥락 반영했나, (d) 빠진 핵심 매커니즘 없나, (e) "독특한 설계"가 진짜 독특한가 아니면 우리가 이미 가진 건가. **공격적으로, 토큰 아끼지 말고.**
3. **반영**: 검수 지적 반영해 수정.
4. **재검수**: 한 번 더 적대 검수해 게이트 통과(중대 지적 0)까지 루프. 토큰 상한 신경쓰지 말고 품질 우선.

## 산출물 → Notion (지하철에서 읽음 = 모바일 가독성 최우선)
- 워크스페이스: 개인(claude_ai_Notion). DB: "Research" (data source `collection://38030fbe-4508-80e1-b182-000ba0f226e8`, 부모 "공방").
- 새 페이지. 제목: `AI 하네스 OSS 매커니즘 흡수 분석 — 2026-06-16`.
- **가독성 규칙(중요)**:
  - 맨 위 **TL;DR 콜아웃**(💡) — 흡수 1순위 5개를 한 줄씩, 폰에서 3초 스캔 가능하게.
  - 짧은 문단(2~3줄), 불릿 위주. 긴 산문 금지.
  - 표는 **좁게**(컬럼 3~4개, 모바일에서 안 깨지게). 와이드 표 쪼개기.
  - 코드/파일경로는 인라인 코드로만, **긴 코드블록 덤프 금지**(지하철 가독성 해침).
  - 섹션마다 H2 헤딩 + 이모지 앵커로 스캔성↑.
  - 각 매커니즘: "**한 줄 요약 → 우리 적용 → 노력(S/M/L)**" 3단으로 압축.
- 본문 구조: 💡TL;DR / 🧠클러스터A 메모리 / 🔧클러스터B 하네스·git / ✨독특한 설계 모음 / 📋흡수 우선순위(좁은 표) / ⛔DO NOT PORT / ▶️다음 액션.

## 전송 → 텔레그램
- 검수·Notion 정리 끝나면 **`mcp__telegram__send_message`로 알림 전송**: 한 줄 완료 보고 + 흡수 1순위 3~5개 핵심 + **Notion 페이지 URL**. 폰에서 바로 열어 지하철에서 읽게.

## 주의
- Codex 토큰 큼(obsidian 때 클러스터당 0.8~2.3M). 분석은 2클러스터, 검수는 dev-cycle 루프 — **토큰 팍팍 써도 됨(사용자 지시).**
- 브라우징 필요시 Playwright(WebFetch 금지). 단 이번은 주로 코드 분석이라 클론+Codex 위주.
- 끝나면 daily 로그(2026-06-16)와 팀 세션로그(jane-doe)에 한 줄 남기기.
