"""
실행 중인 '본인' KakaoTalk.exe 프로세스 메모리에서 DB raw 키를 복원한다.
표준 라이브러리(ctypes)만 쓴다 — 추가 설치 불필요.

동작 원칙(개인 자동화·본인 대상 한정):
  - 이 PC 사용자 본인이 실행한 KakaoTalk.exe 만 연다. 이름이 정확히
    'KakaoTalk.exe' 인 프로세스가 아니면 건드리지 않는다.
  - 읽기 전용(PROCESS_VM_READ)으로만 연다. 메모리를 쓰거나 프로세스를
    변조하지 않는다.
  - MEM_COMMIT 이고 읽기 가능하며 MEM_PRIVATE 인 영역만 읽는다.
  - 찾은 후보는 반드시 '본인 소유 대상 DB' 의 첫 페이지 HMAC 또는
    복호화한 SQLite 헤더로 검증한다.
    검증을 통과한 바이트열만 그 DB의 키로 인정한다.
  - 키 바이트는 반환 객체 안에만 담고 콘솔/파일로 함부로 출력하지 않는다.

키는 카카오톡이 해당 대화방 화면을 메모리에 올려둔 동안에만 존재한다.
(최근 연 적 없는 방의 키는 메모리에 없을 수 있다 — 그 방을 한 번 열고 재시도.)
"""

from __future__ import annotations

import ctypes
import re
from ctypes import wintypes
from dataclasses import dataclass, field, replace
from functools import lru_cache

from .sqlcipher4 import (
    verify_raw_key, FORMAT_PRESETS, SQLCIPHER4_DEFAULTS, SqlCipher4Params,
)

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
MEM_COMMIT = 0x1000
MEM_PRIVATE = 0x20000
PAGE_GUARD = 0x100
PAGE_NOACCESS = 0x01
_READABLE = {0x02, 0x04, 0x08, 0x20, 0x40, 0x80}
TH32CS_SNAPPROCESS = 0x00000002

_HEX64 = re.compile(rb"(?<![0-9A-Fa-f])[0-9A-Fa-f]{64}(?![0-9A-Fa-f])")

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("__align", wintypes.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]


class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_char * 260),
    ]


@dataclass
class RecoveredKey:
    enc_key: bytes = field(repr=False)
    source_pid: int
    db_path: str
    as_hex: str = field(default="", repr=False)
    params: SqlCipher4Params = SQLCIPHER4_DEFAULTS
    format_name: str = field(init=False)

    def __post_init__(self):
        self.as_hex = self.enc_key.hex()
        self.format_name = self.params.name


def find_kakaotalk_pids() -> list[int]:
    """이름이 정확히 KakaoTalk.exe 인 프로세스들의 PID 목록(본인 세션)."""
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == -1:
        raise ctypes.WinError(ctypes.get_last_error())
    pids: list[int] = []
    try:
        entry = PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
        if not kernel32.Process32First(snap, ctypes.byref(entry)):
            return pids
        while True:
            name = entry.szExeFile.decode("ascii", "ignore")
            if name.lower() == "kakaotalk.exe":
                pids.append(entry.th32ProcessID)
            if not kernel32.Process32Next(snap, ctypes.byref(entry)):
                break
    finally:
        kernel32.CloseHandle(snap)
    return pids


def _open_process_readonly(pid: int):
    h = kernel32.OpenProcess(
        PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid
    )
    if not h:
        raise ctypes.WinError(ctypes.get_last_error())
    return h


def _iter_regions(h):
    """읽을 수 있는 private·commit 영역의 (base, size) 를 순회."""
    addr = 0
    mbi = MEMORY_BASIC_INFORMATION()
    size = ctypes.sizeof(mbi)
    max_addr = 0x7FFFFFFF0000
    while addr < max_addr:
        res = kernel32.VirtualQueryEx(
            h, ctypes.c_void_p(addr), ctypes.byref(mbi), size
        )
        if not res:
            break
        region = int(mbi.RegionSize)
        if region <= 0:
            break
        base = int(mbi.BaseAddress or 0)
        protect = int(mbi.Protect)
        low = protect & 0xFF
        if (
            int(mbi.State) == MEM_COMMIT
            and int(mbi.Type) == MEM_PRIVATE
            and not (protect & PAGE_GUARD)
            and low in _READABLE
        ):
            yield base, region
        addr = base + region


def _read_region(h, base: int, size: int, chunk: int = 4 * 1024 * 1024) -> bytes:
    out = bytearray()
    buf = ctypes.create_string_buffer(chunk)
    read = ctypes.c_size_t(0)
    off = 0
    while off < size:
        n = min(chunk, size - off)
        ok = kernel32.ReadProcessMemory(
            h, ctypes.c_void_p(base + off), buf, n, ctypes.byref(read)
        )
        if ok and read.value:
            out += buf.raw[: read.value]
        off += n
    return bytes(out)


def _hex_candidates(blob: bytes):
    """메모리 바이트열에서 64자 hex ASCII(=32바이트 키) 후보를 뽑는다."""
    seen: set[bytes] = set()
    for m in _HEX64.finditer(blob):
        try:
            key = bytes.fromhex(m.group().decode("ascii"))
        except ValueError:
            continue
        if key not in seen:
            seen.add(key)
            yield key


