# `image_bicubic` — 쇼핑몰 상품 사진으로 고품질 썸네일 만들기 (연산 선택형)

| 개발 방식 | 사용자가 작성하는 것 | 커널 | 파라미터 | 플랫폼 자체 테스트 | 공식 라이브러리 테스트 |
|---|---|---|---|---|---|
| 연산 선택형 | 없음 (`isc new` 한 줄 + 출력 크기) | Vitis Codec L2 `xf::codec::resizeTop` (`kernel/bicubic_kernel.cpp`, 공식 데모 커널과 같은 본문) | `out_rows`, `out_cols` | ✅ 통과 (2026-11-05) | ✅ 통과 (2026-11-05) |

> 이 연산은 시나리오 검증용 **프로토타입**입니다. 공식 데모 커널을 그대로 감쌌고, 커널 이름만 다릅니다.

## 1. 시나리오

| 항목 | 내용 |
|---|---|
| 사용자 | 쇼핑몰의 상품 이미지 처리 담당 개발자 |
| 상황 | 쇼핑몰이 상품 원본 사진에서 고품질 썸네일(예: 256×256)을 생성한다 |
| 가속하는 단계 | 고품질 축소 (bicubic) |
| 입력 → 출력 | 8비트 영상 → 축소 영상 |
| 완료 기준 | 같은 커널의 CPU C 시뮬레이션과 바이트 일치 |

## 2. 연산 요약

| 항목 | 내용 |
|---|---|
| 입력 | 리틀 엔디안 헤더 `u32 rows, u32 cols` + `u8 pixels[rows][cols]` (8비트 회색조 1채널) |
| 출력 | 같은 형식: `u32 out_rows, u32 out_cols` + `u8 pixels[out_rows][out_cols]` |
| 파라미터 | `out_rows`, `out_cols` (기본 256, 1~8192). **축소만** 됨: 출력은 입력보다 크지 않아야 함 |
| 크기 한계 | 한 변 1~8192 화소 (공식 커널 `MAX_SRC`·`MAX_DST` 기준) |
| CPU 참조 | 공식 커널 소스를 CPU에서 그대로 컴파일한 C 시뮬레이션 (`verification/u50_vitis_libraries/tools/csim/codec_resize_tb.cpp`) |
| OpenCV와의 관계 | OpenCV `INTER_CUBIC`과는 좌표 정렬·가중치 정의가 달라 **바이트가 같지 않습니다** (이 문서 입력에서 평균 차이 2.71, 최대 138). 그래서 비교 기준으로 쓰지 않습니다 |
| 메모리 | 포트 3개를 HBM 뱅크 0~2에 연결 (`operation.yaml`). 화소는 64비트 워드에 8개씩 묶어 보냄 |

## 3. 준비

