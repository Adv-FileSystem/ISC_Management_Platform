# `crc32` — 야간 백업 파일 손상 확인 (연산 선택형)

| 개발 방식 | 사용자가 작성하는 것 | 커널 | 파라미터 | 플랫폼 자체 테스트 | 공식 라이브러리 테스트 |
|---|---|---|---|---|---|
| 연산 선택형 | 없음 (`isc new` 한 줄) | Vitis Data Compression L1 `xf::compression::crc32_mm` (`kernel/crc32_kernel.cpp`) | 없음 | ✅ 통과 (2026-11-05) | ✅ 통과 (2026-11-06) |

## 1. 시나리오

| 항목 | 내용 |
|---|---|
| 사용자 | 스토리지·백업 운영자 |
| 상황 | 야간 백업 후 백업 파일이 우발적으로 깨지지 않았는지 확인한다. 기존 기록은 zlib(`zlib.crc32`)로 만든 값이다 |
| 가속하는 단계 | 파일 CRC 계산 |
| 입력 → 출력 | 백업 파일 → 4바이트 CRC32 |
| 완료 기준 | `zlib.crc32`와 일치 |

## 2. 연산 요약

| 항목 | 내용 |
|---|---|
| 입력 | 아무 파일 (원시 바이트, 형식 무관). **1바이트 이상** (빈 파일은 거부) |
| 출력 | 4바이트, CRC-32 값을 **big-endian**으로 쓴 파일 |
| CRC 종류 | zlib·gzip·PNG가 쓰는 표준 CRC-32 (`zlib.crc32`, `binascii.crc32`와 같은 값). POSIX `cksum` 명령의 CRC와는 **다른 값**이므로 비교 대상으로 쓰지 않음 |
| 파라미터 | 없음 |
| 한계 | 우발적 손상 검출용. 일부러 조작한 파일을 막는 보안 용도가 아님 (→ [`sha256`](../sha256/README.md)) |

작은 예: 입력 `123456789`(ASCII 9바이트) → 출력 `cb f4 39 26` (`0xcbf43926`)

## 3. 준비

