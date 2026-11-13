"""OWNER 중심 온톨로지 저장소 v0 (ontology/DESIGN.md §4·§6·§7).

- 저장: ontology/data/*.json (contacts·facts·groups·commitments) + journal.jsonl(쓰기 전 기록) + audit.jsonl
- 모든 쓰기는 ontology/schema.json으로 검사한다. 이 모듈은 카톡 발신·Iris 호출 코드를 import하지 않는다
  (봇의 Iris 호출·발신 함수를 쓰지 않음 — tests/test_ontology.py가 import 그래프로 확인).
- 쓰기 프로세스는 봇 하나가 원칙. 콘솔 도구가 쓸 때를 대비해 파일 잠금(.lock)도 건다.
표준 라이브러리만 사용.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path

BASE = Path(__file__).resolve().parent
ONT_DIR = BASE / "ontology"
SCHEMA_FILE = ONT_DIR / "schema.json"
KST = dt.timezone(dt.timedelta(hours=9))

# 코드 상수: schema.json과 다르면 카드 없음 모드(안전 쪽)로 동작한다
LOCKED_TOPICS_MIN = {"입시", "성적", "학점", "병역", "계좌", "송금", "건강"}
CONTACT_PREDICATES = {"relation_label", "my_role", "their_role", "honorific_mine", "address_term",
                      "member_of", "last_thread"}


class OntologyError(ValueError):
    """검증 실패(저장 거부)."""


def now_iso() -> str:
    return dt.datetime.now(KST).isoformat(timespec="seconds")


class Store:
    FILES = ("contacts", "facts", "groups", "commitments")

    def __init__(self, data_dir: Path | None = None, schema_file: Path | None = None) -> None:
        self.dir = Path(data_dir or ONT_DIR / "data")
        self.dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.schema_file = Path(schema_file or SCHEMA_FILE)
        self.ok = False            # False면 카드 없음 모드(읽기 결과 비움)
        self.problem = ""
        self.schema: dict = {}
        self.never: list[re.Pattern] = []
        self.data: dict[str, dict] = {}
        self.reload()

    # ───────── 로드·저장 ─────────
    def reload(self) -> None:
        with self.lock:
            try:
                self.schema = json.loads(self.schema_file.read_text(encoding="utf-8"))
                self.never = [re.compile(p) for p in self.schema["never_store_patterns"]]
                missing = LOCKED_TOPICS_MIN - set(self.schema.get("topic_lexicon", []))
                if missing:
                    raise OntologyError(f"schema.json 잠금 주제 누락: {sorted(missing)}")
                if set(self.schema["predicates"]["Contact"]) != CONTACT_PREDICATES:
                    raise OntologyError("schema.json Contact predicate가 코드 상수와 다름")
                self.data = {name: self._read(name) for name in self.FILES}
                self.ok, self.problem = True, ""
            except (OSError, KeyError, ValueError, re.error) as e:
                self.ok, self.problem = False, f"{type(e).__name__}: {e}"
                self.data = {name: {} for name in self.FILES}

    def _read(self, name: str) -> dict:
        f = self.dir / f"{name}.json"
        if not f.exists():
            return {}
        d = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            raise OntologyError(f"{f.name} 형식 오류")
        return d

    def _file_lock(self):
        store = self

        class _L:
            def __enter__(self_inner):
                lf = store.dir / ".lock"
                t0 = time.time()
                while True:
                    try:
                        fd = os.open(lf, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                        os.write(fd, str(os.getpid()).encode()); os.close(fd)
                        return self_inner
                    except FileExistsError:
                        try:
                            if time.time() - lf.stat().st_mtime > 30:   # 죽은 프로세스가 남긴 잠금
                                lf.unlink()
                                continue
                        except OSError:
                            pass
                        if time.time() - t0 > 10:
                            raise OntologyError("온톨로지 파일 잠금 대기 초과")
                        time.sleep(0.05)

            def __exit__(self_inner, *a):
                try:
                    (store.dir / ".lock").unlink()
                except OSError:
                    pass
        return _L()

    def _write(self, name: str, op: str, detail: dict) -> None:
        """journal 먼저, 그다음 원자적 교체(Windows replace 실패는 재시도)."""
        with self._file_lock():
            with open(self.dir / "journal.jsonl", "a", encoding="utf-8") as j:
                j.write(json.dumps({"at": now_iso(), "file": name, "op": op, **detail}, ensure_ascii=False) + "\n")
            f = self.dir / f"{name}.json"
            tmp = f.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data[name], ensure_ascii=False, indent=1), encoding="utf-8")
            for i in range(20):
                try:
                    tmp.replace(f)
                    break
                except PermissionError:
                    time.sleep(0.05 * (i + 1))
            else:
                raise OntologyError(f"{f.name} 저장 실패")

    def audit(self, who: str, action: str, target: str, note: str = "") -> None:
        with open(self.dir / "audit.jsonl", "a", encoding="utf-8") as a:
            a.write(json.dumps({"at": now_iso(), "who": who, "action": action, "target": target,
                                "note": self.mask(note)[:120]}, ensure_ascii=False) + "\n")

    # ───────── 검사 ─────────
    def never_hit(self, text: str) -> bool:
        return any(p.search(text or "") for p in self.never)

    def mask(self, text: str) -> str:
        out = text or ""
        for p in self.never:
            out = p.sub("[가림]", out)
        return out

    def topic_hit(self, text: str) -> str:
        t = text or ""
        return next((w for w in self.schema.get("topic_lexicon", []) if w in t), "")

    @staticmethod
    def subject_class(subject: str) -> str:
        if subject == "me":
            return "Me"
        if subject.startswith("c:"):
            return "Contact"
        if subject.startswith("g:"):
            return "Group"
        raise OntologyError(f"알 수 없는 subject: {subject}")

    def _check_value(self, predicate: str, value: str) -> str:
        v = str(value or "").strip()
        if not v:
            raise OntologyError("빈 값")
        lim = self.schema["limits"]["relation_label_chars" if predicate == "relation_label" else "value_chars"]
        if len(v) > lim:
            raise OntologyError(f"값이 {lim}자를 넘음")
        if self.never_hit(v):
            raise OntologyError("저장 금지 정보(전화·계좌·학번·이메일·주소) 포함")
        enum = self.schema.get("enums", {}).get(predicate)
        if enum and v not in enum:
            raise OntologyError(f"{predicate}는 {enum} 중 하나")
        return v

    # ───────── 사실 쓰기 ─────────
    def add_fact(self, subject: str, predicate: str, value: str, source: dict,
                 audience: str | None = None, confidence: float | None = None) -> dict:
        """검사를 통과하면 저장하고 Fact를 돌려준다. 실패하면 OntologyError."""
        if not self.ok:
            raise OntologyError(f"카드 없음 모드: {self.problem}")
        cls = self.subject_class(subject)
        if audience is None:   # 기본: 상대 사실은 그 상대에게만, 나머지는 주인만
            audience = f"contact:{subject[2:]}" if cls == "Contact" else "owner"
        if predicate not in self.schema["predicates"].get(cls, []):
            raise OntologyError(f"{cls}에 허용되지 않은 predicate: {predicate}")
        v = self._check_value(predicate, value)
        stype = str(source.get("type") or "")
        base = self.schema["source_types"].get(stype)
        if base is None:
            raise OntologyError(f"알 수 없는 출처 종류: {stype}")
        ref = str(source.get("ref") or "")
        conf = min(float(confidence if confidence is not None else base), base)
        status = "active"

        if cls == "Contact":
            chat_id = subject[2:]
            if stype in ("profile_md", "dm_categories"):
                status, audience = "candidate", "owner"     # 방 단위 출처가 없어 조건 4를 검증할 수 없음
            elif stype not in self.schema["contact_active_sources"]:
                raise OntologyError(f"상대 사실에 쓸 수 없는 출처: {stype}")
            elif stype not in ("owner", "iris_structural") and not ref.startswith(f"chat:{chat_id}#"):
                raise OntologyError("상대 사실은 그 상대와의 갠톡에서 나온 것만(ref=chat:<그 방>#…)")
            if audience not in ("owner", f"contact:{chat_id}"):
                audience = f"contact:{chat_id}"
            if predicate == "member_of":
                g = self.data["groups"].get(v)
                if not g or not g.get("me_member"):
                    raise OntologyError("member_of는 내가 먼저 속한 Group만")
        else:
            if audience == "any" and stype != "owner":
                audience = "group"                          # any는 주인만 부여
            if stype == "chat_other":
                if not ref.startswith("chat:"):
                    raise OntologyError("chat_other에는 ref=chat:<방>#… 필요")
                audience = "contact:" + ref[5:].split("#", 1)[0]
        if audience not in ("any", "group", "owner") and not audience.startswith(("contact:", "group:")):
            raise OntologyError(f"알 수 없는 audience: {audience}")
        if self.topic_hit(v):
            audience = "owner"                              # 잠금 주제는 값으로 판별, predicate와 무관

        fact = {"id": "f_" + uuid.uuid4().hex[:10], "subject": subject, "predicate": predicate, "value": v,
                "source": {"type": stype, "ref": self.mask(ref)[:80],
                           "observed_at": source.get("observed_at") or now_iso()},
                "confidence": round(conf, 2), "audience": audience, "status": status,
                "conflicts_with": [], "valid_from": source.get("valid_from"), "valid_until": source.get("valid_until"),
                "created_at": now_iso()}
        with self.lock:
            same = [f for f in self.data["facts"].values()
                    if f["subject"] == subject and f["predicate"] == predicate and f["status"] in ("active", "conflict")]
            if any(f["value"] == v for f in same):
                dup = next(f for f in same if f["value"] == v)
                return dup
            single = predicate in self.schema.get("identity_predicates", []) or predicate in (
                "relation_label", "their_role", "honorific_mine", "address_term", "status", "my_role")
            if single and same and stype != "owner" and status == "active":
                # 값이 다르면 둘 다 conflict로, 카드에는 값이 나가지 않는다. 해소는 주인만(resolve_conflict)
                fact["status"] = "conflict"
                fact["audience"] = "owner"
                for f in same:
                    f["status"], f["audience"] = "conflict", "owner"
                    f["conflicts_with"] = sorted(set(f["conflicts_with"]) | {fact["id"]})
                    fact["conflicts_with"].append(f["id"])
            elif single and same and stype == "owner":
                for f in same:                              # 주인 입력은 기존 값을 대체
                    f["status"] = "retracted"
            self.data["facts"][fact["id"]] = fact
            self._write("facts", "add", {"id": fact["id"], "subject": subject, "predicate": predicate})
        return fact

    def resolve_conflict(self, fact_id: str, who: str = "owner") -> list[str]:
        """주인이 하나를 채택: 그 값은 active, 경쟁 값은 retracted."""
        with self.lock:
            f = self.data["facts"].get(fact_id)
            if not f or f["status"] != "conflict":
                raise OntologyError("해소할 충돌이 아님")
            changed = [fact_id]
            f["status"], f["conflicts_with"] = "active", []
            for f2 in self.data["facts"].values():
                if f2 is not f and f2["subject"] == f["subject"] and f2["predicate"] == f["predicate"] \
                        and f2["status"] == "conflict":
                    f2["status"] = "retracted"; changed.append(f2["id"])
            self._write("facts", "resolve", {"id": fact_id})
            self.audit(who, "resolve_conflict", fact_id)
            return changed

    def set_audience(self, fact_id: str, audience: str, who: str = "owner") -> dict:
        with self.lock:
            f = self.data["facts"].get(fact_id)
            if not f:
                raise OntologyError("없는 사실")
            if audience != "owner" and self.topic_hit(f["value"]):
                raise OntologyError("잠금 주제는 공개로 바꿀 수 없음(코드 수정 필요)")
            if f["subject"].startswith("c:") and audience not in ("owner", "contact:" + f["subject"][2:]):
                raise OntologyError("상대 사실은 그 상대·주인에게만")
            f["audience"] = audience
            self._write("facts", "audience", {"id": fact_id, "audience": audience})
            self.audit(who, "set_audience", fact_id, audience)
            return f

    def retract(self, fact_id: str, who: str = "owner") -> None:
        with self.lock:
            f = self.data["facts"].get(fact_id)
            if f:
                f["status"] = "retracted"
                self._write("facts", "retract", {"id": fact_id})
                self.audit(who, "retract", fact_id)

    # ───────── 상대·그룹 ─────────
    def ensure_contact(self, chat_id: str, name: str, category: str = "미분류") -> dict:
        with self.lock:
            c = self.data["contacts"].get(chat_id)
            if c and c.get("status") == "forgotten":
                return c                                    # 잊은 상대는 다시 쌓지 않는다
            name = self.mask(name)[:40]
            if not c:
                c = {"chat_id": chat_id, "name": name, "aliases": [], "category": category,
                     "status": "active", "last_inbound": None, "hold_sent": None, "created_at": now_iso()}
                self.data["contacts"][chat_id] = c
                self._write("contacts", "add", {"chat_id": chat_id})
            elif name and name != c["name"] and name not in c["aliases"]:
                c["aliases"].append(name)
                self._write("contacts", "alias", {"chat_id": chat_id})
            return c

    def forget_contact(self, chat_id: str, who: str = "owner") -> int:
        """상대 사실 전부 삭제, 레코드는 chat_id만 남겨 다시 쌓이지 않게(status=forgotten)."""
        with self.lock:
            gone = [k for k, f in self.data["facts"].items() if f["subject"] == f"c:{chat_id}"
                    or f["audience"] == f"contact:{chat_id}"]
            for k in gone:
                del self.data["facts"][k]
            self.data["contacts"][chat_id] = {"chat_id": chat_id, "status": "forgotten", "forgotten_at": now_iso()}
            for g in self.data["groups"].values():
                if chat_id in g.get("members", []):
                    g["members"].remove(chat_id)
            self._write("facts", "forget", {"chat_id": chat_id, "n": len(gone)})
            self._write("contacts", "forget", {"chat_id": chat_id})
            self._write("groups", "forget", {"chat_id": chat_id})
            self.audit(who, "forget_contact", chat_id, f"{len(gone)}건")
            return len(gone)

    def add_group(self, gid: str, kind: str, name: str, me_member: bool = True, audience: str = "group") -> dict:
        if not gid.startswith("g:"):
            raise OntologyError("Group id는 g:로 시작")
        if kind not in self.schema["group_kinds"]:
            raise OntologyError(f"Group kind는 {self.schema['group_kinds']}")
        name = self._check_value("name", name)
        with self.lock:
            g = self.data["groups"].setdefault(gid, {"id": gid, "kind": kind, "name": name, "members": [],
                                                     "me_member": me_member, "show_member_names": False,
                                                     "audience": audience})
            self._write("groups", "add", {"id": gid})
            return g

    def add_member(self, gid: str, chat_id: str) -> None:
        with self.lock:
            g = self.data["groups"].get(gid)
            if not g or not g.get("me_member"):
                raise OntologyError("내가 속한 Group만")
            if chat_id not in g["members"]:
                g["members"].append(chat_id)
                self._write("groups", "member", {"id": gid, "chat_id": chat_id})

    # ───────── 읽기(가시성) ─────────
    def visible_facts(self, viewer_chat_id: str, category: str = "미분류") -> list[dict]:
        """이 상대에게 말해도 되는 active 사실만. 범주 천장·audience·잠금 주제를 모두 적용."""
        if not self.ok:
            return []
        order = {"any": 0, "group": 1, "contact": 2, "owner": 3}
        ceiling = order[self.schema["category_ceiling"].get(category, "contact")]
        if ceiling >= 3:
            return []
        my_groups = {gid for gid, g in self.data["groups"].items() if viewer_chat_id in g.get("members", [])}
        out = []
        for f in self.data["facts"].values():
            if f["status"] != "active" or self.topic_hit(f["value"]) or self.never_hit(f["value"]):
                continue
            a = f["audience"]
            if a == "owner":
                continue
            if a.startswith("contact:"):
                ok = a == f"contact:{viewer_chat_id}"
            elif a.startswith("group:"):
                ok = ceiling >= 1 and a[6:] in my_groups
            elif a == "group":
                # 나(Me)에 관한 group 사실(수업·프로그램·활동)은 학교 맥락 범주(me_group_categories: 학교·교수)에만.
                # Group 객체의 사실은 그 Group에 함께 속한 상대에게만.
                school_ctx = category in self.schema.get("me_group_categories", ["학교", "교수"])
                ok = ceiling >= 1 and ((f["subject"] == "me" and (school_ctx or bool(my_groups)))
                                       or f["subject"] in my_groups or f["subject"] == f"c:{viewer_chat_id}")
            else:  # any
                ok = ceiling >= 0
            if f["subject"].startswith("c:") and f["subject"] != f"c:{viewer_chat_id}":
                ok = False                                  # 다른 상대에 관한 사실은 절대 안 보인다
            if f["subject"].startswith("g:") and f["subject"] not in my_groups:
                ok = False
            if ok:
                out.append(f)
        return out

    def conflicts(self) -> list[dict]:
        return [f for f in self.data["facts"].values() if f["status"] == "conflict"]

    def validate_all(self) -> list[str]:
        """저장된 전체를 다시 검사(콘솔 validate). 문제 목록."""
        probs = []
        for f in self.data["facts"].values():
            if self.never_hit(f["value"]) or self.never_hit(f["source"].get("ref", "")):
                probs.append(f"{f['id']}: 저장 금지 정보")
            if self.topic_hit(f["value"]) and f["audience"] != "owner":
                probs.append(f"{f['id']}: 잠금 주제가 공개 범위")
            if f["subject"].startswith("c:") and f["status"] == "active" and f["source"]["type"] in ("profile_md", "dm_categories"):
                probs.append(f"{f['id']}: 방 출처 없는 상대 사실이 active")
        for c in self.data["contacts"].values():
            if self.never_hit(" ".join([c.get("name") or ""] + c.get("aliases", []))):
                probs.append(f"contact {c['chat_id']}: 저장 금지 정보")
        return probs


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    st = Store()
    print("상태:", "정상" if st.ok else f"카드 없음 모드 — {st.problem}")
    print({k: len(v) for k, v in st.data.items()})
    probs = st.validate_all()
    print("검사:", "문제 없음" if not probs else "\n".join(probs))
