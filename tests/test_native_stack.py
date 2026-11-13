# -*- coding: utf-8 -*-
"""네이티브 경로(kakao_native.account · iris_native · kakaowin) 오프라인 테스트.

실제 카카오톡 DB·프로세스·창을 쓰지 않는다. 평문 SQLite 로 만든 가짜 계정 폴더와
가짜 키 제공자, 가짜 전송 함수로만 시험한다. 실제 메시지는 절대 보내지 않는다.

    py -3.12 -m pytest tests/test_native_stack.py -q
"""
from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "kakao_native" / "tests"))

from kakao_native import account as A  # noqa: E402

ME, FRIEND, OTHER = "1001", "2002", "3003"
NOW = 1_800_000_000


def _make_db(path: Path, ddl: list[str], data: list[tuple[str, list[tuple]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    for d in ddl:
        c.execute(d)
    for sql, rows in data:
        c.executemany(sql, rows)
    c.commit()
    c.close()


@pytest.fixture()
def fake_acc(tmp_path, monkeypatch):
    """가짜 계정 폴더: 방 3개(갠톡·단톡·나와의 채팅)와 사용자 2명, 방별 메시지. 파일은 '평문' SQLite."""
    acc = tmp_path / "users" / "abc"
    _make_db(acc / "TalkUserDB.edb",
             ["CREATE TABLE talkUser(userId, nickName, friendNickName, directChatId)"],
             [("INSERT INTO talkUser VALUES(?,?,?,?)", [(FRIEND, "친구닉", "홍길동", "11"), (OTHER, "다른이", "", "")])])
    _make_db(acc / "chat_data" / "chatListInfo.edb",
             ["CREATE TABLE chatMembers(chatId, userId, isActive)",
              "CREATE TABLE chatRoomList(chatId, type, useCustomChatRoomTitle, chatRoomTitle, directChatMemberId, "
              "activeMembersCount, lastUpdatedAt, lastLogId, linkId)"],
             [("INSERT INTO chatMembers VALUES(?,?,1)", [("11", ME), ("11", FRIEND), ("22", ME), ("22", FRIEND),
                                                         ("22", OTHER), ("33", ME)]),
              ("INSERT INTO chatRoomList VALUES(?,?,?,?,?,?,?,?,?)",
               [("11", "DirectChat", 0, "", FRIEND, 2, NOW + 30, 3, ""),
                ("22", "MultiChat", 1, "스터디방", "", 3, NOW + 20, 2, ""),
                ("33", "MemoChat", 0, "", "", 1, NOW + 10, 1, "")])])
    for cid, msgs in {"11": [(1, FRIEND, 1, "안녕", NOW), (2, ME, 1, "응 안녕", NOW + 5), (3, FRIEND, 2, "", NOW + 6)],
                      "22": [(1, OTHER, 1, "회의 언제?", NOW), (2, FRIEND, 18, "", NOW + 1)],
                      "33": [(1, ME, 1, "메모", NOW)]}.items():
        _make_db(acc / "chat_data" / f"chatLogs_{cid}.edb",
                 ["CREATE TABLE chatLogs(logId, authorId, type, message, attachement, sendAt, threadId, scope)"],
                 [("INSERT INTO chatLogs VALUES(?,?,?,?,?,?,0,0)",
                   [(l, a, t, m, json.dumps({"name": "a.pdf"}) if t == 18 else "", s) for l, a, t, m, s in msgs])])

    @contextlib.contextmanager
    def plain_open(path, key, params):   # 복호화 대신 평문 SQLite 를 그대로 연다
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    monkeypatch.setattr(A, "open_decrypted", plain_open)
    return acc


class FakeKeys:
    name = "fake"

    def __init__(self, missing=()):
        self.missing = set(missing)
        self.scanned: list[list[str]] = []

    def key_for(self, rel):
        return None if rel in self.missing else (b"k" * 32, A.SQLCIPHER4_DEFAULTS)

    def scan(self, rels, deep=False, pids=None):
        self.scanned.append(list(rels))
        return 0

    def forget(self, rel):
        pass


def _acct(fake_acc, **kw):
    return A.Account(acc=str(fake_acc), keys=FakeKeys(**kw), log=lambda *a: None)


# ───────────────────────── account ─────────────────────────
def test_rooms_names_and_me(fake_acc):
    a = _acct(fake_acc)
    assert a.my_id == ME
    assert [r["id"] for r in a.rooms] == ["11", "22", "33"]          # 최근순
    names = {r["id"]: r["name"] for r in a.rooms}
    assert names == {"11": "홍길동", "22": "스터디방", "33": "나와의 채팅"}
    assert a.memo_room()["id"] == "33"
    assert [r["id"] for r in a.find_rooms("스터디")] == ["22"]


def test_messages_marks_and_mine(fake_acc):
    a = _acct(fake_acc)
    m = a.messages(a.find_rooms("홍길동")[0], limit=10)
    assert [x["text"] for x in m] == ["안녕", "응 안녕", "[사진]"]
    assert [x["mine"] for x in m] == [False, True, False]
    assert m[1]["sender"] == "나"
    f = a.messages(a.find_rooms("스터디방")[0])
    assert f[-1]["text"] == "[파일: a.pdf]"
    assert len(a.raw_messages("11", since_logid=1)) == 2


def test_missing_key_room_is_unreadable_not_crash(fake_acc):
    a = _acct(fake_acc, missing={"chat_data\\chatLogs_22.edb"})
    assert a.readable("11") and not a.readable("22")
    assert a.messages(a.find_rooms("스터디방")[0]) == []


def test_find_account_dir(fake_acc):
    assert A.find_account_dir(str(fake_acc.parent)) == str(fake_acc)


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI 는 Windows 전용")
def test_memory_keys_cache_roundtrip(tmp_path):
    cache = tmp_path / "keys.dpapi"
    mk = A.MemoryKeys(str(tmp_path), log=lambda *a: None, cache_path=cache)
    mk.keys["x.edb"] = (bytes(range(32)), A.SQLCIPHER4_DEFAULTS)
    mk._save()
    assert cache.exists() and bytes(range(32)) not in cache.read_bytes()   # 평문 키가 파일에 없다
    again = A.MemoryKeys(str(tmp_path), log=lambda *a: None, cache_path=cache)
    assert again.key_for("x.edb") == (bytes(range(32)), A.SQLCIPHER4_DEFAULTS)


@pytest.mark.skipif(sys.platform != "win32", reason="memkey 는 Windows 전용")
def test_memory_keys_scan_matches_synthetic_db(tmp_path, monkeypatch):
    """가짜 '메모리'에 hex 키 문자열을 두고, 그 키로 암호화한 합성 DB 의 키를 찾아내는지."""
    from test_formats import encrypt_fixture, synthetic_sqlite
    from kakao_native import memkey
    p = A.SQLCIPHER4_DEFAULTS
    key = bytes(range(1, 33))
    rel = "chat_data\\chatLogs_9.edb"
    (tmp_path / "chat_data").mkdir()
    (tmp_path / rel).write_bytes(encrypt_fixture(synthetic_sqlite(4096, p.reserve), key, p))
    blob = b"zz\x00" * 100 + key.hex().encode() + b"\x00zz" * 50   # 앞뒤가 16진수 글자가 아니어야 64자 후보로 잡힌다
    monkeypatch.setattr(memkey, "_open_process_readonly", lambda pid: 1)
    monkeypatch.setattr(memkey, "_iter_regions", lambda h: [(0, len(blob))])
    monkeypatch.setattr(memkey, "_read_region", lambda h, b, s: blob)
    monkeypatch.setattr(memkey.kernel32, "CloseHandle", lambda h: True, raising=False)
    mk = A.MemoryKeys(str(tmp_path), log=lambda *a: None, cache_path=tmp_path / "k.dpapi")
    assert mk.scan([rel], pids=[4242]) == 1
    assert mk.key_for(rel)[0] == key


@pytest.mark.skipif(sys.platform != "win32", reason="memkey 는 Windows 전용")
def test_memory_keys_raw_scan_with_prefilter(tmp_path, monkeypatch):
    """최신 PC 카톡처럼 hex 문자열 없이 raw 32바이트 키만 메모리에 있을 때(8바이트 정렬) 찾아내는지,
    그리고 사전검사가 틀린 키를 거르는지."""
    from test_formats import encrypt_fixture, synthetic_sqlite
    from kakao_native import memkey
    p = A.SQLCIPHER4_DEFAULTS
    k1, k2 = bytes(range(1, 33)), bytes(range(101, 133))
    rels = ["chat_data\\chatListInfo.edb", "TalkUserDB.edb"]
    (tmp_path / "chat_data").mkdir()
    for rel, k in zip(rels, (k1, k2)):
        (tmp_path / rel).write_bytes(encrypt_fixture(synthetic_sqlite(4096, p.reserve), k, p))
    pre = A._HeaderPrefilter({r: (tmp_path / r).read_bytes()[:4096] for r in rels})
    assert pre.hit(k1) and pre.hit(k2) and not pre.hit(bytes(range(50, 82)))
    blob = b"\x00" * 64 + k1 + b"\x00" * 40 + k2 + b"\x00" * 64           # 64·136: 8바이트 정렬
    monkeypatch.setattr(memkey, "_open_process_readonly", lambda pid: 1)
    monkeypatch.setattr(memkey, "_iter_regions", lambda h: [(0, len(blob))])
    monkeypatch.setattr(memkey, "_read_region", lambda h, b, s: blob)
    monkeypatch.setattr(memkey.kernel32, "CloseHandle", lambda h: True, raising=False)
    mk = A.MemoryKeys(str(tmp_path), log=lambda *a: None, cache_path=tmp_path / "k.dpapi")
    assert mk.scan(rels, pids=[4242]) == 2
    assert mk.key_for(rels[0])[0] == k1 and mk.key_for(rels[1])[0] == k2


def test_make_keys_v2_missing_module_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv("KAKAO_AUTOREPLY_HOME", str(tmp_path / "home"))
    import builtins
    real = builtins.__import__

    def no_v2(name, *a, **k):
        if name.endswith("keystore_v2") or (a and a[2] and "keystore_v2" in (a[2] or ())):
            raise ImportError("no v2")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_v2)
    k = A.make_keys(str(tmp_path), "auto", log=lambda *a: None)
    assert k.name == "memory"
    with pytest.raises(RuntimeError):
        A.make_keys(str(tmp_path), "v2", log=lambda *a: None)


# ───────────────────────── iris_native ─────────────────────────
def test_shim_query_poll_and_send_lock(fake_acc, tmp_path, monkeypatch):
    import iris_native as IN
    a = _acct(fake_acc)
    ni = IN.NativeIris(a, verbose=lambda *x: None)
    assert ni.query("SELECT COUNT(*) AS n FROM chat_logs")[0]["n"] == 6
    assert {r["type"] for r in ni.query("SELECT type FROM chat_rooms")} == {"DirectChat", "MultiChat", "MemoChat"}
    with pytest.raises(ValueError):
        ni.query("DELETE FROM chat_logs")
    assert ni.poll() == []                                    # 처음 적재분은 이벤트가 아니다
    c = sqlite3.connect(fake_acc / "chat_data" / "chatLogs_11.edb")
    c.execute("INSERT INTO chatLogs VALUES(4, ?, 1, '새 글', '', ?, 0, 0)", (FRIEND, NOW + 99))
    c.commit(); c.close()
    ev = ni.poll()
    assert len(ev) == 1 and ev[0]["msg"] == "새 글" and ev[0]["json"]["chat_id"] == "11" and ev[0]["sender"] == "홍길동"

    sent = []
    fake_send = lambda title, text: sent.append((title, text)) or {"ok": True, "confirmed": True}  # noqa: E731
    monkeypatch.setattr(IN, "CONFIG", tmp_path / "missing.json")          # 설정 없음 → 잠금
    assert ni.reply({"room": "11", "data": "x"}, sender=fake_send)["success"] is False
    assert ni.reply({"room": "33", "data": "메모"}, sender=fake_send)["success"] is True
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"native_send_only_memo": False, "native_send_rooms_only": ["22"]}), encoding="utf-8")
    monkeypatch.setattr(IN, "CONFIG", cfg)
    assert ni.reply({"room": "11", "data": "x"}, sender=fake_send)["success"] is False   # 허용 방 아님
    assert ni.reply({"room": "22", "data": "y"}, sender=fake_send)["success"] is True
    assert sent == [("나와의 채팅", "메모"), ("스터디방", "y")]


