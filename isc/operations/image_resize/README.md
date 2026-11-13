# `image_resize` — 학습 데이터 이미지를 모델 입력 크기로 축소하고 밝기 보정 (연산 선택형 + 사용자 로직)

| 개발 방식 | 사용하는 라이브러리 함수 | 검증 상태 |
|---|---|---|
| 연산 선택형 + 사용자 로직 `isc_post` | Vitis Vision L1 `xf::cv::resize` bilinear (`kernel/resize_kernel.cpp`). 축소 뒤 화소마다 사용자 함수를 실행 | 플랫폼 자체 테스트 ✅ (1.0 사용자 로직 없음: hw_emu·hw / 1.1 사용자 로직: hw_emu, hw 미실행) · 공식 라이브러리 테스트 미실행 (공식 테스트가 검증 장치를 지원하지 않음·OpenCV C++ 필요) (검증 장치 AMD Alveo U50 · [검증 기록](../../../verification/u50_vitis_libraries/results/image_resize.md)) |

## 1. 연산 예시

AI 모델 개발자가 학습 데이터 이미지를 모델 입력 크기(예: 1920×1080 → 960×540)로 일괄 축소합니다. 야간에 촬영한 영상은 전체가 어두워서, 줄인 뒤 밝기를 일정하게 올립니다.

bilinear 축소는 Vitis Vision 라이브러리가 하고, 밝기 보정 규칙은 사용자가 C++ 함수 하나로 작성합니다. 두 단계 모두 FPGA에서 실행됩니다.

### 1.1. 전처리 코드 안에서 직접 축소하는 기존 방식

```python
import cv2
import numpy as np

img = cv2.imread("frame_0001.png", cv2.IMREAD_GRAYSCALE)
small = cv2.resize(img, (960, 540), interpolation=cv2.INTER_LINEAR)
small = np.where(small > 215, 255, small.astype(int) + 40).astype(np.uint8)
cv2.imwrite("frame_0001_960.png", small)
```

### 1.2. ISC 전주기 시스템 기반의 방식

```bash
python3 isc/operations/image_resize/examples/make_input.py --image frame_0001.png -o frame_0001.bin   # 이미지 → 연산 입력 형식
isc new train_input --operation image_resize --set out_rows=540 --set out_cols=960 --logic          # 처음 한 번: 모듈과 사용자 로직 파일(logic.hpp) 만들기
# modules/train_input/logic.hpp 의 return 줄을 밝기 보정 규칙으로 수정
isc check train_input                                                                               # 사용자 로직 검사
isc run train_input --input frame_0001.bin --output frame_0001_960.bin --target hw --no-validate      # FPGA가 축소
```

- 입력: 헤더 `u32 rows, u32 cols` + `u8 pixels[rows][cols]` (8비트 회색조 1채널, little-endian). 2.1의 스크립트가 이미지에서 만들어 줍니다. 입력 최대 3840×2160
- 출력: 같은 형식으로 줄인 이미지. 출력 최대 1920×1080. **한 번에 2배까지만 줄일 수 있습니다**(`rows ≤ 2·out_rows`, `cols ≤ 2·out_cols`). 화소마다 사용자 함수 `isc_post`가 돌려준 값이 저장됩니다
- 사용자 로직 없이(`--logic` 생략) 만들면 줄인 영상이 그대로 출력됩니다
- 장치는 고정소수점 보간이라 줄인 값이 OpenCV 결과와 조금 다를 수 있습니다(차이 5 이하면 정상)
- 1920×1080 같은 큰 영상은 에뮬레이션(hw_emu)이 매우 느려서, 카드에서 실행할 때 `--no-validate`로 에뮬레이션 대조를 건너뛰고 결과 확인 스크립트로 OpenCV와 비교합니다(3절)
- 실제로 따라 할 절차는 2절(개발 절차)과 3절(실행 방법)에 있습니다.

## 2. 개발 절차

모든 명령은 저장소 루트에서 실행합니다. 입력 변환과 결과 확인에 Python `numpy`, `opencv-python`이 필요합니다.

