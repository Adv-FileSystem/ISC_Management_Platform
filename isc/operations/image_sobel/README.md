# `image_sobel` — 제조 라인 영상에서 부품 윤곽 추출과 잡음 제거 (연산 선택형 + 사용자 로직)

| 개발 방식 | 사용하는 라이브러리 함수 | 검증 상태 |
|---|---|---|
| 연산 선택형 + 사용자 로직 `isc_post` | Vitis Vision L1 `xf::cv::Sobel` 3×3 (`kernel/sobel_kernel.cpp`). 기울기 계산 뒤 화소마다 사용자 함수를 실행 | 플랫폼 자체 테스트 ✅ (1.0 사용자 로직 없음: hw_emu·hw / 1.1 사용자 로직: hw_emu, hw 미실행) · 공식 라이브러리 테스트 미실행 (공식 테스트가 검증 장치를 지원하지 않음·OpenCV C++ 필요) (검증 장치 AMD Alveo U50 · [검증 기록](../../../verification/u50_vitis_libraries/results/image_sobel.md)) |

## 1. 연산 예시

제조 라인 비전 검사 시스템 개발자가 카메라 영상에서 부품 윤곽(에지)을 뽑아 불량 검사의 전처리로 씁니다. 카메라 잡음 때문에 약한 윤곽이 영상 전체에 흩어지므로, 기준보다 약한 값은 지우고 뚜렷한 윤곽만 남깁니다.

x·y 방향 기울기 계산은 Vitis Vision 라이브러리가 하고, 약한 윤곽을 지우는 규칙은 사용자가 C++ 함수 하나로 작성합니다. 두 단계 모두 FPGA에서 실행됩니다.

### 1.1. 검사 전처리 코드 안에서 직접 계산하는 기존 방식

```python
import cv2
import numpy as np

img = cv2.imread("part_0001.png", cv2.IMREAD_GRAYSCALE)
gx = cv2.Sobel(img, cv2.CV_8U, 1, 0, ksize=3, borderType=cv2.BORDER_CONSTANT)
gy = cv2.Sobel(img, cv2.CV_8U, 0, 1, ksize=3, borderType=cv2.BORDER_CONSTANT)
gx[gx < 60] = 0
gy[gy < 60] = 0
edges = np.maximum(gx, gy)
```

### 1.2. ISC 전주기 시스템 기반의 방식

```bash
python3 isc/operations/image_sobel/examples/make_input.py --image part_0001.png -o part_0001.bin   # 이미지 → 연산 입력 형식
isc new part_outline --operation image_sobel --logic                                             # 처음 한 번: 모듈과 사용자 로직 파일(logic.hpp) 만들기
# modules/part_outline/logic.hpp 의 return 줄을 잡음 제거 규칙으로 수정
isc check part_outline                                                                           # 사용자 로직 검사
isc run part_outline --input part_0001.bin --output part_0001.grad.bin --target hw               # FPGA가 x·y 기울기 계산
```

- 입력: 헤더 `u32 rows, u32 cols` + `u8 pixels[rows][cols]` (8비트 회색조 1채널, little-endian). 2.1의 스크립트가 이미지에서 만들어 줍니다. 1 ≤ rows ≤ 1080, 1 ≤ cols ≤ 1920
- 출력: `u32 rows, u32 cols` + `u8 grad_x[rows][cols]` + `u8 grad_y[rows][cols]` — x·y 기울기 두 장을 이어 붙여서 **입력의 2배 크기**입니다. 화소마다 사용자 함수 `isc_post`가 돌려준 값이 저장되며, 1.1의 `gx`, `gy`와 바이트까지 같습니다
- 사용자 로직 없이(`--logic` 생략) 만들면 기울기가 그대로 출력됩니다(`cv2.Sobel` 결과와 같음)
- 기울기는 8비트 포화(음수는 0, 255 초과는 255)라서 어두움→밝음 경계만 값이 남습니다. 가장자리는 영상 밖을 0으로 채웁니다
- 실제로 따라 할 절차는 2절(개발 절차)과 3절(실행 방법)에 있습니다.

## 2. 개발 절차

모든 명령은 저장소 루트에서 실행합니다. 입력 변환과 결과 확인에 Python `numpy`, `opencv-python`이 필요합니다.

### 2.1. 입력 준비

**사용자가 할 일**: 쓸 카메라 영상을 이 연산의 입력 형식으로 변환합니다.

