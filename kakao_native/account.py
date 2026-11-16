"""PC 카카오톡 '내 계정' 로컬 DB를 직접 읽는다: 방 목록·사용자 이름·메시지.

키는 두 가지 방법 중 하나로 구한다(KeyProvider).
  1) MemoryKeys (기본): 실행 중인 내 KakaoTalk.exe 메모리를 한 번 훑어 나온 키 후보를
     각 DB 첫 페이지에 맞춰 본다(memkey). 카톡이 최근에 연 DB만 풀린다. 찾은 키는
     DPAPI 로 묶어 %LOCALAPPDATA%\\kakao-autoreply-win\\keys.dpapi 에 저장해 다음부터 재사용한다.
  2) V2Keys (선택): kakao_native/keystore_v2.py 가 있으면 appstate.dat 키 저장소로 전 방 키를
     한 번에 유도한다. 이 파일은 저장소에 들어 있지 않다(README '라이선스·크레딧' 참고).

범위: 이 PC에 로그인한 본인 계정의 본인 DB만. 원본 DB는 읽기만 하고, 메시지 본문·키를 출력하지 않는다.
의존성: 표준 라이브러리 + cryptography.
"""
from __future__ import annotations

import contextlib
import dataclasses
import datetime as dt
import glob
import json
import os
import re
import sqlite3
import tempfile
import time
from pathlib import Path

from .sqlcipher4 import SQLCIPHER4_DEFAULTS, SqlCipher4Params, apply_wal, decrypt_database

USER_ROOT = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Kakao", "KakaoTalk", "users")
KST = dt.timezone(dt.timedelta(hours=9))
TYPE_KO = {"DirectChat": "갠톡", "MultiChat": "단톡", "OM": "오픈채팅", "OD": "1:1 오픈채팅",
           "MemoChat": "나와의 채팅", "PlusChat": "채널"}
MARK = {2: "[사진]", 27: "[사진 묶음]", 3: "[동영상]", 5: "[음성]", 12: "[이모티콘]", 20: "[이모티콘]", 25: "[이모티콘]",
        16: "[위치]", 17: "[연락처]", 18: "[파일]", 51: "[통화]", 14: "[투표]", 24: "[공지]", 73: "[알림]"}
ROOMLIST_DB = "chat_data\\chatListInfo.edb"
USER_DB = "TalkUserDB.edb"


