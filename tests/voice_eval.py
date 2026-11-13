"""말투 블라인드 평가: OWNER이 과거(봇 이전)에 실제로 답한 갠톡 메시지에 대해
예전 방식(voice off)·새 방식(voice on)으로 답을 만들고, 심사관이 [실제 OWNER 답 / 예전 / 새]를 섞어 보고
자연스러움(1~5)과 '어느 게 실제 사람인지'를 고른다. 정답 답장은 예시(pairs)에서 빼서 베끼지 못하게 한다.
전송·저장 차단(실제 카톡 발신 0). 결과: ontology/voice_eval.md
실행: py -3.12 -I tests\\voice_eval.py [--n 14]
"""
import copy
import datetime as dt
import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")
import bot  # noqa: E402
import style_profile as SP  # noqa: E402

for h in list(logging.getLogger().handlers):
    if isinstance(h, logging.FileHandler):
        logging.getLogger().removeHandler(h)


def _blocked(*a, **k):
    raise RuntimeError("평가 중 전송 시도 — 차단")


bot.send_text = _blocked
_orig_iris = bot.iris
bot.iris = lambda path, body=None, timeout=15: _blocked() if path.startswith("/reply") else _orig_iris(path, body, timeout)
bot.S.save = lambda: None
bot.ONT.audit = lambda *a, **k: None
bot.ONT._write = lambda *a, **k: None
bot.todo_add = lambda *a, **k: None
ME = str(_orig_iris("/config").get("bot_id"))
bot.S.bot_id = ME
N = int(sys.argv[sys.argv.index("--n") + 1]) if "--n" in sys.argv else 14

dm = bot.DM._load()
cats = dm["categories"]
KST = bot.KST
cases = []
for cid, r in dm["rooms"].items():
    cat = r.get("category", "미분류")
    if cats.get(cat, {}).get("mode") == "off":
        continue
    rows = bot.db("SELECT _id, user_id, type, message, v, created_at FROM chat_logs WHERE chat_id = ? ORDER BY _id DESC LIMIT 600", [cid])
    rows = [x for x in reversed(rows) if str(x.get("type")) in ("1", "26") and (x.get("message") or "").strip()]
    found = []
    for i in range(1, len(rows)):
        a, b = rows[i - 1], rows[i]
        if str(a["user_id"]) != ME and str(b["user_id"]) == ME and int(b["created_at"]) < SP.BOT_SEND_START \
                and int(b["created_at"]) - int(a["created_at"]) < 1800 and SP._usable(b["message"], bot.ONT) \
                and SP._usable(a["message"], bot.ONT) and len(a["message"].strip()) >= 4:
            ctx = []
            for x in rows[max(0, i - 13):i - 1]:
                t = dt.datetime.fromtimestamp(int(x["created_at"]), KST).strftime("%m/%d %H:%M")
                ctx.append(f"[{t}] {'나' if str(x['user_id']) == ME else r['name']}: {x['message'].strip()[:300]}")
            found.append({"cid": cid, "name": r["name"], "cat": cat, "ctx": ctx, "q": a["message"].strip(), "human": b["message"].strip()})
    cases += found[-1:]
cases = cases[:N]
print(f"사례 {len(cases)}개")

prof0 = SP.load()
for c in cases:
    pol = cats.get(c["cat"], {})
    bot.fetch_messages = lambda chat_id, n, since_ts=None, _c=c["ctx"]: _c[-n:]
    # 정답 답장은 예시에서 뺀다
    prof = copy.deepcopy(prof0)
    prof["pairs"][c["cid"]] = [p for p in prof.get("pairs", {}).get(c["cid"], []) if p[1] != bot.ONT.mask(c["human"])[:120]]
    for reg in prof["registers"].values():
        reg["examples"] = [e for e in reg["examples"] if e != bot.ONT.mask(c["human"])]
    if c["cid"] in prof.get("acks", {}):
        prof["acks"][c["cid"]] = [a for a in prof["acks"][c["cid"]] if a != c["human"]]
    SP.load = lambda _p=prof: _p
    for mode in ("old", "new"):
        bot.CFG["voice_mode"] = mode == "new"
        res = bot.dm_decide(c["name"], c["cid"], c["name"], c["q"], [], extra=bot.DM.rule(pol), use_context=True,
                            category=c["cat"], mode="on") or ("error", "", "", "")
        c[mode] = res[1] if res[0] == "reply" else f"({res[0]}: {res[3][:30]})"
    print(f"[{c['name']}·{c['cat']}] {c['q'][:40]}\n  실제: {c['human']}\n  예전: {c['old']}\n  새로: {c['new']}\n")
