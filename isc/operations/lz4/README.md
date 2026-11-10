# `lz4` — 모니터링 서버 접근 로그 압축 보관 (연산 선택형)

| 개발 방식 | 사용하는 라이브러리 함수 | 검증 상태 |
|---|---|---|
| 연산 선택형 | Vitis Data Compression L1 LZ4 압축 단계(`lzCompress` → `lzBestMatchFilter` → `lzBooster` → `lz4Compress`)를 감싼 단일 블록 커널 (`kernel/lz4_kernel.cpp`) | 플랫폼 자체 테스트 ✅ · 공식 라이브러리 테스트 ✅ (검증 장치 AMD Alveo U50 · [검증 기록](../../../verification/u50_vitis_libraries/results/lz4.md)) |

> **주의**: 일부 짧은 입력(예: 29바이트)에서 에뮬레이션(`hw_emu`) 실행이 끝나지 않을 수 있습니다. 실행이 멈추면 `Ctrl+C`로 중단하거나 `isc run`에 `--timeout 초`를 붙입니다. 이 문서의 예시 로그는 해당하지 않습니다.

## 1. 연산 예시

백엔드·인프라 개발자가 실시간 모니터링 서버에 1분마다 쌓이는 접근 로그를 압축해 보관합니다. 지금은 서버 CPU가 압축하느라 요청 처리에 쓸 CPU를 나눠 쓰고 있습니다. 이 압축을 CPU 대신 FPGA에서 합니다.

### 1.1. 로그 보관 스크립트 안에서 직접 압축하는 기존 방식

```python
import lz4.frame

with open("service.log", "rb") as f:
    data = f.read()
packed = lz4.frame.compress(data)
open("service.log.lz4", "wb").write(packed)
```

### 1.2. ISC 전주기 시스템 기반의 방식

```bash
isc new log_lz4 --operation lz4 --set block_size=65536                                  # 처음 한 번: 모듈 만들기 (블록 크기 지정)
isc run log_lz4 --input service.log --output service.log.isclz4 --target hw             # FPGA가 압축
python3 isc/operations/lz4/examples/check_output.py service.log.isclz4 --input service.log --restore service.log.restored   # 풀어서 확인
```

- 입력: 아무 파일 (원시 바이트). 빈 파일이면 빈 출력
- 출력: 플랫폼 고유 형식. 입력을 `block_size`씩 자른 블록마다 12바이트 헤더(`u32 raw_size, u32 stored_size, u32 flags`, little-endian) + 페이로드(`flags=0`: 표준 LZ4 블록, `flags=1`: 압축 이득이 없어 원본 그대로)
- **1.1과 달리 표준 `.lz4` 프레임이 아니라 `lz4 -d`로 풀 수 없습니다.** `check_output.py --restore`로 풉니다
- 같은 입력이라도 CPU LZ4와 FPGA는 서로 다른(둘 다 올바른) 압축 바이트를 낼 수 있어서, 압축 바이트가 아니라 **풀어서 원본과 같은지**로 확인합니다
- 실제로 따라 할 절차는 2절(개발 절차)과 3절(실행 방법)에 있습니다.

## 2. 개발 절차

모든 명령은 저장소 루트에서 실행합니다. 결과를 풀 때 Python `lz4` 패키지가 필요합니다(`python3 -m pip install lz4`).

### 2.1. 입력 준비

**사용자가 할 일**: 압축할 로그 파일을 정합니다. 파일을 그대로 쓰므로 변환할 필요가 없습니다.

예시 로그(웹 서버 접근 로그 1,500줄, 약 97 KiB, 고정 시드)로 먼저 해 보려면:

```bash
python3 isc/operations/lz4/examples/make_input.py                   # → datasets/lz4/service.log
```

- 저장 위치를 바꾸려면 `-o 경로`를 붙입니다.

[`examples/make_input.py`](examples/make_input.py) 전체 코드입니다(주석 제외). 예시 데이터를 바꾸려면 `make_sample()`만 고치고, `encode()`(입력 형식)와 `main()`은 그대로 둡니다.

