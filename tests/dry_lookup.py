"""찾아보기 드라이런: 실제 Claude·실제 DB로 판정만(전송·저장 차단). py -3.12 -I tests\\dry_lookup.py"""
import json
import logging
import sys
import urllib.request
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
_orig = bot.iris
bot.iris = lambda path, body=None, timeout=15: _blocked() if path.startswith("/reply") else _orig(path, body, timeout)
bot.S.save = lambda: None
bot.ONT.audit = lambda *a, **k: None
bot.ONT._write = lambda *a, **k: None
bot.S.bot_id = str(bot.iris("/config").get("bot_id"))

dm = bot.DM._load()
cases = [(cid, txt) for cid, txt in json.loads(sys.argv[1])] if len(sys.argv) > 1 else []
for cid, text in cases:
    r = dm["rooms"][cid]
    cat = r["category"]
    res = bot.dm_decide(r["name"], cid, r["name"], text, [], extra=bot.DM.rule(dm["categories"].get(cat, {})),
                        use_context=True, category=cat, mode="on")
    print(f"[{r['name']}·{cat}] {text}\n  → {res[0]}: {res[1] or res[2]}  ({res[3]})\n")
