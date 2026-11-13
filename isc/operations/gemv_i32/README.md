# `gemv_i32` — 대출 심사: 고객 점수를 계산하고 승인·보류·거절 판정 (연산 선택형 + 사용자 로직)

| 개발 방식 | 사용하는 라이브러리 함수 | 검증 상태 |
|---|---|---|
| 연산 선택형 + 사용자 로직 `isc_decide` | Vitis BLAS L1 `xf::blas::gemv` (`kernel/gemv_i32_kernel.cpp`). 점수 계산 뒤 고객마다 사용자 함수를 실행 | 플랫폼 자체 테스트 ✅ (1.0 사용자 로직 없음: hw_emu·hw / 1.1 사용자 로직: hw_emu, hw 미실행) · 공식 라이브러리 테스트 미실행 (공식 문서가 가리키는 벤치마크가 저장소에 없음) (검증 장치 AMD Alveo U50 · [검증 기록](../../../verification/u50_vitis_libraries/results/gemv_i32.md)) |

## 1. 연산 예시

금융사 대출 심사 시스템 개발자가 선형 점수 모델로 고객 수천 명의 점수를 한 번에 계산하고, 회사의 심사 기준에 따라 승인·보류·거절을 판정합니다. 점수 = α·(고객 특성 행렬 A)·(가중치 x) + β·(기본 점수 y)입니다.

점수 계산(행렬-벡터 곱)은 Vitis BLAS 라이브러리가 하고, 판정 기준은 사용자가 C++ 함수 하나로 작성합니다. 두 단계 모두 FPGA에서 실행됩니다.

### 1.1. 심사 배치 코드 안에서 직접 계산하는 기존 방식

```python
import numpy as np

A = np.load("customers.npy")
w = np.load("weights.npy")
scores = A @ w + 600
decision = np.where(scores >= 25000, 2, np.where(scores >= 0, 1, 0))
```

### 1.2. ISC 전주기 시스템 기반의 방식

```bash
python3 isc/operations/gemv_i32/examples/make_input.py -o credit.bin                 # 고객 특성·가중치·기본 점수 → 연산 입력 형식 (2.1에서 내 데이터를 읽도록 수정)
isc new credit_score --operation gemv_i32 --logic                                    # 처음 한 번: 모듈과 사용자 로직 파일(logic.hpp) 만들기
# modules/credit_score/logic.hpp 의 return 줄을 판정 기준으로 수정
isc check credit_score                                                               # 사용자 로직 검사
isc run credit_score --input credit.bin --output decision.bin --target hw            # FPGA가 점수 계산 → 판정
```

- 입력: `u32 m, u32 n, i32 alpha, i32 beta` + `i32 A[m][n]`(행 우선) + `i32 x[n]` + `i32 y[m]` (little-endian). 2.1의 스크립트가 만들어 줍니다. 행렬은 HBM 뱅크 하나(256 MiB)에 들어가야 합니다(`m × n` 약 6,700만 이하)
- 출력: `i32 y_out[m]`. 고객(행) 순서대로, 점수 `alpha·A·x + beta·y`에 사용자 함수 `isc_decide`를 적용한 값입니다. 1.1의 `decision`과 같은 값입니다
- 사용자 로직 없이(`--logic` 생략) 만들면 점수가 그대로 출력됩니다. 1.1의 `scores`와 바이트까지 같습니다(int64로 곱한 뒤 int32로 자른 값)
- **정수(int32)만** 받습니다. 실수 특성은 정수 척도로 바꿔(예: ×100 후 반올림) 넣고 결과도 같은 척도로 해석합니다. 곱의 합이 int32 범위(약 ±21억)를 넘으면 감겨서 틀린 값이 됩니다
- `n`이 4의 배수가 아니어도 플랫폼이 0으로 채워 보내므로 사용자가 맞출 필요는 없습니다
- 실제로 따라 할 절차는 2절(개발 절차)과 3절(실행 방법)에 있습니다.

## 2. 개발 절차

모든 명령은 저장소 루트에서 실행합니다. 입력 변환과 결과 확인에 Python `numpy`가 필요합니다.

### 2.1. 입력 준비

**사용자가 할 일**: 고객 특성 행렬·가중치·기본 점수·α·β를 이 연산의 입력 형식으로 변환합니다. 내 데이터를 쓰려면 [`examples/make_input.py`](examples/make_input.py)의 `make_sample()`이 내 데이터를 읽도록 고칩니다.

예시(고객 512명 × 특성 64개 정수 행렬(−124~124), 특성별 가중치, 기본 점수 600, 고정 시드)로 먼저 해 보려면:

```bash
python3 isc/operations/gemv_i32/examples/make_input.py               # → datasets/gemv_i32/credit_512x64.bin
```

- 저장 위치를 바꾸려면 `-o 경로`를 붙입니다.

[`examples/make_input.py`](examples/make_input.py) 전체 코드입니다(주석 제외). `make_sample()`만 고치고, `encode()`(입력 형식, 크기 검사 포함)와 `main()`은 그대로 둡니다. `make_sample()`은 `A, x, y, alpha, beta`를 돌려주면 됩니다.

```python
import argparse
import struct
from pathlib import Path

import numpy as np

DEFAULT_OUTPUT = "datasets/gemv_i32/credit_512x64.bin"


def make_sample():
    rng = np.random.default_rng(21); m, n = 512, 64
    A = rng.integers(-124, 125, size=(m, n), dtype=np.int32)
    x = rng.integers(-120, 123, size=n, dtype=np.int32)
    y = np.full(m, 600, dtype=np.int32)
    alpha, beta = 1, 1
    return A, x, y, alpha, beta


def encode(A, x, y, alpha: int, beta: int) -> bytes:
    A, x, y = (np.asarray(v, dtype="<i4") for v in (A, x, y))
    m, n = A.shape
    if x.shape != (n,) or y.shape != (m,):
        raise ValueError(f"A가 {m}x{n}이면 x는 {n}개, y는 {m}개여야 합니다 (x {x.shape}, y {y.shape})")
    return struct.pack("<IIii", m, n, alpha, beta) + A.tobytes() + x.tobytes() + y.tobytes()


def main() -> None:
    parser = argparse.ArgumentParser(description="gemv_i32 입력 파일 만들기")
    parser.add_argument("-o", "--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    A, x, y, alpha, beta = make_sample()
    data = encode(A, x, y, alpha, beta)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print("m", A.shape[0], "n", A.shape[1], "bytes", len(data))


if __name__ == "__main__":
    main()
```

수정 예 — 고객 특성 CSV(실수)와 가중치 CSV 읽기 (위 전체 코드에서 `make_sample()`만 이것으로 바꿈):

```python
def make_sample():
    A = np.loadtxt("/data/loan/customers.csv", delimiter=",", skiprows=1)
    A = np.rint(A * 100).astype(np.int32)
    x = np.loadtxt("/data/loan/weights.csv", delimiter=",", dtype=np.int32)
    y = np.full(len(A), 600, dtype=np.int32)
    return A, x, y, 1, 1
```

### 2.2. 모듈 만들기

**사용자가 할 일**: 모듈 이름을 정합니다. 판정 기준을 넣으려면 `--logic`을 붙입니다. 이 연산은 파라미터가 없습니다(α·β는 2.1의 입력 헤더에 씀).

```bash
isc new credit_score --operation gemv_i32 --logic
```

- `modules/credit_score/` 폴더에 `module.yaml`과 사용자 로직 파일 `logic.hpp`가 만들어집니다.
- 로직 파일을 먼저 작성한 뒤 모듈을 만들어도 됩니다. `isc template gemv_i32 -o decide.hpp`로 시작 파일을 받아 2.3처럼 고친 다음, `--logic decide.hpp`로 모듈을 만듭니다.
- 점수만 필요하면 `--logic`을 빼고 만들고, 2.3은 건너뜁니다.

### 2.3. 로직 작성

**사용자가 할 일**: `modules/credit_score/logic.hpp`를 열어 함수 `isc_decide`의 `return` 줄을 판정 기준으로 바꾸고, 그 줄의 `ISC-TODO` 주석을 지웁니다.

아래는 "25,000점 이상 승인(2), 0점 이상 보류(1), 그 밖은 거절(0)" 기준으로 고친 파일 전체입니다.

```cpp
#pragma once

#include <stdint.h>

inline int32_t isc_decide(int32_t score) {
    return score >= 25000 ? 2 : score >= 0 ? 1 : 0;
}
```

- **호출 시점**: 라이브러리 함수 `xf::blas::gemv`가 고객 한 명의 점수를 계산할 때마다 한 번씩 호출됩니다. 함수가 돌려준 값이 그 고객의 출력이 됩니다.
- **입력값**: `score`는 그 고객의 점수(`alpha·A·x + beta·y`, int32)입니다.
- **바꾸면 안 되는 것**: 함수 이름(`isc_decide`), 인자 타입과 반환 타입(`int32_t`). 인자 이름은 바꿔도 됩니다.
- **쓸 수 있는 것**: 비교·사칙·비트 연산, 상수, 조건식. 보조 함수나 상수를 더 정의해도 됩니다. 예:
  - `return score < 0 ? 0 : score;` — 음수 점수를 0으로 바꾸기
  - `return score / 1000;` — 1,000점 단위 등급으로 바꾸기
