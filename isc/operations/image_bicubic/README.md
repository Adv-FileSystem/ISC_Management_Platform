# `image_bicubic` — 학습용 흑백 이미지를 모델 입력 크기로 일괄 축소 (연산 선택형)

| 개발 방식 | 사용하는 라이브러리 함수 | 검증 상태 |
|---|---|---|
| 연산 선택형 | Vitis Codec L2 `xf::codec::resizeTop` (`kernel/bicubic_kernel.cpp`, 공식 데모 커널과 같은 본문) | 플랫폼 자체 테스트 ✅ · 공식 라이브러리 테스트 ✅ (검증 장치 AMD Alveo U50 · [검증 기록](../../../verification/u50_vitis_libraries/results/image_bicubic.md)) |

## 1. 연산 예시

AI 모델 개발자가 제조 결함 사진·문서 스캔·X-ray처럼 흑백 이미지로 된 학습 데이터셋을 모델 입력 크기(예: 256×256)로 일괄 축소합니다. 이 축소를 CPU의 OpenCV 대신 FPGA의 Vitis Codec 라이브러리로 합니다.

### 1.1. 전처리 코드 안에서 직접 축소하는 기존 방식

```python
import cv2

img = cv2.imread("defect_0001.png", cv2.IMREAD_GRAYSCALE)
small = cv2.resize(img, (256, 256), interpolation=cv2.INTER_CUBIC)
cv2.imwrite("defect_0001_256.png", small)
```

### 1.2. ISC 전주기 시스템 기반의 방식

```bash
python3 isc/operations/image_bicubic/examples/make_input.py --image defect_0001.png -o defect_0001.bin   # 이미지 → 연산 입력 형식
isc new train_resize --operation image_bicubic --set out_rows=256 --set out_cols=256                  # 처음 한 번: 모듈 만들기 (출력 크기 지정)
isc run train_resize --input defect_0001.bin --output defect_0001_256.bin --target hw                  # FPGA가 축소
```

- 입력: 8비트 회색조 이미지 1장 (헤더 `u32 rows, u32 cols` + 화소). 한 변 1~8192 화소, 1채널만
- 출력: 같은 형식으로 줄인 이미지. **축소만** 됨. PNG로 보려면 3절 결과 확인의 `--png`를 씀
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

**사용자가 할 일**: 모듈 이름과 출력 크기(모델 입력 크기)를 정합니다.

```bash
isc new train_resize --operation image_bicubic --set out_rows=256 --set out_cols=256
```

- 출력 크기는 나중에 `modules/train_resize/module.yaml`의 `out_rows`·`out_cols`로도 바꿀 수 있고, 바꿔도 다시 빌드하지 않습니다.
- 출력 크기는 입력보다 크지 않아야 합니다(1~8192).

### 2.3. 로직 작성

**사용자가 할 일**: 현재 없음. 추가 예정: `isc_post(px)` — 축소가 끝난 화소마다 호출되는 함수(예: 밝기 정규화, 이진화). 추가되면 이 단계에서 작성합니다.

### 2.4. 검사

**사용자가 할 일**: 빌드하기 전에 모듈 설정을 검사합니다. 문제가 없으면 `PASS`가 표시됩니다.

```bash
isc check train_resize
```

## 3. 실행 방법

**사용자가 할 일**: `--input`에 2.1의 파일, `--output`에 결과를 저장할 경로를 지정합니다. 결과 확인의 C 시뮬레이션 명령에는 입력·출력 크기(`512 512 256 256` 자리)를 실제 크기로 맞춥니다.

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

# 결과 확인 2: FPGA 출력 = C 시뮬레이션이면 MATCH (정상). OpenCV와의 차이는 참고로 출력
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

- `--png 폴더`를 붙이면 입력·FPGA 결과·OpenCV 결과를 `input.png`, `fpga.png`, `opencv.png`로 저장합니다.
- 다른 이미지는 2.1처럼 변환한 뒤 `--input`·`--output`만 바꿔 다시 실행합니다. 모듈은 그대로 재사용하고 다시 빌드하지 않습니다.
- 터미널 출력을 파일로도 남기려면 명령 뒤에 `2>&1 | tee [로그 파일]`을 붙입니다.