### 2.1. 입력 준비

**사용자가 할 일**: 쓸 이미지를 이 연산의 입력 형식으로 변환합니다.

```bash
# 내 이미지(PNG·JPEG 등) → 회색조 + 헤더
python3 isc/operations/image_resize/examples/make_input.py --image [이미지 파일] -o datasets/image_resize/[이름].bin

# 또는 예시 영상(합성 장면: 명암 배경 + 사물 12개 + 약한 잡음, 고정 시드)을 두 크기로 만들어 먼저 해 보기
python3 isc/operations/image_resize/examples/make_input.py            # → datasets/image_resize/frame_384x216.bin, frame_1920x1080.bin
```

- 작은 예시(384×216)는 에뮬레이션으로 확인하는 용도, 큰 예시(1920×1080)는 카드에서만 실행하는 용도입니다(3절).

여러 장은 셸 반복문으로 한 장씩 변환합니다(코드 수정 불필요):

```bash
for f in data/train/*.png; do
    python3 isc/operations/image_resize/examples/make_input.py --image "$f" -o "datasets/image_resize/$(basename "$f" .png).bin"
done
```

[`examples/make_input.py`](examples/make_input.py) 전체 코드입니다(주석 제외). 이미지를 읽는 방식을 바꿔야 하면 `read_image()`(내 이미지 읽기) 또는 `make_sample()`(예시 영상)만 고치고, `encode()`(입력 형식)·`write()`와 `main()`은 그대로 둡니다.

```python
import argparse
import struct
from pathlib import Path

import cv2
import numpy as np

SAMPLES = {"datasets/image_resize/frame_384x216.bin": (384, 216), "datasets/image_resize/frame_1920x1080.bin": (1920, 1080)}


def make_sample(w: int, h: int, seed: int = 3) -> np.ndarray:
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    a = (60 + 120 * xx / w + 40 * np.sin(yy / h * 6)).astype(np.float32)
    for _ in range(12):
        cx, cy, r = rng.integers(0, w), rng.integers(0, h), int(rng.integers(h // 20, h // 6))
        cv2.circle(a, (int(cx), int(cy)), r, float(rng.integers(0, 255)), -1)
    return np.clip(a + rng.normal(0, 6, a.shape), 0, 255).astype(np.uint8)


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


def write(path: str, image: np.ndarray) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(encode(image))


def main() -> None:
    parser = argparse.ArgumentParser(description="image_resize 입력 파일 만들기")
    parser.add_argument("-o", "--output", help="--image와 함께: 저장 위치")
    parser.add_argument("--image", help="실제 사진 파일 (PNG, JPEG 등)")
    args = parser.parse_args()
    if args.image:
        if not args.output:
            parser.error("--image에는 -o가 필요합니다")
        image = read_image(args.image)
        write(args.output, image)
        print("ok", *image.shape)
        return
    for path, (w, h) in SAMPLES.items():
        write(path, make_sample(w, h))
    print("ok")


if __name__ == "__main__":
    main()
```

수정 예 — 세로가 긴 사진을 16:9로 가운데만 잘라 읽기 (2배 축소 제한을 맞추려는 경우. 위 전체 코드에서 `read_image()`만 이것으로 바꿈):

```python
def read_image(path: str) -> np.ndarray:
    image = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise SystemExit(f"영상을 읽지 못했습니다: {path}")
    rows, cols = image.shape
    target = cols * 9 // 16
    if rows > target:
        top = (rows - target) // 2
        image = image[top:top + target]
    return image
```

### 2.2. 모듈 만들기

**사용자가 할 일**: 모듈 이름과 출력 크기(`out_rows` 기본 540·최대 1080, `out_cols` 기본 960·최대 1920)를 정합니다. 밝기 보정 규칙을 넣으려면 `--logic`을 붙입니다. 출력 크기마다 모듈을 따로 만들 수 있고, 로직 파일이 같으면 같은 빌드를 함께 씁니다.

