#!/usr/bin/env python3
"""vector_topk 결과 확인 (README 3).

FPGA 출력 (u32 id, i32 score) k쌍을, numpy로 같은 정수 내적과 정렬 규칙(점수 내림차순 → id 오름차순)을
적용한 결과와 비교합니다.

    python3 isc/operations/vector_topk/examples/check_output.py datasets/vector_topk/result.emu.bin
    python3 isc/operations/vector_topk/examples/check_output.py 출력파일 --input 입력파일 --k 5
"""
import argparse
import struct

import numpy as np

DEFAULT_INPUT = "datasets/vector_topk/docs.bin"


def main() -> None:
    parser = argparse.ArgumentParser(description="vector_topk 결과 확인")
    parser.add_argument("output", help="isc run --output 으로 받은 파일")
    parser.add_argument("--input", default=DEFAULT_INPUT, help="isc run --input 으로 넘긴 파일")
    parser.add_argument("--k", type=int, help="module.yaml의 k (생략하면 출력 길이로 계산)")
    args = parser.parse_args()

    data = open(args.input, "rb").read()
    rows, dim = struct.unpack_from("<II", data)
    query = np.frombuffer(data, np.int8, dim, 8).astype(np.int32)
    db = np.frombuffer(data, np.int8, rows * dim, 8 + dim).reshape(rows, dim).astype(np.int32)
    out = open(args.output, "rb").read()
    k = args.k or len(out) // 8

    scores = db @ query                                                        # 지금 쓰는 CPU 코드 (numpy 내적)
    order = np.lexsort((np.arange(rows), -scores.astype(np.int64)))[:k]
    expected = [(int(i), int(scores[i])) for i in order]
    expected += [(0xFFFFFFFF, -2147483648)] * (k - len(expected))           # rows < k이면 빈 칸
    got = [struct.unpack_from("<Ii", out, o) for o in range(0, len(out), 8)]

    for rank, (doc_id, score) in enumerate(got, 1):
        print(f"{rank}. id={doc_id} score={score}")
    print("MATCH" if got == expected else "MISMATCH")
    raise SystemExit(0 if got == expected else 1)


if __name__ == "__main__":
    main()
