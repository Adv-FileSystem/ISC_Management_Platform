# `image_bicubic` — 학습용 흑백 이미지를 모델 입력 크기로 축소하고 대비 보정 (연산 선택형 + 사용자 로직)

| 개발 방식 | 사용하는 라이브러리 함수 | 검증 상태 |
|---|---|---|
| 연산 선택형 + 사용자 로직 `isc_post` | Vitis Codec L2 `xf::codec::resizeTop` (`kernel/bicubic_kernel.cpp`). 축소가 끝난 뒤 화소마다 사용자 함수를 실행 | 플랫폼 자체 테스트 ✅ (1.0 사용자 로직 없음: hw_emu·hw / 1.1 사용자 로직: hw_emu·hw) · 공식 라이브러리 테스트 ✅ (검증 장치 AMD Alveo U50 · [검증 기록](../../../verification/u50_vitis_libraries/results/image_bicubic.md)) |

## 1. 연산 예시

AI 모델 개발자가 제조 결함 사진·문서 스캔·X-ray처럼 흑백 이미지로 된 학습 데이터셋을 모델 입력 크기(예: 256×256)로 줄입니다. 이런 이미지는 촬영 장비에 따라 밝기 범위가 좁아 흐릿하게 보이는 경우가 많아서, 줄인 뒤 명암 범위를 0~255로 넓히는 대비 보정을 함께 합니다.

축소는 Vitis Codec 라이브러리가 하고, 대비 보정 규칙은 사용자가 C++ 함수 하나로 작성합니다. 두 단계 모두 FPGA에서 실행됩니다.

### 1.1. 전처리 코드 안에서 직접 축소하는 기존 방식

```python
import cv2
import numpy as np

img = cv2.imread("defect_0001.png", cv2.IMREAD_GRAYSCALE)
small = cv2.resize(img, (256, 256), interpolation=cv2.INTER_CUBIC)
lut = [0 if v <= 30 else 255 if v >= 220 else (v - 30) * 255 // 190 for v in range(256)]
small = cv2.LUT(small, np.array(lut, np.uint8))
cv2.imwrite("defect_0001_256.png", small)
```

### 1.2. ISC 전주기 시스템 기반의 방식

```bash
python3 isc/operations/image_bicubic/examples/make_input.py --image defect_0001.png -o defect_0001.bin   # 이미지 → 연산 입력 형식
isc new train_resize --operation image_bicubic --set out_rows=256 --set out_cols=256 --logic          # 처음 한 번: 모듈과 사용자 로직 파일(logic.hpp) 만들기
# modules/train_resize/logic.hpp 의 return 줄을 대비 보정 규칙으로 수정
isc check train_resize                                                                                 # 사용자 로직 검사
isc run train_resize --input defect_0001.bin --output defect_0001_256.bin --target hw                  # FPGA가 축소
```

- 입력: 8비트 회색조 이미지 1장 (헤더 `u32 rows, u32 cols` + 화소). 한 변 1~8192 화소, 1채널만
- 출력: 같은 형식으로 줄인 이미지. 화소마다 사용자 함수 `isc_post`가 돌려준 값이 저장됩니다. **축소만** 됨. PNG로 보려면 3절 결과 확인의 `--png`를 씀
- 사용자 로직 없이(`--logic` 생략) 만들면 줄인 영상이 그대로 출력됩니다.
- 크기는 같게 줄지만 **화소 값은 1.1의 OpenCV 결과와 조금 다릅니다** (bicubic 정의가 다름. 예시 입력에서 평균 차이 2.71). 학습과 추론 모두 같은 방식으로 줄여야 합니다
- 실제로 따라 할 절차는 2절(개발 절차)과 3절(실행 방법)에 있습니다.

## 2. 개발 절차

모든 명령은 저장소 루트에서 실행합니다. 입력 변환과 결과 확인에 Python `numpy`, `opencv-python`이 필요합니다.

### 2.1. 입력 준비

**사용자가 할 일**: 쓸 이미지를 이 연산의 입력 형식으로 변환합니다.

```bash
# 내 이미지(PNG·JPEG 등) → 회색조 + 헤더
python3 isc/operations/image_bicubic/examples/make_input.py --image [이미지 파일] -o datasets/image_bicubic/[이름].bin

# 또는 예시 영상(512×512 합성 영상, 고정 시드)으로 먼저 해 보기
python3 isc/operations/image_bicubic/examples/make_input.py           # → datasets/image_bicubic/train_512.bin
```

여러 장은 셸 반복문으로 한 장씩 변환합니다(코드 수정 불필요):

```bash
for f in data/train/*.png; do
    python3 isc/operations/image_bicubic/examples/make_input.py --image "$f" -o "datasets/image_bicubic/$(basename "$f" .png).bin"
done
```