```bash
# 내 이미지(PNG·JPEG 등) → 회색조 + 헤더
python3 isc/operations/image_sobel/examples/make_input.py --image [이미지 파일] -o datasets/image_sobel/[이름].bin

# 또는 예시 영상(256×256 합성 영상: 컨베이어 위 원형 부품 + 구멍 + 돌출부 + 약한 잡음, 고정 시드)으로 먼저 해 보기
python3 isc/operations/image_sobel/examples/make_input.py             # → datasets/image_sobel/part_cam_256.bin
```

여러 장은 셸 반복문으로 한 장씩 변환합니다(코드 수정 불필요):

```bash
for f in data/line_cam/*.png; do
    python3 isc/operations/image_sobel/examples/make_input.py --image "$f" -o "datasets/image_sobel/$(basename "$f" .png).bin"
done
```

[`examples/make_input.py`](examples/make_input.py) 전체 코드입니다(주석 제외). 이미지를 읽는 방식을 바꿔야 하면 `read_image()`(내 이미지 읽기) 또는 `make_sample()`(예시 영상)만 고치고, `encode()`(입력 형식)와 `main()`은 그대로 둡니다.

```python
import argparse
import struct
from pathlib import Path

import cv2
import numpy as np

DEFAULT_OUTPUT = "datasets/image_sobel/part_cam_256.bin"


def make_sample() -> np.ndarray:
    rng = np.random.default_rng(9); h = w = 256
    a = np.full((h, w), 40, np.uint8)
    cv2.circle(a, (128, 128), 90, 190, -1)
    cv2.circle(a, (128, 128), 30, 40, -1)
    for k in range(6):
        t = k * np.pi / 3; cv2.circle(a, (int(128 + 60 * np.cos(t)), int(128 + 60 * np.sin(t))), 8, 40, -1)
    cv2.rectangle(a, (200, 120), (240, 136), 190, -1)
    return np.clip(a.astype(np.int16) + rng.normal(0, 4, a.shape), 0, 255).astype(np.uint8)


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
    parser = argparse.ArgumentParser(description="image_sobel 입력 파일 만들기")
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

수정 예 — 밝음→어두움 경계도 잡으려고 영상을 반전해 읽기 (8비트 포화라 음의 기울기는 0이 되므로, 원본과 반전 영상을 각각 실행해 결과를 합칩니다. 이 방식은 시험하지 않았습니다. 위 전체 코드에서 `read_image()`만 이것으로 바꿈):

```python
def read_image(path: str) -> np.ndarray:
    image = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise SystemExit(f"영상을 읽지 못했습니다: {path}")
    return 255 - image
```

### 2.2. 모듈 만들기

**사용자가 할 일**: 모듈 이름을 정합니다. 잡음 제거 규칙을 넣으려면 `--logic`을 붙입니다. 이 연산은 파라미터가 없습니다.

```bash
isc new part_outline --operation image_sobel --logic
```

- `modules/part_outline/` 폴더에 `module.yaml`과 사용자 로직 파일 `logic.hpp`가 만들어집니다.
- 로직 파일을 먼저 작성한 뒤 모듈을 만들어도 됩니다. `isc template image_sobel -o denoise.hpp`로 시작 파일을 받아 2.3처럼 고친 다음, `--logic denoise.hpp`로 모듈을 만듭니다.
- 기울기만 필요하면 `--logic`을 빼고 만들고, 2.3은 건너뜁니다.

### 2.3. 로직 작성

**사용자가 할 일**: `modules/part_outline/logic.hpp`를 열어 함수 `isc_post`의 `return` 줄을 잡음 제거 규칙으로 바꾸고, 그 줄의 `ISC-TODO` 주석을 지웁니다.

아래는 "기울기가 60보다 작으면 0으로 지움" 규칙으로 고친 파일 전체입니다.

```cpp
#pragma once

#include <stdint.h>

inline uint8_t isc_post(uint8_t g) {
    return g >= 60 ? g : 0;
}
```

- **호출 시점**: 라이브러리 함수 `xf::cv::Sobel`이 기울기를 계산한 뒤, 기울기 화소마다 한 번씩 호출됩니다. x 기울기 영상과 y 기울기 영상 모두에 같은 함수가 적용됩니다. 함수가 돌려준 값이 그 자리의 출력이 됩니다.
- **입력값**: `g`는 기울기 화소 값(0~255)입니다.
- **바꾸면 안 되는 것**: 함수 이름(`isc_post`), 인자 타입과 반환 타입(`uint8_t`). 인자 이름은 바꿔도 됩니다.
- **쓸 수 있는 것**: 비교·사칙·비트 연산, 상수, 조건식. 보조 함수나 상수를 더 정의해도 됩니다. 예:
  - `return g >= 100 ? 255 : 0;` — 윤곽 이진화
  - `return g > 127 ? 255 : g * 2;` — 약한 윤곽을 두 배로 강조
- **쓸 수 없는 것**: 동적 메모리 할당(`new`, `malloc`), 파일·네트워크 입출력(`fopen`, `open`, `socket`), `#pragma HLS INTERFACE`, 주변 화소 값 참조.
- 파일을 고치지 않고 두면(`return g;`) 원래 연산과 같은 결과, 즉 기울기가 그대로 나옵니다.

