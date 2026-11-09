"""ISC가 관리하는 Snappy 블록 압축 연산 호스트 로직.

공식 호스트(common/libs/compress/snappyOCLHost.cpp)처럼 입력을 2 MiB 덩어리로 나눠 커널을 부르고,
덩어리 안의 64 KiB 블록마다 커널이 쓴 Snappy raw 블록(길이 머리말 포함)을 꺼낸다. 압축 이득이 없는 블록은
원본으로 저장한다 (공식 호스트와 같은 판정). 출력 틀은 플랫폼 lz4 연산과 같은 12바이트 블록 헤더다.
"""
from __future__ import annotations

import struct

HEADER = struct.Struct("<III")
FLAG_SNAPPY, FLAG_STORED = 0, 1
BLOCK_KB = 64
BLOCK = BLOCK_KB * 1024
CHUNK = 2 * 1024 * 1024
ARG_IN, ARG_OUT, ARG_CSIZE, ARG_BSIZE, ARG_BLOCK_KB, ARG_INPUT_SIZE = range(6)


def execute(session, data: bytes, parameters: dict) -> bytes:
    nblocks_max = CHUNK // BLOCK
    bo_in = session.buffer(CHUNK, arg=ARG_IN)
    bo_out = session.buffer(CHUNK, arg=ARG_OUT)
    bo_csize = session.buffer(4 * nblocks_max, arg=ARG_CSIZE)
    bo_bsize = session.buffer(4 * nblocks_max, arg=ARG_BSIZE)
    framed = bytearray()
    stored = blocks = 0
    for start in range(0, len(data), CHUNK):
        chunk = data[start:start + CHUNK]
        sizes = [min(BLOCK, len(chunk) - b) for b in range(0, len(chunk), BLOCK)]
        bo_in.write(chunk.ljust(-(-len(chunk) // BLOCK) * BLOCK, b"\0"))
        bo_bsize.write(struct.pack(f"<{len(sizes)}I", *sizes).ljust(4 * nblocks_max, b"\0"))
        session.run(bo_in, bo_out, bo_csize, bo_bsize, BLOCK_KB, len(chunk))
        csizes = struct.unpack(f"<{len(sizes)}I", bo_csize.read(4 * len(sizes)))
        out = bo_out.read(CHUNK)
        for i, (raw, comp) in enumerate(zip(sizes, csizes)):
            blocks += 1
            if 0 < comp < raw:
                framed += HEADER.pack(raw, comp, FLAG_SNAPPY) + out[i * BLOCK:i * BLOCK + comp]
            else:
                framed += HEADER.pack(raw, raw, FLAG_STORED) + chunk[i * BLOCK:i * BLOCK + raw]
                stored += 1
    session.metric("blocks", blocks)
    session.metric("stored_blocks", stored)
    session.metric("compression_ratio", len(data) / len(framed))
    return bytes(framed)
