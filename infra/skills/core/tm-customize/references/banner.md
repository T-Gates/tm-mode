# 배너 커스텀

## 배너가 뭔가

`tm on`(팀모드 켜기) 할 때 맨 위에 뜨는 ASCII 아트. 팀의 첫인상 — 세션을 열 때마다 보이는 팀 간판이다.

- **소스**: `memory/banner.txt`. 이 파일이 있으면 엔진이 그대로 출력한다. `install.py`로 셋업한 팀엔 기본값(`ansi_shadow`, "TEAMMODE")이 이미 깔려 있다(install이 fresh 팀에도 banner.txt를 깐다). 순수 `teammode.py on`만 돈 환경이면 단순 텍스트로 폴백한다.
- **부수효과**: 배너 내용을 기본과 **다르게** 바꾸면 `personality_customized`가 `true`가 되어 `tm on` 배너 끝의 "💡 팀색 입히기: tm-customize" 권유가 사라진다(이미 팀색을 입혔다는 신호). 이 플래그는 `team.config.json`에 저장되는 키가 **아니라** 엔진이 런타임에 계산하는 값이다 — `tm context --json` 출력의 필드로만 존재하고, 기본 배너·greeting·farewell과 비교해 하나라도 다르면 `true`. ('존재'가 아니라 '내용 비교'라 기본 배너 그대로면 false.)

## ⚠️ 가드 (반드시)

`banner.txt`는 `memory/` 하위 → `kb-write-guard` 훅이 **Edit/Write 도구를 차단**한다. 반드시 **Bash**(`cp`·`tee` 등)로만 쓴다.

```bash
# ✅ 맞음
cp infra/banners/slant.txt memory/banner.txt
# ❌ 막힘 — Edit/Write 도구로 memory/banner.txt 작성 시 가드가 거부
```

## 방법 1 — 프리셋 6종에서 고르기 (기본)

`infra/banners/`에 6종이 있다. 각각 성격이 다르다:

| 폰트 | 성격 | 느낌 |
|---|---|---|
| `ansi_shadow` | 굵은 블록 + 그림자 | 묵직·임팩트 (현재 기본값) |
| `slant` | 이탤릭 슬래시 | 날렵·속도감 |
| `chunky` | 박스/도트형 | 레트로·아케이드 |
| `cyberlarge` | 가늘고 넓음 | 미니멀·테크 |
| `larry3d` | 입체 3D | 화려·올드스쿨 |
| `speed` | 밑줄 이탤릭 | 빠름·심플 |

**절차** — 추측으로 고르지 말고 **실물을 보여주고 사용자가 고르게** 한다:

```bash
# 1) 후보를 직접 보여준다 (한 번에 한둘씩, 또는 전부)
cat infra/banners/slant.txt
# 2) 사용자가 고른 폰트를 적용
cp infra/banners/<폰트명>.txt memory/banner.txt
# 3) 적용 확인
cat memory/banner.txt
```

> 참고: 프리셋은 전부 "TEAM"/"TEAMMODE" 글자다. 팀명으로 바꾸고 싶으면 방법 2.

## 방법 2 — 팀명으로 커스텀 ASCII 생성 (옵션)

`figlet`이 있으면 팀명 그대로 아트를 만든다:

```bash
command -v figlet || echo "figlet 없음"                    # 먼저 가용 확인
figlet -f slant "TGATES" > /tmp/b.txt && cat /tmp/b.txt    # 미리보기
cp /tmp/b.txt memory/banner.txt                            # 마음에 들면 적용
```

- **figlet이 없으면**: ① 방법 1의 프리셋을 쓰거나 ② 설치를 안내한다(`sudo apt install figlet` — 데비안/라즈비안). 설치를 임의로 강행하지 말고 사용자에게 물어본다.
- figlet 폰트는 `standard`·`big`·`banner`·`block`·`slant` 등 다수(`figlist`로 목록). `figlet -f <폰트> "팀명"`으로 미리보고 적용.

## 흔한 실수

| 실수 | 올바른 방법 |
|---|---|
| Edit/Write 도구로 `memory/banner.txt` 작성 | Bash `cp`/`tee`만 (가드 차단됨) |
| 후보를 안 보여주고 임의로 골라 적용 | `cat`으로 실물 보여주고 사용자가 선택 |
| figlet 없는데 있다고 가정 | 먼저 `command -v figlet`로 확인 |
| `infra/banners/` 원본을 수정 | 원본은 그대로, `memory/banner.txt`만 만든다 |
