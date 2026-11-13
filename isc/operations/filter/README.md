# `filter` — 조건에 맞는 값만 추출 (Hook형)

| 개발 방식 | 사용하는 라이브러리 함수 | 검증 상태 |
|---|---|---|
| Hook형 | 없음 — 플랫폼이 생성하는 래퍼 커널(`kernel/filter.cpp.tmpl`)에 사용자 함수 `isc_filter`를 끼워 빌드 | 플랫폼 자체 테스트 ✅ (`u32`) · 공식 라이브러리 테스트 해당 없음 (플랫폼 전용) (검증 장치 AMD Alveo U50 · [검증 기록](../../../verification/u50_vitis_libraries/results/filter.md)) |

> **주의**: 값이 1바이트 타입(`u8`, `i8`)이고 행 수가 65·129이면, 에뮬레이션(`hw_emu`)에서 마지막 1행이 빠질 수 있습니다. 이때는 3절의 결과 확인(CPU 비교)으로 판정합니다. 이 문서의 예시(`u32`)는 해당하지 않습니다.

## 1. 연산 예시

사내 매출 리포트를 만드는 백엔드 개발자가 주문 금액(원 단위 정수) 컬럼에서 1,000원 이상 5,000원 미만인 주문만 뽑습니다. 이 조건 검사를 CPU 대신 FPGA에서 하고, 조건은 사용자가 C++ `return` 한 줄로 씁니다.

### 1.1. 리포트 코드 안에서 직접 거르는 기존 방식

```python
import pandas as pd

orders = pd.read_csv("orders.csv")
picked = orders[(orders.amount >= 1000) & (orders.amount < 5000)]
```

### 1.2. ISC 전주기 시스템 기반의 방식

```bash
python3 isc/operations/filter/examples/make_input.py --csv orders.csv --column amount -o amounts.bin   # CSV 한 열 → 연산 입력 형식
isc template filter --type u32 -o amount_filter.hpp                                                  # 로직 시작 파일 받기
# amount_filter.hpp의 return 줄을 "return value >= 1000 && value < 5000;"로 고침 (2.3)
isc run amount_filter.hpp --input amounts.bin --output picked.bin --target hw                          # FPGA가 조건 검사 (모듈은 자동 생성)
```

- 입력: 한 타입 `T`의 값을 little-endian으로 이어 쓴 파일 (헤더 없음, 빈 파일 허용). 2.1의 스크립트가 CSV 한 열에서 만들어 줍니다. 지원 타입 `u8 u16 u32 u64 i8 i16 i32 i64 f32`
- 출력: 함수가 `true`를 돌려준 값만, 입력 순서대로, 같은 타입 `T`로 이어 쓴 파일. 1.1의 `picked.amount`와 같은 값·순서
- 값 하나만 보는 조건만 됩니다. 여러 열을 함께 보는 조건(행 단위), 앞뒤 값을 보는 조건, 출력 타입 변경(→ [`map`](../map/README.md))은 지원하지 않습니다
- 실제로 따라 할 절차는 2절(개발 절차)과 3절(실행 방법)에 있습니다.

## 2. 개발 절차

모든 명령은 저장소 루트에서 실행합니다. 이 연산은 `VITIS_LIBRARIES`가 필요 없습니다.

### 2.1. 입력 준비

**사용자가 할 일**: 거를 열을 이 연산의 입력 형식(`T` 값의 나열)으로 변환합니다. CSV라면 `--csv`·`--column`으로 바로 변환되고(코드 수정 불필요), 다른 형식이거나 타입이 `u32`가 아니면 스크립트를 고칩니다.

```bash
# 내 CSV의 한 열 → u32 값 나열
python3 isc/operations/filter/examples/make_input.py --csv [CSV 파일] --column [열 이름] -o datasets/filter/[이름].bin

# 또는 예시(주문 금액 10,000건, 0~9,999원, 고정 시드)로 먼저 해 보기
python3 isc/operations/filter/examples/make_input.py                  # → datasets/filter/amounts.bin
```

