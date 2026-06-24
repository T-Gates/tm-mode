# 설치 · 부트스트랩

tm-mode SPEC v0.2 — 설치·부트스트랩

## §4. 설치 · 부트스트랩 (install.py)

> **진입 계약**: 사람은 에이전트에 "셋업해줘"라고 치지 않는다. 설치는 **CLI(`src/teammode/cli.py`)가 wizard로 끝낸다** — `tm-mode init`(새 팀: 레포 생성 → 곧바로 join) 또는 `tm-mode join <url>`(합류: clone+셋업). CLI가 멤버명·org·팀명·역할·에이전트·Obsidian을 대화로 묻고, clone 완료 후 팀 레포의 `infra/install.py`를 subprocess로 위임 호출한다. 설치가 끝나면 CLI가 *"Claude/Codex를 열고 'tm-onboard' 입력 → 검증·브리핑 자동"*이라고 안내한다(`cli.py _done()`).
>
> **install.py의 역할**: CLI로부터 위임받아 이미 clone된 팀 레포 안에서 실행된다. 스캐폴딩·에이전트 배선·env·훅까지 한 번에 서고, 끝에서 `context --json`으로 **L1 데이터가 읽히는지** 확인한다. install.py의 일반 bootstrap 경로는 **결정적 고정 스크립트**이며 LLM 판단(서비스 선택)은 하지 않는다. 단 `--register-obsidian` 신규 등록 경로는 `time.time()`/`os.urandom()`으로 `ts`/`vault_id`를 생성할 수 있다.
>
> ground truth: 2026-06-16 현재 워킹트리의 `infra/install.py`, `infra/install_lib.py`; 진입 계약은 `src/teammode/cli.py`(`cmd_init`·`cmd_join`·`_wizard_join`·`_done`). 현재 워킹트리에는 `install-skills`/어댑터/테스트 관련 미커밋 변경이 있으며, 이 절은 커밋 여부와 무관하게 **현재 파일 내용**을 반영한다.

### 4.0 설계 원칙

| 원칙 | 의미 |
|---|---|
| **코어 ≠ 스킨** | install.py는 설치 코어. 진입 스킨("셋업해줘"·pipx·npx·플러그인)은 결국 install.py를 호출하는 얇은 층. |
| **결정적** | 일반 bootstrap 경로는 같은 입력과 같은 파일 상태 → 같은 결과를 목표로 한다. 이름·identity·role·settings·root는 명시 인자 또는 git/local 파일에서 읽고, 애매하면 멈춘다. 예외: `--register-obsidian`의 신규 등록은 `ts`/`vault_id` 기본값이 실행마다 달라질 수 있다(§4.9). |
| **env 불신뢰** | 팀 루트는 `--root` 또는 팀 표식 있는 cwd로만 결정한다. ambient `TEAMMODE_HOME`/`TGATES_HOME`은 install 경로에서 신뢰하지 않는다. verify subprocess도 env 화이트리스트만 넘긴다. |
| **L1 자력 도달** | 서비스 연결(L2) 없이도 memory 구조, members, 훅 배선, active marker, context 수집까지 간다. 빈 `services` 슬롯은 정상이다. |
| **크로스에이전트** | wire 단계는 home의 `.claude`·`.codex` 디렉토리 존재로 감지한 에이전트를 각 어댑터 CLI에 위임한다. 단 bootstrap verify는 `context`만 호출하므로(`on` 미사용) 어댑터 로드와 무관하다(§4.4·§4.7). |
| **호스트 쓰기 게이트** | 실 에이전트 설정·실 env는 `--yes` 또는 격리 `--settings` 의도가 있을 때만 처리한다. `--settings`가 있으면 env는 실호스트에 쓰지 않는다. |
| **판단은 위임** | "어느 Notion DB·어느 캘린더"는 install.py 범위 밖이다. provider 연결은 별도 connect/onboard 계층이 채우며, install은 빈 슬롯을 허용한다. |

**범위 외**: 서비스 OAuth/토큰 발급·리소스 선택, 팀 레포 생성(`gh repo create --template`)·clone — 이것들은 `cli.py`(`cmd_init`·`cmd_join`)의 몫이다. 호스팅·대시보드·다이제스트도 범위 밖. 현 `install.py`는 이미 clone된 팀 레포 안에서 실행된다고 가정한다(CLI가 clone 후 subprocess로 위임한다).

### 4.1 CLI 계약

```
python infra/install.py [--root PATH] [--agent VALUE] [--member-name NAME]
                        [--role ROLE] [--settings DIR] [--yes]
                        [--update] [--dry-run]
                        [--register-obsidian] [--obsidian-config PATH]

python infra/install.py --uninstall --root PATH [--settings PATH] [--yes]
                        [--profile PATH] [--obsidian-config PATH]

python infra/install.py --<agent> <adapter-args...>
```

