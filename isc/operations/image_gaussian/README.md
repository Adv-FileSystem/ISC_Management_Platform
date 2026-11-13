# `image_gaussian` — 문서 스캔 이미지 잡음 제거와 흑백 이진화 (연산 선택형 + 사용자 로직)

| 개발 방식 | 사용하는 라이브러리 함수 | 검증 상태 |
|---|---|---|
| 연산 선택형 + 사용자 로직 `isc_post` | Vitis Vision L1 `xf::cv::GaussianBlur` 3×3 (`kernel/gaussian_kernel.cpp`). 흐림 뒤 화소마다 사용자 함수를 실행 | 플랫폼 자체 테스트 ✅ (1.0 사용자 로직 없음: hw_emu·hw / 1.1 사용자 로직: hw_emu, hw 미실행) · 공식 라이브러리 테스트 미실행 (공식 테스트가 검증 장치를 지원하지 않음·OpenCV C++ 필요) (검증 장치 AMD Alveo U50 · [검증 기록](../../../verification/u50_vitis_libraries/results/image_gaussian.md)) |

## 1. 연산 예시

문서 관리·OCR 서비스 개발자가 스캐너로 들어온 문서 이미지의 잡음을 줄여 OCR 정확도를 높입니다. 흐림으로 잡음을 줄인 뒤, 글자는 검정(0)·바탕은 흰색(255)으로 이진화해 OCR 엔진에 넘깁니다.

3×3 Gaussian 흐림은 Vitis Vision 라이브러리가 하고, 이진화 규칙은 사용자가 C++ 함수 하나로 작성합니다. 두 단계 모두 FPGA에서 실행됩니다.

### 1.1. OCR 전처리 코드 안에서 직접 흐리게 하는 기존 방식

```python
import cv2
import numpy as np

img = cv2.imread("scan_0001.png", cv2.IMREAD_GRAYSCALE)
clean = cv2.GaussianBlur(img, (3, 3), 0.5, borderType=cv2.BORDER_CONSTANT)
clean = np.where(clean >= 128, 255, 0).astype(np.uint8)
text = ocr(clean)
```

### 1.2. ISC 전주기 시스템 기반의 방식

```bash
python3 isc/operations/image_gaussian/examples/make_input.py --image scan_0001.png -o scan_0001.bin   # 이미지 → 연산 입력 형식
isc new ocr_denoise --operation image_gaussian --set sigma_milli=500 --logic                        # 처음 한 번: 모듈과 사용자 로직 파일(logic.hpp) 만들기 (σ 0.5)
# modules/ocr_denoise/logic.hpp 의 return 줄을 이진화 규칙으로 수정
isc check ocr_denoise                                                                               # 사용자 로직 검사
isc run ocr_denoise --input scan_0001.bin --output scan_0001.clean.bin --target hw                   # FPGA가 흐림
```

- 입력: 헤더 `u32 rows, u32 cols` + `u8 pixels[rows][cols]` (8비트 회색조 1채널, little-endian). 2.1의 스크립트가 이미지에서 만들어 줍니다. 1 ≤ rows ≤ 1080, 1 ≤ cols ≤ 1920
- 출력: 입력과 같은 형식과 크기. 화소마다 사용자 함수 `isc_post`가 돌려준 값이 저장됩니다
- 사용자 로직 없이(`--logic` 생략) 만들면 흐린 영상이 그대로 출력됩니다
- 가장자리는 영상 밖을 0으로 채웁니다(`BORDER_CONSTANT`). 테두리 1화소는 어두워지고, OpenCV 기본값(`BORDER_REFLECT_101`)과는 테두리가 다릅니다
- 장치는 고정소수점 가중치를 써서 흐린 값이 OpenCV 결과와 조금 다를 수 있습니다(차이 2 이하면 정상). 이진화처럼 경계값이 있는 규칙에서는 경계 근처 화소의 결과가 1.1과 다를 수 있습니다
- 실제로 따라 할 절차는 2절(개발 절차)과 3절(실행 방법)에 있습니다.

## 2. 개발 절차

모든 명령은 저장소 루트에서 실행합니다. 입력 변환과 결과 확인에 Python `numpy`, `opencv-python`이 필요합니다.

### 2.1. 입력 준비

**사용자가 할 일**: 쓸 이미지를 이 연산의 입력 형식으로 변환합니다.

```bash
# 내 이미지(PNG·JPEG 등) → 회색조 + 헤더
python3 isc/operations/image_gaussian/examples/make_input.py --image [이미지 파일] -o datasets/image_gaussian/[이름].bin

# 또는 예시 영상(256×256 합성 영상: 종이 바탕 + 거래 명세서 글자 + 스캐너 잡음, 고정 시드)으로 먼저 해 보기
python3 isc/operations/image_gaussian/examples/make_input.py          # → datasets/image_gaussian/ocr_scan_256.bin
```

여러 장은 셸 반복문으로 한 장씩 변환합니다(코드 수정 불필요):

