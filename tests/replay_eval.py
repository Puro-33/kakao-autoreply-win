"""재생 평가(ontology/DESIGN.md §10): 실제 갠톡에서 받은 최근 메시지를 예전 방식(off)·온톨로지 방식(on)으로
각각 판정만 해 보고 비교한다. 전송·상태 저장·온톨로지 쓰기는 모두 막는다(실제 카톡 발신 0).
결과: ontology/replay_report.md
실행: py -3.12 -I tests\\replay_eval.py [--n 30]
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
import ontology_card as OC  # noqa: E402

for h in list(logging.getLogger().handlers):          # bot.log에 섞지 않는다
    if isinstance(h, logging.FileHandler):
        logging.getLogger().removeHandler(h)


def _blocked(*a, **k):
    raise RuntimeError("재생 평가 중 전송 시도 — 차단")


bot.send_text = _blocked
_orig_iris = bot.iris
bot.iris = lambda path, body=None, timeout=15: _blocked() if path.startswith("/reply") else _orig_iris(path, body, timeout)
bot.S.save = lambda: None
bot.ONT.audit = lambda *a, **k: None
bot.ONT._write = lambda *a, **k: None
bot.todo_add = lambda *a, **k: None

N = int(sys.argv[sys.argv.index("--n") + 1]) if "--n" in sys.argv else 30
me = bot.iris("/config").get("bot_id")
dm = bot.DM._load()
cats = dm.get("categories", {})
since = int(time.time()) - 90 * 86400
samples = []
for cid, r in dm.get("rooms", {}).items():
    cat = r.get("category", "미분류")
    if cats.get(cat, {}).get("mode") == "off":
        continue
    rows = bot.db("SELECT _id, user_id, type, message, v, created_at FROM chat_logs WHERE chat_id = ? AND user_id != ? "
                  "AND created_at >= ? ORDER BY _id DESC LIMIT 2", [cid, str(me), since])
    for x in rows:
        if str(x.get("type")) in ("1", "26") and (x.get("message") or "").strip():
            samples.append((cid, r.get("name") or "상대", cat, x["message"].strip()[:400]))
samples = samples[:N]
print(f"표본 {len(samples)}건")


def leak(reply, cid):
    return OC.output_check(reply, bot.ONT, cid) if reply else ""


rows_out, agree, stat = [], 0, {"off": {}, "on": {}, "leak_off": 0, "leak_on": 0, "gate": 0, "llm_on": 0}
for i, (cid, name, cat, text) in enumerate(samples, 1):
    pol = cats.get(cat, {})
    extra = bot.DM.rule(pol)
    res = {}
    for mode in ("off", "on"):
        t0 = time.time()
        calls = [0]
        orig = bot.run_claude

        def counted(*a, **k):
            calls[0] += 1
            return orig(*a, **k)
        bot.run_claude = counted
        try:
            r = bot.dm_decide(f"{name}", cid, name, text, [], extra=extra, use_context=False, category=cat, mode=mode)
        finally:
            bot.run_claude = orig
        r = r or ("error", "", "", "판정 실패")
        res[mode] = (r, calls[0], time.time() - t0)
        stat[mode][r[0]] = stat[mode].get(r[0], 0) + 1
    (a0, rp0, _, rs0), _, _ = res["off"]
    (a1, rp1, _, rs1), c1, _ = res["on"]
    agree += a0 == a1
    l0, l1 = leak(rp0 if a0 == "reply" else "", cid), leak(rp1 if a1 == "reply" else "", cid)
    stat["leak_off"] += bool(l0)
    stat["leak_on"] += bool(l1)
    stat["gate"] += rs1.startswith("코드 관문")
    stat["llm_on"] += c1
    rows_out.append(f"| {i} | {name} ({cat}) | {bot.mask_sensitive(text)[:50]} | {a0}: {bot.mask_sensitive(rp0)[:60]}"
                    f"{' ⚠️' + l0 if l0 else ''} | {a1}: {bot.mask_sensitive(rp1)[:60]}{' ⚠️' + l1 if l1 else ''} | {rs1[:40]} |")
    print(f"{i}/{len(samples)} {name}: off={a0} on={a1} ({rs1[:30]})")

rep = [f"# 재생 평가 ({time.strftime('%Y-%m-%d')}, 표본 {len(samples)}건, 실제 발신 0)", "",
       "같은 메시지를 대화 맥락 없이 두 방식으로 판정만 했다. ⚠️는 '그대로 보냈다면' 출력 검사에 걸리는 답.", "",
       "| 지표 | 예전(off) | 온톨로지(on) |", "|---|---|---|",
       f"| 판정 분포 | {stat['off']} | {stat['on']} |",
       f"| 출력 검사에 걸릴 답(유출 위험) | {stat['leak_off']} | {stat['leak_on']} |",
       f"| 코드 관문 보류(LLM 호출 없음) | - | {stat['gate']} |",
       f"| LLM 호출 | {len(samples)} | {stat['llm_on']} |",
       f"| 두 방식 판정 일치 | {agree}/{len(samples)} | |", "",
       "| # | 상대 | 받은 말 | 예전 | 온톨로지 | 온톨로지 이유 |", "|---|---|---|---|---|---|", *rows_out]
(ROOT / "ontology" / "replay_report.md").write_text("\n".join(rep) + "\n", encoding="utf-8")
print("\n".join(rep[:12]))