| 플래그 | 의미 | 기본 |
|---|---|---|
| `--root PATH` | 팀 루트 명시. `Path(PATH).resolve()`로 정규화한다. | 미지정 시 cwd가 팀 표식(`.git`/`team.config.json`/`memory`)을 가지면 cwd, 아니면 exit 2 |
| `--agent VALUE` | `Options.agent`에 저장된다. | `auto` |
| `--member-name NAME` | 세션로그 author/member 이름. `opts.member_name` 우선, 없으면 `git config user.name`에서 Unicode 영숫자만 남긴 뒤 lowercase한 제안값 사용. 최종 검증은 `_validate_author` 규칙(Unicode `isalnum()` + `-`/`_`, 첫 글자 영숫자)이다. | 제안 실패 시 없음 → scaffold 전에 exit 3 |
| `--role ROLE` | `team.config.json.members`의 자기 엔트리에 넣을 선택 role 문자열. members.md의 role 판정값이 아니다. | `None`이면 config member role 키 생략/제거 |
| `--settings DIR/PATH` | bootstrap에서는 에이전트 설정·MCP·skills를 실호스트 대신 둘 격리 디렉토리로 해석한다(verify는 `context`만 호출 — settings 파일 안 만듦). `--uninstall`에서는 같은 값이 디렉토리로 해석되지 않고 Claude `settings.json` 파일 경로 문자열로 그대로 쓰인다. | 미지정 |
| `--yes` | `--settings` 없이 실호스트 배선·env 주입까지 진행하겠다는 동의. | off |
| `--update` | `Options.update=True`로 파싱만 된다. | **현 bootstrap에서 사용하지 않음** |
| `--dry-run` | 변경 없이 계획만 출력. settings·memory·env 무접촉. | off |
| `--register-obsidian` | 부트스트랩과 별개인 Obsidian 볼트 등록 단독 액션. 이 플래그가 있으면 bootstrap을 실행하지 않는다. | off |
| `--obsidian-config PATH` | obsidian.json 경로 오버라이드. 미지정=플랫폼 기본. | 플랫폼 기본 |
| `--uninstall` | 부트스트랩/디스패치보다 먼저 잡히는 호스트 되돌리기 액션. | off |
| `--profile PATH` | `--uninstall`에서 POSIX env 제거 대상 프로파일. | 미지정 시 POSIX `~/.bashrc`, Windows는 파일 없음 |

구현상 주의:

- `parse_args()`는 손파싱이다. 위에 열거된 플래그 외 토큰은 조용히 무시한다.
- `--agent`는 현재 bootstrap에서 배선 필터로 사용되지 않는다. 실제 배선 대상은 `_detect()`가 감지한 전체 에이전트 목록이다. 따라서 `--agent codex`를 줘도 현재 코드의 `wire_agents()` 호출에는 반영되지 않는다.
- `--register-obsidian`이 있으면 `--dry-run`, `--yes`, `--member-name` 등 bootstrap 관련 플래그는 의미가 없다.

**종료 코드**: `0` 성공 또는 비치명 skip / `2` 전제·인자 오류 / `3` 이름 결정 실패·이름 충돌·wire 부분 실패·verify 실패. `--register-obsidian`은 root 오류만 exit 2이고, Obsidian 미설치/깨짐/중복 등 등록 실패는 비치명 `0`이다.

### 4.2 엔트리 분기와 디스패처

`main(argv)`의 분기는 순서가 고정이다.

1. `ensure_utf8_io()`를 먼저 호출해 stdout/stderr를 UTF-8로 보정한다. stdin은 보정하지 않는다.
2. argv에 `--uninstall`이 있으면 `_parse_uninstall()` 후 `cmd_uninstall()`로 간다. 다른 bootstrap/dispatch 분기는 보지 않는다.
3. `_split_agent(argv)`가 첫 번째로 발견한 `--<agent>` 중 `infra/agents/<agent>/` 디렉토리가 존재하는 것을 agent로 잡는다. 그 플래그만 제거하고 나머지 argv를 어댑터에 넘긴다.
4. agent가 있으면 `_dispatch(agent, rest)`:
   - `agents/<agent>/adapter.py`가 파일이 아니면 exit 2.
   - `rest`에 `--settings`도 `--install`도 없으면 exit 2. 명시 없이 실호스트 설정에 쓰지 않는다.
   - 이 게이트는 `--config`를 안전 의도로 인정하지 않는다. 따라서 `python infra/install.py --codex --config <path> sync`는 Codex 어댑터에 도달하기 전에 exit 2다.
   - `--settings`는 디스패처 게이트를 통과시키지만 Codex 어댑터 CLI에는 없는 옵션이다. Codex 디스패치에서 명시 config를 쓰려면 현재 구현상 `--install` 게이트와 Codex `--config`를 함께 써야 한다.
   - `--install`은 디스패처 전용 플래그라 어댑터 argv에서는 제거한다.
   - `sys.argv = [adapter_path] + rest`로 바꾼 뒤 `runpy.run_path()`로 adapter.py를 로드하고 `main(rest)`를 호출한다. 반환 rc를 그대로 반환한다.
