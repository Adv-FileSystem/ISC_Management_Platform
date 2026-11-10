# `aggregate` — 조건에 맞는 값의 건수·합계·최솟값·최댓값 (Hook형)

| 개발 방식 | 사용하는 라이브러리 함수 | 검증 상태 |
|---|---|---|
| Hook형 | 없음 — 플랫폼이 생성하는 래퍼 커널(`kernel/aggregate.cpp.tmpl`)에 사용자 함수 `isc_where`를 끼워 빌드 | 플랫폼 자체 테스트 ✅ (`i32`) · 공식 라이브러리 테스트 해당 없음 (플랫폼 전용) (검증 장치 AMD Alveo U50 · [검증 기록](../../../verification/u50_vitis_libraries/results/aggregate.md)) |

## 1. 연산 예시

결제 데이터를 분석하는 데이터 분석가가 1,000원 미만 소액 거래(환불 포함)의 건수·합계·최솟값·최댓값을 매일 산출합니다. 거래 금액은 원 단위 정수이고 환불은 음수입니다. 이 조건부 집계를 CPU 대신 FPGA에서 하고, 집계할 조건은 사용자가 C++ `return` 한 줄로 씁니다.

### 1.1. 분석 코드 안에서 직접 집계하는 기존 방식

```python
import pandas as pd

tx = pd.read_csv("transactions.csv")["amount"]
small = tx[tx < 1000]
print(small.count(), small.sum(), small.min(), small.max())
```

### 1.2. ISC 전주기 시스템 기반의 방식

```bash
python3 isc/operations/aggregate/examples/make_input.py --csv transactions.csv --column amount -o tx.bin   # CSV 한 열 → 연산 입력 형식
isc template aggregate --type i32 -o small_tx.hpp                                                        # 로직 시작 파일 받기
# small_tx.hpp의 return 줄을 "return value < 1000;"로 고침 (2.3)
isc run small_tx.hpp --input tx.bin --output small_tx.stats --target hw                                    # FPGA가 조건부 집계 (모듈은 자동 생성)
```

- 입력: 한 정수 타입 `T`의 값을 little-endian으로 이어 쓴 파일 (헤더 없음, 빈 파일 허용). 2.1의 스크립트가 CSV 한 열에서 만들어 줍니다. **정수만** 지원(`u8 u16 u32 u64 i8 i16 i32 i64`, `f32` 불가). 실수는 고정 소수점 정수로 바꿔 넣습니다(예: 0.01 단위 → ×100)
- 출력: 고정 32바이트, little-endian 64비트 값 4개 `count, sum, min, max`. 부호 있는 `T`면 `sum`·`min`·`max`를 부호 있는 `i64`로 읽습니다(`struct` `"<Qqqq"`), 부호 없는 `T`면 `"<QQQQ"`. 1.1의 네 값과 같습니다
- 조건을 만족한 값이 없으면 네 값 모두 0입니다. `sum`은 64비트에서 넘치면 감깁니다
- 평균은 `sum / count`로 직접 계산합니다. 분산 같은 다른 통계, 그룹별 집계(GROUP BY), 여러 열은 지원하지 않습니다
- 실제로 따라 할 절차는 2절(개발 절차)과 3절(실행 방법)에 있습니다.

## 2. 개발 절차

모든 명령은 저장소 루트에서 실행합니다. 이 연산은 `VITIS_LIBRARIES`가 필요 없습니다.

### 2.1. 입력 준비

**사용자가 할 일**: 집계할 열을 이 연산의 입력 형식(`T` 값의 나열)으로 변환합니다. CSV라면 `--csv`·`--column`으로 바로 변환되고(코드 수정 불필요), 다른 형식이거나 타입이 `i32`가 아니면 스크립트를 고칩니다.

```bash
# 내 CSV의 한 열 → i32 값 나열
python3 isc/operations/aggregate/examples/make_input.py --csv [CSV 파일] --column [열 이름] -o datasets/aggregate/[이름].bin

# 또는 예시(거래 10,000건, -2,000 ~ 20,000원, 음수 = 환불, 고정 시드)로 먼저 해 보기
python3 isc/operations/aggregate/examples/make_input.py               # → datasets/aggregate/tx.bin
```

[`examples/make_input.py`](examples/make_input.py) 전체 코드입니다(주석 제외). `VALUE_FORMAT`(값 타입)·`make_sample()`(예시 데이터)·`read_csv()`(CSV 읽기)만 고치고, `encode()`(입력 형식)와 `main()`은 그대로 둡니다. `VALUE_FORMAT`은 2.2의 `--type`과 같아야 합니다.

```python
import argparse
import csv
import random
import struct
from pathlib import Path

DEFAULT_OUTPUT = "datasets/aggregate/tx.bin"
VALUE_FORMAT = "i"


def make_sample() -> list:
    rng = random.Random(21)
    return [rng.randint(-2000, 20000) for _ in range(10000)]


def read_csv(path: str, column: str) -> list:
    convert = float if VALUE_FORMAT == "f" else int
    with open(path, newline="", encoding="utf-8") as f:
        return [convert(row[column]) for row in csv.DictReader(f) if row[column].strip()]


def encode(values: list) -> bytes:
    return struct.pack(f"<{len(values)}{VALUE_FORMAT}", *values)


def main() -> None:
    parser = argparse.ArgumentParser(description="aggregate 입력 파일 만들기")
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

수정 예 — 소수점이 있는 금액(예: 12.50달러)을 센트 단위 정수로 바꿔 읽기 (위 전체 코드에서 `read_csv()`만 이것으로 바꿈):

```python
def read_csv(path: str, column: str) -> list:
    with open(path, newline="", encoding="utf-8") as f:
        return [round(float(row[column]) * 100) for row in csv.DictReader(f) if row[column].strip()]