### 2.4. 검사

**사용자가 할 일**: 빌드하기 전에 모듈 설정과 로직 파일을 검사합니다. 문제가 없으면 `PASS`가 표시됩니다.

```bash
isc check part_outline
```

- 함수 이름·인자·반환 타입이 다르거나 쓸 수 없는 문법이 있으면 `problem`이 표시되고, 고칠 때까지 빌드와 실행이 진행되지 않습니다.
- `ISC-TODO` 주석이 남아 있으면 `warning`이 표시됩니다. 빌드는 진행됩니다.

## 3. 실행 방법

**사용자가 할 일**:

- `--input`에 2.1의 파일, `--output`에 결과를 저장할 경로를 지정합니다. 결과 확인 명령에도 같은 입력 파일을 `--input`으로 지정합니다(예시 파일이면 생략).
- 결과 확인 스크립트 [`examples/check_output.py`](examples/check_output.py)의 `[바꾸는 곳]`에 있는 `post()`를 2.3과 같은 규칙으로 맞춥니다. 2.3의 예시 규칙을 그대로 썼다면 고칠 것이 없습니다.
- 2.2에서 `--logic` 없이 모듈을 만들었다면, 결과 확인 명령에 `--original`을 붙입니다.

```bash
# 에뮬레이션 실행 (카드 없이, 처음 한 번 수 분 빌드. 빌드가 캐시에 있으면 이 입력은 약 40초)
isc run part_outline --input datasets/image_sobel/part_cam_256.bin --output datasets/image_sobel/part_outline.emu.bin

# 결과 확인: cv2.Sobel 결과에 같은 규칙을 적용한 값과 비교, MATCH가 나오면 정상. 강한 윤곽 화소 수도 출력
python3 isc/operations/image_sobel/examples/check_output.py datasets/image_sobel/part_outline.emu.bin --input datasets/image_sobel/part_cam_256.bin

# FPGA 카드 실행 (hw) (처음 한 번 hw 빌드 약 40분) — 카드 출력이 에뮬레이션 출력과 같은지 플랫폼이 자동 비교
isc run part_outline --input datasets/image_sobel/part_cam_256.bin --output datasets/image_sobel/part_outline.hw.bin --target hw
python3 isc/operations/image_sobel/examples/check_output.py datasets/image_sobel/part_outline.hw.bin --input datasets/image_sobel/part_cam_256.bin --png datasets/image_sobel

# 실행 기록
isc records --module part_outline
isc show [실행 ID]
```

결과 확인 스크립트는 `cv2.Sobel`로 기울기를 계산한 뒤, 아래 `post()` 함수를 화소마다 적용해 FPGA 출력과 비교합니다. `post()`는 2.3의 규칙을 Python으로 옮긴 것이므로, 2.3의 규칙을 바꾸면 이 함수도 함께 바꿉니다.

```python
def post(g: int) -> int:
    return g if g >= 60 else 0
```

- `--png 폴더`를 붙이면 두 기울기를 합친 윤곽 영상을 `edges.png`로 저장합니다.
- **규칙을 바꿀 때**: `logic.hpp`를 고치고 `isc run`을 다시 실행하면, 바뀐 로직으로 자동으로 다시 빌드합니다. 에뮬레이션으로 먼저 확인한 다음 FPGA 카드에서 실행합니다.
- **성능 측정**: 사용자 로직이 들어간 모듈은 `isc bench`에서 CPU 기준선과 비교하지 않습니다. 가속 효과를 측정하려면 `--logic` 없이 만든 모듈을 씁니다.
- 잡음이 많은 영상은 먼저 [`image_gaussian`](../image_gaussian/README.md)으로 잡음을 줄인 뒤 이 연산을 실행합니다(두 번 따로 실행).
- **다른 영상으로 실행할 때**: 2.1처럼 변환한 뒤 `--input`·`--output`만 바꿔 실행합니다. 로직이 같으면 다시 빌드하지 않습니다.
- 터미널 출력을 파일로도 남기려면 명령 뒤에 `2>&1 | tee [로그 파일]`을 붙입니다.
