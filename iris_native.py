# -*- coding: utf-8 -*-
"""Iris 호환 네이티브 셔임 — LDPlayer 없이 PC 카카오톡만으로 bot.py 를 돌린다.

bot.py 는 원래 안드로이드 에뮬레이터 안의 Iris 에 HTTP 로 붙는다(/query·/reply·웹훅).
이 모듈은 같은 HTTP 인터페이스를 흉내 내되 속을 PC 카카오톡으로 채운다:
  - /query : PC 카톡 로컬 DB(kakao_native.account)를 Iris 스키마(chat_logs, chat_rooms, db2.friends ...)로
             옮긴 인메모리 SQLite 에 SELECT 만 실행한다.
  - /reply : pc_kakao_send 로 PC 카톡 창에 직접 넣는다.
  - 웹훅   : 새 메시지를 폴링해 봇의 /webhook 으로 Iris 형식 이벤트를 보낸다.

본인 PC·본인 계정 전용. DB 는 읽기만 한다. 전송 잠금(config.json):
  native_send_only_memo  : true 면 '나와의 채팅' 말고는 PC 창에 아무것도 넣지 않는다(기본·설정을 못 읽어도 true).
  native_send_rooms_only : chat_id 목록. 비어 있지 않으면 그 방에만 보낸다.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kakao_native.account import Account, _int  # noqa: E402

CONFIG = ROOT / "config.json"


def load_cfg() -> dict:
    """보낼 때마다 새로 읽는다(재시작 없이 잠금을 풀고 건다). 못 읽으면 가장 안전한 값."""
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"native_send_only_memo": True}


def send_allowed(cfg: dict, room_id: str, room_type: str | None) -> tuple[bool, str]:
    if cfg.get("native_send_only_memo", True) and room_type != "MemoChat":
        return False, "test lock: MemoChat only (native_send_only_memo)"
    if room_type == "PlusChat":
        return False, "channel rooms are never sent to"
    only = [str(x) for x in (cfg.get("native_send_rooms_only") or [])]
    if only and room_id not in only:
        return False, "room not in native_send_rooms_only"
    return True, ""


class NativeIris:
    """PC 카톡 DB → Iris 스키마 인메모리 SQLite. 새 메시지는 poll() 로 집어낸다."""

    def __init__(self, account: Account | None = None, verbose=print, per_room: int = 3000, rescan_sec: float = 120.0):
        self.log = verbose
        self.acct = account or Account(log=verbose)
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        self.last_by_room: dict[str, int] = {}
        self.endpoint: str | None = None   # 봇이 /config/endpoint 로 알려 주는 웹훅 주소
        self.rescan_sec = rescan_sec
        self._last_scan = 0.0
        self._mtimes: dict[str, float] = {}
        self._schema()
        self._load_static()
        self.acct.scan_rooms()
        self._load_all_logs(per_room)

    @property
    def my_id(self) -> str:
        return self.acct.my_id

    def _schema(self) -> None:
        c = self.db
        c.execute("CREATE TABLE chat_logs(_id INTEGER, id INTEGER, chat_id TEXT, user_id TEXT, type INTEGER, "
                  "message TEXT, attachment TEXT, v TEXT, created_at INTEGER, thread_id INTEGER, scope TEXT)")
        c.execute("CREATE INDEX ix_cl ON chat_logs(chat_id, _id)")
        c.execute("CREATE INDEX ix_cl_at ON chat_logs(created_at)")
        c.execute("CREATE TABLE chat_rooms(id TEXT PRIMARY KEY, type TEXT, link_id TEXT, active_member_ids TEXT, "
                  "active_members_count INTEGER, meta TEXT, last_updated_at INTEGER, last_log_id INTEGER)")
        c.execute("ATTACH DATABASE ':memory:' AS db2")
        c.execute("CREATE TABLE db2.friends(id TEXT, name TEXT, nick_name TEXT, enc INTEGER, chat_id TEXT)")
        c.execute("CREATE TABLE db2.open_link(id TEXT, name TEXT, description TEXT)")
        c.execute("CREATE TABLE db2.open_chat_member(involved_chat_id TEXT, user_id TEXT, nickname TEXT, enc INTEGER)")
        c.execute("CREATE TABLE db2.open_profile(link_id TEXT, user_id TEXT, nickname TEXT)")
        c.commit()

    def _load_static(self) -> None:
        users = self.acct.query("TalkUserDB.edb", "SELECT userId, nickName, friendNickName, directChatId FROM talkUser")
        self.db.executemany("INSERT INTO db2.friends(id,name,nick_name,enc,chat_id) VALUES(?,?,?,0,?)",
                            [(str(r["userId"]), r["nickName"] or "", r["friendNickName"] or "", str(r["directChatId"] or ""))
                             for r in users])
        for r in self.acct.rooms:
            meta = json.dumps([{"type": 3, "content": r["title"]}] if r["title"] else [], ensure_ascii=False)
            self.db.execute("INSERT OR REPLACE INTO chat_rooms(id,type,link_id,active_member_ids,active_members_count,"
                            "meta,last_updated_at,last_log_id) VALUES(?,?,?,?,?,?,?,?)",
                            (r["id"], r["type"], r["link_id"], json.dumps(r["members"] + [self.my_id]),
                             r["member_count"], meta, r["last"], r["last_log_id"]))
        self.db.commit()
        self.log(f"iris_native: 방 {len(self.acct.rooms)}개, 친구 {len(users)}명")

    def _insert_logs(self, chat_id: str, rows) -> int:
        buf = []
        for r in rows:
            lid = _int(r["logId"])
            buf.append((lid, lid, chat_id, str(r["authorId"]), _int(r["type"]), r["message"] or "",
                        r["attachement"] or "", "", _int(r["sendAt"]), _int(r["threadId"]), str(r["scope"] or "0")))
            self.last_by_room[chat_id] = max(self.last_by_room.get(chat_id, 0), lid)
        if buf:
            self.db.executemany("INSERT INTO chat_logs(_id,id,chat_id,user_id,type,message,attachment,v,created_at,"
                                "thread_id,scope) VALUES(?,?,?,?,?,?,?,?,?,?,?)", buf)
        return len(buf)

    def _load_all_logs(self, per_room: int) -> None:
        total = readable = 0
        with self.lock:
            for room in self.acct.rooms:
                if not self.acct.readable(room["id"]):
                    continue
                readable += 1
                rows = self.acct.raw_messages(room["id"])
                if per_room and len(rows) > per_room:
                    rows = rows[-per_room:]
                total += self._insert_logs(room["id"], rows)
                self.last_by_room.setdefault(room["id"], 0)
            self.db.commit()
        self.log(f"iris_native: 읽을 수 있는 방 {readable}/{len(self.acct.rooms)}개, 메시지 {total:,}줄 적재")

    def _rescan_changed(self) -> None:
        """키가 없는 방 중 DB 파일이 바뀐 방(=새 메시지)만 골라 메모리 키를 다시 찾는다(최대 rescan_sec 마다 한 번).
        새 메시지가 오면 카톡이 그 방 DB 를 열어 키가 메모리에 올라온다. 전에 키가 나온 영역부터 보므로 대개 빨리 끝난다."""
        if time.time() - self._last_scan < self.rescan_sec:
            return
        self._last_scan = time.time()
        changed = []
        for room in self.acct.rooms:
            if room["type"] == "PlusChat" or self.acct.readable(room["id"]):
                continue
            path = os.path.join(self.acct.acc, self.acct.chat_relpath(room["id"]))
            try:
                m = max(os.path.getmtime(p) for p in (path, path + "-wal") if os.path.exists(p))
            except ValueError:
                continue
            if self._mtimes.get(room["id"]) != m:
                self._mtimes[room["id"]] = m
                changed.append(room["id"])
        if changed:
            try:
                self.acct.scan_rooms(changed)
            except Exception as e:  # noqa: BLE001
                self.log(f"키 재스캔 실패: {type(e).__name__}")

    def poll(self) -> list[dict]:
        """새 메시지를 chat_logs 에 넣고 Iris 형식 이벤트 목록을 돌려준다. 내 글도 넣는다(봇이 스스로 거른다)."""
        self._rescan_changed()
        events = []
        names = {r["id"]: r["name"] for r in self.acct.rooms}
        with self.lock:
            for room in self.acct.rooms:
                cid = room["id"]
                if not self.acct.readable(cid):
                    continue
                first = cid not in self.last_by_room
                last = self.last_by_room.get(cid, 0)
                rows = self.acct.raw_messages(cid, last)
                if not rows:
                    self.last_by_room.setdefault(cid, 0)
                    continue
                self._insert_logs(cid, rows)
                if first:
                    continue   # 새로 읽게 된 방의 과거 글은 이벤트로 보내지 않는다
                for r in rows:
                    uid, msg = str(r["authorId"]), r["message"] or ""
                    events.append({
                        "json": {"_id": _int(r["logId"]), "id": _int(r["logId"]), "chat_id": cid, "user_id": uid,
                                 "type": _int(r["type"]), "message": msg, "created_at": _int(r["sendAt"]),
                                 "thread_id": _int(r["threadId"])},
                        "msg": msg, "sender": "나" if uid == self.my_id else self.acct.names.get(uid, uid),
                        "room": names.get(cid, cid)})
            self.db.commit()
        return events

    def query(self, sql: str, bind: list | None = None) -> list[dict]:
        if not re.match(r"\s*(SELECT|PRAGMA)\b", sql, re.I):
            raise ValueError("read only: SELECT only")
        with self.lock:
            cur = self.db.execute(sql, list(bind or []))
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def reply(self, body: dict, sender=None) -> dict:
        """봇의 /reply 요청을 PC 카톡 창으로 보낸다. sender(title, text) 는 테스트용 주입점."""
        room_id = str(body.get("room") or "")
        text = body.get("data") or ""
        row = self.query("SELECT id, type, meta FROM chat_rooms WHERE id = ?", [room_id])
        rtype = row[0]["type"] if row else None
        ok, why = send_allowed(load_cfg(), room_id, rtype)
        if not ok:
            self.log(f"전송 거부({why}) room=..{room_id[-4:]} type={rtype}")
            return {"success": False, "native_pc": True, "error": why}
        title = ""
        if row:
            try:
                for m in json.loads(row[0]["meta"] or "[]"):
                    if m.get("type") == 3 and m.get("content"):
                        title = m["content"]
            except ValueError:
                pass
        if not title:
            title = next((r["name"] for r in self.acct.rooms if r["id"] == room_id), room_id)
        try:
            if sender is None:
                import pc_kakao_send as PK
                r = PK.send(title, text, send=True, auto_open=True)
            else:
                r = sender(title, text)
            # 단계별 전송 시간을 남긴다(느린 구간을 찾는 용도). 글 내용·방 이름은 적지 않는다.
            try:
                tm = dict(r.get("timings_ms") or {})
                op = tm.pop("open_chat", None)
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} 전송 시간 room={str(room_id)[-5:]} ok={bool(r.get('ok'))} "
                      f"confirmed={bool(r.get('confirmed'))} 방열기={'예' if op is not None else '아니요'} "
                      f"ms={json.dumps(tm, ensure_ascii=False)}"
                      + (f" 방열기_ms={json.dumps(op, ensure_ascii=False)}" if op else ""), flush=True)
            except Exception:  # noqa: BLE001
                pass
            return {"success": bool(r.get("ok") and r.get("confirmed")), "native_pc": True, "error": r.get("error")}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "native_pc": True, "error": f"pc_send: {type(e).__name__}"}


# ───────────────────────── HTTP 셔임 ─────────────────────────
def make_server(ni: NativeIris, port: int = 3001, verbose=print) -> ThreadingHTTPServer:
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, obj, code=200):
            b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):  # noqa: N802
            if self.path.rstrip("/") == "/config":
                self._send({"bot_id": ni.my_id, "bot_name": "", "native": True, "web_server_endpoint": ni.endpoint})
            else:
                self._send({"error": "not found"}, 404)

        def do_POST(self):  # noqa: N802
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n).decode("utf-8") or "{}") if n else {}
            except ValueError:
                return self._send({"error": "bad json"}, 400)
            path = self.path.rstrip("/")
            if path == "/query":
                try:
                    self._send({"data": ni.query(body.get("query", ""), body.get("bind"))})
                except Exception as e:  # noqa: BLE001
                    self._send({"error": str(e)[:200]}, 400)
            elif path == "/reply":
                self._send(ni.reply(body))
            elif path == "/config/endpoint":
                ni.endpoint = body.get("endpoint") or ni.endpoint
                verbose(f"웹훅 등록 → {ni.endpoint}")
                self._send({"ok": True})
            else:
                self._send({"error": "not found"}, 404)

    return ThreadingHTTPServer(("127.0.0.1", port), H)


def serve(port: int = 3001, webhook: str | None = None, poll_sec: float = 2.0, verbose=print, keys_mode: str = "auto"):
    ni = NativeIris(Account(keys_mode=keys_mode, log=verbose), verbose=verbose)
    srv = make_server(ni, port, verbose)
    verbose(f"iris_native 셔임: http://127.0.0.1:{port}")
    if webhook:
        ni.endpoint = webhook   # 아니면 봇이 /config/endpoint 로 알려 줄 때까지 기다린다

    def _poller():
        while True:
            try:
                for ev in (ni.poll() if ni.endpoint else []):
                    try:
                        req = urllib.request.Request(ni.endpoint, data=json.dumps(ev).encode("utf-8"),
                                                     headers={"Content-Type": "application/json"})
                        urllib.request.urlopen(req, timeout=10).read()
                    except Exception:  # noqa: BLE001
                        pass
            except Exception as e:  # noqa: BLE001
                verbose(f"poll 오류: {str(e)[:120]}")
            time.sleep(poll_sec)

    threading.Thread(target=_poller, daemon=True).start()
    srv.serve_forever()


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    import argparse
    ap = argparse.ArgumentParser(description="Iris 호환 PC 카카오톡 셔임")
    ap.add_argument("--port", type=int, default=3001)
    ap.add_argument("--webhook", default=None)
    ap.add_argument("--keys", choices=("auto", "memory", "v2"), default="auto")
    a = ap.parse_args()
    serve(a.port, a.webhook, keys_mode=a.keys)
