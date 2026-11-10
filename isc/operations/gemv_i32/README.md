# `gemv_i32` — 대출 심사 점수 모델로 고객 점수 일괄 계산 (연산 선택형)

| 개발 방식 | 사용하는 라이브러리 함수 | 검증 상태 |
|---|---|---|
| 연산 선택형 | Vitis BLAS L1 `xf::blas::gemv` (`kernel/gemv_i32_kernel.cpp`) | 플랫폼 자체 테스트 ✅ · 공식 라이브러리 테스트 미실행 (공식 문서가 가리키는 벤치마크가 저장소에 없음) (검증 장치 AMD Alveo U50 · [검증 기록](../../../verification/u50_vitis_libraries/results/gemv_i32.md)) |

## 1. 연산 예시

금융사 대출 심사 시스템 개발자가 선형 점수 모델로 고객 수천 명의 점수를 한 번에 계산합니다. 점수 = α·(고객 특성 행렬 A)·(가중치 x) + β·(기본 점수 y)입니다. 이 행렬-벡터 곱을 CPU 대신 FPGA에서 합니다.

### 1.1. 심사 배치 코드 안에서 직접 계산하는 기존 방식

```python
import numpy as np

A = np.load("customers.npy")
w = np.load("weights.npy")
scores = A @ w + 600
```

### 1.2. ISC 전주기 시스템 기반의 방식

```bash
python3 isc/operations/gemv_i32/examples/make_input.py -o credit.bin                 # 고객 특성·가중치·기본 점수 → 연산 입력 형식 (2.1에서 내 데이터를 읽도록 수정)
isc new credit_score --operation gemv_i32                                            # 처음 한 번: 모듈 만들기
isc run credit_score --input credit.bin --output scores.bin --target hw              # FPGA가 행렬-벡터 곱 계산
```

- 입력: `u32 m, u32 n, i32 alpha, i32 beta` + `i32 A[m][n]`(행 우선) + `i32 x[n]` + `i32 y[m]` (little-endian). 2.1의 스크립트가 만들어 줍니다. 행렬은 HBM 뱅크 하나(256 MiB)에 들어가야 합니다(`m × n` 약 6,700만 이하)
- 출력: `i32 y_out[m]` = `alpha·A·x + beta·y`. 1.1의 `scores`와 바이트까지 같습니다(int64로 곱한 뒤 int32로 자른 값)
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

**사용자가 할 일**: 모듈 이름을 정합니다. 이 연산은 파라미터가 없습니다(α·β는 2.1의 입력 헤더에 씀).

```bash
isc new credit_score --operation gemv_i32
```

`modules/credit_score/module.yaml`이 만들어지며 고칠 것은 없습니다.

### 2.3. 로직 작성

**사용자가 할 일**: 현재 없음. 추가 예정: 고객별 점수를 받아 결과 코드를 돌려주는 함수 `isc_decide(score)`(예: 점수 구간에 따라 승인·보류·거절)와 특성 값 변환 함수. 추가되면 이 단계에서 작성합니다.

### 2.4. 검사

**사용자가 할 일**: 빌드하기 전에 모듈 설정을 검사합니다. 문제가 없으면 `PASS`가 표시됩니다.

```bash
isc check credit_score
```

## 3. 실행 방법

**사용자가 할 일**: `--input`에 2.1의 파일, `--output`에 결과를 저장할 경로를 지정합니다. 결과 확인 명령에도 같은 입력 파일을 `--input`으로 지정하고, 점수표가 필요하면 `--csv`에 저장할 경로를 지정합니다.

```bash
# 에뮬레이션 실행 (카드 없이, 처음 한 번 수 분 빌드. 빌드가 캐시에 있으면 약 30초)
isc run credit_score --input datasets/gemv_i32/credit_512x64.bin --output datasets/gemv_i32/credit_score.emu.bin

# 결과 확인: CPU 참조(baseline.py, numpy)와 바이트 비교 — MATCH면 정상
python3 isc/operations/gemv_i32/examples/check_output.py datasets/gemv_i32/credit_score.emu.bin --input datasets/gemv_i32/credit_512x64.bin

# FPGA 카드 실행 (hw) (처음 한 번 hw 빌드 약 47분) — 카드 출력이 에뮬레이션 출력과 같은지 플랫폼이 자동 비교
isc run credit_score --input datasets/gemv_i32/credit_512x64.bin --output datasets/gemv_i32/credit_score.hw.bin --target hw
python3 isc/operations/gemv_i32/examples/check_output.py datasets/gemv_i32/credit_score.hw.bin --input datasets/gemv_i32/credit_512x64.bin --csv datasets/gemv_i32/scores.csv

# 실행 기록
isc records --module credit_score
isc show [실행 ID]
```

- `--csv`로 저장한 파일(`customer,score`)이 고객별 점수표입니다. 순서는 입력 행렬의 행 순서와 같습니다.
- 행렬이 HBM 뱅크 하나에 들어가지 않으면 고객을 나눠 여러 번 실행합니다. 고객 수가 바뀌어도 다시 빌드하지 않습니다.
- 터미널 출력을 파일로도 남기려면 명령 뒤에 `2>&1 | tee [로그 파일]`을 붙입니다.
