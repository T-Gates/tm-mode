# 설치 — tm-mode

tm-mode 설치의 **단일 소스**다. 진입은 `tm-mode` CLI 한 줄 — 레포 생성/clone·scaffold·훅 배선·스킬 배포·env 주입까지 wizard가 전부 처리한다.

## 요구사항
- **Python 3.9+**, **git**
- 새 팀 생성(`init`)은 **GitHub CLI(`gh`)** 도 필요(인증된 상태 — `gh auth login`)

## 1. 런처 설치 (pip 또는 curl — 택1)
```bash
# pip
pip install "git+https://github.com/T-Gates/tm-mode"

# 또는 curl (pip 없이) — 아래 2단계 명령을 그대로 이어붙인다:
#   curl -fsSL https://raw.githubusercontent.com/T-Gates/tm-mode/main/install.sh | sh -s -- <명령>
```

## 2. 팀 만들기 / 합류

### 새 팀 — 도입자
```bash
tm-mode init
```
org·계정, 팀명, 레포명을 wizard가 묻고 → 레포 생성(template) → 곧바로 본인 머신에 설치(clone+셋업)까지 한 번에. (비대화로 지정하려면 `tm-mode init OWNER/REPO`.)

### 기존 팀 합류 — 팀원
```bash
tm-mode join <팀레포 clone-url>
```
설치 위치·에이전트(claude/codex)·이름·역할·Obsidian을 wizard가 묻고 clone+셋업.

> curl 진입도 동일하다 — `... | sh -s -- init` / `... | sh -s -- join <url>`.

설치가 끝나면 CLI가 안내한다: **Claude Code나 Codex를 열고 `tm-onboard`라고 입력** → 설치 검증·팀모드 가치 브리핑이 자동으로 진행된다. (설치는 됐지만 팀모드는 아직 꺼져 있다 — **설치 ≠ 활성화.**)

## 3. 팀모드 켜기 (활성화)
설치는 팀모드를 자동으로 켜지 않는다. 작업을 시작할 때 켠다:
```bash
# 에이전트에게 "팀모드 켜" / "tm on"   (또는 직접:)
python infra/teammode.py on --root . --install
```
켜면 다음 세션부터 `session-start` 훅이 팀 맥락을 자동 주입한다. 끄려면 `... off --root . --install`.

## 4. (선택) Obsidian 볼트
`join` wizard가 묻는다. 나중에 다시 붙이려면 같은 위치에서 `tm-mode join <url>`을 다시 실행한다(멱등). `memory/`를 Obsidian 그래프로 본다. 미설치면 우아하게 skip(아무것도 안 만듦).

---

## 부록 — `install.py` / 플래그 (고급·내부)
정상 진입에선 직접 쓸 일이 없다 — `tm-mode init/join`이 clone된 레포의 `infra/install.py`를 subprocess로 위임 호출한다(`--root . --yes`). 디버그·격리·되돌리기 시에만 참고:

| 플래그 | 역할 |
|---|---|
| `--yes` | 실 에이전트 설정(`~/.claude/settings.json` 등)에 배선 허용. **없으면 안 씀**(안전 게이트). CLI는 항상 `--yes`로 호출 |
| `--settings <디렉토리>` | 격리 실행 — 실 호스트 무접촉(테스트·CI). 경로는 디렉토리(그 아래 에이전트별 설정 생성) |
| `--dry-run` | 변경 없이 계획만 출력 |
| `--register-obsidian` | Obsidian 볼트 등록(opt-in) |
| `--uninstall` | install이 호스트에 더한 것(훅·스킬·env·마커)을 역순 제거 |

### 엔진 동사 (teammode.py)
| 동사 | 역할 |
|---|---|
| `on` / `off` | 팀모드 켜기·끄기 (배너·훅·active 마커) |
| `log` | 세션로그 기록 (날짜·frontmatter 자동) |
| `context` | 전원 최근 세션로그·상태를 JSON으로 수집 (요약은 스킬 몫) |
| `pull` / `commit` / `update` | git 동기화·커밋·upstream 갱신 |

모든 동사는 팀 루트를 `--root`로 명시받는다(환경변수 무신뢰 — 안전).

### 제거
`tm-mode` CLI에는 uninstall이 없다. 호스트에서 제거하려면 팀 레포 안에서:
```bash
python infra/install.py --root . --uninstall
```

---

tm-mode가 **무엇인지·왜 쓰는지**는 [README.md](README.md), 동작 명세는 [docs/spec/](docs/spec/README.md) 참조.
