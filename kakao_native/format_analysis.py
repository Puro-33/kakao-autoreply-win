"""Read-only, bounded structural measurements; never copies database bytes."""
from __future__ import annotations

import math
import os
from collections import Counter
from functools import reduce
from pathlib import Path

from .sqlcipher4 import PAGE_SIZES, SQLITE_MAGIC, detect_format


def _entropy(data: bytes) -> float:
    counts = Counter(data)
    n = len(data)
    return round(-sum((v / n) * math.log2(v / n) for v in counts.values()), 6) if n else 0.0


def _region_stats(values: list[bytes]) -> dict:
    counts = Counter(values)
    pooled = b"".join(values)
    return {
        "samples": len(values),
        "unique_values": len(counts),
        "largest_identical_group": max(counts.values(), default=0),
        "all_zero_samples": sum(not any(v) for v in values),
        "pooled_bytes": len(pooled),
        "pooled_entropy_bits_per_byte": _entropy(pooled),
        "pooled_zero_bytes": pooled.count(0),
    }


def analyze_directory(directory: str, page_size: int = 4096) -> dict:
    """Sample first/middle/last full pages in each chatLogs_*.edb.

    page_size is a SAMPLING HYPOTHESIS. Entropy cannot identify a salt or HMAC.
    Output contains only counts/statistics, no DB names or original byte strings.
    Permission errors propagate: never report an inaccessible directory as empty.
    """
    if page_size not in PAGE_SIZES:
        raise ValueError("unsupported sampling page size")
    root = Path(directory)
    with os.scandir(root) as entries:
        paths = sorted(
            Path(e.path) for e in entries
            if e.name.startswith("chatLogs_") and e.name.endswith(".edb")
            and e.is_file(follow_symlinks=False)
        )
    sizes, prefixes, bodies, tails16, tails80, iv80 = [], [], [], [], [], []
    plaintext_count = 0
    changes = 0
    errors = Counter()
    sampled_pages = 0
    detections = Counter()
    for path in paths:
        try:
            with path.open("rb") as f:
                before = os.fstat(f.fileno())
                size = before.st_size
                prefix = f.read(65536)
                sizes.append(size)
                if len(prefix) >= 16:
                    prefixes.append(prefix[:16])
                plaintext_count += prefix.startswith(SQLITE_MAGIC)
                detections[detect_format(prefix, size).status] += 1
                page_count = size // page_size
                for index in sorted({0, page_count // 2, page_count - 1}):
                    if index < 0 or index >= page_count:
                        continue
                    f.seek(index * page_size)
                    page = f.read(page_size)
                    if len(page) != page_size:
                        errors["short_page"] += 1
                        continue
                    sampled_pages += 1
                    bodies.append(page[16:-80])
                    tails16.append(page[-16:])
                    tails80.append(page[-80:])
                    iv80.append(page[-80:-64])
                after = os.fstat(f.fileno())
                changes += (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
        except OSError as exc:
            errors[type(exc).__name__] += 1
    return {
        "files_found": len(paths),
        "files_opened": len(sizes),
        "read_errors": dict(errors),
        "files_changed_during_read": changes,
        "size_bytes": {"min": min(sizes, default=0), "max": max(sizes, default=0),
                       "total": sum(sizes), "gcd": reduce(math.gcd, sizes, 0)},
        "divisible_file_counts": {str(s): sum(n > 0 and n % s == 0 for n in sizes)
                                  for s in PAGE_SIZES},
        "visible_sqlite_magic_files": plaintext_count,
        "first16": _region_stats(prefixes),
        "sampling_page_size_hypothesis": page_size,
        "sampled_pages": sampled_pages,
        "body16_to_minus80": _region_stats(bodies),
        "tail16_possible_iv": _region_stats(tails16),
        "tail80_possible_reserve": _region_stats(tails80),
        "minus80_to_minus64_possible_iv": _region_stats(iv80),
        "first_page_detection_counts": dict(detections),
        "layout_arithmetic_4096": {
            "sqlcipher4": {"page1_ciphertext": 4000, "later_ciphertext": 4016,
                           "first_page_offset": 16, "reserve": 80, "aes_block_aligned": True},
            "device_aes128": {"page1_ciphertext": 4080, "later_ciphertext": 4080,
                             "first_page_offset": 0, "reserve": 16, "aes_block_aligned": True},
        },
        "conclusion": (
            "Ciphertext-only measurements do not establish AES key size, salt semantics, "
            "reserve size or an exact page size. Equal prefixes show equality only; "
            "different prefixes are compatible with random salts and with ciphertext. "
            "Validate with an owned raw key, decrypted header fields and SQLite integrity_check."
        ),
    }
