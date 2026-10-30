"""CPU 기준선 — 장치 커널과 같은 프레이밍으로 LZ4 블록을 압축한다.

성능 비교 전용이다. 같은 입력에 대해 CPU LZ4 와 장치 커널은 서로 다른, 그러나
둘 다 올바른 블록을 낸다(매치 탐색 전략이 다르다). 그래서 operation.yaml 의
baseline.equivalence 는 `none` 이고 바이트 대조에는 쓰이지 않는다.

압축률은 비교 가능해야 하므로 어댑터와 동일한 12 바이트 블록 헤더를 쓴다.
"""
from __future__ import annotations

import struct

from isc.operations.baseline import BaselineUnavailable

HEADER = struct.Struct("<III")
FLAG_LZ4 = 0
FLAG_STORED = 1


def compute(data: bytes, parameters: dict) -> bytes:
    try:
        import lz4.block as lz4_block
    except ImportError as exc:  # 조용히 다른 알고리즘으로 대체하지 않는다
        raise BaselineUnavailable(
            "lz4 CPU 기준선에는 `lz4` 패키지가 필요합니다 (pip install lz4). "
            "다른 압축 알고리즘으로 대체하면 speedup 이 의미를 잃습니다"
        ) from exc

    if not data:
        return b""

    block_size = int(parameters["block_size"])
    framed = bytearray()
    for offset in range(0, len(data), block_size):
        block = data[offset:offset + block_size]
        compressed = lz4_block.compress(block, store_size=False)
        # 어댑터와 같은 판정: 이득이 없으면 원본을 저장한다.
        if not 0 < len(compressed) < len(block):
            framed += HEADER.pack(len(block), len(block), FLAG_STORED)
            framed += block
        else:
            framed += HEADER.pack(len(block), len(compressed), FLAG_LZ4)
            framed += compressed
    return bytes(framed)
