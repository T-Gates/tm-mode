# 설치 — teammode

teammode 설치 절차의 **단일 소스**다. 사람이 직접 따라 해도 되고, 에이전트에게 맡겨도 된다.

> **권장 — 에이전트에게 맡기기 (사람 정독 0):**
> 에이전트(Claude Code · Codex)에게 그대로 말하세요:
> ```
> 이 https://github.com/T-Gates/teammode 레포 셋업해줘
> ```
> 에이전트가 `AGENTS.md`를 거쳐 이 문서의 절차를 읽고 알아서 실행합니다. 클론·설정·정독은 에이전트 몫.
>
> **직접 하기:** 아래 절차를 따른다.

## 요구사항

- **Python 3.9+**
- **git** (메모리가 git 기반)

## 1. 설치

### 도입자 — 팀을 새로 시작
이 레포를 템플릿으로 새 팀 레포를 만든 뒤, 그 안에서:

```bash
python infra/install.py --root . --yes
```

### 팀원 — 기존 팀에 합류
팀 레포를 clone 한 뒤:

```bash
python infra/install.py --root . --member-name <영문이름> --yes
```

`install.py`가 기계적인 것(스캐폴드·훅 배선·스킬 배포·env 주입·검증)을 전부 한다. 끝나면 설치는 됐지만 **팀모드는 아직 꺼져 있다** — 다음 단계에서 켠다.

## 2. 팀모드 켜기 (활성화)

**설치는 팀모드를 자동으로 켜지 않는다 — 설치 ≠ 활성화.** 사용자가 명시적으로 켜야 한다:

```bash
python infra/teammode.py on --root . --install   # 또는 에이전트에게 "팀모드 켜"
```

`on`/`off`는 어디에 배선할지 알아야 하므로 `--install`(실 호스트) 또는 `--settings <경로>`(격리)가 **필수**다.

- 설치 직후 `tm-onboard`가 **"지금 팀모드를 켤까요?"** 하고 제안한다 — 동의하면 그때 켜진다.
- 켜면 다음 세션부터 `session-start` 훅이 팀 맥락을 자동 주입한다. 끄려면 `teammode.py off --root . --install`.

## 3. (선택) Obsidian 볼트 등록

`memory/`를 Obsidian으로 그래프처럼 보려면 — 온보딩 때 안 했어도 언제든:

```bash
python infra/install.py --root . --register-obsidian
```

Obsidian 미설치면 우아하게 skip(아무것도 안 만듦). `obsidian.json`은 실 호스트 설정이라 이 opt-in 명령으로만 건드린다.

## 플래그

| 플래그 | 역할 |
|---|---|
| `--yes` | 실 에이전트 설정(`~/.claude/settings.json` 등)에 배선 허용. **없으면 안 씀**(안전 게이트) |
| `--settings <디렉토리>` | 격리 실행 — 실 호스트 무접촉 (테스트·CI). 경로는 디렉토리(그 아래 에이전트별 설정 파일 생성) |
| `--dry-run` | 변경 없이 계획만 출력 |
| `--register-obsidian` | Obsidian 볼트 등록 (opt-in) |
| `--uninstall` | install이 호스트에 더한 것(훅·스킬·env·마커)을 역순 제거 |

## 설치 후 — 엔진 동사 (teammode.py)

| 동사 | 역할 |
|---|---|
| `on` / `off` | 팀모드 켜기·끄기 (배너·훅·active 마커) |
| `log` | 세션로그 기록 (날짜·frontmatter 자동) |
| `context` | 전원 최근 세션로그·상태를 JSON으로 수집 (요약은 스킬 몫) |
| `pull` / `commit` / `update` | git 동기화·커밋·upstream 갱신 |

모든 동사는 팀 루트를 `--root`로 명시받는다(환경변수 무신뢰 — 안전).

## 제거

```bash
python infra/install.py --root . --uninstall
```

---

teammode가 **무엇인지·왜 쓰는지**는 [README.md](README.md), 동작 명세는 [docs/spec/](docs/spec/README.md) 참조.