def app_home() -> Path:
    """키 캐시 등 이 프로그램의 개인 파일을 두는 곳(저장소 밖). KAKAO_AUTOREPLY_HOME 으로 바꿀 수 있다."""
    base = os.environ.get("KAKAO_AUTOREPLY_HOME") or os.path.join(os.environ.get("LOCALAPPDATA", "."), "kakao-autoreply-win")
    p = Path(base)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _int(v, d: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return d


def ts(t: int, fmt: str = "%Y-%m-%d %H:%M") -> str:
    try:
        return dt.datetime.fromtimestamp(int(t), KST).strftime(fmt)
    except (ValueError, OSError, OverflowError):
        return "?"


def find_account_dir(user_root: str = USER_ROOT) -> str | None:
    """계정 폴더(users\\<해시>). appstate.dat 또는 chat_data\\chatListInfo.edb 가 있는 첫 폴더."""
    for pat in (os.path.join(user_root, "*", "appstate.dat"), os.path.join(user_root, "*", "chat_data", "chatListInfo.edb")):
        hits = sorted(glob.glob(pat), key=os.path.getmtime, reverse=True)
        if hits:
            return os.path.dirname(hits[0]) if pat.endswith("appstate.dat") else os.path.dirname(os.path.dirname(hits[0]))
    return None


def first_page(path: str, size: int = 4096) -> bytes:
    with open(path, "rb") as f:
        return f.read(size)


class _HeaderPrefilter:
    """32바이트 후보 키를 HMAC 검증 전에 싸게 거른다(SQLCipher 4, 4096B 페이지, reserve 80).

    1페이지 암호문의 첫 블록(오프셋 16)을 그 페이지의 IV 로 CBC 복호화하면 SQLite 헤더 16~31바이트가 나온다:
    페이지 크기 0x1000, (쓰기·읽기 버전), reserve, 0x40 0x20 0x20. 6바이트가 우연히 맞을 확률은 2^-48.
    DB 여러 개의 블록을 이어 붙여 후보마다 AES-ECB 한 번으로 모두 본다."""

    TARGET = {0: 0x10, 1: 0x00, 5: 0x40, 6: 0x20, 7: 0x20}

    def __init__(self, want: dict[str, bytes], params: SqlCipher4Params = SQLCIPHER4_DEFAULTS):
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        self._cipher = lambda k: Cipher(algorithms.AES(k), modes.ECB()).decryptor()
        ct_end = params.page_size - params.reserve
        target = dict(self.TARGET)
        target[4] = params.reserve
        blocks, self.expect = [], []
        for fp in want.values():
            if len(fp) < params.page_size:
                continue
            ct, iv = fp[16:32], fp[ct_end:ct_end + 16]
            blocks.append(ct)
            self.expect.append(tuple((j, iv[j] ^ t) for j, t in sorted(target.items())))
        self.blob = b"".join(blocks)
        self.n = len(want)

    def stale(self, want: dict) -> bool:
        return len(want) != self.n

    def hit(self, key: bytes) -> bool:
        if not self.blob:
            return False
        d = self._cipher(key).update(self.blob)
        for i, exp in enumerate(self.expect):
            o = 16 * i
            if all(d[o + j] == v for j, v in exp):
                return True
        return False


class MemoryKeys:
    """DB별 키를 실행 중인 KakaoTalk.exe 메모리에서 찾고 DPAPI 캐시에 둔다(기본)."""

    name = "memory"

    def __init__(self, acc: str, log=print, cache_path: Path | None = None):
        self.acc = acc
        self.log = log
        self.cache_path = cache_path or (app_home() / "keys.dpapi")
        self.keys: dict[str, tuple[bytes, SqlCipher4Params]] = {}
        self.hot: set[int] = set()
        self._load()

    def _load(self) -> None:
        from . import dpapi
        try:
            blob = self.cache_path.read_bytes()
        except OSError:
            return
        raw = dpapi.unprotect(blob)
        if not raw:
            self.log("키 캐시를 풀지 못했습니다(다른 PC·계정의 파일?). 새로 찾습니다.")
            return
        try:
            for rel, v in json.loads(raw.decode("utf-8")).items():
                self.keys[rel] = (bytes.fromhex(v["k"]), SqlCipher4Params(**v["p"]))
        except (ValueError, KeyError, TypeError):
            self.keys = {}

    def _save(self) -> None:
        from . import dpapi
        data = {rel: {"k": k.hex(), "p": dataclasses.asdict(p)} for rel, (k, p) in self.keys.items()}
        blob = dpapi.protect(json.dumps(data).encode("utf-8"))
        if blob:
            tmp = self.cache_path.with_suffix(".tmp")
            tmp.write_bytes(blob)
            os.replace(tmp, self.cache_path)

    def key_for(self, relpath: str):
        return self.keys.get(relpath)

    def forget(self, relpath: str) -> None:
        if self.keys.pop(relpath, None) is not None:
            self._save()

    def scan(self, relpaths: list[str], deep: bool = False, pids: list[int] | None = None) -> int:
        """메모리를 훑어 relpaths 중 키가 없는 DB들의 키를 찾는다. 새로 찾은 개수.

        1단계: 64자 hex 키 문자열(옛 형식). 2단계: 8바이트 정렬 32바이트 윈도(raw 키, 최신 PC 카톡).
        deep=True 면 3단계로 1바이트 간격 32/16바이트 윈도까지 본다(느림).
        raw 단계는 후보마다 각 DB 첫 블록 하나만 AES 로 풀어 SQLite 헤더 바이트를 보고(빠른 거르기),
        맞을 때만 HMAC 전체 검증을 한다."""
        from . import memkey
        want: dict[str, bytes] = {}
        for rel in relpaths:
            if rel in self.keys:
                continue
            try:
                want[rel] = first_page(os.path.join(self.acc, rel))
            except OSError:
                continue
        if not want:
            return 0
        pids = memkey.find_kakaotalk_pids() if pids is None else pids
        if not pids:
            raise RuntimeError("실행 중인 KakaoTalk.exe 가 없습니다. PC 카카오톡을 켜고 로그인하세요")
        t0 = time.time()
        found = 0

        def accept(key: bytes) -> bool:
            nonlocal found
            for rel, fp in list(want.items()):
                matched = memkey._matching_format(fp, key, SQLCIPHER4_DEFAULTS)
                if matched is not None:
                    self.keys[rel] = (key, matched)
                    del want[rel]
                    found += 1
            return not want

        passes = [("hex", None)]
        passes.append(("raw8", 8))
        if deep:
            passes.append(("raw1", 1))
        for label, step in passes:
            if not want:
                break
            seen: set[bytes] = set()
            pre = _HeaderPrefilter(want) if step else None
            for pid in pids:
                try:
                    h = memkey._open_process_readonly(pid)
                except OSError:
                    continue
                try:
                    regions = sorted(memkey._iter_regions(h), key=lambda r: r[0] not in self.hot)
                    for base, size in regions:
                        blob = memkey._read_region(h, base, size)
                        if not blob:
                            continue
                        before = found
                        if step is None:
                            for key in memkey._hex_candidates(blob):
                                if key not in seen:
                                    seen.add(key)
                                    if accept(key):
                                        break
                        else:
                            sizes = (32, 16) if step == 1 else (32,)
                            for key in memkey._raw_candidates(blob, set(), sizes=sizes, min_distinct=20, step=step):
                                if (len(key) != 32 or pre.hit(key)) and accept(key):
                                    break
                                if pre.stale(want):
                                    pre = _HeaderPrefilter(want)
                        if found > before:
                            self.hot.add(base)
                        if not want:
                            break
                finally:
                    memkey.kernel32.CloseHandle(h)
                if not want:
                    break
            self.log(f"  키 스캔 {label}: 누적 {found}개 찾음, 남은 {len(want)}개, {time.time() - t0:.1f}초")
        if found:
            self._save()
        return found


class V2Keys:
    """선택 모듈 keystore_v2.py 가 있을 때만 쓰는 일괄 키 유도(전 방)."""

    name = "v2"

    def __init__(self, acc: str, log=print):
        from . import keystore_v2 as kv
        from .memkey import find_kakaotalk_pids, find_v2_root_key
        appstate = os.path.join(acc, "appstate.dat")
        ks = kv.parse_appstate(appstate)
        cache = str(app_home() / "rootkey.dpapi")
        root = kv.load_root_key(cache, ks)
        if root is None:
            if not find_kakaotalk_pids():
                raise RuntimeError("실행 중인 KakaoTalk.exe 가 없습니다")
            log("루트 키를 메모리에서 찾는 중(최초 1회)...")
            hit = find_v2_root_key(ks)
            if hit is None:
                raise RuntimeError("루트 키를 찾지 못했습니다")
            root = hit[0]
            kv.save_root_key(root, cache)
        self.deks = {rel.decode("utf-8", "replace"): dek for rel, dek in kv.recover_all_deks(root, ks).items()}
        log(f"v2 키 저장소: {len(self.deks)}개 DB 키 유도")

    def key_for(self, relpath: str):
        dek = self.deks.get(relpath)
        return (dek, SQLCIPHER4_DEFAULTS) if dek else None

    def forget(self, relpath: str) -> None:
        pass

    def scan(self, relpaths, deep: bool = False, pids=None) -> int:
        return 0


def make_keys(acc: str, mode: str = "auto", log=print):
    """mode: auto(v2 모듈이 있으면 v2, 없으면 memory) | memory | v2"""
    if mode in ("auto", "v2"):
        try:
            return V2Keys(acc, log)
        except ImportError:
            if mode == "v2":
                raise RuntimeError("keystore_v2.py 가 없습니다(저장소에 포함되지 않음). --keys memory 를 쓰세요")
        except Exception as e:  # noqa: BLE001
            if mode == "v2":
                raise
            log(f"v2 키 저장소 실패({type(e).__name__}) → 메모리 키로 전환")
    return MemoryKeys(acc, log)


@contextlib.contextmanager
def open_decrypted(enc_path: str, key: bytes, params: SqlCipher4Params):
    """암호화 .edb 를 복호화해 읽기 전용 sqlite 연결로 연다. 최근 글이 있는 -wal 도 덮어쓴다.
    평문은 디스크에 쓰지 않고 메모리에서 연다(sqlite3 deserialize, Python 3.11+).
    예전에는 임시 .db 파일로 열었는데, 카카오톡 DB 가 WAL 방식이라 SQLite 가 그 옆에 -wal/-shm 을 만들고 우리는 .db 만 지워서
    새 메시지를 확인할 때마다 찌꺼기 파일이 두 개씩 남았다(실제로 임시 폴더에 150만 개가 쌓여 디스크가 가득 찼다)."""
    enc = open(enc_path, "rb").read()
    plain = decrypt_database(enc, key, params)
    try:
        wal = open(enc_path + "-wal", "rb").read()
    except OSError:
        wal = b""
    if len(wal) > 32:
        plain, _n = apply_wal(plain, enc, wal, key, params)
    conn, tmp = _connect_plain(plain)
    try:
        yield conn
    finally:
        conn.close()
        for p in ((tmp, tmp + "-wal", tmp + "-shm") if tmp else ()):
            try:
                os.unlink(p)
            except OSError:
                pass


def _connect_plain(plain: bytes) -> tuple[sqlite3.Connection, str]:
    """복호화한 SQLite 이미지 → (연결, 임시 파일 경로 또는 ""). 메모리에서 여는 것이 기본이고, 안 되면 임시 파일로 연다.
    메모리 DB 는 WAL 을 쓸 수 없어서 머리말의 읽기·쓰기 판(18·19번째 바이트)이 2(WAL)면 1로 바꿔 연다
    (WAL 의 커밋된 내용은 apply_wal 이 이미 본문에 덮어썼다)."""
    try:
        img = bytearray(plain)
        if len(img) > 20 and (img[18] == 2 or img[19] == 2):
            img[18] = img[19] = 1
        conn = sqlite3.connect(":memory:")
        try:
            conn.deserialize(bytes(img))
            conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except Exception:
            conn.close()
            raise
        conn.row_factory = sqlite3.Row
        return conn, ""
    except Exception:  # noqa: BLE001
        pass
    fd, tmp = tempfile.mkstemp(suffix=".db", prefix="kknat_")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(plain)
        conn = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
    except Exception:
        for p in (tmp, tmp + "-wal", tmp + "-shm"):
            try:
                os.unlink(p)
            except OSError:
                pass
        raise
    conn.row_factory = sqlite3.Row
    return conn, tmp


def rows(conn: sqlite3.Connection | None, sql: str, bind=()) -> list[sqlite3.Row]:
    if conn is None:
        return []
    try:
        return list(conn.execute(sql, bind))
    except sqlite3.DatabaseError:
        return []


class Account:
    """방 목록·사용자 이름·메시지 읽기. rooms: [{id,type,name,members,member_count,last}] 최근순."""

    def __init__(self, keys_mode: str = "auto", log=print, acc: str | None = None, keys=None, scan: bool = True):
        self.log = log
        self.acc = acc or find_account_dir()
        if not self.acc:
            raise RuntimeError("카카오톡 계정 폴더를 찾지 못했습니다(%LOCALAPPDATA%\\Kakao\\KakaoTalk\\users)")
        self.keys = keys or make_keys(self.acc, keys_mode, log)
        self.names: dict[str, str] = {}
        self.my_id = ""
        self.rooms: list[dict] = []
        if scan:
            self.keys.scan([ROOMLIST_DB, USER_DB])
        self._load_users()
        self._load_rooms()

    def chat_relpath(self, chat_id: str) -> str:
        return f"chat_data\\chatLogs_{chat_id}.edb"

    @contextlib.contextmanager
    def db(self, relpath: str):
        path = os.path.join(self.acc, relpath)
        got = self.keys.key_for(relpath)
        if got is None or not os.path.exists(path):
            yield None
            return
        key, params = got
        try:
            with open_decrypted(path, key, params) as conn:
                yield conn
        except Exception as e:  # noqa: BLE001
            self.log(f"[열기 실패] {os.path.basename(relpath)}: {type(e).__name__}")
            yield None

    def query(self, relpath: str, sql: str, bind=()) -> list[sqlite3.Row]:
        with self.db(relpath) as conn:
            return rows(conn, sql, bind)

    def scan_rooms(self, chat_ids: list[str] | None = None, deep: bool = False) -> int:
        """키가 없는 방 DB의 키를 메모리에서 찾는다(MemoryKeys). 카톡에서 그 방을 한 번 열어 두면 잘 찾는다.
        chat_ids 를 안 주면 채널(PlusChat)을 뺀 모든 방(봇은 채널에 답하지 않는다)."""
        ids = chat_ids if chat_ids is not None else [r["id"] for r in self.rooms if r["type"] != "PlusChat"]
        return self.keys.scan([self.chat_relpath(c) for c in ids], deep=deep)

    def readable(self, chat_id: str) -> bool:
        return self.keys.key_for(self.chat_relpath(chat_id)) is not None

    def _load_users(self) -> None:
        for r in self.query(USER_DB, "SELECT userId, nickName, friendNickName FROM talkUser"):
            nm = (r["friendNickName"] or r["nickName"] or "").strip()
            if nm:
                self.names[str(r["userId"])] = nm

    def _load_rooms(self) -> None:
        with self.db(ROOMLIST_DB) as conn:
            if conn is None:
                raise RuntimeError("방 목록 DB(chatListInfo.edb)를 열지 못했습니다. PC 카카오톡을 켜고 다시 시도하세요")
            members: dict[str, list[str]] = {}
            for r in rows(conn, "SELECT chatId, userId FROM chatMembers WHERE isActive=1"):
                members.setdefault(str(r["chatId"]), []).append(str(r["userId"]))
            freq: dict[str, int] = {}
            for ms in members.values():
                for u in ms:
                    freq[u] = freq.get(u, 0) + 1
            self.my_id = max(freq, key=freq.get) if freq else ""
            lst = rows(conn, "SELECT chatId, type, useCustomChatRoomTitle, chatRoomTitle, directChatMemberId, "
                             "activeMembersCount, lastUpdatedAt, lastLogId, linkId FROM chatRoomList")
        self.rooms = []
        for r in lst:
            cid, typ = str(r["chatId"]), str(r["type"] or "")
            ms = [m for m in members.get(cid, []) if m != self.my_id]
            self.rooms.append({
                "id": cid, "type": typ, "name": self.room_name(r, typ, ms), "title": (r["chatRoomTitle"] or "").strip(),
                "members": ms, "member_count": _int(r["activeMembersCount"], len(ms) + 1),
                "last": _int(r["lastUpdatedAt"]), "last_log_id": _int(r["lastLogId"]), "link_id": str(r["linkId"] or ""),
            })
        self.rooms.sort(key=lambda x: x["last"], reverse=True)

    def room_name(self, r, typ: str, members: list[str]) -> str:
        if typ == "MemoChat":
            return "나와의 채팅"
        title = (r["chatRoomTitle"] or "").strip()
        if _int(r["useCustomChatRoomTitle"]) and title:
            return title
        if typ in ("DirectChat", "PlusChat"):
            other = str(r["directChatMemberId"] or (members[0] if members else ""))
            return self.names.get(other) or title or f"{TYPE_KO.get(typ, typ)} {str(r['chatId'])[-4:]}"
        if title:
            return title
        known = [self.names.get(m) for m in members[:5] if self.names.get(m)]
        if known:
            return ", ".join(known[:4]) + (f" 외 {len(members) - 4}명" if len(members) > 4 else "")
        return f"{TYPE_KO.get(typ, typ or '방')} {str(r['chatId'])[-4:]}"

    def find_rooms(self, name: str) -> list[dict]:
        want = name.strip()
        exact = [r for r in self.rooms if r["name"].strip() == want or r["title"] == want]
        return exact or [r for r in self.rooms if want and want in r["name"]]

    def memo_room(self) -> dict | None:
        memo = [r for r in self.rooms if r["type"] == "MemoChat"]
        return memo[0] if len(memo) == 1 else None

    def raw_messages(self, chat_id: str, since_logid: int = 0, limit: int | None = None) -> list[sqlite3.Row]:
        sql = ("SELECT logId, authorId, type, message, attachement, sendAt, COALESCE(threadId,0) AS threadId, "
               "COALESCE(scope,0) AS scope FROM chatLogs WHERE logId > ? ORDER BY logId ASC")
        if limit:
            sql += f" LIMIT {int(limit)}"
        return self.query(self.chat_relpath(chat_id), sql, (since_logid,))

    def read_since(self, chat_id: str, since_logid: int = 0) -> list[sqlite3.Row] | None:
        """raw_messages 와 같되, DB 를 열지 못했으면 None 을 돌려준다(새 글이 없는 빈 목록과 구분: 다음에 다시 읽어야 한다)."""
        sql = ("SELECT logId, authorId, type, message, attachement, sendAt, COALESCE(threadId,0) AS threadId, "
               "COALESCE(scope,0) AS scope FROM chatLogs WHERE logId > ? ORDER BY logId ASC")
        with self.db(self.chat_relpath(chat_id)) as conn:
            if conn is None:
                return None
            try:
                return list(conn.execute(sql, (since_logid,)))
            except sqlite3.DatabaseError:
                return None

    def signature(self, chat_id: str) -> tuple:
        """방 DB 파일(.edb 와 -wal)의 (수정 시각, 크기). 둘 다 그대로면 새 글이 없다."""
        path = os.path.join(self.acc, self.chat_relpath(chat_id))
        out = []
        for p in (path, path + "-wal"):
            try:
                st = os.stat(p)
                out.append((st.st_mtime_ns, st.st_size))
            except OSError:
                out.append(None)
        return tuple(out)

    def messages(self, room: dict, limit: int = 50, since: int = 0) -> list[dict]:
        """최근 limit개(시각 오름차순): {log_id, at, mine, sender, text}."""
        got = self.query(self.chat_relpath(room["id"]),
                         "SELECT logId, authorId, type, message, attachement, sendAt FROM chatLogs "
                         "WHERE sendAt >= ? ORDER BY sendAt DESC, logId DESC LIMIT ?", (since, int(limit)))
        out = []
        for r in got:
            typ, msg = _int(r["type"], 1), (r["message"] or "").strip()
            if typ == 0 or typ >= 16384:
                continue
            if typ in (1, 26):
                body = msg
            elif typ in MARK:
                body = MARK[typ]
                if typ == 18:
                    try:
                        body = f"[파일: {json.loads(r['attachement'] or '{}').get('name') or ''}]"
                    except (ValueError, TypeError, AttributeError):
                        pass
                elif typ in (12, 20, 25) and msg:
                    body += " " + msg
            else:
                body = msg or f"[기타 {typ}]"
            if not body:
                continue
            uid = str(r["authorId"])
            mine = uid == self.my_id
            who = "나" if mine else (self.names.get(uid) or f"사용자{uid[-4:]}")
            out.append({"log_id": _int(r["logId"]), "at": _int(r["sendAt"]), "mine": mine,
                        "sender": re.sub(r"[\r\n]+", " ", who).strip(), "text": body})
        out.sort(key=lambda x: (x["at"], x["log_id"]))
        return out
