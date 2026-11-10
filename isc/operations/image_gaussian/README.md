# `image_gaussian` — 문서 스캔 이미지 잡음 제거 (연산 선택형)

| 개발 방식 | 사용자가 작성하는 것 | 커널 | 파라미터 | 플랫폼 자체 테스트 | 공식 라이브러리 테스트 |
|---|---|---|---|---|---|
| 연산 선택형 | 없음 (`isc new` 한 줄) | Vitis Vision L1 `xf::cv::GaussianBlur` 3×3 (`kernel/gaussian_kernel.cpp`, 공식 L2 예제와 같은 설정) | `sigma_milli` | ✅ 통과 (2026-11-05) | 미실행 |

> 커널 본문과 설정(3×3, 8비트 1채널, 한 사이클 1화소, 가장자리 0 채움)은 공식 L2 예제와 같고, 최대 영상 크기만 1920×1080으로 정했습니다.

## 1. 시나리오

| 항목 | 내용 |
|---|---|
| 사용자 | 문서 관리·OCR 서비스 개발자 |
| 상황 | 스캐너로 들어온 문서 이미지의 잡음을 줄여 OCR 정확도를 높인다 |
| 가속하는 단계 | 3×3 Gaussian 흐림 |
| 입력 → 출력 | 8비트 회색조 영상 → 같은 크기의 필터 결과 |
| 완료 기준 | OpenCV `GaussianBlur`와 화소 차이 2 이하 (공식 testbench 허용오차) |

## 2. 연산 요약

| 항목 | 내용 |
|---|---|
| 입력 | 리틀 엔디안 헤더 `u32 rows, u32 cols` + `u8 pixels[rows][cols]` (8비트 회색조 1채널) |
| 출력 | 입력과 같은 형식과 크기 |
| 파라미터 | `sigma_milli` — Gaussian 표준편차 × 1000 (기본 500 = σ 0.5, 공식 3×3 테스트 값. 100~10000). 실행 인자라 바꿔도 다시 빌드하지 않음 |
| 크기 한계 | 1 ≤ rows ≤ 1080, 1 ≤ cols ≤ 1920 (커널 상수 `HEIGHT`, `WIDTH`) |
| 가장자리 | 영상 밖을 0으로 채움 (`XF_BORDER_CONSTANT`). 테두리 1화소는 어두워짐 |
| CPU 참조 | `baseline.py` — OpenCV `cv2.GaussianBlur(3×3, BORDER_CONSTANT)`. 장치는 고정소수점 가중치를 써서 값이 조금 다를 수 있어, 플랫폼 자동 대조에는 쓰지 않고 4.5에서 허용오차로 비교 |
| 메모리 | 포트 2개(입력, 출력)를 HBM 뱅크 0~1에 연결 (`operation.yaml`) |

## 3. 준비

