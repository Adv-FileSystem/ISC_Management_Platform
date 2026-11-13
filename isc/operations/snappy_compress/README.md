# `snappy_compress` — Kafka로 보내는 이벤트 메시지 묶음의 개인정보 가림과 Snappy 압축 (연산 선택형 + 사용자 로직)

| 개발 방식 | 사용하는 라이브러리 함수 | 검증 상태 |
|---|---|---|
| 연산 선택형 + 사용자 로직 `isc_pre` | Vitis Data Compression L2 `xilSnappyCompress` (`kernel/snappy_kernel.cpp`). 압축 전 바이트마다 사용자 함수를 실행 | 플랫폼 자체 테스트 ✅ (1.0 사용자 로직 없음: hw_emu·hw / 1.1 사용자 로직: hw_emu, hw 미실행) · 공식 라이브러리 테스트 ✅ (검증 장치 AMD Alveo U50 · [검증 기록](../../../verification/u50_vitis_libraries/results/snappy_compress.md)) |

## 1. 연산 예시

이벤트 수집 파이프라인 개발자가 Kafka로 보내기 전에 클릭·구매 이벤트 메시지 묶음을 Snappy로 압축합니다. 이 압축을 CPU 대신 FPGA에서 합니다.

### 1.1. 수집 파이프라인 코드 안에서 직접 압축하는 기존 방식

```python
import snappy

with open("kafka_events.bin", "rb") as f:
    batch = f.read()
batch = bytes(ord("#") if 48 <= b <= 57 else b for b in batch)
packed = snappy.compress(batch)
```

### 1.2. ISC 전주기 시스템 기반의 방식

```bash
isc new kafka_snappy --operation snappy_compress --logic                                 # 처음 한 번: 모듈과 사용자 로직 파일(logic.hpp) 만들기
# modules/kafka_snappy/logic.hpp 의 return 줄을 가림 규칙으로 수정
isc check kafka_snappy                                                                   # 사용자 로직 검사
isc run kafka_snappy --input kafka_events.bin --output kafka_events.isc_snappy --target hw   # FPGA가 압축
python3 isc/operations/snappy_compress/check_output.py kafka_events.bin kafka_events.isc_snappy   # 풀어서 확인
```

- 입력: 아무 파일 (원시 바이트, 1바이트 이상). 바이트마다 사용자 함수 `isc_pre`가 돌려준 값을 압축합니다
- 사용자 로직 없이(`--logic` 생략) 만들면 원본을 그대로 압축합니다
- 출력: 입력을 64 KiB 블록으로 나눠, 블록마다 12바이트 헤더(`u32 raw_size, u32 stored_size, u32 flags`) + 페이로드(`flags=0`: 표준 Snappy raw 블록, `flags=1`: 압축 이득이 없어 원본 그대로)
- **1.1과 달리 출력 전체가 Snappy 프레이밍 형식(`.sz`)이나 Kafka 메시지 형식이 아닙니다.** 그대로 Kafka 압축 배치로 쓸 수 없고, 블록별 페이로드를 꺼내 쓰는 변환은 시험하지 않았습니다
- CPU Snappy와 FPGA는 서로 다른(둘 다 올바른) 압축 바이트를 낼 수 있어서, 풀어서 원본(사용자 로직이 있으면 바뀐 원본)과 같은지로 확인합니다
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

**사용자가 할 일**: 모듈 이름을 정합니다. 가림 규칙을 넣으려면 `--logic`을 붙입니다. 이 연산은 파라미터가 없습니다(블록 64 KiB 고정).

```bash
isc new kafka_snappy --operation snappy_compress --logic
```

- `modules/kafka_snappy/` 폴더에 `module.yaml`과 사용자 로직 파일 `logic.hpp`가 만들어집니다.
- 로직 파일을 먼저 작성한 뒤 모듈을 만들어도 됩니다. `isc template snappy_compress -o mask.hpp`로 시작 파일을 받아 2.3처럼 고친 다음, `--logic mask.hpp`로 모듈을 만듭니다.
- 압축만 필요하면 `--logic`을 빼고 만들고, 2.3은 건너뜁니다.

### 2.3. 로직 작성

**사용자가 할 일**: `modules/kafka_snappy/logic.hpp`를 열어 함수 `isc_pre`의 `return` 줄을 가림 규칙으로 바꾸고, 그 줄의 `ISC-TODO` 주석을 지웁니다.

아래는 "숫자(0~9)를 모두 `#`으로 가림" 규칙으로 고친 파일 전체입니다. 이벤트 메시지 속 전화번호·계좌번호 같은 숫자를 Kafka로 보내기 전에 지웁니다.

```cpp
#pragma once

#include <stdint.h>

inline uint8_t isc_pre(uint8_t b) {
    return (b >= '0' && b <= '9') ? '#' : b;
}
```