[`examples/make_input.py`](examples/make_input.py) 전체 코드입니다(주석 제외). `VALUE_FORMAT`(값 타입)·`make_sample()`(예시 데이터)·`read_csv()`(CSV 읽기)만 고치고, `encode()`(입력 형식)와 `main()`은 그대로 둡니다. `VALUE_FORMAT`은 2.2의 `--type`과 같아야 합니다(`struct` 코드: `u8 B`, `u16 H`, `u32 I`, `u64 Q`, `i8 b`, `i16 h`, `i32 i`, `i64 q`, `f32 f`).

```python
import argparse
import csv
import random
import struct
from pathlib import Path

DEFAULT_OUTPUT = "datasets/filter/amounts.bin"
VALUE_FORMAT = "I"


def make_sample() -> list:
    rng = random.Random(5)
    return [rng.randint(0, 9999) for _ in range(10000)]


def read_csv(path: str, column: str) -> list:
    convert = float if VALUE_FORMAT == "f" else int
    with open(path, newline="", encoding="utf-8") as f:
        return [convert(row[column]) for row in csv.DictReader(f) if row[column].strip()]


def encode(values: list) -> bytes:
    return struct.pack(f"<{len(values)}{VALUE_FORMAT}", *values)


def main() -> None:
    parser = argparse.ArgumentParser(description="filter 입력 파일 만들기")
    parser.add_argument("-o", "--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--csv", help="실제 데이터 CSV 파일 (머리글 줄 필요)")
    parser.add_argument("--column", help="--csv에서 읽을 열 이름")
    args = parser.parse_args()
    if args.csv and not args.column:
        parser.error("--csv에는 --column이 필요합니다")
    values = read_csv(args.csv, args.column) if args.csv else make_sample()
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(encode(values))
    print(len(values), "values ->", out)


if __name__ == "__main__":
    main()
```

수정 예 — numpy 배열로 저장해 둔 금액 열을 읽기 (위 전체 코드에서 `make_sample()`만 이것으로 바꿈):

```python
def make_sample() -> list:
    import numpy as np
    return np.load("/data/sales/amounts.npy").astype(int).tolist()
```

### 2.2. 모듈 만들기

**사용자가 할 일**: 값 타입(`--type`, 2.1의 `VALUE_FORMAT`과 같게)과 모듈 이름을 정합니다. **로직 파일 이름이 모듈 이름**이 됩니다(예: `amount_filter.hpp` → 모듈 `amount_filter`).

```bash
isc template filter --type u32 -o datasets/filter/amount_filter.hpp
```

시작 파일만 만들어지고 모듈은 아직 없습니다. 모듈은 2.4 또는 3절의 `isc run`이 이 파일로 만듭니다.

### 2.3. 로직 작성

**사용자가 할 일**: `datasets/filter/amount_filter.hpp`를 열어 `isc_filter`의 **`return` 줄**을 조건으로 바꾸고 `ISC-TODO` 주석을 지웁니다. 고친 뒤의 파일 전체입니다.

```cpp
// ISC Hook 시작 파일 — 프로파일 filter (u32 값의 조건 필터)
//
// 입력: u32 값(little-endian)이 이어진 파일
// 출력: 함수가 true 를 돌려준 u32 값만, 입력 순서대로
//
// 고치는 곳: 아래 함수의 return 줄만. 함수 이름과 인자 이름(value)은 바꾸지 마십시오.
// 인자 타입(uint32_t)이 입력 값의 타입을 정합니다.
// 예)  return value >= 1000;
//      return value >= 100 && value < 200;
//      return (value & 1) == 0;
// 쓸 수 없는 것: 동적 할당, 파일 입출력, HLS INTERFACE 지시어.
#pragma once

#include <ap_int.h>
#include <stdint.h>

inline bool isc_filter(uint32_t value) {
    return value >= 1000 && value < 5000;
}
```

