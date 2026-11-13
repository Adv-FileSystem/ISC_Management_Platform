# `crc32` — 데이터 레이크 적재 파일의 무결성 검사 (연산 선택형)

| 개발 방식 | 사용하는 라이브러리 함수 | 검증 상태 |
|---|---|---|
| 연산 선택형 | Vitis Data Compression L1 `xf::compression::crc32_mm` (`kernel/crc32_kernel.cpp`) | 플랫폼 자체 테스트 ✅ · 공식 라이브러리 테스트 ✅ (검증 장치 AMD Alveo U50 · [검증 기록](../../../verification/u50_vitis_libraries/results/crc32.md)) |

## 1. 연산 예시

데이터 엔지니어가 매일 만들어지는 적재 파일(Parquet·CSV 등)을 객체 저장소에 올리기 전후로 CRC-32 값을 계산해, 옮기는 중에 파일이 깨지지 않았는지 확인합니다. 두 값이 같으면 파일이 그대로 옮겨진 것입니다. 이 계산을 CPU 대신 FPGA에서 합니다.

### 1.1. 파이프라인 코드 안에서 직접 계산하는 기존 방식

```python
import zlib

with open("part-0001.parquet", "rb") as f:
    crc = zlib.crc32(f.read())
print(f"{crc:08x}")
```

### 1.2. ISC 전주기 시스템 기반의 방식

```bash
isc new lake_crc --operation crc32                                               # 처음 한 번: 모듈 만들기
isc run lake_crc --input part-0001.parquet --output part-0001.crc --target hw   # FPGA가 계산 → 4바이트 결과 파일
```

- 입력: 아무 파일 (1바이트 이상, 형식 무관). 변환 없이 그대로 넘김
- 출력: 4바이트 CRC-32 값 (big-endian). 1.1의 `zlib.crc32`와 같은 값이며, `cksum` 명령의 CRC와는 다름
- 실제로 따라 할 절차는 2절(개발 절차)과 3절(실행 방법)에 있습니다.

## 2. 개발 절차

모든 명령은 저장소 루트에서 실행합니다.

### 2.1. 입력 준비

**사용자가 할 일**: 확인할 적재 파일을 정합니다. 파일을 그대로 쓰므로 변환할 필요가 없습니다.

예시 파일(256 KiB, 고정 시드)로 먼저 해 보려면:

```bash
python3 isc/operations/crc32/examples/make_input.py                 # → datasets/crc32/part-0001.bin
```

- 저장 위치를 바꾸려면 `-o 경로`를 붙입니다.

[`examples/make_input.py`](examples/make_input.py) 전체 코드입니다(주석 제외). 예시 데이터를 바꾸려면 `make_sample()`만 고치고, `encode()`(입력 형식)와 `main()`은 그대로 둡니다.

```python
import argparse
import random
import zlib
from pathlib import Path

DEFAULT_OUTPUT = "datasets/crc32/part-0001.bin"


def make_sample() -> bytes:
    return random.Random(7).randbytes(256 * 1024)


def encode(data: bytes) -> bytes:
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description="crc32 입력 파일 만들기")
    parser.add_argument("-o", "--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    data = make_sample()
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(encode(data))
    print(f"expected crc32 = {zlib.crc32(data):08x}")


if __name__ == "__main__":
    main()
```

수정 예 — 실제 적재 파일의 앞부분만 떼어 작게 시험해 보기 (위 전체 코드에서 `make_sample()`만 이것으로 바꿈):

```python
def make_sample() -> bytes:
    with open("/data/lake/2026-11-10/part-0001.parquet", "rb") as f:
        return f.read(1024 * 1024)
```

스크립트가 마지막에 출력하는 `expected crc32`는 `zlib.crc32`로 계산한 기준값입니다.

### 2.2. 모듈 만들기

**사용자가 할 일**: 모듈 이름을 정합니다 (소문자로 시작, 소문자·숫자·밑줄). 이 연산은 파라미터가 없습니다.

```bash
isc new lake_crc --operation crc32
```

`modules/lake_crc/module.yaml`이 만들어지며 고칠 것은 없습니다.

### 2.3. 로직 작성

**사용자가 할 일**: 없음. 체크섬 전에 입력을 바꾸면 "원본이 깨지지 않았는지 확인"이라는 목적이 사라지므로, 이 연산에는 사용자 로직 자리를 두지 않습니다.

### 2.4. 검사

**사용자가 할 일**: 빌드하기 전에 모듈 설정을 검사합니다. 문제가 없으면 `PASS`가 표시됩니다.

```bash
isc check lake_crc
```

## 3. 실행 방법

**사용자가 할 일**: `--input`에 2.1의 파일, `--output`에 결과를 저장할 경로를 지정합니다. 결과 확인 명령에도 같은 원본 파일을 `--input`으로 지정합니다(예시 파일이면 생략).

```bash
# 에뮬레이션 실행 (카드 없이, 처음 한 번 수 분 빌드)
isc run lake_crc --input datasets/crc32/part-0001.bin --output datasets/crc32/part-0001.crc.emu

# 결과 확인: 지금 쓰는 CPU 코드(zlib.crc32)와 비교 — MATCH면 정상
python3 isc/operations/crc32/examples/check_output.py datasets/crc32/part-0001.crc.emu --input datasets/crc32/part-0001.bin

# FPGA 카드 실행 (hw) (처음 한 번 hw 빌드 수십 분) — 카드 출력이 에뮬레이션 출력과 같은지 플랫폼이 자동 비교
isc run lake_crc --input datasets/crc32/part-0001.bin --output datasets/crc32/part-0001.crc.hw --target hw
python3 isc/operations/crc32/examples/check_output.py datasets/crc32/part-0001.crc.hw --input datasets/crc32/part-0001.bin

# 실행 기록
isc records --module lake_crc
isc show [실행 ID]
```

- 다른 파일을 확인할 때는 `--input`·`--output`만 바꿔 다시 실행합니다. 모듈은 그대로 재사용하고 다시 빌드하지 않습니다.
- 터미널 출력을 파일로도 남기려면 명령 뒤에 `2>&1 | tee [로그 파일]`을 붙입니다.
