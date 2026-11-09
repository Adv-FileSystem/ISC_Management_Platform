# `snappy_compress` — Kafka로 보내는 이벤트 메시지 묶음 Snappy 압축 (연산 선택형)

| 개발 방식 | 사용자가 작성하는 것 | 커널 | 파라미터 | 플랫폼 자체 테스트 | 공식 라이브러리 테스트 |
|---|---|---|---|---|---|
| 연산 선택형 | 없음 (`isc new` 한 줄) | Vitis Data Compression L2 `xilSnappyCompress` (`kernel/snappy_kernel.cpp` = 공식 `snappy_compress_mm.cpp`를 공식 테스트 설정 `PARALLEL_BLOCK=8`로 그대로 컴파일) | 없음 | ✅ 통과 (2026-11-06) | ✅ 통과 (2026-11-05) |

> 이 연산은 시나리오 검증용 **프로토타입**입니다. 공식 커널 소스를 수정 없이 씁니다.

## 1. 시나리오

| 항목 | 내용 |
|---|---|
| 사용자 | 이벤트 수집 파이프라인 개발자 |
| 상황 | Kafka로 보내는 이벤트 메시지 묶음을 Snappy로 압축한다 |
| 가속하는 단계 | 메시지 묶음 압축 |
| 입력 → 출력 | 이벤트 묶음(바이너리) → Snappy 블록 |
| 완료 기준 | `python-snappy`로 풀면 원본과 일치 |

검증한 서버에는 `python-snappy`가 설치되어 있지 않았습니다. 그래서 Snappy 형식 문서대로 따로 작성한 순수 파이썬 해제기(`snappy_raw.py`)로 풀어 비교했습니다. 이 해제기는 먼저 공식 라이브러리 테스트가 카드에서 만든 `.snappy` 파일의 앞부분 24 MB를 풀어 원본과 같은 것을 확인한 뒤 사용했습니다.

## 2. 연산 요약

| 항목 | 내용 |
|---|---|
| 입력 | 아무 파일 (원시 바이트). 1바이트 이상 |
| 처리 | 공식 호스트(`snappyOCLHost.cpp`)처럼 입력을 2 MiB 덩어리로 나눠 커널을 부르고, 64 KiB 블록마다 Snappy raw 블록을 받음. 압축 이득이 없는 블록은 원본 그대로 저장 |
| 출력 | 블록마다 12바이트 헤더(`u32 raw_size, u32 stored_size, u32 flags`) + 페이로드. `flags` 0 = Snappy raw 블록, 1 = 원본 |
| 출력 형식 주의 | 블록 헤더는 이 플랫폼 형식입니다 (플랫폼 `lz4` 연산과 같은 틀). **Snappy 프레이밍 형식(`.sz`)이나 Kafka 메시지 형식이 아닙니다**. 페이로드 하나하나는 표준 Snappy raw 블록입니다 |
| 파라미터 | 없음 (블록 64 KiB 고정) |
| 정답 판정 | CPU Snappy와 커널은 서로 다르지만 둘 다 올바른 압축 바이트를 낼 수 있어, 풀어서 원본과 비교합니다 (`check_output.py`) |
| 메모리 | 포트 2개를 HBM 뱅크 0·1에 연결 (`operation.yaml`) |

## 3. 준비