5. agent는 없지만 argv에 `sync` 또는 `uninstall` 토큰이 있으면 "에이전트를 지정하세요: --<agent>" 오류와 사용 가능한 agent 디렉토리 목록을 출력하고 exit 2.
6. 그 외는 bootstrap 인자로 파싱한다.
7. 파싱 결과 `register_obsidian=True`이면 `register_obsidian()` 단독 액션을 실행하고 종료한다.
8. 나머지는 `bootstrap()`을 실행한다.

`--uninstall`의 `_parse_uninstall()`은 `--root`, `--settings`, `--profile`, `--obsidian-config`만 값을 소비하고 `--yes`만 bool로 인식한다. 알 수 없는 부울 플래그는 무시한다. 값 플래그 뒤에 값이 없으면 `None`이 들어갈 수 있다.

### 4.3 전제조건과 detect

preflight 함수는 `install_lib.preflight(team_root, python_version, git_present, remote_authed)`이며, 입력값은 호출자가 주입한다.

| 전제 | 구현 | 실패 |
|---|---|---|
| Python | `python_version >= MIN_PYTHON`, 현재 `MIN_PYTHON = (3, 9)` | `PreflightResult(ok=False, exit_code=2, message=...)` → bootstrap exit 2 |
| git 바이너리 | bootstrap에서 `shutil.which("git") is not None` | exit 2 |
| 팀 루트 표식 | `has_team_marker(team_root)`: `.git`, `team.config.json`, `memory` 중 하나 존재 | exit 2 |
| 원격 인증 | preflight 인자로는 현재 bootstrap이 `remote_authed=True`를 넣는다. 실제 인증 확인은 detect 후 별도 경고 | 인증 실패만으로 종료하지 않음 |

root 해석:

- `--root`가 있으면 `Path(opts.root).resolve()`를 쓴다. 존재 여부를 별도로 검사하지 않고, preflight의 marker 검사에서 실패할 수 있다.
- `--root`가 없으면 cwd가 팀 표식을 가질 때만 cwd를 쓴다.
- 둘 다 아니면 bootstrap/register-obsidian은 `[error] --root <팀루트> 가 필요합니다... 환경변수(TEAMMODE_HOME)는 읽지 않습니다.` 후 exit 2.

detect 함수 `_detect(team_root, home)`는 읽기만 한다.

| 키 | 값 |
|---|---|
| `remote_url` | `git remote get-url origin`, 실패/timeout/git 부재면 `None` |
| `team_name_default` | remote URL 마지막 세그먼트에서 `.git` 제거. remote가 없으면 `None` |
| `git_user_name` | `git config user.name`, 실패 시 `None` |
| `git_user_email` | `git config user.email`, 실패 시 `None`; members.md identity 주석에 사용 |
| `member_name_suggestion` | git user.name을 lowercase 후 `[^a-z0-9]` 제거. 빈 문자열이면 `None` |
| `agents` | `home/.claude`, `home/.codex` 디렉토리 존재 여부를 보고 `["claude", "codex"]` 중 정렬 반환 |
| `remote_authed` | remote가 있고 `git ls-remote --exit-code origin HEAD`가 5초 안에 성공하면 true |
| `role` | `detect_role(team_root)` 결과 |

`_git()`은 `GIT_TERMINAL_PROMPT=0`, timeout 5초, capture_output으로 실행한다. 실패·timeout·git 부재·비zero rc는 `None`이다. remote 인증 실패는 bootstrap에서 `[warn] git 원격 인증 미확인...`만 출력하고 계속한다.

### 4.4 절차 (멱등하게 순서대로)

```
① preflight   Python 버전·git 바이너리·팀 표식 검사. 실패 즉시 exit 2.
② detect      git remote→org/repo, git user.name→이름 제안, user.email→identity,
              설치 에이전트(~/.claude·~/.codex), 원격 인증, role(③)
③ role        team.config.json 유효(spec_version + team.name 비-placeholder) → 팀원(§4.5)
              부재/미초기화 → 도입자(§4.5). ※ services 채움 여부로 가르지 않음(빈 슬롯 정상)
④ plan        team_root, role, agents, member_name 출력. --dry-run이면 여기서 exit 0.
⑤ scaffold    memory/ 구조·INDEX·members.md 등재·banner 선기록, 도입자는 최소 config.
              첫 세션로그는 쓰지 않는다(디렉토리만).
⑥ wire        감지 에이전트마다 install-mcp → sync --on → install-skills.
              실호스트 배선은 --yes 또는 --settings 필요.
⑦ env         런타임 훅용 TEAMMODE_HOME 영구 주입 — POSIX:셸 프로파일 1줄 / Windows:setx (§4.8)
⑧ verify      context --json 으로 설치 확인(데이터 읽힘) — 팀모드 on 은 호출하지 않는다
              (설치 ≠ 활성화 — on 의 auto_update 부작용 회피). 활성화는 사용자가 `tm on` 할 때만.
              ※ 실제 *맥락 주입*은 여기가 아니라 다음 세션의 SessionStart 훅(§1.6·§2.4)
```

