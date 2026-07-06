---
version: 2
affects: [hook, install, settings]
summary: 본인 세션로그 자유편집 (가드 예외 + TEAMMODE_MEMBER env 단일소스)
---

본인 세션로그(`memory/team/sessions/<본인>/`)를 `kb-write-guard` 예외로 두어
toolkit 식 **자유 편집**(append뿐 아니라 수정·재구성·요약 갱신)을 복원한다.
본인 판정은 settings.json `env` 의 `TEAMMODE_MEMBER` 단일 소스.

코드(`kb-write-guard`·`session-log-remind`·`install`)는 `tm-mode update`(pull)로
반영된다. 마이그레이션이 **추가로 하는 일은 기존 멤버의 settings.json 에
`TEAMMODE_MEMBER` 를 박는 것** 뿐이다.

## 왜 settings.json env 인가 (Claude)

셸 프로파일 env 주입(`TEAMMODE_HOME`, §9)은 PreToolUse 훅 환경에 닿지 않는다.
Claude Code 가 훅·도구 환경에 주입하는 건 settings.json 의 `env` 이므로,
가드훅이 `TEAMMODE_MEMBER` 를 읽으려면 이 경로라야 한다.

## Codex 는 어떻게 (config.toml command prefix)

Codex 는 hook 환경변수 주입 경로가 다르다. Codex 의 command hook 에는 `env` 필드가
없고(공식 hooks 문서), 셸 프로파일 env 도 이미 떠 있는 Codex 프로세스나
non-interactive 실행에는 닿지 않는다. 대신 Codex 는 hook `command` 를 **셸로 실행**하므로
(문서가 `command` 에 `$(...)` 명령치환 예시를 보이는 것이 근거), `~/.codex/config.toml`
의 teammode hook 블록 command 앞에 `env TEAMMODE_MEMBER=<본인> ` prefix 를 박아 전달한다.
이 prefix 는 `install`(Codex wired) 시 Codex 어댑터가 자동 렌더한다 — 별도 settings 파일은
쓰지 않는다.

> ⚠️ **Windows known-limitation**: `env VAR=val cmd` prefix 는 POSIX 셸 전제다.
> Windows 네이티브 셸(cmd/powershell)에서는 동작하지 않는다 — 현 Codex 어댑터는 단일
> `command` 만 렌더하고 `command_windows` 를 내지 않으므로(기존 bash 가정과 동일 한계),
> Windows Codex 멀티멤버 식별은 미지원이다. 필요 시 후속에서 `command_windows` 분기로 보강.

## 적용 (기존 멤버)

`TEAMMODE_MEMBER` 가 런타임에 닿지 않으면 가드 예외가 작동하지 않는다
(fail-closed — 세션로그 직접 편집이 막혀 `log` 동사/unlock 로만 쓰게 됨). 또한 멀티멤버
팀에서 `session-log-remind` 가 본인 세션로그를 특정하지 못해 약한 리마인더가 반복된다
(issue #26). 에이전트별로 박는다:

### Claude

1. **install 재실행**(권장, 멱등):
   `python infra/install.py --root . --member-name <본인이름> --yes`
   → 기존 설정 보존하고 `env` 에 `TEAMMODE_MEMBER` 만 추가.
2. **수동**: `~/.claude/settings.json` 의 `"env"` 에
   `"TEAMMODE_MEMBER": "<본인이름>"` 추가.

### Codex

1. **install 재실행**(권장, 멱등):
   `python infra/install.py --root . --member-name <본인이름> --yes`
   → `~/.codex/config.toml` 의 teammode hook command 에
   `env TEAMMODE_MEMBER=<본인> ` prefix 를 자동 갱신.
2. **수동**: `~/.codex/config.toml` 의 `# teammode-hooks-start`~`-end` 블록 안 각
   `command = '...'` 앞에 `env TEAMMODE_MEMBER=<본인이름> ` 를 직접 추가.
   (셸 프로파일 `.zshrc` 등에 export 하는 방식은 이미 떠 있는 Codex 프로세스·non-interactive
   실행에 닿지 않아 부족하다 — config.toml prefix 가 단일 소스.)

## 검증

settings.json env 확인 후, 본인 세션로그를 `Edit`/`Write` 도구로 직접 수정 →
가드에 막히지 않으면 성공. 남의 세션로그·메모리(`decisions/`·`INDEX.md`)은
여전히 차단되어야 한다(`TEAMMODE_MEMBER` 와 다른 폴더).

## 이름 등록 강화 (install)

이번 버전부터 `install` 은 멤버 이름을 받을 때:
- **UNIQUE**: 같은 이름·다른 식별자 → 거부(기존 M4)
- **유사성 가드**(신규): 기존 이름과 혼동될 만큼 비슷하면 거부
  (`jonathan`↔`jonathon` 같은 AI 혼동 — 편집거리/프리픽스 휴리스틱).
  더 구별되는 슬러그를 쓰거나, 정말 의도했다면 `members.md` 에 수동 등재.
