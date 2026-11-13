#!/usr/bin/env python3
"""graph_twohop 결과 확인 (README 3).

FPGA 출력(u32 score[P])을, CPU 참조(baseline.py, 이웃 목록 순회)로 센 공통 이웃 수에
같은 규칙을 Python으로 적용한 결과와 값·순서까지 비교합니다.
로직 파일(isc_score)의 규칙을 바꾸면 아래 [바꾸는 곳]의 score()도 같은 규칙으로 바꿉니다.
로직 파일 없이 만든 모듈의 출력이면 --original 을 붙입니다 (공통 이웃 수 그대로와 비교).

    python3 isc/operations/graph_twohop/examples/check_output.py datasets/graph_twohop/fraud_link.emu.bin
    python3 isc/operations/graph_twohop/examples/check_output.py 출력파일 --input 입력파일 [--original]
"""
import argparse
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))       # 연산 폴더 (baseline.py, twohop_format.py)
from baseline import compute  # noqa: E402
from twohop_format import parse  # noqa: E402

DEFAULT_INPUT = "datasets/graph_twohop/accounts_devices.bin"


# ── [바꾸는 곳] 로직 파일의 isc_score와 같은 규칙 ─────────────────────
def score(src: int, dst: int, count: int) -> int:
    return count * 10 if count >= 3 else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="graph_twohop 결과 확인")
    parser.add_argument("output", help="isc run --output 으로 받은 파일")
    parser.add_argument("--input", default=DEFAULT_INPUT, help="isc run --input 으로 넘긴 파일")
    parser.add_argument("--original", action="store_true",
                        help="로직 파일 없이 만든 모듈의 출력 (공통 이웃 수 그대로와 비교)")
    args = parser.parse_args()

    inp = open(args.input, "rb").read()
    pairs = parse(inp)[4]
    raw = compute(inp, {})
    counts = struct.unpack(f"<{len(pairs)}I", raw)
    expected = list(counts) if args.original else [
        score(s, d, c) & 0xFFFFFFFF for (s, d), c in zip(pairs, counts)]
    out = open(args.output, "rb").read()
    got = list(struct.unpack(f"<{len(out) // 4}I", out))

    flagged = [(s, d, v) for (s, d), v in zip(pairs, got) if v]
    print(f"pairs={len(got)} nonzero={len(flagged)} max={max(got, default=0)}")
    for s, d, v in sorted(flagged, key=lambda item: -item[2])[:5]:
        print(f"  ({s}, {d}) -> {v}")
    print("MATCH" if got == expected else "MISMATCH")
    raise SystemExit(0 if got == expected else 1)


if __name__ == "__main__":
    main()
