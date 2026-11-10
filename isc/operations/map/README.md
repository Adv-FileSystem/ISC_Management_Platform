# `map` — 수치 컬럼을 다른 타입·단위로 변환 (Hook형)

| 개발 방식 | 사용하는 라이브러리 함수 | 검증 상태 |
|---|---|---|
| Hook형 | 없음 — 플랫폼이 생성하는 래퍼 커널(`kernel/map.cpp.tmpl`)에 사용자 함수 `isc_map`을 끼워 빌드 | 플랫폼 자체 테스트 ✅ (`f32→u8`) · 공식 라이브러리 테스트 해당 없음 (플랫폼 전용) (검증 장치 AMD Alveo U50 · [검증 기록](../../../verification/u50_vitis_libraries/results/map.md)) |

> **주의**: 출력이 1바이트 타입(`u8`, `i8`)이고 출력 길이가 65·129바이트이면, 에뮬레이션(`hw_emu`)에서 마지막 바이트가 `00`으로 나올 수 있습니다. 이때는 3절의 결과 확인(CPU 비교)으로 판정합니다.

## 1. 연산 예시

공장 설비의 센서 데이터를 모으는 데이터 개발자가 온도 센서 값(°C, `f32`)을 0.5 °C 단위 1바이트 코드(`u8`)로 줄여 장기 보관합니다. 0 °C 이하는 0, 127.5 °C 이상은 255로 포화시킵니다. 이 변환을 CPU 대신 FPGA에서 하고, 변환식은 사용자가 C++ `return` 한 줄로 씁니다.

### 1.1. 수집 파이프라인 코드 안에서 직접 변환하는 기존 방식

```python
import numpy as np

temps = np.fromfile("temps.f32", dtype="<f4")
codes = np.clip(temps * np.float32(2.0), 0, 255).astype(np.uint8)
codes.tofile("temps.u8")
```

### 1.2. ISC 전주기 시스템 기반의 방식

```bash
python3 isc/operations/map/examples/make_input.py --csv sensor.csv --column temp -o temps.bin   # CSV 한 열 → 연산 입력 형식
isc template map --type f32 --out-type u8 -o temp_code.hpp                                    # 로직 시작 파일 받기
# temp_code.hpp의 return 줄을 변환식으로 고침 (2.3)
isc run temp_code.hpp --input temps.bin --output codes.bin --target hw                          # FPGA가 변환 (모듈은 자동 생성)
```

- 입력: 한 타입 `T`의 값을 little-endian으로 이어 쓴 파일 (헤더 없음, 빈 파일 허용). 2.1의 스크립트가 CSV 한 열에서 만들어 줍니다
- 출력: 입력과 **같은 개수**의 `U` 값을 little-endian으로 이어 쓴 파일. 입력 `T`·출력 `U` 모두 `u8 u16 u32 u64 i8 i16 i32 i64 f32`
- 변환은 C++ 형 변환 규칙을 따릅니다(정수 나눗셈은 버림, `f32 → 정수`는 0 쪽으로 버림). **범위를 벗어난 `f32 → 정수` 변환은 결과가 정해지지 않으므로** 반드시 범위를 먼저 잘라 냅니다(2.3 예시)
- 개수를 바꾸는 변환(→ [`filter`](../filter/README.md)), 앞뒤 값을 쓰는 변환(이동 평균 등), 여러 열 결합은 지원하지 않습니다
- 실제로 따라 할 절차는 2절(개발 절차)과 3절(실행 방법)에 있습니다.

## 2. 개발 절차

모든 명령은 저장소 루트에서 실행합니다. 이 연산은 `VITIS_LIBRARIES`가 필요 없습니다. 결과 확인에 Python `numpy`가 필요합니다.

### 2.1. 입력 준비

**사용자가 할 일**: 변환할 열을 이 연산의 입력 형식(`T` 값의 나열)으로 변환합니다. CSV라면 `--csv`·`--column`으로 바로 변환되고(코드 수정 불필요), 다른 형식이거나 입력 타입이 `f32`가 아니면 스크립트를 고칩니다.

