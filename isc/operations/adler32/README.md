# `adler32` — 파일 동기화용 블록 체크섬 (연산 선택형)

| 개발 방식 | 사용자가 작성하는 것 | 커널 | 파라미터 | 플랫폼 자체 테스트 | 공식 라이브러리 테스트 |
|---|---|---|---|---|---|
| 연산 선택형 | 없음 (`isc new` 한 줄) | Vitis Data Compression L1 `xf::compression::adler32_mm` (`kernel/adler32_kernel.cpp`) | 없음 | ✅ 통과 (2026-11-05) | ✅ 통과 (2026-11-05) |

## 1. 시나리오

| 항목 | 내용 |
|---|---|
| 사용자 | 파일 동기화 도구 개발자 |
| 상황 | 파일 동기화 도구가 원격 파일과 바뀐 블록만 찾으려고 블록별 체크섬을 계산한다 (rsync 방식) |
| 가속하는 단계 | 블록 체크섬 계산 |
| 입력 → 출력 | 데이터 블록 → 4바이트 Adler-32 |
| 완료 기준 | `zlib.adler32`와 일치 |

이 문서는 256 KiB 블록 하나를 한 번 실행합니다. 실행 한 번에 체크섬 하나가 나오므로, 블록이 여러 개면 블록마다 실행합니다 (5절).

## 2. 연산 요약

| 항목 | 내용 |
|---|---|
| 입력 | 아무 파일 (원시 바이트, 형식 무관). **1바이트 이상** (빈 파일은 거부) |
| 출력 | 4바이트, Adler-32 값을 **big-endian**으로 쓴 파일 (초기값 1, `zlib.adler32(data)`와 같은 정의) |
| 파라미터 | 없음 |
| 한계 | CRC-32보다 짧은 입력에서 오류 검출력이 약함. 보안(위·변조 방지) 용도가 아님 (→ [`sha256`](../sha256/README.md)) |

작은 예: 입력 `123456789`(ASCII 9바이트) → 출력 `09 1e 01 de` (`0x091e01de`)

## 3. 준비

