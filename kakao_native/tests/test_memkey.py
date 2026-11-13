"""Synthetic memory only; all Win32 access is replaced before memkey import."""
import ctypes
import importlib
from dataclasses import replace
from types import SimpleNamespace

import pytest

from test_formats import encrypt_fixture, synthetic_sqlite, PRESETS
from kakao_native.sqlcipher4 import SQLCIPHER4_DEFAULTS, DEVICE_AES128_DEFAULTS


class NoWin32:
    def __getattr__(self, name):
        raise AssertionError(f"Real process access is forbidden in tests: {name}")


@pytest.fixture
def scanner(monkeypatch):
    blocked = NoWin32()
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: blocked, raising=False)
    module = importlib.import_module("kakao_native.memkey")
    monkeypatch.setattr(module, "kernel32", blocked)
    return module


def fake_memory(monkeypatch, scanner, blobs):
    """Exercise the real recovery loop with a wholly fake process/region source."""
    opened, closed, reads = [], [], []
    monkeypatch.setattr(scanner, "find_kakaotalk_pids", lambda: [123])
    monkeypatch.setattr(scanner, "_open_process_readonly", lambda pid: opened.append(pid) or 456)
    monkeypatch.setattr(scanner, "_iter_regions", lambda h: enumerate(map(len, blobs)))
    def read(h, base, size):
        assert h == 456 and size == len(blobs[base])
        reads.append(base)
        return blobs[base]
    monkeypatch.setattr(scanner, "_read_region", read)
    monkeypatch.setattr(scanner, "kernel32", SimpleNamespace(CloseHandle=closed.append))
    return opened, closed, reads


@pytest.mark.parametrize("preset", PRESETS)
@pytest.mark.parametrize("offset", (0, 1, 15, 31))
@pytest.mark.parametrize("page_size", (4096, 8192))
def test_deep_roundtrip_fake_memory(scanner, monkeypatch, preset, offset, page_size):
    p = replace(preset, page_size=page_size)
    key = bytes(range(0x81, 0x81 + p.key_size))
    encrypted = encrypt_fixture(synthetic_sqlite(page_size, p.reserve), key, p)
    # Key ends exactly at the blob boundary, including blobs shorter than 32 bytes.
    blobs = [bytes(64), bytes(offset) + key]
    opened, closed, reads = fake_memory(monkeypatch, scanner, blobs)
    rec = scanner.recover_key_for_db(
        encrypted[:page_size], "synthetic.edb", deep_scan=True,
        params=replace(SQLCIPHER4_DEFAULTS, page_size=page_size),
    )
    assert (rec.enc_key, rec.format_name, rec.params) == (key, p.name, p)
    assert (rec.source_pid, rec.db_path) == (123, "synthetic.edb")
    assert opened == [123] and closed == [456]
    assert reads == [0, 1, 0, 1]  # hex pass first, raw pass second
    assert key.hex() not in repr(rec) and repr(key) not in repr(rec)


@pytest.mark.parametrize("preset", PRESETS)
def test_default_does_not_scan_raw(scanner, monkeypatch, preset):
    key = bytes(range(0x81, 0x81 + preset.key_size))
    encrypted = encrypt_fixture(synthetic_sqlite(4096, preset.reserve), key, preset)
    _, closed, reads = fake_memory(monkeypatch, scanner, [b"prefix" + key])
    assert scanner.recover_key_for_db(encrypted[:4096], "synthetic.edb") is None
    assert closed == [456] and reads == [0]


@pytest.mark.parametrize("deep", (False, True))
def test_hex_pass_precedes_raw_and_keeps_64_char_rule(scanner, monkeypatch, deep):
    key = bytes(range(32))
    encrypted = encrypt_fixture(synthetic_sqlite(4096, 80), key, SQLCIPHER4_DEFAULTS)
    _, closed, reads = fake_memory(monkeypatch, scanner, [b"!" + key.hex().upper().encode() + b"!"])
    def forbidden(*a, **kw):
        raise AssertionError("raw pass should not run after a hex hit")
    monkeypatch.setattr(scanner, "_scan_raw_windows", forbidden)
    rec = scanner.recover_key_for_db(
        encrypted[:4096], "synthetic.edb", deep_scan=deep,
        params=DEVICE_AES128_DEFAULTS,  # still tests the other preset
    )
    assert (rec.enc_key, rec.format_name) == (key, "SQLCIPHER4")
    assert closed == [456] and reads == [0]
    assert list(scanner._hex_candidates(b"!" + key[:16].hex().encode() + b"!")) == []