- 공통 설치와 환경 확인: [README 1절](../../../README.md#1-설치-및-환경-구성)
- 빌드에 **`VITIS_LIBRARIES`** 가 필요합니다: `export VITIS_LIBRARIES=[Vitis Libraries 2025.1 경로]`
- 모든 명령은 **저장소 루트**에서 실행합니다. 실습 파일은 `datasets/pscen/`(git에 올라가지 않음)에 만듭니다.

## 4. 개발 절차

### 4.1. 입력 만들기

Kafka로 보낼 클릭 이벤트 메시지 약 200 KB를 만듭니다(고정 시드). 메시지마다 `u32 길이 + u64 키` 머리말 뒤에 JSON 본문이 붙습니다.

```bash
mkdir -p datasets/pscen
python3 - <<'EOF'
import json, random, struct
rng = random.Random(8); out = bytearray()
while len(out) < 200_000:
    ev = {"ts": 1762300000 + len(out) // 50, "user": f"u{rng.randrange(5000):05d}",
          "event": rng.choice(["view", "click", "cart", "buy"]), "item": rng.randrange(20000),
          "price": round(rng.uniform(1, 300), 2), "session": f"s{rng.randrange(100000):06x}"}
    body = json.dumps(ev, separators=(",", ":")).encode()
    out += struct.pack("<IQ", len(body), rng.getrandbits(64)) + body   # 메시지 머리말(길이, 키) + JSON 본문
open("datasets/pscen/kafka_events_200k.bin", "wb").write(out)
print(len(out), "bytes")
EOF
```

```text
200074 bytes
```

### 4.2. 모듈 만들기

```bash
isc describe snappy_compress                # 입력·출력 형식 확인 (선택)
isc new kafka_snappy --operation snappy_compress
```

생성되는 `modules/kafka_snappy/module.yaml` — 고칠 것이 없습니다.

```yaml
module:
  id: kafka_snappy
  name: kafka_snappy
  version: 0.1.0
operation:
  id: snappy_compress
  version: '1.0'
```

### 4.3. 검사

```bash
isc check kafka_snappy
```

```text
kafka_snappy: PASS
  note   : operation 'snappy_compress' has no CPU baseline (baseline.py); `isc bench` cannot compute a speedup for it
```

`note`는 오류가 아닙니다. 이 연산은 CPU 기준선 대신 4.5의 방법으로 정답을 확인하므로, 속도 비교(`isc bench`)만 할 수 없다는 뜻입니다.

### 4.4. 에뮬레이션 실행 (hw_emu)

```bash
isc run kafka_snappy --input datasets/pscen/kafka_events_200k.bin --output datasets/pscen/kafka_snappy.emu.bin
```

### 4.5. 결과 확인

연산 폴더의 확인 스크립트가 블록 헤더를 따라가며 페이로드를 풀어 원본과 비교합니다.

```bash
python3 isc/operations/snappy_compress/check_output.py datasets/pscen/kafka_events_200k.bin datasets/pscen/kafka_snappy.emu.bin
```

```text
blocks=4 original=200074 compressed=84485 ratio=2.37
ROUNDTRIP OK
```

`python-snappy`가 있는 환경이라면 `check_output.py` 안의 `snappy_raw.decompress(payload)` 대신 `snappy.uncompress(payload)`를 써도 됩니다 (이 방법은 시험하지 않았습니다).

### 4.6. U50 실행 (hw)

```bash
isc run kafka_snappy --input datasets/pscen/kafka_events_200k.bin --output datasets/pscen/kafka_snappy.hw.bin --target hw
python3 isc/operations/snappy_compress/check_output.py datasets/pscen/kafka_events_200k.bin datasets/pscen/kafka_snappy.hw.bin
```

플랫폼이 hw_emu 기준 출력을 먼저 만들고 → hw 빌드 → 카드 실행 → 두 출력을 바이트 비교합니다. 이 서버에서 처음 실행(빌드 포함)은 약 50분 걸렸습니다.

실제 출력 (발췌):

```text
  input      : 200074 bytes  .../datasets/pscen/kafka_events_200k.bin
  output     : 84485 bytes  .../kafka_snappy_kafka_events_200k.hw.bin
  kernel_ms  : 0.407
  validation : PASS  (cross_target) matches reference target output (84485 bytes)
```

### 4.7. 기록 확인

```bash
isc records --module kafka_snappy
isc show [실행 ID]
```

## 5. 내 데이터에 적용하기

| 바꿀 것 | 방법 |
|---|---|
| 메시지 묶음 | 보낼 묶음을 파일로 써서 `--input`으로. 모듈은 그대로 재사용 (빌드 다시 안 함) |
| Kafka로 보내기 | 출력은 플랫폼 블록 형식이므로 그대로 Kafka 압축 배치로 쓸 수 없음. 블록별 Snappy 페이로드를 꺼내 쓰는 변환은 시험하지 않았음 |
| 압축률 | 4.5 스크립트의 `ratio`. 이 문서 입력에서 2.37 |

## 6. 문제 해결

| 증상 | 원인 | 해결 |
|---|---|---|
| `ROUNDTRIP MISMATCH` | 출력 파일이 다른 입력의 결과이거나 잘림 | 같은 입력으로 다시 실행하고 `isc show`로 입력 sha256 확인 |
| `ModuleNotFoundError: snappy` | `python-snappy` 미설치 | 필요 없음. 4.5의 `check_output.py`는 순수 파이썬 해제기를 씀 |
| `isc check`에서 include 경로 오류 | `VITIS_LIBRARIES` 미설정 | `export VITIS_LIBRARIES=[경로]` |

## 7. 검증 기록

### 7.1. 플랫폼 자체 테스트

| 항목 | 내용 |
|---|---|
| 확인일 | 2026-11-06 |
| 환경 | Ubuntu 22.04.5 LTS (커널 6.8.0-138), Vitis 2025.1, XRT 2.19.194, Vitis Libraries `v2025.1_update2` (커밋 b2c657d), AMD Alveo U50 (`xilinx_u50_gen3x16_xdma_base_5`), 저장소 기준 커밋 `8742866` + 이 연산 폴더(커밋 전) |
| 입력 | 4.1의 `kafka_events_200k.bin` (200,074바이트, sha256 `0650df6b…`) |
| hw_emu | 통과 — 실행 `20261106T013637-607798` |
| hw (U50) | **통과** — 실행 `20261106T013637-7367f4`. hw 출력 = hw_emu 출력(플랫폼 자동 대조). 풀어서 원본과 일치(블록 4개, 압축률 2.37) |
| 판정 도구 | 순수 파이썬 해제기 `snappy_raw.py` (`python-snappy` 미설치). 공식 테스트 출력 24 MB로 먼저 검증 |
| 확인하지 않은 것 | 성능(가속 효과). `python-snappy`로 푸는 방법. 이 문서의 입력 한 가지로만 확인 |

### 7.2. 공식 라이브러리 테스트

Vitis Libraries 공식 테스트를 고치지 않고 U50에서 빌드·실행했습니다. **입력은 공식 테스트에 들어 있는 데이터**이며 위 시나리오 데이터가 아닙니다.

| 항목 | 내용 |
|---|---|
| 공식 테스트 | `data_compression/L2/tests/snappy_compress` (Vitis Libraries `v2025.1_update2`). 공식 테스트의 대상 장치 목록에 U50은 없음 |
| 빌드 | `make all TARGET=hw PLATFORM=xilinx_u50_gen3x16_xdma_5_202210_1`, 이 서버의 메모리 제한 때문에 `VPP_LDFLAGS="--vivado.synth.jobs 4 --vivado.impl.jobs 4"`를 붙임 (2026-11-05, 약 86분) |
| 실행 | `make run TARGET=hw PLATFORM=xilinx_u50_gen3x16_xdma_5_202210_1` (공식 `data/sample.txt`) |
| 결과 | **통과** — 2026-11-05, 압축 후 해제해 원본과 비교 `Compression is successful. No errors found.` |
