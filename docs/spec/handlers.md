# handlers/ 규약 (spec)

**확정 규약** — 이 문서는 팀 핸들러 코드의 계약·인터페이스·보안 규칙을 정의한다.
설계 메모·임시 문서가 아니라 코드·검증 인프라가 참조하는 단일 소스다.

## 1. 위치

```
<팀 레포 루트>/
└── handlers/
    ├── issues.py
    ├── chat.py
    ├── docs.py
    └── calendar.py
```

- `infra/` 와 **동일 레벨** — `SYNC_PATHS = ["infra/", "NOTICE.md"]`만 덮어쓰므로 `handlers/`는
  upstream `teammode update`에 의해 덮어쓰이지 않는다.
- 실제 서비스 연결 코드는 S7 도그푸딩 단계에서 팀이 작성한다. 그 전까지 디렉토리가
  없거나 비어 있어도 정상이다.

## 2. 인터페이스 계약

각 핸들러 파일은 아래 함수를 **반드시** 정의해야 한다 (`handlers_are_valid()` 검증).

| 역할 | 필수 함수 시그니처 |
|------|-------------------|
| `issues` | `issues_create(title, body?, assignee?, label?, priority?) -> dict` |
| `issues` | `issues_list(filter?) -> list` |
| `issues` | `issues_get(id) -> dict` |
| `issues` | `issues_update(id, **kwargs) -> dict` |
| `chat` | `chat_send(message, channel?) -> dict` |
| `chat` | `chat_list(channel?, limit?) -> list` |
| `docs` | `docs_read(id) -> dict` |
| `docs` | `docs_write(id, content) -> dict` |
| `docs` | `docs_list(query?) -> list` |
| `docs` | `docs_create(title, content?) -> dict` |
| `calendar` | `calendar_list(start, end?) -> list` |
| `calendar` | `calendar_create(title, start, end?, description?) -> dict` |

**반환 타입**: 역할 계약 스키마. 서비스별 응답을 핸들러 내부에서 정규화해 통일
인터페이스를 유지한다. 스킬·에이전트는 서비스 고유 응답 형식에 의존하지 않는다.

## 3. 보안 규칙

### 3.0 토큰 취득 계약 (S4)

핸들러는 자신이 필요한 토큰을 **직접** `credentials.load()` 로 취득한다.
`infra/mcp/role_server.py` 는 핸들러에 토큰을 주입하지 않는다 (핸들러 자율 원칙).

```python
# 올바른 방법 — 핸들러가 직접 credentials.load() 호출
token = credentials.load(TEAM, "personal", "issues")
```

`role_server.py` 에 있는 `get_token_for_role(team, scope, role, auth_type)` 함수는
핸들러가 호출할 수 있는 **헬퍼 유틸리티**다. 핸들러에서 직접 `credentials.load()` 를
쓰기 어려운 경우(예: infra/ 경로 설정이 복잡한 환경) 이 헬퍼를 임포트해 사용할 수 있다:

```python
# 대안: role_server 헬퍼 사용
import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1] / 'infra' / 'mcp'))
from role_server import get_token_for_role
token = get_token_for_role(team=TEAM, scope="personal", role="issues", auth_type="api_key")
```

| auth_type | 금고 키 |
|-----------|---------|
| `api_key` / `bot_token` | `<role>` (예: `"issues"`) |
| `oauth` | `<role>_access_token` (예: `"issues_access_token"`) |

### 3.1 토큰 리터럴 금지

핸들러 코드에 토큰·API 키를 직접값으로 embed하는 것을 **절대 금지**한다.

```python
# 금지 — handlers_are_valid() 와 secret lint 가 모두 잡는다
AUTH = "Bearer eyJhbGci..."
API_KEY = "sk-proj-abc123..."
SLACK = "xoxb-12345-..."

# 올바른 방법 — credentials.load() 경유
import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1] / 'infra'))
import credentials

TEAM = "default"  # team 인자는 단일 금고에서 무시됨(2026-06-21) — 임의 식별자 가능

def issues_create(title, body="", ...):
    token = credentials.load(TEAM, "personal", "issues")
    # ... 서비스 API 호출 ...
```

### 3.2 탐지 레이어

이중 방어로 토큰 embed를 차단한다:

1. **`handlers_are_valid()`** (`infra/install_lib.py`) — AST 기반 heuristic.
   파일을 실행하지 않고 문자열 리터럴 노드를 검사한다. 문법 오류·필수 함수 누락도 함께 검출.

2. **`lint_no_tracked_secrets()`** (`conformance/check.py`) — 라인 기반 패턴 탐지.
   `handlers/*.py`는 `_SECRET_TARGET_SKIP_SUFFIXES`의 `.py` 예외에도 불구하고
   **강제 스캔** 대상이다 (suffix skip 목록을 건드리지 않고 명시 수집으로 포함).

탐지 패턴:
- `Authorization: Bearer <값>`
- `sk-` 접두 문자열 (OpenAI / Anthropic)
- `xoxb-` / `xoxp-` / `xoxa-` 접두 (Slack)

Placeholder 값은 **전체 값이 아래 목록 중 하나와 정확히 일치**할 때만 허용한다. 부분 포함
은 허용하지 않는다 (`"Bearer your-token-here"` 처럼 prefix 가 붙으면 실제 토큰 형식으로
간주해 차단):

허용 placeholder 전체 목록: `your-token-here`, `changeme`, `placeholder`, `todo`, `tbd`,
`example`, `redacted`, `xxx`, `...`, `<...>`, `null`, `true`, `false`, `none`

## 4. 검증 API

### `handlers_are_valid(handlers_dir: Path) -> bool`

`infra/install_lib.py` 제공.

```python
from pathlib import Path
from install_lib import handlers_are_valid

ok = handlers_are_valid(Path("handlers"))
```

검증 항목:
- 디렉토리 부재 / 빈 디렉토리 → `True` (아직 미연결 = 정상)
- Python 문법 파싱 (`ast.parse()`, 실행 아님)
- 역할별 필수 함수 존재 대조
- 토큰 리터럴 heuristic (AST 문자열 노드 스캔)

실패 시 파일 생성을 하지 않아야 한다 (no-commit). 파일 쓰기가 필요하면
`infra/install_lib._atomic_write_text(path, content)`를 사용해 tmp→rename atomic write로
partial 파일을 방지한다.

## 5. atomic write

핸들러 파일 생성 시 partial 파일을 막기 위해 `_atomic_write_text(path, content)` 사용:

```python
from install_lib import _atomic_write_text
_atomic_write_text(Path("handlers/issues.py"), source_code)
```

- 같은 디렉토리에 `.tmp` 파일 생성 → flush/fsync → `os.replace()` 원자 커밋
- 실패 시 `.tmp` 정리, 원본 무손상
- 심링크 경유 시 실타깃에 replace (링크 보존)

## 6. 예외

- 핸들러 파일이 **없는** 역할은 invalid가 아니다 (빈 슬롯 = S7 이전 정상).
- 알 수 없는 역할명의 파일이 있어도 문법·토큰 검증만 적용하고 함수 목록은 강제하지 않는다.
- `.example` 확장자 파일은 secret lint 스캔에서 제외 (placeholder 관례).