세부 불변식:

- `--dry-run`은 detect와 plan 출력 후 즉시 exit 0이다. scaffold 이전이므로 memory/settings/env/verify를 건드리지 않는다.
- 멤버 이름은 `opts.member_name or det["member_name_suggestion"]`이다. 둘 다 없으면 scaffold 전에 exit 3.
- `team_name_default`는 remote repo name이 있으면 그것, 없으면 `team_root.name`이다.
- `shell="__env__"` 기본값은 `os.environ["SHELL"]`에서 bash/zsh/fish 감지에만 쓰인다. 팀 root 결정에는 env를 쓰지 않는다.
- `platform=None`이면 `sys.platform`으로 Windows/POSIX env 분기를 결정한다.
- verify는 `_engine_capture(["context", "--root", team_root, "--json"])`만 호출한다(**`on` 미사용** — `cmd_on`의 `auto_update_on_start`가 팀 레포에 자동 커밋을 남기는 부작용 회피). `context` rc 비zero·stdout JSON 파싱 실패는 exit 3이다. settings·active 마커를 만들지 않으며 팀모드를 켜지 않는다(설치 ≠ 활성화).
- `_engine_capture()`는 subprocess env를 `PATH`, `HOME`, `LANG`, `LC_ALL`, `LC_CTYPE`, `TMPDIR`, `TZ`, `PYTHONPATH`, `TERM`, `XDG_STATE_HOME`로 제한한다. ambient `TEAMMODE_HOME`/`TGATES_HOME`은 넘기지 않는다.

### 4.5 도입자/팀원 role 분기

**role 판정(closed — `config_is_valid`)**: `team.config.json`이 dict이고 `spec_version`(truthy) + `team.name`(str, placeholder 아님)이면 **팀원(member)**, 아니면 **도입자(introducer)**. placeholder = `{"", "changeme", "todo", "your-team-name", "team-name", "tbd", "placeholder"}`. services 채움 여부로 가르지 않는다.

`load_config()`는 `team.config.json` 부재, JSON parse 실패, read 실패 모두 `None`으로 처리한다. `providers_dir` 인자는 `config_is_valid()` 시그니처 호환용으로 받지만 role 판정에는 사용하지 않는다. provider 팩 누락·services 스키마 위반·members 스키마 위반은 introducer/member 판정을 뒤집지 않는다.

scaffold 대상:

- 공통 디렉토리: `memory/team`, `memory/team/decisions/archive`, `memory/team/meeting/summary`, `memory/team/meeting/raw`, `memory/team/sessions/<member_name>`.
- 공통 파일: `memory/INDEX.md`와 `memory/team/decisions/current.md`는 없을 때만 쓴다.
- `memory/team/members.md`는 없으면 헤더를 만들고, member line을 append한다.
- `memory/banner.txt`는 없을 때만 `=== <team_name> ===\n`으로 쓴다.
- 첫 세션로그 파일은 만들지 않는다.

도입자 경로:

- `role == "introducer"`일 때만 `write_introducer_config()`를 호출한다.
- 이미 `config_is_valid(load_config(team_root))`이면 config를 덮어쓰지 않는다.
- 새 config는 다음 shape로 쓴다.
  ```json
  {
    "spec_version": "0.2",
    "team": {
      "name": "<team_name>",
      "timezone": "Asia/Seoul",
      "locale": "ko_KR",
      "greeting": "<team_name> 팀모드 ON",
      "farewell": "수고하셨습니다 — <team_name>"
    },
    "admin_contact": "<member_name>",
    "members_file": "memory/team/members.md",
    "banner_file": "memory/banner.txt",
    "services": {}
  }
  ```
- `timezone`/`locale` 인자가 truthy면 그 값을 쓰지만, 현재 `_detect()`는 이 키를 만들지 않으므로 bootstrap 경로에서는 기본값 `Asia/Seoul`, `ko_KR`이 들어간다.

팀원 경로:

- `role == "member"`이면 config의 team/services/admin 필드는 쓰지 않는다.
- 단, 현재 구현은 도입자와 팀원 모두 `upsert_member_role()`을 호출해 `team.config.json.members`의 **자기 name 엔트리만** 추가/갱신할 수 있다. 이것은 기존 "팀원은 config 읽기만"보다 현재 코드가 더 넓다.
- `members`가 없으면 `[]`로 보고 자기 엔트리를 append한다. `members`가 list가 아니거나 config 부재/깨짐이면 무작업이다.
- 같은 name 엔트리가 있으면 기존 추가 키는 보존하고 `name`/`role`만 갱신한다. `--role`이 없거나 빈 문자열이면 기존 자기 엔트리의 `role` 키를 제거한다.
- 타인 엔트리는 순서·내용 모두 건드리지 않는다.

members/services 스키마 검증:

- `services_are_valid()`는 role 판정에 사용되지 않는다. 정규 role key는 `issues`, `chat`, `docs`, `calendar`; slot은 object여야 하며 provider 필수, provider pack 존재 필수, scope는 있으면 `team|personal`, provider pack의 `resource_fields`가 모두 non-empty string이어야 한다. 추가 키는 허용한다. provider 파일이 존재하지만 JSON/schema가 잘못되면 `ProviderValidationError`가 전파될 수 있다.
- `members_are_valid()`도 role 판정에 사용되지 않는다. bootstrap은 scaffold 뒤 config가 dict이고 members가 유효하지 않으면 warn만 출력한다. members entry는 `{name 필수, role 선택, 추가 키 허용}`이고 name은 엔진 `_validate_author`, role은 비어 있지 않은 string이며 개행·제어문자를 금지한다.

**이름 충돌 정책(closed — M4·결정적):**
- 같은 영문 이름 + 같은(또는 미상) identity → 추가 안 함, 본인 항목 간주(멱등). 동일인 재설치/다른 머신 = 정상.
- 같은 이름 + **다른 identity**(둘 다 식별자 존재) → **exit 3**(사람이 해소, "나인가 남인가" 추측 금지). reference: `register_member`가 `ConflictError`.
- identity 미상(레거시 항목 또는 미주입)이면 충돌로 보지 않음(멱등).
- 다른 이름을 원하면 `--member-name`으로 오버라이드. 잘못된 이름(traversal·선두 dash 등)은 `InvalidNameError` → exit 3. 이름 검증은 엔진 `_validate_author` 단일 소스 재사용이다.
- identity는 detect한 `git config user.email`이다. 없으면 members.md line에 id 주석을 붙이지 않는다.
- members.md line은 `- <name>  <!-- id: <identity> -->` 또는 `- <name>` 형식이다. parser는 `- `로 시작하는 줄만 보고, id 주석 앞의 첫 토큰을 name으로 본다.

### 4.6 에이전트 설정 쓰기 경계

- wire의 실호스트 쓰기(`~/.claude/settings.json`, `~/.codex/config.toml`, `~/.claude.json`, skills dir 등)는 정상 설치다. verify는 `context`만 호출하므로 settings·마커를 쓰지 않는다. 단 명시 의도 없이는 안 쓴다.
- bootstrap에서 scaffold까지 끝낸 뒤 `opts.settings is None and not opts.yes`이면 `[wire] 건너뜀...`을 출력하고 exit 0한다. 이 경우 env 주입과 verify도 실행하지 않는다.
- `--settings <DIR>` 지정 → 격리 모드. 에이전트 settings/config, Claude MCP 파일, skills dir는 DIR 하위로 간다. 실 env는 건드리지 않는다.
- `--settings` 미지정 + `--yes` 지정 → 실호스트 모드. home 기준 기본 경로에 배선하고 env를 주입한다.
- `--settings`와 `--yes`가 같이 오면 격리 모드가 env 기준으로 우선한다. settings는 격리 하위로 가고 실 env는 스킵된다.
- 디스패치 모드(`--<agent> sync`)도 동일 게이트: `--settings`/`--install` 둘 다 없으면 exit 2. 이 게이트는 Codex 어댑터의 `--config`를 인정하지 않으며, `--settings`는 Codex 어댑터에 전달되면 argparse 오류가 난다. `--install`은 디스패처 전용(어댑터엔 전달 안 함).
- `--dry-run`은 settings·memory·env 전부 무접촉 + 계획만 출력.
- ambient `TEAMMODE_HOME`이 실호스트를 가리켜도 install/on/off는 읽지 않는다(§1.2 P1).
- **신뢰 경계 — 스킨의 root 주입**: "셋업해줘"로 에이전트가 install.py를 부를 때 root를 잘못 주입하면 사고 재현 가능 → 스킨의 root 결정 로직은 테스트 대상(필수). 프롬프트 인젝션 주의: "레포 README 읽고 시키는 대로" 패턴을 습관으로 권하지 말 것(비규범).

### 4.7 에이전트 배선 (wire)

