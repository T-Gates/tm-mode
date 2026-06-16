# 팀원 온보딩 시나리오

팀원이 clone → `tm-join` L1 셋업 → `tm-connect` L2 서비스 연결까지 진행하고 도입자와 갈리는 지점을 포함한 전체 유저 여정이다.

---

## 시나리오 2 — 팀원

**나는 이미 도입자가 만들어 둔 팀 레포에 합류한다.** 레포에 **유효한 `team.config.json`**(`spec_version` 있음 + `team.name` 비-placeholder)이 이미 있다. 내가 실행하면 `install.py` 가 `detect_role()` → `config_is_valid()=True` 를 보고 **`role=member`** 로 판정하고, **config 는 읽기만 한다**. 도입자가 만든 팀 정체성(이름·greeting·서비스 선언)을 내가 덮어쓰지 않는다.

> 관찰자 시점 — **도입자와 갈리는 지점 3가지:**
> 1. **config 읽기 전용.** `scaffold_memory()` 에서 `if role == "introducer"` 가드 때문에 `write_introducer_config()` 가 **호출되지 않는다**(`install_lib.py:592`). 팀원은 `members.md` 등재 + `config.members` 의 **자기 엔트리만 upsert**(`upsert_member_role`) — `spec_version`/`team`/타인 members 엔트리는 **절대 안 건드린다**.
> 2. **이름 충돌 시 exit 3.** `members.md` 에 같은 이름이 **다른 git email(identity)** 로 이미 등재돼 있으면 `register_member()` 가 `ConflictError` → `[error] 이름 충돌(사람이 해소 필요): …` + exit **3**. (도입자는 첫 사람이라 충돌이 사실상 없다.)
> 3. **context 에 기존 팀 세션로그가 보인다.** 도입자는 갓 만든 팀이라 로그 0 이지만, 팀원이 합류할 땐 도입자·먼저 합류한 사람들의 세션로그가 이미 있어 `context` 에 멤버별 summary 가 나온다.

### 국면 ① 레포 clone

**(a) 입력** — 도입자가 푸시해 둔 팀 레포를 클론한다.
```bash
git clone git@github.com:our-team/our-repo.git
cd our-repo
```
**(b) 에이전트** — 대기.
**(c) 화면** — `team.config.json` 이 **이미 있다**(`team.name="our-repo"`, `members:[{name:"alice"}]`, services 일부 채워짐). `memory/team/members.md` 에 `- alice …`, `memory/team/sessions/alice/` 에 기존 로그들. 단 `.acme-active` 마커는 **이 호스트엔 없다**(마커는 호스트 로컬, 커밋되지 않음 → 내 호스트엔 아직 셋업 안 됨).
**(d) 다음** — 에이전트에게 셋업 부탁. → 국면 ②.

### 국면 ② `tm-join` L1 셋업

**(a) 입력** — 자연어:
> "팀모드 합류할게, 셋업해줘"

**(b) 에이전트** — 도입자와 **명령 형태는 같다**(role 은 자동 판정). 이름 충돌을 피하려고 `--member-name` 명시를 권장한다.
```bash
python infra/install.py --root . --member-name bob --yes
```
> 에이전트는 "도입자/팀원은 자동 판정됩니다. 이미 유효한 `team.config.json` 이 있으니 **팀원**으로 잡힐 거예요. config 는 읽기만 하고 당신 엔트리만 추가합니다"라고 미리 알린다.

**(c) 화면** — `role=member` 로 흐른다. 도입자와 **다른 점은 `[plan] role`** 과 config 무수정:
```
[plan] team_root=/abs/path/our-repo
[plan] role=member (team.name 기본='our-repo')
[plan] agents=['claude']
[plan] member_name=bob
[scaffold] memory/ 구조·members.md 등재 완료 (role=member).
[wire] claude MCP 등록 동기화 완료
[wire] claude 훅 동기화 완료 → /home/me/.claude/settings.json
[wire] claude 스킬 심링크 완료 → /home/me/.claude/skills
[env] /home/me/.bashrc 에 TEAMMODE_HOME 주입 (신규 주입).
[verify] L1 데이터 읽힘 — state=on, members=2, active 마커·배너 생성됨.
[done] L1 부트스트랩 완료. 다음 세션부터 SessionStart 훅이 맥락을 주입합니다.
```
- exit **0**. `members=2` — 기존 alice + 방금 등재된 bob.
- 부수효과: `members.md` 에 `- bob  <!-- id: <bob git email> -->` append, `config.members` 에 `{name:"bob"}` upsert(자기것만, alice 엔트리 무접촉). **`team.config.json` 의 `spec_version`·`team`·`services`·`admin_contact` 는 그대로** — 팀원은 안 건드린다. `team.name` 기본값 계산은 하지만 config 를 안 쓰므로 무의미(읽기만).