```bash
isc new train_preview --operation image_resize --set out_rows=108 --set out_cols=192 --logic   # 작은 예시 확인용 (384×216 → 192×108)
isc new train_input   --operation image_resize --set out_rows=540 --set out_cols=960 --logic   # 원래 크기 (1920×1080 → 960×540)
```

- 각 모듈 폴더(`modules/train_preview/`, `modules/train_input/`)에 `module.yaml`과 사용자 로직 파일 `logic.hpp`가 만들어집니다.
- 로직 파일을 먼저 작성한 뒤 모듈을 만들어도 됩니다. `isc template image_resize -o brighten.hpp`로 시작 파일을 받아 2.3처럼 고친 다음, 두 모듈 모두 `--logic brighten.hpp`로 만듭니다.
- 축소만 필요하면 `--logic`을 빼고 만들고, 2.3은 건너뜁니다.

- 출력 크기는 나중에 `modules/[모듈 이름]/module.yaml`의 `out_rows`·`out_cols`로도 바꿀 수 있고, 바꿔도 다시 빌드하지 않습니다.
- 2배보다 크게 줄이려면(예: 3840×2160 → 960×540) 두 번 나눠 실행합니다(3840×2160 → 1920×1080 → 960×540). 이 방법과 확대는 시험하지 않았습니다.

### 2.3. 로직 작성

**사용자가 할 일**: `modules/train_preview/logic.hpp`를 열어 함수 `isc_post`의 `return` 줄을 밝기 보정 규칙으로 바꾸고, 그 줄의 `ISC-TODO` 주석을 지웁니다. 고친 파일을 다른 모듈에도 복사해 같은 규칙을 씁니다.

```bash
cp modules/train_preview/logic.hpp modules/train_input/logic.hpp
```

아래는 "모든 화소를 40 밝게, 255를 넘으면 255" 규칙으로 고친 파일 전체입니다.

```cpp
#pragma once

#include <stdint.h>

inline uint8_t isc_post(uint8_t px) {
    return px > 215 ? 255 : px + 40;
}
```

- **호출 시점**: 라이브러리 함수 `xf::cv::resize`가 영상을 줄인 뒤, 출력 화소마다 한 번씩 호출됩니다. 함수가 돌려준 값이 그 자리의 출력 화소가 됩니다.
- **입력값**: `px`는 줄인 영상의 화소 값(0~255)입니다.
- **바꾸면 안 되는 것**: 함수 이름(`isc_post`), 인자 타입과 반환 타입(`uint8_t`). 인자 이름은 바꿔도 됩니다.
- **쓸 수 있는 것**: 비교·사칙·비트 연산, 상수, 조건식. 보조 함수나 상수를 더 정의해도 됩니다. 예:
  - `return 255 - px;` — 명암 반전
  - `return px >= 128 ? 255 : 0;` — 흑백 이진화
- **쓸 수 없는 것**: 동적 메모리 할당(`new`, `malloc`), 파일·네트워크 입출력(`fopen`, `open`, `socket`), `#pragma HLS INTERFACE`, 주변 화소 값 참조.
- 파일을 고치지 않고 두면(`return px;`) 원래 연산과 같은 결과, 즉 줄인 영상이 그대로 나옵니다.

### 2.4. 검사

**사용자가 할 일**: 빌드하기 전에 모듈 설정과 로직 파일을 검사합니다. 문제가 없으면 `PASS`가 표시됩니다.

```bash
isc check train_preview
isc check train_input
```

- 함수 이름·인자·반환 타입이 다르거나 쓸 수 없는 문법이 있으면 `problem`이 표시되고, 고칠 때까지 빌드와 실행이 진행되지 않습니다.
- `ISC-TODO` 주석이 남아 있으면 `warning`이 표시됩니다. 빌드는 진행됩니다.

## 3. 실행 방법

**사용자가 할 일**:

- `--input`에 2.1의 파일, `--output`에 결과를 저장할 경로를 지정합니다. 결과 확인 명령에도 같은 입력 파일을 `--input`으로 지정합니다(출력 크기는 출력 파일에서 읽음). 큰 영상은 `--no-validate`를 붙이고 결과 확인을 반드시 합니다.
- 결과 확인 스크립트 [`examples/check_output.py`](examples/check_output.py)의 `[바꾸는 곳]`에 있는 `post()`를 2.3과 같은 규칙으로 맞춥니다. 2.3의 예시 규칙을 그대로 썼다면 고칠 것이 없습니다.
- 2.2에서 `--logic` 없이 모듈을 만들었다면, 결과 확인 명령에 `--original`을 붙입니다.

```bash
# 에뮬레이션 실행: 작은 영상만 (처음 한 번 수 분 빌드. 빌드가 캐시에 있으면 약 80초)
isc run train_preview --input datasets/image_resize/frame_384x216.bin --output datasets/image_resize/train_preview.emu.bin

# 결과 확인: cv2.resize 결과에 같은 규칙을 적용한 값과 화소마다 비교, PASS가 나오면 정상
python3 isc/operations/image_resize/examples/check_output.py datasets/image_resize/train_preview.emu.bin --input datasets/image_resize/frame_384x216.bin

# FPGA 카드 실행 (hw), 작은 영상 (처음 한 번 hw 빌드 약 44분) — 카드 출력이 에뮬레이션 출력과 같은지 플랫폼이 자동 비교
isc run train_preview --input datasets/image_resize/frame_384x216.bin --output datasets/image_resize/train_preview.hw.bin --target hw
python3 isc/operations/image_resize/examples/check_output.py datasets/image_resize/train_preview.hw.bin --input datasets/image_resize/frame_384x216.bin

# FPGA 카드 실행 (hw), 원래 크기 — 에뮬레이션 대조를 건너뛰므로(validation: skipped) 결과 확인으로 OpenCV와 꼭 비교
isc run train_input --input datasets/image_resize/frame_1920x1080.bin --output datasets/image_resize/train_input.hw.bin --target hw --no-validate
python3 isc/operations/image_resize/examples/check_output.py datasets/image_resize/train_input.hw.bin --input datasets/image_resize/frame_1920x1080.bin --png datasets/image_resize

# 실행 기록
isc records --module train_input
isc show [실행 ID]
```

결과 확인 스크립트는 `cv2.resize`로 줄인 뒤, 아래 `post()` 함수를 화소마다 적용해 FPGA 출력과 비교합니다. FPGA의 줄인 값은 OpenCV와 5까지 다를 수 있으므로, "OpenCV 값 ±5 중 하나에 `post()`를 적용한 값"이면 그 화소는 맞습니다(`outside_tolerance`가 0이면 PASS). `post()`는 2.3의 규칙을 Python으로 옮긴 것이므로, 2.3의 규칙을 바꾸면 이 함수도 함께 바꿉니다.

```python
def post(px: int) -> int:
    return 255 if px > 215 else px + 40
```

- `--png 폴더`를 붙이면 FPGA 결과를 PNG로 저장합니다.
- **규칙을 바꿀 때**: `logic.hpp`를 고치고 `isc run`을 다시 실행하면, 바뀐 로직으로 자동으로 다시 빌드합니다. 에뮬레이션으로 먼저 확인한 다음 FPGA 카드에서 실행합니다.
- **성능 측정**: 사용자 로직이 들어간 모듈은 `isc bench`에서 CPU 기준선과 비교하지 않습니다. 가속 효과를 측정하려면 `--logic` 없이 만든 모듈을 씁니다.
- **다른 이미지로 실행할 때**: 2.1처럼 변환한 뒤 `--input`·`--output`만 바꿔 실행합니다. 로직이 같으면 다시 빌드하지 않습니다.
- 컬러 이미지를 채널별로 세 번 실행하는 방식은 시험하지 않았습니다. 고품질 축소(bicubic)가 필요하면 [`image_bicubic`](../image_bicubic/README.md)을 씁니다.
- 터미널 출력을 파일로도 남기려면 명령 뒤에 `2>&1 | tee [로그 파일]`을 붙입니다.