def find_v2_root_key(ks, pids: list[int] | None = None):
    """실행 중 본인 KakaoTalk.exe 메모리에서 v2 루트 키 R(32B)을 '한 번' 찾는다.
    각 32바이트 후보를 appstate 엔트리 하나의 ICV로 결정론적으로 판정(try_root_key). 찾으면 (R, pid), 없으면 None.
    이 R 하나로 전체 방 DEK를 0.3초에 유도한다(keystore_v2.recover_all_deks). 읽기 전용."""
    from .keystore_v2 import try_root_key
    if pids is None:
        pids = find_kakaotalk_pids()
    for step in (8, 1):
        seen: set[bytes] = set()
        for pid in pids:
            try:
                h = _open_process_readonly(pid)
            except OSError:
                continue
            try:
                for base, size in _iter_regions(h):
                    blob = _read_region(h, base, size)
                    if not blob:
                        continue
                    for cand in _raw_candidates(blob, seen, sizes=(32,), min_distinct=20, step=step):
                        if try_root_key(cand, ks):
                            return cand, pid
            finally:
                kernel32.CloseHandle(h)
    return None


def recover_key_for_db(db_first_page: bytes, db_path: str,
                       pids: list[int] | None = None,
                       params: SqlCipher4Params = SQLCIPHER4_DEFAULTS,
                       deep_scan: bool = False) -> RecoveredKey | None:
    """
    대상 '본인' DB 의 첫 페이지(4096바이트)를 받아, 실행 중인 본인
    KakaoTalk.exe 메모리에서 그 DB 를 여는 키와 포맷을 찾아 검증 후 반환.

    1차: 메모리 속 64자 hex ASCII(키 스펙 문자열) 후보 → 포맷 검증.
    2차(deep_scan=True): private 영역의 32/16바이트 윈도(엔트로피 필터).
    키 길이가 맞는 포맷만 HMAC 또는 실제 SQLite 헤더로 검증한다.
    params를 우선 시도하고 같은 페이지 크기의 두 프리셋도 시도한다.
    못 찾으면 None.
    """
    if pids is None:
        pids = find_kakaotalk_pids()
    if not pids:
        return None

    seen: set[bytes] = set()
    for pid in pids:
        try:
            h = _open_process_readonly(pid)
        except OSError:
            continue
        try:
            regions = list(_iter_regions(h))
            for base, size in regions:
                blob = _read_region(h, base, size)
                if not blob:
                    continue
                for key in _hex_candidates(blob):
                    if key in seen:
                        continue
                    seen.add(key)
                    matched = _matching_format(db_first_page, key, params)
                    if matched is not None:
                        return RecoveredKey(key, pid, db_path, params=matched)
            if deep_scan:
                sizes = tuple(sorted({c.key_size for c in _candidate_formats(params)}, reverse=True))
                for base, size in regions:
                    blob = _read_region(h, base, size)
                    hit = _scan_raw_windows(blob, db_first_page, params, seen=seen, sizes=sizes, min_distinct=20)
                    if hit is not None:
                        key, matched = hit
                        return RecoveredKey(key, pid, db_path, params=matched)
        finally:
            kernel32.CloseHandle(h)
    return None


@lru_cache(maxsize=32)
def _candidate_formats(params: SqlCipher4Params) -> tuple[SqlCipher4Params, ...]:
    """Build the format list once, not once per sliding window."""
    candidates = [params]
    for preset in FORMAT_PRESETS.values():
        if params.page_size - preset.reserve < 480:
            continue
        candidate = replace(preset, page_size=params.page_size)
        if candidate not in candidates:
            candidates.append(candidate)
    return tuple(candidates)


def _matching_format(first_page: bytes, key: bytes,
                     params: SqlCipher4Params) -> SqlCipher4Params | None:
    """Reuse HMAC/header validation without truncating or padding candidate keys."""
    for candidate in _candidate_formats(params):
        if len(key) == candidate.key_size and verify_raw_key(first_page, key, candidate):
            return candidate
    return None


def _raw_candidates(blob: bytes, seen: set[bytes] | None = None,
                    sizes: tuple[int, ...] = (32, 16), min_distinct: int = 1,
                    step: int = 1):
    """Byte-aligned windows of the given sizes, retaining the existing null-count filter.

    This is a cheap entropy heuristic, not a guarantee of randomness. Deduplicate
    equal bytes, including overlapping windows and candidates from earlier passes.
    min_distinct>1 cheaply rejects non-key windows (a random 32-byte key has ~28 distinct
    bytes) before the expensive AES verification — 2026-11-13 속도 개선.
    """
    if seen is None:
        seen = set()
    smallest = min(sizes)
    thr = {size: (size * 5 // 8 if min_distinct == 0 else min(min_distinct, size * 5 // 8)) for size in sizes}
    for i in range(0, max(0, len(blob) - smallest + 1), step):
        for size in sizes:
            if i + size > len(blob):
                continue
            window = blob[i:i + size]
            if window.count(0) > 4 or len(set(window)) < thr[size]:
                continue
            if window in seen:
                continue
            seen.add(window)
            yield window


def _scan_raw_windows(blob: bytes, db_first_page: bytes,
                      params: SqlCipher4Params = SQLCIPHER4_DEFAULTS,
                      *, seen: set[bytes] | None = None,
                      sizes: tuple[int, ...] = (32, 16), min_distinct: int = 1
                      ) -> tuple[bytes, SqlCipher4Params] | None:
    """Return the validated (raw key, format) from a supplied memory blob."""
    for window in _raw_candidates(blob, seen, sizes=sizes, min_distinct=min_distinct):
        matched = _matching_format(db_first_page, window, params)
        if matched is not None:
            return window, matched
    return None