- 공통 설치와 환경 확인: [README 1절](../../../README.md#1-설치-및-환경-구성)
- 빌드에 **`VITIS_LIBRARIES`** 가 필요합니다: `export VITIS_LIBRARIES=[Vitis Libraries 2025.1 경로]`
- 4.1의 예시 입력을 만들려면 Python `numpy`, `opencv-python`이 필요합니다 (검증 때 numpy 2.2.6, OpenCV 4.11.0). 실제 사진을 쓸 때는 필요 없습니다.
- 4.5의 C 시뮬레이션은 Vitis를 설치하면 함께 오는 HLS 헤더(`$XILINX_HLS/include`)와 `g++`를 씁니다.
- 모든 명령은 **저장소 루트**에서 실행합니다. 실습 파일은 `datasets/pscen/`(git에 올라가지 않음)에 만듭니다.

## 4. 개발 절차

### 4.1. 입력 만들기

상품 사진 대신 512×512 회색조 합성 영상(상품 몸체·뚜껑·라벨 글자 + 약한 잡음, 고정 시드)을 만듭니다.

```bash
python3 isc/operations/image_bicubic/examples/make_input.py
```

입력 파일은 [`examples/make_input.py`](examples/make_input.py)가 만듭니다. 스크립트는 두 부분으로 나뉩니다.

| 부분 | 하는 일 | 사용자 |
|---|---|---|
| `make_sample()` | 예시 데이터를 만듦 | **바꾸는 곳** — 자기 데이터로 바꿈 |
| `encode()` | 2절의 입력 형식(헤더·엔디안·타입)대로 바이트를 씀 | 고치지 않음 |

- 내 사진(PNG·JPEG 등) 변환: `python3 isc/operations/image_bicubic/examples/make_input.py --image 사진.jpg -o datasets/pscen/my_photo.bin` (회색조로 바꾸고 헤더를 붙임)
- 저장 위치를 바꾸려면 `-o 경로`를 붙입니다.

### 4.2. 모듈 만들기

```bash
isc describe image_bicubic                  # 입력·출력 형식 확인 (선택)
isc new product_thumb --operation image_bicubic --set out_rows=256 --set out_cols=256
```

생성되는 `modules/product_thumb/module.yaml`:

```yaml
module:
  id: product_thumb
  name: product_thumb
  version: 0.1.0
operation:
  id: image_bicubic
  version: '1.0'
parameters:
  out_rows: 256
  out_cols: 256
```

출력 크기는 호스트에서 넘기는 값이라 바꿔도 다시 빌드하지 않습니다.

### 4.3. 검사

```bash
isc check product_thumb
```

```text
product_thumb: PASS
  note   : operation 'image_bicubic' has no CPU baseline (baseline.py); `isc bench` cannot compute a speedup for it
```

`note`는 오류가 아닙니다. 이 연산은 CPU 기준선 대신 4.5의 방법으로 정답을 확인하므로, 속도 비교(`isc bench`)만 할 수 없다는 뜻입니다.

### 4.4. 에뮬레이션 실행 (hw_emu)

```bash
isc run product_thumb --input datasets/pscen/product_512.bin --output datasets/pscen/product_thumb.emu.bin
```

### 4.5. 결과 확인

공식 커널 소스를 CPU용으로 한 번 컴파일하고(수 초), 같은 입력으로 돌려 FPGA 출력(헤더 8바이트 제외)과 바이트 비교합니다.

```bash
source [Vitis 설치 경로]/settings64.sh      # $XILINX_HLS 설정
V=$VITIS_LIBRARIES/codec
g++ -O2 -std=c++14 -w -I$XILINX_HLS/include -I$V/L2/include/hw/resize -I$V/L2/demos/resize/kernel \
    verification/u50_vitis_libraries/tools/csim/codec_resize_tb.cpp $V/L2/demos/resize/kernel/kernel_resize.cpp \
    -o datasets/pscen/codec_resize_tb
tail -c +9 datasets/pscen/product_512.bin > datasets/pscen/product_512.raw
datasets/pscen/codec_resize_tb datasets/pscen/product_512.raw datasets/pscen/thumb_csim.raw 512 512 256 256
python3 - <<'EOF'
got = open("datasets/pscen/product_thumb.emu.bin", "rb").read()
ref = open("datasets/pscen/thumb_csim.raw", "rb").read()
print("header", got[:8].hex(), "pixels", len(got) - 8)
print("MATCH" if got[8:] == ref else "MISMATCH")
EOF
```

```text
header 0001000000010000 pixels 65536
MATCH
```

### 4.6. U50 실행 (hw)

```bash
isc run product_thumb --input datasets/pscen/product_512.bin --output datasets/pscen/product_thumb.hw.bin --target hw
```

플랫폼이 hw_emu 기준 출력을 먼저 만들고 → hw 빌드 → 카드 실행 → 두 출력을 바이트 비교합니다. 이 서버에서 처음 실행(빌드 포함)은 약 48분 걸렸습니다. 4.5의 Python 부분에서 `product_thumb.emu.bin`을 `product_thumb.hw.bin`으로 바꿔 한 번 더 확인합니다.

실제 출력 (발췌):

```text
  input      : 262152 bytes  .../datasets/pscen/product_512.bin
  output     : 65544 bytes  .../product_thumb_product_512.hw.bin
  kernel_ms  : 0.228
  validation : PASS  (cross_target) matches reference target output (65544 bytes)
```

### 4.7. 기록 확인

```bash
isc records --module product_thumb
isc show [실행 ID]
```

## 5. 내 데이터에 적용하기

| 바꿀 것 | 방법 |
|---|---|
| 사진 | 4.1 스크립트의 `--image 사진 -o 경로`로 변환한 파일을 `--input`으로 |
| 썸네일 크기 | `modules/product_thumb/module.yaml`의 `out_rows`·`out_cols`를 바꾸거나, 크기별로 모듈을 따로 만듦 |
| 컬러 사진 | 이 연산은 1채널만 받음. 채널을 나눠 세 번 실행한 뒤 합치는 방식은 시험하지 않았음 |

## 6. 문제 해결

| 증상 | 원인 | 해결 |
|---|---|---|
| `image_bicubic only downscales: ... -> ...` | 출력이 입력보다 큼 | `out_rows ≤ rows`, `out_cols ≤ cols`로 |
| `image must be 1..8192 on each side; ...` | 한 변이 8192 초과 | 먼저 다른 방법으로 줄이거나 나눠서 처리 |
| `header says RxC = ... pixels; received ...` | 헤더와 화소 수가 다름 | 헤더를 `rows, cols` 순서로 썼는지 확인 |
| `bicubicinterpolator.hpp` 없음 (빌드) | 헤더 경로 | 이 연산의 `operation.yaml`에 공식 경로(`codec/L2/include/hw/resize`)가 들어 있음. `VITIS_LIBRARIES`가 올바른지 확인 |
| OpenCV 결과와 다름 | 정의가 다른 bicubic | 정상. 4.5의 C 시뮬레이션과 비교 |

## 7. 검증 기록

### 7.1. 플랫폼 자체 테스트

| 항목 | 내용 |
|---|---|
| 확인일 | 2026-11-05 |
| 환경 | Ubuntu 22.04.5 LTS (커널 6.8.0-138), Vitis 2025.1, XRT 2.19.194, Vitis Libraries `v2025.1_update2` (커밋 b2c657d), AMD Alveo U50 (`xilinx_u50_gen3x16_xdma_base_5`), 저장소 기준 커밋 `8742866` + 이 연산 폴더(커밋 전) |
| 입력 | 4.1의 `product_512.bin` (262,152바이트, sha256 `d2d22836…`), 512×512 → 256×256 |
| hw_emu | 통과 — 실행 `20261105T235433-da9d66` |
| hw (U50) | **통과** — 실행 `20261105T235433-3457ff`. hw 출력 = hw_emu 출력(플랫폼 자동 대조) = CPU C 시뮬레이션 (65,536화소 바이트 일치) |
| 참고 | 1차 빌드는 헤더 경로 오류, 2차는 동시 빌드 중 메모리 부족으로 실패. 경로를 고치고 단독으로 다시 빌드해 통과. 2026-11-09에 4.5의 C 시뮬레이션 명령을 다시 실행해 hw 출력과 일치함을 재확인 |
| 확인하지 않은 것 | 성능(가속 효과). 이 문서의 입력 한 가지로만 확인 |

### 7.2. 공식 라이브러리 테스트

Vitis Libraries 공식 테스트를 고치지 않고 U50에서 빌드·실행했습니다. **입력은 공식 테스트에 들어 있는 데이터**이며 위 시나리오 데이터가 아닙니다.

| 항목 | 내용 |
|---|---|
| 공식 테스트 | `codec/L2/demos/resize` (Vitis Libraries `v2025.1_update2`) |
| 빌드 | `make all TARGET=hw PLATFORM=xilinx_u50_gen3x16_xdma_5_202210_1` (2026-11-04, 약 71분) |
| 실행 | `make run TARGET=hw PLATFORM=xilinx_u50_gen3x16_xdma_5_202210_1` (공식 `images/t0.raw`, 512×512 → 64×64) |
| 결과 | **통과** — 2026-11-05, `PASS: no error found.` / `Info: Test passed` |