[`examples/make_input.py`](examples/make_input.py) 전체 코드입니다(주석 제외). 이미지를 읽는 방식을 바꿔야 하면 `read_image()`(내 이미지 읽기) 또는 `make_sample()`(예시 영상)만 고치고, `encode()`(입력 형식)와 `main()`은 그대로 둡니다.

```python
import argparse
import struct
from pathlib import Path

import cv2
import numpy as np

DEFAULT_OUTPUT = "datasets/image_bicubic/train_512.bin"


def make_sample() -> np.ndarray:
    rng = np.random.default_rng(7); h = w = 512
    a = np.full((h, w), 245.0)
    cv2.ellipse(a, (256, 280), (150, 110), 0, 0, 360, 90, -1)
    cv2.rectangle(a, (200, 120), (312, 190), 140, -1)
    cv2.putText(a, "ISC", (190, 300), cv2.FONT_HERSHEY_SIMPLEX, 2.5, 230, 6)
    return np.clip(a + rng.normal(0, 3, a.shape), 0, 255).astype(np.uint8)


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
    parser = argparse.ArgumentParser(description="image_bicubic 입력 파일 만들기")
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

수정 예 — 16비트 X-ray 이미지를 8비트로 바꿔 읽기 (이 연산은 8비트 1채널만 받음. 위 전체 코드에서 `read_image()`만 이것으로 바꿈):

```python
def read_image(path: str) -> np.ndarray:
    image = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if image is None:
        raise SystemExit(f"영상을 읽지 못했습니다: {path}")
    if image.ndim == 3:
        image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    if image.dtype == np.uint16:
        image = (image >> 8).astype(np.uint8)
    return image
```

### 2.2. 모듈 만들기

**사용자가 할 일**: 모듈 이름과 출력 크기(모델 입력 크기)를 정합니다. 대비 보정 규칙을 넣으려면 `--logic`을 붙입니다.

```bash
isc new train_resize --operation image_bicubic --set out_rows=256 --set out_cols=256 --logic
```

- `modules/train_resize/` 폴더에 `module.yaml`과 사용자 로직 파일 `logic.hpp`가 만들어집니다.
- 로직 파일을 먼저 작성한 뒤 모듈을 만들어도 됩니다. `isc template image_bicubic -o contrast.hpp`로 시작 파일을 받아 2.3처럼 고친 다음, `--logic contrast.hpp`로 모듈을 만듭니다.
- 축소만 필요하면 `--logic`을 빼고 만들고, 2.3은 건너뜁니다.

- 출력 크기는 나중에 `modules/train_resize/module.yaml`의 `out_rows`·`out_cols`로도 바꿀 수 있고, 바꿔도 다시 빌드하지 않습니다.
- 출력 크기는 입력보다 크지 않아야 합니다(1~8192).

### 2.3. 로직 작성

**사용자가 할 일**: `modules/train_resize/logic.hpp`를 열어 함수 `isc_post`의 `return` 줄을 대비 보정 규칙으로 바꾸고, 그 줄의 `ISC-TODO` 주석을 지웁니다.

아래는 "30 이하는 0, 220 이상은 255, 그 사이(31~219)는 1~253으로 고르게 늘림" 규칙으로 고친 파일 전체입니다.

```cpp
#pragma once

#include <stdint.h>

inline uint8_t isc_post(uint8_t px) {
    return px <= 30 ? 0 : px >= 220 ? 255 : (px - 30) * 255 / 190;
}
```

- **호출 시점**: 라이브러리 함수 `xf::codec::resizeTop`이 영상을 다 줄인 뒤, 출력 화소마다 한 번씩 호출됩니다. 함수가 돌려준 값이 그 자리의 출력 화소가 됩니다.
- **입력값**: `px`는 줄인 영상의 화소 값(0~255)입니다.
- **바꾸면 안 되는 것**: 함수 이름(`isc_post`), 인자 타입과 반환 타입(`uint8_t`). 인자 이름은 바꿔도 됩니다.
- **쓸 수 있는 것**: 비교·사칙·비트 연산, 상수, 조건식. 보조 함수나 상수를 더 정의해도 됩니다. 예:
  - `return px >= 128 ? 255 : 0;` — 흑백 이진화
  - `return 255 - px;` — 명암 반전
- **쓸 수 없는 것**: 동적 메모리 할당(`new`, `malloc`), 파일·네트워크 입출력(`fopen`, `open`, `socket`), `#pragma HLS INTERFACE`, 주변 화소 값 참조.
- 파일을 고치지 않고 두면(`return px;`) 원래 연산과 같은 결과, 즉 줄인 영상이 그대로 나옵니다.

### 2.4. 검사

**사용자가 할 일**: 빌드하기 전에 모듈 설정과 로직 파일을 검사합니다. 문제가 없으면 `PASS`가 표시됩니다.

