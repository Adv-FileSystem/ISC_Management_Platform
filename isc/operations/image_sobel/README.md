# `image_sobel` — 제조 라인 영상에서 부품 윤곽 추출 (연산 선택형)

| 개발 방식 | 사용자가 작성하는 것 | 커널 | 파라미터 | 플랫폼 자체 테스트 | 공식 라이브러리 테스트 |
|---|---|---|---|---|---|
| 연산 선택형 | 없음 (`isc new` 한 줄) | Vitis Vision L1 `xf::cv::Sobel` 3×3 (`kernel/sobel_kernel.cpp`, 공식 L1 예제와 같은 설정) | 없음 | ✅ 통과 (2026-11-05) | 미실행 |

> 커널 본문과 설정(3×3, 8비트 입력·출력, 한 사이클 1화소, 가장자리 0 채움)은 공식 L1 예제와 같고, 최대 영상 크기만 1920×1080으로 정했습니다.

## 1. 시나리오

| 항목 | 내용 |
|---|---|
| 사용자 | 제조 라인 비전 검사 시스템 개발자 |
| 상황 | 제조 라인 카메라 영상에서 부품 윤곽(에지)을 뽑아 불량 검사의 전처리로 쓴다 |
| 가속하는 단계 | x·y 방향 기울기 계산 |
| 입력 → 출력 | 8비트 회색조 영상 → 기울기 영상 2장 (x 방향, y 방향) |
| 완료 기준 | OpenCV `Sobel`과 바이트 일치 (공식 testbench도 차이 0 요구) |

## 2. 연산 요약

| 항목 | 내용 |
|---|---|
| 입력 | 리틀 엔디안 헤더 `u32 rows, u32 cols` + `u8 pixels[rows][cols]` (8비트 회색조 1채널) |
| 출력 | `u32 rows, u32 cols` + `u8 grad_x[rows][cols]` + `u8 grad_y[rows][cols]` (입력의 2배 크기) |
| 기울기 값 | 8비트 포화: 음수는 0, 255를 넘으면 255 (OpenCV `CV_8U`와 같음). 어두움→밝음 경계만 값이 남음 |
| 파라미터 | 없음 |
| 크기 한계 | 1 ≤ rows ≤ 1080, 1 ≤ cols ≤ 1920 (커널 상수 `HEIGHT`, `WIDTH`) |
| 가장자리 | 영상 밖을 0으로 채움 (`XF_BORDER_CONSTANT`) |
| CPU 참조 | `baseline.py` — OpenCV `cv2.Sobel(CV_8U, 3×3, BORDER_CONSTANT)` x와 y. 장치 출력과 **바이트까지 같아야** 함 |
| 메모리 | 포트 3개(입력, x 기울기, y 기울기)를 HBM 뱅크 0~2에 연결 (`operation.yaml`) |

## 3. 준비

