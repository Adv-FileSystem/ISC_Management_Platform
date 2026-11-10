# `snappy_compress` — Kafka로 보내는 이벤트 메시지 묶음 Snappy 압축 (연산 선택형)

| 개발 방식 | 사용하는 라이브러리 함수 | 검증 상태 |
|---|---|---|
| 연산 선택형 | Vitis Data Compression L2 `xilSnappyCompress` (`kernel/snappy_kernel.cpp`) | 플랫폼 자체 테스트 ✅ · 공식 라이브러리 테스트 ✅ (검증 장치 AMD Alveo U50 · [검증 기록](../../../verification/u50_vitis_libraries/results/snappy_compress.md)) |

## 1. 연산 예시

이벤트 수집 파이프라인 개발자가 Kafka로 보내기 전에 클릭·구매 이벤트 메시지 묶음을 Snappy로 압축합니다. 이 압축을 CPU 대신 FPGA에서 합니다.

### 1.1. 수집 파이프라인 코드 안에서 직접 압축하는 기존 방식

```python
import snappy

with open("kafka_events.bin", "rb") as f:
    batch = f.read()
packed = snappy.compress(batch)
```

### 1.2. ISC 전주기 시스템 기반의 방식

```bash
isc new kafka_snappy --operation snappy_compress                                         # 처음 한 번: 모듈 만들기
isc run kafka_snappy --input kafka_events.bin --output kafka_events.isc_snappy --target hw   # FPGA가 압축
python3 isc/operations/snappy_compress/check_output.py kafka_events.bin kafka_events.isc_snappy   # 풀어서 확인
```

- 입력: 아무 파일 (원시 바이트, 1바이트 이상)
- 출력: 입력을 64 KiB 블록으로 나눠, 블록마다 12바이트 헤더(`u32 raw_size, u32 stored_size, u32 flags`) + 페이로드(`flags=0`: 표준 Snappy raw 블록, `flags=1`: 압축 이득이 없어 원본 그대로)
- **1.1과 달리 출력 전체가 Snappy 프레이밍 형식(`.sz`)이나 Kafka 메시지 형식이 아닙니다.** 그대로 Kafka 압축 배치로 쓸 수 없고, 블록별 페이로드를 꺼내 쓰는 변환은 시험하지 않았습니다
- CPU Snappy와 FPGA는 서로 다른(둘 다 올바른) 압축 바이트를 낼 수 있어서, 풀어서 원본과 같은지로 확인합니다
- 실제로 따라 할 절차는 2절(개발 절차)과 3절(실행 방법)에 있습니다.

## 2. 개발 절차

모든 명령은 저장소 루트에서 실행합니다.

### 2.1. 입력 준비

**사용자가 할 일**: 압축할 메시지 묶음 파일을 정합니다. 파일을 그대로 쓰므로 변환할 필요가 없습니다.

예시 묶음(클릭 이벤트 메시지 약 200 KB, 고정 시드. 메시지마다 `u32 길이 + u64 키` 머리말 뒤에 JSON 본문)으로 먼저 해 보려면:

```bash
python3 isc/operations/snappy_compress/examples/make_input.py        # → datasets/snappy_compress/kafka_events_200k.bin
```

- 저장 위치를 바꾸려면 `-o 경로`를 붙입니다.

[`examples/make_input.py`](examples/make_input.py) 전체 코드입니다(주석 제외). 예시 데이터를 바꾸려면 `make_sample()`만 고치고, `encode()`(입력 형식)와 `main()`은 그대로 둡니다.

```python
import argparse
import json
import random
import struct
from pathlib import Path

DEFAULT_OUTPUT = "datasets/snappy_compress/kafka_events_200k.bin"


def make_sample() -> bytes:
    rng = random.Random(8)
    out = bytearray()
    while len(out) < 200_000:
        ev = {"ts": 1762300000 + len(out) // 50, "user": f"u{rng.randrange(5000):05d}",
              "event": rng.choice(["view", "click", "cart", "buy"]), "item": rng.randrange(20000),
              "price": round(rng.uniform(1, 300), 2), "session": f"s{rng.randrange(100000):06x}"}
        body = json.dumps(ev, separators=(",", ":")).encode()
        out += struct.pack("<IQ", len(body), rng.getrandbits(64)) + body
    return bytes(out)


def encode(data: bytes) -> bytes:
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description="snappy_compress 입력 파일 만들기")
    parser.add_argument("-o", "--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    data = encode(make_sample())
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print(len(data), "bytes")


if __name__ == "__main__":
    main()
```

