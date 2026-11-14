# -*- coding: utf-8 -*-
"""높임 판별(register.py) 테스트. 지어낸 문장만 쓴다.

    py -3.12 -m pytest tests/test_register.py -q
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import register as REG  # noqa: E402


def test_sentence_and_text_register():
    assert REG.sentence_register("네 알겠습니다") == "합쇼체"
    assert REG.sentence_register("지금 가요 ㅎㅎ") == "해요체"
    assert REG.sentence_register("ㅇㅋ 갈게") == "반말"
    assert REG.sentence_register("넵!") == "해요체"
    assert REG.sentence_register("ㅋㅋㅋ") == ""                      # 꼬리만 있으면 모름(반말로 세지 않는다)
    assert REG.text_register("확인했습니다. 내일 뵙겠습니다") == "합쇼체"
    assert REG.text_register("알겠어요\n내일 봐") == "섞임"
    assert REG.text_register("회의실 3층") == ""                      # 명사로 끝나면 모름


def test_register_mismatch():
    assert REG.register_mismatch("내일 봐", "합쇼체") == "합쇼체 상대에 반말"
    assert REG.register_mismatch("알겠어요 내일 봐", "해요체").endswith("반말")
    assert REG.register_mismatch("내일 뵙겠습니다", "반말") == "반말 상대에 존댓말"
    assert REG.register_mismatch("알겠어요", "합쇼체") == ""           # 한 단계 아래 존댓말은 허용
    assert REG.register_mismatch("회의실 3층", "합쇼체") == ""         # 모르면 어긋남으로 보지 않는다
    assert REG.register_mismatch("응", "해요체", allowed=["응!"]) == ""  # 허용한 짧은 말
    assert REG.register_mismatch("내일 봐", "모름") == ""


def test_register_for_uses_card_then_category():
    fact = {"subject": "c:11", "predicate": "honorific_mine", "status": "active", "value": "합쇼체"}
    old = dict(fact, subject="c:22", status="retired")
    store = types.SimpleNamespace(data={"facts": {"a": fact, "b": old}})
    assert REG.register_for(store, "11", "친구") == "합쇼체"            # 상대 카드에 적힌 값이 먼저
    assert REG.register_for(store, "22", "친구") == "반말"              # 꺼진 사실은 안 쓴다 → 범주 기본
    assert REG.register_for(store, "33", "교수") == "합쇼체"
    assert REG.register_for(store, "33", "처음 보는 범주") == "해요체"
