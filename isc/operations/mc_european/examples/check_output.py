#!/usr/bin/env python3
"""mc_european 결과 확인 (README 3).

옵션마다 FPGA 가격을 Black-Scholes 해석해(연산 폴더의 baseline.py)와 비교합니다.
공식 테스트의 판정식 |가격 - 정답| / 기초가 S 가 1% 이하이면 PASS입니다. 가격 대비 오차(|d|/BS)도 함께 출력합니다.

    python3 isc/operations/mc_european/examples/check_output.py datasets/mc_european/option_book.emu.bin
    python3 isc/operations/mc_european/examples/check_output.py 출력파일 --input 입력파일
"""
import argparse
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))       # 연산 폴더 (baseline.py, mc_format.py)
from baseline import compute  # noqa: E402
from mc_format import options  # noqa: E402

DEFAULT_INPUT = "datasets/mc_european/option_book_8.bin"


def main() -> None:
    parser = argparse.ArgumentParser(description="mc_european 결과 확인")
    parser.add_argument("output", help="isc run --output 으로 받은 파일")
    parser.add_argument("--input", default=DEFAULT_INPUT, help="isc run --input 으로 넘긴 파일")
    args = parser.parse_args()

    inp = open(args.input, "rb").read()
    got = open(args.output, "rb").read()
    opts = options(inp)
    fpga = struct.unpack(f"<{len(opts)}f", got)
    bs = struct.unpack(f"<{len(opts)}f", compute(inp, {}))
    worst = 0.0
    for (s, k, sig, q, r, t, kind, _), g, e in zip(opts, fpga, bs):
        err_s = abs(g - e) / s                      # 공식 테스트 판정식: |가격 - 정답| / 기초가
        worst = max(worst, err_s)
        print(f"{'put ' if kind else 'call'} K={k:4.0f} sigma={sig:.2f} T={t:g}: fpga={g:8.4f} BS={e:8.4f} "
              f"|d|/S={err_s * 100:.3f}%  |d|/BS={abs(g - e) / e * 100:.3f}%")
    ok = worst <= 0.01
    print(f"max |d|/S = {worst * 100:.3f}%  ->", "PASS" if ok else "FAIL", "(official rule: <= 1%)")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
