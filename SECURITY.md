# 보안 정책 (Security Policy)

> **EN:** Report vulnerabilities privately via GitHub Security Advisories (not public issues). Latest `main` is supported. Tokens live only in a local 0600 vault file — never in config, never committed, never synced.

## 지원 버전

| 버전 | 지원 |
| --- | --- |
| 최신 `main` | O |
| 그 외 | X |

tm-mode는 최신 `main` 기준으로만 보안 수정을 제공합니다. `tm-mode update`로 최신 상태를 유지하세요.

## 취약점 신고

**공개 이슈로 올리지 마세요.** 취약점은 [GitHub Security Advisories](https://github.com/T-Gates/tm-mode/security/advisories/new)로 **비공개** 신고해주세요. 재현 절차·영향 범위·가능하면 수정 제안을 포함하면 처리가 빨라집니다. 접수 후 확인 답변을 드리고, 수정·공개 일정을 조율합니다.

토큰이 화면·로그에 노출되는 사례는 특히 심각하게 다룹니다 — 마스킹 철칙 위반이므로 바로 신고해주세요.

## 토큰 보안 모델

tm-mode의 L2 서비스 연동(issues/chat/docs/calendar) 토큰은 다음 원칙으로 다룹니다.

- **로컬 금고에만 저장** — 토큰은 각 멤버의 로컬 디스크 `$XDG_DATA_HOME/teammode/credentials/default.json` (기본 `~/.local/share/teammode/credentials`) 한 곳에만 저장됩니다. 파일 권한은 **0600**(소유자만 읽기/쓰기), 디렉토리는 0700.
- **전송 채널 없음** — 이 금고 모듈(`infra/credentials.py`)은 store/load/delete 모두 로컬 디스크에만 작용하며 토큰을 네트워크로 보내는 경로가 없습니다. 팀 scope 토큰도 각 멤버가 직접 1회 입력합니다 (자동 공유 메커니즘 없음).
- **git 추적 금지** — 금고 파일은 커밋되지 않으며(.gitignore `*credentials*` 패턴), 클라우드 동기화 폴더에 두지 말라고 경고합니다(tm-connect 스킬).
- **config에 비밀 금지** — `team.config.json`에는 토큰/비밀을 넣을 수 없습니다. 넣으면 **린트가 거부**합니다. config에는 provider 이름·채널 ID 같은 비밀 아닌 리소스 식별자만 들어갑니다.
- **마스킹 철칙** — 토큰 평문은 stdout/로그/예외 메시지에 절대 새지 않습니다(키 이름만 노출). 누출 0은 테스트(`tests/test_credentials_l2e.py`)로 강제됩니다.
- **알려진 한계 (v0.1)** — 금고는 **평문 JSON**입니다 (OS 키체인 연동은 v0.2 예정). 따라서 0600 권한 + git 미추적 + 동기화 폴더 금지가 현재의 방어선입니다. 로컬 디스크 접근 권한을 가진 공격자에 대한 암호화 방어는 아직 없습니다.

## 일반 원칙

- 런타임 의존성 0 (stdlib-only) — 서드파티 공급망 표면을 최소화합니다.
- 팀 메모리(`memory/`)는 팀 git 레포에 그대로 커밋됩니다 — 비밀·자격증명을 메모리에 적지 마세요.
