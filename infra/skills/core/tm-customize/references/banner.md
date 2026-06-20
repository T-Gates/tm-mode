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

## 방법 선택 — 배너 커스텀을 시작할 때 반드시 두 갈래를 제시하라

**⚠️ 에이전트 필수 행동**: 배너 커스텀 요청이 오면, 프리셋만 보여주고 끝내지 말고 **다음 두 갈래를 명시적으로 안내**한다:

> **(A) 프리셋 6종** — "TEAMMODE" 글자의 기성 ASCII 아트 중 폰트를 고르는 방법  
> **(B) 팀명 ASCII** — 예: "ACME" 같은 실제 팀명으로 커스텀 아트를 직접 생성하는 방법

둘 다 가능하다는 걸 사용자에게 **먼저** 알리고, 어느 쪽으로 갈지 물어보거나 양쪽 샘플을 보여준 뒤 고르게 한다.

---

## 방법 1 — 프리셋 6종에서 고르기

`infra/banners/`에 6종이 있다. 각각 성격이 다르다:

| 폰트 | 성격 | 느낌 |
|---|---|---|
| `ansi_shadow` | 굵은 블록 + 그림자 | 묵직·임팩트 (현재 기본값) |
| `slant` | 이탤릭 슬래시 | 날렵·속도감 |
| `chunky` | 박스/도트형 | 레트로·아케이드 |
| `cyberlarge` | 가늘고 넓음 | 미니멀·테크 |
| `larry3d` | 입체 3D | 화려·올드스쿨 |
| `speed` | 밑줄 이탤릭 | 빠름·심플 |

**추천**: 팀 이름이 짧고 임팩트를 원한다면 `ansi_shadow`(기본값), 날렵한 느낌을 원한다면 `slant`를 먼저 보여주길 권장한다. 최종 선택은 사용자가.

**절차** — 추측으로 고르지 말고 **실물을 보여주고 사용자가 고르게** 한다:

```bash
# 1) 후보를 직접 보여준다 (한 번에 한둘씩, 또는 전부)
cat infra/banners/slant.txt
# 2) 사용자가 고른 폰트를 적용
cp infra/banners/<폰트명>.txt memory/banner.txt
# 3) 적용 확인
cat memory/banner.txt
```

**⚠️ 출력 규칙**: ASCII 아트는 공백·정렬이 의미를 가진다. 후보를 사용자에게 보여줄 때는 **반드시 코드블록(``` 펜스)으로 감싸라**. 감싸지 않으면 마크다운 렌더러가 정렬을 깨뜨리거나 내용을 축약한다. 여러 후보를 한 번에 보여줄 땐 각 후보를 **각각 별도 코드블록**으로 감싼다.

> 참고: 프리셋은 전부 "TEAM"/"TEAMMODE" 글자다. 팀명으로 바꾸고 싶으면 방법 2.

## 방법 2 — 팀명으로 커스텀 ASCII 생성

`figlet` 또는 `pyfiglet`(파이썬 패키지)으로 팀명 그대로 아트를 만든다. **figlet이 없어도 pyfiglet으로 생성 가능**하므로 아래 순서대로 시도한다.

### figlet (시스템 명령어)

```bash
command -v figlet || echo "figlet 없음"                    # 먼저 가용 확인
figlet -f slant "ACME" > /tmp/b.txt && cat /tmp/b.txt    # 미리보기
cp /tmp/b.txt memory/banner.txt                            # 마음에 들면 적용
```

- figlet 폰트: `standard`·`big`·`banner`·`block`·`slant` 등 다수(`figlist`로 목록). `figlet -f <폰트> "팀명"`으로 미리보고 적용.

### pyfiglet (파이썬 패키지 — figlet 없을 때 우선 시도)

figlet이 없으면 먼저 pyfiglet을 확인한다. 시스템 python3에 이미 설치돼 있는 경우가 많다(별도 설치 불필요):

```bash
python3 -c "import pyfiglet; print(pyfiglet.__version__)"                          # 가용 확인
python3 -c "import pyfiglet; print(pyfiglet.figlet_format('ACME', font='slant'))"  # 미리보기
```

pyfiglet로 생성해서 적용:

```bash
python3 -c "import pyfiglet; print(pyfiglet.figlet_format('ACME', font='ansi_shadow'))" > /tmp/b.txt
cat /tmp/b.txt          # 확인 후
cp /tmp/b.txt memory/banner.txt   # 적용
```

pyfiglet 폰트명은 프리셋과 동일하게 매칭된다(`ansi_shadow`·`slant`·`chunky`·`cyberlarge`·`larry3d`·`speed` 등).

**추천**: 팀명 ASCII는 `slant`가 대부분의 팀명 길이에 잘 맞는다. 팀명이 4글자 이하면 `ansi_shadow`로 묵직하게 가도 좋다.

### figlet·pyfiglet 모두 없을 때

① 방법 1의 프리셋을 쓰거나 ② 설치를 안내한다(`sudo apt install figlet` — 데비안/라즈비안, 또는 `pip install pyfiglet`). 설치를 임의로 강행하지 말고 사용자에게 물어본다.

> **출력 규칙 동일**: 생성한 ASCII 미리보기를 사용자에게 보여줄 때도 **코드블록으로 감싸라** (방법 1과 동일 원칙).

## 흔한 실수

| 실수 | 올바른 방법 |
|---|---|
| Edit/Write 도구로 `memory/banner.txt` 작성 | Bash `cp`/`tee`만 (가드 차단됨) |
| 후보를 안 보여주고 임의로 골라 적용 | `cat`으로 실물 보여주고 사용자가 선택 |
| ASCII 아트를 코드블록 없이 그냥 붙여넣기 | 반드시 ``` 펜스로 감싼다 (정렬 보존) |
| 프리셋만 보여주고 팀명 ASCII 선택지 안 알림 | 방법 1·2 두 갈래를 먼저 제시한다 |
| figlet 없는데 있다고 가정 | `command -v figlet` 확인 → 없으면 pyfiglet 시도 |
| pyfiglet도 없는데 그냥 실패 처리 | `python3 -c "import pyfiglet"` 확인 → 없으면 설치 안내 |
| `infra/banners/` 원본을 수정 | 원본은 그대로, `memory/banner.txt`만 만든다 |
