# `image_resize` — 학습 데이터 이미지를 모델 입력 크기로 일괄 축소 (연산 선택형)

| 개발 방식 | 사용자가 작성하는 것 | 커널 | 파라미터 | 플랫폼 자체 테스트 | 공식 라이브러리 테스트 |
|---|---|---|---|---|---|
| 연산 선택형 | 없음 (`isc new` 한 줄 + 출력 크기) | Vitis Vision L1 `xf::cv::resize` bilinear (`kernel/resize_kernel.cpp`, 공식 L2 예제와 같은 설정) | `out_rows`, `out_cols` | ✅ 통과 (2026-11-05) | 미실행 |

> 커널 본문과 설정(bilinear, 최대 입력 3840×2160, 최대 출력 1920×1080, 최대 2배 축소, URAM 사용, 8비트 1채널)은 공식 L2 예제와 같습니다.

## 1. 시나리오

| 항목 | 내용 |
|---|---|
| 사용자 | AI 모델 개발자 (학습 데이터 전처리) |
| 상황 | 학습 데이터 이미지를 모델 입력 크기(예: 1920×1080 → 960×540)로 일괄 축소한다 |
| 가속하는 단계 | 크기 조정 (bilinear 보간) |
| 입력 → 출력 | 8비트 회색조 영상 → 축소 영상 |
| 완료 기준 | OpenCV `INTER_LINEAR`와 화소 차이 5 이하 (공식 testbench 허용오차) |

## 2. 연산 요약

| 항목 | 내용 |
|---|---|
| 입력 | 리틀 엔디안 헤더 `u32 rows, u32 cols` + `u8 pixels[rows][cols]` (8비트 회색조 1채널) |
| 출력 | 같은 형식: `u32 out_rows, u32 out_cols` + `u8 pixels[out_rows][out_cols]` |
| 파라미터 | `out_rows`(기본 540, 최대 1080), `out_cols`(기본 960, 최대 1920). 실행 인자라 바꿔도 다시 빌드하지 않음 |
| 크기 한계 | 입력 최대 3840×2160, 출력 최대 1920×1080. **축소는 2배까지**: `rows ≤ 2·out_rows`, `cols ≤ 2·out_cols` (커널 상수 `MAXDOWNSCALE 2`) |
| CPU 참조 | `baseline.py` — OpenCV `cv2.resize(INTER_LINEAR)`. 장치는 고정소수점 보간이라 값이 조금 다를 수 있어, 플랫폼 자동 대조에는 쓰지 않고 4.5에서 허용오차로 비교 |
| 메모리 | 포트 2개(입력, 출력)를 HBM 뱅크 0~1에 연결 (`operation.yaml`) |

## 3. 준비