수정 예 — 우리 서비스의 주문 이벤트 형식으로 예시 묶음 만들기 (위 전체 코드에서 `make_sample()`만 이것으로 바꿈):

```python
def make_sample() -> bytes:
    rng = random.Random(8)
    out = bytearray()
    while len(out) < 200_000:
        ev = {"order_id": rng.randrange(10**9), "amount": rng.randrange(1000, 500000), "status": "paid"}
        body = json.dumps(ev, separators=(",", ":")).encode()
        out += struct.pack("<IQ", len(body), rng.getrandbits(64)) + body
    return bytes(out)
```

### 2.2. 모듈 만들기

**사용자가 할 일**: 모듈 이름을 정합니다. 이 연산은 파라미터가 없습니다(블록 64 KiB 고정).

```bash
isc new kafka_snappy --operation snappy_compress
```

`modules/kafka_snappy/module.yaml`이 만들어지며 고칠 것은 없습니다.

### 2.3. 로직 작성

**사용자가 할 일**: 현재 없음. 추가 예정: 압축 직전 바이트마다 호출되는 변환 함수. 공식 커널 소스를 그대로 쓰는 연산이라, 추가하려면 플랫폼이 커널을 먼저 다시 짜야 합니다.

### 2.4. 검사

**사용자가 할 일**: 빌드하기 전에 모듈 설정을 검사합니다. 문제가 없으면 `PASS`가 표시됩니다.

```bash
isc check kafka_snappy
```

검사 결과에 나오는 `note`(CPU 기준선 없음)는 오류가 아닙니다. 속도 비교(`isc bench`)만 할 수 없다는 뜻입니다.

## 3. 실행 방법

**사용자가 할 일**: `--input`에 압축할 파일, `--output`에 결과를 저장할 경로를 지정합니다. 결과 확인 명령에는 `[원본 파일] [출력 파일]` 순서로 지정합니다.

```bash
# 에뮬레이션 실행 (카드 없이, 처음 한 번 수 분 빌드)
isc run kafka_snappy --input datasets/snappy_compress/kafka_events_200k.bin --output datasets/snappy_compress/kafka_snappy.emu.bin

# 결과 확인: 블록마다 풀어 원본과 비교 — ROUNDTRIP OK면 정상, 압축률(ratio)도 출력
python3 isc/operations/snappy_compress/check_output.py datasets/snappy_compress/kafka_events_200k.bin datasets/snappy_compress/kafka_snappy.emu.bin

# FPGA 카드 실행 (hw) (처음 한 번 hw 빌드 약 50분) — 카드 출력이 에뮬레이션 출력과 같은지 플랫폼이 자동 비교
isc run kafka_snappy --input datasets/snappy_compress/kafka_events_200k.bin --output datasets/snappy_compress/kafka_snappy.hw.bin --target hw
python3 isc/operations/snappy_compress/check_output.py datasets/snappy_compress/kafka_events_200k.bin datasets/snappy_compress/kafka_snappy.hw.bin

# 실행 기록
isc records --module kafka_snappy
isc show [실행 ID]
```

- 결과 확인 스크립트([`check_output.py`](check_output.py))는 `python-snappy` 없이 동작하는 순수 Python 해제기(`snappy_raw.py`)를 씁니다. 이 해제기는 공식 라이브러리 테스트가 카드에서 만든 `.snappy` 파일을 풀어 원본과 같은 것을 먼저 확인한 뒤 사용했습니다.
- 다른 묶음은 `--input`·`--output`만 바꿔 다시 실행합니다. 모듈은 그대로 재사용하고 다시 빌드하지 않습니다.
- 터미널 출력을 파일로도 남기려면 명령 뒤에 `2>&1 | tee [로그 파일]`을 붙입니다.