- 공통 설치와 환경 확인: [README 1절](../../../README.md#1-설치-및-환경-구성)
- 이 연산은 빌드에 **`VITIS_LIBRARIES`** 가 필요합니다: `export VITIS_LIBRARIES=[Vitis Libraries 2025.1 경로]`
- 모든 명령은 **저장소 루트**에서 실행합니다. 실습 파일은 `datasets/crc32/`(git에 올라가지 않음)에 만듭니다.

## 4. 개발 절차

### 4.1. 입력 만들기

백업 파일 대신 256 KiB 무작위 파일(고정 시드)을 만들고 기준값을 함께 출력합니다. 실제 파일을 쓸 때는 이 단계를 건너뛰고 그 파일 경로를 씁니다.

```bash
mkdir -p datasets/crc32
python3 - <<'EOF'
import random, zlib
data = random.Random(7).randbytes(256 * 1024)          # 256 KiB, 고정 시드
open("datasets/crc32/backup.bin", "wb").write(data)
print(f"expected crc32 = {zlib.crc32(data):08x}")
EOF
```

```text
expected crc32 = e3f9cf99
```

### 4.2. 모듈 만들기

```bash
isc describe crc32                          # 입력·출력 형식 확인 (선택)
isc new backup_crc --operation crc32
```

생성되는 `modules/backup_crc/module.yaml` — 고칠 것이 없습니다.

```yaml
module:
  id: backup_crc        # 변경 금지
  name: backup_crc      # 수정 가능
  version: 0.1.0        # 수정 가능
operation:
  id: crc32             # 변경 금지
  version: '1.0'        # 변경 금지
```

### 4.3. 검사

```bash
isc check backup_crc
```

```text
backup_crc: PASS
```

### 4.4. 에뮬레이션 실행 (hw_emu)

```bash
isc run backup_crc --input datasets/crc32/backup.bin --output datasets/crc32/result.bin
```

실제 출력 (발췌):

```text
backup_crc [hw_emu] succeeded  run_id=20261105T110128-a5031a
  device     : [0] xilinx_u50_gen3x16_xdma_5_202210_1  (emulated)
  build      : cache hit  a977e2df5db76ebc78dbd2b427a7476f
  input      : 262144 bytes  /home/.../datasets/crc32/backup.bin
  output     : 4 bytes  /home/.../datasets/crc32/result.bin
  validation : PASS  (cross_target) ran on the reference target itself; nothing to compare
```

처음 실행하면 hw_emu 빌드(수 분)를 한 뒤 실행합니다. `validation : PASS`는 hw_emu가 자기 자신을 기준으로 삼았다는 뜻이므로, 값은 다음 단계에서 확인합니다.

### 4.5. 결과 확인

출력 4바이트를 big-endian 정수로 읽어 `zlib.crc32`와 비교합니다.

```bash
python3 - <<'EOF'
import zlib
data = open("datasets/crc32/backup.bin", "rb").read()
got = int.from_bytes(open("datasets/crc32/result.bin", "rb").read(), "big")
print(f"fpga crc32     = {got:08x}")
print("MATCH" if got == zlib.crc32(data) else "MISMATCH")
EOF
```

```text
fpga crc32     = e3f9cf99
MATCH
```

### 4.6. U50 실행 (hw)

```bash
isc run backup_crc --input datasets/crc32/backup.bin --output datasets/crc32/result_hw.bin --target hw
```

플랫폼이 hw_emu 기준 출력을 먼저 만들고 → hw 빌드(처음 한 번 수십 분) → 카드 실행 → 두 출력을 바이트 비교합니다. 4.5의 코드에서 `result.bin`을 `result_hw.bin`으로 바꿔 한 번 더 확인합니다.

실제 출력 (발췌, 빌드 캐시 재사용):

```text
backup_crc [hw] succeeded  run_id=20261105T111257-c36142
  device     : [0] xilinx_u50_gen3x16_xdma_base_5
  build      : cache hit  2c5ab186dde9a112886a8a5d8641cf62
  input      : 262144 bytes  /home/.../datasets/crc32/backup.bin
  output     : 4 bytes  /home/.../datasets/crc32/result_hw.bin
  kernel_ms  : 3.264
  validation : PASS  (cross_target) matches reference target output (4 bytes)
```

`validation : PASS ... matches reference target output`는 카드 출력이 hw_emu 출력과 바이트까지 같다는 뜻입니다. 결과 확인 코드를 hw 출력에 적용한 결과:

```text
fpga crc32     = e3f9cf99
MATCH
```

### 4.7. 기록 확인

```bash
isc records --module backup_crc
isc show [실행 ID]          # 입력·출력 sha256, 빌드 지문, 단계별 시간
```

## 5. 내 데이터에 적용하기

| 바꿀 것 | 방법 |
|---|---|
| 확인할 파일 | `--input`에 실제 파일 경로. 모듈은 그대로 재사용 (빌드 다시 안 함) |
| 여러 파일 | 파일마다 `isc run backup_crc --input <파일> --output <파일>.crc --target hw` 반복. 실행 기록에 입력 sha256과 출력이 남음 |
| 기존 기록과 비교 | 기존 값이 `zlib.crc32`/`gzip` 계열이면 그대로 비교. `cksum` 값과는 비교하지 않음 |

## 6. 문제 해결

| 증상 | 원인 | 해결 |
|---|---|---|
| `... requires an environment variable for include path '${VITIS_LIBRARIES}/...'` (`isc check`) | `VITIS_LIBRARIES` 미설정 | `export VITIS_LIBRARIES=[경로]` 후 다시 검사 |
| `error : operation 'crc32' requires at least 1 input bytes; received 0` | 빈 파일 입력. 입력은 1바이트 이상 (`operation.yaml`의 `min_bytes: 1`) | 빈 파일은 FPGA로 보내지 않음 (빈 데이터의 CRC-32는 `00000000`) |
| 값이 기존 기록과 다름 | 바이트 순서를 little-endian으로 읽음, 또는 기존 값이 `cksum` CRC | 4.5처럼 `"big"`으로 읽고, 기존 값이 어떤 CRC인지 확인 |
| 장치 대기가 길어짐 | 다른 실행이 장치 사용 중 | 기다리거나 `--device-timeout SEC` 지정 |

## 7. 검증 기록

### 7.1. 플랫폼 자체 테스트

| 항목 | 내용 |
|---|---|
| 확인일 | 2026-11-05 |
| 환경 | Ubuntu 22.04.5 LTS (커널 6.8.0-138), Python 3.10.12, Vitis 2025.1, XRT 2.19.194 (2025.1), Vitis Libraries `v2025.1_update2` (커밋 b2c657d), AMD Alveo U50 (`xilinx_u50_gen3x16_xdma_base_5`), 저장소 기준 커밋 `8742866` |
| 실행한 단계 | 이 문서의 명령을 저장소 루트에서 순서대로 그대로 실행 (입력 만들기 → 모듈 생성 → 검사 → hw_emu → 결과 확인 → hw → 결과 확인) |
| hw_emu | 통과 — 실행 `20261105T110128-a5031a`, `zlib.crc32`와 일치 (`e3f9cf99`) |
| hw (U50) | 통과 — 실행 `20261105T111257-c36142`, hw 출력 = hw_emu 출력(플랫폼 자동 대조) = CPU 참조 (`zlib.crc32`) |
| 확인하지 않은 것 | 성능(가속 효과)은 측정하지 않음. 이 문서의 입력 한 가지로만 확인 |

### 7.2. 공식 라이브러리 테스트

Vitis Libraries 공식 테스트를 고치지 않고 U50에서 빌드·실행했습니다. **입력은 공식 테스트에 들어 있는 데이터**이며 위 시나리오 데이터가 아닙니다.

| 항목 | 내용 |
|---|---|
| 공식 테스트 | `security/L1/benchmarks/crc32` (Vitis Libraries `v2025.1_update2`). 공식 테스트의 대상 장치 목록에 U50은 없음 |
| 빌드 | `make all TARGET=hw PLATFORM=xilinx_u50_gen3x16_xdma_5_202210_1`, 이 서버의 메모리 제한 때문에 `VPP_LDFLAGS="--vivado.synth.jobs 4 --vivado.impl.jobs 4"`를 붙임 (2026-11-06, 약 38분). 11-05 첫 빌드는 동시 빌드 중 메모리 부족으로 중단 |
| 실행 | `make run TARGET=hw PLATFORM=xilinx_u50_gen3x16_xdma_5_202210_1` (공식 `test.dat`, `-num 16`) |
| 결과 | **통과** — 2026-11-06, 공식 비교 `Info: Test passed` |
