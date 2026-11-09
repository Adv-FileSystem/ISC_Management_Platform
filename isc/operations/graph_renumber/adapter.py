"""ISC가 관리하는 연속 번호 재매김 연산 호스트 로직 (공식 테스트 호스트와 같은 버퍼 구성)."""
from __future__ import annotations

import struct

from renumber_format import parse

ARG_CFG, ARG_OLD, ARG_MAP0, ARG_MAP1, ARG_NEW = range(5)


def check_input(data: bytes, parameters: dict) -> None:
    parse(data)


def execute(session, data: bytes, parameters: dict) -> bytes:
    n, ids = parse(data)
    size = 4 * (n + 1)
    bo_cfg = session.buffer(64, arg=ARG_CFG)
    bo_old = session.buffer(size, arg=ARG_OLD)
    bo_map0 = session.buffer(size, arg=ARG_MAP0)
    bo_map1 = session.buffer(size, arg=ARG_MAP1)
    bo_new = session.buffer(size, arg=ARG_NEW)
    bo_cfg.write(struct.pack("<2i", n, 0).ljust(64, b"\0"))
    bo_old.write(struct.pack(f"<{n}i", *ids) + b"\0\0\0\0")
    bo_map0.write(b"\0" * size)
    bo_map1.write(b"\0" * size)
    session.run(bo_cfg, bo_old, bo_map0, bo_map1, bo_new)
    clusters = struct.unpack("<2i", bo_cfg.read(8))[1]
    session.metric("ids", n)
    session.metric("clusters", clusters)
    return struct.pack("<I", clusters) + bo_new.read(4 * n)
