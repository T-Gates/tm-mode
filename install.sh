#!/usr/bin/env sh
# teammode — curl 진입 스킨 (pip 와 등가, 둘 다 cli.py 로 위임).
#
#   curl -fsSL https://raw.githubusercontent.com/T-Gates/teammode/main/install.sh | sh -s -- join <clone-url>
#
# python3·git 존재만 확인하고, stdlib 단일파일 cli.py 를 raw 로 받아 그대로 실행한다.
# 패키지 설치(pip) 단계를 건너뛰는 얇은 진입점 — 스킬·훅·엔진 번들 없음(cli.py 가 clone 후 위임).
#
# 테스트/미러: 소스 URL 은 TEAMMODE_CLI_URL 로 override(file://경로 또는 http(s)). 미지정 시 main raw.
set -e

CLI_URL="${TEAMMODE_CLI_URL:-https://raw.githubusercontent.com/T-Gates/teammode/main/src/teammode/cli.py}"

command -v python3 >/dev/null 2>&1 || { echo "[error] python3 가 필요합니다." >&2; exit 2; }
command -v git     >/dev/null 2>&1 || { echo "[error] git 이 필요합니다." >&2; exit 2; }

# ⚠️ 템플릿은 trailing X 여야 한다(BSD/macOS mktemp 는 X 뒤 suffix 를 치환 안 해 충돌).
# 확장자는 불필요 — python3 는 파일명 무관하게 실행한다.
TMP=$(mktemp "${TMPDIR:-/tmp}/teammode-cli.XXXXXX")
trap 'rm -f "$TMP"' EXIT

case "$CLI_URL" in
  file://*)
    cp "${CLI_URL#file://}" "$TMP" || { echo "[error] cli.py 복사 실패: $CLI_URL" >&2; exit 2; }
    ;;
  *)
    command -v curl >/dev/null 2>&1 || { echo "[error] curl 이 필요합니다." >&2; exit 2; }
    curl -fsSL "$CLI_URL" -o "$TMP" || { echo "[error] cli.py 다운로드 실패: $CLI_URL" >&2; exit 2; }
    ;;
esac

# 빈 다운로드 방어: HTTP 200 빈 바디/빈 파일이면 python3 가 조용히 exit 0 → "무동작 성공" 위장.
[ -s "$TMP" ] || { echo "[error] cli.py 를 받지 못했습니다(빈 응답: $CLI_URL)." >&2; exit 2; }

# ⚠️ exec 금지 — exec 는 셸을 교체해 EXIT trap(임시파일 정리)이 안 돈다(누수).
# 마지막 명령이라 cli.py 의 exit code 가 그대로 install.sh 의 종료코드로 전파된다.
python3 "$TMP" "$@"