- 공통 설치와 환경 확인: [README 1절](../../../README.md#1-설치-및-환경-구성)
- 빌드에 **`VITIS_LIBRARIES`** 가 필요합니다: `export VITIS_LIBRARIES=[Vitis Libraries 2025.1 경로]`
- 4.1의 예시 입력과 4.5의 결과 확인에는 Python `numpy`, `opencv-python`이 필요합니다 (검증 때 numpy 2.2.6, OpenCV 4.11.0).
- 모든 명령은 **저장소 루트**에서 실행합니다. 실습 파일은 `datasets/pscen/`(git에 올라가지 않음)에 만듭니다.

## 4. 개발 절차

### 4.1. 입력 만들기

카메라 영상 대신 256×256 합성 영상(어두운 컨베이어 위 원형 부품 + 가운데 구멍 + 볼트 구멍 6개 + 돌출부 + 약한 잡음, 고정 시드)을 만듭니다.

```bash
python3 isc/operations/image_sobel/examples/make_input.py
```

```text
ok 256 256
```

입력 파일은 [`examples/make_input.py`](examples/make_input.py)가 만듭니다. 스크립트는 두 부분으로 나뉩니다.

| 부분 | 하는 일 | 사용자 |
|---|---|---|
| `make_sample()` | 예시 데이터를 만듦 | **바꾸는 곳** — 자기 데이터로 바꿈 |
| `encode()` | 2절의 입력 형식(헤더·엔디안·타입)대로 바이트를 씀 | 고치지 않음 |

- 내 사진(PNG·JPEG 등) 변환: `python3 isc/operations/image_sobel/examples/make_input.py --image 사진.jpg -o datasets/pscen/my_photo.bin` (회색조로 바꾸고 헤더를 붙임)
- 저장 위치를 바꾸려면 `-o 경로`를 붙입니다.

### 4.2. 모듈 만들기

```bash
isc describe image_sobel                    # 입력·출력 형식 확인 (선택)
isc new part_outline --operation image_sobel
```

생성되는 `modules/part_outline/module.yaml` — 고칠 것이 없습니다.

```yaml
module:
  id: part_outline
  name: part_outline
  version: 0.1.0
operation:
  id: image_sobel
  version: '1.0'
```

### 4.3. 검사

```bash
isc check part_outline
```

```text
part_outline: PASS
```

### 4.4. 에뮬레이션 실행 (hw_emu)

```bash
isc run part_outline --input datasets/pscen/part_cam_256.bin --output datasets/pscen/part_outline.emu.bin
```

처음 실행하면 hw_emu 빌드(수 분)를 한 뒤 실행합니다. 빌드가 캐시에 있으면 이 입력은 약 40초 걸립니다.

### 4.5. 결과 확인

CPU 참조(OpenCV)로 같은 입력을 계산해 바이트 비교합니다. 강한 윤곽(값 100 이상) 화소 수도 함께 봅니다.

```bash
python3 - <<'EOF'
import sys, struct, numpy as np
sys.path.insert(0, "isc/operations/image_sobel")
from baseline import compute
inp = open("datasets/pscen/part_cam_256.bin", "rb").read()
got = open("datasets/pscen/part_outline.emu.bin", "rb").read()
rows, cols = struct.unpack("<II", got[:8]); n = rows * cols
gx, gy = np.frombuffer(got, np.uint8, n, 8), np.frombuffer(got, np.uint8, n, 8 + n)
print("size", rows, cols, "strong edges x", int((gx >= 100).sum()), "y", int((gy >= 100).sum()))
print("MATCH" if got == compute(inp, {}) else "MISMATCH")
EOF
```

```text
size 256 256 strong edges x 1456 y 1502
MATCH
```

윤곽 영상으로 보려면 두 기울기를 합쳐 저장합니다 (예: `cv2.imwrite("edges.png", np.maximum(gx, gy).reshape(rows, cols))`).

### 4.6. U50 실행 (hw)

```bash
isc run part_outline --input datasets/pscen/part_cam_256.bin --output datasets/pscen/part_outline.hw.bin --target hw
```

플랫폼이 hw_emu 기준 출력을 먼저 만들고 → hw 빌드 → 카드 실행 → 두 출력을 바이트 비교합니다. 이 서버에서 hw 빌드는 약 40분 걸렸습니다(처음 한 번). 4.5의 코드에서 `part_outline.emu.bin`을 `part_outline.hw.bin`으로 바꿔 한 번 더 확인합니다.

실제 출력 (발췌):

```text
part_outline [hw] succeeded  run_id=20261110T105530-bce3f4
  input      : 65544 bytes  .../datasets/pscen/part_cam_256.bin
  output     : 131080 bytes  .../datasets/pscen/part_outline.hw.bin
  kernel_ms  : 0.457
  validation : PASS  (cross_target) matches reference target output (131080 bytes)
```

### 4.7. 기록 확인

```bash
isc records --module part_outline
isc show [실행 ID]
```

## 5. 내 데이터에 적용하기

| 바꿀 것 | 방법 |
|---|---|
| 카메라 영상 | 4.1 스크립트의 `--image 사진 -o 경로`로 변환한 파일을 `--input`으로 |
| 밝음→어두움 경계도 필요 | 8비트 포화라 음의 기울기는 0이 됨. 영상을 반전(`255 - 영상`)해 한 번 더 실행하고 결과를 합침 (시험하지 않았음) |
| 잡음이 많은 영상 | 먼저 [`image_gaussian`](../image_gaussian/README.md)으로 잡음을 줄인 뒤 이 연산 실행 (두 번 따로 실행) |
| 큰 영상 (1920×1080 초과) | 커널 최대 크기를 넘음. 줄이거나 나눠서 처리 |

## 6. 문제 해결

| 증상 | 원인 | 해결 |
|---|---|---|
| `image input needs a 8-byte header and pixels; received ...` | 헤더가 없거나 화소가 없음 | `u32 rows, u32 cols`를 앞에 씀 |
| `image must be 1..1080 rows x 1..1920 cols; the header says ...` | 크기가 한계를 넘음 | 줄이거나 나눠서 처리 |
| `header says RxC = ... pixels; received ...` | 헤더와 화소 수가 다름 | 헤더를 `rows, cols` 순서로 썼는지 확인 |
| 출력이 입력의 2배 크기 | x·y 기울기 두 장을 이어 붙인 형식 | 정상. 4.5처럼 `8`바이트 뒤 `rows·cols`씩 나눠 읽음 |
| OpenCV와 다름 | OpenCV를 `CV_16S`나 기본 가장자리 방식으로 계산함 | `cv2.CV_8U`, `borderType=cv2.BORDER_CONSTANT`로 비교 (`baseline.py`와 같게) |
| `... requires an environment variable for include path '${VITIS_LIBRARIES}/...'` (`isc check`) | `VITIS_LIBRARIES` 미설정 | `export VITIS_LIBRARIES=[경로]` 후 다시 검사 |

## 7. 검증 기록

### 7.1. 플랫폼 자체 테스트

| 항목 | 내용 |
|---|---|
| 확인일 | 2026-11-05 (시나리오 검증), 2026-11-10 (이 문서 절차 재실행) |
| 환경 | Ubuntu 22.04.5 LTS (커널 6.8.0-138), Python 3.10.12, Vitis 2025.1, XRT 2.19.194 (2025.1), Vitis Libraries `v2025.1_update2` (커밋 b2c657d), AMD Alveo U50 (`xilinx_u50_gen3x16_xdma_base_5`), 저장소 기준 커밋 `3237e67` + 이 연산 폴더(커밋 전), numpy 2.2.6, OpenCV 4.11.0 |
| 시나리오 검증 (11-05) | 모듈 `part_edges`, 부품 윤곽 256×256. hw = hw_emu 일치(플랫폼 대조 PASS), OpenCV `Sobel`과 바이트 일치 — 실행 `20261105T172120-417b5d` |
| 이 문서 절차 (11-10) | 4.1~4.6을 저장소 루트에서 그대로 실행. 입력 `part_cam_256.bin` (65,544바이트, sha256 `4395f2aa…`). hw_emu `20261110T105139-848f71` = OpenCV 바이트 일치. hw `20261110T105530-bce3f4` = hw_emu(플랫폼 대조 PASS) = OpenCV 바이트 일치 |
| 빌드 | hw 빌드 약 40분 (2026-11-04), 타이밍 미달로 인한 클럭 축소 없음 |
| 추가 크기 확인 | 128×128 — hw = hw_emu (플랫폼 대조 PASS). C 시뮬레이션으로 128×128·512×512·1920×1080에서 OpenCV와 바이트 일치 |
| 확인하지 않은 것 | 성능(가속 효과). 카드에서 512×512 이상 영상 |

### 7.2. 공식 라이브러리 테스트

**미실행** — 공식 Vision 테스트의 대상 장치에 U50이 없고(L2는 zcu104·vck190·aws-vu9p-f1), 공식 호스트가 OpenCV C++ 라이브러리를 요구하는데 이 서버에는 설치되어 있지 않습니다(관리자 권한 필요). 그래서 같은 커널 설정을 플랫폼 연산으로 감싸 7.1로 검증했습니다.
