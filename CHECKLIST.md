# teammode 자율 빌드 체크리스트 (goal)

> 시작: 2026-06-13 01:25 / 모드: 무인 dev-cycle (구현→적대적 검수→반영 루프)
> 규칙: 슬라이스 순서대로. 각 슬라이스는 검수 "수정할 내역 없음" 나올 때까지 루프. 푸시 금지(은수 판단). 항목별 커밋(conventional + Co-Authored-By).
> 스펙 소스: `/home/euns/work/soma/tgates/teammode/spec/{01,02,03}-*.md` + `teammode-adapter-spec-draft.md`

## 기준선
- [x] 빈 레포, 테스트 0개 = 기준선 0

## 🔴 슬라이스 0 — P0 긴급 (다른 모든 작업보다 먼저! 안 하면 호스트 환경 또 오염)
> 사고: check.py가 ambient `TGATES_HOME`을 폴백으로 읽어 off-persist 시나리오를 실 호스트 toolkit에 실행 → 호스트 `.tgates-active` 삭제. 변수명이 호스트와 겹친 게 근본 원인.
- [x] 0.1 `TGATES_HOME` → `TEAMMODE_HOME` 전수 치환 (infra/teammode.py:26, infra/hooks/session-log-remind.py:25, conformance/check.py:322·325, tests/test_normalize.py ×5, 그 외 grep로 전수). teammode는 독립 프로젝트 = 자기 환경변수
- [x] 0.2 check.py 환경 격리 강화 — ambient env 무시. subprocess를 `env={}` 빈 환경 + 명시 주입(`TEAMMODE_HOME=<run root>`, PATH 등 필수만)으로 실행. ambient `TEAMMODE_HOME`/`TGATES_HOME`이 set돼 있어도 새지 않게(`env -i` 정신). 누가 변수 set해도 호스트 오염 0 보장
- [x] 0.3 회귀 테스트 신규: "ambient에 TEAMMODE_HOME=/실호스트 가 set된 상태에서 verify/conform 돌려도 그 경로를 절대 건드리지 않는다" (격리 증명)
- [x] 0.4 검수 수행됨 — **"수정 필요 1건(P1)+권고(P2)" 판정**. P1 슬라이스로 이월 → 완료

## 🔶 슬라이스 P1 — 검수 지적 반영 (완료 2026-06-13)
> 적대적 검수(0.4)가 잡은 진짜 근본: 변수명 rename은 반쪽 처방. 엔진이 ambient env를 무조건 신뢰하는 게 사고의 진짜 원인.
- [x] P1-a `infra/teammode.py` `_team_root()` env 폴백 **제거**. 팀 루트를 **명시 인자 `--root`로만** 받음. `--root` 미지정 시 **정책 (A): 에러 종료**(exit 2, "--root 필수"). 근거: 엔진이 어느 폴더를 건드릴지 추측 0 = 사고 근본 처방.
- [x] P1-b 회귀 테스트: ambient `TEAMMODE_HOME=피해자` set 상태로 `teammode.py off`를 **SubprocessEngine 우회 직접 CLI 호출**해도 피해자 `.tgates-active` 생존 단언. `--root` 없으면 에러 종료(정책 A)·cwd 무접촉 단언. on/off 양쪽 + 격리 루트 한정 쓰기 단언 (tests/test_isolation.py).
- [x] P2 `--settings` 생략 시 실 `~/.claude` 오염 가드: 엔진이 `--settings <경로>`(격리) 또는 `--install`(실설치) 중 하나를 **필수**로 요구. 둘 다 없으면 거부(exit 2). 실 설치(`--install`)는 정상 동작(가짜 HOME로 e2e 확인). conformance CLI는 run root 하위 격리 settings(`.teammode-settings.json`)를 자동 주입.
- [x] 호출처 동기화: `conformance/check.py` SubprocessEngine(`--root` 명시 주입 + env 화이트리스트에서 팀루트 변수 제거 + CLI가 격리 settings 주입), `session-log-remind.py`(런타임 훅이라 env 유지 — 엔진과 구분 명시), 스펙 01 §1.2·§2.4 반영, 기존 test_isolation 기대값 수정.
- [x] 재검수 → "수정할 내역 없음" (적대적 오염 재현 4종 전부 차단, conftest 가드 통과, 실 settings 누수 0)

