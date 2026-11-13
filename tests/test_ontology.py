"""ontology_store.py 오프라인 테스트. 임시 폴더에만 쓴다(실제 ontology/data/·카톡 무관)."""
import ast
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")
from ontology_store import OntologyError, Store  # noqa: E402

ok = True
cnt = 0


def check(name, cond):
    global ok, cnt
    cnt += 1
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name)


def rejects(fn):
    try:
        fn()
        return False
    except OntologyError:
        return True


tmp = Path(tempfile.mkdtemp(prefix="ont_test_"))
st = Store(tmp)
check("스키마 로드 정상", st.ok)

# ── 저장 금지 ──
for bad in ["010-1234-5678", "110-123-456789", "홍길동_12345678", "a.b@example.ac.kr", "서울시 가상구 예시로 12",
            "101동 1203호", "1234567890123입니다"]:
    check(f"[금지] '{bad}' 값 거부", rejects(lambda b=bad: st.add_fact("me", "phrase", b, {"type": "owner"})))
for good in ["2026-11-07", "11월 7일 2시", "3만원", "CLUB 부원"]:
    check(f"[금지] '{good}'은 통과", not st.never_hit(good))

# ── predicate·출처 규칙 ──
st.ensure_contact("111", "이도윤", "학교")
st.ensure_contact("222", "박민수", "친구")
check("[규칙] 상대에게 school predicate 거부",
      rejects(lambda: st.add_fact("c:111", "school", "한빛대", {"type": "owner"})))
check("[규칙] 상대 사실은 그 방 출처만(다른 방 ref 거부)",
      rejects(lambda: st.add_fact("c:111", "relation_label", "팀원", {"type": "chat_other", "ref": "chat:222#5"})))
f = st.add_fact("c:111", "relation_label", "동아리 프로젝트 팀원", {"type": "chat_self", "ref": "chat:111#90"})
check("[규칙] 그 방 출처면 저장, audience는 그 상대", f["status"] == "active" and f["audience"] == "contact:111")
f2 = st.add_fact("c:222", "relation_label", "친구", {"type": "profile_md", "ref": "profile.md#2"})
check("[규칙] profile_md 출처 상대 사실은 candidate·주인만", f2["status"] == "candidate" and f2["audience"] == "owner")
check("[규칙] enum 밖 값 거부", rejects(lambda: st.add_fact("c:111", "their_role", "보스", {"type": "owner"})))
check("[규칙] 내가 속하지 않은 Group에 member_of 거부",
      rejects(lambda: st.add_fact("c:111", "member_of", "g:none", {"type": "owner"})))
fa = st.add_fact("me", "interest", "보안/CTF", {"type": "browser_summary", "ref": "x"}, audience="any")
check("[규칙] any는 주인만 부여(파이프라인은 group으로 낮춤)", fa["audience"] == "group")
fo = st.add_fact("me", "interest", "LLM 도구", {"type": "owner"}, audience="any")
check("[규칙] 주인은 any 가능", fo["audience"] == "any")
fl = st.add_fact("me", "status", "입시 준비 중", {"type": "owner"}, audience="any")
check("[잠금] 값에 잠금 주제가 있으면 predicate·출처와 무관하게 주인만", fl["audience"] == "owner")
check("[잠금] 잠금 주제는 공개로 못 바꿈", rejects(lambda: st.set_audience(fl["id"], "any")))
fc = st.add_fact("g:x", "status", "진행 중", {"type": "chat_other", "ref": "chat:222#7"}) if st.add_group("g:x", "project", "팀플") else None
check("[규칙] chat_other 출처 Group 사실은 그 방에만", fc["audience"] == "contact:222")

# ── 충돌 ──
a = st.add_fact("me", "school", "한빛대학교", {"type": "browser_summary", "ref": "b"})
b = st.add_fact("me", "school", "누리대학교", {"type": "memory", "ref": "m"})
check("[충돌] 다른 학교 값 → 둘 다 conflict·주인만",
      st.data["facts"][a["id"]]["status"] == "conflict" and b["status"] == "conflict" and b["audience"] == "owner")
check("[충돌] 충돌 값은 누구에게도 안 보임",
      not any(x["predicate"] == "school" for x in st.visible_facts("111", "학교")))
