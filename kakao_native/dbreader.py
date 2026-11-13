# -*- coding: utf-8 -*-
"""
복호화한 본인 카카오톡 로컬 DB(.edb)를 sqlite3 로 열어 최근 대화를 읽는다.
읽기 전용. 원본 .edb 는 건드리지 않는다.

주의: chatLogs_*.edb 의 message 컬럼은 SQLCipher 복호화와 '별개로'
카카오 앱 계층에서 한 번 더 암호화돼 있을 수 있다(버전별). 이 모듈은
DB 구조/행/타임스탬프를 읽어 파이프라인이 동작함을 확인하는 데 집중하고,
message 본문이 평문인지/추가 암호화인지는 있는 그대로 보고한다.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from dataclasses import dataclass

from .sqlcipher4 import decrypt_database, SQLCIPHER4_DEFAULTS, SqlCipher4Params


@dataclass
class TableInfo:
    name: str
    columns: list[str]
    row_count: int


def decrypt_to_tempfile(enc_path: str, enc_key: bytes,
                        params: SqlCipher4Params = SQLCIPHER4_DEFAULTS,
                        check_hmac: bool = True) -> str:
    """본인 소유 .edb 를 복호화해 임시 .db 파일 경로를 돌려준다(호출자가 삭제)."""
    with open(enc_path, "rb") as f:
        enc = f.read()
    plain = decrypt_database(enc, enc_key, params, check_hmac=check_hmac)
    fd, tmp = tempfile.mkstemp(suffix=".db", prefix="kakao_dec_")
    with os.fdopen(fd, "wb") as f:
        f.write(plain)
    return tmp


def _open_ro(path: str) -> sqlite3.Connection:
    uri = f"file:{path}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def list_tables(conn: sqlite3.Connection) -> list[TableInfo]:
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    names = [r[0] for r in cur.fetchall()]
    infos: list[TableInfo] = []
    for name in names:
        cur.execute(f"PRAGMA table_info('{name}')")
        cols = [r[1] for r in cur.fetchall()]
        try:
            cur.execute(f"SELECT COUNT(*) FROM '{name}'")
            cnt = cur.fetchone()[0]
        except sqlite3.DatabaseError:
            cnt = -1
        infos.append(TableInfo(name, cols, cnt))
    return infos


def _pick_chat_table(infos: list[TableInfo]) -> TableInfo | None:
    """대화 로그처럼 보이는 테이블(메시지/시각 컬럼 보유, 행 많은 것) 선택."""
    best = None
    for t in infos:
        lc = [c.lower() for c in t.columns]
        has_msg = any("message" in c or c == "v" or "text" in c for c in lc)
        # KakaoTalk PC chatLogs의 시각 칼럼은 'sendAt'(=sendat) — 'send'도 시각으로 인정한다
        has_time = any("created" in c or "sent" in c or "send" in c or "date" in c or "time" in c
                       for c in lc)
        if has_msg and has_time and t.row_count > 0:
            if best is None or t.row_count > best.row_count:
                best = t
    return best


def read_recent(enc_path: str, enc_key: bytes, limit: int = 5,
                params: SqlCipher4Params = SQLCIPHER4_DEFAULTS) -> dict:
    """
    본인 .edb 하나를 복호화해 테이블 목록과, 추정 대화 테이블의 최근 몇 행을
    돌려준다. 실제 메시지 전송은 하지 않는다.
    """
    tmp = decrypt_to_tempfile(enc_path, enc_key, params)
    try:
        conn = _open_ro(tmp)
        try:
            infos = list_tables(conn)
            chat = _pick_chat_table(infos)
            recent = []
            order_col = None
            if chat is not None:
                lc = {c.lower(): c for c in chat.columns}
                for key in ("sendat", "created_at", "sent_at", "date", "logid", "_id", "id"):
                    if key in lc:
                        order_col = lc[key]
                        break
                cur = conn.cursor()
                q = f"SELECT * FROM '{chat.name}'"
                if order_col:
                    q += f" ORDER BY {order_col} DESC"
                q += f" LIMIT {int(limit)}"
                cur.execute(q)
                cols = [d[0] for d in cur.description]
                for row in cur.fetchall():
                    recent.append(dict(zip(cols, row)))
            return {
                "file": os.path.basename(enc_path),
                "tables": [(t.name, t.row_count) for t in infos],
                "schema": {t.name: t.columns for t in infos},  # 칼럼 이름만(본문 없음) — 대화 테이블 매핑용
                "chat_table": chat.name if chat else None,
                "order_col": order_col,
                "recent": recent,
            }
        finally:
            conn.close()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def verify_database_bytes(encrypted: bytes, enc_key: bytes,
                          params: SqlCipher4Params | None = None) -> dict:
    """Verify a supplied raw key and SQLite integrity entirely in memory.

    No scan, temporary file, message query, original-file write or WAL replay.
    Returns structural metadata only. A no-HMAC file has no authenticity guarantee.
    """
    from dataclasses import asdict
    from .sqlcipher4 import matching_formats

    candidates = (params,) if params is not None else None
    matches = matching_formats(encrypted[:65536], len(encrypted), enc_key, candidates)
    if len(matches) != 1:
        raise ValueError(f"expected one key/header format match; found {len(matches)}")
    chosen = matches[0]
    plain = decrypt_database(encrypted, enc_key, chosen)
    if not hasattr(sqlite3.Connection, "deserialize"):
        raise RuntimeError("this Python SQLite build lacks in-memory deserialize")
    wal_header = plain[18:20] != b"\x01\x01"
    if wal_header:
        # A standalone deserialized DB cannot open a disk WAL. Normalize ONLY the
        # in-memory header for checking this main-file image, never the original.
        plain = plain[:18] + b"\x01\x01" + plain[20:]
    conn = sqlite3.connect(":memory:")
    try:
        conn.deserialize(plain)
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA trusted_schema=OFF")
        results = conn.execute("PRAGMA integrity_check").fetchall()
        if results != [("ok",)]:
            raise ValueError("SQLite integrity_check failed")
        return {
            "params": asdict(chosen),
            "pages": len(encrypted) // chosen.page_size,
            "integrity_check": "ok",
            "authenticated_pages": chosen.use_hmac,
            "main_file_only": True,
            "wal_header_normalized_in_memory": wal_header,
        }
    finally:
        conn.close()


def verify_database_file(enc_path: str, enc_key: bytes,
                         params: SqlCipher4Params | None = None) -> dict:
    """Open the original rb once, decrypt in RAM, and return no content or keys."""
    with open(enc_path, "rb") as f:
        before = os.fstat(f.fileno())
        encrypted = f.read()
        after = os.fstat(f.fileno())
    if ((before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
            or len(encrypted) != before.st_size):
        raise ValueError("database changed while reading; retry with a stable main file")
    return verify_database_bytes(encrypted, enc_key, params)
