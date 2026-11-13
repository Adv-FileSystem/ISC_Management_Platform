# `mc_european` — 리스크팀의 유럽형 옵션 가격 재계산과 변동성 스트레스 테스트 (연산 선택형 + 사용자 로직)

| 개발 방식 | 사용하는 라이브러리 함수 | 검증 상태 |
|---|---|---|
| 연산 선택형 + 사용자 로직 `isc_volatility` | Vitis Quantitative Finance L2 `xf::fintech::MCEuropeanEngine` (`kernel/mc_european_kernel.cpp`). 엔진 호출 전 옵션마다 사용자 함수를 실행 | 플랫폼 자체 테스트 ✅ (1.0 사용자 로직 없음: hw_emu·hw / 1.1 사용자 로직: hw_emu, hw 미실행) · 공식 라이브러리 테스트 ✅ (검증 장치 AMD Alveo U50 · [검증 기록](../../../verification/u50_vitis_libraries/results/mc_european.md)) |

## 1. 연산 예시

금융사 리스크팀의 가격 계산 담당 개발자가 매일 밤 보유 중인 유럽형 옵션 가격을 Monte Carlo 시뮬레이션으로 다시 계산합니다. 시장이 흔들릴 때를 대비해 "변동성이 20% 오르면 가격이 얼마가 되는가"를 보는 스트레스 테스트도 함께 합니다.

Monte Carlo 가격 계산은 Vitis Quantitative Finance 라이브러리가 하고, 스트레스 시나리오(엔진에 넘길 변동성)는 사용자가 C++ 함수 하나로 작성합니다. 두 단계 모두 FPGA에서 실행됩니다.

### 1.1. 리스크 배치 코드 안에서 직접 계산하는 기존 방식

```python
import numpy as np

def mc_price(S, K, sigma, q, r, T, put, samples=4096):
    z = np.random.default_rng(1).standard_normal(samples)
    ST = S * np.exp((r - q - 0.5 * sigma**2) * T + sigma * np.sqrt(T) * z)
    payoff = np.maximum(K - ST, 0) if put else np.maximum(ST - K, 0)
    return np.exp(-r * T) * payoff.mean()

stressed = [(S, K, sigma * 1.2, q, r, T, put) for S, K, sigma, q, r, T, put in book]
prices = [mc_price(*opt) for opt in stressed]
```

### 1.2. ISC 전주기 시스템 기반의 방식

```bash
python3 isc/operations/mc_european/examples/make_input.py --csv book.csv -o book.bin    # 보유 옵션 CSV → 연산 입력 형식
isc new option_book --operation mc_european --set samples=4096 --logic                   # 처음 한 번: 모듈과 사용자 로직 파일(logic.hpp) 만들기
# modules/option_book/logic.hpp 의 return 줄을 스트레스 시나리오로 수정
isc check option_book                                                                    # 사용자 로직 검사
isc run option_book --input book.bin --output prices.bin --target hw                     # FPGA가 옵션마다 Monte Carlo 가격 계산
```

- 입력: `u32 count, u32 0` + 옵션마다 `f32` 8개(`S, K, sigma, q, r, T, type, 0`, little-endian). `type` 0 = 콜, 1 = 풋. 2.1의 스크립트가 CSV에서 만들어 줍니다. 옵션 1~65,536건, `S, K, sigma, T`는 0보다 커야 함
- 출력: `f32 price[count]` — 입력 순서대로 옵션 가격. 엔진은 사용자 함수 `isc_volatility`가 돌려준 변동성으로 계산합니다
- 사용자 로직 없이(`--logic` 생략) 만들면 입력의 변동성 그대로 계산합니다
- Monte Carlo는 근사값이라 1.1과 바이트까지 같지는 않습니다. 정답 기준은 Black-Scholes 해석해이고, 판정식(`|가격 − 정답| / 기초가 S ≤ 1%`)으로 확인합니다
- 만기 수익 식(payoff)은 엔진 내부에 콜·풋으로 고정되어 있어 사용자 함수로 바꿀 수 없습니다
- 실제로 따라 할 절차는 2절(개발 절차)과 3절(실행 방법)에 있습니다.

## 2. 개발 절차

모든 명령은 저장소 루트에서 실행합니다.

### 2.1. 입력 준비

**사용자가 할 일**: 보유 옵션 목록을 이 연산의 입력 형식으로 변환합니다. CSV가 있으면 `--csv` 옵션으로 바로 변환되고(코드 수정 불필요), 다른 형식이면 `make_sample()`을 고칩니다.

```bash
# 내 보유 옵션 CSV (머리글 S,K,sigma,q,r,T,type, 한 줄에 옵션 1건) → 입력 형식
python3 isc/operations/mc_european/examples/make_input.py --csv [보유 옵션 CSV] -o datasets/mc_european/[이름].bin

# 또는 예시(옵션 8건: 콜 4·풋 4)로 먼저 해 보기
python3 isc/operations/mc_european/examples/make_input.py                # → datasets/mc_european/option_book_8.bin
```

