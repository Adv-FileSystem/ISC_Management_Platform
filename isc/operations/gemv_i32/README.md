# `gemv_i32` — 대출 심사 점수 모델로 고객 점수 일괄 계산 (연산 선택형)

| 개발 방식 | 사용자가 작성하는 것 | 커널 | 파라미터 | 플랫폼 자체 테스트 | 공식 라이브러리 테스트 |
|---|---|---|---|---|---|
| 연산 선택형 | 없음 (`isc new` 한 줄) | Vitis BLAS L1 `xf::blas::gemv` (`kernel/gemv_i32_kernel.cpp`, 공식 L1 테스트 top `uut_top.cpp`와 같은 구성) | 없음 (α·β는 입력 헤더에 씀) | ✅ 통과 (2026-11-05) | 미실행 |

> 공식 L1 테스트 top(`blas/L1/tests/hw/gemv/uut_top.cpp`)의 구성(`gem2Stream` → `gemv` → `writeStream2Vec`, 4 lane)을 그대로 쓰고, 배열만 메모리 포트로 바꿨습니다.

## 1. 시나리오

| 항목 | 내용 |
|---|---|
| 사용자 | 금융사 대출 심사 시스템 개발자 |
| 상황 | 대출 심사용 선형 점수 모델로 고객 수천 명의 점수를 한 번에 계산한다 (점수 = α·(고객 특성 행렬 A)·(가중치 x) + β·(기본 점수 y)) |
| 가속하는 단계 | 행렬-벡터 곱 |
| 입력 → 출력 | 고객 특성 행렬 A(int32), 가중치 x, 기본 점수 y → 고객별 점수 |
| 완료 기준 | numpy 계산과 바이트 일치 |

## 2. 연산 요약

| 항목 | 내용 |
|---|---|
| 입력 | 리틀 엔디안 `u32 m, u32 n, i32 alpha, i32 beta` + `i32 A[m][n]`(행 우선) + `i32 x[n]` + `i32 y[m]` |
| 출력 | `i32 y_out[m]` = `alpha·A·x + beta·y` (int32 산술. 넘치면 2^32로 감김) |
| 파라미터 | 없음. `alpha`, `beta`는 입력 헤더에 씀 |
| 크기 한계 | `m ≥ 1`, `n ≥ 1`, 행렬이 HBM 뱅크 하나(256 MiB)에 들어가야 함 (`m × n` 약 6,700만 이하) |
| 열 수 맞춤 | 커널은 한 번에 4열씩 곱합니다. `n`이 4의 배수가 아니면 플랫폼이 0으로 채워 보내므로 사용자가 맞출 필요는 없습니다 |
| CPU 참조 | `baseline.py` — numpy int64로 곱한 뒤 int32로 자름. 장치 출력과 **바이트까지 같아야** 함 |
| 메모리 | 포트 4개(A, x, y, y_out)를 HBM 뱅크 0~3에 연결 (`operation.yaml`) |

## 3. 준비