- **호출 시점**: 입력 바이트마다 한 번, 라이브러리 커널 `xilSnappyCompress`에 넘기기 직전에 호출됩니다. 함수가 돌려준 바이트가 압축됩니다.
- **입력값**: `b`는 원본 파일의 바이트(0~255)입니다.
- **바꾸면 안 되는 것**: 함수 이름(`isc_pre`), 인자 타입과 반환 타입(`uint8_t`). 인자 이름은 바꿔도 됩니다.
- **쓸 수 있는 것**: 비교·사칙·비트 연산, 상수(문자 `'#'` 포함), 조건식. 보조 함수나 상수를 더 정의해도 됩니다. 예:
  - `return (b >= 'A' && b <= 'Z') ? b + 32 : b;` — 대문자를 소문자로
  - `return b == '\t' ? ' ' : b;` — 탭을 공백으로
- **쓸 수 없는 것**: 동적 메모리 할당(`new`, `malloc`), 파일·네트워크 입출력(`fopen`, `open`, `socket`), `#pragma HLS INTERFACE`, 앞뒤 바이트 값 참조.
- 파일을 고치지 않고 두면(`return b;`) 원래 연산과 같은 결과, 즉 풀었을 때 원본이 그대로 나옵니다.
- 압축은 바뀐 바이트로 하므로, 출력 파일을 풀면 **원본이 아니라 바뀐 값**이 나옵니다. 원본이 필요하면 따로 보관합니다.

### 2.4. 검사

**사용자가 할 일**: 빌드하기 전에 모듈 설정과 로직 파일을 검사합니다. 문제가 없으면 `PASS`가 표시됩니다.

```bash
isc check kafka_snappy
```

- 함수 이름·인자·반환 타입이 다르거나 쓸 수 없는 문법이 있으면 `problem`이 표시되고, 고칠 때까지 빌드와 실행이 진행되지 않습니다.
- `ISC-TODO` 주석이 남아 있으면 `warning`이 표시됩니다. 빌드는 진행됩니다.
- 검사 결과에 나오는 `note`(CPU 기준선 없음)는 오류가 아닙니다. 속도 비교(`isc bench`)만 할 수 없다는 뜻입니다.

## 3. 실행 방법

**사용자가 할 일**:

- `--input`에 압축할 파일, `--output`에 결과를 저장할 경로를 지정합니다. 결과 확인 명령에는 `[원본 파일] [출력 파일]` 순서로 지정합니다.
- 결과 확인 스크립트 [`check_output.py`](check_output.py)의 `[바꾸는 곳]`에 있는 `pre()`를 2.3과 같은 규칙으로 맞춥니다. 2.3의 예시 규칙을 그대로 썼다면 고칠 것이 없습니다.
- 2.2에서 `--logic` 없이 모듈을 만들었다면, 결과 확인 명령 끝에 `--original`을 붙입니다.

```bash
# 에뮬레이션 실행 (카드 없이, 처음 한 번 수 분 빌드)
isc run kafka_snappy --input datasets/snappy_compress/kafka_events_200k.bin --output datasets/snappy_compress/kafka_snappy.emu.bin

# 결과 확인: 블록마다 풀어 원본에 같은 규칙을 적용한 값과 비교, ROUNDTRIP OK면 정상. 압축률(ratio)도 출력
python3 isc/operations/snappy_compress/check_output.py datasets/snappy_compress/kafka_events_200k.bin datasets/snappy_compress/kafka_snappy.emu.bin

# FPGA 카드 실행 (hw) (처음 한 번 hw 빌드 약 50분) — 카드 출력이 에뮬레이션 출력과 같은지 플랫폼이 자동 비교
isc run kafka_snappy --input datasets/snappy_compress/kafka_events_200k.bin --output datasets/snappy_compress/kafka_snappy.hw.bin --target hw
python3 isc/operations/snappy_compress/check_output.py datasets/snappy_compress/kafka_events_200k.bin datasets/snappy_compress/kafka_snappy.hw.bin

# 실행 기록
isc records --module kafka_snappy
isc show [실행 ID]
```

결과 확인 스크립트는 출력을 풀어서, 원본에 아래 `pre()` 함수를 바이트마다 적용한 값과 비교합니다. `pre()`는 2.3의 규칙을 Python으로 옮긴 것이므로, 2.3의 규칙을 바꾸면 이 함수도 함께 바꿉니다.

```python
def pre(b: int) -> int:
    return ord("#") if ord("0") <= b <= ord("9") else b
```

- 결과 확인 스크립트는 `python-snappy` 없이 동작하는 순수 Python 해제기(`snappy_raw.py`)를 씁니다.
- **규칙을 바꿀 때**: `logic.hpp`를 고치고 `isc run`을 다시 실행하면, 바뀐 로직으로 자동으로 다시 빌드합니다. 에뮬레이션으로 먼저 확인한 다음 FPGA 카드에서 실행합니다.
- **성능 측정**: 사용자 로직이 들어간 모듈은 `isc bench`에서 CPU 기준선과 비교하지 않습니다. 가속 효과를 측정하려면 `--logic` 없이 만든 모듈을 씁니다.
- **다른 묶음으로 실행할 때**: `--input`·`--output`만 바꿔 실행합니다. 로직이 같으면 다시 빌드하지 않습니다.
- 터미널 출력을 파일로도 남기려면 명령 뒤에 `2>&1 | tee [로그 파일]`을 붙입니다.
