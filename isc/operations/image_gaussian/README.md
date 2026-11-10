# `image_gaussian` — 문서 스캔 이미지 잡음 제거 (연산 선택형)

| 개발 방식 | 사용하는 라이브러리 함수 | 검증 상태 |
|---|---|---|
| 연산 선택형 | Vitis Vision L1 `xf::cv::GaussianBlur` 3×3 (`kernel/gaussian_kernel.cpp`) | 플랫폼 자체 테스트 ✅ · 공식 라이브러리 테스트 미실행 (공식 테스트가 검증 장치를 지원하지 않음·OpenCV C++ 필요) (검증 장치 AMD Alveo U50 · [검증 기록](../../../verification/u50_vitis_libraries/results/image_gaussian.md)) |

## 1. 연산 예시

문서 관리·OCR 서비스 개발자가 스캐너로 들어온 문서 이미지의 잡음을 줄여 OCR 정확도를 높입니다. 이 3×3 Gaussian 흐림을 CPU 대신 FPGA에서 합니다.

### 1.1. OCR 전처리 코드 안에서 직접 흐리게 하는 기존 방식

```python
import cv2

img = cv2.imread("scan_0001.png", cv2.IMREAD_GRAYSCALE)
clean = cv2.GaussianBlur(img, (3, 3), 0.5, borderType=cv2.BORDER_CONSTANT)
text = ocr(clean)
```

### 1.2. ISC 전주기 시스템 기반의 방식

```bash
python3 isc/operations/image_gaussian/examples/make_input.py --image scan_0001.png -o scan_0001.bin   # 이미지 → 연산 입력 형식
isc new ocr_denoise --operation image_gaussian --set sigma_milli=500                                # 처음 한 번: 모듈 만들기 (σ 0.5)
isc run ocr_denoise --input scan_0001.bin --output scan_0001.clean.bin --target hw                   # FPGA가 흐림
```

- 입력: 헤더 `u32 rows, u32 cols` + `u8 pixels[rows][cols]` (8비트 회색조 1채널, little-endian). 2.1의 스크립트가 이미지에서 만들어 줍니다. 1 ≤ rows ≤ 1080, 1 ≤ cols ≤ 1920
- 출력: 입력과 같은 형식과 크기
- 가장자리는 영상 밖을 0으로 채웁니다(`BORDER_CONSTANT`). 테두리 1화소는 어두워지고, OpenCV 기본값(`BORDER_REFLECT_101`)과는 테두리가 다릅니다
- 장치는 고정소수점 가중치를 써서 1.1의 OpenCV 결과와 화소 값이 조금 다를 수 있습니다. 차이 2 이하면 정상입니다
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

**사용자가 할 일**: 모듈 이름과 흐림 정도(`sigma_milli` = σ × 1000, 기본 500 = σ 0.5, 100~10000)를 정합니다.

```bash
isc new ocr_denoise --operation image_gaussian --set sigma_milli=500
```

- 값은 나중에 `modules/ocr_denoise/module.yaml`의 `sigma_milli`로도 바꿀 수 있고, 바꿔도 다시 빌드하지 않습니다.
- σ 0.5 외의 값은 시험하지 않았습니다.

### 2.3. 로직 작성

**사용자가 할 일**: 현재 없음. 추가 예정: 흐림 앞·뒤 화소마다 호출되는 함수 `isc_pre(px)`·`isc_post(px)`(예: 흐린 뒤 임계값으로 흑백 이진화). 추가되면 이 단계에서 작성합니다.

### 2.4. 검사

**사용자가 할 일**: 빌드하기 전에 모듈 설정을 검사합니다. 문제가 없으면 `PASS`가 표시됩니다.

```bash
isc check ocr_denoise
```

## 3. 실행 방법

**사용자가 할 일**: `--input`에 2.1의 파일, `--output`에 결과를 저장할 경로를 지정합니다. 결과 확인 명령에도 같은 입력 파일(`--input`)과 2.2의 `sigma_milli` 값(`--sigma-milli`)을 지정합니다(예시 파일과 기본값이면 생략).

```bash
# 에뮬레이션 실행 (카드 없이, 처음 한 번 수 분 빌드. 빌드가 캐시에 있으면 이 입력은 약 40초)
isc run ocr_denoise --input datasets/image_gaussian/ocr_scan_256.bin --output datasets/image_gaussian/ocr_denoise.emu.bin

# 결과 확인: 지금 쓰는 CPU 코드(cv2.GaussianBlur)와 화소마다 비교 — 차이 2 이하면 PASS
python3 isc/operations/image_gaussian/examples/check_output.py datasets/image_gaussian/ocr_denoise.emu.bin --input datasets/image_gaussian/ocr_scan_256.bin --sigma-milli 500

# FPGA 카드 실행 (hw) (처음 한 번 hw 빌드 약 43분) — 카드 출력이 에뮬레이션 출력과 같은지 플랫폼이 자동 비교
isc run ocr_denoise --input datasets/image_gaussian/ocr_scan_256.bin --output datasets/image_gaussian/ocr_denoise.hw.bin --target hw
python3 isc/operations/image_gaussian/examples/check_output.py datasets/image_gaussian/ocr_denoise.hw.bin --input datasets/image_gaussian/ocr_scan_256.bin --sigma-milli 500 --png datasets/image_gaussian

# 실행 기록
isc records --module ocr_denoise
isc show [실행 ID]
```

- `--png 폴더`를 붙이면 입력과 FPGA 결과를 `input.png`, `fpga.png`로 저장합니다.
- 다른 이미지는 2.1처럼 변환한 뒤 `--input`·`--output`만 바꿔 다시 실행합니다. 모듈은 그대로 재사용하고 다시 빌드하지 않습니다.
- 큰 이미지를 나눠 처리할 때는 나눈 경계의 1화소가 0 채움이 되는 점에 주의합니다. 컬러 이미지를 채널별로 세 번 실행하는 방식은 시험하지 않았습니다.
- 터미널 출력을 파일로도 남기려면 명령 뒤에 `2>&1 | tee [로그 파일]`을 붙입니다.
