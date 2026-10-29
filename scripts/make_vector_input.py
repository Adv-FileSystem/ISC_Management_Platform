"""vector_topk 합성 입력 -- 질의 하나와 int8 벡터 행렬.

표준 라이브러리만 쓰고 같은 인자는 같은 바이트를 만든다. `--plant N` 은 질의에 작은
잡음을 더한 근접 복사본 N개를 데이터베이스에 심고 그 id 를 stderr 에 알린다. 그러면
검색 결과의 앞 N개가 순서와 무관하게 그 id 들과 같으므로 점수 표 없이 "검색이 맞게 되었다"를
눈으로 확인할 수 있다 (RAG 에서 정답 문서가 1위라는 것의 합성 대응물). 이 확인은 `--plant` 가
`k` 이하이고 dim 이 32 이상일 때만 성립한다: 심은 id 는 id 순, 결과는 점수 순이라 집합으로
비교해야 하고, dim 이 16 이하이면 잡음 때문에 자주 어긋난다 (실측, 4096행·시드 10개 중
성립한 수: dim 8 은 1, dim 16 은 5, dim 32 와 128 은 10).

순수 파이썬이라 값 백만 개에 1초쯤 걸린다. 검증용 작은 입력(예: 4096 x 128)은 즉시 끝나고,
큰 데이터베이스는 시간이 걸린다.

    python3 scripts/make_vector_input.py --rows 4096 --dim 128 --seed 1 --plant 8 \\
        -o datasets/vec_4k_128.bin
"""
from __future__ import annotations

import argparse
import json
import random
import struct
import sys
from pathlib import Path

MAX_DIM = 2048
NOISE = 0.15          # 심은 복사본의 잡음: 표준편차 1인 원본 대비


def quantize(values: list[float]) -> list[int]:
    """벡터의 최대 절댓값이 127 이 되도록 맞춘다. -128 은 나오지 않는다."""
    peak = max(abs(value) for value in values) or 1.0
    return [max(-127, min(127, round(value / peak * 127))) for value in values]


def build(rows: int, dim: int, seed: int, plant: int = 0) -> tuple[bytes, list[int]]:
    """입력 파일의 내용과 심은 id(오름차순)를 돌려준다."""
    if rows < 1:
        raise ValueError(f"rows must be at least 1, got {rows}")
    if not 1 <= dim <= MAX_DIM:
        raise ValueError(f"dim must be 1..{MAX_DIM}, got {dim}")
    if not 0 <= plant <= rows:
        raise ValueError(f"plant must be 0..rows ({rows}), got {plant}")

    rng = random.Random(seed)
    query_exact = [rng.gauss(0.0, 1.0) for _ in range(dim)]
    planted = sorted(rng.sample(range(rows), plant))
    planted_set = set(planted)

    data = bytearray(struct.pack("<II", rows, dim))
    data += bytes(value & 0xFF for value in quantize(query_exact))
    for index in range(rows):
        if index in planted_set:
            vector = [value + rng.gauss(0.0, NOISE) for value in query_exact]
        else:
            vector = [rng.gauss(0.0, 1.0) for _ in range(dim)]
        data += bytes(value & 0xFF for value in quantize(vector))
    return bytes(data), planted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Make a synthetic input file for vector_topk.")
    parser.add_argument("--rows", type=int, required=True, help="database vectors")
    parser.add_argument("--dim", type=int, required=True, help=f"vector dimension, 1..{MAX_DIM}")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--plant", type=int, default=0,
                        help="hide this many noisy copies of the query in the database; the top "
                             "PLANT results then equal the planted ids as a set (order-free), "
                             "provided PLANT <= k and dim >= 32")
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        data, planted = build(args.rows, args.dim, args.seed, args.plant)
    except ValueError as exc:
        parser.error(str(exc))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(data)
    print(json.dumps({"output": str(args.output), "rows": args.rows, "dim": args.dim,
                      "bytes": len(data), "planted_ids": planted}), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
