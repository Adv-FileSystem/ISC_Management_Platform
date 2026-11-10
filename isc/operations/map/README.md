# `map` — 수치 컬럼을 다른 타입·단위로 변환 (Hook형)

| 개발 방식 | 사용자가 작성하는 것 | 커널 | 지원 타입 | 플랫폼 자체 테스트 | 공식 라이브러리 테스트 |
|---|---|---|---|---|---|
| Hook형 | `U isc_map(T value)`의 `return` 한 줄 | 플랫폼이 생성하는 래퍼 커널(`kernel/map.cpp.tmpl`)에 사용자 함수를 끼워 빌드 | 입력 `T`·출력 `U` 모두 `u8 u16 u32 u64 i8 i16 i32 i64 f32` | ✅ 통과 (2026-11-05, `f32→u8`) | — 해당 없음 |

> **알려진 문제 (2026-11-05 확인)**: 출력이 1바이트 타입(`u8`, `i8`)일 때 `hw_emu`에서 출력 길이 65·129바이트의 **마지막 바이트가 `00`** 으로 나옵니다(17·33바이트는 정상, `u8→u8`·`i8→i8`·`f32→u8`에서 재현).
> 같은 종류의 문제가 있던 `identity`는 카드(`hw`) 출력이 정상이었지만, `map`의 이 길이에서 카드 결과는 아직 확인하지 않았습니다.
> 플랫폼은 hw 출력을 hw_emu 출력과 대조하므로 이런 길이에서는 대조가 틀릴 수 있습니다. 결과는 4.4의 CPU 비교로 판정하십시오. 2바이트 이상 타입은 시험하지 않았습니다.

## 1. 시나리오

| 항목 | 내용 |
|---|---|
| 사용자 | 공장 설비의 센서 데이터를 모으는 데이터 개발자 |
| 상황 | 공장 온도 센서 값(°C, `f32`)을 0.5 °C 단위 1바이트 코드(`u8`)로 줄여 장기 보관한다. 0 °C 이하는 0, 127.5 °C 이상은 255로 포화시킨다 |
| 가속하는 단계 | 컬럼 값 변환 |
| 입력 → 출력 | f32 컬럼 → u8 컬럼 |
| 완료 기준 | numpy `float32` 변환과 일치 |

## 2. 연산 요약

| 항목 | 내용 |
|---|---|
| 입력 | 한 타입 `T`의 값을 little-endian으로 이어 쓴 파일 (헤더 없음). 빈 파일 허용 |
| 출력 | 입력과 **같은 개수**의 `U` 값을 little-endian으로 이어 쓴 파일 |
| 사용자 함수 | `inline U isc_map(T value)` — 값 하나를 받아 변환한 값을 돌려준다. `T`는 `--type`, `U`는 `--out-type`(생략하면 `T`와 같음) |
| 파라미터 | 없음 |
| 변환 규칙 | C++ 형 변환 규칙을 그대로 따름. 정수 나눗셈은 버림, `f32 → 정수`는 0 쪽으로 버림. **범위를 벗어난 `f32 → 정수` 변환은 C++에서 정의되지 않으므로** 반드시 범위를 먼저 잘라 냄(4.2 예시) |
| 지원하지 않는 것 | 개수를 바꾸는 변환(→ [`filter`](../filter/README.md)), 앞뒤 값을 쓰는 변환(이동 평균 등), 여러 컬럼 결합 |

작은 예: `T = f32`, `U = u8`, 4.2의 변환식, 입력 `[-9.03, 10.78, 46.0, 119.98, 140.0]` → 출력 `[0, 21, 92, 239, 255]`

## 3. 준비