## 슬라이스 1 — 검수 도구 우선 (골든 시나리오 + 러너)
- [x] 1.1 `conformance/scenarios/` — 골든 시나리오 5개 선언적 명세 (on→배너 / context 주입 / issue 생성 / log 누적 / off 저장)
- [x] 1.2 `conformance/check.py` — 3모드 러너 골격: `lint`(정적) `verify`(동적 시나리오) `conform`(임의구현+Tier). 우선 verify/lint 동작
- [x] 1.3 `tests/test_check.py` — 시나리오 파싱·통과/실패 판정·Tier 산출 (RED→GREEN)
- [x] 1.4 빈 엔진에 `verify` 실행 → 전부 RED 확인(=인수 테스트로 박힘), 결과를 BUILD-LOG.md에 기록
- [x] 1.5 검수 통과("수정할 내역 없음")

## 슬라이스 2 — Claude 어댑터 수직 슬라이스
- [x] 2.1 `infra/hooks/manifest.json` — 정규형 샘플(PostToolUse+file_edit, SessionStart, PreToolUse+mcp; enforcement/fallback 필드 포함)
- [x] 2.2 `infra/agents/claude/events.json` — 번역표(events 매핑, actions.file_edit→`Write|Edit`, config_file)
- [x] 2.3 `infra/agents/claude/adapter.py` — `sync` 구현(파싱→번역→settings.json upsert, normalize 경유 배선, 멱등, 제거)
- [x] 2.4 `infra/install.py` — 디스패처 골격(`--claude`→adapter 위임, 분기로직 0)
- [x] 2.5 `tests/test_adapter_claude.py` — 6케이스(정규엔트리/action번역/mcp번역/멱등/제거/normalize경유)
- [x] 2.6 검수 통과
- [x] 2.7 `verify` 재실행 → on/off 시나리오 일부 GREEN 전환 확인

## 슬라이스 3 — normalize 런타임 + 공통 훅 1종 (stretch)
- [x] 3.1 `infra/agents/claude/normalize.py` — 입력 JSON→정규 스키마 변환 + 자가 필터
- [x] 3.2 공통 훅 1종 이식(session-log-remind 또는 auto-commit) — 정규 스키마만 인지
- [x] 3.3 `tests/test_normalize.py`
- [x] 3.4 검수 통과

## 슬라이스 4 — Codex 어댑터 + 폴백 (stretch)
- [x] 4.1 `infra/agents/codex/{events.json,adapter.py,normalize.py}` (PreToolUse null + fallback)
- [x] 4.2 `tests/test_adapter_codex.py` — 폴백·enforcement 축소 검증
- [x] 4.3 검수 통과 + 크로스에이전트 시나리오 GREEN

## 🔶 슬라이스 U — 상시 레포 최신화 훅 (throttled auto-pull) [완료 2026-06-13]
> 설계(은수 새벽 합의): UserPromptSubmit 마다 팀 레포 최신화하되 스로틀로 과부하 방지, **실패는 절대 작업 차단 금지**(철칙). 슬라이스 T(on 시 fetch)와 별개 — 이건 상시 pull.
- [x] U.1 `infra/hooks/auto_pull.py` 순수 함수 — `should_pull`(스로틀 판정·시각 주입) / `do_pull`(`git pull --ff-only`) / `auto_pull`(조립, 절대 raise 안 함). 시각·경로·스로틀초 전부 인자 주입(P1 교훈).
- [x] U.2 스로틀 기본 300s, 상태는 팀 루트 밖 `$XDG_STATE_HOME/teammode/last-pull`. **시도(attempt) 단위 기록** — 원격 장애 시 매 프롬프트 재시도 세금 방지.
- [x] U.3 안전: `--ff-only`(충돌 회피) + `GIT_TERMINAL_PROMPT=0`/`GIT_ASKPASS`/SSH BatchMode(자격증명 hang 차단) + subprocess 타임아웃 5s + git `http.lowSpeedLimit/Time`(defense-in-depth) + **프로세스 그룹 kill**(손자 git-remote-https 고아 방지).
- [x] U.4 훅 통합: `session-log-remind.py`(기존 UserPromptSubmit 훅)가 `.tgates-active` 활성 시 **리마인드 판정 전에** `auto_pull` 호출. 별도 manifest 엔트리·normalize subprocess 불필요. 어떤 예외도 삼킴(철칙).
- [x] U.5 회귀 테스트 19종(/tmp fake remote): 스로틀(시각 주입)·ff-forward·ff불가 무오염·non-git 무raise·자격증명 hang 차단·훅 통합·throttle 2nd call. conftest 가드 강화(XDG 격리 autouse + last-pull 오염 가드, 가드 작동 실증).
- [x] U.6 적대적 자기검수(새 관점, /tmp만) — 비라우팅/stall 원격 hang 없음(3s killpg) + 고아 프로세스 0 + state=dir/nonexist 무raise. **실측 버그 2건 수정**: ① timeout 후 손자 git 고아 누수 → 프로세스 그룹 killpg ② test_normalize 기존 테스트가 실 `~/.local/state` 오염 → XDG 격리+가드. 재검수 "수정할 내역 없음".