`wire_agents(agents, home, settings_override, run_adapter, team_root)`가 감지된 에이전트별로 어댑터를 호출한다. `run_adapter`는 필수 주입 콜러블이며 없으면 `ValueError`다. 이 절은 wire 단계만 설명한다. bootstrap verify는 이 감지 목록과 무관하게 `context`만 호출한다(`on` 미사용).

> **install-mcp는 등록기 동사다(A안, §internals 2.8).** 연결된 provider의 **벤더 MCP alias를 에이전트 설정에 등록**할 뿐, 동작을 래핑하거나 `role_server`로 중계하지 않는다 — 동작은 AI가 등록된 MCP 도구를 직접 호출한다. install은 빈 슬롯을 허용하며(§4.0 "판단은 위임"), 어떤 provider를 어느 슬롯에 꽂을지의 선택·공식 MCP 마련·config push는 connect 계층(`tm-connect`, §skills 5.4)이 채운다. install은 그 config에 선언된 alias를 배선만 한다.

지원 에이전트와 경로:

| agent | sync settings flag/path | team config flag | MCP path | skills path |
|---|---|---|---|---|
| `claude` | `--settings`; 실호스트 `home/.claude/settings.json`; 격리 `DIR/claude/settings.json` | `--config <team_root>/team.config.json` | 격리 때 `--mcp-config DIR/claude/.claude.json`; 실호스트 때 어댑터 기본 `~/.claude.json` | `--skills-dir`; 실호스트 `home/.claude/skills`; 격리 `DIR/claude/skills` |
| `codex` | `--config`; 실호스트 `home/.codex/config.toml`; 격리 `DIR/codex/config.toml` | `--team-config <team_root>/team.config.json` | 별도 플래그 없음. config.toml 안 블록 사용 | `--skills-dir`; 실호스트 `home/.codex/skills`; 격리 `DIR/codex/skills` |

호출 순서와 실패 분기:

1. 미지원 agent 이름이면 `(agent, "지원하지 않는 에이전트")`를 failed에 넣고 계속한다.
2. `install-mcp`: `run_adapter(agent, "install-mcp", settings_flag, settings_path, cfg_extra + mcp_extra)`.
   - rc가 0이 아니면 해당 agent 실패 `install-mcp rc=<rc>`로 집계하고 sync/install-skills는 건너뛴다.
   - 성공하면 `[wire] <agent> MCP 등록 동기화 완료`.
3. `sync --on`: 같은 settings path와 team config extra, 그리고 claude 격리 MCP extra를 다시 넘긴다.
   - `_make_run_adapter()`는 verb가 `sync`이면 argv를 `[global_flags..., "sync", "--on"]`으로 만든다.
   - rc가 0이 아니면 해당 agent 실패 `sync rc=<rc>`로 집계하고 install-skills는 건너뛴다.
   - 성공하면 `[wire] <agent> 훅 동기화 완료 → <path>`.
4. `install-skills`: `cfg_extra + [skills_flag, skills_path]`를 넘긴다.
   - rc 0이면 agent를 wired에 넣고 `[wire] <agent> 스킬 심링크 완료 → <skills_path>`.
   - rc 비zero면 해당 agent 실패 `install-skills rc=<rc>`로 집계한다.
5. 어떤 adapter 호출이 예외를 던져도 그 agent만 실패로 집계하고 다른 agent는 계속한다.
6. 하나라도 실패하면 `WireResult(ok=False, exit_code=3)`. 성공분은 롤백하지 않는다.

`_make_run_adapter()`는 adapter.py를 `runpy.run_path()`로 매번 로드해 `main(argv)`를 호출한다. settings path의 부모 디렉토리는 미리 만든다. extra_args는 글로벌 플래그로 subcommand 앞에 놓인다. `install-mcp`/`install-skills`는 `[global_flags..., verb]`, `sync`는 `[global_flags..., "sync", "--on"]`이다.

### 4.8 환경변수 주입 (§9)

- 변수명은 `TEAMMODE_HOME`이다. install/on/off 같은 의도적 호출은 이 env를 신뢰하지 않고, 런타임 훅이 팀 루트를 찾는 용도로만 쓴다.
- 격리 모드(`--settings`)에서는 env 주입을 항상 건너뛰고 수동 설정 안내만 출력한다.
- Windows(`sys.platform`이 `win` 또는 `cygwin`으로 시작)는 셸 프로파일을 쓰지 않고 `setx TEAMMODE_HOME <abs team_root>`를 실행한다. 성공하면 profile 표시는 `HKCU\Environment\TEAMMODE_HOME`이다. setx 예외 또는 rc 비zero는 비치명 `injected=False`.
- POSIX는 shell 종류별 프로파일에 마커 줄을 쓴다.
  - bash: `<home>/.bashrc`, `export TEAMMODE_HOME="<team_root>"  # teammode (env injection, §9)`
  - zsh: `<home>/.zshrc`, 같은 export 형식
  - fish: `<home>/.config/fish/config.fish`, `set -gx TEAMMODE_HOME "<team_root>"  # teammode (env injection, §9)`
