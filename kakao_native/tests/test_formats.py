"""Synthetic tests only: no original EDB, real key, memkey import or process access."""
import builtins
import hashlib
import hmac
import importlib
import json
from pathlib import Path
import sqlite3
import sys
from dataclasses import replace

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from kakao_native.sqlcipher4 import (
    CipherParams, SQLCIPHER4_DEFAULTS, DEVICE_AES128_DEFAULTS, SQLITE_MAGIC,
    decrypt_database, derive_hmac_key, detect_format, matching_formats,
    valid_sqlite_header, verify_raw_key,
)
from kakao_native.dbreader import verify_database_bytes
from kakao_native.format_analysis import analyze_directory

PRESETS = (SQLCIPHER4_DEFAULTS, DEVICE_AES128_DEFAULTS)


def synthetic_sqlite(page_size, reserve):
    """Build a real two-page SQLite DB independently, with one table/one row."""
    usable = page_size - reserve
    first, second = bytearray(page_size), bytearray(page_size)
    first[:16] = SQLITE_MAGIC
    first[16:18] = (1 if page_size == 65536 else page_size).to_bytes(2, "big")
    first[18:24] = bytes((1, 1, reserve, 64, 32, 32))
    for off, value in ((24, 1), (28, 2), (40, 1), (44, 4), (56, 1),
                       (92, 1), (96, 3046000)):
        first[off:off + 4] = value.to_bytes(4, "big")
    sql = b"CREATE TABLE sample(value TEXT)"
    schema = bytes((6, 23, 25, 25, 1, 13 + 2 * len(sql)))
    schema += b"table" + b"sample" + b"sample" + b"\x02" + sql
    value = b"synthetic hello"
    record = bytes((2, 13 + 2 * len(value))) + value

    for page, offset, payload in ((first, 100, schema), (second, 0, record)):
        cell = bytes((len(payload), 1)) + payload
        start = usable - len(cell)
        page[offset] = 13
        page[offset + 3:offset + 5] = (1).to_bytes(2, "big")
        page[offset + 5:offset + 7] = start.to_bytes(2, "big")
        page[offset + 8:offset + 10] = start.to_bytes(2, "big")
        page[start:usable] = cell
    return bytes(first + second)


