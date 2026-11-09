# `filter` — 조건에 맞는 값만 추출 (Hook형)

| 개발 방식 | 사용자가 작성하는 것 | 커널 | 지원 타입 | 플랫폼 자체 테스트 | 공식 라이브러리 테스트 |
|---|---|---|---|---|---|
| Hook형 | `bool isc_filter(T value)`의 `return` 한 줄 | 플랫폼이 생성하는 래퍼 커널(`kernel/filter.cpp.tmpl`)에 사용자 함수를 끼워 빌드 | `u8 u16 u32 u64 i8 i16 i32 i64 f32` | ✅ 통과 (2026-11-05, `u32`) | — 해당 없음 |

> **알려진 문제 (2026-11-05 확인)**: 값이 1바이트 타입(`u8`, `i8`)일 때 `hw_emu`에서 행 수 65·129의 입력이 **1행 모자라게** 처리됩니다(입력의 마지막 바이트를 잘못 읽음).
> 이 길이에서 카드(`hw`) 결과는 아직 확인하지 않았습니다. 플랫폼은 hw 출력을 hw_emu 출력과 대조하므로 이런 길이에서는 대조가 틀릴 수 있습니다.
> 결과는 4.4의 CPU 비교로 판정하십시오. 이 문서의 예시(`u32`)는 해당하지 않으며, 2바이트 이상 타입은 시험하지 않았습니다.

## 1. 시나리오

| 항목 | 내용 |
|---|---|
| 사용자 | 사내 매출 리포트를 만드는 백엔드 개발자 |
| 상황 | 매출 리포트용으로 금액 1,000~5,000원 주문만 추출한다 (1,000 이상 5,000 미만). 주문 금액 컬럼(원 단위 정수)이 큰 이진 파일로 저장되어 있다 |
| 가속하는 단계 | 컬럼 조건 검사 |
| 입력 → 출력 | u32 금액 컬럼 → 조건을 만족한 값 |
| 완료 기준 | Python 필터와 값·순서 일치 |

## 2. 연산 요약

| 항목 | 내용 |
|---|---|
| 입력 | 한 타입 `T`의 값을 little-endian으로 이어 쓴 파일 (헤더 없음). 빈 파일 허용 |
| 출력 | 함수가 `true`를 돌려준 값만, 입력 순서대로, 같은 타입 `T`로 이어 쓴 파일. 하나도 없으면 빈 파일 |
| 사용자 함수 | `inline bool isc_filter(T value)` — 값 하나를 받아 남길지 정한다 |
| 파라미터 | 없음 |
| 지원하지 않는 것 | 여러 컬럼을 함께 보는 조건(행 단위 레코드), 앞뒤 값을 참조하는 조건, 출력 타입 변경(→ [`map`](../map/README.md)) |

작은 예: `T = u32`, 조건 `value >= 1000 && value < 5000`, 입력 `[500, 1200, 4999, 5000, 3000]` → 출력 `[1200, 4999, 3000]`

## 3. 준비