[`examples/make_input.py`](examples/make_input.py) 전체 코드입니다(주석 제외). `make_sample()`(예시 옵션)·`read_csv()`(CSV 읽기)만 고치고, `encode()`(입력 형식)와 `main()`은 그대로 둡니다. 옵션 하나는 `(S, K, sigma, q, r, T, type)` 튜플입니다.

```python
import argparse
import csv
import struct
from pathlib import Path

DEFAULT_OUTPUT = "datasets/mc_european/option_book_8.bin"
FIELDS = ("S", "K", "sigma", "q", "r", "T", "type")


def make_sample() -> list:
    return [(100, 75, 0.11, 0.00, 0.01, 1, 0), (100, 100, 0.50, 0.00, 0.05, 1, 0), (100, 125, 1.20, 0.05, 0.15, 1, 0),
            (100, 100, 0.30, 0.02, 0.03, 0.5, 0), (100, 75, 0.11, 0.00, 0.01, 1, 1), (100, 100, 0.50, 0.00, 0.05, 1, 1),
            (100, 125, 1.20, 0.05, 0.15, 1, 1), (100, 110, 0.25, 0.01, 0.04, 2, 1)]


def read_csv(path: str) -> list:
    with open(path, newline="", encoding="utf-8") as f:
        return [tuple(float(row[k]) for k in FIELDS) for row in csv.DictReader(f)]


def encode(book: list) -> bytes:
    return struct.pack("<II", len(book), 0) + b"".join(struct.pack("<8f", *o, 0.0) for o in book)


def main() -> None:
    parser = argparse.ArgumentParser(description="mc_european 입력 파일 만들기")
    parser.add_argument("-o", "--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--csv", help="보유 옵션 CSV (머리글: S,K,sigma,q,r,T,type)")
    args = parser.parse_args()
    book = read_csv(args.csv) if args.csv else make_sample()
    data = encode(book)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print(len(book), "options,", len(data), "bytes")


if __name__ == "__main__":
    main()
```

수정 예 — 시스템에서 내려받은 CSV의 열 이름이 다를 때 (위 전체 코드에서 `read_csv()`만 이것으로 바꿈):

```python
def read_csv(path: str) -> list:
    with open(path, newline="", encoding="utf-8") as f:
        return [(float(row["spot"]), float(row["strike"]), float(row["vol"]), float(row["div"]),
                 float(row["rate"]), float(row["maturity"]), 1.0 if row["cp"] == "P" else 0.0)
                for row in csv.DictReader(f)]
```

### 2.2. 모듈 만들기

**사용자가 할 일**: 모듈 이름과 경로 수(`samples`, 기본 4096, 1024~1048576), 경로당 시간 단계(`time_steps`, 기본 1, 1~100)를 정합니다. 스트레스 시나리오를 넣으려면 `--logic`을 붙입니다.

```bash
isc new option_book --operation mc_european --set samples=4096 --set time_steps=1 --logic
```

- `modules/option_book/` 폴더에 `module.yaml`과 사용자 로직 파일 `logic.hpp`가 만들어집니다.
- 로직 파일을 먼저 작성한 뒤 모듈을 만들어도 됩니다. `isc template mc_european -o stress.hpp`로 시작 파일을 받아 2.3처럼 고친 다음, `--logic stress.hpp`로 모듈을 만듭니다.
- 평상시 가격만 필요하면 `--logic`을 빼고 만들고, 2.3은 건너뜁니다.

- 두 값은 나중에 `modules/option_book/module.yaml`로도 바꿀 수 있고, 바꿔도 다시 빌드하지 않습니다.
- 경로 수를 늘리면 오차가 줄고 실행 시간이 늘어납니다.

### 2.3. 로직 작성

**사용자가 할 일**: `modules/option_book/logic.hpp`를 열어 함수 `isc_volatility`의 `return` 줄을 스트레스 시나리오로 바꾸고, 그 줄의 `ISC-TODO` 주석을 지웁니다.

아래는 "모든 옵션의 변동성이 20% 오름" 시나리오로 고친 파일 전체입니다.

```cpp
#pragma once

inline float isc_volatility(float sigma) {
    return sigma * 1.2f;
}
```

- **호출 시점**: 옵션마다 한 번, 라이브러리 엔진 `xf::fintech::MCEuropeanEngine`에 넘기기 직전에 호출됩니다. 함수가 돌려준 값을 엔진이 변동성으로 씁니다.
- **입력값**: `sigma`는 입력 파일에 적힌 그 옵션의 변동성(예: 0.2 = 20%)입니다.
- **바꾸면 안 되는 것**: 함수 이름(`isc_volatility`), 인자 타입과 반환 타입(`float`). 인자 이름은 바꿔도 됩니다.
- **쓸 수 있는 것**: 비교·사칙 연산, 상수(`1.2f`처럼 `f`를 붙인 실수), 조건식. 보조 함수나 상수를 더 정의해도 됩니다. 예:
  - `return sigma < 0.3f ? 0.3f : sigma;` — 변동성 하한 30%
  - `return sigma + 0.05f;` — 변동성 5%p 상승
