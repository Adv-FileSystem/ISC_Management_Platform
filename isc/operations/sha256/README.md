# `sha256` — 배포 파일 다이제스트 대조 (연산 선택형)

| 개발 방식 | 사용하는 라이브러리 함수 | 검증 상태 |
|---|---|---|
| 연산 선택형 | Vitis Security L1 SHA-256 단계 (`preProcessing`·`generateMsgSchedule`·`sha256Digest`, `kernel/sha256_kernel.cpp`) | 플랫폼 자체 테스트 ✅ · 공식 라이브러리 테스트 미실행 (검증 장치 AMD Alveo U50 · [검증 기록](../../../verification/u50_vitis_libraries/results/sha256.md)) |

## 1. 연산 예시

배포 담당자가 설치 패키지·이미지 같은 배포 파일을 서버에 올린 뒤, 공개한 SHA-256 다이제스트와 서버의 파일이 같은지 확인합니다. 이 다이제스트 계산을 CPU 대신 FPGA에서 합니다.

### 1.1. 배포 스크립트 안에서 직접 계산하는 기존 방식

```python
import hashlib

with open("release.tar.gz", "rb") as f:
    digest = hashlib.sha256(f.read()).hexdigest()
print(digest == PUBLISHED_DIGEST)
```

### 1.2. ISC 전주기 시스템 기반의 방식

```bash
isc new release_sha --operation sha256                                                  # 처음 한 번: 모듈 만들기
isc run release_sha --input release.tar.gz --output release.sha256 --target hw          # FPGA가 계산 → 32바이트 결과 파일
python3 isc/operations/sha256/examples/check_output.py release.sha256 --expected [공개한 다이제스트]   # 공개 값과 비교
```

- 입력: 아무 파일 (원시 바이트, 형식 무관). 빈 파일 허용
- 출력: 32바이트 이진 다이제스트. `sha256sum`이 16진수로 보여 주는 값의 원래 바이트이며, 1.1의 `hashlib.sha256`과 같은 값
- 다이제스트는 "파일이 같은가"만 알려 줍니다. 누가 만들었는지(서명·출처 인증)는 보장하지 않습니다
- 실제로 따라 할 절차는 2절(개발 절차)과 3절(실행 방법)에 있습니다.

## 2. 개발 절차

모든 명령은 저장소 루트에서 실행합니다.

### 2.1. 입력 준비

**사용자가 할 일**: 확인할 배포 파일을 정합니다. 파일을 그대로 쓰므로 변환할 필요가 없습니다. 공개된 다이제스트와 비교하려면 그 16진수 값을 준비합니다.

예시 파일(100,000바이트, 고정 시드)로 먼저 해 보려면:

```bash
python3 isc/operations/sha256/examples/make_input.py                # → datasets/sha256/release.bin
sha256sum datasets/sha256/release.bin                               # 기준값 (CPU)
```

- 저장 위치를 바꾸려면 `-o 경로`를 붙입니다.

[`examples/make_input.py`](examples/make_input.py) 전체 코드입니다(주석 제외). 예시 데이터를 바꾸려면 `make_sample()`만 고치고, `encode()`(입력 형식)와 `main()`은 그대로 둡니다.

```python
import argparse
import random
from pathlib import Path

DEFAULT_OUTPUT = "datasets/sha256/release.bin"


def make_sample() -> bytes:
    return random.Random(3).randbytes(100_000)


def encode(data: bytes) -> bytes:
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description="sha256 입력 파일 만들기")
    parser.add_argument("-o", "--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(encode(make_sample()))


if __name__ == "__main__":
    main()
```

수정 예 — 256바이트짜리 작은 입력으로 빠르게 시험하기 (위 전체 코드에서 `make_sample()`만 이것으로 바꿈):

```python
def make_sample() -> bytes:
    return random.Random(3).randbytes(256)
```

### 2.2. 모듈 만들기

**사용자가 할 일**: 모듈 이름을 정합니다. 이 연산은 파라미터가 없습니다.

```bash
isc new release_sha --operation sha256
```

`modules/release_sha/module.yaml`이 만들어지며 고칠 것은 없습니다.

### 2.3. 로직 작성

**사용자가 할 일**: 없음. 해시 전에 입력을 바꾸면 공개된 다이제스트와 비교할 수 없으므로, 이 연산에는 사용자 로직 자리를 두지 않습니다.

### 2.4. 검사

**사용자가 할 일**: 빌드하기 전에 모듈 설정을 검사합니다. 문제가 없으면 `PASS`가 표시됩니다.

```bash
isc check release_sha
```

## 3. 실행 방법

**사용자가 할 일**: `--input`에 배포 파일, `--output`에 결과를 저장할 경로를 지정합니다. 결과 확인 명령에는 같은 배포 파일(`--input`)이나 공개된 다이제스트(`--expected`)를 지정합니다.

```bash
# 에뮬레이션 실행 (카드 없이, 처음 한 번 수 분 빌드)
isc run release_sha --input datasets/sha256/release.bin --output datasets/sha256/release.sha256.emu

# 결과 확인: 지금 쓰는 CPU 코드(hashlib.sha256)와 비교 — MATCH면 정상
python3 isc/operations/sha256/examples/check_output.py datasets/sha256/release.sha256.emu --input datasets/sha256/release.bin

# FPGA 카드 실행 (hw) (처음 한 번 hw 빌드 약 45분) — 카드 출력이 에뮬레이션 출력과 같은지 플랫폼이 자동 비교
isc run release_sha --input datasets/sha256/release.bin --output datasets/sha256/release.sha256.hw --target hw
python3 isc/operations/sha256/examples/check_output.py datasets/sha256/release.sha256.hw --input datasets/sha256/release.bin

# 공개된 다이제스트와 비교할 때
python3 isc/operations/sha256/examples/check_output.py datasets/sha256/release.sha256.hw --expected [공개한 16진수 다이제스트]

# 실행 기록 (입력 파일의 sha256도 함께 남음)
isc records --module release_sha
isc show [실행 ID]
```

- 명령줄에서만 보려면 `xxd -p -c 32 [출력 파일]`의 16진수를 `sha256sum` 값과 비교해도 됩니다.
- 다른 파일은 `--input`·`--output`만 바꿔 다시 실행합니다. 모듈은 그대로 재사용하고 다시 빌드하지 않습니다.
- 진행 단계는 다른 터미널에서 `isc watch [실행 ID]`로 볼 수 있습니다.
- 터미널 출력을 파일로도 남기려면 명령 뒤에 `2>&1 | tee [로그 파일]`을 붙입니다.
