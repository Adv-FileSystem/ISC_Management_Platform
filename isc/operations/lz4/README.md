# `lz4` — 모니터링 서버 접근 로그 압축 보관 (연산 선택형)

| 개발 방식 | 사용자가 작성하는 것 | 커널 | 파라미터 | 플랫폼 자체 테스트 | 공식 라이브러리 테스트 |
|---|---|---|---|---|---|
| 연산 선택형 | 없음 (`isc new` 한 줄, 필요하면 `--set block_size=...`) | Vitis Data Compression L1 LZ4 압축 단계(`lzCompress` → `lzBestMatchFilter` → `lzBooster` → `lz4Compress`)를 감싼 단일 블록 커널 (`kernel/lz4_kernel.cpp`) | `block_size` | ✅ 통과 (2026-11-05) | ✅ 통과 (2026-11-05) |

> **알려진 문제 (2026-11-05 확인)**: 29바이트 입력(`j` 1바이트 + `Z` 20바이트 반복 + 8바이트)에서 `hw_emu` 실행이 끝나지 않았습니다(시뮬레이터가 계속 돌고 교착 보고 없음, 99분 뒤 중단).
> 원인은 확인하지 않았고, 같은 입력의 카드(`hw`) 실행은 시험하지 않았습니다. 이 문서의 예시(약 97 KiB 로그)와 129·257바이트 입력은 hw_emu에서 정상이었습니다.
> 실행이 멈추면 `Ctrl+C` 또는 `--timeout`으로 중단하십시오.

## 1. 시나리오

| 항목 | 내용 |
|---|---|
| 사용자 | 모니터링 서버를 운영하는 백엔드·인프라 개발자 |
| 상황 | 실시간 모니터링 서버가 1분마다 쌓이는 접근 로그를 빠르게 압축해 보관한다. 압축을 서버 CPU 대신 FPGA에서 한다 |
| 가속하는 단계 | 로그 블록 압축 |
| 입력 → 출력 | 텍스트 로그 → 블록 헤더 + LZ4 블록 |
| 완료 기준 | CPU `lz4.block`으로 풀면 원본과 바이트 일치, 압축률 기록 |

## 2. 연산 요약

| 항목 | 내용 |
|---|---|
| 입력 | 아무 파일 (원시 바이트). 빈 파일이면 빈 출력 |
| 출력 | 플랫폼 고유 형식 `isc_lz4_blocks`: 입력을 `block_size`씩 자른 블록마다 **12바이트 헤더 + 페이로드**를 이어 쓴 파일 |
| 블록 헤더 | little-endian `u32 raw_size`(원본 블록 크기), `u32 stored_size`(뒤따르는 페이로드 크기), `u32 flags`(0 = LZ4 블록, 1 = 압축하지 않은 원본) |
| 페이로드 | `flags=0`: 표준 LZ4 **블록**(크기 정보 없음). `flags=1`: 원본 바이트 그대로 (압축 이득이 없거나 커널이 리터럴 한도를 넘은 블록) |
| 파라미터 `block_size` | 블록 하나의 바이트 수, 1024–65536, 기본 65536. 바꿔도 재빌드 없음 |
| 지원하지 않는 것 | 압축 해제(FPGA), 표준 `.lz4` 프레임 형식. **`lz4` 명령으로 바로 풀 수 없으므로** 4.5의 방법으로 풂 |
| 결과 비교 방식 | 같은 입력이라도 CPU LZ4와 FPGA 커널은 서로 다른(둘 다 올바른) 압축 바이트를 낼 수 있음. 그래서 압축 바이트가 아니라 **풀어서 원본과 같은지**로 확인 |

## 3. 준비