```python
import argparse
import random
from pathlib import Path

DEFAULT_OUTPUT = "datasets/lz4/service.log"


def make_sample() -> str:
    rng = random.Random(1)
    lines = []
    for i in range(1500):
        level = rng.choice(["INFO", "INFO", "INFO", "WARN", "ERROR"])
        path = rng.choice(["/api/orders", "/api/users", "/health", "/api/cart"])
        lines.append(f"2026-11-05T10:{i // 60 % 60:02d}:{i % 60:02d}Z {level} GET {path} status=200 latency_ms={rng.randint(1, 900)}\n")
    return "".join(lines)


def encode(text: str) -> bytes:
    return text.encode()


def main() -> None:
    parser = argparse.ArgumentParser(description="lz4 입력 파일 만들기")
    parser.add_argument("-o", "--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(encode(make_sample()))


if __name__ == "__main__":
    main()
```

수정 예 — 우리 서비스의 로그 형식(JSON 한 줄 로그)으로 예시 만들기 (위 전체 코드에서 `make_sample()`만 이것으로 바꿈):

```python
def make_sample() -> str:
    rng = random.Random(1)
    lines = []
    for i in range(1500):
        status = rng.choice([200, 200, 200, 404, 500])
        lines.append(f'{{"ts":"2026-11-10T09:{i // 60 % 60:02d}:{i % 60:02d}Z","path":"/api/orders","status":{status}}}\n')
    return "".join(lines)
```

### 2.2. 모듈 만들기

**사용자가 할 일**: 모듈 이름과 블록 크기(`block_size`, 1024–65536, 기본 65536)를 정합니다.

```bash
isc new log_lz4 --operation lz4 --set block_size=65536
```

- 블록 크기는 나중에 `modules/log_lz4/module.yaml`의 `block_size`로도 바꿀 수 있고, 바꿔도 다시 빌드하지 않습니다.
- 블록이 작을수록 따로 풀 수 있는 단위가 작아지고, 보통 압축률은 낮아집니다.

### 2.3. 로직 작성

**사용자가 할 일**: 현재 없음. 추가 예정: 압축 직전 바이트마다 호출되는 변환 함수(예: 로그 속 개인정보 숫자 마스킹). 추가되면 이 단계에서 작성합니다.

### 2.4. 검사

**사용자가 할 일**: 빌드하기 전에 모듈 설정을 검사합니다. 문제가 없으면 `PASS`가 표시됩니다.

```bash
isc check log_lz4
```

## 3. 실행 방법

**사용자가 할 일**: `--input`에 압축할 파일, `--output`에 결과를 저장할 경로를 지정합니다. 결과 확인 명령에도 같은 원본 파일을 `--input`으로 지정합니다(예시 파일이면 생략).

```bash
# 에뮬레이션 실행 (카드 없이, 처음 한 번 수 분 빌드). 시뮬레이션이라 100 KB에도 수십 초 걸림
isc run log_lz4 --input datasets/lz4/service.log --output datasets/lz4/service.log.isclz4.emu

# 결과 확인: 블록마다 풀어 원본과 비교 — ROUNDTRIP OK면 정상, 압축률(ratio)도 출력
python3 isc/operations/lz4/examples/check_output.py datasets/lz4/service.log.isclz4.emu --input datasets/lz4/service.log

# FPGA 카드 실행 (hw) (처음 한 번 hw 빌드 수십 분) — 카드 출력이 에뮬레이션 출력과 같은지 플랫폼이 자동 비교
isc run log_lz4 --input datasets/lz4/service.log --output datasets/lz4/service.log.isclz4.hw --target hw
python3 isc/operations/lz4/examples/check_output.py datasets/lz4/service.log.isclz4.hw --input datasets/lz4/service.log

# 압축 파일을 다시 원본으로 풀기
python3 isc/operations/lz4/examples/check_output.py datasets/lz4/service.log.isclz4.hw --input datasets/lz4/service.log --restore datasets/lz4/service.log.restored

# 실행 기록 (compression_ratio, blocks, stored_blocks 지표 포함)
isc records --module log_lz4
isc show [실행 ID]
```

- 다른 로그는 `--input`·`--output`만 바꿔 다시 실행합니다. 모듈은 그대로 재사용하고 다시 빌드하지 않습니다.
- `stored_blocks` 지표가 크면 압축 이득이 없는 데이터(이미 압축됨, 무작위 등)라서 원본 그대로 저장된 블록이 많다는 뜻입니다.
- CPU 압축과 속도를 비교하려면 [README 5절](../../../README.md#5-가속-효과-검증)의 `isc plan` → `isc bench` → `isc report`를 씁니다.
- 터미널 출력을 파일로도 남기려면 명령 뒤에 `2>&1 | tee [로그 파일]`을 붙입니다.
