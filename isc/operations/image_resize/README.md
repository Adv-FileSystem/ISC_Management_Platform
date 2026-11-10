# `image_resize` — 학습 데이터 이미지를 모델 입력 크기로 일괄 축소 (연산 선택형)

| 개발 방식 | 사용하는 라이브러리 함수 | 검증 상태 |
|---|---|---|
| 연산 선택형 | Vitis Vision L1 `xf::cv::resize` bilinear (`kernel/resize_kernel.cpp`) | 플랫폼 자체 테스트 ✅ · 공식 라이브러리 테스트 미실행 (공식 테스트가 검증 장치를 지원하지 않음·OpenCV C++ 필요) (검증 장치 AMD Alveo U50 · [검증 기록](../../../verification/u50_vitis_libraries/results/image_resize.md)) |

## 1. 연산 예시

AI 모델 개발자가 학습 데이터 이미지를 모델 입력 크기(예: 1920×1080 → 960×540)로 일괄 축소합니다. 이 bilinear 축소를 CPU 대신 FPGA에서 합니다.

### 1.1. 전처리 코드 안에서 직접 축소하는 기존 방식

```python
import cv2

img = cv2.imread("frame_0001.png", cv2.IMREAD_GRAYSCALE)
small = cv2.resize(img, (960, 540), interpolation=cv2.INTER_LINEAR)
cv2.imwrite("frame_0001_960.png", small)
```

### 1.2. ISC 전주기 시스템 기반의 방식

```bash
python3 isc/operations/image_resize/examples/make_input.py --image frame_0001.png -o frame_0001.bin   # 이미지 → 연산 입력 형식
isc new train_input --operation image_resize --set out_rows=540 --set out_cols=960                  # 처음 한 번: 모듈 만들기 (출력 크기 지정)
isc run train_input --input frame_0001.bin --output frame_0001_960.bin --target hw --no-validate      # FPGA가 축소
```

- 입력: 헤더 `u32 rows, u32 cols` + `u8 pixels[rows][cols]` (8비트 회색조 1채널, little-endian). 2.1의 스크립트가 이미지에서 만들어 줍니다. 입력 최대 3840×2160
- 출력: 같은 형식으로 줄인 이미지. 출력 최대 1920×1080. **한 번에 2배까지만 줄일 수 있습니다**(`rows ≤ 2·out_rows`, `cols ≤ 2·out_cols`)
- 장치는 고정소수점 보간이라 1.1의 OpenCV 결과와 화소 값이 조금 다를 수 있습니다. 차이 5 이하면 정상입니다
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

**사용자가 할 일**: 모듈 이름과 출력 크기(`out_rows` 기본 540·최대 1080, `out_cols` 기본 960·최대 1920)를 정합니다. 출력 크기마다 모듈을 따로 만들 수 있고, 같은 빌드를 함께 씁니다.

```bash
isc new train_preview --operation image_resize --set out_rows=108 --set out_cols=192   # 작은 예시 확인용 (384×216 → 192×108)
isc new train_input   --operation image_resize --set out_rows=540 --set out_cols=960   # 원래 크기 (1920×1080 → 960×540)
```

- 출력 크기는 나중에 `modules/[모듈 이름]/module.yaml`의 `out_rows`·`out_cols`로도 바꿀 수 있고, 바꿔도 다시 빌드하지 않습니다.
- 2배보다 크게 줄이려면(예: 3840×2160 → 960×540) 두 번 나눠 실행합니다(3840×2160 → 1920×1080 → 960×540). 이 방법과 확대는 시험하지 않았습니다.

### 2.3. 로직 작성

**사용자가 할 일**: 현재 없음. 추가 예정: 축소 앞·뒤 화소마다 호출되는 함수 `isc_pre(px)`·`isc_post(px)`(예: 축소 후 밝기 정규화). 추가되면 이 단계에서 작성합니다.

### 2.4. 검사

**사용자가 할 일**: 빌드하기 전에 모듈 설정을 검사합니다. 문제가 없으면 `PASS`가 표시됩니다.

```bash
isc check train_preview
isc check train_input
```

## 3. 실행 방법

**사용자가 할 일**: `--input`에 2.1의 파일, `--output`에 결과를 저장할 경로를 지정합니다. 결과 확인 명령에도 같은 입력 파일을 `--input`으로 지정합니다(출력 크기는 출력 파일에서 읽음). 큰 영상은 `--no-validate`를 붙이고 결과 확인을 반드시 합니다.

```bash
# 에뮬레이션 실행: 작은 영상만 (처음 한 번 수 분 빌드. 빌드가 캐시에 있으면 약 80초)
isc run train_preview --input datasets/image_resize/frame_384x216.bin --output datasets/image_resize/train_preview.emu.bin

# 결과 확인: 지금 쓰는 CPU 코드(cv2.resize INTER_LINEAR)와 화소마다 비교 — 차이 5 이하면 PASS
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

- `--png 폴더`를 붙이면 FPGA 결과를 PNG로 저장합니다.
- 다른 이미지는 2.1처럼 변환한 뒤 `--input`·`--output`만 바꿔 다시 실행합니다. 모듈은 그대로 재사용하고 다시 빌드하지 않습니다.
- 컬러 이미지를 채널별로 세 번 실행하는 방식은 시험하지 않았습니다. 고품질 축소(bicubic)가 필요하면 [`image_bicubic`](../image_bicubic/README.md)을 씁니다.
- 터미널 출력을 파일로도 남기려면 명령 뒤에 `2>&1 | tee [로그 파일]`을 붙입니다.
