"""ISC가 관리하는 LZ4 연산 호스트 로직.

커널은 한 번 호출에 한 블록만 압축한다. 블록 반복과 출력 프레이밍은
호스트의 몫이며, 그것이 바로 이 파일이다.

출력 포맷 (블록마다 12바이트 리틀엔디언 헤더 + 페이로드):
    u32 raw_size      원본 블록 크기
    u32 stored_size   뒤따르는 페이로드 바이트 수
    u32 flags         0 = LZ4 블록, 1 = 비압축 원본 저장
"""
from __future__ import annotations

import struct

HEADER = struct.Struct("<III")
FLAG_LZ4 = 0
FLAG_STORED = 1

# 커널 인자 인덱스 (operation.yaml의 kernel.arguments 순서와 같다)
ARG_IN, ARG_OUT, ARG_INPUT_SIZE, ARG_OUTPUT_SIZE, ARG_OVERFLOW = range(5)


def worst_case_size(size: int) -> int:
    """LZ4 블록 포맷의 최악 팽창 상한."""
    return size + size // 255 + 16


def execute(session, data: bytes, parameters: dict) -> bytes:
    if not data:
        return b""

    block_size = int(parameters["block_size"])
    bo_in = session.buffer(block_size, arg=ARG_IN)
    bo_out = session.buffer(worst_case_size(block_size), arg=ARG_OUT)
    bo_size = session.buffer(4, arg=ARG_OUTPUT_SIZE)
    bo_overflow = session.buffer(4, arg=ARG_OVERFLOW)

    framed = bytearray()
    stored_blocks = 0

    for offset in range(0, len(data), block_size):
        block = data[offset:offset + block_size]
        bo_in.write(block)
        session.run(bo_in, bo_out, len(block), bo_size, bo_overflow)

        compressed_size = int.from_bytes(bo_size.read(4), "little")
        overflow = int.from_bytes(bo_overflow.read(4), "little")

        # overflow 가 서면 커널이 리터럴 초과분을 조용히 버렸으므로 출력을
        # 신뢰할 수 없다. 압축이 이득이 없을 때도 마찬가지로 원본을 저장한다.
        if overflow or not 0 < compressed_size < len(block):
            framed += HEADER.pack(len(block), len(block), FLAG_STORED)
            framed += block
            stored_blocks += 1
        else:
            framed += HEADER.pack(len(block), compressed_size, FLAG_LZ4)
            framed += bo_out.read(compressed_size)

    session.metric("compression_ratio", len(data) / len(framed))
    session.metric("stored_blocks", stored_blocks)
    session.metric("blocks", (len(data) + block_size - 1) // block_size)
    return bytes(framed)
