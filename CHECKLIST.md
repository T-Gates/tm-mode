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

## 🔶 슬라이스 T — 템플릿 풀 (on 시 upstream fetch, §11.6) [완료 — V.5 로 편입 2026-06-13]
> 은수: "팀모드 킬때 템플릿 풀도". P1로 엔진 on/off 구조 정리됐으니 그 위에 얹음.
> ⚠️ 구현 전 Gstack 자동업데이트 메커니즘 조사(ae839f8) 결과 반영 — 같은 문제(매 호출 시 업데이트)를 실전에서 어떻게 푸는지 보고 throttle·실패처리·알림 방식 차용.
- [x] T.1 `teammode.py on`에 upstream fetch 단계: upstream git fetch(조용히·타임아웃·실패무시) → behind 계산(count_behind) → 변경목록(upstream_changes)+알림 문자열 → on 나머지. [자기 레포 pull 은 별도 pull 동사(V.3)로 분리 — on 의 핵심 경로를 네트워크가 막지 않게]
- [x] T.2 안전: fetch만 자동, **merge 절대 자동 금지**(_maybe_notify_upstream 은 fetch+알림만). upstream remote 미설정→우아한 축소(_has_remote 가드, 무알림). 오프라인→조용히 패스(예외 삼킴, on 무차단). divergent 시 on 무오염(적대검수 락)
- [x] T.3 `teammode update` 동사: 명시적 fetch+merge --ff-only+변경요약. ff불가(divergent)→비치명 거부(워킹트리 무오염·사람판단 유도). allow_unrelated 는 라이브러리 옵션(기본 안전 ff-only)
- [x] T.4 회귀 테스트(/tmp 격리): fetch 실패·오프라인 hang가드·미설정·behind 감지·divergent 무오염 각 케이스. 네트워크는 로컬 fake upstream으로 모사
- [x] T.5 검수 통과 + Gstack 교훈(fetch만 자동·실패 무차단·throttle 정신) 반영 확인

## 🔷 슬라이스 V — 엔진 핵심 동사 (활성, 우선순위 순)
> 어젯밤 "기계적 단계 py화" 조사 = 이 동사들의 설계도. 원칙: 엔진=기계적 재료손질, 스킬=판단(요약·정리). 동사는 재료만 모으고 요약은 에이전트.
> ⚠️ git 동사는 신규 작성 금지 — 어젯밤 auto_pull.py(do_pull, 손자프로세스 killpg·ff-only·타임아웃 안전장치 포함)를 `infra/git_ops.py` 공통 모듈로 키워 pull/commit/auto-pull 전부 재사용(중복=드리프트 방지).
- [x] V.1 `log` — 세션로그 파일 생성/이어쓰기(날짜·frontmatter·06시컷 기계적 자동). 매일 쓰는 바닥, 훅이 부름. **먼저**(데이터 연료). [완료: workday.py 06시컷 단일소스, author 화이트리스트+선두dash거부, 하루1파일 append. 골든 04 GREEN. 신규 28테스트(log 20+workday 8). 적대검수 1버그(선두dash) 수정→"수정 없음"]
- [x] V.2 `context` — 전원 세션로그·상태 긁어 JSON으로 모음(요약은 스킬). teammode 간판 "지금 팀 상황". log 다음(연료로 보여줌). [완료: 멤버별 최근1파일+summary/date, INDEX 읽기, .tgates-active 상태, --json/텍스트, 구로그 summary 생략. 골든 02 GREEN. 신규 17테스트. 적대검수 0버그(frontmatter 누수 probe 안전 확인+회귀락)→"수정 없음"]
- [x] V.3 `pull` — git_ops로 통합(auto_pull.do_pull 재사용), 엔진 동사로 노출. [완료: infra/git_ops.py 단일소스(do_pull·killpg·ff-only·타임아웃·자격증명차단), auto_pull 재사용 리팩토링(19테스트 유지·동일객체 단언), pull 동사 비치명실패. 신규 13테스트. 적대검수 0버그(고아누수 회귀락)→"수정 없음"]
- [x] V.4 `commit` — add/commit/push 묶음, git_ops에 추가. 여러 스킬이 매번 하던 것 흡수. [완료: git_ops.do_commit(add-A→변경검사→commit→선택push), push실패 커밋보존, 변경없음/git아님 비치명, --message필수/--push선택. 신규 17테스트. 적대검수 0버그(arg주입면역·롤백없음 락)→"수정 없음"]
- [x] V.5 `update` — 슬라이스 T(템플릿 풀: upstream fetch→변경목록+Y/n→ff머지). 별도 축이나 동사로 편입. [완료: git_ops fetch_upstream/count_behind/upstream_changes/update_from_upstream. on=fetch만+알림(자동merge금지), update=명시적 ff-only(divergent 무오염 거부). 신규 20테스트. 적대검수 0버그(divergent 무오염 락)→"수정 없음"]
- [x] V.6 각 동사 golden 시나리오 GREEN 전환 확인 + 검수 통과. [02-context·04-log GREEN(슬라이스 V 직접 인수). 01-on·05-off PASS 유지. 03-issue 는 슬라이스 V 범위 밖(별도 issue 동사). 각 동사 적대검수 1라운드씩(V.1만 실측버그1, 나머지 0버그)+회귀락 후 "수정 없음"]

