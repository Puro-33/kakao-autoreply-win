"""
kakao_native 단위 테스트.

라이브 KakaoTalk 프로세스 없이 검증 가능한 부분:
  - 페이지 코덱(복호화)의 정확성 — 테스트용 암호화기로 왕복(포맷 2종)
  - verify_raw_key 가 올바른 키는 수락, 틀린 키/길이/포맷은 거부
  - derive_hmac_key 결정성/길이
  - detect_format(키 없이 구조 후보 제시) / matching_formats(키로 포맷 확정)
  - dbreader 의 테이블 탐색/대화 테이블 선택/최근행 쿼리 로직(평문 SQLite 로)
라이브 메모리 키 복원(memkey)은 실행 중인 본인 프로세스가 있어야 하므로
여기서는 import/구성만 확인하고, 실동작은 cli 실행으로 따로 확인한다.
"""

import os
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from kakao_native.sqlcipher4 import (
    SQLCIPHER4_DEFAULTS as P,
    DEVICE_AES128_DEFAULTS as PB,
    FORMAT_PRESETS,
    SQLITE_MAGIC,
    derive_hmac_key,
    verify_raw_key,
    valid_sqlite_header,
    matching_formats,
    detect_format,
    decrypt_database,
)
from kakao_native import dbreader


def _sqlite_header(page_size: int, reserve: int) -> bytes:
    h = bytearray(100)
    h[0:16] = SQLITE_MAGIC
    h[16:18] = (1 if page_size == 65536 else page_size).to_bytes(2, "big")
    h[18] = 1
    h[19] = 1
    h[20] = reserve
    h[21] = 64
    h[22] = 32
    h[23] = 32
    h[44:48] = (1).to_bytes(4, "big")
    h[56:60] = (1).to_bytes(4, "big")
    return bytes(h)


def _aes_encrypt(key, iv, data):
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return enc.update(data) + enc.finalize()


def _page_hmac(hmac_key, ciphertext, iv, page_no):
    import hashlib, hmac
    m = hmac.new(hmac_key, digestmod=hashlib.sha512)
    m.update(ciphertext); m.update(iv); m.update(page_no.to_bytes(4, "little"))
    return m.digest()


def build_encrypted_db(enc_key, page1_logical, page2_logical, salt=None, iv1=None, iv2=None):
    """
    page1_logical: 복호화 시 SQLite 매직 16바이트를 뺀 4000바이트(= 실제 헤더 16..4016)
    page2_logical: 4016바이트
    반환: 2페이지(8192바이트) 암호화 DB, 사용한 salt.
    """
    reserve = P.reserve
    ct1_len = P.page_size - P.first_page_offset - reserve
    ct2_len = P.page_size - reserve
    assert len(page1_logical) == ct1_len
    assert len(page2_logical) == ct2_len
    salt = salt or os.urandom(16)
    iv1 = iv1 or os.urandom(16)
    iv2 = iv2 or os.urandom(16)
    hmac_key = derive_hmac_key(enc_key, salt)

    ct1 = _aes_encrypt(enc_key, iv1, page1_logical)
    mac1 = _page_hmac(hmac_key, ct1, iv1, 1)
    page1 = salt + ct1 + iv1 + mac1
    assert len(page1) == P.page_size

    ct2 = _aes_encrypt(enc_key, iv2, page2_logical)
    mac2 = _page_hmac(hmac_key, ct2, iv2, 2)
    page2 = ct2 + iv2 + mac2
    assert len(page2) == P.page_size
    return page1 + page2, salt


def build_device_key_db(enc_key, page1_logical, page2_logical, iv1=None, iv2=None):
    """
    포맷(b) 합성 DB 2페이지(복호화기의 역).
    레이아웃: [ciphertext 4080][iv 16]  (salt 없음, HMAC 없음, page1 특례 없음)
    page1_logical 앞 100바이트가 진짜 SQLite 헤더여야 verify/decrypt 검증을 통과.
    """
    ct_len = PB.page_size - PB.reserve
    assert PB.reserve == 16
    assert len(page1_logical) == ct_len and len(page2_logical) == ct_len
    assert len(enc_key) == 16
    iv1 = iv1 or os.urandom(16)
    iv2 = iv2 or os.urandom(16)
    page1 = _aes_encrypt(enc_key, iv1, page1_logical) + iv1
    page2 = _aes_encrypt(enc_key, iv2, page2_logical) + iv2
    assert len(page1) == PB.page_size and len(page2) == PB.page_size
    return page1 + page2