```bash
# 내 CSV의 한 열 → f32 값 나열
python3 isc/operations/map/examples/make_input.py --csv [CSV 파일] --column [열 이름] -o datasets/map/[이름].bin

# 또는 예시(-10 ~ 140 °C 온도 10,000개, 범위 밖 값 포함, 고정 시드)로 먼저 해 보기
python3 isc/operations/map/examples/make_input.py                     # → datasets/map/temps.bin
```

[`examples/make_input.py`](examples/make_input.py) 전체 코드입니다(주석 제외). `VALUE_FORMAT`(입력 타입)·`make_sample()`(예시 데이터)·`read_csv()`(CSV 읽기)만 고치고, `encode()`(입력 형식)와 `main()`은 그대로 둡니다. `VALUE_FORMAT`은 2.2의 `--type`과 같아야 합니다.

```python
import argparse
import csv
import random
import struct
from pathlib import Path

DEFAULT_OUTPUT = "datasets/map/temps.bin"
VALUE_FORMAT = "f"


def make_sample() -> list:
    rng = random.Random(9)
    return [round(rng.uniform(-10.0, 140.0), 2) for _ in range(10000)]


def read_csv(path: str, column: str) -> list:
    convert = float if VALUE_FORMAT == "f" else int
    with open(path, newline="", encoding="utf-8") as f:
        return [convert(row[column]) for row in csv.DictReader(f) if row[column].strip()]


def encode(values: list) -> bytes:
    return struct.pack(f"<{len(values)}{VALUE_FORMAT}", *values)


def main() -> None:
    parser = argparse.ArgumentParser(description="map 입력 파일 만들기")
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

수정 예 — 센서 로그(한 줄에 "시각,센서ID,온도")에서 온도만 읽기 (위 전체 코드에서 `make_sample()`만 이것으로 바꿈):

```python
def make_sample() -> list:
    with open("/data/factory/sensor_2026-11-10.log") as f:
        return [float(line.split(",")[2]) for line in f if line.strip()]
```

### 2.2. 모듈 만들기

**사용자가 할 일**: 입력 타입(`--type`, 2.1의 `VALUE_FORMAT`과 같게), 출력 타입(`--out-type`, 생략하면 입력과 같음)과 모듈 이름을 정합니다. **로직 파일 이름이 모듈 이름**이 됩니다(예: `temp_code.hpp` → 모듈 `temp_code`).

```bash
isc template map --type f32 --out-type u8 -o datasets/map/temp_code.hpp
```

시작 파일만 만들어지고 모듈은 아직 없습니다. 모듈은 2.4 또는 3절의 `isc run`이 이 파일로 만듭니다.

### 2.3. 로직 작성

**사용자가 할 일**: `datasets/map/temp_code.hpp`를 열어 `isc_map`의 **`return` 줄**을 변환식으로 바꾸고 `ISC-TODO` 주석을 지웁니다. 고친 뒤의 파일 전체입니다.

```cpp
// ISC Hook 시작 파일 — 프로파일 map (f32 값을 u8 값으로 변환)
//
// 입력: f32 값(little-endian)이 이어진 파일
// 출력: 변환된 u8 값, 입력과 같은 개수
//
// 고치는 곳: 아래 함수의 return 줄만. 함수 이름과 인자 이름(value)은 바꾸지 마십시오.
// 인자 타입(float)이 입력 값의 타입을, 반환 타입(uint8_t)이 출력 값의 타입을 정합니다.
// 예)  return value * 0.5f;
//      return value * 255.0f;
//      return value > 0.0f ? value : 0.0f;
// 쓸 수 없는 것: 동적 할당, 파일 입출력, HLS INTERFACE 지시어.
#pragma once

#include <ap_int.h>
#include <stdint.h>