bot.CFG["voice_mode"] = True

JUDGE = """너는 한국 대학생의 카카오톡 1:1 대화를 많이 본 심사관이다. 각 사례에서 A·B·C 세 답 가운데 하나는 실제 사람(계정 주인)이 쓴 것이고 나머지는 AI가 대신 쓴 것이다.
각 답의 자연스러움(1~5, 5=이 사람이 이 상대에게 실제로 보냈을 법함)을 매기고, 실제 사람이 쓴 것 하나를 고른다. '(hold:'로 시작하는 건 답을 안 보낸 경우라 점수 0.
JSON만: {"cases":[{"i":번호,"scores":{"A":n,"B":n,"C":n},"human_guess":"A|B|C","why":"한 줄"}],"overall":"세 문장 이내"}"""
order = ["ABC", "BCA", "CAB"]
payload, keymap = [], {}
for i, c in enumerate(cases, 1):
    o = order[i % 3]
    slots = dict(zip(o, ["human", "old", "new"]))
    keymap[i] = slots
    payload.append(f"[{i}] 앞 대화:\n" + "\n".join(c["ctx"][-6:]) + f"\n상대({c['cat']}): {c['q']}\n" +
                   "\n".join(f"{k}: {c[v]}" for k, v in sorted(slots.items())))
def judge_once(txt):
    o = bot.run_claude([{"type": "text", "text": txt}], JUDGE, False, timeout=400) or "{}"
    try:
        return json.loads(o[o.find("{"): o.rfind("}") + 1])
    except (ValueError, json.JSONDecodeError):
        return {"cases": [], "overall": o[:300]}


# 심사 두 번(두 번째는 사례 순서를 뒤집어) 평균 — 한 번짜리 잡음 줄이기
j1 = judge_once("\n\n".join(payload))
j2 = judge_once("\n\n".join(reversed(payload)))
merged = {}
for jj in (j1, j2):
    for vv in jj.get("cases", []):
        m = merged.setdefault(vv.get("i"), {"i": vv.get("i"), "scores": {}, "guesses": []})
        for kk, sc in (vv.get("scores") or {}).items():
            m["scores"].setdefault(kk, []).append(sc)
        m["guesses"].append(vv.get("human_guess", ""))
j = {"cases": [], "overall": str(j1.get("overall", "")) + " / " + str(j2.get("overall", ""))}
for i, m in merged.items():
    j["cases"].append({"i": i, "scores": {kk: sum(x) / len(x) for kk, x in m["scores"].items()},
                       "human_guess": m["guesses"][0], "guess2": m["guesses"][-1]})

agg = {"human": [], "old": [], "new": []}
fooled = {"old": 0, "new": 0}
rows_md = []
for v in j.get("cases", []):
    i = v.get("i")
    slots = keymap.get(i)
    if not slots:
        continue
    sc = {slots[k]: v.get("scores", {}).get(k, 0) for k in slots}
    for who, s in sc.items():
        agg[who].append(s)
    g = slots.get(v.get("human_guess", ""), "")
    for gg in {v.get("human_guess", ""), v.get("guess2", "")} if v.get("human_guess") != v.get("guess2") else [v.get("human_guess", "")]:
        w = slots.get(gg, "")
        if w in fooled:
            fooled[w] += 0.5 if v.get("human_guess") != v.get("guess2") else 1
    c = cases[i - 1]
    rows_md.append(f"| {i} | {c['name']}({c['cat']}) | {c['q'][:30]} | {c['human'][:40]} | {sc.get('human')} | "
                   f"{c['old'][:50]} | {sc.get('old')} | {c['new'][:50]} | {sc.get('new')} | {g or '-'} |")
avg = {k: round(sum(x) / len(x), 2) if x else 0 for k, x in agg.items()}

# ── OWNER 실제 답과의 거리(코드로 잼): 목표가 'OWNER 말투'라 심사관 취향보다 이것이 주 지표 ──
import math
import re as _re