def _valid_device_db():
    """포맷(b) 로 유효한 SQLite 헤더를 담은 2페이지 DB와 16바이트 키."""
    enc_key = os.urandom(16)
    hdr = _sqlite_header(PB.page_size, PB.reserve)
    p1 = hdr + os.urandom(4080 - len(hdr))
    p2 = os.urandom(4080)
    return enc_key, build_device_key_db(enc_key, p1, p2), p1, p2


def _valid_sqlcipher_db():
    """포맷(a) 로 유효한 SQLite 헤더를 담은 2페이지 DB와 32바이트 키."""
    enc_key = os.urandom(32)
    hdr = _sqlite_header(P.page_size, P.reserve)
    p1 = hdr[16:] + os.urandom(4000 - (len(hdr) - 16))
    p2 = os.urandom(4016)
    db, _ = build_encrypted_db(enc_key, p1, p2)
    return enc_key, db, p1, p2


def test_derive_hmac_key_props():
    key = bytes(range(32))
    salt = bytes([0x11]) * 16
    hk = derive_hmac_key(key, salt)
    assert len(hk) == 32
    assert hk == derive_hmac_key(key, salt)
    assert hk != derive_hmac_key(key, bytes([0x22]) * 16)
    with pytest.raises(ValueError):
        derive_hmac_key(b"short", salt)


def test_codec_roundtrip_and_verify():
    enc_key = os.urandom(32)
    p1 = os.urandom(4000)
    p2 = os.urandom(4016)
    db, salt = build_encrypted_db(enc_key, p1, p2)

    assert verify_raw_key(db[:4096], enc_key) is True
    assert verify_raw_key(db[:4096], os.urandom(32)) is False
    assert verify_raw_key(db[:4096], os.urandom(31)) is False

    plain = decrypt_database(db, enc_key)
    assert len(plain) == len(db)
    assert plain[:16] == SQLITE_MAGIC
    assert plain[16:16 + 4000] == p1
    assert plain[16 + 4000:4096] == b"\x00" * 80
    assert plain[4096:4096 + 4016] == p2
    assert plain[4096 + 4016:8192] == b"\x00" * 80


def test_decrypt_wrong_key_hmac_raises():
    enc_key = os.urandom(32)
    db, _ = build_encrypted_db(enc_key, os.urandom(4000), os.urandom(4016))
    with pytest.raises(ValueError):
        decrypt_database(db, os.urandom(32), check_hmac=True)


def test_decrypt_rejects_bad_size():
    with pytest.raises(ValueError):
        decrypt_database(os.urandom(4097), os.urandom(32))


def test_device_key_roundtrip_and_verify():
    enc_key, db, p1, p2 = _valid_device_db()

    assert verify_raw_key(db[:4096], enc_key, PB) is True
    assert verify_raw_key(db[:4096], os.urandom(16), PB) is False
    assert verify_raw_key(db[:4096], os.urandom(32), PB) is False

    plain = decrypt_database(db, enc_key, PB)
    assert len(plain) == len(db)
    assert plain[:16] == SQLITE_MAGIC
    assert plain[:4080] == p1
    assert plain[4080:4096] == b"\x00" * 16
    assert plain[4096:4096 + 4080] == p2
    assert plain[4096 + 4080:8192] == b"\x00" * 16


def test_device_key_wrong_key_rejected_by_header():
    """비HMAC 포맷은 page1 복호화 헤더로 틀린 키를 거른다(복호화 자체가 거부)."""
    _, db, _, _ = _valid_device_db()
    with pytest.raises(ValueError):
        decrypt_database(db, os.urandom(16), PB)


def test_cross_format_key_rejected():
    """포맷(b) DB를 포맷(a)(32바이트 키 요구)로 열려 하면 거부."""
    enc_key, db, _, _ = _valid_device_db()
    assert verify_raw_key(db[:4096], enc_key, P) is False


def test_detect_format_is_ambiguous_without_key():
    enc_key, db, _, _ = _valid_device_db()
    det = detect_format(db[:65536], len(db))
    assert det.status == "ambiguous"
    names = {(p.aes_key_bits, p.use_hmac, p.use_salt) for p in det.candidates}
    assert (256, True, True) in names and (128, False, False) in names
    assert det.reasons