def test_raw_filter_and_overlapping_duplicate_windows(scanner):
    assert list(scanner._raw_candidates(bytes(80))) == []
    assert list(scanner._raw_candidates(bytes(15))) == []
    assert list(scanner._raw_candidates(b"x" * 64)) == [b"x" * 32, b"x" * 16]
    assert b"\0" * 4 + b"x" * 12 in list(scanner._raw_candidates(b"\0" * 4 + b"x" * 12))
    assert list(scanner._raw_candidates(b"\0" * 5 + b"x" * 11)) == []
    seen = set()
    first = list(scanner._raw_candidates(bytes(range(64)), seen))
    assert first and list(scanner._raw_candidates(bytes(range(64)), seen)) == []


def test_dedup_across_hex_raw_regions_and_pids(scanner, monkeypatch):
    key = b"x" * 32
    blob = b"!" + key.hex().encode() + b"!" + key * 2
    fake_memory(monkeypatch, scanner, [blob, blob])
    calls = []
    monkeypatch.setattr(scanner, "_matching_format", lambda page, key, p: calls.append(key))
    assert scanner.recover_key_for_db(bytes(4096), "fake", pids=[123, 123], deep_scan=True) is None
    assert calls and len(calls) == len(set(calls))
    assert calls.count(key) == 1


@pytest.mark.parametrize("preset", PRESETS)
def test_verifier_only_receives_matching_key_lengths(scanner, monkeypatch, preset):
    key = bytes(range(preset.key_size))
    calls = []
    def verify(page, candidate, p):
        assert len(candidate) == p.key_size
        calls.append(p)
        return True
    monkeypatch.setattr(scanner, "verify_raw_key", verify)
    assert scanner._matching_format(bytes(4096), key, SQLCIPHER4_DEFAULTS) == preset
    assert calls == [preset]


@pytest.mark.parametrize("preset", PRESETS)
def test_wrong_keys_and_corrupt_first_page_rejected(scanner, preset):
    key = bytes(range(0x81, 0x81 + preset.key_size))
    encrypted = encrypt_fixture(synthetic_sqlite(4096, preset.reserve), key, preset)
    assert scanner._scan_raw_windows(b"\xff" * 40, encrypted[:4096]) is None
    corrupt = bytearray(encrypted[:4096])
    corrupt[16 if preset.use_salt else 0] ^= 1
    assert scanner._scan_raw_windows(key, bytes(corrupt)) is None
    assert scanner._scan_raw_windows(key, encrypted[:4095]) is None


def test_hmac_acceptance_does_not_depend_on_synthesized_magic(scanner):
    key = bytes(range(32))
    encrypted = encrypt_fixture(bytes(4096), key, SQLCIPHER4_DEFAULTS)
    assert scanner._scan_raw_windows(key, encrypted) == (key, SQLCIPHER4_DEFAULTS)


def test_handle_closed_on_synthetic_read_failure(scanner, monkeypatch):
    _, closed, _ = fake_memory(monkeypatch, scanner, [b"fake"])
    def fail(*a):
        raise OSError("synthetic read failure")
    monkeypatch.setattr(scanner, "_read_region", fail)
    with pytest.raises(OSError, match="synthetic"):
        scanner.recover_key_for_db(bytes(4096), "fake", deep_scan=True)
    assert closed == [456]


@pytest.mark.parametrize("preset", PRESETS)
def test_cli_propagates_format_and_hides_all_row_values(scanner, monkeypatch, capsys, preset):
    from kakao_native import cli
    key = bytes(range(0x81, 0x81 + preset.key_size))
    p = replace(preset, page_size=8192)
    encrypted = encrypt_fixture(synthetic_sqlite(8192, p.reserve), key, p)
    fake_memory(monkeypatch, scanner, [b"prefix" + key])
    monkeypatch.setattr(cli, "find_chat_dbs", lambda: ["synthetic.edb"])
    monkeypatch.setattr(cli, "_first_page", lambda path, size: encrypted[:size])
    calls = []
    def read(path, found_key, params, limit):
        calls.append((found_key, params, limit))
        return {"tables": [("sample", 1)], "chat_table": "sample", "order_col": "id",
                "recent": [{"short": "SECRET", "long": "PRIVATE" * 12,
                            "raw": b"HIDDEN", "number": 123456789, "empty": None}]}
    monkeypatch.setattr(cli, "read_recent", read)
    assert cli.main(["--deep", "--page-size", "8192", "--limit", "1"]) == 0
    output = capsys.readouterr().out
    assert calls == [(key, p, 1)]
    assert f"format: {p.name}" in output
    assert "<str:6>" in output and "<bytes:6>" in output and "<int>" in output
    for private in ("SECRET", "PRIVATE", "HIDDEN", "123456789", key.hex(), repr(key)):
        assert private not in output
