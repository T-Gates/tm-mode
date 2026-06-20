# 배너 커스텀

- 배너 = tm on 시 출력. 소스: `memory/banner.txt` (있으면 그대로 출력)
- 기본 폰트 6종: `infra/banners/{ansi_shadow,slant,chunky,cyberlarge,larry3d,speed}.txt`
- 적용: `cp infra/banners/<폰트명>.txt memory/banner.txt`
  ⚠️ `banner.txt`는 `memory/` 하위 → Edit/Write 금지(kb-write-guard). **Bash cp만**
- 바꾸면 `personality_customized` 자동 True
- 순서: 각 후보를 `cat infra/banners/<폰트명>.txt` 로 보여주고 → 사용자 선택 → cp 적용
