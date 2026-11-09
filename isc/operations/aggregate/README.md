# `aggregate` — 조건에 맞는 값의 건수·합계·최솟값·최댓값 (Hook형)

| 개발 방식 | 사용자가 작성하는 것 | 커널 | 지원 타입 | 플랫폼 자체 테스트 | 공식 라이브러리 테스트 |
|---|---|---|---|---|---|
| Hook형 | `bool isc_where(T value)`의 `return` 한 줄 | 플랫폼이 생성하는 래퍼 커널(`kernel/aggregate.cpp.tmpl`)에 사용자 함수를 끼워 빌드 | 정수만: `u8 u16 u32 u64 i8 i16 i32 i64` (`f32` 불가) | ✅ 통과 (2026-11-05, `i32`) | — 해당 없음 |

## 1. 시나리오

| 항목 | 내용 |
|---|---|
| 사용자 | 결제 데이터를 분석하는 데이터 분석가 |
| 상황 | 1,000원 미만 소액 거래(환불 포함)의 건수·합계·최소·최대를 매일 산출한다. 거래 금액은 원 단위 `i32`이고 환불은 음수다 |
| 가속하는 단계 | 조건부 집계 |
| 입력 → 출력 | i32 금액 컬럼 → count·sum·min·max |
| 완료 기준 | Python 집계와 일치 |

## 2. 연산 요약

| 항목 | 내용 |
|---|---|
| 입력 | 한 정수 타입 `T`의 값을 little-endian으로 이어 쓴 파일 (헤더 없음). 빈 파일 허용 |
| 출력 | 고정 32바이트: little-endian 64비트 값 4개 `count, sum, min, max` |
| 부호 | 부호 있는 `T`(`i8`~`i64`)는 64비트로 부호 확장해 집계 → `sum`·`min`·`max`를 **부호 있는 `i64`** 로 읽음 (`struct` `"<Qqqq"`). 부호 없는 `T`는 `"<QQQQ"` |
| 넘침 | `sum`은 64비트에서 넘치면 감김(wrap) |
| 해당 없음 | 조건을 만족한 값이 하나도 없으면 네 값 모두 0 |
| 사용자 함수 | `inline bool isc_where(T value)` — `true`인 값만 집계 |
| 파라미터 | 없음 |
| 지원하지 않는 것 | 평균·분산 등 다른 통계(평균은 `sum / count`로 직접 계산), 그룹별 집계(GROUP BY), 실수(`f32`) 입력, 여러 컬럼 |

작은 예: `T = i32`, 조건 `value < 1000`, 입력 `[500, -200, 1500, 999]` → `count=3, sum=1299, min=-200, max=999`

## 3. 준비