- 공통 설치와 환경 확인: [README 1절](../../../README.md#1-설치-및-환경-구성)
- 이 연산은 빌드에 **`VITIS_LIBRARIES`** 가 필요합니다: `export VITIS_LIBRARIES=[Vitis Libraries 2025.1 경로]`
- 결과를 풀 때 Python `lz4` 패키지가 필요합니다: `python3 -m pip install lz4`
- 모든 명령은 **저장소 루트**에서 실행합니다. 실습 파일은 `datasets/lz4/`(git에 올라가지 않음)에 만듭니다.

## 4. 개발 절차

### 4.1. 입력 만들기

웹 서버 접근 로그 형태의 텍스트 1,500줄(약 97 KiB, 고정 시드)을 만듭니다. 실제 로그를 쓸 때는 이 단계를 건너뜁니다.

```bash
mkdir -p datasets/lz4
python3 - <<'EOF'
import random
rng = random.Random(1)
lines = []
for i in range(1500):
    level = rng.choice(["INFO", "INFO", "INFO", "WARN", "ERROR"])
    path = rng.choice(["/api/orders", "/api/users", "/health", "/api/cart"])
    lines.append(f"2026-11-05T10:{i // 60 % 60:02d}:{i % 60:02d}Z {level} GET {path} status=200 latency_ms={rng.randint(1, 900)}\n")
open("datasets/lz4/service.log", "w").write("".join(lines))
EOF
ls -l datasets/lz4/service.log
```

```text
-rw-rw-r-- 1 ... 99503 ... datasets/lz4/service.log
```

### 4.2. 모듈 만들기

```bash
isc describe lz4                            # 출력 형식과 파라미터 확인 (선택)
isc new log_lz4 --operation lz4 --set block_size=65536
```

생성되는 `modules/log_lz4/module.yaml`:

```yaml
module:
  id: log_lz4             # 변경 금지
  name: log_lz4           # 수정 가능
  version: 0.1.0          # 수정 가능
operation:
  id: lz4                 # 변경 금지
  version: '1.0'          # 변경 금지
parameters:               # 수정 가능 (재빌드 없음)
  block_size: 65536
```

### 4.3. 검사

```bash
isc check log_lz4
```

```text
log_lz4: PASS
```

### 4.4. 에뮬레이션 실행 (hw_emu)

```bash
isc run log_lz4 --input datasets/lz4/service.log --output datasets/lz4/service.log.isclz4
```

실제 출력 (발췌, 처음 실행이라 hw_emu 빌드 포함):

```text
log_lz4 [hw_emu] succeeded  run_id=20261105T110128-d5da24
  device     : [0] xilinx_u50_gen3x16_xdma_5_202210_1  (emulated)
  build      : built  f2d123ac48cc2a1e8626515afc054d23
  input      : 99503 bytes  /home/.../datasets/lz4/service.log
  output     : 19144 bytes  /home/.../datasets/lz4/service.log.isclz4
  compression_ratio: 5.1976076055160885
  stored_blocks: 0.0
  blocks     : 2.0
  validation : PASS  (cross_target) ran on the reference target itself; nothing to compare
```

hw_emu는 시뮬레이션이라 100 KB에도 수십 초가 걸립니다. 작은 파일로 확인하고 큰 파일은 hw로 실행합니다.

### 4.5. 결과 확인 (풀어서 원본과 비교)

블록 헤더를 읽으며 `flags=0`인 블록은 `lz4.block.decompress`로 풀고, `flags=1`인 블록은 그대로 이어 붙입니다.
이 코드가 곧 이 형식의 **복원기**이므로 실제 운영에서도 그대로 씁니다.

```bash
python3 - <<'EOF'
import struct, lz4.block
framed = open("datasets/lz4/service.log.isclz4", "rb").read()
out, pos, blocks = bytearray(), 0, 0
while pos < len(framed):
    raw_size, stored_size, flags = struct.unpack_from("<III", framed, pos)
    pos += 12
    payload = framed[pos:pos + stored_size]
    pos += stored_size
    out += payload if flags == 1 else lz4.block.decompress(payload, uncompressed_size=raw_size)
    blocks += 1
original = open("datasets/lz4/service.log", "rb").read()
print(f"blocks={blocks} original={len(original)} compressed={len(framed)} ratio={len(original)/len(framed):.2f}")
print("ROUNDTRIP OK" if bytes(out) == original else "ROUNDTRIP MISMATCH")
EOF
```

```text
blocks=2 original=99503 compressed=19144 ratio=5.20
ROUNDTRIP OK
```

### 4.6. U50 실행 (hw)

```bash
isc run log_lz4 --input datasets/lz4/service.log --output datasets/lz4/service.log.hw.isclz4 --target hw
```

플랫폼이 hw_emu 기준 출력을 먼저 만들고 → hw 빌드(처음 한 번 수십 분) → 카드 실행 → 두 출력을 바이트 비교합니다. 4.5의 코드에서 입력 파일을 `service.log.hw.isclz4`로 바꿔 한 번 더 풀어 봅니다.

실제 출력 (발췌, 빌드 캐시 재사용):

```text
log_lz4 [hw] succeeded  run_id=20261105T111257-a41f1a
  device     : [0] xilinx_u50_gen3x16_xdma_base_5
  build      : cache hit  b7e2c8b4e2336e0bec7118e7e4fd8fc5
  input      : 99503 bytes  /home/.../datasets/lz4/service.log
  output     : 19144 bytes  /home/.../datasets/lz4/service.log.hw.isclz4
  kernel_ms  : 0.616
  validation : PASS  (cross_target) matches reference target output (19144 bytes)
```

`validation : PASS ... matches reference target output`는 카드 출력이 hw_emu 출력과 바이트까지 같다는 뜻입니다. 결과 확인 코드를 hw 출력에 적용한 결과:

```text
blocks=2 original=99503 compressed=19144 ratio=5.20
ROUNDTRIP OK
```

### 4.7. 기록 확인

```bash
isc records --module log_lz4
isc show [실행 ID]          # compression_ratio, blocks, stored_blocks 지표 포함
```

## 5. 내 데이터에 적용하기

| 바꿀 것 | 방법 |
|---|---|
| 압축할 파일 | `--input`에 실제 로그 경로, `--output`에 저장할 경로 |
| 블록 크기 | `module.yaml`의 `block_size` (1024–65536). 작을수록 블록마다 따로 풀 수 있는 단위가 작아지고, 보통 압축률은 낮아짐. 재빌드 없음 |
| 압축률이 낮을 때 | `stored_blocks` 지표가 크면 압축 이득이 없는 데이터(이미 압축됨, 무작위 등)임. 그런 블록은 원본 그대로 저장됨 |
| CPU 압축과 속도 비교 | [README 5절](../../../README.md#5-가속-효과-검증)의 `isc plan` → `isc bench` → `isc report` (CPU 기준선은 Python `lz4` 패키지 사용) |

## 6. 문제 해결

| 증상 | 원인 | 해결 |
|---|---|---|
| `... requires an environment variable for include path '${VITIS_LIBRARIES}/...'` (`isc check`) | `VITIS_LIBRARIES` 미설정 | `export VITIS_LIBRARIES=[경로]` 후 다시 검사 |
| `lz4` 명령으로 풀리지 않음 | 출력은 `.lz4` 프레임이 아니라 블록 헤더 + LZ4 블록 형식 | 4.5의 복원 코드 사용 |
| `ModuleNotFoundError: No module named 'lz4'` | Python `lz4` 패키지 없음 | `python3 -m pip install lz4` |
| `ROUNDTRIP MISMATCH` | 헤더를 big-endian으로 읽음, 또는 `uncompressed_size`를 넘기지 않음 | `struct.unpack_from("<III", ...)`과 `uncompressed_size=raw_size` 확인 |

## 7. 검증 기록

### 7.1. 플랫폼 자체 테스트

| 항목 | 내용 |
|---|---|
| 확인일 | 2026-11-05 |
| 환경 | Ubuntu 22.04.5 LTS (커널 6.8.0-138), Python 3.10.12, Vitis 2025.1, XRT 2.19.194 (2025.1), Vitis Libraries `v2025.1_update2` (커밋 b2c657d), AMD Alveo U50 (`xilinx_u50_gen3x16_xdma_base_5`), 저장소 기준 커밋 `8742866` |
| 실행한 단계 | 이 문서의 명령을 저장소 루트에서 순서대로 그대로 실행 (입력 만들기 → 모듈 생성 → 검사 → hw_emu → 결과 확인 → hw → 결과 확인) |
| hw_emu | 통과 — 실행 `20261105T110128-d5da24`, Python `lz4.block`으로 풀어 원본과 일치 (압축률 5.20) |
| hw (U50) | 통과 — 실행 `20261105T111257-a41f1a`, hw 출력 = hw_emu 출력(플랫폼 자동 대조) = CPU 참조 (Python `lz4.block`으로 풀어 원본과 일치) |
| 확인하지 않은 것 | 성능(가속 효과)은 측정하지 않음. 이 문서의 입력 한 가지로만 확인 |

### 7.2. 공식 라이브러리 테스트

Vitis Libraries 공식 테스트를 고치지 않고 U50에서 빌드·실행했습니다. **입력은 공식 테스트에 들어 있는 데이터**이며 위 시나리오 데이터가 아닙니다.

| 항목 | 내용 |
|---|---|
| 공식 테스트 | `data_compression/L2/tests/lz4_compress` (Vitis Libraries `v2025.1_update2`, 같은 태그의 로컬 클론 `Vitis_Libraries_2025.1`) |
| 빌드 | 2026-07-10에 그 클론에서 미리 빌드해 둔 `compress.xclbin`을 사용. 이 빌드의 명령·소요 시간은 이번 검증 기록에 없음 |
| 실행 | `make run TARGET=hw PLATFORM=xilinx_u50_gen3x16_xdma_5_202210_1` (공식 `data/sample.txt`) |
| 결과 | **통과** — 2026-11-05, 압축 후 해제해 원본과 비교 `Compression is successful. No errors found.` |
