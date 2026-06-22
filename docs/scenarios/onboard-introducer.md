# 도입자 온보딩 시나리오

도입자가 clone → `tm-join` L1 셋업 → `tm-connect` L2 서비스 연결까지 진행하는 전체 유저 여정이다.

---

## 시나리오 1 — 도입자

**나는 팀에서 teammode 를 처음 켜는 사람이다.** 빈(또는 기존 코드만 있는) 레포에 `team.config.json` 이 없다. 내가 실행하면 `install.py` 가 `detect_role()` 로 config 부재를 보고 **`role=introducer`** 로 판정하고, **config 를 새로 쓴다**(팀 이름·기본 greeting/farewell·빈 services). 이후 합류하는 팀원들이 이 config 를 읽는다.

> 관찰자 시점: 도입자 경로의 분기점은 `install_lib.detect_role()` → `config_is_valid()` 이다. `team.config.json` 이 없거나, `spec_version` 이 없거나, `team.name` 이 placeholder(`""`/`changeme`/`todo`/`your-team-name`/`team-name`/`tbd`/`placeholder`) 면 **introducer**. 그래서 도입자만 `scaffold_memory()` 안에서 `write_introducer_config()` 가 호출돼 config 가 생긴다(`install_lib.py:592`).

### 국면 ① 레포 clone

**(a) 입력** — 나는 빈 팀 레포(또는 teammode 템플릿)를 클론하고 그 안에서 에이전트를 띄운다.
```bash
git clone git@github.com:our-team/our-repo.git
cd our-repo
```
**(b) 에이전트** — 아직 아무것도 안 한다. 내가 말을 걸 때까지 대기.
**(c) 화면** — 셸 프롬프트뿐. teammode 흔적 없음(`team.config.json`·`memory/`·`.teammode-active` 전부 없음).
**(d) 다음** — 보통은 바로 셋업을 부탁하지만, 처음 보는 레포라 호기심에 "뭐하는 레포냐"부터 물을 수 있다. → 국면 ①.5 (또는 곧장 국면 ②).

### 국면 ①.5 첫 호기심 — "이거 뭐하는 레포야?"

**(a) 입력** — 셋업 전, 호기심에 묻는다:
> "이거 뭐하는 레포야?"

**(b) 에이전트** — `README.md`·`AGENTS.md` 를 읽고 **사람 말로** 답한다. 외우게 하지 않고, 다음을 자연스럽게 담는다:
- **정체**: Claude Code·Codex 같은 AI 코딩 에이전트를 위한 크로스에이전트 팀 협업 툴킷.
- **한 줄 가치**: "소통하지 않아도 팀이 뭘 하는지 안다" — 팀 맥락을 git 레포 하나에 모으고 세션 시작 시 자동 주입.
- **도입 쉬움**: "이 레포 셋업해줘" 한 마디. 설정·문서 먼저 안 읽어도 됨(진입장벽 0).
- **복리**: 세션로그가 쌓일수록 자동주입 맥락이 두꺼워지고, 합류자도 첫날부터 전체 히스토리를 안고 시작 — 쓸수록 강해진다.
- **기록은 개인에게도 남는다**: 결정의 이유·막힌 지점·그날 한 일이 남아 회고·이력서·자기소개서·블로그 글감이 된다.
- **상태 정직**: L1(팀 메모리·맥락 자동주입·Obsidian 뷰) 동작, L2(서비스 연동) WIP.
- ⭐ **AI 행동 지침**: 위 가치를 추상적으로 나열하지 말고, **사용자의 상황(직무·관심사)을 짐작해 맞춤 예시를 하나 들어준다.** 한 사람에게 가장 와닿을 한 장면을 고른다.
  - 예) 개발자 → "3개월 뒤 '이 설계 왜 이렇게 했지' 싶을 때, PR 뒤질 필요 없이 그날 세션로그에 이유가 적혀 있어요."
  - 예) 취업 준비 중 → "자소서 쓸 때 이번 분기에 뭘 했는지 막막하잖아요. 로그가 '언제 뭘 왜 했는지'를 다 말해줘요."
  - 예) 블로그 운영 → "막혔다 푼 과정이 그대로 남으니, 그게 곧 글감이 돼요."