- 공통 설치와 환경 확인: [README 1절](../../../README.md#1-설치-및-환경-구성)
- 이 연산은 `VITIS_LIBRARIES`가 필요 없습니다.
- 모든 명령은 **저장소 루트**에서 실행합니다. 실습 파일은 `datasets/aggregate/`(git에 올라가지 않음)에 만듭니다.

## 4. 개발 절차

### 4.1. 입력 만들기

거래 10,000건(-2,000 ~ 20,000원, 음수는 환불, 고정 시드)을 `i32` little-endian으로 씁니다.

```bash
mkdir -p datasets/aggregate
python3 - <<'EOF'
import random, struct
rng = random.Random(21)
tx = [rng.randint(-2000, 20000) for _ in range(10000)]   # 음수 = 환불
open("datasets/aggregate/tx.bin", "wb").write(struct.pack(f"<{len(tx)}i", *tx))
EOF
```

### 4.2. 로직 파일 만들기

```bash
isc template aggregate --type i32 -o datasets/aggregate/small_tx.hpp
```

편집기로 열어 **`return` 줄 하나만** 고칩니다. 파일 이름(`small_tx`)이 모듈 이름이 됩니다.

```cpp
inline bool isc_where(int32_t value) {
    return value < 1000;     // 고친 줄 (ISC-TODO 주석도 함께 지움)
}
```

- 함수 이름 `isc_where`와 인자 이름 `value`는 바꾸지 않습니다. 모든 값을 집계하려면 `return true;`로 두고 `ISC-TODO` 주석만 지웁니다.
- 쓸 수 없는 것: 동적 할당, 파일·소켓 입출력, `#pragma HLS INTERFACE`.

### 4.3. 에뮬레이션 실행 (hw_emu)

```bash
isc run datasets/aggregate/small_tx.hpp --input datasets/aggregate/tx.bin --output datasets/aggregate/result.bin
```

실제 출력 (발췌):

```text
small_tx: created /home/.../ISC_Management_Platform/modules/small_tx (profile aggregate)
small_tx [hw_emu] succeeded  run_id=20261105T110128-245685
  device     : [0] xilinx_u50_gen3x16_xdma_5_202210_1  (emulated)
  build      : cache hit  80b9201ff8870989e4cb79b9170872d9
  input      : 40000 bytes  /home/.../datasets/aggregate/tx.bin
  output     : 32 bytes  /home/.../datasets/aggregate/result.bin
  validation : PASS  (cross_target) ran on the reference target itself; nothing to compare
```

`validation : PASS`는 hw_emu가 자기 자신을 기준으로 삼았다는 뜻일 뿐입니다. 집계가 맞는지는 다음 단계에서 확인합니다.

### 4.4. 결과 확인

32바이트를 `count`(부호 없음)와 `sum`·`min`·`max`(입력이 `i32`이므로 부호 있음)로 풀어 Python 집계와 비교합니다.

```bash
python3 - <<'EOF'
import struct
data = open("datasets/aggregate/tx.bin", "rb").read()
tx = struct.unpack(f"<{len(data)//4}i", data)
sel = [v for v in tx if v < 1000]
expected = (len(sel), sum(sel), min(sel), max(sel)) if sel else (0, 0, 0, 0)
count, total, low, high = struct.unpack("<Qqqq", open("datasets/aggregate/result.bin", "rb").read())
print(f"count={count} sum={total} min={low} max={high}")
print("MATCH" if (count, total, low, high) == expected else "MISMATCH")
EOF
```

```text
count=1390 sum=-694618 min=-1998 max=999
MATCH
```

### 4.5. U50 실행 (hw)

```bash
isc run datasets/aggregate/small_tx.hpp --input datasets/aggregate/tx.bin --output datasets/aggregate/result_hw.bin --target hw
```

플랫폼이 hw_emu 기준 출력을 먼저 만들고 → hw 빌드(처음 한 번 수십 분) → 카드 실행 → 두 출력의 바이트 비교를 합니다. 4.4의 코드에서 `result.bin`을 `result_hw.bin`으로 바꿔 한 번 더 확인합니다.

실제 출력 (발췌, 빌드 캐시 재사용):

```text
small_tx [hw] succeeded  run_id=20261105T111257-c24f46
  device     : [0] xilinx_u50_gen3x16_xdma_base_5
  build      : cache hit  931000e77c59334ff944d44307a5feba
  input      : 40000 bytes  /home/.../datasets/aggregate/tx.bin
  output     : 32 bytes  /home/.../datasets/aggregate/result_hw.bin
  kernel_ms  : 5.414
  validation : PASS  (cross_target) matches reference target output (32 bytes)
```

`validation : PASS ... matches reference target output`는 카드 출력이 hw_emu 출력과 바이트까지 같다는 뜻입니다. 결과 확인 코드를 hw 출력에 적용한 결과:

```text
count=1390 sum=-694618 min=-1998 max=999
MATCH
```

### 4.6. 기록 확인

```bash
isc records --module small_tx
isc show [실행 ID]
```

### 4.7. 로직을 바꿀 때

`datasets/aggregate/small_tx.hpp`를 고친 뒤 4.3의 명령을 다시 실행하면 모듈의 `logic.hpp`가 갱신되고(`updated`) 다시 빌드합니다. 상수 하나만 바꿔도 새 빌드이므로 hw_emu에서 확인한 뒤 hw로 실행합니다.

## 5. 내 데이터에 적용하기

| 바꿀 것 | 방법 |
|---|---|
| 값 타입 | `isc template aggregate --type <T>`와 입력 파일 타입을 함께 바꿈 (정수만). 결과 해석: 부호 있는 `T` → `"<Qqqq"`, 부호 없는 `T` → `"<QQQQ"` |
| 조건 | `return` 줄만 수정. 예: `return value >= 100 && value < 200;`, `return (value & 1) == 0;` (짝수) |
| 평균 | `sum / count` (count가 0이면 해당 없음) |
| 여러 조건의 집계 | 조건마다 로직 파일(모듈)을 따로 만들어 실행 |

## 6. 문제 해결

| 증상 | 원인 | 해결 |
|---|---|---|
| `error: unsupported --type 'f32'; choose one of: u8, u16, ...` (`isc template`) | `aggregate`는 정수만 지원 | 실수는 고정 소수점 정수로 바꿔 저장 (예: 0.01 단위 → ×100 후 `i32`/`i64`) |
| ``error: unsupported type `float` for the parameter of `isc_where`; ...`` (`isc run`, `isc new-custom --from`) | 직접 쓴 로직 파일의 인자 타입이 실수 | 위와 같이 정수 타입으로 바꿈 |
| `sum`이 매우 큰 양수로 보임 | 부호 있는 입력의 결과를 `"<QQQQ"`로 읽음 | 4.4처럼 `"<Qqqq"`로 읽음 |
| 네 값이 모두 0 | 조건을 만족한 값이 없음 | 정상 동작. 조건과 입력 범위 확인 |
| `warning: ... still has the starter marker ISC-TODO` | `return` 줄을 고치지 않았거나 주석을 남김 | `return` 줄 작성 후 `ISC-TODO` 주석 삭제 |
| `no hook profile matches this logic file` | 함수 이름이 `isc_where`가 아님 | 함수 이름을 되돌림 |

## 7. 검증 기록

### 7.1. 플랫폼 자체 테스트

| 항목 | 내용 |
|---|---|
| 확인일 | 2026-11-05 |
| 환경 | Ubuntu 22.04.5 LTS (커널 6.8.0-138), Python 3.10.12, Vitis 2025.1, XRT 2.19.194 (2025.1), Vitis Libraries `v2025.1_update2` (커밋 b2c657d), AMD Alveo U50 (`xilinx_u50_gen3x16_xdma_base_5`), 저장소 기준 커밋 `8742866` |
| 실행한 단계 | 이 문서의 명령을 저장소 루트에서 순서대로 그대로 실행 (입력 만들기 → 모듈 생성 → 검사 → hw_emu → 결과 확인 → hw → 결과 확인) |
| hw_emu | 통과 — 실행 `20261105T110128-245685`, Python 집계와 일치 (`count=1390 sum=-694618 min=-1998 max=999`) |
| hw (U50) | 통과 — 실행 `20261105T111257-c24f46`, hw 출력 = hw_emu 출력(플랫폼 자동 대조) = CPU 참조 (Python 집계) |
| 확인하지 않은 것 | 성능(가속 효과)은 측정하지 않음. 이 문서의 입력 한 가지로만 확인 |

### 7.2. 공식 라이브러리 테스트

**해당 없음** — 이 연산에 대응하는 Vitis Libraries 공식 테스트가 없습니다 (플랫폼 전용 연산).