inline uint8_t isc_map(float value) {
    return value <= 0.0f ? (uint8_t)0 : value >= 127.5f ? (uint8_t)255 : (uint8_t)(value * 2.0f);
}
```

- 위 식은 먼저 범위를 잘라 낸 뒤 `value * 2.0f`를 정수로 바꿉니다(0 쪽으로 버림). 범위 검사 없이 `(uint8_t)(value * 2.0f)`만 쓰면 음수·큰 값에서 결과가 정해지지 않습니다.
- 함수 이름 `isc_map`과 인자 이름 `value`, 인자·반환 타입은 바꾸지 않습니다. 타입을 바꾸려면 `--type`/`--out-type`으로 시작 파일을 다시 받습니다.
- 다른 변환 예: 섭씨 → 화씨 `f32 → f32`: `return value * 1.8f + 32.0f;` / 밀리초 → 초 `u32 → u32`: `return value / 1000;` / `i32 → u8` 범위 자르기: `return value < 0 ? (uint8_t)0 : value > 255 ? (uint8_t)255 : (uint8_t)value;`
- 쓸 수 없는 것: 동적 할당, 파일·소켓 입출력, `#pragma HLS INTERFACE`. 보조 함수나 상수를 추가하는 것은 됩니다.

### 2.4. 검사

**사용자가 할 일**: 2.3에서 고친 로직 파일로 모듈을 만들고(이미 있으면 갱신), 빌드하기 전에 검사합니다.

```bash
isc new-custom --from datasets/map/temp_code.hpp            # 로직 파일로 모듈 만들기(또는 갱신) + 정적 검사
isc check temp_code
```

`problem`이 있으면 빌드·실행이 진행되지 않습니다. FPGA 회로로 변환(합성)되는지는 3절의 첫 hw_emu 빌드에서 확인됩니다. 빌드가 실패하면 오류 메시지에 빌드 로그의 끝부분이 함께 표시되고, 전체 로그는 `artifacts/cache/` 아래 해당 빌드 폴더의 `build.log`에 있습니다.

## 3. 실행 방법

**사용자가 할 일**: `--input`에 2.1의 파일, `--output`에 결과를 저장할 경로를 지정합니다. 결과 확인 스크립트([`examples/check_output.py`](examples/check_output.py))의 `[바꾸는 곳]` `convert()`를 2.3과 **같은 변환식**으로, `IN_DTYPE`·`OUT_DTYPE`을 입출력 타입으로 맞춥니다(예시 그대로면 고칠 것 없음).

```bash
# 에뮬레이션 실행 — 로직 파일을 넘기면 모듈 갱신 → 정적 검사 → 빌드(처음 한 번 수 분) → 실행 → 기록
isc run datasets/map/temp_code.hpp --input datasets/map/temps.bin --output datasets/map/result.emu.bin

# 결과 확인: 같은 변환식을 numpy float32로 계산한 결과와 비교 — MATCH면 정상
python3 isc/operations/map/examples/check_output.py datasets/map/result.emu.bin --input datasets/map/temps.bin

# FPGA 카드 실행 (hw) (처음 한 번 hw 빌드 약 45분) — 카드 출력이 에뮬레이션 출력과 같은지 플랫폼이 자동 비교
isc run datasets/map/temp_code.hpp --input datasets/map/temps.bin --output datasets/map/result.hw.bin --target hw
python3 isc/operations/map/examples/check_output.py datasets/map/result.hw.bin --input datasets/map/temps.bin

# 실행 기록
isc records --module temp_code
isc show [실행 ID]
```

- **로직을 바꿀 때**: `temp_code.hpp`를 고친 뒤 위 `isc run` 명령을 다시 실행하면 모듈의 `logic.hpp`가 갱신되고(`updated`) 다시 빌드합니다. 상수 하나만 바꿔도 새 빌드이므로 hw_emu에서 확인한 뒤 hw로 실행합니다. `check_output.py`의 `convert()`도 같이 바꿉니다.
- 결과 확인에서 일부 값만 1 차이 나면 CPU 쪽을 `float64`로 계산한 경우입니다. `np.float32`로 계산합니다(스크립트가 이렇게 함).
- 입력 파일 크기가 값 크기의 배수가 아니면 끝에 남는 바이트는 오류 없이 무시됩니다.
- 터미널 출력을 파일로도 남기려면 명령 뒤에 `2>&1 | tee [로그 파일]`을 붙입니다.