## ⏸ 나중 슬라이스 (지금 안 함, 버리지 않음)
- [ ] (공개 직전) `doctor` — 자가진단(스킬링크·훅·플래그·MCP토큰·버전·config) + 기계적 자동수리/사람조치 안내. 어젯밤 플래그 좀비 같은 불일치 잡는 안전망. + 지원채널(이슈자동생성→슬랙, §11.8)은 외부팀 생긴 후. 점검대상(스킬·MCP)이 먼저 깔려야 가치 있음
- [ ] (install 흡수) `symlink` — 독립 동사 아님, install/update 동사의 내부 함수로 (심링크→정션→복사 사다리)

## 🟢 L1 — install.py 부트스트랩 (0→L1 셀프호스트, 2026-06-14 새벽 착수)
> 스펙: `/home/euns/work/soma/tgates/teammode/spec/04-install.md` (+ 01,02). 기준선 166 passed.
> 적대적 계획검수 8건 반영 v2(7슬라이스). 무인 철칙: 호스트 변형 테스트는 fake HOME+--settings/tmp, 실 ~/.bashrc·~/.claude·실 git config 무접촉, L1-0 가드 선행, 푸시 금지, 직렬.
> 스코프: install-mcp(서비스)=L2 제외. L1=메모리 스캐폴드+훅+env+context 자동주입까지.

- [x] **L1-0 (선행) conftest 가드 강화** — `tests/conftest.py` `_GUARDED`에 `~/.bashrc·~/.zshrc·~/.profile·~/.bash_profile·~/.config/fish/config.fish` 추가. install.py도 `--install`/`--settings` 없으면 거부(엔진 P2 계승). 가드 작동 실증 테스트. **(B1 — 다른 슬라이스보다 먼저)** [완료: ⚠️ dotfile `.suffix==""` blind spot 실측·수정(`_CONTENT_GUARDED` 신설, suffix 무관 검사). 디스패처 P2 가드+`--install` 흡수. 가드 발화 격리 실증. 신규 5테스트(166→171). 적대검수 "수정 없음"]
- [x] **L1-A CLI·preflight·detect·role** — install.py 인자파서(--root/--agent/--member-name/--settings/--yes/--update/--dry-run, exit 0/2/3). preflight(Python버전·git바이너리·팀루트표식; 원격인증 부재는 경고). detect(git remote·user.name·tz·에이전트·config). role=config 존재+필수키(spec_version/team.name) 유효성(서비스 무관, M3). **착수 전 `grep -rl install.py tests/ conformance/`로 의존처 전수** + dispatcher(`--<agent> sync`) 흡수/보존 결정(M5). 신규테스트 + I-dry/I4b/I6b. test_install_dispatch.py 기대값 갱신 허용.
- [x] **L1-B scaffold** — `memory/INDEX.md`·`memory/team/members.md`·`memory/team/sessions/<member>/`(정확 경로, 엔진 teammode.py:191 단일소스, M1). 도입자 최소 config(빈 서비스 슬롯). `memory/banner.txt`를 team.name으로 선기록(엔진 무수정, M4). 이름은 엔진 `_validate_author` 재사용(m1). **첫 세션로그 안 씀(M2)**. 이름충돌 정책(동일=멱등/오버라이드충돌=exit3, M4).
- [x] **L1-C wire (훅 sync만)** — install.py가 감지된 에이전트마다 adapter `sync` 호출(훅 등록). **스킬 심링크 제외(M2 — infra/skills 없고 L1은 훅이 주입)**. 에이전트별 독립 실패=exit3+stderr, 성공분 롤백 안 함(M5). 멱등.
- [x] **L1-D env 주입** — 셸 프로파일 감지(bash/zsh/fish) `TEAMMODE_HOME`(m2) 멱등 1줄. **테스트는 monkeypatch HOME=tmp 강제(B1)**. 멱등·중복금지·셸별.
- [x] **L1-E session-start.py 훅 구현 (신규, M3)** — manifest 등록됐으나 teammode-repo에 파일 부재 = L1 진짜 payoff. `infra/hooks/session-start.py`: `.tgates-active` 활성 시 context(멤버별 최근 세션로그) 읽어 SessionStart additionalContext로 주입. normalize 경유 깨지지 않게. 신규테스트.
- [x] **L1-F verify + 골든** — install.py ⑦(on --install + context --json로 L1 데이터 읽힘 확인; 테스트선 --settings 격리). conformance 골든 I1(빈→L1)·I2(팀원)·I2b(다음세션 주입)·I3(멱등)·I4·I4b(격리)·I-dry. 최종 전체 스위트 166+신규 green.
- [ ] 마감(사람): 푸시/PR 은수 판단, 세션로그 반영