## 마감 (사람 몫 — 에이전트 금지)
- [ ] 푸시/PR 판단 (은수)
- [ ] 세션로그·계획서 반영

## 🔶 슬라이스 T — 템플릿 풀 (on 시 upstream fetch, §11.6) [활성 — 다음]
> 은수: "팀모드 킬때 템플릿 풀도". P1로 엔진 on/off 구조 정리됐으니 그 위에 얹음.
> ⚠️ 구현 전 Gstack 자동업데이트 메커니즘 조사(ae839f8) 결과 반영 — 같은 문제(매 호출 시 업데이트)를 실전에서 어떻게 푸는지 보고 throttle·실패처리·알림 방식 차용.
- [ ] T.1 `teammode.py on`에 upstream fetch 단계: ① 자기 레포 git pull ② upstream git fetch(조용히·타임아웃·실패무시) ③ behind 계산→배너 아래 알림 문자열 ④ on 나머지
- [ ] T.2 안전: fetch만 자동, **merge 절대 자동 금지**. upstream remote 미설정 시 우아한 축소(스킵·무알림). 오프라인 시 조용히 패스(on 막지 않기)
- [ ] T.3 `teammode update` 동사: 명시적 merge(--allow-unrelated-histories 첫회)+충돌처리+변경요약. 팀당 1회
- [ ] T.4 회귀 테스트(/tmp 격리): fetch 실패·오프라인·미설정·behind 감지 각 케이스. 네트워크는 로컬 fake remote로 모사
- [ ] T.5 검수 통과 + Gstack 교훈 반영 확인

## 🔷 슬라이스 V — 엔진 핵심 동사 (활성, 우선순위 순)
> 어젯밤 "기계적 단계 py화" 조사 = 이 동사들의 설계도. 원칙: 엔진=기계적 재료손질, 스킬=판단(요약·정리). 동사는 재료만 모으고 요약은 에이전트.
> ⚠️ git 동사는 신규 작성 금지 — 어젯밤 auto_pull.py(do_pull, 손자프로세스 killpg·ff-only·타임아웃 안전장치 포함)를 `infra/git_ops.py` 공통 모듈로 키워 pull/commit/auto-pull 전부 재사용(중복=드리프트 방지).
- [ ] V.1 `log` — 세션로그 파일 생성/이어쓰기(날짜·frontmatter·06시컷 기계적 자동). 매일 쓰는 바닥, 훅이 부름. **먼저**(데이터 연료)
- [ ] V.2 `context` — 전원 세션로그·상태 긁어 JSON으로 모음(요약은 스킬). teammode 간판 "지금 팀 상황". log 다음(연료로 보여줌)
- [ ] V.3 `pull` — git_ops로 통합(auto_pull.do_pull 재사용), 엔진 동사로 노출
- [ ] V.4 `commit` — add/commit/push 묶음, git_ops에 추가. 여러 스킬이 매번 하던 것 흡수
- [ ] V.5 `update` — 슬라이스 T(템플릿 풀: upstream fetch→변경목록+Y/n→ff머지). 별도 축이나 동사로 편입
- [ ] V.6 각 동사 golden 시나리오 GREEN 전환 확인 + 검수 통과

## ⏸ 나중 슬라이스 (지금 안 함, 버리지 않음)
- [ ] (공개 직전) `doctor` — 자가진단(스킬링크·훅·플래그·MCP토큰·버전·config) + 기계적 자동수리/사람조치 안내. 어젯밤 플래그 좀비 같은 불일치 잡는 안전망. + 지원채널(이슈자동생성→슬랙, §11.8)은 외부팀 생긴 후. 점검대상(스킬·MCP)이 먼저 깔려야 가치 있음
- [ ] (install 흡수) `symlink` — 독립 동사 아님, install/update 동사의 내부 함수로 (심링크→정션→복사 사다리)
