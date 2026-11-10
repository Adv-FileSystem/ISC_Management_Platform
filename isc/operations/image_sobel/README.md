# `image_sobel` — 제조 라인 영상에서 부품 윤곽 추출 (연산 선택형)

| 개발 방식 | 사용하는 라이브러리 함수 | 검증 상태 |
|---|---|---|
| 연산 선택형 | Vitis Vision L1 `xf::cv::Sobel` 3×3 (`kernel/sobel_kernel.cpp`) | 플랫폼 자체 테스트 ✅ · 공식 라이브러리 테스트 미실행 (공식 테스트가 검증 장치를 지원하지 않음·OpenCV C++ 필요) (검증 장치 AMD Alveo U50 · [검증 기록](../../../verification/u50_vitis_libraries/results/image_sobel.md)) |

## 1. 연산 예시

제조 라인 비전 검사 시스템 개발자가 카메라 영상에서 부품 윤곽(에지)을 뽑아 불량 검사의 전처리로 씁니다. 이 x·y 방향 기울기 계산을 CPU 대신 FPGA에서 합니다.

### 1.1. 검사 전처리 코드 안에서 직접 계산하는 기존 방식

```python
import cv2
import numpy as np

img = cv2.imread("part_0001.png", cv2.IMREAD_GRAYSCALE)
gx = cv2.Sobel(img, cv2.CV_8U, 1, 0, ksize=3, borderType=cv2.BORDER_CONSTANT)
gy = cv2.Sobel(img, cv2.CV_8U, 0, 1, ksize=3, borderType=cv2.BORDER_CONSTANT)
edges = np.maximum(gx, gy)
```

### 1.2. ISC 전주기 시스템 기반의 방식

```bash
python3 isc/operations/image_sobel/examples/make_input.py --image part_0001.png -o part_0001.bin   # 이미지 → 연산 입력 형식
isc new part_outline --operation image_sobel                                                     # 처음 한 번: 모듈 만들기
isc run part_outline --input part_0001.bin --output part_0001.grad.bin --target hw               # FPGA가 x·y 기울기 계산
```

- 입력: 헤더 `u32 rows, u32 cols` + `u8 pixels[rows][cols]` (8비트 회색조 1채널, little-endian). 2.1의 스크립트가 이미지에서 만들어 줍니다. 1 ≤ rows ≤ 1080, 1 ≤ cols ≤ 1920
- 출력: `u32 rows, u32 cols` + `u8 grad_x[rows][cols]` + `u8 grad_y[rows][cols]` — x·y 기울기 두 장을 이어 붙여서 **입력의 2배 크기**입니다. 1.1의 `gx`, `gy`와 바이트까지 같습니다
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

**사용자가 할 일**: 모듈 이름을 정합니다. 이 연산은 파라미터가 없습니다.

```bash
isc new part_outline --operation image_sobel
```

`modules/part_outline/module.yaml`이 만들어지며 고칠 것은 없습니다.

### 2.3. 로직 작성

**사용자가 할 일**: 현재 없음. 추가 예정: 화소마다 x·y 기울기를 받아 윤곽 값을 돌려주는 함수 `isc_edge(gx, gy)`(예: 두 기울기를 합쳐 임계값 이상이면 255로 이진화). 추가되면 이 단계에서 작성하고, 그때 출력은 두 장이 아니라 윤곽 영상 한 장이 됩니다.

### 2.4. 검사

**사용자가 할 일**: 빌드하기 전에 모듈 설정을 검사합니다. 문제가 없으면 `PASS`가 표시됩니다.

```bash
isc check part_outline
```

## 3. 실행 방법

**사용자가 할 일**: `--input`에 2.1의 파일, `--output`에 결과를 저장할 경로를 지정합니다. 결과 확인 명령에도 같은 입력 파일을 `--input`으로 지정합니다(예시 파일이면 생략).

```bash
# 에뮬레이션 실행 (카드 없이, 처음 한 번 수 분 빌드. 빌드가 캐시에 있으면 이 입력은 약 40초)
isc run part_outline --input datasets/image_sobel/part_cam_256.bin --output datasets/image_sobel/part_outline.emu.bin

# 결과 확인: 지금 쓰는 CPU 코드(cv2.Sobel)와 바이트 비교 — MATCH면 정상, 강한 윤곽 화소 수도 출력
python3 isc/operations/image_sobel/examples/check_output.py datasets/image_sobel/part_outline.emu.bin --input datasets/image_sobel/part_cam_256.bin

# FPGA 카드 실행 (hw) (처음 한 번 hw 빌드 약 40분) — 카드 출력이 에뮬레이션 출력과 같은지 플랫폼이 자동 비교
isc run part_outline --input datasets/image_sobel/part_cam_256.bin --output datasets/image_sobel/part_outline.hw.bin --target hw
python3 isc/operations/image_sobel/examples/check_output.py datasets/image_sobel/part_outline.hw.bin --input datasets/image_sobel/part_cam_256.bin --png datasets/image_sobel

# 실행 기록
isc records --module part_outline
isc show [실행 ID]
```

- `--png 폴더`를 붙이면 두 기울기를 합친 윤곽 영상을 `edges.png`로 저장합니다.
- 잡음이 많은 영상은 먼저 [`image_gaussian`](../image_gaussian/README.md)으로 잡음을 줄인 뒤 이 연산을 실행합니다(두 번 따로 실행).
- 다른 영상은 2.1처럼 변환한 뒤 `--input`·`--output`만 바꿔 다시 실행합니다. 모듈은 그대로 재사용하고 다시 빌드하지 않습니다.
- 터미널 출력을 파일로도 남기려면 명령 뒤에 `2>&1 | tee [로그 파일]`을 붙입니다.
