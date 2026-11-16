"""높임(반말·해요체·합쇼체) 판별과 불일치 검사. 글의 말끝만 보고 정한다(학습·통계·저장 파일 없음).

봇이 답을 보내기 전에 '이 상대에게 반말을 써도 되는가', '답의 높임이 정한 높임과 어긋나지 않는가'를 코드로 확인하는 데 쓴다.
카톡 발신 코드 없음.
"""
from __future__ import annotations

import re

CATEGORY_REGISTER = {"친구": "반말", "교수": "합쇼체", "가족": "반말"}


def register_for(store, chat_id: str, category: str) -> str:
    """이 상대에게 쓰는 기본 높임: 상대 카드에 적힌 값(honorific_mine)이 있으면 그것, 없으면 범주 기본, 그것도 없으면 해요체."""
    for f in store.data["facts"].values():
        if f["subject"] == f"c:{chat_id}" and f["predicate"] == "honorific_mine" and f["status"] == "active":
            return f["value"]
    return CATEGORY_REGISTER.get(category, "해요체")


_POLITE_END = re.compile(r"(요|죠|니다|니까|세요|까요|나요|데요|네요|어요|아요|해요|에요|예요|래요|용|욤|여|염)[\s?!.~ㅋㅎㅠㅜ;^]*$")


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?~ㅠㅜㅋ])\s+|\n+", (text or "").strip())
    return [p.strip() for p in parts if len(p.strip()) >= 2]


_REG_TAIL_RE = re.compile(r"[\s.,!?~ㅋㅎㅠㅜ;:^…><)(\-_*+=♡♥☆★'\"\U0001F300-\U0001FAFF☀-➿]+$")
_HASIPSHO_END_RE = re.compile(r"(?:니다|니까|십시오|습니다|습니까|ㅂ니다|십니까|시죠|십쇼)$")
_HAEYO_END_RE = re.compile(r"(?:요|용|욤|죠|죵|여|염|세용|에염)$")
_POLITE_WORD_RE = re.compile(r"^(?:네|넵|넹|넴|예|네네|넵넵|네엡|옙|앗\s?네|아\s?네|네\s?넵)$")
_BANMAL_WORD_RE = re.compile(r"^(?:응|웅|ㅇㅇ|ㅇㅋ|ㄱㄱ|ㄴㄴ|ㅇㅈ|ㄹㅇ|어|엉|그래|아니|아냐|오키|오케이?|ㅇㅋㅇㅋ)$")
_BANMAL_LAST = set("어아야지해자냐니게께래까다네든데걸임음함됨줘봐와거고듯나대군셈랑구여")


def sentence_register(s: str) -> str:
    """한 문장의 높임: '합쇼체' / '해요체' / '반말' / ""(말끝으로 모름)."""
    core = _REG_TAIL_RE.sub("", (s or "").strip())
    if not core:
        return ""
    if _POLITE_WORD_RE.match(core):
        return "해요체"
    if _BANMAL_WORD_RE.match(core):
        return "반말"
    if _HASIPSHO_END_RE.search(core):
        return "합쇼체"
    if _HAEYO_END_RE.search(core):
        return "해요체"
    if re.search(r"[ㄱ-ㅎ]{2,}$", core):
        return "반말" if re.search(r"(?:ㅇㅇ|ㅇㅋ|ㄱㄱ|ㄴㄴ|ㅇㅈ|ㄹㅇ)$", core) else ""
    return "반말" if core[-1] in _BANMAL_LAST else ""


def _reg_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?~ㅠㅜㅋㅎ…])\s+|\n+|(?<=[.!?])(?=[가-힣])", (text or "").strip())
    return [p.strip() for p in parts if p.strip()]


def text_register(text: str) -> str:
    """글 전체의 높임: '합쇼체' / '해요체' / '반말' / '섞임' / ""(모름). 존댓말 문장이 하나라도 합쇼체면 합쇼체."""
    regs = [x for x in (sentence_register(p) for p in _reg_sentences(text)) if x]
    if not regs:
        return ""
    pol = [x for x in regs if x != "반말"]
    if pol and len(pol) < len(regs):
        return "섞임"
    if not pol:
        return "반말"
    return "합쇼체" if "합쇼체" in pol else "해요체"


def register_mismatch(reply: str, target: str, allowed: list[str] | None = None) -> str:
    """답 높임이 정한 높임(target: 반말/해요체/합쇼체)과 어긋나면 이유, 맞거나 모르면 "".
    allowed: OWNER이 이 상대에게 실제로 쓰던 짧은 말(맞장구) — 그대로면 어긋남으로 보지 않는다.
    합쇼체 상대에게 해요체는 허용(한 단계 아래 존댓말), 해요체 상대에게 합쇼체도 허용."""
    r = (reply or "").strip()
    if not r or target not in ("반말", "해요체", "합쇼체"):
        return ""
    if allowed and _REG_TAIL_RE.sub("", r) in {_REG_TAIL_RE.sub("", a.strip()) for a in allowed if a}:
        return ""
    got = text_register(r)
    if not got:
        return ""
    if target == "반말" and got != "반말":
        return f"반말 상대에 {'존댓말 섞임' if got == '섞임' else '존댓말'}"
    if target != "반말" and got in ("반말", "섞임"):
        return f"{target} 상대에 {'반말 섞임' if got == '섞임' else '반말'}"
    return ""