- 공통 설치와 환경 확인: [README 1절](../../../README.md#1-설치-및-환경-구성)
- 빌드에 **`VITIS_LIBRARIES`** 가 필요합니다: `export VITIS_LIBRARIES=[Vitis Libraries 2025.1 경로]`
- 4.1의 예시 입력과 4.5의 결과 확인에는 Python `numpy`가 필요합니다 (검증 때 numpy 2.2.6).
- 모든 명령은 **저장소 루트**에서 실행합니다. 실습 파일은 `datasets/pscen/`(git에 올라가지 않음)에 만듭니다.

## 4. 개발 절차

### 4.1. 입력 만들기

고객 512명 × 특성 64개의 정수 특성 행렬(−124~124, 공식 L1 테스트와 같은 값 범위), 특성별 가중치, 기본 점수 600을 만듭니다 (고정 시드).

```bash
python3 isc/operations/gemv_i32/examples/make_input.py
```

```text
m 512 n 64 bytes 133392
```

입력 파일은 [`examples/make_input.py`](examples/make_input.py)가 만듭니다. 스크립트는 두 부분으로 나뉩니다.

| 부분 | 하는 일 | 사용자 |
|---|---|---|
| `make_sample()` | 예시 데이터를 만듦 | **바꾸는 곳** — 자기 데이터로 바꿈 |
| `encode()` | 2절의 입력 형식(헤더·엔디안·타입)대로 바이트를 씀 | 고치지 않음 |

- 내 데이터는 `make_sample()`이 돌려주는 값을 같은 모양으로 바꿔 넣습니다 (예: `np.load(...)`로 읽은 배열).
- 저장 위치를 바꾸려면 `-o 경로`를 붙입니다.

### 4.2. 모듈 만들기

```bash
isc describe gemv_i32                       # 입력·출력 형식 확인 (선택)
isc new credit_score --operation gemv_i32
```

생성되는 `modules/credit_score/module.yaml` — 고칠 것이 없습니다.

```yaml
module:
  id: credit_score
  name: credit_score
  version: 0.1.0
operation:
  id: gemv_i32
  version: '1.0'
```

### 4.3. 검사

```bash
isc check credit_score
```

```text
credit_score: PASS
```

### 4.4. 에뮬레이션 실행 (hw_emu)

```bash
isc run credit_score --input datasets/pscen/credit_512x64.bin --output datasets/pscen/credit_score.emu.bin
```

처음 실행하면 hw_emu 빌드(수 분)를 한 뒤 실행합니다. 빌드가 캐시에 있으면 이 입력은 약 30초 걸립니다.

### 4.5. 결과 확인

연산 폴더의 CPU 참조(`baseline.py`)로 같은 입력을 계산해 바이트 비교합니다.

```bash
python3 - <<'EOF'
import sys, numpy as np
sys.path.insert(0, "isc/operations/gemv_i32")
from baseline import compute
inp = open("datasets/pscen/credit_512x64.bin", "rb").read()
got = open("datasets/pscen/credit_score.emu.bin", "rb").read()
score = np.frombuffer(got, "<i4")
print("customers", len(score), "min", score.min(), "max", score.max(), "first", score[:3].tolist())
print("MATCH" if got == compute(inp, {}) else "MISMATCH")
EOF
```

```text
customers 512 min -122813 max 122734 first [-73301, -22887, 27422]
MATCH
```

### 4.6. U50 실행 (hw)

```bash
isc run credit_score --input datasets/pscen/credit_512x64.bin --output datasets/pscen/credit_score.hw.bin --target hw
```

플랫폼이 hw_emu 기준 출력을 먼저 만들고 → hw 빌드 → 카드 실행 → 두 출력을 바이트 비교합니다. 이 서버에서 hw 빌드는 약 47분 걸렸습니다(처음 한 번). 4.5의 코드에서 `credit_score.emu.bin`을 `credit_score.hw.bin`으로 바꿔 한 번 더 확인합니다.

실제 출력 (발췌):

```text
credit_score [hw] succeeded  run_id=20261110T105407-51b6c2
  input      : 133392 bytes  .../datasets/pscen/credit_512x64.bin
  output     : 2048 bytes  .../datasets/pscen/credit_score.hw.bin
  kernel_ms  : 0.156
  validation : PASS  (cross_target) matches reference target output (2048 bytes)
```

### 4.7. 기록 확인

```bash
isc records --module credit_score
isc show [실행 ID]
```

## 5. 내 데이터에 적용하기

| 바꿀 것 | 방법 |
|---|---|
| 고객 특성 | 실수 특성은 정수 척도로 바꿔(예: ×100 후 반올림) `A`에 씀. 결과도 같은 척도로 해석 |
| 가중치·기본 점수 | `x`, `y`를 바꿈. 기본 점수가 없으면 `beta = 0` |
| 고객 수 | 헤더의 `m`을 바꿈. 모듈은 그대로 재사용 (크기는 실행 인자라 다시 빌드하지 않음) |
| 큰 값 | 곱의 합이 int32 범위(약 ±21억)를 넘으면 감겨서 틀린 값이 됨. 특성·가중치 척도를 줄임 |

## 6. 문제 해결

| 증상 | 원인 | 해결 |
|---|---|---|
| `gemv_i32 input needs a 16-byte header; received ...` | 헤더가 없거나 짧음 | `u32 m, u32 n, i32 alpha, i32 beta`를 앞에 씀 |
| `gemv_i32 needs m >= 1 and n >= 1, ...` | 크기가 0 | 헤더의 `m`, `n` 확인 |
| `gemv_i32 header says m=..., n=..., which is ... bytes; received ...` | 헤더와 파일 길이가 다름 | 파일 길이 = `16 + 4·(m·n + n + m)` |
| `gemv_i32 matrix of ...x... does not fit one 256 MiB HBM bank` | 행렬이 너무 큼 | 고객을 나눠 여러 번 실행 |
| numpy 결과와 다름 | numpy를 int32로 바로 곱해 중간값 처리가 다름, 또는 행/열 순서 | 4.5처럼 `baseline.py`(int64로 곱한 뒤 int32로 자름)와 비교. `A`는 행 우선 |
| `... requires an environment variable for include path '${VITIS_LIBRARIES}/...'` (`isc check`) | `VITIS_LIBRARIES` 미설정 | `export VITIS_LIBRARIES=[경로]` 후 다시 검사 |

## 7. 검증 기록

### 7.1. 플랫폼 자체 테스트

| 항목 | 내용 |
|---|---|
| 확인일 | 2026-11-05 (시나리오 검증), 2026-11-10 (이 문서 절차 재실행) |
| 환경 | Ubuntu 22.04.5 LTS (커널 6.8.0-138), Python 3.10.12, Vitis 2025.1, XRT 2.19.194 (2025.1), Vitis Libraries `v2025.1_update2` (커밋 b2c657d), AMD Alveo U50 (`xilinx_u50_gen3x16_xdma_base_5`), 저장소 기준 커밋 `3237e67` + 이 연산 폴더(커밋 전) |
| 시나리오 검증 (11-05) | 모듈 `loan_score`, 대출 점수 512×64 (y = Ax + 600). hw = hw_emu 일치(플랫폼 대조 PASS), numpy와 바이트 일치 — 실행 `20261105T171642-107156` |
| 이 문서 절차 (11-10) | 4.1~4.6을 저장소 루트에서 그대로 실행. 입력 `credit_512x64.bin` (133,392바이트, sha256 `0e6bb4f8…`). hw_emu `20261110T105025-235709` = CPU 참조 바이트 일치. hw `20261110T105407-51b6c2` = hw_emu(플랫폼 대조 PASS) = CPU 참조 바이트 일치 |
| 빌드 | hw 빌드 약 47분 (2026-11-04), 타이밍 미달로 인한 클럭 축소 없음 |
| 추가 크기 확인 | 64×130 (α=3, β=−2, `n`이 4의 배수가 아닌 경우) — hw_emu = numpy 바이트 일치, hw = hw_emu (플랫폼 대조 PASS) |
| 확인하지 않은 것 | 성능(가속 효과). 1024×4096 같은 큰 행렬 |

### 7.2. 공식 라이브러리 테스트

**미실행** — 공식 문서가 가리키는 GEMV 벤치마크(`L2/benchmarks/streamingKernel/gemvStreamCh16`)가 2024.2·2025.1 저장소에 없습니다. L3는 CPU·GPU 비교용이고, L1 `tests/hw/gemv`는 HLS 단독 시험(카드 실행 없음)입니다. 그래서 L1 테스트 top과 같은 구성을 플랫폼 연산으로 만들어 7.1로 검증했습니다.