def is_banmal(s):
    s = s.strip()
    if _re.search(r"(요|니다|세요|까요|죠|습니다|에요|예요|네요)[\s?!.~ㅋㅎㅠㅜ♡]*$", s):
        return False
    return True


def dist(c, who):
    b, h = c[who], c["human"]
    if b.startswith("("):
        return None
    return {"len_log": abs(math.log((len(b) + 1) / (len(h) + 1))),
            "reg_ok": is_banmal(b) == is_banmal(h),
            "tells": len(SP.lint(b, c["cid"], SP.register_for(bot.ONT, c["cid"], c["cat"]))),
            "emoji": bool(SP.EMOJI_RE.search(b)) and not SP.EMOJI_RE.search(h)}


obj = {}
for who in ("old", "new"):
    ds = [d for d in (dist(c, who) for c in cases) if d]
    if ds:
        obj[who] = {"n": len(ds), "len_x": round(math.exp(sum(d["len_log"] for d in ds) / len(ds)), 2),
                    "reg": round(sum(d["reg_ok"] for d in ds) / len(ds) * 100),
                    "tells": round(sum(d["tells"] for d in ds) / len(ds), 2),
                    "emoji": sum(d["emoji"] for d in ds)}
    else:
        obj[who] = {"n": 0, "len_x": "-", "reg": "-", "tells": "-", "emoji": "-"}
(ROOT / "ontology" / "voice_eval_cases.json").write_text(json.dumps(cases, ensure_ascii=False, indent=1), encoding="utf-8")
answered = [i for i, c in enumerate(cases, 1) if not c["old"].startswith("(") and not c["new"].startswith("(")]
agg2 = {"human": [], "old": [], "new": []}
for vv in j.get("cases", []):
    if vv.get("i") in answered:
        for kk, who in keymap[vv["i"]].items():
            agg2[who].append(vv.get("scores", {}).get(kk, 0))
avg2 = {k: round(sum(x) / len(x), 2) if x else 0 for k, x in agg2.items()}
md = [f"# 말투 블라인드 평가 ({time.strftime('%Y-%m-%d %H:%M')}, 사례 {len(cases)}개, 실제 발신 0)", "",
      "**OWNER 실제 답과의 거리(주 지표, 답을 낸 건만)**", "",
      "| 지표 | 예전 봇 | 새 봇 |", "|---|---|---|",
      f"| 답을 낸 건수 | {obj['old']['n']} | {obj['new']['n']} |",
      f"| 길이 차이(배, 1.0=같음) | {obj['old']['len_x']} | {obj['new']['len_x']} |",
      f"| 반말/존댓말 일치(%) | {obj['old']['reg']} | {obj['new']['reg']} |",
      f"| 답 하나당 AI 티 표현 수 | {obj['old']['tells']} | {obj['new']['tells']} |",
      f"| OWNER이 안 쓴 이모지를 쓴 건수 | {obj['old']['emoji']} | {obj['new']['emoji']} |", "",
      "**심사관 판단(참고: 심사관은 길고 정중한 답을 더 자연스럽게 보는 경향이 있음)**", "",
      "| 지표 | 실제 OWNER | 예전 봇 | 새 봇 |", "|---|---|---|---|",
      f"| 자연스러움 평균(5점, 보류=0 포함) | {avg['human']} | {avg['old']} | {avg['new']} |",
      f"| 자연스러움 평균(둘 다 답한 {len(answered)}건) | {avg2['human']} | {avg2['old']} | {avg2['new']} |",
      f"| 심사관이 '실제 사람'으로 고른 횟수 | {len(agg['human']) - fooled['old'] - fooled['new']} | {fooled['old']} | {fooled['new']} |", "",
      "| # | 상대 | 받은 말 | 실제 답 | 점수 | 예전 봇 | 점수 | 새 봇 | 점수 | 사람으로 고른 것 |",
      "|---|---|---|---|---|---|---|---|---|---|", *rows_md, "", "**총평**: " + str(j.get("overall", ""))]
(ROOT / "ontology" / "voice_eval.md").write_text("\n".join(md) + "\n", encoding="utf-8")
print("\n".join(md[:8]))
print("총평:", j.get("overall", ""))
