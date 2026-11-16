"""Raw-key page codecs. Windows KakaoTalk's actual format is NOT established.

SQLCipher4: [salt16 on page1][CBC ciphertext][IV16][HMAC-SHA512 64].
Device-key hypothesis: [CBC ciphertext, including SQLite magic][IV16].
Neither a random-looking prefix nor a random-looking trailer proves a layout.
Functions here operate on bytes only; no files or processes are opened.
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
from dataclasses import dataclass, replace

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

SQLITE_MAGIC = b"SQLite format 3\x00"
PAGE_SIZES = (512, 1024, 2048, 4096, 8192, 16384, 32768, 65536)


@dataclass(frozen=True)
class SqlCipher4Params:
    page_size: int = 4096
    kdf_iter: int = 256000
    fast_kdf_iter: int = 2
    hmac_salt_mask: int = 0x3A
    iv_size: int = 16
    hmac_size: int = 64
    salt_size: int = 16
    aes_key_bits: int = 256
    use_hmac: bool = True
    use_salt: bool = True
    reserve: int | None = None

    def __post_init__(self):
        if self.page_size not in PAGE_SIZES:
            raise ValueError("page_size must be a SQLite power of two, 512..65536")
        if self.aes_key_bits not in (128, 256):
            raise ValueError("aes_key_bits must be 128 or 256")
        if self.iv_size != 16:
            raise ValueError("AES-CBC requires a 16-byte IV")
        if self.salt_size != 16:
            raise ValueError("salt_size must be 16; use_salt=False disables it")
        if self.hmac_size != 64:
            raise ValueError("only HMAC-SHA512 is supported; use_hmac=False disables it")
        if self.fast_kdf_iter < 1 or not 0 <= self.hmac_salt_mask <= 255:
            raise ValueError("invalid HMAC KDF parameters")
        minimum = 16 + (64 if self.use_hmac else 0)
        if self.reserve is None:
            object.__setattr__(self, "reserve", minimum)
        if (not isinstance(self.reserve, int) or self.reserve < minimum
                or self.reserve > 255 or self.reserve % 16):
            raise ValueError("reserve must fit IV/HMAC, be <=255 and divisible by 16")
        if self.page_size - self.reserve < 480:
            raise ValueError("SQLite requires at least 480 usable bytes per page")

    @property
    def name(self) -> str:
        if self.aes_key_bits == 256 and self.use_hmac and self.use_salt:
            return "SQLCIPHER4"
        if self.aes_key_bits == 128 and not self.use_hmac and not self.use_salt:
            return "DEVICE_KEY_AES128"
        return "CUSTOM"

    @property
    def salt_prefix(self) -> int:
        return self.first_page_offset

    @property
    def key_size(self) -> int:
        return self.aes_key_bits // 8

    @property
    def first_page_offset(self) -> int:
        return 16 if self.use_salt else 0


CipherParams = SqlCipher4Params
SQLCIPHER4_DEFAULTS = CipherParams()
DEVICE_AES128_DEFAULTS = CipherParams(
    aes_key_bits=128, use_hmac=False, use_salt=False, reserve=16
)
FORMAT_PRESETS = {
    "sqlcipher4": SQLCIPHER4_DEFAULTS,
    "device-aes128": DEVICE_AES128_DEFAULTS,
}


@dataclass(frozen=True)
class FormatDetection:
    status: str
    candidates: tuple[CipherParams, ...]
    possible_page_sizes: tuple[int, ...]
    reasons: tuple[str, ...]


def detect_format(first_page: bytes, file_size: int) -> FormatDetection:
    """Return structural candidates, never pretend ciphertext identifies a codec.

    Supply up to 65536 bytes for all supported page sizes. Candidates requiring
    more bytes than supplied remain listed in possible_page_sizes, but cannot be
    key-tested yet. 4096 is tried first for convenience, not statistical evidence.
    This detects only the two presets; custom combinations need explicit params.
    """
    if file_size <= 0 or not first_page or len(first_page) > file_size:
        return FormatDetection("invalid", (), (), ("Empty or inconsistent input.",))
    sizes = tuple(s for s in PAGE_SIZES if file_size % s == 0)
    if first_page.startswith(SQLITE_MAGIC):
        return FormatDetection(
            "plaintext", (), sizes, ("SQLite magic is visible; not an encrypted preset.",)
        )
    if not sizes:
        return FormatDetection("invalid", (), (), ("No supported page size divides file_size.",))
    ordered = sorted(sizes, key=lambda s: (s != 4096, s))
    candidates = tuple(
        replace(p, page_size=s)
        for s in ordered if len(first_page) >= s
        for p in FORMAT_PRESETS.values() if s - p.reserve >= 480
    )
    return FormatDetection(
        "ambiguous" if candidates else "insufficient",
        candidates, sizes,
        ("File-size divisibility constrains page size but does not prove it.",
         "Without a key, random salt/ciphertext/IV/HMAC bytes cannot identify boundaries.",
         "Both presets are hypotheses; other codecs remain possible.")
    )


def _check_key(enc_key: bytes, params: CipherParams) -> None:
    if len(enc_key) != params.key_size:
        raise ValueError(f"enc_key must be {params.key_size} bytes (AES-{params.aes_key_bits})")


def derive_hmac_key(enc_key: bytes, salt: bytes,
                    params: CipherParams = SQLCIPHER4_DEFAULTS) -> bytes:
    """SQLCipher uses cipher key size, NOT digest size, for the derived HMAC key.

    See sqlcipher.c: sqlcipher_cipher_ctx_key_derive(), ctx->key_sz.
    With use_salt=False the raw-key extension uses an empty KDF salt.
    """
    _check_key(enc_key, params)
    if not params.use_hmac:
        raise ValueError("HMAC is disabled for this format")
    expected_salt = 16 if params.use_salt else 0
    if len(salt) != expected_salt:
        raise ValueError(f"salt must be {expected_salt} bytes")
    hmac_salt = bytes(b ^ params.hmac_salt_mask for b in salt)
    return hashlib.pbkdf2_hmac(
        "sha512", enc_key, hmac_salt, params.fast_kdf_iter, dklen=params.key_size
    )


def _page_hmac(hmac_key: bytes, ciphertext: bytes, iv: bytes, page_no: int) -> bytes:
    return _hmac.new(
        hmac_key, ciphertext + iv + page_no.to_bytes(4, "little"), hashlib.sha512
    ).digest()


def _split_page(page: bytes, page_no: int, params: CipherParams):
    if len(page) != params.page_size:
        raise ValueError("exactly one complete page is required")
    if not 1 <= page_no <= 0xFFFFFFFE:
        raise ValueError("invalid page number")
    body_off = params.first_page_offset if page_no == 1 else 0
    ct_end = params.page_size - params.reserve
    mac_size = params.hmac_size if params.use_hmac else 0
    return (page[body_off:ct_end], page[ct_end:ct_end + 16],
            page[ct_end + 16:ct_end + 16 + mac_size], body_off)


def _decrypt_page(page: bytes, page_no: int, enc_key: bytes, hmac_key: bytes,
                  params: CipherParams, check_hmac: bool) -> bytes:
    ciphertext, iv, stored_hmac, body_off = _split_page(page, page_no, params)
    if params.use_hmac and check_hmac:
        calc = _page_hmac(hmac_key, ciphertext, iv, page_no)
        if not _hmac.compare_digest(calc, stored_hmac):
            raise ValueError(f"page {page_no}: HMAC mismatch (wrong key or corrupt)")
    dec = Cipher(algorithms.AES(enc_key), modes.CBC(iv)).decryptor()
    plain = dec.update(ciphertext) + dec.finalize()
    if body_off:
        plain = SQLITE_MAGIC + plain
    return plain + b"\x00" * params.reserve


def valid_sqlite_header(page: bytes, params: CipherParams) -> bool:
    """Check decrypted fields independently of the supplied/reconstructed magic.

    This is a wrong-key/format check, not authentication for a no-HMAC database.
    Whole-database SQLite integrity_check is still required.
    """
    if len(page) < 100 or page[:16] != SQLITE_MAGIC:
        return False
    size = int.from_bytes(page[16:18], "big")
    size = 65536 if size == 1 else size
    return (
        size == params.page_size
        and page[18] in (1, 2) and page[19] in (1, 2)
        and page[20] == params.reserve
        and page[21:24] == b"\x40\x20\x20"
        and int.from_bytes(page[44:48], "big") in (1, 2, 3, 4)
        and int.from_bytes(page[56:60], "big") in (1, 2, 3)
        and page[72:92] == b"\x00" * 20
    )


def verify_raw_key(first_page: bytes, enc_key: bytes,
                   params: CipherParams = SQLCIPHER4_DEFAULTS) -> bool:
    """HMAC mode: authenticate page1. No-HMAC: check decrypted SQLite fields.

    Exact page and exact key lengths are required. A 32-byte key is never silently
    truncated to AES-128. The memkey deep scanner emits 16- and 32-byte keys.
    """
    if len(enc_key) != params.key_size or len(first_page) != params.page_size:
        return False
    if params.use_hmac:
        salt = first_page[:16] if params.use_salt else b""
        hk = derive_hmac_key(enc_key, salt, params)
        ciphertext, iv, mac, _ = _split_page(first_page, 1, params)
        return _hmac.compare_digest(_page_hmac(hk, ciphertext, iv, 1), mac)
    plain = _decrypt_page(first_page, 1, enc_key, b"", params, False)
    return valid_sqlite_header(plain, params)


def matching_formats(first_page: bytes, file_size: int, enc_key: bytes,
                     candidates: tuple[CipherParams, ...] | None = None
                     ) -> tuple[CipherParams, ...]:
    """Key-test all structural candidates, requiring a valid decrypted header."""
    if candidates is None:
        candidates = detect_format(first_page, file_size).candidates
    matches = []
    for p in candidates:
        if file_size <= 0 or file_size % p.page_size:
            continue
        page = first_page[:p.page_size]
        if not verify_raw_key(page, enc_key, p):
            continue
        salt = page[:16] if p.use_salt else b""
        hk = derive_hmac_key(enc_key, salt, p) if p.use_hmac else b""
        plain = _decrypt_page(page, 1, enc_key, hk, p, True)
        if valid_sqlite_header(plain, p):
            matches.append(p)
    return tuple(matches)


def decrypt_database(encrypted: bytes, enc_key: bytes,
                     params: CipherParams = SQLCIPHER4_DEFAULTS,
                     check_hmac: bool = True) -> bytes:
    """Decrypt bytes only; reserved bytes remain reserved and are zeroed.

    HMAC verifies every page when enabled. No-HMAC mode checks the first header,
    but cannot authenticate later pages. Caller must check SQLite integrity.
    check_hmac=False is retained for compatibility; it does not authenticate data.
    """
    _check_key(enc_key, params)
    if not encrypted or len(encrypted) % params.page_size:
        raise ValueError("file must contain a nonzero whole number of pages")
    salt = encrypted[:16] if params.use_salt else b""
    hk = derive_hmac_key(enc_key, salt, params) if params.use_hmac else b""
    out = bytearray()
    for index in range(len(encrypted) // params.page_size):
        page = encrypted[index * params.page_size:(index + 1) * params.page_size]
        plain = _decrypt_page(page, index + 1, enc_key, hk, params, check_hmac)
        if index == 0 and not params.use_hmac and not valid_sqlite_header(plain, params):
            raise ValueError("decrypted SQLite header mismatch (wrong key or format)")
        out.extend(plain)
    return bytes(out)


WAL_MAGICS = (0x377F0682, 0x377F0683)


def apply_wal(plain_db: bytes, encrypted_db: bytes, wal: bytes, enc_key: bytes,
              params: CipherParams = SQLCIPHER4_DEFAULTS) -> tuple[bytes, int]:
    """Overlay committed frames of an SQLCipher -wal file onto the decrypted main DB.

    KakaoTalk keeps recent messages in chatLogs_*.edb-wal until a checkpoint, so reading
    only the main file misses them (2026-11-12: a sent message stayed in the WAL for minutes).
    WAL header/frame headers are plaintext; each frame page is encrypted like a main page
    with its own page number. Frames are taken only while their salts match the WAL header
    and only up to the last commit frame; an HMAC failure stops reading (stale tail).
    Returns (plain bytes, number of committed frames applied)."""
    if len(wal) < 32 or int.from_bytes(wal[:4], "big") not in WAL_MAGICS:
        return plain_db, 0
    ps = int.from_bytes(wal[8:12], "big")
    if ps != params.page_size:
        return plain_db, 0
    salts = wal[16:24]
    salt = encrypted_db[:16] if params.use_salt else b""
    hk = derive_hmac_key(enc_key, salt, params) if params.use_hmac else b""
    pages: dict[int, bytes] = {}
    pending: dict[int, bytes] = {}
    db_pages = len(plain_db) // ps
    applied = 0
    off = 32
    while off + 24 + ps <= len(wal):
        fh = wal[off:off + 24]
        if fh[8:16] != salts:
            break
        pgno = int.from_bytes(fh[:4], "big")
        commit = int.from_bytes(fh[4:8], "big")
        try:
            pending[pgno] = _decrypt_page(wal[off + 24:off + 24 + ps], pgno, enc_key, hk, params, True)
        except ValueError:
            break
        off += 24 + ps
        if commit:
            pages.update(pending)
            applied += len(pending)
            pending = {}
            db_pages = commit
    if not pages:
        return plain_db, 0
    out = bytearray(plain_db[:db_pages * ps].ljust(db_pages * ps, b"\x00"))
    for pgno, data in pages.items():
        if pgno <= db_pages:
            out[(pgno - 1) * ps:pgno * ps] = data
    return bytes(out), applied


SqlCipherParams = CipherParams
DEVICE_KEY_AES128 = DEVICE_AES128_DEFAULTS
SQLITE_HEADER = SQLITE_MAGIC
PRESETS = tuple(FORMAT_PRESETS.values())


def try_decrypt_database(encrypted: bytes, enc_key: bytes,
                         presets=None, check_hmac: bool = True):
    """Compatibility raw codec helper: (plaintext, params), else (None, None).

    For a real SQLite image use verify_database_bytes for whole-file validation.
    An authenticated arbitrary payload is not necessarily a valid SQLite DB.
    """
    candidates = PRESETS if presets is None else presets
    for p in candidates:
        if not encrypted or len(encrypted) % p.page_size:
            continue
        if not verify_raw_key(encrypted[:p.page_size], enc_key, p):
            continue
        try:
            return decrypt_database(encrypted, enc_key, p, check_hmac), p
        except ValueError:
            continue
    return None, None
