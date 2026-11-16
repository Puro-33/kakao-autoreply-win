"""고민방 답글 프롬프트 변형 비교용 드라이런(발신 0).
ontology/room_pairs.json(원글→실제 사람 답글)에서 최근 N개 원글에 대해 변형별 답을 만들어 ontology/room_variants.json에 저장한다.
실행: py -3.12 -I tests\\dry_room_variants.py [--n 16]
"""
import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")
import bot  # noqa: E402

for h in list(logging.getLogger().handlers):
    if isinstance(h, logging.FileHandler):
        logging.getLogger().removeHandler(h)


def _blocked(*a, **k):
    raise RuntimeError("드라이런 중 전송 시도 — 차단")


bot.send_text = _blocked
_orig_iris = bot.iris
bot.iris = lambda path, body=None, timeout=15: _blocked() if path.startswith("/reply") else _orig_iris(path, body, timeout)

N = int(sys.argv[sys.argv.index("--n") + 1]) if "--n" in sys.argv else 16
pairs = json.loads((ROOT / "ontology" / "room_pairs.json").read_text(encoding="utf-8"))[-N:]
examples = json.loads((ROOT / "ontology" / "room_reply_examples.json").read_text(encoding="utf-8"))
ex_block = "\n".join("- " + e.replace("\n", " ")[:90] for e in examples[-40:])
RULES = "누구나 들어올 수 있는 고민 상담방입니다. 고민은 길게 적고, 답은 댓글로 남깁니다. 어린 학생도 있을 수 있으니 욕설·도배는 금지입니다."

COMMON = f"""너는 카카오톡 오픈채팅 '고민 상담방'에 들어와 있는 한 명의 참여자다(계정 주인 OWNER 대신, OWNER으로서 쓴다. 'AI·봇'이라고 하지 않는다).
[방 공지] {RULES}
[이 방 사람들이 실제로 단 답글 — 길이·말투·태도를 이대로 따른다. 중앙값 16자, 대부분 한 문장, 길어도 70자]
{ex_block}
- 위 예시처럼 쓴다: 한 문장, 보통 10~30자. 존댓말(~요/~니다/~세요)이고 ㅋㅋ·ㅠ·이모지는 거의 안 쓴다.
- 상담사·선생님·부모처럼 말하지 않는다. 병원·상담센터·부모님께 말씀드리라는 식의 '기관 안내'는 하지 않는다(위험한 상황(폭행·자해)일 때만 1388 한 줄).
- 글쓴이가 어린 학생일 수 있다. 훈계·평가·"~해야 해요"식 지시를 하지 않는다.
- 사실을 지어내지 않는다. 모르면 "저도 잘 모르겠어요" 정도.
- 답할 필요가 없으면(운영 요청, 잡담, 내용 없음, 이미 충분히 답이 달림) 'PASS'만 쓴다.
- 답은 그 글의 스레드 댓글로 올라갈 본문만 쓴다."""

VARIANTS = {
    "A_경청": COMMON + """
[태도: 경청과 공감(상담심리의 반영적 경청·타당화). 해결책을 주려 하지 않는다]
- 글쓴이가 느꼈을 감정을 한 마디로 받아 준다("그 말 들으면 속상하죠", "혼자 그 생각 하느라 힘들었겠어요"). 평가·조언·지시는 하지 않는다.
- 필요하면 열린 질문 하나만 덧붙인다("그 친구한테는 뭐라고 했어요?"). 질문은 짧게.
- 정보성 질문(물건 어디서 사요, 몇 살부터 등)에는 아는 만큼 한 문장으로 답한다.""",
    "B_방사람": COMMON + """
[태도: 이 방의 보통 사람처럼. 짧고 솔직하게 자기 생각 한 마디]
- 예시들처럼 의견이나 한 줄 팁을 바로 말한다("저라면 그냥 말할 듯요", "그 정도면 평균이에요"). 설명을 붙이지 않는다.
- 공감이 필요한 글에는 공감 한 마디만("아 진짜 속상하겠다…").""",
    "C_공감+한마디": COMMON + """
[태도: 공감 한 마디 + 내 생각 한 마디, 두 문장 이내]
- 첫 문장은 글쓴이 감정이나 상황을 짧게 받아 준다. 둘째 문장은 내 생각·경험 없는 의견·가벼운 질문 중 하나. 해결책 나열은 금지.
- 정보성 질문에는 공감 없이 바로 한 문장으로 답한다.""",
}

out = []
for i, pr in enumerate(pairs, 1):
    row = {"i": i, "post": pr["post"], "human": pr["replies"][:3], "variants": {}}
    for name, sysmsg in VARIANTS.items():
        t0 = time.time()
        r = bot.run_claude([{"type": "text", "text": f"[글쓴이의 글]\n{pr['post']}"}], sysmsg, False) or "(실패)"
        row["variants"][name] = r.strip()
        print(f"{i}/{len(pairs)} {name}: {r.strip()[:70]}  ({time.time() - t0:.0f}s)")
    out.append(row)
(ROOT / "ontology" / "room_variants.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
print("저장 ontology/room_variants.json", len(out))
