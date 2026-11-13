#!/usr/bin/env python3
"""mc_european 결과 확인 (README 3).

옵션마다 FPGA 가격을 Black-Scholes 해석해(연산 폴더의 baseline.py)와 비교합니다. 해석해는 입력의 변동성에
volatility() 를 적용한 값으로 계산합니다. 판정식 |가격 - 정답| / 기초가 S 가 1% 이하이면 PASS입니다.
가격 대비 오차(|d|/BS)도 함께 출력합니다.
volatility() 는 모듈의 logic.hpp 에 쓴 isc_volatility 와 같은 규칙이어야 합니다. 로직 파일 없이 만든 모듈은
--original 을 붙여 입력의 변동성 그대로 비교합니다.

    python3 isc/operations/mc_european/examples/check_output.py datasets/mc_european/option_book.emu.bin
    python3 isc/operations/mc_european/examples/check_output.py 출력파일 --input 입력파일
"""
import argparse
import struct
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))       # 연산 폴더 (baseline.py, mc_format.py)
from baseline import compute  # noqa: E402
from mc_format import options  # noqa: E402

DEFAULT_INPUT = "datasets/mc_european/option_book_8.bin"


# [바꾸는 곳] logic.hpp 의 isc_volatility 와 같은 규칙 (FPGA와 같게 float32로 계산)
def volatility(sigma):
    return sigma * np.float32(1.2)


def stressed(inp: bytes) -> bytes:
    """입력 파일의 변동성(옵션마다 3번째 f32)에 volatility() 를 적용한 입력."""
    head, count = inp[:8], struct.unpack_from("<I", inp)[0]
    table = np.frombuffer(inp, "<f4", count * 8, 8).reshape(count, 8).copy()
    table[:, 2] = [volatility(np.float32(v)) for v in table[:, 2]]
    return head + table.astype("<f4").tobytes()


def main() -> None:
    parser = argparse.ArgumentParser(description="mc_european 결과 확인")
    parser.add_argument("output", help="isc run --output 으로 받은 파일")
    parser.add_argument("--input", default=DEFAULT_INPUT, help="isc run --input 으로 넘긴 파일")
    parser.add_argument("--original", action="store_true", help="로직 파일 없이 만든 모듈: volatility() 없이 비교")
    args = parser.parse_args()

    inp = open(args.input, "rb").read()
    if not args.original:
        inp = stressed(inp)
    got = open(args.output, "rb").read()
    opts = options(inp)
    fpga = struct.unpack(f"<{len(opts)}f", got)
    bs = struct.unpack(f"<{len(opts)}f", compute(inp, {}))
    worst = 0.0
    for (s, k, sig, q, r, t, kind, _), g, e in zip(opts, fpga, bs):
        err_s = abs(g - e) / s                      # 판정식: |가격 - 정답| / 기초가
        worst = max(worst, err_s)
        print(f"{'put ' if kind else 'call'} K={k:4.0f} sigma={sig:.2f} T={t:g}: fpga={g:8.4f} BS={e:8.4f} "
              f"|d|/S={err_s * 100:.3f}%  |d|/BS={abs(g - e) / e * 100:.3f}%")
    ok = worst <= 0.01
    print(f"max |d|/S = {worst * 100:.3f}%  ->", "PASS" if ok else "FAIL", "(rule: <= 1%)")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