**(c) 화면** — 에이전트의 자연어 설명(터미널 명령 아님). 정체 → 한 줄 가치 → (도입 쉬움·복리·기록 활용) → 상태 → 맞춤 예시 1개 → 셋업 제안 순.

**(d) 다음** — 흥미가 생기면 "그래 셋업해줘" → 국면 ②. (관심 없으면 그냥 닫아도 됨 — 강요 없음.)

### 국면 ② `tm-join` L1 셋업

**(a) 입력** — 슬래시가 아니라 **자연어**로:
> "이 레포 셋업해줘"

**(b) 에이전트** — `tm-join`(현 `tm-onboard`) 스킬을 따른다. 먼저 "도입자/팀원은 `install.py` 가 `team.config.json` 유효성으로 자동 판정한다"고 알리고, 단계를 손으로 재현하지 않고 한 명령으로 위임한다. `--member-name` 은 분기 스위치가 아니라 양쪽에서 author 이름을 정하는 인자다.
```bash
python infra/install.py --root . --member-name alice --yes
```
- `--root .` : 팀 루트는 **명시만**(env `TEAMMODE_HOME` 무신뢰, `_resolve_root`). 추측 금지.
- `--yes` : **실 `~/.claude/settings.json` 에 훅을 배선(write)** 하는 게이트. 없으면 wire 를 건너뛴다.

**(c) 화면** — `install.py` 부트스트랩이 `①preflight ②detect ③role ④scaffold ⑤wire ⑥env ⑦verify` 순서로 흐른다. config 가 없으므로 `role=introducer`. 실제 출력(태그·문자열은 코드 그대로):
```
[plan] team_root=/abs/path/our-repo
[plan] role=introducer (team.name 기본='our-repo')
[plan] agents=['claude']
[plan] member_name=alice
[scaffold] memory/ 구조·members.md 등재 완료 (role=introducer).
[wire] claude MCP 등록 동기화 완료
[wire] claude 훅 동기화 완료 → /home/me/.claude/settings.json
[wire] claude 스킬 심링크 완료 → /home/me/.claude/skills
[env] /home/me/.bashrc 에 TEAMMODE_HOME 주입 (신규 주입).
[verify] 설치 검증 OK — members=1 (팀모드는 꺼둠).
[done] 설치 완료. 팀모드를 켜려면 `tm on`(또는 /tm) 하세요.
```
- exit code **0**.
- `team.name` 기본값은 git remote 의 repo 명(`repo_name_from_remote`), 없으면 폴더명. 위 예는 remote → `our-repo`.
- 부수효과(도입자만): `team.config.json` 신규 생성 — `spec_version="0.2"`, `team.name`·`team.greeting`(`"our-repo 팀모드 ON"`)·`team.farewell`(`"수고하셨습니다 — our-repo"`), `admin_contact="alice"`, `services: {}`(전부 빈 슬롯), `members: [{name:"alice"}]`. 그리고 `memory/INDEX.md`·`memory/team/members.md`(`- alice  <!-- id: <git email> -->`)·`memory/team/decisions/current.md`·`memory/banner.txt`·빈 세션 디렉토리 `memory/team/sessions/alice/`. **첫 세션로그는 안 쓴다**(M2 — 디렉토리만).