def test_send_allowed_rules():
    import iris_native as IN
    assert IN.send_allowed({}, "1", "DirectChat")[0] is False                 # 기본값은 잠금
    assert IN.send_allowed({"native_send_only_memo": False}, "1", "DirectChat")[0] is True
    assert IN.send_allowed({"native_send_only_memo": False}, "1", "PlusChat")[0] is False


# ───────────────────────── kakaowin ─────────────────────────
def test_cli_parse_and_since():
    import kakaowin as K
    p = K.build_parser()
    a = p.parse_args(["send", "방", "글"])
    assert a.room == "방" and a.text == "글" and a.yes is False                 # 기본은 미리보기
    assert p.parse_args(["messages", "--chat", "x", "--since", "7d"]).since == "7d"
    assert abs(K.parse_since("1h") - (__import__("time").time() - 3600)) < 5
    assert K.parse_since(None) == 0
    with pytest.raises(SystemExit):
        K.parse_since("어제")
    assert K.send_lock_on(None) is True and K.send_lock_on({"native_send_only_memo": False}) is False


def test_cli_send_refuses_without_yes_and_under_lock(fake_acc, monkeypatch, capsys):
    import kakaowin as K
    import types
    calls = []
    fake_pk = types.SimpleNamespace(send=lambda t, b, send=False, auto_open=False: calls.append((t, b, send)) or
                                    {"ok": True, "steps": ["s1"], "confirmed": True})
    monkeypatch.setitem(sys.modules, "pc_kakao_send", fake_pk)
    monkeypatch.setattr(K, "_account", lambda args, scan=True: _acct(fake_acc))
    monkeypatch.setattr(K, "load_cfg", lambda: None)                          # 설정 없음 → 잠금
    assert K.main(["send", "스터디방", "안녕하세요"]) == 0                       # 미리보기
    assert calls == [("스터디방", "안녕하세요", False)]
    assert K.main(["send", "스터디방", "안녕하세요", "--yes"]) == 1              # 잠금으로 거부
    assert len(calls) == 1
    assert "전송 잠금" in capsys.readouterr().out
    assert K.main(["send", "x" * 10, "y" * 501]) == 1                         # 길이 제한