- shell 감지는 `$SHELL` 경로 basename에 `bash`/`zsh`/`fish`가 포함되는지로 한다. 테스트 주입처럼 `shell`이 이미 종류 문자열이면 그대로 쓴다.
- 프로파일이 없으면 부모 디렉토리를 만들고 새 파일을 쓴다.
- 기존 마커 줄이 1개이고 내용이 같으면 무변경이다.
- 기존 마커 줄이 있되 값이 다르거나 마커가 여러 개면 모든 기존 마커 줄을 제거하고 새 줄 1개만 append한다.
- 미지원/미감지 shell은 비치명 안내만 출력한다.

env 제거(`remove_injected_env`)는 uninstall에서만 사용한다. Windows는 `reg delete HKCU\Environment /v TEAMMODE_HOME /f`, POSIX는 `# teammode (env injection` prefix가 들어간 줄만 삭제한다. 파일 부재·마커 부재·write 실패·reg 실패는 모두 `False`이며 raise하지 않는다.

### 4.9 Obsidian 볼트 등록 (`--register-obsidian`, opt-in)

05 draft의 "Obsidian 뷰"가 reference에서 **단독 opt-in 액션으로 구현**됨(부트스트랩과 별개, 온보딩 후 언제든 실행 가능). root 해석 오류를 제외하면 **비치명 — 등록 실패도 exit 0**이다.

- memory/를 볼트화(`.obsidian/` 없으면 생성 — core: graph/backlink/global-search, community: dataview; 쓰기 실패해도 빈 `.obsidian/`로 비치명).
- obsidian.json 경로: `--obsidian-config` 우선, 미지정 시 플랫폼 기본(linux `~/.config/obsidian/obsidian.json`, mac `~/Library/Application Support/obsidian/obsidian.json`, win `<home>/AppData/Roaming/obsidian/obsidian.json`; pure helper는 `appdata` 주입 가능). 경로·id·ts는 주입 가능하다.
- `register_obsidian()` 호출에서 `now_ms`/`vault_id`를 주입하지 않으면 `time.time()`과 `os.urandom(8).hex()`로 생성한다. 따라서 같은 입력과 같은 파일 상태라도 신규 등록 항목의 `ts`/id는 실행마다 달라질 수 있다. 이미 같은 path가 등록된 경우는 skip되어 결과가 멱등이다.
- **merge 등록(clobber 0)**: 기존 vaults 전부 보존하고 신규 항목만 추가. 항목 = `{"<16hex id>": {"path": <memory 절대경로>, "ts": <ms>, "open": false}}`. 원자 쓰기(temp+os.replace, 심링크면 실타깃에 replace해 링크 유지).
- **안전 skip(비치명)**: 설정 디렉토리 부재(Obsidian 미설치)·obsidian.json 파싱 실패·최상위가 object 아님·vaults가 dict 아님·같은 path 이미 등록(멱등)·vault_id 충돌 → 등록 안 하고 `registered=False`. 어떤 오류도 raise하지 않음.
- `register_obsidian()` 자체는 root를 해석해야 하므로 root가 없고 cwd에도 팀 표식이 없으면 exit 2다. 그 외 등록 실패는 메시지만 출력하고 exit 0이다.

### 4.10 호스트 되돌리기 (`--uninstall`)

`cmd_uninstall()`은 install이 호스트에 더한 흔적 중 off·Claude hook·env·Obsidian 등록을 역순으로 되돌리는 별도 분기다. memory/ 팀 데이터는 절대 삭제하지 않는다. MCP 등록과 skills 설치 흔적은 현 구현에서 회수하지 않는다.

필수 게이트:

- `--root`가 없으면 exit 2.
- `--settings`도 `--yes`도 없으면 exit 2. 실 `~/.claude` 되돌리기도 명시 의도 없이는 하지 않는다.
- settings path는 `--settings`가 있으면 그 값을 파일 경로 문자열로 그대로 쓰고, 없으면 `~/.claude/settings.json`이다. bootstrap의 `--settings <격리 디렉토리>` 의미와 다르므로, uninstall에 디렉토리를 넘기면 그 디렉토리 자체를 settings 파일처럼 취급한다.

단계와 실패 정책:

1. `teammode.py`를 `runpy.run_path()`로 로드해 `cmd_off(team_root, settings_path)` 호출. `.teammode-active`가 있었고 호출 후 없어졌으면 제거 목록에 active marker를 넣는다. 예외는 warn 후 계속.
2. Claude adapter를 로드해 `Adapter(...).uninstall()` 호출. `[remove]` 메시지가 있으면 settings hook 제거로 기록한다. 예외는 warn 후 계속.
3. env 제거:
   - `--settings`가 있고 `--profile`이 없으면 격리 대칭 원칙으로 실 env 제거를 건너뛴다.
   - 그 외 POSIX는 `--profile` 또는 기본 `~/.bashrc`에서 marker 줄만 제거한다.
   - Windows는 profile 인자와 무관하게 `reg delete`를 시도한다.
   - 예외는 warn 후 계속.
4. Obsidian 등록 해제: `--obsidian-config` 또는 플랫폼 기본 obsidian.json에서 `team_root/memory` path와 일치하는 vault 항목만 제거한다. 예외는 warn 후 계속.
5. 제거한 항목이 있으면 목록을 출력하고, 없으면 "되돌릴 호스트 변경 없음"을 출력한다. 항상 memory 보존 문구를 출력한다.

현 구현상 uninstall은 claude·codex **양쪽** 어댑터의 `Adapter(...).uninstall()`·`uninstall_skills()`를 호출한다(흔적 0 대칭, #4). claude `uninstall()`은 `settings.json` 훅만 제거하고, codex `uninstall()`은 `config.toml`의 훅 블록(`teammode-hooks-*`)과 MCP 블록(`teammode-mcp-*`)을 함께 제거한다(codex는 훅·MCP가 단일 파일). codex 경로는 claude `settings.json` 경로의 조부모(격리 루트)에서 `agent_settings_path`로 파생한다. 단 claude `.claude.json`의 MCP 등록(`install-mcp`) 역동작은 여전히 호출하지 않는다(claude MCP·statusLine 정리는 후속).

### 4.11 합격 기준 (골든 — §6 시나리오 후보)

| # | 시나리오 | 합격 |
|---|---|---|
| I1 | 빈/엔진만 레포(config 없음)에서 `--yes` 또는 `--settings` install | 도입자 경로 완주 → memory/·최소 config(`spec_version: "0.2"`, 빈 services)·sessions/·members·banner·wire·env(실설치만)·verify. 첫 로그 미생성 |
| I1b | 빈/엔진만 레포에서 `--yes`/`--settings` 없이 install | scaffold까지 완료 후 wire/env/verify 건너뛰고 exit 0 |
| I2 | 유효 config 레포에서 install | member 경로 → team/services/admin 필드 보존, members.md 등재, config.members 자기 엔트리만 upsert, 배선. context가 기존 로그 읽음 |
| I2b | I1/I2 직후 새 세션 시작 | SessionStart 훅이 맥락을 **실제 주입**(install이 아니라 여기서) |
| I3 | I1 직후 재실행 | 멱등 — 중복 생성 0, 변경 없음 |
| I4 | ambient `TEAMMODE_HOME`=실호스트 set 실행 | 실호스트 무접촉(env 격리) |
| I4b | bootstrap에서 `--settings <격리디렉토리>` 지정 | 실 `~/.claude/settings.json` **및 실 호스트 env(POSIX 셸 프로파일 / Windows 레지스트리) 무접촉**, 격리 디렉토리 하위 경로에만. bootstrap에서 `--settings`=env 격리 권위(`--yes` 와 와도 격리 우선). 실 env 주입은 `--settings` 없는 `--yes` 에서만 |
| I-win | nt 모킹(`platform=win32` + setx/reg runner 주입) 라운드트립 | install→on→context→uninstall: env 는 setx/reg delete(셸 프로파일 무접촉), active 마커·memory 정상, 실 setx/reg 미실행. (실 Windows 검증은 native 환경 권장) |
| I-dry | `--dry-run` | settings·memory·env 무접촉 + 계획만 |
| I5 | `--agent codex`만으로 단일 agent 배선 기대 | 현재 구현은 `--agent`를 필터로 쓰지 않으므로 감지된 전체 agent 배선. 단일 agent 필터는 잔여갭 |
| I6 / I6b | Python 하한 미달/git 바이너리 부재 / 원격 인증만 부재 | I6: exit 2 무변경 / I6b: 경고 후 로컬 L1 진행 |
| I7 / I8 | 같은 이름 재설치 / `--member-name`이 타인 등재명 충돌 | I7: 멱등 본인 항목 / I8: exit 3 + members.md 무변경 |
| I9 | `--register-obsidian`에서 Obsidian 미설치/깨진 obsidian.json | root만 유효하면 비치명 skip + exit 0 |
| I10 | `--uninstall --root <r> --settings <settings-file-path>` | off·Claude hook·해당 Obsidian vault 제거 시도, 실 env 제거는 `--profile` 없으면 스킵, memory 보존. MCP/skills 흔적은 현재 이 경로에서 제거하지 않음 |

---