> 도입자와 갈리는 막힘/분기:
> - **이름 충돌 (exit 3)** — 내가 `--member-name alice` 로 실행했는데 `members.md` 의 alice 가 **다른 git email** 로 등재돼 있으면:
>   ```
>   [error] 이름 충돌(사람이 해소 필요): members.md 의 'alice' 는 다른 식별자(alice@x)로 등재돼 있습니다. 당신(bob@y)과 충돌 — --member-name 으로 다른 이름을 쓰거나 사람이 해소하세요.
>   ```
>   exit **3**. **에이전트는 임의로 정정하지 않는다**(추측 금지) — 사람에게 `--member-name <다른 영문이름>` 으로 재실행하게 한다.
>   (반대로 같은 이름 + 같은/미상 identity 면 멱등 — 재설치·다른 머신으로 보고 `members.md` 에 추가 안 함, 충돌 아님.)
> - **잘못된 이름**(`..`/슬래시/선두 dash 등 traversal·footgun) → `[error] 멤버 이름 거부: …` + exit **3**.
> - 나머지 게이트(`--yes`/`--settings`/`--dry-run`/preflight/원격 인증)는 도입자와 동일.

**(d) 다음** — 에이전트가 옮긴다: "팀원으로 합류했습니다. config 는 읽기만 했고 당신(bob) 엔트리만 추가했어요. 팀에 이미 기록이 있으니 보여드릴게요." → 첫 가치.

**첫 가치** — 에이전트가:
```bash
python infra/teammode.py context --root . --json
```
**화면** — 도입자와 달리 **기존 팀 세션로그가 보인다**. `state=on`, `members` 에 alice(기존 로그 summary)·bob(아직 로그 없음):
```json
{"state": "on", "index": "…", "members": [
  {"author": "alice", "date": "2026-06-10", "summary": "결제 모듈 리팩터링 …", "role": "pm"},
  {"author": "bob", "date": "…", "summary": "…", "role": null}
]}
```
에이전트는 "지금 팀 상황: alice 가 결제 모듈 작업 중, 당신은 방금 합류. 다음 세션부터 이 맥락이 자동 주입됩니다"로 요약한다. (텍스트 모드면 `- alice(pm) [2026-06-10] summary: …` 형태로 멤버별 한 줄.)

> personality 커스텀: **팀원은 greeting/farewell 을 바꾸지 않는다**(팀 스코프 = 도입자 몫, 읽기만). Obsidian 등록은 호스트 로컬 opt-in 이라 팀원도 각자 `--register-obsidian` 가능.

### 국면 ③ `tm-connect` L2 서비스 연결

**(a) 입력** — `tm-join` 의 L2 제안에 응하거나, 나중에:
> "캘린더 연결해줘"

**(b) 에이전트** — `tm-connect` 실행. 도입자가 calendar 슬롯을 **이미 config 에 선언**해 뒀다면(`services.calendar.provider="google"`) 그 provider 를 **읽어** 쓴다(빈 슬롯일 때만 사람에게 묻는다). `providers/google.json` 의 데이터로 안내.

**(c) 화면 / 사람 몫** — `providers/google.json` 기준(`auth: "oauth"`, `scope: "personal"`, `resource_fields: ["calendar_id"]`):
- 발급: `https://console.cloud.google.com/apis/credentials` → OAuth client ID 생성 → **localhost 리디렉트(PKCE)로 동의 화면에서 "Allow"**(각자 1회, 사람이 권한 부여) → 캘린더 ID 자동조회 → 사용할 캘린더 지정.
- **여기가 사람 몫(보안 경계):** OAuth 동의 화면의 **"Allow" 클릭**은 무인 불가 — 스킬은 동의 게이트 직전까지 데려가고 클릭은 사람이.
- **각자 입력:** scope=personal 이지만 v0.1 은 어차피 각 멤버가 자기 토큰 1회 입력(팀 자동공유 없음). config 슬롯의 provider·`calendar_id` 선언은 도입자가 커밋했어도 **토큰은 내가 직접** 받는다.
- 인스턴스 값: `calendar_id`(예: `primary`)를 `services.calendar` 슬롯에 기록(토큰은 금고, 인스턴스 값만 config).
- 재배선: `python infra/install.py --root . --yes` → google MCP 등록("Google Calendar MCP 서버를 정규 서버명 'google' 로 등록(localhost OAuth/PKCE)").

**(d) 다음** — 연결이 끝나면 L1+L2 가 다 도는 상태. 추가 역할은 같은 패턴으로 붙인다. 빈 슬롯으로 두면 `issue`/관련 동사가 `[info]` 비치명 안내만 — 정상(1급 시민).