- 공통 설치와 환경 확인: [README 1절](../../../README.md#1-설치-및-환경-구성)
- 이 연산은 빌드에 **`VITIS_LIBRARIES`** 가 필요합니다: `export VITIS_LIBRARIES=[Vitis Libraries 2025.1 경로]`
- 모든 명령은 **저장소 루트**에서 실행합니다. 실습 파일은 `datasets/adler32/`(git에 올라가지 않음)에 만듭니다.

## 4. 개발 절차

### 4.1. 입력 만들기

전송할 데이터 대신 256 KiB 무작위 파일(고정 시드)을 만들고 기준값을 함께 출력합니다.

```bash
python3 isc/operations/adler32/examples/make_input.py
```

```text
expected adler32 = d9e79171
```

입력 파일은 [`examples/make_input.py`](examples/make_input.py)가 만듭니다. 스크립트는 두 부분으로 나뉩니다.

| 부분 | 하는 일 | 사용자 |
|---|---|---|
| `make_sample()` | 예시 데이터를 만듦 | **바꾸는 곳** — 자기 데이터로 바꿈 |
| `encode()` | 2절의 입력 형식(헤더·엔디안·타입)대로 바이트를 씀 | 고치지 않음 |

- 실제 파일은 변환할 필요가 없습니다. 이 단계를 건너뛰고 그 파일을 바로 `--input`에 넘깁니다.
- 저장 위치를 바꾸려면 `-o 경로`를 붙입니다.

### 4.2. 모듈 만들기

```bash
isc describe adler32                        # 입력·출력 형식 확인 (선택)
isc new transfer_adler --operation adler32
```

생성되는 `modules/transfer_adler/module.yaml` — 고칠 것이 없습니다.

```yaml
module:
  id: transfer_adler    # 변경 금지
  name: transfer_adler  # 수정 가능
  version: 0.1.0        # 수정 가능
operation:
  id: adler32           # 변경 금지
  version: '1.0'        # 변경 금지
```

### 4.3. 검사

```bash
isc check transfer_adler
```

```text
transfer_adler: PASS
```

### 4.4. 에뮬레이션 실행 (hw_emu)

```bash
isc run transfer_adler --input datasets/adler32/payload.bin --output datasets/adler32/result.bin
```

실제 출력 (발췌):

```text
transfer_adler [hw_emu] succeeded  run_id=20261105T110128-dbcd0a
  device     : [0] xilinx_u50_gen3x16_xdma_5_202210_1  (emulated)
  build      : cache hit  5fa478c1f272dc649e7edb95397d2f13
  input      : 262144 bytes  /home/.../datasets/adler32/payload.bin
  output     : 4 bytes  /home/.../datasets/adler32/result.bin
  validation : PASS  (cross_target) ran on the reference target itself; nothing to compare
```

처음 실행하면 hw_emu 빌드(수 분)를 한 뒤 실행합니다. `validation : PASS`는 hw_emu가 자기 자신을 기준으로 삼았다는 뜻이므로, 값은 다음 단계에서 확인합니다.

### 4.5. 결과 확인

```bash
python3 - <<'EOF'
import zlib
data = open("datasets/adler32/payload.bin", "rb").read()
got = int.from_bytes(open("datasets/adler32/result.bin", "rb").read(), "big")
print(f"fpga adler32     = {got:08x}")
print("MATCH" if got == zlib.adler32(data) else "MISMATCH")
EOF
```

```text
fpga adler32     = d9e79171
MATCH
```

### 4.6. U50 실행 (hw)

```bash
isc run transfer_adler --input datasets/adler32/payload.bin --output datasets/adler32/result_hw.bin --target hw
```

플랫폼이 hw_emu 기준 출력을 먼저 만들고 → hw 빌드(처음 한 번 수십 분) → 카드 실행 → 두 출력을 바이트 비교합니다. 4.5의 코드에서 `result.bin`을 `result_hw.bin`으로 바꿔 한 번 더 확인합니다.

실제 출력 (발췌, 빌드 캐시 재사용):

```text
transfer_adler [hw] succeeded  run_id=20261105T111257-fe3ef3
  device     : [0] xilinx_u50_gen3x16_xdma_base_5
  build      : cache hit  ac55fdff107971d99ed38ded7b1d2564
  input      : 262144 bytes  /home/.../datasets/adler32/payload.bin
  output     : 4 bytes  /home/.../datasets/adler32/result_hw.bin
  kernel_ms  : 0.193
  validation : PASS  (cross_target) matches reference target output (4 bytes)
```

`validation : PASS ... matches reference target output`는 카드 출력이 hw_emu 출력과 바이트까지 같다는 뜻입니다. 결과 확인 코드를 hw 출력에 적용한 결과:

```text
fpga adler32     = d9e79171
MATCH
```

### 4.7. 기록 확인

```bash
isc records --module transfer_adler
isc show [실행 ID]
```

## 5. 내 데이터에 적용하기

| 바꿀 것 | 방법 |
|---|---|
| 확인할 파일 | `--input`에 실제 파일 경로. 모듈은 그대로 재사용 |
| 블록별 체크섬 (동기화) | 파일을 블록 크기로 잘라 블록마다 `isc run transfer_adler --input <블록> --output <블록>.adler --target hw`를 실행하고, 원격에서 같은 방법으로 낸 값과 블록끼리 비교. 여러 블록을 한 번에 넣는 방식은 이 연산에 없음 |
| 받는 쪽 확인 | 받은 파일로 같은 명령을 실행해 두 출력 파일을 `cmp`로 비교하거나, 받는 쪽에서 `zlib.adler32`로 계산해 비교 |
| zlib 스트림 확인 | zlib 형식(RFC 1950) 스트림의 마지막 4바이트는 **압축 전 원본**의 Adler-32(big-endian)임. 원본 파일을 입력으로 주면 그 4바이트와 같은 값이 나옴 |

## 6. 문제 해결

| 증상 | 원인 | 해결 |
|---|---|---|
| `... requires an environment variable for include path '${VITIS_LIBRARIES}/...'` (`isc check`) | `VITIS_LIBRARIES` 미설정 | `export VITIS_LIBRARIES=[경로]` 후 다시 검사 |
| `error : operation 'adler32' requires at least 1 input bytes; received 0` | 빈 파일 입력. 입력은 1바이트 이상 (`operation.yaml`의 `min_bytes: 1`) | 빈 데이터의 Adler-32는 `00000001` |
| 값이 다름 | 출력을 little-endian으로 읽음 | 4.5처럼 `"big"`으로 읽음 |

## 7. 검증 기록

### 7.1. 플랫폼 자체 테스트

| 항목 | 내용 |
|---|---|
| 확인일 | 2026-11-05 |
| 환경 | Ubuntu 22.04.5 LTS (커널 6.8.0-138), Python 3.10.12, Vitis 2025.1, XRT 2.19.194 (2025.1), Vitis Libraries `v2025.1_update2` (커밋 b2c657d), AMD Alveo U50 (`xilinx_u50_gen3x16_xdma_base_5`), 저장소 기준 커밋 `8742866` |
| 실행한 단계 | 이 문서의 명령을 저장소 루트에서 순서대로 그대로 실행 (입력 만들기 → 모듈 생성 → 검사 → hw_emu → 결과 확인 → hw → 결과 확인) |
| hw_emu | 통과 — 실행 `20261105T110128-dbcd0a`, `zlib.adler32`와 일치 (`d9e79171`) |
| hw (U50) | 통과 — 실행 `20261105T111257-fe3ef3`, hw 출력 = hw_emu 출력(플랫폼 자동 대조) = CPU 참조 (`zlib.adler32`) |
| 확인하지 않은 것 | 성능(가속 효과)은 측정하지 않음. 이 문서의 입력 한 가지로만 확인 |

### 7.2. 공식 라이브러리 테스트

Vitis Libraries 공식 테스트를 고치지 않고 U50에서 빌드·실행했습니다. **입력은 공식 테스트에 들어 있는 데이터**이며 위 시나리오 데이터가 아닙니다.

| 항목 | 내용 |
|---|---|
| 공식 테스트 | `security/L1/benchmarks/adler32` (Vitis Libraries `v2025.1_update2`) |
| 빌드 | `make all TARGET=hw PLATFORM=xilinx_u50_gen3x16_xdma_5_202210_1`, 이 서버의 메모리 제한 때문에 `VPP_LDFLAGS="--vivado.synth.jobs 4 --vivado.impl.jobs 4"`를 붙임 (2026-11-05, 약 54분) |
| 실행 | `make run TARGET=hw PLATFORM=xilinx_u50_gen3x16_xdma_5_202210_1` (공식 `data/test.dat`, `-num 16`) |
| 결과 | **통과** — 2026-11-05, 공식 비교 `Info: Test passed` |