```

### 2.2. 모듈 만들기

**사용자가 할 일**: 값 타입(`--type`, 정수만, 2.1의 `VALUE_FORMAT`과 같게)과 모듈 이름을 정합니다. **로직 파일 이름이 모듈 이름**이 됩니다(예: `small_tx.hpp` → 모듈 `small_tx`).

```bash
isc template aggregate --type i32 -o datasets/aggregate/small_tx.hpp
```

시작 파일만 만들어지고 모듈은 아직 없습니다. 모듈은 2.4 또는 3절의 `isc run`이 이 파일로 만듭니다.

### 2.3. 로직 작성

**사용자가 할 일**: `datasets/aggregate/small_tx.hpp`를 열어 `isc_where`의 **`return` 줄**을 집계할 값의 조건으로 바꾸고 `ISC-TODO` 주석을 지웁니다. 고친 뒤의 파일 전체입니다.

```cpp
// ISC Hook 시작 파일 — 프로파일 aggregate (i32 값의 조건부 집계)
//
// 입력: i32 값(little-endian)이 이어진 파일
// 출력: 고정 32바이트: little-endian u64 네 개 (count, sum, min, max). 조건을 만족한 값이 없으면 모두 0
//
// 고치는 곳: 아래 함수의 return 줄만. 함수 이름과 인자 이름(value)은 바꾸지 마십시오.
// 인자 타입(int32_t)이 입력 값의 타입을 정합니다. 함수가 true 를 돌려준 값만 집계됩니다.
// 예)  return value >= 1000;
//      return value >= 100 && value < 200;
//      return (value & 1) == 0;
// 쓸 수 없는 것: 동적 할당, 파일 입출력, HLS INTERFACE 지시어.
#pragma once

#include <ap_int.h>
#include <stdint.h>

inline bool isc_where(int32_t value) {
    return value < 1000;
}
```

- 함수 이름 `isc_where`와 인자 이름 `value`는 바꾸지 않습니다. 모든 값을 집계하려면 `return true;`로 두고 `ISC-TODO` 주석만 지웁니다.
- 다른 조건 예: `return value >= 100 && value < 200;`, `return (value & 1) == 0;`(짝수). 여러 조건의 집계가 필요하면 조건마다 로직 파일(모듈)을 따로 만듭니다.
- 쓸 수 없는 것: 동적 할당, 파일·소켓 입출력, `#pragma HLS INTERFACE`. 보조 함수나 상수를 추가하는 것은 됩니다.

### 2.4. 검사

**사용자가 할 일**: 2.3에서 고친 로직 파일로 모듈을 만들고(이미 있으면 갱신), 빌드하기 전에 검사합니다.

```bash
isc new-custom --from datasets/aggregate/small_tx.hpp       # 로직 파일로 모듈 만들기(또는 갱신) + 정적 검사
isc check small_tx
```

`problem`이 있으면 빌드·실행이 진행되지 않습니다. 인자 타입이 실수(`float`)면 `unsupported type` 오류가 나므로 정수 타입으로 바꿉니다.

## 3. 실행 방법

**사용자가 할 일**: `--input`에 2.1의 파일, `--output`에 결과를 저장할 경로를 지정합니다. 결과 확인 스크립트([`examples/check_output.py`](examples/check_output.py))의 `[바꾸는 곳]` `selected()`를 2.3과 **같은 조건**으로, `VALUE_FORMAT`·`RESULT_FORMAT`을 값 타입으로 맞춥니다(예시 그대로면 고칠 것 없음).

```bash
# 에뮬레이션 실행 — 로직 파일을 넘기면 모듈 갱신 → 정적 검사 → 빌드(처음 한 번 수 분) → 실행 → 기록
isc run datasets/aggregate/small_tx.hpp --input datasets/aggregate/tx.bin --output datasets/aggregate/result.emu.bin

# 결과 확인: 같은 조건으로 Python에서 집계한 결과와 비교 — MATCH면 정상
python3 isc/operations/aggregate/examples/check_output.py datasets/aggregate/result.emu.bin --input datasets/aggregate/tx.bin

# FPGA 카드 실행 (hw) (처음 한 번 hw 빌드 수십 분) — 카드 출력이 에뮬레이션 출력과 같은지 플랫폼이 자동 비교
isc run datasets/aggregate/small_tx.hpp --input datasets/aggregate/tx.bin --output datasets/aggregate/result.hw.bin --target hw
python3 isc/operations/aggregate/examples/check_output.py datasets/aggregate/result.hw.bin --input datasets/aggregate/tx.bin

# 실행 기록
isc records --module small_tx
isc show [실행 ID]
```

- **로직을 바꿀 때**: `small_tx.hpp`를 고친 뒤 위 `isc run` 명령을 다시 실행하면 모듈의 `logic.hpp`가 갱신되고(`updated`) 다시 빌드합니다. 상수 하나만 바꿔도 새 빌드이므로 hw_emu에서 확인한 뒤 hw로 실행합니다. `check_output.py`의 `selected()`도 같이 바꿉니다.
- `sum`이 매우 큰 양수로 보이면 부호 있는 입력의 결과를 `"<QQQQ"`로 읽은 경우입니다. `"<Qqqq"`로 읽습니다.
- 터미널 출력을 파일로도 남기려면 명령 뒤에 `2>&1 | tee [로그 파일]`을 붙입니다.