```bash
isc check train_resize
```

- 함수 이름·인자·반환 타입이 다르거나 쓸 수 없는 문법이 있으면 `problem`이 표시되고, 고칠 때까지 빌드와 실행이 진행되지 않습니다.
- `ISC-TODO` 주석이 남아 있으면 `warning`이 표시됩니다. 빌드는 진행됩니다.

## 3. 실행 방법

**사용자가 할 일**:

- `--input`에 2.1의 파일, `--output`에 결과를 저장할 경로를 지정합니다.
- 결과 확인의 C 시뮬레이션 명령에는 입력·출력 크기(`512 512 256 256` 자리)를 실제 크기로 맞춥니다.
- 결과 확인 스크립트 [`examples/check_output.py`](examples/check_output.py)의 `[바꾸는 곳]`에 있는 `post()`를 2.3과 같은 규칙으로 맞춥니다. 2.3의 예시 규칙을 그대로 썼다면 고칠 것이 없습니다.
- 2.2에서 `--logic` 없이 모듈을 만들었다면, 결과 확인 명령에 `--original`을 붙입니다.

```bash
# 에뮬레이션 실행 (카드 없이, 처음 한 번 수 분 빌드)
isc run train_resize --input datasets/image_bicubic/train_512.bin --output datasets/image_bicubic/train_256.emu.bin

# 결과 확인 1: 정답 기준 만들기 — 라이브러리 커널 소스를 CPU용으로 컴파일해 같은 입력으로 실행
source [Vitis 설치 경로]/settings64.sh
V=$VITIS_LIBRARIES/codec
g++ -O2 -std=c++14 -w -I$XILINX_HLS/include -I$V/L2/include/hw/resize -I$V/L2/demos/resize/kernel \
    verification/u50_vitis_libraries/tools/csim/codec_resize_tb.cpp $V/L2/demos/resize/kernel/kernel_resize.cpp \
    -o datasets/image_bicubic/codec_resize_tb
tail -c +9 datasets/image_bicubic/train_512.bin > datasets/image_bicubic/train_512.raw
datasets/image_bicubic/codec_resize_tb datasets/image_bicubic/train_512.raw datasets/image_bicubic/train_256.csim.raw 512 512 256 256

# 결과 확인 2: FPGA 출력 = C 시뮬레이션 결과에 post()를 적용한 값이면 MATCH (정상). OpenCV와의 차이는 참고로 출력
python3 isc/operations/image_bicubic/examples/check_output.py datasets/image_bicubic/train_256.emu.bin \
    --input datasets/image_bicubic/train_512.bin --csim datasets/image_bicubic/train_256.csim.raw

# FPGA 카드 실행 (hw) (처음 한 번 hw 빌드 약 48분) — 카드 출력이 에뮬레이션 출력과 같은지 플랫폼이 자동 비교
isc run train_resize --input datasets/image_bicubic/train_512.bin --output datasets/image_bicubic/train_256.hw.bin --target hw
python3 isc/operations/image_bicubic/examples/check_output.py datasets/image_bicubic/train_256.hw.bin \
    --input datasets/image_bicubic/train_512.bin --csim datasets/image_bicubic/train_256.csim.raw --png datasets/image_bicubic

# 실행 기록
isc records --module train_resize
isc show [실행 ID]
```

결과 확인 스크립트는 C 시뮬레이션 결과에 아래 `post()` 함수를 적용해 FPGA 출력과 비교합니다. `post()`는 2.3의 규칙을 Python으로 옮긴 것이므로, 2.3의 규칙을 바꾸면 이 함수도 함께 바꿉니다.

```python
def post(px: int) -> int:
    return 0 if px <= 30 else 255 if px >= 220 else (px - 30) * 255 // 190
```

- `--png 폴더`를 붙이면 입력·FPGA 결과·OpenCV 결과를 `input.png`, `fpga.png`, `opencv.png`로 저장합니다.
- (2)의 OpenCV 비교는 1.1처럼 대비 보정 전의 `cv2.resize` 결과와 비교하므로, 사용자 로직이 있으면 차이가 크게 나옵니다. 정상 여부는 (1)로 판정합니다.
- **규칙을 바꿀 때**: `logic.hpp`를 고치고 `isc run`을 다시 실행하면, 바뀐 로직으로 자동으로 다시 빌드합니다. 에뮬레이션으로 먼저 확인한 다음 FPGA 카드에서 실행합니다.
- **다른 이미지로 실행할 때**: 2.1처럼 변환한 뒤 `--input`·`--output`만 바꿔 실행합니다. 로직이 같으면 다시 빌드하지 않습니다.
- 터미널 출력을 파일로도 남기려면 명령 뒤에 `2>&1 | tee [로그 파일]`을 붙입니다.