- **쓸 수 없는 것**: 동적 메모리 할당(`new`, `malloc`), 파일·네트워크 입출력(`fopen`, `open`, `socket`), `#pragma HLS INTERFACE`, 다른 고객의 값 참조.
- 파일을 고치지 않고 두면(`return score;`) 원래 연산과 같은 결과, 즉 점수가 그대로 나옵니다.

### 2.4. 검사

**사용자가 할 일**: 빌드하기 전에 모듈 설정과 로직 파일을 검사합니다. 문제가 없으면 `PASS`가 표시됩니다.

```bash
isc check credit_score
```

- 함수 이름·인자·반환 타입이 다르거나 쓸 수 없는 문법이 있으면 `problem`이 표시되고, 고칠 때까지 빌드와 실행이 진행되지 않습니다.
- `ISC-TODO` 주석이 남아 있으면 `warning`이 표시됩니다. 빌드는 진행됩니다.

## 3. 실행 방법

**사용자가 할 일**:

- `--input`에 2.1의 파일, `--output`에 결과를 저장할 경로를 지정합니다. 결과 확인 명령에도 같은 입력 파일을 `--input`으로 지정하고, 결과표가 필요하면 `--csv`에 저장할 경로를 지정합니다.
- 결과 확인 스크립트 [`examples/check_output.py`](examples/check_output.py)의 `[바꾸는 곳]`에 있는 `decide()`를 2.3과 같은 기준으로 맞춥니다. 2.3의 예시 기준을 그대로 썼다면 고칠 것이 없습니다.
- 2.2에서 `--logic` 없이 모듈을 만들었다면, 결과 확인 명령에 `--original`을 붙입니다.

```bash
# 에뮬레이션 실행 (카드 없이, 처음 한 번 수 분 빌드. 빌드가 캐시에 있으면 약 30초)
isc run credit_score --input datasets/gemv_i32/credit_512x64.bin --output datasets/gemv_i32/credit_score.emu.bin

# 결과 확인: CPU로 계산한 점수에 같은 기준을 적용한 값과 비교, MATCH가 나오면 정상
python3 isc/operations/gemv_i32/examples/check_output.py datasets/gemv_i32/credit_score.emu.bin --input datasets/gemv_i32/credit_512x64.bin

# FPGA 카드 실행 (hw) (처음 한 번 hw 빌드 약 47분) — 카드 출력이 에뮬레이션 출력과 같은지 플랫폼이 자동 비교
isc run credit_score --input datasets/gemv_i32/credit_512x64.bin --output datasets/gemv_i32/credit_score.hw.bin --target hw
python3 isc/operations/gemv_i32/examples/check_output.py datasets/gemv_i32/credit_score.hw.bin --input datasets/gemv_i32/credit_512x64.bin --csv datasets/gemv_i32/decision.csv

# 실행 기록
isc records --module credit_score
isc show [실행 ID]
```

결과 확인 스크립트는 CPU로 점수를 계산한 뒤, 아래 `decide()` 함수로 판정해 FPGA 출력과 비교합니다. `decide()`는 2.3의 기준을 Python으로 옮긴 것이므로, 2.3의 기준을 바꾸면 이 함수도 함께 바꿉니다.

```python
def decide(score: int) -> int:
    return 2 if score >= 25000 else 1 if score >= 0 else 0
```

- 결과 확인 스크립트는 결과 값별 고객 수(`result counts`)를 보여 줍니다. 예시 데이터에서는 거절 250명, 보류 129명, 승인 133명입니다.
- `--csv`로 저장한 파일(`customer,result`)이 고객별 결과표입니다. 순서는 입력 행렬의 행 순서와 같습니다.
- **기준을 바꿀 때**: `logic.hpp`를 고치고 `isc run`을 다시 실행하면, 바뀐 로직으로 자동으로 다시 빌드합니다. 에뮬레이션으로 먼저 확인한 다음 FPGA 카드에서 실행합니다.
- **성능 측정**: 사용자 로직이 들어간 모듈은 `isc bench`에서 CPU 기준선과 비교하지 않습니다. 가속 효과를 측정하려면 `--logic` 없이 만든 모듈을 씁니다.
- 행렬이 HBM 뱅크 하나에 들어가지 않으면 고객을 나눠 여러 번 실행합니다. 고객 수가 바뀌어도 다시 빌드하지 않습니다.
- 터미널 출력을 파일로도 남기려면 명령 뒤에 `2>&1 | tee [로그 파일]`을 붙입니다.