def encrypt_fixture(plain, key, p):
    """Independent test encoder; intentionally does NOT call production KDF/split."""
    salt = bytes(range(0xA0, 0xB0)) if p.use_salt else b""
    hk = hashlib.pbkdf2_hmac(
        "sha512", key, bytes(x ^ 0x3A for x in salt), 2, dklen=len(key)
    )
    result = bytearray()
    for i in range(len(plain) // p.page_size):
        number = i + 1
        page = plain[i * p.page_size:(i + 1) * p.page_size]
        offset = 16 if i == 0 and p.use_salt else 0
        iv = hashlib.sha256(b"synthetic iv" + number.to_bytes(4, "little")).digest()[:16]
        enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
        ciphertext = enc.update(page[offset:p.page_size - p.reserve]) + enc.finalize()
        tag = hmac.new(hk, ciphertext + iv + number.to_bytes(4, "little"),
                       hashlib.sha512).digest() if p.use_hmac else b""
        trailer = iv + tag
        result.extend((salt if offset else b"") + ciphertext + trailer
                      + bytes(p.reserve - len(trailer)))
    return bytes(result)


@pytest.mark.parametrize("p,size", [
    (p, size) for p in PRESETS for size in (512, 1024, 4096, 8192, 65536)
    if size - p.reserve >= 480
])
def test_sqlite_roundtrip_and_integrity(p, size):
    p = replace(p, page_size=size)
    key = bytes(range(p.key_size))
    plain = synthetic_sqlite(size, p.reserve)
    encrypted = encrypt_fixture(plain, key, p)
    assert encrypted[:16] != SQLITE_MAGIC
    assert decrypt_database(encrypted, key, p) == plain
    assert verify_raw_key(encrypted[:size], key, p)
    assert not verify_raw_key(encrypted[:size], bytes([91]) * p.key_size, p)
    assert not verify_raw_key(encrypted[:size], key + b"x", p)
    assert not verify_raw_key(encrypted[:size - 1], key, p)
    assert not verify_raw_key(encrypted[:size] + b"x", key, p)
    assert valid_sqlite_header(plain[:size], p)
    assert matching_formats(encrypted[:65536], len(encrypted), key) == (p,)
    report = verify_database_bytes(encrypted, key)
    assert report["integrity_check"] == "ok"
    assert report["params"]["page_size"] == size
    assert report["params"]["aes_key_bits"] == p.aes_key_bits
    conn = sqlite3.connect(":memory:")
    try:
        conn.deserialize(decrypt_database(encrypted, key, p))
        assert conn.execute("SELECT value FROM sample").fetchall() == [("synthetic hello",)]
    finally:
        conn.close()


@pytest.mark.parametrize("bits", (128, 256))
@pytest.mark.parametrize("use_salt", (True, False))
@pytest.mark.parametrize("use_hmac", (True, False))
def test_custom_parameter_combinations(bits, use_salt, use_hmac):
    p = CipherParams(aes_key_bits=bits, use_salt=use_salt, use_hmac=use_hmac)
    key = bytes(range(p.key_size))
    plain = synthetic_sqlite(p.page_size, p.reserve)
    enc = encrypt_fixture(plain, key, p)
    assert decrypt_database(enc, key, p) == plain
    assert verify_database_bytes(enc, key, p)["integrity_check"] == "ok"


@pytest.mark.parametrize("p", PRESETS)
def test_extra_reserve_padding(p):
    p = replace(p, reserve=p.reserve + 16)
    key = bytes(range(p.key_size))
    plain = synthetic_sqlite(p.page_size, p.reserve)
    enc = encrypt_fixture(plain, key, p)
    assert decrypt_database(enc, key, p) == plain
    assert verify_database_bytes(enc, key, p)["integrity_check"] == "ok"


def test_sqlcipher_kdf_cipher_key_size_not_digest_size():
    key, salt = bytes(range(32)), bytes(range(16))
    expected = hashlib.pbkdf2_hmac("sha512", key, bytes(x ^ 0x3A for x in salt), 2, 32)
    assert derive_hmac_key(key, salt) == expected
    assert len(derive_hmac_key(key, salt)) == 32
    with pytest.raises(ValueError):
        derive_hmac_key(key, b"short")


@pytest.mark.parametrize("position", (16, 4016, 4032, 4096 + 20, 8191))
def test_hmac_corruption_on_each_page_is_rejected(position):
    p = SQLCIPHER4_DEFAULTS
    key = bytes(range(32))
    enc = bytearray(encrypt_fixture(synthetic_sqlite(4096, 80), key, p))
    enc[position] ^= 1
    with pytest.raises(ValueError, match="HMAC"):
        decrypt_database(bytes(enc), key, p)


def test_hmac_binds_page_number():
    p = SQLCIPHER4_DEFAULTS
    key = bytes(range(32))
    plain = synthetic_sqlite(4096, 80) + bytes(4096)
    enc = encrypt_fixture(plain, key, p)
    reordered = enc[:4096] + enc[8192:] + enc[4096:8192]
    with pytest.raises(ValueError, match="HMAC"):
        decrypt_database(reordered, key, p)


@pytest.mark.parametrize("p", PRESETS)
def test_empty_partial_and_wrong_key_inputs(p):
    key = bytes(range(p.key_size))
    for enc in (b"", bytes(4095), bytes(4097)):
        with pytest.raises(ValueError):
            decrypt_database(enc, key, p)
    enc = encrypt_fixture(synthetic_sqlite(4096, p.reserve), key, p)
    with pytest.raises(ValueError):
        decrypt_database(enc, b"short", p)
    with pytest.raises(ValueError):
        decrypt_database(enc, bytes([251]) * p.key_size, p)


def test_no_hmac_checks_decrypted_magic_not_synthesized():
    p = DEVICE_AES128_DEFAULTS
    key = bytes(range(16))
    plain = bytearray(synthetic_sqlite(4096, 16))
    plain[:16] = bytes(16)
    enc = encrypt_fixture(bytes(plain), key, p)
    assert not verify_raw_key(enc[:4096], key, p)
    with pytest.raises(ValueError, match="header"):
        decrypt_database(enc, key, p)


def test_no_hmac_later_corruption_needs_sqlite_check():
    p = DEVICE_AES128_DEFAULTS
    key = bytes(range(16))
    enc = bytearray(encrypt_fixture(synthetic_sqlite(4096, 16), key, p))
    enc[-16] ^= 13 ^ 255
    assert verify_raw_key(bytes(enc[:4096]), key, p)
    with pytest.raises((ValueError, sqlite3.DatabaseError)):
        verify_database_bytes(bytes(enc), key, p)


@pytest.mark.parametrize("p", PRESETS)
def test_wal_header_only_normalized_in_memory(p):
    key = bytes(range(p.key_size))
    plain = bytearray(synthetic_sqlite(4096, p.reserve))
    plain[18:20] = b"\x02\x02"
    enc = encrypt_fixture(bytes(plain), key, p)
    assert verify_database_bytes(enc, key, p)["wal_header_normalized_in_memory"]
    assert decrypt_database(enc, key, p)[18:20] == b"\x02\x02"


def test_detection_is_honestly_ambiguous_without_key():
    for p in PRESETS:
        enc = encrypt_fixture(synthetic_sqlite(4096, p.reserve), bytes(range(p.key_size)), p)
        d = detect_format(enc[:4096], len(enc))
        assert d.status == "ambiguous"
        assert SQLCIPHER4_DEFAULTS in d.candidates
        assert DEVICE_AES128_DEFAULTS in d.candidates
        assert d.possible_page_sizes == (512, 1024, 2048, 4096, 8192)
    assert detect_format(b"", 0).status == "invalid"
    assert detect_format(bytes(4096), 4097).status == "invalid"
    assert detect_format(bytes(16), 4096).status == "insufficient"
    assert detect_format(SQLITE_MAGIC + bytes(4080), 4096).status == "plaintext"


@pytest.mark.parametrize("kwargs", (
    {"aes_key_bits": 192}, {"page_size": 3000}, {"page_size": 512}, {"reserve": 16},
    {"reserve": 81}, {"reserve": 256}, {"iv_size": 8}, {"salt_size": 0},
    {"hmac_size": 32}, {"fast_kdf_iter": 0}, {"hmac_salt_mask": 256},
))
def test_invalid_parameters(kwargs):
    with pytest.raises(ValueError):
        CipherParams(**kwargs)


def test_analyzer_synthetic_files_readonly(tmp_path):
    p = SQLCIPHER4_DEFAULTS
    enc = encrypt_fixture(synthetic_sqlite(4096, 80), bytes(range(32)), p)
    paths = [tmp_path / f"chatLogs_fake{i}.edb" for i in range(3)]
    for path in paths:
        path.write_bytes(enc)
    before = [(path.stat().st_mtime_ns, path.read_bytes()) for path in paths]
    result = analyze_directory(str(tmp_path))
    assert result["files_opened"] == 3
    assert result["size_bytes"]["gcd"] == 8192
    assert result["first16"]["unique_values"] == 1
    assert result["sampled_pages"] == 6
    assert result["divisible_file_counts"]["4096"] == 3
    assert result["tail16_possible_iv"]["samples"] == 6
    assert result["files_changed_during_read"] == 0
    assert result["visible_sqlite_magic_files"] == 0
    assert [(path.stat().st_mtime_ns, path.read_bytes()) for path in paths] == before
    assert "fake" not in json.dumps(result)
    assert enc[:16].hex() not in json.dumps(result)


def test_analyzer_does_not_hide_access_denial(monkeypatch):
    import kakao_native.format_analysis as module
    def denied(*a, **kw):
        raise PermissionError("synthetic denial")
    monkeypatch.setattr(module.os, "scandir", denied)
    with pytest.raises(PermissionError):
        analyze_directory("synthetic-only")


def test_safe_cli_paths_never_import_memkey(monkeypatch, capsys):
    original_import = builtins.__import__
    def guarded(name, *a, **kw):
        if "memkey" in name:
            raise AssertionError("memkey must not be imported/executed")
        return original_import(name, *a, **kw)
    monkeypatch.setattr(builtins, "__import__", guarded)
    cli = importlib.import_module("kakao_native.cli")
    monkeypatch.setattr(cli, "analyze_directory", lambda *a: {
        "files_opened": 3, "read_errors": {}, "files_changed_during_read": 0,
    })
    assert cli.main(["--analyze-dir", "synthetic-only"]) == 0
    monkeypatch.setattr(cli.getpass, "getpass", lambda *a: "00" * 16)
    calls = []
    def verify(path, key, params):
        calls.append((path, key, params))
        return {"integrity_check": "ok"}
    monkeypatch.setattr(cli, "verify_database_file", verify)
    assert cli.main(["--verify-key", "--db", "synthetic-only", "--format", "device-aes128"]) == 0
    assert calls[0] == ("synthetic-only", bytes(16), DEVICE_AES128_DEFAULTS)
    assert "00" * 16 not in capsys.readouterr().out
    monkeypatch.setattr(cli, "find_chat_dbs", lambda: [])
    assert cli.main(["--format", "device-aes128", "--list"]) == 2


@pytest.mark.parametrize("p", PRESETS)
def test_verify_file_is_rb_and_never_creates_plaintext(tmp_path, monkeypatch, p):
    import kakao_native.dbreader as reader
    key = bytes(range(p.key_size))
    encrypted = encrypt_fixture(synthetic_sqlite(4096, p.reserve), key, p)
    path = tmp_path / "synthetic.edb"
    path.write_bytes(encrypted)
    before = path.stat().st_mtime_ns
    original_open = builtins.open
    modes_used = []
    def guarded_open(filename, mode="r", *a, **kw):
        assert Path(filename) == path
        modes_used.append(mode)
        assert mode == "rb"
        return original_open(filename, mode, *a, **kw)
    def forbidden(*a, **kw):
        raise AssertionError("plaintext temporary file must never be created")
    monkeypatch.setattr(reader, "open", guarded_open, raising=False)
    monkeypatch.setattr(reader.tempfile, "mkstemp", forbidden)
    assert reader.verify_database_file(str(path), key)["integrity_check"] == "ok"
    assert modes_used == ["rb"]
    assert path.read_bytes() == encrypted
    assert path.stat().st_mtime_ns == before


def test_compatibility_aliases_and_raw_helper():
    from kakao_native.sqlcipher4 import (
        DEVICE_KEY_AES128, SQLITE_HEADER, SqlCipherParams, try_decrypt_database,
    )
    assert DEVICE_KEY_AES128 == DEVICE_AES128_DEFAULTS
    assert SQLITE_HEADER == SQLITE_MAGIC
    assert SqlCipherParams is CipherParams
    for p in PRESETS:
        key = bytes(range(p.key_size))
        plain = synthetic_sqlite(4096, p.reserve)
        enc = encrypt_fixture(plain, key, p)
        assert try_decrypt_database(enc, key) == (plain, p)
        assert try_decrypt_database(enc, bytes([231]) * p.key_size) == (None, None)
