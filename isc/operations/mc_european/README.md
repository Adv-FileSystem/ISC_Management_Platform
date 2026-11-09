# `mc_european` — 리스크팀의 유럽형 옵션 가격 야간 재계산 (연산 선택형)

| 개발 방식 | 사용자가 작성하는 것 | 커널 | 파라미터 | 플랫폼 자체 테스트 | 공식 라이브러리 테스트 |
|---|---|---|---|---|---|
| 연산 선택형 | 없음 (`isc new` 한 줄) | Vitis Quantitative Finance L2 `xf::fintech::MCEuropeanEngine` (`kernel/mc_european_kernel.cpp`) | `samples`, `time_steps` | ✅ 통과 (2026-11-05) | ✅ 통과 (2026-11-05) |

> 이 연산은 시나리오 검증용 **프로토타입**입니다. 엔진 호출과 설정(`float`, `UN=2`, 허용오차 0.02)은 공식 L2 테스트 커널(`mc_euro_k.cpp`)과 같고, 메모리에서 읽은 옵션 묶음을 차례로 계산하는 반복만 덧붙였습니다. 옵션마다 시드를 고정해 실행할 때마다 같은 값이 나옵니다.

## 1. 시나리오

| 항목 | 내용 |
|---|---|
| 사용자 | 금융사 리스크팀의 가격 계산 담당 개발자 |
| 상황 | 리스크팀이 매일 밤 보유 유럽형 옵션 가격을 Monte Carlo로 재계산한다 |
| 가속하는 단계 | 경로 시뮬레이션과 가격 계산 |
| 입력 → 출력 | 옵션 조건(기초가, 행사가, 변동성, 만기) → 가격 |
| 완료 기준 | 공식 골든 가격과 상대오차 2% 안 |

**판정 방법.** 공식 테스트(`host/test.cpp`)는 오차를 `|가격 − golden| / 기초가 S`로 정의하고 1% 이하를 통과로 봅니다. 공식 golden은 Black-Scholes 해석해 값입니다(예: `S=100, K=75, σ=0.11, r=0.01, T=1` 콜의 golden 25.7561 = 해석해). 시나리오 입력에는 공식 golden이 따로 없으므로, 같은 해석해(`baseline.py`)를 정답으로 두고 공식 테스트와 같은 식으로 판정했습니다. 이 식으로 1% 이하면 시나리오의 2% 기준도 만족합니다.

## 2. 연산 요약

| 항목 | 내용 |
|---|---|
| 입력 | 리틀 엔디안 이진 파일: `u32 count, u32 0` + 옵션마다 `f32` 8개 (`S, K, sigma, q, r, T, type, 0`). `type` 0 = 콜, 1 = 풋 |
| 출력 | `f32 price[count]` — 입력 순서대로 옵션 가격 |
| 파라미터 | `samples` 옵션당 경로 수 (기본 4096, 1024~1048576. 공식 테스트는 40000), `time_steps` 경로당 시간 단계 (기본 1, 1~100) |
| 한계 | `count` 1~65536. `S, K, sigma, T`는 0보다 커야 함 |
| CPU 참조 | `baseline.py` (Black-Scholes 해석해, `math.erf`). Monte Carlo는 근사값이므로 바이트 일치가 아니라 오차로 판정 |
| 메모리 | 포트 2개 (`operation.yaml`) |

## 3. 준비