> 막히는/사람이 직접 해야 하는 지점, 분기:
> - **`--yes`/`--settings` 둘 다 없으면** wire·env·verify 를 건너뛰고 scaffold 까지만 하고 끝난다(exit 0). 화면엔 `[wire] 건너뜀 — 실호스트 배선은 --yes(실설치) 또는 --settings(격리) 필요. 스캐폴드는 완료(메모리는 준비됨).` → state 는 `off` 로 남는다.
> - **`--dry-run`** 이면 `[plan]` 출력 후 `[dry-run] 변경 없음 — 계획만 출력했습니다(settings·memory·env 무접촉).` 만 찍고 exit 0. 파일 무생성.
> - **`--settings <격리경로>`** 면 실 호스트 무접촉: `[env] 건너뜀 — 격리 모드(--settings): 실 호스트 env … 무접촉.`, verify 는 `context` 만 호출(설치 확인 — 팀모드 on 미사용이라 settings 무관). 격리 테스트·CI 용.
> - **git 원격 인증이 없으면** stderr 에 `[warn] git 원격 인증 미확인 — 로컬 L1 은 진행(협업 시 push/pull 막힘).` (비치명, 진행).
> - **이름을 못 정하면**(`--member-name` 없고 git user.name 도 없음) → stderr `[error] 멤버 이름을 정할 수 없습니다. --member-name <영문이름> 으로 지정하세요…` + exit **3**. 사람이 이름 주고 재실행.
> - **preflight 실패**(Python 3.9 미만 / git 바이너리 부재 / 팀 표식 부재) → stderr `[error] preflight 실패: …` + exit **2**. 무변경.

**(d) 다음** — 에이전트가 결과를 사람 말로 옮긴다: "팀을 새로 만들었습니다(도입자). 메모리 구조가 섰고 훅이 실 settings 에 배선됐어요. **갓 만든 팀이라 세션로그는 0** — 다음 작업부터 자동 기록·주입됩니다." 그리고 첫 가치(L1)를 보여준다.

**첫 가치 (셋업 직후)** — 에이전트가 실행:
```bash
python infra/teammode.py context --root . --json
```
**화면** — `state=off`(설치는 팀모드를 켜지 않는다 — `tm on` 전까지 off, 마커 없음), `members` 에 alice 1명, summary 는 없음(로그 0):
```json
{"state": "off", "index": "# 팀 메모리 인덱스 (INDEX.md)…", "members": [{"author": "alice", "date": "…", "summary": "…", "role": null}]}
```
에이전트는 "지금 팀 상황: 멤버 1명(alice), 아직 작업 로그 없음, 구조는 준비됨"으로 요약한다.

> personality 커스텀(opt-in, 키 0): 에이전트가 "배너·시작멘트·끝맺음말 커스텀할래요?"라고 **물어본다**. 예 → `team.config.json` 의 `team.greeting`/`team.farewell` 교체(엔진 `on` 이 배너 직후 greeting, `off` 가 farewell 출력) 또는 `memory/banner.txt` 직접 교체. **도입자만** 이 팀 스코프 값을 쓴다(커밋되면 팀원 공유). 아니오 → 기본값 그대로(0 영향).
>
> Obsidian 뷰(opt-in, 키 0): 예 → `python infra/install.py --root . --register-obsidian`. `.obsidian/`(graph·dataview) 생성 + `obsidian.json` 에 **merge 등록**(기존 볼트 보존·멱등). **Obsidian 미설치면 우아하게 skip** → `[obsidian] 등록 건너뜀 — Obsidian 미설치(설정 디렉토리 부재) — skip (비치명, install 계속).`, 항상 exit **0**. 나중에 "Obsidian 등록해줘"로 독립 실행 가능.

### 국면 ③ `tm-connect` L2 서비스 연결

**(a) 입력** — `tm-join` 이 첫 가치 직후 "서비스(이슈·채팅·문서·캘린더) 연결할래요? 나중에 해도 돼요"라고 **강요 없이 제안**한다. 내가:
> "이슈 트래커 연결해줘"

