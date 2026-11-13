#!/usr/bin/env python3
"""graph_renumber 결과 확인 (README 3).

FPGA 출력(u32 clusters + i32 new_id[N])을 연산 폴더의 CPU 참조(baseline.py)와 바이트 비교하고,
원본 ID와 새 번호가 일대일인지 확인합니다. --map 을 주면 원본 ID → 새 번호 대응표를 CSV로 저장합니다.

    python3 isc/operations/graph_renumber/examples/check_output.py datasets/graph_renumber/member_renumber.emu.bin
    python3 isc/operations/graph_renumber/examples/check_output.py 출력파일 --input 입력파일 --map 대응표.csv
"""
import argparse
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))       # 연산 폴더 (baseline.py)
from baseline import compute  # noqa: E402

DEFAULT_INPUT = "datasets/graph_renumber/members_5000.bin"


def main() -> None:
    parser = argparse.ArgumentParser(description="graph_renumber 결과 확인")
    parser.add_argument("output", help="isc run --output 으로 받은 파일")
    parser.add_argument("--input", default=DEFAULT_INPUT, help="isc run --input 으로 넘긴 파일")
    parser.add_argument("--map", metavar="CSV", help="원본 ID,새 번호 대응표를 저장할 파일")
    args = parser.parse_args()

    inp = open(args.input, "rb").read()
    got = open(args.output, "rb").read()
    n = struct.unpack_from("<I", inp)[0]
    old = struct.unpack_from(f"<{n}i", inp, 4)
    clusters = struct.unpack_from("<I", got)[0]
    new = struct.unpack_from(f"<{n}i", got, 4)

    pairs = set(zip(old, new))
    one_to_one = len(pairs) == len(set(old)) == len({b for _, b in pairs}) and set(new) == set(range(clusters))
    print("ids", n, "distinct old", len(set(old)), "clusters", clusters, "one-to-one", one_to_one)
    print("first 5:", [f"{a}->{b}" for a, b in list(zip(old, new))[:5]])
    if args.map:
        with open(args.map, "w") as f:
            f.write("old_id,new_id\n")
            f.writelines(f"{a},{b}\n" for a, b in sorted(pairs, key=lambda p: p[1]))
        print("대응표 저장:", args.map)

    ok = got == compute(inp, {}) and one_to_one
    print("MATCH" if ok else "MISMATCH")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