st.resolve_conflict(a["id"])
check("[충돌] 주인 채택 → 하나 active, 나머지 retracted",
      st.data["facts"][a["id"]]["status"] == "active" and st.data["facts"][b["id"]]["status"] == "retracted")

# ── 가시성 ──
st.add_group("g:club", "org", "CLUB(동아리)")
st.add_member("g:club", "111")
st.add_fact("g:club", "my_role", "부원", {"type": "owner"}, audience="group")
v111 = st.visible_facts("111", "학교")
v222 = st.visible_facts("222", "친구")
check("[가시성] 같은 그룹 상대는 그룹 사실을 봄", any(x["subject"] == "g:club" for x in v111))
check("[가시성] 그룹 밖 상대는 그룹 사실을 못 봄", not any(x["subject"] == "g:club" for x in v222))
check("[가시성] 다른 상대에 관한 사실은 안 보임", not any(x["subject"] == "c:111" for x in v222))
check("[가시성] 자기 관계 사실은 봄", any(x["subject"] == "c:111" for x in v111))
check("[가시성] candidate는 안 보임", not any(x["status"] != "active" for x in v111 + v222))
check("[가시성] 가족 범주는 아무것도 안 보임", st.visible_facts("111", "가족") == [])
st.ensure_contact("333", "교수님", "교수")
fc2 = st.add_fact("me", "course", "자료구조", {"type": "site", "ref": "sites:ecampus"}, audience="group")
check("[가시성] 그룹이 없어도 학교·교수 범주 상대는 내 수업(group)을 봄", any(x["id"] == fc2["id"] for x in st.visible_facts("333", "교수")))
st.ensure_contact("444", "외부", "업무·외부")
check("[가시성] 친구는 내 수업을 보고 업무·외부는 못 봄", any(x["id"] == fc2["id"] for x in st.visible_facts("222", "친구")) and not any(x["id"] == fc2["id"] for x in st.visible_facts("444", "업무·외부")))
check("[가시성] 그룹 객체 사실은 여전히 그 그룹 상대에게만", not any(x["subject"] == "g:club" for x in st.visible_facts("333", "교수")))

# ── 잊기·재시작 ──
n = st.forget_contact("111")
check("[잊기] 상대 사실 삭제, 레코드는 forgotten", n >= 1 and st.data["contacts"]["111"]["status"] == "forgotten")
st.ensure_contact("111", "이도윤", "학교")
check("[잊기] 잊은 상대는 다시 쌓이지 않음", st.data["contacts"]["111"]["status"] == "forgotten")
st2 = Store(tmp)
check("[저장] 다시 읽어도 같은 내용", st2.ok and len(st2.data["facts"]) == len(st.data["facts"]))
check("[저장] journal·audit 기록", (tmp / "journal.jsonl").exists() and (tmp / "audit.jsonl").exists())
check("[저장] validate_all 문제 없음", st2.validate_all() == [])

# ── 안전 쪽 실패 ──
bad_schema = tmp / "bad_schema.json"
d = json.loads((ROOT / "ontology" / "schema.json").read_text(encoding="utf-8"))
d["topic_lexicon"] = [w for w in d["topic_lexicon"] if w != "입시"]
bad_schema.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
st3 = Store(tmp / "d3", bad_schema)
check("[실패] 잠금 주제가 빠진 스키마 → 카드 없음 모드(읽기 비움·쓰기 거부)",
      not st3.ok and st3.visible_facts("1") == [] and rejects(lambda: st3.add_fact("me", "phrase", "x", {"type": "owner"})))

# ── 발신 경로 없음(import 그래프) ──
tree = ast.parse((ROOT / "ontology_store.py").read_text(encoding="utf-8"))
mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | \
       {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
src = (ROOT / "ontology_store.py").read_text(encoding="utf-8")
check("[발신 없음] 저장소는 bot·urllib·socket·subprocess를 import하지 않음",
      not mods & {"bot", "urllib", "urllib.request", "socket", "http.client", "subprocess", "requests"})
check("[발신 없음] send_text·iris(·/reply 문자열 없음", "send_text" not in src and "iris(" not in src and "/reply" not in src)

shutil.rmtree(tmp, ignore_errors=True)
print(f"{cnt} checks:", "ALL OK" if ok else "SOME FAILED")
