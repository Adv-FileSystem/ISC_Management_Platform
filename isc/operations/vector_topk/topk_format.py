"""vector_topk 의 바이트 형식. 어댑터와 CPU 기준선이 같은 정의를 쓴다.

입력 isc_vectors_i8 (리틀 엔디안):
    u32 rows, u32 dim, i8 query[dim], i8 db[rows][dim]   (행 우선)
출력 isc_topk_pairs:
    k 개의 (u32 id, i32 score). 점수 내림차순, 같으면 id 오름차순.
    rows < k 이면 남는 칸은 id 0xFFFFFFFF, score -2**31.

순위는 (score 내림차순, id 오름차순) 전순서다. id 는 유일하므로 같은 입력에는 답이
하나뿐이고, 그래서 커널·어댑터·numpy 가 바이트까지 같아야 한다.
"""
from __future__ import annotations

import struct

HEADER = struct.Struct("<II")        # rows, dim
ENTRY = struct.Struct("<Ii")         # id, score
MAX_DIM = 2048
MAX_K = 16
LANES = 16                           # 커널이 한 사이클에 읽는 바이트
NO_ID = 0xFFFFFFFF
NO_SCORE = -(2 ** 31)


def check(data: bytes) -> tuple[int, int]:
    """(rows, dim) of a well-formed input; ValueError saying what is wrong otherwise."""
    if len(data) < HEADER.size + 1:
        raise ValueError(
            f"vector_topk input needs at least {HEADER.size + 1} bytes "
            f"(rows, dim, query); received {len(data)}"
        )
    rows, dim = HEADER.unpack_from(data, 0)
    if not 1 <= dim <= MAX_DIM:
        raise ValueError(f"vector_topk dim must be 1..{MAX_DIM}, the header says {dim}")
    expected = HEADER.size + dim + rows * dim
    if len(data) != expected:
        raise ValueError(
            f"vector_topk header says rows={rows}, dim={dim}, which is {expected} bytes; "
            f"received {len(data)}"
        )
    return rows, dim


def padded_dim(dim: int) -> int:
    """dim rounded up to a whole number of lanes; the host pads with zeros, which add nothing."""
    return -(-dim // LANES) * LANES


def pad_rows(block: bytes, count: int, dim: int, stride: int) -> bytes:
    if stride == dim:
        return block
    pad = bytes(stride - dim)
    return b"".join(block[i * dim:(i + 1) * dim] + pad for i in range(count))


def order(entries: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Best first: score descending, then id ascending."""
    return sorted(entries, key=lambda entry: (-entry[1], entry[0]))


def pack(entries: list[tuple[int, int]], k: int) -> bytes:
    kept = order(entries)[:k]
    kept += [(NO_ID, NO_SCORE)] * (k - len(kept))
    return b"".join(ENTRY.pack(entry_id, score) for entry_id, score in kept)


def decode(output: bytes) -> list[tuple[int, int]]:
    """The real entries of an output, best first. Empty slots are left out."""
    if len(output) % ENTRY.size:
        raise ValueError(f"vector_topk output must be a multiple of {ENTRY.size} bytes, got {len(output)}")
    entries = [ENTRY.unpack_from(output, offset) for offset in range(0, len(output), ENTRY.size)]
    return [(entry_id, score) for entry_id, score in entries if entry_id != NO_ID]