## 🪟 W — Windows 네이티브 지원 (크로스플랫폼, 2026-06-15 새벽 착수)
> 스펙(CLAUDE.md 규칙8·SPEC §0)이 Linux/macOS/Windows 요구하나 native 윈도우 미동작 = 스펙 위반/갭. 기준선 301.
> ⚠️ 파이(Linux)에선 실 윈도우 실행 불가 → Windows 분기는 **sys.platform/os.name 모킹 + subprocess(setx) 모킹**으로 단위 테스트. 합격=모킹 윈도우 테스트 green + Linux 301 무회귀. **실 윈도우 검증=은수 내일.** 호스트 철칙(fake HOME·실 setx 실행 금지=모킹·실 ~/.claude/~/.bashrc 무접촉) 유지. 푸시 금지.
- [x] **W-A env 주입 Windows(setx)** — install_lib inject_env에 윈도우 분기: `setx TEAMMODE_HOME "<abs>"`(HKCU\Environment 영구, 새 프로세스 반영). uninstall 역(remove_injected_env Windows: reg delete). 플랫폼 감지(os.name=='nt'/sys.platform 'win'). subprocess 모킹 테스트. [완료: is_windows(값주입) + inject_env_windows/remove_injected_env_windows(runner 주입). inject_env/remove_injected_env에 platform 분기, install.py bootstrap ⑥env·cmd_uninstall ③에 윈도우 라우팅(platform 주입). 신규 13테스트(301→314). 적대검수: nt 분기 실타격·posix 무회귀·실 setx/reg 미실행(runner 주입)·격리 모드 setx 차단 확인]
- [x] **W-B 훅 명령 크로스플랫폼** — adapter `python="python3"` 하드코딩 제거 → 설치 시점 sys.executable(절대경로). build_command·is_owned이 그 명령으로 일관. 윈도우 경로/따옴표 안전. 모킹 테스트. [완료: default_python()=sys.executable(빈 값 폴백 win=python/posix=python3), 생성자 python=None→해석, _quote_arg(공백 경로 조건부 인용), argparse --python 기본 None(claude+codex). codex 는 상속으로 자동 반영. is_owned 무파손(normalize.py substring). 신규 9테스트(314→323). 적대검수: is_owned 일관·윈도우 공백경로 인용·기존 python3 명령 재sync 마이그레이션·Linux 무회귀 확인]
- [x] **W-C POSIX 종속 감사** — install_lib·install.py·hooks·adapter·teammode.py 전수 grep. 교정: `_default_profile`(윈도우→None, env=레지스트리), bootstrap shell 정규화(백슬래시 경로도 인식), `_default_obsidian_config`(윈도우 AppData\Roaming·mac Library 위임). 검증 OK였던 것: expanduser(크로스플랫폼), teammode author 검사(/·\ 양쪽), 훅 os.path.join, default_python(W-B). 신규 11테스트(323→332). 적대검수: 윈도우 None 프로파일 안전·소스 절대경로 하드코딩 0·훅 리터럴 / 결합 0 확인.
- [x] **W-D verify + SPEC** — nt 모킹 라운드트립 e2e(install→on→context→uninstall, platform=win32 주입+setx/reg runner 레코더). cmd_uninstall 에 platform 주입 + 격리 모드 실 env 제거 스킵(install 대칭, 잠재 POSIX 누수도 동시 수정). SPEC §4.8(env 크로스플랫폼)·§2(어댑터 sys.executable 명령)·§4.9 I4b·I-win·A.3(Windows=모킹검증 완료) 반영 + spec/04 §9·spec/02 §6 일관 정정. 합격=334 green(301+33). 신규 2 라운드트립(+uninstall 회귀 유지). [실 윈도우 검증=은수 내일]
- [ ] 마감(사람): 은수 내일 native 윈도우 실테스트 → 막히는 점=후속. 푸시 판단.