- 공통 설치와 환경 확인: [README 1절](../../../README.md#1-설치-및-환경-구성)
- 빌드에 **`VITIS_LIBRARIES`** 가 필요합니다: `export VITIS_LIBRARIES=[Vitis Libraries 2025.1 경로]`
- 모든 명령은 **저장소 루트**에서 실행합니다. 실습 파일은 `datasets/pscen/`(git에 올라가지 않음)에 만듭니다.

## 4. 개발 절차

### 4.1. 입력 만들기

보유 옵션 8건(콜 4, 풋 4)을 만듭니다. 6건은 공식 테스트의 조건표에 있는 조합이고, 2건(`T=0.5`, `T=2`)은 그 밖의 조합입니다.

```bash
mkdir -p datasets/pscen
python3 - <<'EOF'
import struct
# S, K, sigma, q, r, T, type(0 콜, 1 풋)
book = [(100, 75, 0.11, 0.00, 0.01, 1, 0), (100, 100, 0.50, 0.00, 0.05, 1, 0), (100, 125, 1.20, 0.05, 0.15, 1, 0),
        (100, 100, 0.30, 0.02, 0.03, 0.5, 0), (100, 75, 0.11, 0.00, 0.01, 1, 1), (100, 100, 0.50, 0.00, 0.05, 1, 1),
        (100, 125, 1.20, 0.05, 0.15, 1, 1), (100, 110, 0.25, 0.01, 0.04, 2, 1)]
data = struct.pack("<II", len(book), 0) + b"".join(struct.pack("<8f", *o, 0.0) for o in book)
open("datasets/pscen/option_book_8.bin", "wb").write(data)
print(len(book), "options,", len(data), "bytes")
EOF
```

```text
8 options, 264 bytes
```

### 4.2. 모듈 만들기

```bash
isc describe mc_european                    # 입력·출력 형식 확인 (선택)
isc new option_book --operation mc_european
```

생성되는 `modules/option_book/module.yaml`:

```yaml
module:
  id: option_book
  name: option_book
  version: 0.1.0
operation:
  id: mc_european
  version: '1.0'
parameters:
  samples: 4096
  time_steps: 1
```

`samples`·`time_steps`는 호스트에서 넘기는 값이라 바꿔도 다시 빌드하지 않습니다. 경로 수를 늘리면 오차가 줄고 실행 시간이 늘어납니다.

### 4.3. 검사

```bash
isc check option_book
```

### 4.4. 에뮬레이션 실행 (hw_emu)

```bash
isc run option_book --input datasets/pscen/option_book_8.bin --output datasets/pscen/option_book.emu.bin
```

### 4.5. 결과 확인

옵션마다 FPGA 가격을 Black-Scholes 해석해와 비교합니다. 공식 테스트의 판정식(`|d|/S`)과 가격 대비 오차(`|d|/BS`)를 함께 출력합니다.

```bash
python3 - datasets/pscen/option_book.emu.bin <<'EOF'
import sys, struct
sys.path.insert(0, "isc/operations/mc_european")
from baseline import compute
from mc_format import options
inp = open("datasets/pscen/option_book_8.bin", "rb").read()
got = open(sys.argv[1], "rb").read()
opts = options(inp)
fpga = struct.unpack(f"<{len(opts)}f", got)
bs = struct.unpack(f"<{len(opts)}f", compute(inp, {}))
worst = 0.0
for (s, k, sig, q, r, t, kind, _), g, e in zip(opts, fpga, bs):
    err_s = abs(g - e) / s                      # 공식 테스트 판정식: |가격 - 정답| / 기초가
    worst = max(worst, err_s)
    print(f"{'put ' if kind else 'call'} K={k:4.0f} sigma={sig:.2f} T={t:g}: fpga={g:8.4f} BS={e:8.4f} "
          f"|d|/S={err_s * 100:.3f}%  |d|/BS={abs(g - e) / e * 100:.3f}%")
print(f"max |d|/S = {worst * 100:.3f}%  ->", "PASS" if worst <= 0.01 else "FAIL", "(official rule: <= 1%)")
EOF
```

U50(hw) 출력에 적용한 결과 (hw_emu 출력과 바이트까지 같으므로 값도 같습니다):

```text
call K=  75 sigma=0.11 T=1: fpga= 25.6835 BS= 25.7561 |d|/S=0.073%  |d|/BS=0.282%
call K= 100 sigma=0.50 T=1: fpga= 21.5879 BS= 21.7926 |d|/S=0.205%  |d|/BS=0.939%
call K= 125 sigma=1.20 T=1: fpga= 40.0326 BS= 39.7416 |d|/S=0.291%  |d|/BS=0.732%
call K= 100 sigma=0.30 T=0.5: fpga=  8.6017 BS=  8.5913 |d|/S=0.010%  |d|/BS=0.120%
put  K=  75 sigma=0.11 T=1: fpga=  0.0092 BS=  0.0098 |d|/S=0.001%  |d|/BS=6.616%
put  K= 100 sigma=0.50 T=1: fpga= 16.9149 BS= 16.9155 |d|/S=0.001%  |d|/BS=0.004%
put  K= 125 sigma=1.20 T=1: fpga= 51.7792 BS= 52.2071 |d|/S=0.428%  |d|/BS=0.820%
put  K= 110 sigma=0.25 T=2: fpga= 16.0924 BS= 15.8314 |d|/S=0.261%  |d|/BS=1.649%
max |d|/S = 0.428%  -> PASS (official rule: <= 1%)
```

가격이 아주 작은 옵션(위의 깊은 외가격 풋, 가격 약 0.01)은 절대 오차가 0.0006이어도 가격 대비 오차가 6.6%로 커집니다. 가격 대비 오차가 필요하면 `samples`를 늘려 다시 실행하십시오 (이 문서에서는 시험하지 않았습니다).

### 4.6. U50 실행 (hw)

```bash
isc run option_book --input datasets/pscen/option_book_8.bin --output datasets/pscen/option_book.hw.bin --target hw
```

플랫폼이 hw_emu 기준 출력을 먼저 만들고 → hw 빌드 → 카드 실행 → 두 출력을 바이트 비교합니다. 이 서버에서 처음 실행(빌드 포함)은 약 67분 걸렸습니다. 4.5의 명령에서 파일 이름을 `option_book.hw.bin`으로 바꿔 확인합니다.

실제 출력 (발췌):

```text
  input      : 264 bytes  .../datasets/pscen/option_book_8.bin
  output     : 32 bytes  .../option_book_option_book_8.hw.bin
  kernel_ms  : 0.442
  validation : PASS  (cross_target) matches reference target output (32 bytes)
```

### 4.7. 기록 확인

```bash
isc records --module option_book
isc show [실행 ID]
```

## 5. 내 데이터에 적용하기

| 바꿀 것 | 방법 |
|---|---|
| 보유 옵션 | 옵션마다 `S, K, sigma, q, r, T, type, 0`을 `f32`로 써서 4.1 형식으로. 최대 65,536건 |
| 정밀도 | `modules/option_book/module.yaml`의 `samples`를 늘림 (공식 테스트는 40000) |
| 결과 사용 | 출력 순서는 입력 순서와 같음 |

## 6. 문제 해결

| 증상 | 원인 | 해결 |
|---|---|---|
| `mc_european count must be 1..65536, the header says ...` | 헤더의 개수가 범위 밖 | 나눠서 실행 |
| `mc_european header says N options = ... bytes; received ...` | 헤더 개수와 파일 길이가 다름 | `8 + 32·count` 바이트로 맞춤 |
| `mc_european option i: S, K, sigma, T must be > 0 and type 0 or 1` | 잘못된 옵션 값 | 값과 `type`(0 콜, 1 풋) 확인 |
| 가격이 해석해와 조금씩 다름 | Monte Carlo 근사 | 정상. 4.5의 오차로 판정 |
| hw 빌드가 메모리 부족으로 중단 | 다른 hw 빌드와 동시에 실행 | 한 번에 하나씩 빌드 (이 서버에서 동시 빌드 중 한 번 발생) |

## 7. 검증 기록

### 7.1. 플랫폼 자체 테스트

| 항목 | 내용 |
|---|---|
| 확인일 | 2026-11-05 |
| 환경 | Ubuntu 22.04.5 LTS (커널 6.8.0-138), Vitis 2025.1, XRT 2.19.194, Vitis Libraries `v2025.1_update2` (커밋 b2c657d), AMD Alveo U50 (`xilinx_u50_gen3x16_xdma_base_5`), 저장소 기준 커밋 `8742866` + 이 연산 폴더(커밋 전) |
| 입력 | 4.1의 `option_book_8.bin` (264바이트, sha256 `9e513277…`), `samples=4096`, `time_steps=1` |
| hw_emu | 통과 — 실행 `20261105T205122-93697b` |
| hw (U50) | **통과** — 실행 `20261105T205122-fc53c6`. hw 출력 = hw_emu 출력(플랫폼 자동 대조). Black-Scholes 해석해 대비 공식 판정식(`\|d\|/S`) 최대 0.428% (기준 1%). 가격 대비 오차는 최대 6.6%(깊은 외가격 풋 1건), 나머지 7건은 1.65% 이하 |
| 참고 | 첫 hw 빌드는 다른 빌드와 동시에 돌다 메모리 부족으로 실패했고, 단독으로 다시 빌드해 통과 |
| 확인하지 않은 것 | 성능(가속 효과). `samples`를 바꾼 실행. 이 문서의 입력 한 가지로만 확인 |

### 7.2. 공식 라이브러리 테스트

Vitis Libraries 공식 테스트를 고치지 않고 U50에서 빌드·실행했습니다. **입력은 공식 테스트에 들어 있는 조건표**이며 위 시나리오 데이터가 아닙니다.

| 항목 | 내용 |
|---|---|
| 공식 테스트 | `quantitative_finance/L2/tests/MCEuropeanEngine` (Vitis Libraries `v2025.1_update2`) |
| 빌드 | `make all TARGET=hw PLATFORM=xilinx_u50_gen3x16_xdma_5_202210_1`, 이 서버의 메모리 제한 때문에 `VPP_LDFLAGS="--vivado.synth.jobs 4 --vivado.impl.jobs 4"`를 붙임 (2026-11-04, 약 46분) |
| 실행 | `make run TARGET=hw PLATFORM=xilinx_u50_gen3x16_xdma_5_202210_1` (공식 `host/test.cpp`의 조건표와 golden) |
| 결과 | **통과** — 2026-11-05, golden 비교(`\|d\|/S ≤ 0.01`) `Info: Test passed` |