**(b) 에이전트** — 여기서부터 **`tm-connect` 스킬**이 실행한다(tm-join 은 제안+트리거까지만). 역할 어휘(issues)로만 말하고, 어느 제품인지는 데이터가 답한다. issues 슬롯이 비어 있으므로 어떤 provider 를 쓸지 묻고(예: Linear), `providers/linear.json` 이 실재하는지 확인한 뒤 그 팩의 `token_guide`·`auth`·`default_scope`·`resource_fields` 를 **읽어** 안내를 구성한다(스킬 본문에 링크 하드코딩 금지).

**(c) 화면 / 사람이 직접 해야 하는 것** — `providers/linear.json` 기준:
- 발급 안내(`token_guide.url`·`steps`): `https://linear.app/settings/api` → Security & access → Personal API keys → "Create key" → 라벨 입력 → 키 복사.
- `auth: "api_key"` → 멘트: "개인 키를 Create → 복사 → 붙여넣기. attribution 이 본인으로 남도록 **각자 발급**."
- `default_scope: "personal"` → 각 멤버가 **자기 토큰 직접 입력**(v0.1 은 팀 자동공유 없음).
- **여기가 사람 몫(보안 경계, 무인 불가):** 토큰 발급(Create 클릭)·복사는 사람이. 토큰은 표준입력으로만 로컬 금고에 저장:
  ```bash
  python -c "import sys; sys.path.insert(0,'infra'); import credentials; \
    credentials.store('our-repo', 'personal', 'issues', input())"
  ```
  → `$XDG_DATA_HOME/teammode/credentials/our-repo.json`(0600, git 미추적). 평문 금고이므로 **동기화 폴더 금지**(Syncthing/Dropbox/iCloud 절대 금지).
- config 슬롯 기록: `resource_fields: []`(Linear 은 인스턴스 필드 불필요) → `team.config.json` 의 `services.issues = {provider:"linear", scope:"personal"}`. **토큰은 config 에 안 적는다**(금고에만).
- 재배선: `python infra/install.py --root . --yes` → 어댑터가 issues MCP 등록(`mcp.register_hint`: "Linear 공식 MCP 서버를 정규 서버명 'linear' 로 등록").

**첫 가치(issues 동사)** — 에이전트가:
```bash
python infra/teammode.py issue create --root . --title "온보딩 테스트"
```
연결됐으면 정규 입력 스키마가 JSON 으로 echo(exit 0):
```json
{"verb": "issue", "action": "create", "service": "issues", "provider": "linear", "input": {"title": "온보딩 테스트"}}
```
> 미구현/멈춤 정직 표시: 엔진 `issue` 동사는 **스키마 echo 까지만** 한다 — `action_map` 해석·페이로드 변환·실 MCP 호출은 **하지 않는다**(어댑터/스킬 몫). 즉 이 명령이 실제로 Linear 에 이슈를 만들지는 않는다. 연결 안 된 빈 슬롯이면 `[info] issues 슬롯이 연결돼 있지 않습니다. team.config.json 의 services.issues 를 연결하세요(tm-connect).` + exit **0**(빈 슬롯 = 1급 시민, 에러 아님).

**(d) 다음** — 도입자가 팀 scope 슬롯(예: chat=slack, docs=notion)을 추가로 연결하면 그 provider·인스턴스 값을 config 에 **커밋**해 팀원이 읽게 한다. 단 **토큰은 v0.1 에서 각자 입력**이라 도입자 1회로 끝나지 않는다.

> provider 별 사람 몫 차이(`auth` 값으로 분기):
> | provider | role 슬롯 | auth | scope | resource_fields | 사람 몫 |
> |---|---|---|---|---|---|
> | linear | issues | api_key | personal | (없음) | 키 Create→복사→붙여넣기 |
> | slack | chat | bot_token | team | channel_id | 앱 생성→봇 스코프→설치→봇 토큰 복사 |
> | notion | docs | api_key | team | database_id | integration 생성→토큰 복사→대상 DB 에 Connections 공유 |
> | google | calendar | oauth | personal | calendar_id | OAuth client→동의 화면 **"Allow"**(localhost PKCE) |
