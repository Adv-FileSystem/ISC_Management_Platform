"""ISC가 관리하는 2-hop 경로 수 연산 호스트 로직. 1-hop·2-hop 에 같은 CSR 을 쓴다 (공식 테스트와 같음)."""
from __future__ import annotations

import struct

from twohop_format import parse

ARG_NUM, ARG_PAIR, ARG_OFF1, ARG_IDX1, ARG_OFF2, ARG_IDX2, ARG_COUNT = range(7)


def check_input(data: bytes, parameters: dict) -> None:
    parse(data)


def execute(session, data: bytes, parameters: dict) -> bytes:
    v, e, offset, index, pairs = parse(data)
    p = len(pairs)
    off_bytes = struct.pack(f"<{v + 1}I", *offset)
    idx_bytes = struct.pack(f"<{e}I", *index) if e else b"\0" * 4
    pair_bytes = b"".join(struct.pack("<Q", (s << 32) | d) for s, d in pairs)
    bo_pair = session.buffer(len(pair_bytes), arg=ARG_PAIR)
    bo_off1 = session.buffer(len(off_bytes), arg=ARG_OFF1)
    bo_idx1 = session.buffer(len(idx_bytes), arg=ARG_IDX1)
    bo_off2 = session.buffer(len(off_bytes), arg=ARG_OFF2)
    bo_idx2 = session.buffer(len(idx_bytes), arg=ARG_IDX2)
    bo_cnt = session.buffer(4 * p, arg=ARG_COUNT)
    bo_pair.write(pair_bytes)
    for bo, raw in ((bo_off1, off_bytes), (bo_idx1, idx_bytes), (bo_off2, off_bytes), (bo_idx2, idx_bytes)):
        bo.write(raw)
    session.run(p, bo_pair, bo_off1, bo_idx1, bo_off2, bo_idx2, bo_cnt)
    session.metric("vertices", v)
    session.metric("edges", e)
    session.metric("pairs", p)
    return bo_cnt.read(4 * p)