- 함수 이름 `isc_filter`와 인자 이름 `value`는 바꾸지 않습니다. 인자 타입(`uint32_t`)은 2.2의 `--type`이 정하며, 타입을 바꾸려면 시작 파일을 다시 받습니다.
- 조건에는 비교·논리·비트 연산과 상수를 씁니다. 예: `return value >= 1000;`, `return (value & 1) == 0;`, `return value == value;`(`f32`에서 NaN 제외). 실수(`f32`)면 상수에 `f`를 붙입니다(`3.0f`).
- 쓸 수 없는 것: 동적 할당(`new`, `malloc`), 파일·소켓 입출력(`fopen`, `open`, `socket`), `#pragma HLS INTERFACE`. 보조 함수나 상수를 추가하는 것은 됩니다.

### 2.4. 검사

**사용자가 할 일**: 2.3에서 고친 로직 파일로 모듈을 만들고(이미 있으면 갱신), 빌드하기 전에 검사합니다.

```bash
isc new-custom --from datasets/filter/amount_filter.hpp     # 로직 파일로 모듈 만들기(또는 갱신) + 정적 검사
isc check amount_filter
```

`problem`이 있으면 빌드·실행이 진행되지 않습니다. `ISC-TODO`를 지우지 않았으면 `warning`이 나옵니다(빌드는 진행됨). FPGA 회로로 변환(합성)되는지는 3절의 첫 hw_emu 빌드에서 확인됩니다. 빌드가 실패하면 오류 메시지에 빌드 로그의 끝부분이 함께 표시되고, 전체 로그는 `artifacts/cache/` 아래 해당 빌드 폴더의 `build.log`에 있습니다.

## 3. 실행 방법

**사용자가 할 일**: `--input`에 2.1의 파일, `--output`에 결과를 저장할 경로를 지정합니다. 결과 확인 스크립트([`examples/check_output.py`](examples/check_output.py))의 `[바꾸는 곳]` `keep()`을 2.3의 조건과 **같은 조건**으로, `VALUE_FORMAT`을 값 타입으로 맞춥니다(예시 그대로면 고칠 것 없음).

```bash
# 에뮬레이션 실행 — 로직 파일을 넘기면 모듈 갱신 → 정적 검사 → 빌드(처음 한 번 수 분) → 실행 → 기록
isc run datasets/filter/amount_filter.hpp --input datasets/filter/amounts.bin --output datasets/filter/result.emu.bin

# 결과 확인: 같은 조건을 Python으로 적용한 결과와 값·순서 비교 — MATCH면 정상
python3 isc/operations/filter/examples/check_output.py datasets/filter/result.emu.bin --input datasets/filter/amounts.bin

# FPGA 카드 실행 (hw) (처음 한 번 hw 빌드 수십 분) — 카드 출력이 에뮬레이션 출력과 같은지 플랫폼이 자동 비교
isc run datasets/filter/amount_filter.hpp --input datasets/filter/amounts.bin --output datasets/filter/result.hw.bin --target hw
python3 isc/operations/filter/examples/check_output.py datasets/filter/result.hw.bin --input datasets/filter/amounts.bin

# 실행 기록
isc records --module amount_filter
isc show [실행 ID]
```

- **로직을 바꿀 때**: `amount_filter.hpp`를 고친 뒤 위 `isc run` 명령을 다시 실행하면 모듈의 `logic.hpp`가 갱신되고(`updated`) 바뀐 로직으로 다시 빌드합니다. 상수 하나만 바꿔도 새 빌드이므로 hw_emu에서 확인한 뒤 hw로 실행합니다. `check_output.py`의 `keep()`도 같이 바꿉니다.
- 에뮬레이션의 `validation : PASS`는 hw_emu가 자기 자신을 기준으로 삼았다는 뜻일 뿐입니다. 조건이 맞는지는 결과 확인으로 판정합니다.
- 입력 파일 크기가 값 크기의 배수가 아니면 끝에 남는 바이트는 오류 없이 무시됩니다.
- 터미널 출력을 파일로도 남기려면 명령 뒤에 `2>&1 | tee [로그 파일]`을 붙입니다.
