"""고민방 대리 답변 v2 드라이런(발신 0): ontology/room_pairs.json의 최근 N개 원글에 대해 실제 ask_proxy 경로
(방 사람 답글 예시·이미 달린 답글·코드 관문 포함)로 답을 만들고 길이·금지 표현 통계를 낸다.
실행: py -3.12 -I tests\\dry_room_v2.py [--n 16]
"""
import json
import logging
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")
import bot  # noqa: E402
import style_profile as SP  # noqa: E402

for h in list(logging.getLogger().handlers):
    if isinstance(h, logging.FileHandler):
        logging.getLogger().removeHandler(h)
logging.getLogger().setLevel(logging.WARNING)


def _blocked(*a, **k):
    raise RuntimeError("드라이런 중 전송 시도 — 차단")


bot.send_text = _blocked
_orig_iris = bot.iris
bot.iris = lambda path, body=None, timeout=15: _blocked() if path.startswith("/reply") else _orig_iris(path, body, timeout)
bot.S.bot_id = str(_orig_iris("/config").get("bot_id"))
ROOM = (bot.CFG.get("open_chat_proxy_rooms") or [""])[0]   # 대리 답변(고민방) chat_id
bot.CFG["open_chat_proxy_rooms"] = [ROOM]
bot.CFG["proxy_skip_if_replies"] = 99   # 평가용: 이미 답글이 있어도 답을 만들어 본다(실제 운영은 3개면 넘김)

N = int(sys.argv[sys.argv.index("--n") + 1]) if "--n" in sys.argv else 16
pairs = json.loads((ROOT / "ontology" / "room_pairs.json").read_text(encoding="utf-8"))[-N:]
BAD = re.compile(r"하셨겠어요|힘드셨겠|마음이 느껴|충분히 이해|어떤 마음|언제부터|1388|병원|소아과|상담센터|푹 쉬세요|침착하세요|감사합니다|참고해|도움이 됐")  # ㅋㅋ·ㅠ는 글쓴이가 쓰면 허용
out, lens, bad = [], [], 0
for i, pr in enumerate(pairs, 1):
    bot._PROXY_SENT.clear()
    r = bot.ask_proxy("고민 상담방", ROOM, "글쓴이", pr["post"], [], post_id=pr["post_id"])
    rep = r or "(PASS/관문)"
    if r:
        lens.append(len(r))
        bad += bool(BAD.search(r))
    out.append({"i": i, "post": pr["post"][:200], "human": pr["replies"][:2], "reply": rep})
    print(f"[{i}] {pr['post'][:70].replace(chr(10), ' ')}\n   사람: {pr['replies'][0][:60]}\n   봇:   {rep[:90]}\n")
print(f"답 {len(lens)}/{len(pairs)}, 길이 중앙값 {statistics.median(lens) if lens else 0}, 최대 {max(lens) if lens else 0}, 금지 표현 포함 {bad}")
sc = [SP.mirror_score(o["post"], o["reply"]) for o in out if not o["reply"].startswith("(")]
if sc:
    print(f"글쓴이 기준: 존댓말/반말 일치 {sum(x['register_ok'] for x in sc)}/{len(sc)}, "
          f"길이 비율 중앙값 {statistics.median(x['len_ratio'] for x in sc)}, 표시 일치율 {round(sum(x['feat_agree'] for x in sc) / len(sc), 2)}, "
          f"글쓴이가 안 쓴 표시를 쓴 답 {sum(bool(x['extra_marks']) for x in sc)}")
    for o, x in zip([o for o in out if not o["reply"].startswith("(")], sc):
        o["mirror"] = x
(ROOT / "ontology" / "room_v2_dry.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