```bash
for f in data/scans/*.png; do
    python3 isc/operations/image_gaussian/examples/make_input.py --image "$f" -o "datasets/image_gaussian/$(basename "$f" .png).bin"
done
```

[`examples/make_input.py`](examples/make_input.py) 전체 코드입니다(주석 제외). 이미지를 읽는 방식을 바꿔야 하면 `read_image()`(내 이미지 읽기) 또는 `make_sample()`(예시 영상)만 고치고, `encode()`(입력 형식)와 `main()`은 그대로 둡니다.

```python
import argparse
import struct
from pathlib import Path

import cv2
import numpy as np

DEFAULT_OUTPUT = "datasets/image_gaussian/ocr_scan_256.bin"


def make_sample() -> np.ndarray:
    rng = np.random.default_rng(5); h = w = 256
    a = np.full((h, w), 235, np.uint8)
    for i, line in enumerate(["INVOICE 2026-11", "ITEM  QTY  PRICE", "A-102   3   4500", "B-207   1  12000", "TOTAL     25500"]):
        cv2.putText(a, line, (12, 40 + 44 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.7, 30, 2)
    return np.clip(a.astype(np.int16) + rng.normal(0, 12, a.shape), 0, 255).astype(np.uint8)


def read_image(path: str) -> np.ndarray:
    image = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise SystemExit(f"영상을 읽지 못했습니다: {path}")
    return image


def encode(image: np.ndarray) -> bytes:
    image = np.ascontiguousarray(image, dtype=np.uint8)
    if image.ndim != 2:
        raise ValueError("회색조(2차원) 영상만 받습니다")
    rows, cols = image.shape
    return struct.pack("<II", rows, cols) + image.tobytes()


def main() -> None:
    parser = argparse.ArgumentParser(description="image_gaussian 입력 파일 만들기")
    parser.add_argument("-o", "--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--image", help="실제 사진 파일 (PNG, JPEG 등)")
    args = parser.parse_args()
    image = read_image(args.image) if args.image else make_sample()
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(encode(image))
    print("ok", *image.shape)


if __name__ == "__main__":
    main()
```

수정 예 — 큰 스캔 이미지를 커널 최대 크기(1920×1080) 안으로 줄여 읽기 (위 전체 코드에서 `read_image()`만 이것으로 바꿈):

```python
def read_image(path: str) -> np.ndarray:
    image = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise SystemExit(f"영상을 읽지 못했습니다: {path}")
    rows, cols = image.shape
    scale = min(1.0, 1080 / rows, 1920 / cols)
    if scale < 1.0:
        image = cv2.resize(image, (int(cols * scale), int(rows * scale)), interpolation=cv2.INTER_AREA)
    return image
```

### 2.2. 모듈 만들기

**사용자가 할 일**: 모듈 이름과 흐림 정도(`sigma_milli` = σ × 1000, 기본 500 = σ 0.5, 100~10000)를 정합니다. 이진화 규칙을 넣으려면 `--logic`을 붙입니다.

```bash
isc new ocr_denoise --operation image_gaussian --set sigma_milli=500 --logic
```

- `modules/ocr_denoise/` 폴더에 `module.yaml`과 사용자 로직 파일 `logic.hpp`가 만들어집니다.
- 로직 파일을 먼저 작성한 뒤 모듈을 만들어도 됩니다. `isc template image_gaussian -o binarize.hpp`로 시작 파일을 받아 2.3처럼 고친 다음, `--logic binarize.hpp`로 모듈을 만듭니다.
- 흐림만 필요하면 `--logic`을 빼고 만들고, 2.3은 건너뜁니다.

- 값은 나중에 `modules/ocr_denoise/module.yaml`의 `sigma_milli`로도 바꿀 수 있고, 바꿔도 다시 빌드하지 않습니다.
- σ 0.5 외의 값은 시험하지 않았습니다.

### 2.3. 로직 작성

**사용자가 할 일**: `modules/ocr_denoise/logic.hpp`를 열어 함수 `isc_post`의 `return` 줄을 이진화 규칙으로 바꾸고, 그 줄의 `ISC-TODO` 주석을 지웁니다.

아래는 "128 이상은 흰색(255), 그 밖은 검정(0)" 규칙으로 고친 파일 전체입니다.

```cpp
#pragma once

#include <stdint.h>

inline uint8_t isc_post(uint8_t px) {
    return px >= 128 ? 255 : 0;
}
```

- **호출 시점**: 라이브러리 함수 `xf::cv::GaussianBlur`가 영상을 흐리게 한 뒤, 출력 화소마다 한 번씩 호출됩니다. 함수가 돌려준 값이 그 자리의 출력 화소가 됩니다.
- **입력값**: `px`는 흐린 영상의 화소 값(0~255)입니다.
- **바꾸면 안 되는 것**: 함수 이름(`isc_post`), 인자 타입과 반환 타입(`uint8_t`). 인자 이름은 바꿔도 됩니다.
- **쓸 수 있는 것**: 비교·사칙·비트 연산, 상수, 조건식. 보조 함수나 상수를 더 정의해도 됩니다. 예:
  - `return px < 40 ? 0 : px;` — 아주 어두운 잡음은 0으로
  - `return 255 - px;` — 명암 반전(흰 글자·검은 바탕 문서)
