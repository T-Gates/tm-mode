"""tm-mode i18n — stdlib 경량 메시지 카탈로그(dict). gettext 미사용(.mo 배치 부담).

Phase 1(2026-07): 골격 + 구조(비위저드) 메시지. 위저드 프롬프트는 배포 단계에서 확장.
fallback = en_US(1b 결정). 새 메시지는 두 언어 모두 채운다.
"""

_DEFAULT = "en_US"

MESSAGES = {
    "en_US": {
        "done_installed":
            "[done] Install complete. Run `tm on` (or /tm) to turn team mode on.",
        "verify_ok":
            "[verify] Install verified OK — members={n} (team mode is off).",
    },
    "ko_KR": {
        "done_installed":
            "[done] 설치 완료. 팀모드를 켜려면 `tm on`(또는 /tm) 하세요.",
        "verify_ok":
            "[verify] 설치 검증 OK — members={n} (팀모드는 꺼둠).",
    },
}


def resolve_lang(locale=None) -> str:
    """로캘 문자열(예 'ko_KR','en_US.UTF-8','ko')을 카탈로그 키로. 미지원 시 en_US."""
    if locale:
        base = str(locale).split(".")[0].split("@")[0].strip()
        if base in MESSAGES:
            return base
        lang = base.split("_")[0].lower()
        for key in MESSAGES:
            if key.split("_")[0].lower() == lang:
                return key
    return _DEFAULT


def t(key, lang=None, **fmt) -> str:
    """키→현지화 문자열. lang 미지원/키 없음이면 en_US→키 원문 폴백. **fmt 로 포맷."""
    catalog = MESSAGES.get(resolve_lang(lang), MESSAGES[_DEFAULT])
    template = catalog.get(key, MESSAGES[_DEFAULT].get(key, key))
    try:
        return template.format(**fmt) if fmt else template
    except (KeyError, IndexError):
        return template