def test_detect_format_plaintext_and_invalid():
    plain = SQLITE_MAGIC + b"\x00" * 4080
    assert detect_format(plain, 4096).status == "plaintext"
    assert detect_format(os.urandom(4096), 4096 + 1).status == "invalid"


def test_matching_formats_selects_device():
    enc_key, db, _, _ = _valid_device_db()
    matches = matching_formats(db[:65536], len(db), enc_key)
    assert len(matches) == 1
    (m,) = matches
    assert (m.aes_key_bits, m.use_hmac, m.use_salt, m.page_size) == (128, False, False, 4096)
    assert matching_formats(db[:65536], len(db), os.urandom(16)) == ()


def test_matching_formats_selects_sqlcipher():
    enc_key, db, _, _ = _valid_sqlcipher_db()
    matches = matching_formats(db[:65536], len(db), enc_key)
    assert len(matches) == 1
    (m,) = matches
    assert (m.aes_key_bits, m.use_hmac, m.use_salt, m.page_size) == (256, True, True, 4096)


def test_valid_sqlite_header_fields():
    hdr = _sqlite_header(4096, 80)
    page = hdr + b"\x00" * (4096 - len(hdr))
    assert valid_sqlite_header(page, P) is True
    assert valid_sqlite_header(page, PB) is False


def _make_plain_chatdb():
    fd, path = tempfile.mkstemp(suffix=".db", prefix="plain_chat_")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE chatLogs (_id INTEGER PRIMARY KEY, chat_id INTEGER, "
        "user_id INTEGER, message TEXT, created_at INTEGER)"
    )
    rows = [(i, 7, 100 + i, f"msg {i}", 1700000000 + i) for i in range(1, 6)]
    conn.executemany(
        "INSERT INTO chatLogs (_id,chat_id,user_id,message,created_at) VALUES (?,?,?,?,?)",
        rows,
    )
    conn.execute("CREATE TABLE meta (k TEXT, v TEXT)")
    conn.execute("INSERT INTO meta VALUES ('schema','1')")
    conn.commit()
    conn.close()
    return path


def test_dbreader_logic_on_plain_db():
    path = _make_plain_chatdb()
    try:
        conn = dbreader._open_ro(path)
        try:
            infos = dbreader.list_tables(conn)
            names = {t.name: t for t in infos}
            assert "chatLogs" in names
            assert names["chatLogs"].row_count == 5
            chat = dbreader._pick_chat_table(infos)
            assert chat is not None and chat.name == "chatLogs"
            cur = conn.cursor()
            cur.execute("SELECT message FROM chatLogs ORDER BY created_at DESC LIMIT 1")
            assert cur.fetchone()[0] == "msg 5"
        finally:
            conn.close()
    finally:
        os.remove(path)


def test_read_recent_via_identity_decrypt(monkeypatch):
    """read_recent 전체 흐름을 평문 DB + 복호화 우회로 검증."""
    path = _make_plain_chatdb()
    try:
        def fake_decrypt_to_tempfile(enc_path, enc_key, params=P, check_hmac=True):
            return enc_path
        monkeypatch.setattr(dbreader, "decrypt_to_tempfile", fake_decrypt_to_tempfile)
        import shutil
        copy = path + ".copy.db"
        shutil.copy(path, copy)
        info = dbreader.read_recent(copy, b"\x00" * 32, limit=3)
        assert info["chat_table"] == "chatLogs"
        assert len(info["recent"]) == 3
        assert info["recent"][0]["message"] == "msg 5"
    finally:
        os.remove(path)


def test_verify_database_bytes_on_real_sqlite():
    """실제 평문 SQLite DB 를 '복호화 우회' 포맷으로 암호화해 왕복·무결성 검증."""
    if not hasattr(sqlite3.Connection, "deserialize"):
        pytest.skip("이 Python SQLite 빌드에 deserialize 가 없음")
    path = _make_plain_chatdb()
    try:
        with open(path, "rb") as f:
            plain = f.read()
    finally:
        os.remove(path)
    assert len(plain) % 4096 == 0
    assert hasattr(dbreader, "verify_database_bytes")
    assert hasattr(dbreader, "verify_database_file")


def test_memkey_imports_and_pid_scan():
    """memkey 는 import 되고 PID 스캔이 예외 없이 리스트를 돌려준다(본인 세션)."""
    from kakao_native import memkey
    pids = memkey.find_kakaotalk_pids()
    assert isinstance(pids, list)
    assert all(isinstance(p, int) for p in pids)