- 공통 설치와 환경 확인: [README 1절](../../../README.md#1-설치-및-환경-구성)
- 이 연산은 `VITIS_LIBRARIES`가 필요 없습니다. 예시의 결과 확인에 **numpy**가 필요합니다 (`python3 -m pip install numpy`).
- 모든 명령은 **저장소 루트**에서 실행합니다. 실습 파일은 `datasets/map/`(git에 올라가지 않음)에 만듭니다.

## 4. 개발 절차

### 4.1. 입력 만들기

-10 ~ 140 °C 사이 온도 10,000개(소수 둘째 자리, 고정 시드)를 `f32` little-endian으로 씁니다. 범위 밖 값도 일부러 넣어 포화 처리를 확인합니다.

```bash
python3 isc/operations/map/examples/make_input.py
```

입력 파일은 [`examples/make_input.py`](examples/make_input.py)가 만듭니다. 스크립트는 두 부분으로 나뉩니다.

| 부분 | 하는 일 | 사용자 |
|---|---|---|
| `make_sample()` | 예시 데이터를 만듦 | **바꾸는 곳** — 자기 데이터로 바꿈 |
| `encode()` | 2절의 입력 형식(헤더·엔디안·타입)대로 바이트를 씀 | 고치지 않음 |

- 내 CSV의 한 열을 변환: `python3 isc/operations/map/examples/make_input.py --csv 내파일.csv --column temp -o datasets/map/my.bin`
- 저장 위치를 바꾸려면 `-o 경로`를 붙입니다.

### 4.2. 로직 파일 만들기

입력 타입과 출력 타입을 지정해 시작 파일을 받습니다.

```bash
isc template map --type f32 --out-type u8 -o datasets/map/temp_code.hpp
```

편집기로 열어 **`return` 줄 하나만** 고칩니다. 파일 이름(`temp_code`)이 모듈 이름이 됩니다.

```cpp
inline uint8_t isc_map(float value) {
    return value <= 0.0f ? (uint8_t)0 : value >= 127.5f ? (uint8_t)255 : (uint8_t)(value * 2.0f);   // 고친 줄
}
```

- 함수 이름 `isc_map`과 인자 이름 `value`, 인자·반환 타입은 바꾸지 않습니다. 타입을 바꾸려면 `--type`/`--out-type`으로 시작 파일을 다시 받습니다.
- 위 식은 먼저 범위를 잘라 낸 뒤 `value * 2.0f`를 정수로 바꿉니다(0 쪽으로 버림). 범위 검사 없이 `(uint8_t)(value * 2.0f)`만 쓰면 음수·큰 값에서 결과가 정해지지 않습니다.
- 쓸 수 없는 것: 동적 할당, 파일·소켓 입출력, `#pragma HLS INTERFACE`.

### 4.3. 에뮬레이션 실행 (hw_emu)

로직 파일을 그대로 실행하면 `modules/temp_code/`가 만들어지고 정적 검사 → 빌드(처음 한 번 수 분) → 실행 → 기록까지 진행됩니다.

```bash
isc run datasets/map/temp_code.hpp --input datasets/map/temps.bin --output datasets/map/result.bin
```

실제 출력 (발췌, 처음 실행이라 hw_emu 빌드 포함):

```text
temp_code: created /home/.../ISC_Management_Platform/modules/temp_code (profile map)
temp_code [hw_emu] succeeded  run_id=20261105T110128-fb39ed
  device     : [0] xilinx_u50_gen3x16_xdma_5_202210_1  (emulated)
  build      : built  5d1937b601207b4f47958af781fda346
  input      : 40000 bytes  /home/.../datasets/map/temps.bin
  output     : 10000 bytes  /home/.../datasets/map/result.bin
  validation : PASS  (cross_target) ran on the reference target itself; nothing to compare
```

`validation : PASS`는 hw_emu가 자기 자신을 기준으로 삼았다는 뜻일 뿐입니다. 변환이 맞는지는 다음 단계에서 확인합니다.

### 4.4. 결과 확인

같은 변환을 numpy `float32`로 계산해 비교합니다(곱셈을 `float32`로 해야 FPGA와 같은 반올림이 됩니다).

```bash
python3 - <<'EOF'
import numpy as np
temps = np.frombuffer(open("datasets/map/temps.bin", "rb").read(), "<f4")
expected = [0 if t <= 0 else 255 if t >= 127.5 else int(np.float32(t) * np.float32(2.0)) for t in temps]
got = list(open("datasets/map/result.bin", "rb").read())
print(f"input={len(temps)} output={len(got)} first5={list(zip(temps[:5].tolist(), got[:5]))}")
print("MATCH" if got == expected else "MISMATCH")
EOF
```

```text
input=10000 output=10000 first5=[(59.45000076293945, 118), (46.0, 92), (10.779999732971191, 21), (119.9800033569336, 239), (-9.029999732971191, 0)]
MATCH
```

### 4.5. U50 실행 (hw)

```bash
isc run datasets/map/temp_code.hpp --input datasets/map/temps.bin --output datasets/map/result_hw.bin --target hw
```

플랫폼이 hw_emu 기준 출력을 먼저 만들고 → hw 빌드(처음 한 번 수십 분) → 카드 실행 → 두 출력의 바이트 비교를 합니다. 4.4의 코드에서 `result.bin`을 `result_hw.bin`으로 바꿔 한 번 더 확인합니다.

실제 출력 (발췌, 처음 실행이라 hw 빌드 포함 — 이 환경에서 약 45분):

```text
temp_code: unchanged /home/.../modules/temp_code (profile map)
temp_code [hw] succeeded  run_id=20261105T111257-68f90b
  device     : [0] xilinx_u50_gen3x16_xdma_base_5
  build      : built  3c375dffed8f4139a77ad32bd9067494
  input      : 40000 bytes  /home/.../datasets/map/temps.bin
  output     : 10000 bytes  /home/.../datasets/map/result_hw.bin
  kernel_ms  : 0.141
  validation : PASS  (cross_target) matches reference target output (10000 bytes)
```

`validation : PASS ... matches reference target output`는 카드 출력이 hw_emu 출력과 바이트까지 같다는 뜻입니다. 결과 확인 코드를 hw 출력에 적용한 결과:

```text
input=10000 output=10000 first5=[(59.45000076293945, 118), (46.0, 92), (10.779999732971191, 21), (119.9800033569336, 239), (-9.029999732971191, 0)]
MATCH
```

### 4.6. 기록 확인

```bash
isc records --module temp_code
isc show [실행 ID]
```

### 4.7. 로직을 바꿀 때

`datasets/map/temp_code.hpp`를 고친 뒤 4.3의 명령을 다시 실행하면 모듈의 `logic.hpp`가 갱신되고(`updated`) 다시 빌드합니다. 상수 하나만 바꿔도 새 빌드이므로 hw_emu에서 확인한 뒤 hw로 실행합니다.

## 5. 내 데이터에 적용하기

| 바꿀 것 | 방법 |
|---|---|
| 입력·출력 타입 | `isc template map --type <T> --out-type <U>`와 입력 파일 타입을 함께 바꿈 (`struct` 코드: `u8 B`, `u16 H`, `u32 I`, `u64 Q`, `i8 b`, `i16 h`, `i32 i`, `i64 q`, `f32 f`) |
| 단위 변환 | 예: 섭씨 → 화씨 `f32 → f32`: `return value * 1.8f + 32.0f;` / 밀리초 → 초 `u32 → u32`: `return value / 1000;` (버림) |
| 정수 범위 줄이기 | 출력 타입보다 큰 값이 나올 수 있으면 먼저 잘라 냄. 예: `i32 → u8`: `return value < 0 ? (uint8_t)0 : value > 255 ? (uint8_t)255 : (uint8_t)value;` |
| 결과 확인 | 4.4 코드의 기대값 계산식을 로직과 같은 식으로 바꿈. 실수 연산은 numpy `float32`로 계산 |

## 6. 문제 해결

| 증상 | 원인 | 해결 |
|---|---|---|
| `warning: ... still has the starter marker ISC-TODO` | `return` 줄을 고치지 않았거나 주석을 남김 | `return` 줄 작성 후 `ISC-TODO` 주석 삭제 |
| `no hook profile matches this logic file` | 함수 이름이 `isc_map`이 아님 | 함수 이름을 되돌림 |
| ``unsupported type `...` for the parameter of ...`` | 지원하지 않는 타입 (`double`, `int` 등) | `--type`/`--out-type`으로 지원 타입을 골라 다시 받음 |
| 일부 값만 CPU 결과와 1 차이 | CPU 쪽을 `float64`로 계산 | 4.4처럼 `np.float32`로 계산 |
| 음수·큰 값에서 hw와 hw_emu 결과가 다름 | 범위를 벗어난 `f32 → 정수` 변환 (정의되지 않은 동작) | 4.2처럼 변환 전에 범위를 잘라 냄 |
| 출력 개수가 입력보다 적음 | 입력 크기가 `T` 크기의 배수가 아님 (끝 바이트는 무시됨) | 입력 파일 크기 확인 |

## 7. 검증 기록

### 7.1. 플랫폼 자체 테스트

| 항목 | 내용 |
|---|---|
| 확인일 | 2026-11-05 |
| 환경 | Ubuntu 22.04.5 LTS (커널 6.8.0-138), Python 3.10.12, Vitis 2025.1, XRT 2.19.194 (2025.1), Vitis Libraries `v2025.1_update2` (커밋 b2c657d), AMD Alveo U50 (`xilinx_u50_gen3x16_xdma_base_5`), 저장소 기준 커밋 `8742866` |
| 실행한 단계 | 이 문서의 명령을 저장소 루트에서 순서대로 그대로 실행 (입력 만들기 → 로직 파일 → hw_emu → 결과 확인 → hw → 결과 확인) |
| hw_emu | 통과 — 실행 `20261105T110128-fb39ed`, numpy `float32` 변환과 10,000개 모두 일치 (포화 구간 포함) |
| hw (U50) | 통과 — 실행 `20261105T111257-68f90b`, hw 출력 = hw_emu 출력(플랫폼 자동 대조) = CPU 참조 |
| 확인하지 않은 것 | 성능(가속 효과)은 측정하지 않음. `f32 → u8` 한 가지 조합과 이 문서의 입력으로만 확인 |

### 7.2. 공식 라이브러리 테스트

**해당 없음** — 이 연산에 대응하는 Vitis Libraries 공식 테스트가 없습니다 (플랫폼 전용 연산).