- **쓸 수 없는 것**: 동적 메모리 할당(`new`, `malloc`), 파일·네트워크 입출력(`fopen`, `open`, `socket`), `#pragma HLS INTERFACE`, 주변 화소 값 참조.
- 파일을 고치지 않고 두면(`return px;`) 원래 연산과 같은 결과, 즉 흐린 영상이 그대로 나옵니다.

### 2.4. 검사

**사용자가 할 일**: 빌드하기 전에 모듈 설정과 로직 파일을 검사합니다. 문제가 없으면 `PASS`가 표시됩니다.

```bash
isc check ocr_denoise
```

- 함수 이름·인자·반환 타입이 다르거나 쓸 수 없는 문법이 있으면 `problem`이 표시되고, 고칠 때까지 빌드와 실행이 진행되지 않습니다.
- `ISC-TODO` 주석이 남아 있으면 `warning`이 표시됩니다. 빌드는 진행됩니다.

## 3. 실행 방법

**사용자가 할 일**:

- `--input`에 2.1의 파일, `--output`에 결과를 저장할 경로를 지정합니다. 결과 확인 명령에도 같은 입력 파일(`--input`)과 2.2의 `sigma_milli` 값(`--sigma-milli`)을 지정합니다(예시 파일과 기본값이면 생략).
- 결과 확인 스크립트 [`examples/check_output.py`](examples/check_output.py)의 `[바꾸는 곳]`에 있는 `post()`를 2.3과 같은 규칙으로 맞춥니다. 2.3의 예시 규칙을 그대로 썼다면 고칠 것이 없습니다.
- 2.2에서 `--logic` 없이 모듈을 만들었다면, 결과 확인 명령에 `--original`을 붙입니다.

```bash
# 에뮬레이션 실행 (카드 없이, 처음 한 번 수 분 빌드. 빌드가 캐시에 있으면 이 입력은 약 40초)
isc run ocr_denoise --input datasets/image_gaussian/ocr_scan_256.bin --output datasets/image_gaussian/ocr_denoise.emu.bin

# 결과 확인: cv2.GaussianBlur 결과에 같은 규칙을 적용한 값과 화소마다 비교, PASS가 나오면 정상
python3 isc/operations/image_gaussian/examples/check_output.py datasets/image_gaussian/ocr_denoise.emu.bin --input datasets/image_gaussian/ocr_scan_256.bin --sigma-milli 500

# FPGA 카드 실행 (hw) (처음 한 번 hw 빌드 약 43분) — 카드 출력이 에뮬레이션 출력과 같은지 플랫폼이 자동 비교
isc run ocr_denoise --input datasets/image_gaussian/ocr_scan_256.bin --output datasets/image_gaussian/ocr_denoise.hw.bin --target hw
python3 isc/operations/image_gaussian/examples/check_output.py datasets/image_gaussian/ocr_denoise.hw.bin --input datasets/image_gaussian/ocr_scan_256.bin --sigma-milli 500 --png datasets/image_gaussian

# 실행 기록
isc records --module ocr_denoise
isc show [실행 ID]
```

결과 확인 스크립트는 `cv2.GaussianBlur`로 흐린 뒤, 아래 `post()` 함수를 화소마다 적용해 FPGA 출력과 비교합니다. FPGA의 흐린 값은 OpenCV와 2까지 다를 수 있으므로, "OpenCV 값 ±2 중 하나에 `post()`를 적용한 값"이면 그 화소는 맞습니다(`outside_tolerance`가 0이면 PASS). `post()`는 2.3의 규칙을 Python으로 옮긴 것이므로, 2.3의 규칙을 바꾸면 이 함수도 함께 바꿉니다.

```python
def post(px: int) -> int:
    return 255 if px >= 128 else 0
```

- `--png 폴더`를 붙이면 입력과 FPGA 결과를 `input.png`, `fpga.png`로 저장합니다.
- **규칙을 바꿀 때**: `logic.hpp`를 고치고 `isc run`을 다시 실행하면, 바뀐 로직으로 자동으로 다시 빌드합니다. 에뮬레이션으로 먼저 확인한 다음 FPGA 카드에서 실행합니다.
- **성능 측정**: 사용자 로직이 들어간 모듈은 `isc bench`에서 CPU 기준선과 비교하지 않습니다. 가속 효과를 측정하려면 `--logic` 없이 만든 모듈을 씁니다.
- **다른 이미지로 실행할 때**: 2.1처럼 변환한 뒤 `--input`·`--output`만 바꿔 실행합니다. 로직이 같으면 다시 빌드하지 않습니다.
- 큰 이미지를 나눠 처리할 때는 나눈 경계의 1화소가 0 채움이 되는 점에 주의합니다. 컬러 이미지를 채널별로 세 번 실행하는 방식은 시험하지 않았습니다.
- 터미널 출력을 파일로도 남기려면 명령 뒤에 `2>&1 | tee [로그 파일]`을 붙입니다.