- 공통 설치와 환경 확인: [README 1절](../../../README.md#1-설치-및-환경-구성)
- 빌드에 **`VITIS_LIBRARIES`** 가 필요합니다: `export VITIS_LIBRARIES=[Vitis Libraries 2025.1 경로]`
- 4.1의 예시 입력과 4.5의 결과 확인에는 Python `numpy`, `opencv-python`이 필요합니다 (검증 때 numpy 2.2.6, OpenCV 4.11.0).
- 모든 명령은 **저장소 루트**에서 실행합니다. 실습 파일은 `datasets/pscen/`(git에 올라가지 않음)에 만듭니다.

## 4. 개발 절차

### 4.1. 입력 만들기

스캔 문서 대신 256×256 합성 영상(종이 바탕 + 거래 명세서 글자 + 스캐너 잡음, 고정 시드)을 만듭니다.

```bash
python3 isc/operations/image_gaussian/examples/make_input.py
```

```text
ok 256 256
```


입력 파일은 [`examples/make_input.py`](examples/make_input.py)가 만듭니다. 스크립트는 두 부분으로 나뉩니다.

| 부분 | 하는 일 | 사용자 |
|---|---|---|
| `make_sample()` | 예시 데이터를 만듦 | **바꾸는 곳** — 자기 데이터로 바꿈 |
| `encode()` | 2절의 입력 형식(헤더·엔디안·타입)대로 바이트를 씀 | 고치지 않음 |

- 내 사진(PNG·JPEG 등) 변환: `python3 isc/operations/image_gaussian/examples/make_input.py --image 사진.jpg -o datasets/pscen/my_photo.bin` (회색조로 바꾸고 헤더를 붙임)
- 저장 위치를 바꾸려면 `-o 경로`를 붙입니다.

### 4.2. 모듈 만들기

```bash
isc describe image_gaussian                 # 입력·출력 형식 확인 (선택)
isc new ocr_denoise --operation image_gaussian
```

생성되는 `modules/ocr_denoise/module.yaml` — 기본값(σ 0.5)을 그대로 씁니다.

```yaml
module:
  id: ocr_denoise
  name: ocr_denoise
  version: 0.1.0
operation:
  id: image_gaussian
  version: '1.0'
parameters:
  sigma_milli: 500
```

### 4.3. 검사

```bash
isc check ocr_denoise
```

```text
ocr_denoise: PASS
```

### 4.4. 에뮬레이션 실행 (hw_emu)

```bash
isc run ocr_denoise --input datasets/pscen/ocr_scan_256.bin --output datasets/pscen/ocr_denoise.emu.bin
```

처음 실행하면 hw_emu 빌드(수 분)를 한 뒤 실행합니다. 빌드가 캐시에 있으면 이 입력은 약 40초 걸립니다.

### 4.5. 결과 확인

CPU 참조(OpenCV)로 같은 입력을 계산하고, 화소마다 차이가 2 이하인지 봅니다.

```bash
python3 - <<'EOF'
import sys, numpy as np
sys.path.insert(0, "isc/operations/image_gaussian")
from baseline import compute
inp = open("datasets/pscen/ocr_scan_256.bin", "rb").read()
got = open("datasets/pscen/ocr_denoise.emu.bin", "rb").read()
ref = compute(inp, {"sigma_milli": 500})
d = np.abs(np.frombuffer(got[8:], np.uint8).astype(int) - np.frombuffer(ref[8:], np.uint8))
print("header", got[:8] == ref[:8], "max_diff", d.max(), "over_2", int((d > 2).sum()))
print("PASS" if got[:8] == ref[:8] and d.max() <= 2 else "FAIL")
EOF
```

```text
header True max_diff 0 over_2 0
PASS
```

### 4.6. U50 실행 (hw)

```bash
isc run ocr_denoise --input datasets/pscen/ocr_scan_256.bin --output datasets/pscen/ocr_denoise.hw.bin --target hw
```

플랫폼이 hw_emu 기준 출력을 먼저 만들고 → hw 빌드 → 카드 실행 → 두 출력을 바이트 비교합니다. 이 서버에서 hw 빌드는 약 43분 걸렸습니다(처음 한 번). 4.5의 코드에서 `ocr_denoise.emu.bin`을 `ocr_denoise.hw.bin`으로 바꿔 한 번 더 확인합니다.

실제 출력 (발췌):

```text
ocr_denoise [hw] succeeded  run_id=20261110T105444-091fed
  input      : 65544 bytes  .../datasets/pscen/ocr_scan_256.bin
  output     : 65544 bytes  .../datasets/pscen/ocr_denoise.hw.bin
  kernel_ms  : 0.407
  validation : PASS  (cross_target) matches reference target output (65544 bytes)
```

### 4.7. 기록 확인

```bash
isc records --module ocr_denoise
isc show [실행 ID]
```

## 5. 내 데이터에 적용하기

| 바꿀 것 | 방법 |
|---|---|
| 스캔 이미지 | 4.1 스크립트의 `--image 사진 -o 경로`로 변환한 파일을 `--input`으로 |
| 흐림 정도 | `modules/ocr_denoise/module.yaml`의 `sigma_milli` (예: 1000 = σ 1.0). 다시 빌드하지 않음. σ 0.5 외의 값은 시험하지 않았음 |
| 큰 이미지 (1920×1080 초과) | 커널 최대 크기를 넘음. 줄이거나 나눠서 처리 (나눌 때는 경계 1화소가 0 채움이 되는 점 주의) |
| 컬러 이미지 | 이 연산은 1채널만 받음. 채널을 나눠 세 번 실행하는 방식은 시험하지 않았음 |

## 6. 문제 해결

| 증상 | 원인 | 해결 |
|---|---|---|
| `image input needs a 8-byte header and pixels; received ...` | 헤더가 없거나 화소가 없음 | `u32 rows, u32 cols`를 앞에 씀 |
| `image must be 1..1080 rows x 1..1920 cols; the header says ...` | 크기가 한계를 넘음 | 줄이거나 나눠서 처리 |
| `header says RxC = ... pixels; received ...` | 헤더와 화소 수가 다름 | 헤더를 `rows, cols` 순서로 썼는지 확인 |
| OpenCV와 1~2 정도 다름 | 장치는 고정소수점 가중치 | 정상. 공식 testbench도 2까지 허용 |
| 테두리가 OpenCV 기본 결과보다 어두움 | OpenCV 기본 가장자리 방식(`BORDER_REFLECT_101`)과 비교함 | `borderType=cv2.BORDER_CONSTANT`로 비교 (4.5의 `baseline.py`가 이렇게 함) |
| `... requires an environment variable for include path '${VITIS_LIBRARIES}/...'` (`isc check`) | `VITIS_LIBRARIES` 미설정 | `export VITIS_LIBRARIES=[경로]` 후 다시 검사 |

## 7. 검증 기록

### 7.1. 플랫폼 자체 테스트

| 항목 | 내용 |
|---|---|
| 확인일 | 2026-11-05 (시나리오 검증), 2026-11-10 (이 문서 절차 재실행) |
| 환경 | Ubuntu 22.04.5 LTS (커널 6.8.0-138), Python 3.10.12, Vitis 2025.1, XRT 2.19.194 (2025.1), Vitis Libraries `v2025.1_update2` (커밋 b2c657d), AMD Alveo U50 (`xilinx_u50_gen3x16_xdma_base_5`), 저장소 기준 커밋 `3237e67` + 이 연산 폴더(커밋 전), numpy 2.2.6, OpenCV 4.11.0 |
| 시나리오 검증 (11-05) | 모듈 `scan_denoise`, 문서 스캔 256×256, σ 0.5. hw = hw_emu 일치(플랫폼 대조 PASS), OpenCV와 최대 차이 0 (허용 2) — 실행 `20261105T172008-99197e` |
| 이 문서 절차 (11-10) | 4.1~4.6을 저장소 루트에서 그대로 실행. 입력 `ocr_scan_256.bin` (65,544바이트, sha256 `87bda2d4…`). hw_emu `20261110T105057-13ce1d`, OpenCV와 최대 차이 0. hw `20261110T105444-091fed` = hw_emu(플랫폼 대조 PASS), OpenCV와 최대 차이 0 |
| 빌드 | hw 빌드 약 43분 (2026-11-04), 타이밍 미달로 인한 클럭 축소 없음 |
| 추가 크기 확인 | 128×128 공식 샘플 크기 — hw_emu에서 OpenCV와 차이 0, hw = hw_emu (플랫폼 대조 PASS) |
| 확인하지 않은 것 | 성능(가속 효과). 512×512·1920×1080 같은 큰 영상, σ 0.5 외의 값 |

### 7.2. 공식 라이브러리 테스트

**미실행** — 공식 Vision L2 테스트의 대상 장치는 zcu104·vck190·aws-vu9p-f1이고 U50이 없습니다. 또 공식 호스트가 OpenCV C++ 라이브러리를 요구하는데, 이 서버에는 설치되어 있지 않습니다(관리자 권한 필요). 그래서 같은 커널 설정을 플랫폼 연산으로 감싸 7.1로 검증했습니다.
