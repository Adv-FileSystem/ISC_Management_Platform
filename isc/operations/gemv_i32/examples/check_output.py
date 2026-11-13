#!/usr/bin/env python3
"""gemv_i32 결과 확인 (README 3).

FPGA 출력(i32 y_out[m])을 CPU 참조와 바이트 비교합니다. CPU 참조는 연산 폴더의 baseline.py(numpy int64로
곱한 뒤 int32로 자름)로 점수를 계산하고, 고객마다 decide() 를 적용한 값입니다.
decide() 는 모듈의 logic.hpp 에 쓴 isc_decide 와 같은 규칙이어야 합니다. 로직 파일 없이 만든 모듈은
--original 을 붙여 점수 그대로 비교합니다.
--csv 를 주면 고객별 결과를 "고객번호,결과" CSV로 저장합니다.

    python3 isc/operations/gemv_i32/examples/check_output.py datasets/gemv_i32/credit_score.emu.bin
    python3 isc/operations/gemv_i32/examples/check_output.py 출력파일 --input 입력파일 --csv 점수.csv
"""
import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))       # 연산 폴더 (baseline.py)
from baseline import compute  # noqa: E402

DEFAULT_INPUT = "datasets/gemv_i32/credit_512x64.bin"


# [바꾸는 곳] logic.hpp 의 isc_decide 와 같은 규칙
def decide(score: int) -> int:
    return 2 if score >= 25000 else 1 if score >= 0 else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="gemv_i32 결과 확인")
    parser.add_argument("output", help="isc run --output 으로 받은 파일")
    parser.add_argument("--input", default=DEFAULT_INPUT, help="isc run --input 으로 넘긴 파일")
    parser.add_argument("--csv", metavar="FILE", help="고객번호,결과 CSV를 저장할 파일")
    parser.add_argument("--original", action="store_true", help="로직 파일 없이 만든 모듈: decide() 없이 비교")
    args = parser.parse_args()

    inp = open(args.input, "rb").read()
    got = open(args.output, "rb").read()
    score = np.frombuffer(got, "<i4")
    print("customers", len(score), "min", score.min(), "max", score.max(), "first", score[:3].tolist())
    if args.csv:
        with open(args.csv, "w") as f:
            f.write("customer,result\n")
            f.writelines(f"{i},{s}\n" for i, s in enumerate(score.tolist()))
        print("결과 저장:", args.csv)

    expected = np.frombuffer(compute(inp, {}), "<i4")
    if not args.original:
        expected = np.array([decide(int(s)) for s in expected], "<i4")
        print("result counts", {int(k): int(v) for k, v in zip(*np.unique(score, return_counts=True))})
    ok = got == expected.tobytes()
    print("MATCH" if ok else "MISMATCH")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