- 공통 설치와 환경 확인: [README 1절](../../../README.md#1-설치-및-환경-구성) (`isc doctor`가 카드를 보여야 `hw` 실행 가능)
- 이 연산은 `VITIS_LIBRARIES`가 필요 없습니다.
- 모든 명령은 **저장소 루트**에서 실행합니다. 실습 파일은 `datasets/filter/`(git에 올라가지 않음)에 만듭니다.

## 4. 개발 절차

### 4.1. 입력 만들기

금액 10,000건(0~9,999원, 고정 시드)을 `u32` little-endian으로 씁니다. 실제 데이터라면 같은 형식으로 내보내면 됩니다
(예: numpy `arr.astype("<u4").tofile(경로)`).

```bash
mkdir -p datasets/filter
python3 - <<'EOF'
import random, struct
rng = random.Random(5)
amounts = [rng.randint(0, 9999) for _ in range(10000)]
open("datasets/filter/amounts.bin", "wb").write(struct.pack(f"<{len(amounts)}I", *amounts))
EOF
```

### 4.2. 로직 파일 만들기

입력 타입에 맞춰 시작 파일을 받습니다. `--type`이 함수 인자 타입을 정합니다(`u32` → `uint32_t`).

```bash
isc template filter --type u32 -o datasets/filter/amount_filter.hpp
```

편집기로 열어 **`return` 줄 하나만** 고칩니다. 파일 이름(`amount_filter`)이 모듈 이름이 됩니다.

```cpp
inline bool isc_filter(uint32_t value) {
    return value >= 1000 && value < 5000;     // 고친 줄 (ISC-TODO 주석도 함께 지움)
}
```

- 함수 이름 `isc_filter`와 인자 이름 `value`는 바꾸지 않습니다. 조건에는 비교·논리·비트 연산과 상수를 씁니다.
- 실수 입력(`--type f32`)이면 상수에 `f`를 붙입니다: `return value >= -3.0f && value <= 3.0f;`
- 쓸 수 없는 것: 동적 할당(`new`, `malloc`), 파일·소켓 입출력, `#pragma HLS INTERFACE`.

### 4.3. 에뮬레이션 실행 (hw_emu)

로직 파일을 그대로 실행하면 플랫폼이 `modules/amount_filter/`를 만들고, 정적 검사 → 빌드(처음 한 번 수 분) → 실행 → 기록까지 합니다.

```bash
isc run datasets/filter/amount_filter.hpp --input datasets/filter/amounts.bin --output datasets/filter/result.bin
```

실제 출력 (발췌):

```text
amount_filter: created /home/.../ISC_Management_Platform/modules/amount_filter (profile filter)
amount_filter [hw_emu] succeeded  run_id=20261105T110128-e2449f
  device     : [0] xilinx_u50_gen3x16_xdma_5_202210_1  (emulated)
  build      : cache hit  bc62d3c04c40cb7b8a9946ba72371ead
  input      : 40000 bytes  /home/.../datasets/filter/amounts.bin
  output     : 16088 bytes  /home/.../datasets/filter/result.bin
  validation : PASS  (cross_target) ran on the reference target itself; nothing to compare
```

`validation : PASS`는 hw_emu가 자기 자신을 기준으로 삼았다는 뜻일 뿐입니다. 로직이 맞는지는 다음 단계에서 확인합니다.

### 4.4. 결과 확인

출력 파일을 `u32`로 풀어 같은 조건을 Python으로 적용한 결과와 비교합니다.

```bash
python3 - <<'EOF'
import struct
data = open("datasets/filter/amounts.bin", "rb").read()
amounts = struct.unpack(f"<{len(data)//4}I", data)
expected = [v for v in amounts if 1000 <= v < 5000]
out = open("datasets/filter/result.bin", "rb").read()
got = list(struct.unpack(f"<{len(out)//4}I", out))
print(f"input={len(amounts)} kept={len(got)} first5={got[:5]}")
print("MATCH" if got == expected else "MISMATCH")
EOF
```

```text
input=10000 kept=4022 first5=[4185, 4080, 2569, 1854, 4039]
MATCH
```

### 4.5. U50 실행 (hw)

같은 명령에 `--target hw`를 붙입니다. 플랫폼이 hw_emu 기준 출력을 먼저 만들고, hw 빌드(처음 한 번 수십 분) → 카드 실행 → 두 출력의 바이트 비교를 합니다.
결과는 다른 파일로 받아 4.4의 확인 코드를 `result_hw.bin`에 한 번 더 적용합니다.

```bash
isc run datasets/filter/amount_filter.hpp --input datasets/filter/amounts.bin --output datasets/filter/result_hw.bin --target hw
```

실제 출력 (발췌, 빌드 캐시 재사용):

```text
amount_filter [hw] succeeded  run_id=20261105T111257-0e565f
  device     : [0] xilinx_u50_gen3x16_xdma_base_5
  build      : cache hit  cac128fa1db2dfe650326544f7b4ef78
  input      : 40000 bytes  /home/.../datasets/filter/amounts.bin
  output     : 16088 bytes  /home/.../datasets/filter/result_hw.bin
  kernel_ms  : 0.52
  validation : PASS  (cross_target) matches reference target output (16088 bytes)
```

`validation : PASS ... matches reference target output`는 카드 출력이 hw_emu 출력과 바이트까지 같다는 뜻입니다. 결과 확인 코드를 hw 출력에 적용한 결과:

```text
input=10000 kept=4022 first5=[4185, 4080, 2569, 1854, 4039]
MATCH
```

### 4.6. 기록 확인

```bash
isc records --module amount_filter
isc show [실행 ID]          # 입력·출력 sha256, 빌드 지문, 단계별 시간
```

### 4.7. 로직을 바꿀 때

`datasets/filter/amount_filter.hpp`를 고친 뒤 4.3의 명령을 다시 실행하면 모듈의 `logic.hpp`가 갱신되고(`updated`), 바뀐 로직으로 다시 빌드합니다.
상수 하나만 바꿔도 새 빌드이므로 hw_emu에서 충분히 확인한 뒤 hw로 실행합니다.

## 5. 내 데이터에 적용하기

| 바꿀 것 | 방법 |
|---|---|
| 값 타입 | `isc template filter --type <T>` 의 `<T>`와 입력 파일의 타입을 함께 바꿈 (`struct` 코드: `u8 B`, `u16 H`, `u32 I`, `u64 Q`, `i8 b`, `i16 h`, `i32 i`, `i64 q`, `f32 f`) |
| 조건 | `return` 줄만 수정. 예: `return value >= 1000;`, `return (value & 1) == 0;`, `return value == value;` (f32에서 NaN 제외) |
| 모듈 이름 | 로직 파일 이름을 바꾸거나 `isc new-custom <모듈> --from <파일>.hpp` |
| 입력 크기 | 파일 크기가 값 크기의 배수가 아니면 끝에 남는 바이트는 오류 없이 무시됨. 미리 맞춰 둘 것 |

## 6. 문제 해결

| 증상 | 원인 | 해결 |
|---|---|---|
| `warning: ... still has the starter marker ISC-TODO` | `return` 줄을 고치지 않았거나 주석을 남김 | `return` 줄 작성 후 `ISC-TODO` 주석 삭제 |
| `no hook profile matches this logic file` | 함수 이름이 `isc_filter`가 아님 | 함수 이름을 되돌림 |
| ``unsupported type `...` for the parameter of ...`` | 지원하지 않는 인자 타입 (`double`, `int` 등) | `--type`으로 지원 타입을 골라 시작 파일을 다시 받음 |
| `hook source contains unsupported dynamic allocation (or host I/O)` | `new`, `malloc`, `fopen` 등 사용 | 해당 코드 삭제 |
| 개수·값이 CPU 결과와 다름 | 입력 파일 타입과 `--type`이 다름, 또는 경계 조건(`<` / `<=`) 차이 | 4.4 코드의 `struct` 코드와 조건을 로직과 같게 맞춰 원인 확인 |
| 빌드 실패 | 로직이 합성되지 않음 | `artifacts/cache/<지문>/build.log`와 생성된 래퍼 소스 확인 |

## 7. 검증 기록

### 7.1. 플랫폼 자체 테스트

| 항목 | 내용 |
|---|---|
| 확인일 | 2026-11-05 |
| 환경 | Ubuntu 22.04.5 LTS (커널 6.8.0-138), Python 3.10.12, Vitis 2025.1, XRT 2.19.194 (2025.1), Vitis Libraries `v2025.1_update2` (커밋 b2c657d), AMD Alveo U50 (`xilinx_u50_gen3x16_xdma_base_5`), 저장소 기준 커밋 `8742866` |
| 실행한 단계 | 이 문서의 명령을 저장소 루트에서 순서대로 그대로 실행 (입력 만들기 → 모듈 생성 → 검사 → hw_emu → 결과 확인 → hw → 결과 확인) |
| hw_emu | 통과 — 실행 `20261105T110128-e2449f`, Python 필터와 일치 (4,022개, 순서 포함) |
| hw (U50) | 통과 — 실행 `20261105T111257-0e565f`, hw 출력 = hw_emu 출력(플랫폼 자동 대조) = CPU 참조 (Python 필터) |
| 확인하지 않은 것 | 성능(가속 효과)은 측정하지 않음. 이 문서의 입력 한 가지로만 확인 |

### 7.2. 공식 라이브러리 테스트

**해당 없음** — 이 연산에 대응하는 Vitis Libraries 공식 테스트가 없습니다 (플랫폼 전용 연산).