- 공통 설치와 환경 확인: [README 1절](../../../README.md#1-설치-및-환경-구성)
- 빌드에 **`VITIS_LIBRARIES`** 가 필요합니다: `export VITIS_LIBRARIES=[Vitis Libraries 2025.1 경로]`
- 4.1의 예시 입력과 4.5의 결과 확인에는 Python `numpy`, `opencv-python`이 필요합니다 (검증 때 numpy 2.2.6, OpenCV 4.11.0).
- 모든 명령은 **저장소 루트**에서 실행합니다. 실습 파일은 `datasets/pscen/`(git에 올라가지 않음)에 만듭니다.

## 4. 개발 절차

1920×1080 영상은 hw_emu에서 너무 오래 걸립니다. 그래서 **작은 영상(384×216 → 192×108)으로 hw_emu와 카드 결과를 맞춰 본 뒤**, 원래 크기(1920×1080 → 960×540)는 카드에서만 실행해 OpenCV와 비교합니다. 출력 크기가 달라 모듈을 두 개 만듭니다(빌드는 같은 것을 함께 씁니다).

### 4.1. 입력 만들기

학습 사진 대신 합성 장면(명암 배경 + 사물 12개 + 약한 잡음, 고정 시드)을 두 크기로 만듭니다.

```bash
python3 isc/operations/image_resize/examples/make_input.py
```

```text
ok
```

입력 파일은 [`examples/make_input.py`](examples/make_input.py)가 만듭니다. 스크립트는 두 부분으로 나뉩니다.

| 부분 | 하는 일 | 사용자 |
|---|---|---|
| `make_sample()` | 예시 데이터를 만듦 | **바꾸는 곳** — 자기 데이터로 바꿈 |
| `encode()` | 2절의 입력 형식(헤더·엔디안·타입)대로 바이트를 씀 | 고치지 않음 |

- 내 사진(PNG·JPEG 등) 변환: `python3 isc/operations/image_resize/examples/make_input.py --image 사진.jpg -o datasets/pscen/my_photo.bin` (회색조로 바꾸고 헤더를 붙임)
- 저장 위치를 바꾸려면 `-o 경로`를 붙입니다.

### 4.2. 모듈 만들기

```bash
isc describe image_resize                   # 입력·출력 형식 확인 (선택)
isc new train_preview --operation image_resize --set out_rows=108 --set out_cols=192   # 작은 영상 확인용
isc new train_input   --operation image_resize --set out_rows=540 --set out_cols=960   # 원래 크기
```

생성되는 `modules/train_input/module.yaml` (`train_preview`는 `out_rows: 108`, `out_cols: 192`):

```yaml
module:
  id: train_input
  name: train_input
  version: 0.1.0
operation:
  id: image_resize
  version: '1.0'
parameters:
  out_rows: 540
  out_cols: 960
```

### 4.3. 검사

```bash
isc check train_preview
isc check train_input
```

```text
train_preview: PASS
train_input: PASS
```

### 4.4. 에뮬레이션 실행 (hw_emu)

작은 영상만 실행합니다.

```bash
isc run train_preview --input datasets/pscen/frame_384x216.bin --output datasets/pscen/train_preview.emu.bin
```

처음 실행하면 hw_emu 빌드(수 분)를 한 뒤 실행합니다. 빌드가 캐시에 있으면 이 입력은 약 80초 걸립니다.

### 4.5. 결과 확인

CPU 참조(OpenCV)로 같은 입력을 계산하고, 화소마다 차이가 5 이하인지 봅니다.

```bash
python3 - <<'EOF'
import sys, numpy as np
sys.path.insert(0, "isc/operations/image_resize")
from baseline import compute
inp = open("datasets/pscen/frame_384x216.bin", "rb").read()
got = open("datasets/pscen/train_preview.emu.bin", "rb").read()
ref = compute(inp, {"out_rows": 108, "out_cols": 192})
d = np.abs(np.frombuffer(got[8:], np.uint8).astype(int) - np.frombuffer(ref[8:], np.uint8))
print("header", got[:8] == ref[:8], "max_diff", d.max(), "over_5", int((d > 5).sum()))
print("PASS" if got[:8] == ref[:8] and d.max() <= 5 else "FAIL")
EOF
```

```text
header True max_diff 1 over_5 0
PASS
```

### 4.6. U50 실행 (hw)

```bash
# 작은 영상: hw_emu 기준 출력과 자동 대조
isc run train_preview --input datasets/pscen/frame_384x216.bin --output datasets/pscen/train_preview.hw.bin --target hw
# 원래 크기: hw_emu 기준 실행을 건너뜀 (--no-validate)
isc run train_input --input datasets/pscen/frame_1920x1080.bin --output datasets/pscen/train_input.hw.bin --target hw --no-validate
```

첫 번째 명령은 hw_emu 기준 출력을 먼저 만들고 → hw 빌드 → 카드 실행 → 두 출력을 바이트 비교합니다. 이 서버에서 hw 빌드는 약 44분 걸렸습니다(처음 한 번, 두 모듈이 같은 빌드를 씀). 두 번째 명령은 플랫폼 대조가 `skipped`로 나오므로, **반드시 4.5의 코드로 OpenCV와 비교합니다**. 파일 이름을 `train_input.hw.bin`·`frame_1920x1080.bin`으로, 파라미터를 `{"out_rows": 540, "out_cols": 960}`으로 바꿉니다. `train_preview.hw.bin`도 같은 방법으로 한 번 더 확인합니다.

실제 출력 (발췌):

```text
train_preview [hw] succeeded  run_id=20261110T105618-d3e5b0
  input      : 82952 bytes  .../datasets/pscen/frame_384x216.bin
  output     : 20744 bytes  .../datasets/pscen/train_preview.hw.bin
  kernel_ms  : 1.977
  validation : PASS  (cross_target) matches reference target output (20744 bytes)

train_input [hw] succeeded  run_id=20261110T105739-35fb6e
  input      : 2073608 bytes  .../datasets/pscen/frame_1920x1080.bin
  output     : 518408 bytes  .../datasets/pscen/train_input.hw.bin
  kernel_ms  : 41.547
  validation : PASS  (cross_target) skipped
```

OpenCV 비교 (두 출력 모두):

```text
train_preview.hw.bin  header True max_diff 1 over_5 0
train_input.hw.bin    header True max_diff 1 over_5 0
```

### 4.7. 기록 확인

```bash
isc records --module train_input
isc show [실행 ID]
```

## 5. 내 데이터에 적용하기

| 바꿀 것 | 방법 |
|---|---|
| 사진 | 4.1 스크립트의 `--image 사진 -o 경로`로 변환한 파일을 `--input`으로. 사진마다 반복 실행 (모듈 재사용) |
| 출력 크기 | `module.yaml`의 `out_rows`·`out_cols`. 다시 빌드하지 않음. 크기별로 모듈을 따로 만들어도 됨 |
| 2배보다 크게 축소 (예: 4K → 960×540) | 한 번에 안 됨. 두 번 나눠 실행 (3840×2160 → 1920×1080 → 960×540). 이 방법은 시험하지 않았음 |
| 확대 | 크기 검사는 통과하지만 시험하지 않았음 |
| 컬러 사진 | 이 연산은 1채널만 받음. 채널을 나눠 세 번 실행하는 방식은 시험하지 않았음 |
| 고품질 썸네일 (bicubic) | [`image_bicubic`](../image_bicubic/README.md) 사용 |

## 6. 문제 해결

| 증상 | 원인 | 해결 |
|---|---|---|
| `image input needs a 8-byte header and pixels; received ...` | 헤더가 없거나 화소가 없음 | `u32 rows, u32 cols`를 앞에 씀 |
| `input image must be at most 2160x3840; the header says ...` | 입력이 최대 크기를 넘음 | 먼저 다른 방법으로 줄임 |
| `header says RxC = ... pixels; received ...` | 헤더와 화소 수가 다름 | 헤더를 `rows, cols` 순서로 썼는지 확인 |
| `output must be at most 1080x1920; asked ...` | 출력이 최대 크기를 넘음 | `out_rows ≤ 1080`, `out_cols ≤ 1920` |
| `downscale beyond 2x is not built in: ... -> ...` | 2배보다 크게 축소 | 두 번에 나눠 실행 (5절) |
| 원래 크기 hw_emu가 끝나지 않음 | 1920×1080은 에뮬레이션이 매우 느림 | 4.4처럼 작은 영상으로 hw_emu 확인, 원래 크기는 `--target hw --no-validate` 후 4.5로 비교 |
| OpenCV와 1 정도 다름 | 장치는 고정소수점 보간 | 정상. 공식 testbench는 5까지 허용 |
| `... requires an environment variable for include path '${VITIS_LIBRARIES}/...'` (`isc check`) | `VITIS_LIBRARIES` 미설정 | `export VITIS_LIBRARIES=[경로]` 후 다시 검사 |

## 7. 검증 기록

### 7.1. 플랫폼 자체 테스트

| 항목 | 내용 |
|---|---|
| 확인일 | 2026-11-05 (시나리오 검증), 2026-11-10 (이 문서 절차 재실행) |
| 환경 | Ubuntu 22.04.5 LTS (커널 6.8.0-138), Python 3.10.12, Vitis 2025.1, XRT 2.19.194 (2025.1), Vitis Libraries `v2025.1_update2` (커밋 b2c657d), AMD Alveo U50 (`xilinx_u50_gen3x16_xdma_base_5`), 저장소 기준 커밋 `3237e67` + 이 연산 폴더(커밋 전), numpy 2.2.6, OpenCV 4.11.0 |
| 시나리오 검증 (11-05) | 모듈 `train_resize_small`(384×216 → 192×108): hw = hw_emu 일치(플랫폼 대조 PASS), OpenCV 최대 차이 1 — 실행 `20261105T172233-219b41`. 모듈 `train_resize_full`(1920×1080 → 960×540, hw만): OpenCV 최대 차이 1 — 실행 `20261105T172429-98181a` |
| 이 문서 절차 (11-10) | 4.1~4.6을 저장소 루트에서 그대로 실행. 입력 `frame_384x216.bin` (82,952바이트, sha256 `c76fda41…`), `frame_1920x1080.bin` (2,073,608바이트, sha256 `b388fe30…`). hw_emu `20261110T105221-9137f5` OpenCV 최대 차이 1. hw `20261110T105618-d3e5b0` = hw_emu(플랫폼 대조 PASS), OpenCV 최대 차이 1. hw `20261110T105739-35fb6e`(원래 크기) OpenCV 최대 차이 1, 허용 5 초과 화소 0 |
| 빌드 | hw 빌드 약 44분 (2026-11-04), 타이밍 미달로 인한 클럭 축소 없음 |
| 추가 크기 확인 | 128×128 → 64×64 — hw_emu에서 OpenCV와 최대 차이 1, hw = hw_emu (플랫폼 대조 PASS) |
| 확인하지 않은 것 | 성능(가속 효과). 3840×2160 입력, 확대, 시험한 크기 외의 비율 |

### 7.2. 공식 라이브러리 테스트

**미실행** — 공식 Vision L2 테스트의 대상 장치는 zcu104·vck190·aws-vu9p-f1이고 U50이 없습니다. 또 공식 호스트가 OpenCV C++ 라이브러리를 요구하는데, 이 서버에는 설치되어 있지 않습니다(관리자 권한 필요). 그래서 같은 커널 설정을 플랫폼 연산으로 감싸 7.1로 검증했습니다.
