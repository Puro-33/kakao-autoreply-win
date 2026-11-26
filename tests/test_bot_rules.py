"""bot.py 의 하트·단톡 의견·보충 판정 규칙 시험. 실제 카톡·전송은 건드리지 않는다.

bot.py 는 import 할 때 config.json 이 필요하므로 저장소를 임시 폴더로 복사하고 config.example.json 을 config.json 으로 둔다.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent

CHECKS = r'''
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8")
import bot

n, ok = [0], [True]


def check(name, cond):
    n[0] += 1
    ok[0] = ok[0] and bool(cond)
    print(("OK   " if cond else "FAIL ") + name)


bot.CFG["heart_reaction"] = {**bot.CFG["heart_reaction"], "enabled": True}
for t in ("다음주에 봬요!", "넵 감사합니다~", "넵 제출했습니다", "ㅇㅋ", "수고하셨어요"):
    check("하트: " + t, bot.heart_judge(t))
for t in ("다음주에 몇 시에 볼까요?", "감사합니다 혹시 파일 다시 보내주실 수 있어요?", "내일 3시에 봬요", "계좌 알려주세요", ""):
    check("글 답장: " + t, not bot.heart_judge(t))
check("긴 글은 하트 안 함", not bot.heart_judge("감사합니다 " * 8))
check("갠톡 텍스트만 하트", bot.heart_wanted("1", "DirectChat", 1, "넵 감사합니다~", "")
      and not bot.heart_wanted("1", "MultiChat", 1, "넵 감사합니다~", "")
      and not bot.heart_wanted("1", "DirectChat", 2, "넵 감사합니다~", "")
      and not bot.heart_wanted("1", "DirectChat", 1, "넵 감사합니다~", "멘션")
      and not bot.heart_wanted("1", "DirectChat", 1, "넵 감사합니다~", "", 77))
bot.CFG["heart_reaction"]["enabled"] = False
check("enabled=false 면 끔", not bot.heart_judge("넵 감사합니다~"))

bot.CFG["group_opinion"] = {"enabled": True, "always_users": ["9"], "exclude_rooms": ["5"]}
check("의견 질문", bot.opinion_ask("1", "2", "이 일정으로 진행해도 괜찮을까요?") and bot.opinion_ask("1", "2", "다들 어떻게 생각하세요?"))
check("멘션·짧은 글·명령은 제외", not bot.opinion_ask("1", "2", "@홍길동 괜찮을까요?") and not bot.opinion_ask("1", "2", "괜찮나요")
      and not bot.opinion_ask("1", "2", "!상태 괜찮을까요?"))
check("제외 방", not bot.opinion_ask("5", "2", "이 일정으로 진행해도 괜찮을까요?"))
check("always_users 는 물음표만 있어도", bot.opinion_ask("1", "9", "오늘 모임 가나?") and not bot.opinion_ask("1", "2", "오늘 모임 가나?"))
bot.CFG["group_opinion"]["enabled"] = False
check("enabled=false 면 끔", not bot.opinion_ask("1", "2", "이 일정으로 진행해도 괜찮을까요?"))

H = 3600
rows = [{"_id": 1, "user_id": "x", "created_at": 1000}, {"_id": 2, "user_id": "x", "created_at": 1000 + H},
        {"_id": 3, "user_id": "x", "created_at": 1000 + 2 * H}, {"_id": 3, "user_id": "x", "created_at": 1000 + 2 * H},
        {"_id": 4, "user_id": "x", "created_at": 1000 + 2 * H + 600}]


def mine(u):
    return u == "me"


check("마지막 묶음은 간격 안으로 이어진 끝 글들", bot.catchup_batch_since(rows, mine, 1800) == 1000 + 2 * H)
check("내 글이 더 뒤에 있으면 대상 없음", bot.catchup_batch_since(rows + [{"_id": 5, "user_id": "me", "created_at": 1000 + 3 * H}], mine, 1800) == float("inf"))
check("내 글 뒤의 남의 글만 센다", bot.catchup_batch_since(rows + [{"_id": 5, "user_id": "me", "created_at": 1000 + 2 * H + 60}], mine, 1800) == 1000 + 2 * H + 600)
check("보충 판정: 통과", bot.catchup_verdict(20 * H, False, 5000, 5000, 3600, 72 * H) == "")
check("보충 판정: 72시간 넘음", "72시간" in bot.catchup_verdict(73 * H, False, 5000, 5000, 3600, 72 * H))
check("보충 판정: 마지막 묶음 아님", "묶음" in bot.catchup_verdict(20 * H, False, 4000, 5000, 3600, 72 * H))
check("보충 판정: 내 글은 시간 안이면 통과, 넘으면 건너뜀", bot.catchup_verdict(30 * 60, True, 1, 9, 3600, 72 * H) == ""
      and bot.catchup_verdict(2 * H, True, 1, 9, 3600, 72 * H) != "")
print(f"{n[0]} checks:", "ALL OK" if ok[0] else "SOME FAILED")
sys.exit(0 if ok[0] else 1)
'''


def test_bot_rules():
    with tempfile.TemporaryDirectory() as tmp:
        dst = Path(tmp) / "repo"
        shutil.copytree(BASE, dst, ignore=shutil.ignore_patterns(".git", "tests", "research", "__pycache__", "logs"))
        shutil.copy(dst / "config.example.json", dst / "config.json")
        script = dst / "run_checks.py"
        script.write_text(CHECKS, encoding="utf-8")
        r = subprocess.run([sys.executable, "-I", str(script)], cwd=dst,
                           capture_output=True, text=True, encoding="utf-8", timeout=120)
        assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
        assert "ALL OK" in r.stdout