- **쓸 수 없는 것**: 동적 메모리 할당(`new`, `malloc`), 파일·네트워크 입출력(`fopen`, `open`, `socket`), `#pragma HLS INTERFACE`, 다른 옵션의 값 참조.
- 돌려주는 값은 0보다 커야 합니다(엔진은 양수 변동성만 받습니다).
- 파일을 고치지 않고 두면(`return sigma;`) 원래 연산과 같은 결과, 즉 입력의 변동성 그대로 계산합니다.

### 2.4. 검사

**사용자가 할 일**: 빌드하기 전에 모듈 설정과 로직 파일을 검사합니다. 문제가 없으면 `PASS`가 표시됩니다.

```bash
isc check option_book
```

- 함수 이름·인자·반환 타입이 다르거나 쓸 수 없는 문법이 있으면 `problem`이 표시되고, 고칠 때까지 빌드와 실행이 진행되지 않습니다.
- `ISC-TODO` 주석이 남아 있으면 `warning`이 표시됩니다. 빌드는 진행됩니다.

## 3. 실행 방법

**사용자가 할 일**:

- `--input`에 2.1의 파일, `--output`에 결과를 저장할 경로를 지정합니다. 결과 확인 명령에도 같은 입력 파일을 `--input`으로 지정합니다(예시 파일이면 생략).
- 결과 확인 스크립트 [`examples/check_output.py`](examples/check_output.py)의 `[바꾸는 곳]`에 있는 `volatility()`를 2.3과 같은 규칙으로 맞춥니다. 2.3의 예시 규칙을 그대로 썼다면 고칠 것이 없습니다.
- 2.2에서 `--logic` 없이 모듈을 만들었다면, 결과 확인 명령에 `--original`을 붙입니다.

```bash
# 에뮬레이션 실행 (카드 없이, 처음 한 번 수 분 빌드)
isc run option_book --input datasets/mc_european/option_book_8.bin --output datasets/mc_european/option_book.emu.bin

# 결과 확인: 같은 규칙으로 바꾼 변동성의 Black-Scholes 해석해와 비교, 판정식 max |d|/S ≤ 1%면 PASS
python3 isc/operations/mc_european/examples/check_output.py datasets/mc_european/option_book.emu.bin --input datasets/mc_european/option_book_8.bin

# FPGA 카드 실행 (hw) (처음 한 번 hw 빌드 약 67분) — 카드 출력이 에뮬레이션 출력과 같은지 플랫폼이 자동 비교
isc run option_book --input datasets/mc_european/option_book_8.bin --output datasets/mc_european/option_book.hw.bin --target hw
python3 isc/operations/mc_european/examples/check_output.py datasets/mc_european/option_book.hw.bin --input datasets/mc_european/option_book_8.bin

# 실행 기록
isc records --module option_book
isc show [실행 ID]
```

- 가격이 아주 작은 옵션(예시의 깊은 외가격 풋, 가격 약 0.01)은 절대 오차가 작아도 가격 대비 오차(`|d|/BS`)가 크게 나옵니다(예시에서 6.6%). 가격 대비 오차가 필요하면 `samples`를 늘려 다시 실행합니다(이 방법은 시험하지 않았습니다).
결과 확인 스크립트는 입력의 변동성에 아래 `volatility()` 함수를 적용한 뒤 Black-Scholes 해석해를 계산해 FPGA 가격과 비교합니다. `volatility()`는 2.3의 규칙을 Python으로 옮긴 것이므로, 2.3의 규칙을 바꾸면 이 함수도 함께 바꿉니다.

```python
def volatility(sigma):
    return sigma * np.float32(1.2)
```

- **규칙을 바꿀 때**: `logic.hpp`를 고치고 `isc run`을 다시 실행하면, 바뀐 로직으로 자동으로 다시 빌드합니다. 에뮬레이션으로 먼저 확인한 다음 FPGA 카드에서 실행합니다.
- **성능 측정**: 사용자 로직이 들어간 모듈은 `isc bench`에서 CPU 기준선과 비교하지 않습니다. 가속 효과를 측정하려면 `--logic` 없이 만든 모듈을 씁니다.
- **다른 옵션 목록으로 실행할 때**: 2.1처럼 변환한 뒤 `--input`·`--output`만 바꿔 실행합니다. 로직이 같으면 다시 빌드하지 않습니다.
- hw 빌드는 다른 hw 빌드와 동시에 돌리면 메모리가 부족해 멈출 수 있으므로, 한 번에 하나씩 빌드합니다.
- 터미널 출력을 파일로도 남기려면 명령 뒤에 `2>&1 | tee [로그 파일]`을 붙입니다.
