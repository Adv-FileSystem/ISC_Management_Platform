# `adler32` — 파일 동기화용 블록 체크섬 (연산 선택형)

| 개발 방식 | 사용하는 라이브러리 함수 | 검증 상태 |
|---|---|---|
| 연산 선택형 | Vitis Data Compression L1 `xf::compression::adler32_mm` (`kernel/adler32_kernel.cpp`) | 플랫폼 자체 테스트 ✅ · 공식 라이브러리 테스트 ✅ (검증 장치 AMD Alveo U50 · [검증 기록](../../../verification/u50_vitis_libraries/results/adler32.md)) |

## 1. 연산 예시

파일 동기화 도구 개발자가 원격 파일과 비교해 바뀐 블록만 보내려고, 파일을 블록으로 나눠 블록마다 Adler-32 체크섬을 계산합니다(rsync 방식). 양쪽 체크섬이 다른 블록만 전송합니다. 이 체크섬 계산을 CPU 대신 FPGA에서 합니다.

### 1.1. 동기화 도구 코드 안에서 직접 계산하는 기존 방식

```python
import zlib

BLOCK = 256 * 1024
checksums = []
with open("dataset.tar", "rb") as f:
    while block := f.read(BLOCK):
        checksums.append(zlib.adler32(block))
```

### 1.2. ISC 전주기 시스템 기반의 방식

```bash
split -b 256K -d dataset.tar blocks/block_                                             # 파일을 블록 파일로 나누기
isc new transfer_adler --operation adler32                                             # 처음 한 번: 모듈 만들기
isc run transfer_adler --input blocks/block_00 --output blocks/block_00.adler --target hw   # FPGA가 블록 하나의 체크섬 계산
```

- 입력: 블록 파일 하나 (1바이트 이상, 형식 무관). **실행 한 번에 블록 하나**의 체크섬이 나오므로 블록마다 실행합니다
- 출력: 4바이트 Adler-32 값 (big-endian). 1.1의 `zlib.adler32`와 같은 값 (빈 데이터의 Adler-32는 `00000001`)
- 우발적 오류 검출용이며 보안(위·변조 방지) 용도가 아님 (→ [`sha256`](../sha256/README.md))
- 실제로 따라 할 절차는 2절(개발 절차)과 3절(실행 방법)에 있습니다.

## 2. 개발 절차

모든 명령은 저장소 루트에서 실행합니다.

### 2.1. 입력 준비

**사용자가 할 일**: 체크섬을 낼 블록 파일을 준비합니다. 블록 내용은 변환할 필요가 없지만, 큰 파일은 블록 크기로 잘라야 합니다.

```bash
# 내 파일을 256 KiB 블록 파일로 나누기
mkdir -p datasets/adler32/blocks
split -b 256K -d [내 파일] datasets/adler32/blocks/block_

# 또는 예시 블록(256 KiB, 고정 시드)으로 먼저 해 보기
python3 isc/operations/adler32/examples/make_input.py                 # → datasets/adler32/payload.bin
```

- 저장 위치를 바꾸려면 `-o 경로`를 붙입니다.

[`examples/make_input.py`](examples/make_input.py) 전체 코드입니다(주석 제외). 예시 데이터를 바꾸려면 `make_sample()`만 고치고, `encode()`(입력 형식)와 `main()`은 그대로 둡니다.

```python
import argparse
import random
import zlib
from pathlib import Path

DEFAULT_OUTPUT = "datasets/adler32/payload.bin"


def make_sample() -> bytes:
    return random.Random(11).randbytes(256 * 1024)


def encode(data: bytes) -> bytes:
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description="adler32 입력 파일 만들기")
    parser.add_argument("-o", "--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    data = make_sample()
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(encode(data))
    print(f"expected adler32 = {zlib.adler32(data):08x}")


if __name__ == "__main__":
    main()
```

수정 예 — 내 파일의 첫 블록을 예시로 쓰기 (위 전체 코드에서 `make_sample()`만 이것으로 바꿈):

```python
def make_sample() -> bytes:
    with open("/data/sync/dataset.tar", "rb") as f:
        return f.read(256 * 1024)
```

스크립트가 마지막에 출력하는 `expected adler32`는 `zlib.adler32`로 계산한 기준값입니다.

### 2.2. 모듈 만들기

**사용자가 할 일**: 모듈 이름을 정합니다 (소문자로 시작, 소문자·숫자·밑줄). 이 연산은 파라미터가 없습니다.

```bash
isc new transfer_adler --operation adler32
```

`modules/transfer_adler/module.yaml`이 만들어지며 고칠 것은 없습니다.

### 2.3. 로직 작성

**사용자가 할 일**: 없음. 체크섬 전에 입력을 바꾸면 원격 블록과 비교하는 목적이 사라지므로, 이 연산에는 사용자 로직 자리를 두지 않습니다.

### 2.4. 검사

**사용자가 할 일**: 빌드하기 전에 모듈 설정을 검사합니다. 문제가 없으면 `PASS`가 표시됩니다.

```bash
isc check transfer_adler
```

## 3. 실행 방법

**사용자가 할 일**: `--input`에 블록 파일, `--output`에 결과를 저장할 경로를 지정합니다. 결과 확인 명령에도 같은 블록 파일을 `--input`으로 지정합니다(예시 파일이면 생략).

```bash
# 에뮬레이션 실행 (카드 없이, 처음 한 번 수 분 빌드)
isc run transfer_adler --input datasets/adler32/payload.bin --output datasets/adler32/payload.adler.emu

# 결과 확인: 지금 쓰는 CPU 코드(zlib.adler32)와 비교 — MATCH면 정상
python3 isc/operations/adler32/examples/check_output.py datasets/adler32/payload.adler.emu --input datasets/adler32/payload.bin

# FPGA 카드 실행 (hw) (처음 한 번 hw 빌드 수십 분) — 카드 출력이 에뮬레이션 출력과 같은지 플랫폼이 자동 비교
isc run transfer_adler --input datasets/adler32/payload.bin --output datasets/adler32/payload.adler.hw --target hw
python3 isc/operations/adler32/examples/check_output.py datasets/adler32/payload.adler.hw --input datasets/adler32/payload.bin

# 블록이 여러 개면 블록마다 실행 (모듈은 재사용, 다시 빌드하지 않음)
for b in datasets/adler32/blocks/block_*; do
    isc run transfer_adler --input "$b" --output "$b.adler" --target hw
done

# 실행 기록
isc records --module transfer_adler
isc show [실행 ID]
```

- 받는 쪽에서도 같은 방법으로 블록 체크섬을 낸 뒤, 두 `.adler` 파일을 `cmp`로 비교하면 바뀐 블록을 찾을 수 있습니다.
- zlib 형식(RFC 1950) 스트림의 마지막 4바이트는 압축 전 원본의 Adler-32(big-endian)라서, 원본 파일을 입력으로 주면 그 4바이트와 같은 값이 나옵니다.
- 터미널 출력을 파일로도 남기려면 명령 뒤에 `2>&1 | tee [로그 파일]`을 붙입니다.
