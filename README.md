# ISC Management Platform

## 개요

- ISC가 제공하는 FPGA 연산으로 모듈을 만들고 **검사 → 빌드 → 실행 → 검증 → 기록**하는 로컬 관리
플랫폼입니다. 사용자는 HLS 커널, AXI 인터페이스, 메모리 뱅크, XRT 어댑터 작성 없이 원하는 연산을 작성하여 ISC 모듈을 개발할 수 있습니다. 
- 모듈 개발 방식은 총 2가지로 제공되며, 사전 정의된 모듈을 선택하거나 원하는 연산의 프로파일을 선택하여 함수를 수정하는 Hook형으로 구분됩니다. 연산 선택형 중 사용자 함수 자리가 있는 연산(현재 `graph_twohop`)은 Vitis 라이브러리 함수 뒤에 사용자가 작성한 함수를 붙일 수 있습니다.

| 유형 | 개발 방식 | 제공 항목 |
|---|---|---|
| 연산 선택형 | 사전 정의된 연산을 선택하고 `module.yaml`의 파라미터 설정. 사용자 함수 자리가 있는 연산은 선택적으로 함수 하나의 `return` 줄 작성 | Vitis Libraries 연산([지원 범위](#지원-범위) 1), 플랫폼 자체 연산 `identity` `vector_topk`. 사용자 함수 자리: `graph_twohop` |
| Hook형 | 프로파일을 고르고 함수 하나의 `return` 줄 작성 | `filter` `map` `aggregate` ([지원 범위](#지원-범위) 2) |

## 디렉터리 구조

플랫폼 저장소(설치 대상, 사용자가 수정하지 않음):

```text
ISC_Management_Platform/
├── pyproject.toml                  # 패키지 정의 (isc 명령 설치)
├── isc/
│   ├── cli.py                      # isc 명령 진입점
│   ├── lifecycle/                  # 검사·빌드·실행·검증·기록 (check, build, runtime, validate, record 등)
│   ├── generator/                  # 모듈 스켈레톤·커널 소스 생성
│   ├── backends/                   # 장치 실행 (XRT)
│   ├── benchmark/                  # 가속 효과 검증 (plan, bench, report)
│   └── operations/                 # 연산 정의 (연산·프로파일마다 폴더 하나)
│       ├── lz4/                    # 연산 선택형 예 (crc32, vector_topk 등도 같은 구성)
│       │   ├── operation.yaml      # 입출력·파라미터·빌드 설정
│       │   ├── kernel/             # HLS 커널 소스
│       │   ├── adapter.py          # 호스트 입출력 변환
│       │   ├── baseline.py         # CPU 기준선 (있는 연산만)
│       │   ├── examples/make_input.py  # 입력 파일 만들기 템플릿 (예시 데이터·형식 변환)
│       │   └── README.md           # 연산별 실행 가이드
│       └── filter/                 # Hook형 예 (map, aggregate도 같은 구성)
│           ├── operation.yaml
│           ├── kernel/filter.cpp.tmpl   # 래퍼 커널 틀 (사용자 함수를 끼워 빌드)
│           ├── logic.hpp.template  # isc template이 내주는 시작 파일
│           ├── adapter.py
│           └── README.md
└── verification/                   # U50 검증 도구·결과 기록
```

작업 디렉터리(사용자가 `isc` 명령을 실행하는 곳):

```text
[작업 디렉터리]/
├── modules/                        # 사용자가 작성
│   ├── compressor/                 # 연산 선택형: module.yaml, README.md
│   └── clip_filter/                # Hook형: module.yaml, logic.hpp, README.md
├── experiments/
│   └── [실험 이름]/experiment.yaml  # 가속 효과 검증 실험 정의 (isc plan이 생성)
└── artifacts/                      # 플랫폼이 생성
    ├── cache/<지문>/                # 빌드 결과 (합성 실패 시 생성된 소스와 build.log)
    ├── outputs/                    # 실행 출력
    ├── records.jsonl               # 실행 기록
    ├── events/                     # 실행 단계 이벤트
    ├── locks/                      # 장치 예약 (한 번에 한 실행)
    ├── benchmarks/                 # 가속 검증 측정값
    └── benchdata/                  # 가속 검증용 생성 입력
```

- 사용자가 수정하는 파일은 `module.yaml`과 `logic.hpp`입니다. 이외의 커널, 호스트 어댑터, 빌드 설정은 전주기 관리 시스템이 담당합니다.
- `hw` 빌드는 수십 분에서 수 시간이 걸리고, 장치는 한 번에 하나의 실행만 사용할 수 있으며, 빌드 전 검사(`isc check`)는 작성 규칙 위반만 검출할 뿐 로직의 논리적 오류는 검출하지 않습니다. 빌드·실행 전에 불필요한 재빌드와 검증 결과의 오해를 막기 위해 [**사용자 유의사항**](#6-사용자-유의사항)을 꼭 확인하시기 바랍니다.

## 지원 범위
- 지원 범위에 대한 최신 정보는 `isc devices`, `isc operations`, `isc describe [연산 이름]`으로 확인합니다.

- 현재 지원 환경은 다음과 같습니다.
  - 검증 장치: **AMD Alveo U50**
  - 라이브러리: **AMD Vitis Libraries 2025.1**([`v2025.1_update2`](https://github.com/Xilinx/Vitis_Libraries/tree/v2025.1_update2))
  - 연산 이름을 누르면 그 연산의 세부 실행 가이드(연산 예시, 개발 절차, 실행 방법)로 이동합니다.
  - 라이브러리 이름과 공식 테스트 경로를 누르면 Vitis Libraries 저장소(`v2025.1_update2`)의 해당 폴더로 이동합니다.

### 1. Vitis Libraries 연산

| 라이브러리 | 연산 | 사용하는 라이브러리 함수 | 사용 예시 | 플랫폼 자체 테스트 | 공식 라이브러리 테스트 | 검증 상태 · 사유 |
|---|---|---|---|---|---|---|
| [Data Compression L1](https://github.com/Xilinx/Vitis_Libraries/tree/v2025.1_update2/data_compression/L1) | [`lz4`](isc/operations/lz4/README.md) | `lzCompress` → `lzBestMatchFilter` → `lzBooster` → `lz4Compress` | 1분마다 쌓이는 접근 로그를 LZ4로 압축 보관 | ✅ | ✅ ([`L2/tests/lz4_compress`](https://github.com/Xilinx/Vitis_Libraries/tree/v2025.1_update2/data_compression/L2/tests/lz4_compress)) | 완료 |
| [Data Compression L1](https://github.com/Xilinx/Vitis_Libraries/tree/v2025.1_update2/data_compression/L1) | [`crc32`](isc/operations/crc32/README.md) | `xf::compression::crc32_mm` | 데이터 레이크 적재 파일의 무결성 검사 (`zlib.crc32` 대체) | ✅ | ✅ ([Security L1 `benchmarks/crc32`](https://github.com/Xilinx/Vitis_Libraries/tree/v2025.1_update2/security/L1/benchmarks/crc32)) | 완료 |
| [Data Compression L1](https://github.com/Xilinx/Vitis_Libraries/tree/v2025.1_update2/data_compression/L1) | [`adler32`](isc/operations/adler32/README.md) | `xf::compression::adler32_mm` | 파일 동기화 도구의 블록별 체크섬 (rsync 방식) | ✅ | ✅ ([Security L1 `benchmarks/adler32`](https://github.com/Xilinx/Vitis_Libraries/tree/v2025.1_update2/security/L1/benchmarks/adler32)) | 완료 |
| [Data Compression L2](https://github.com/Xilinx/Vitis_Libraries/tree/v2025.1_update2/data_compression/L2) | [`snappy_compress`](isc/operations/snappy_compress/README.md) | `xilSnappyCompress` | Kafka로 보내는 이벤트 메시지 묶음 압축 | ✅ | ✅ ([`L2/tests/snappy_compress`](https://github.com/Xilinx/Vitis_Libraries/tree/v2025.1_update2/data_compression/L2/tests/snappy_compress)) | 완료 |
| [Graph L2](https://github.com/Xilinx/Vitis_Libraries/tree/v2025.1_update2/graph/L2) | [`graph_twohop`](isc/operations/graph_twohop/README.md) | `xf::graph::twoHop` | 금융 이상거래 탐지: 계좌 쌍이 함께 쓴 기기 수 + 사용자 위험 규칙 (`isc_score`) | ✅ | ✅ ([`L2/tests/twoHop`](https://github.com/Xilinx/Vitis_Libraries/tree/v2025.1_update2/graph/L2/tests/twoHop)) | 완료 |
| [Graph L2](https://github.com/Xilinx/Vitis_Libraries/tree/v2025.1_update2/graph/L2) | [`graph_renumber`](isc/operations/graph_renumber/README.md) | `initRenumber` + `renumberCore` | 그래프 분석 전 원본 ID를 0부터 연속 번호로 | ✅ | ✅ ([`L2/tests/renumber`](https://github.com/Xilinx/Vitis_Libraries/tree/v2025.1_update2/graph/L2/tests/renumber)) | 완료 |
| [Codec L2](https://github.com/Xilinx/Vitis_Libraries/tree/v2025.1_update2/codec/L2) | [`image_bicubic`](isc/operations/image_bicubic/README.md) | `xf::codec::resizeTop` | 학습용 흑백 이미지를 모델 입력 크기로 일괄 축소 | ✅ | ✅ ([`L2/demos/resize`](https://github.com/Xilinx/Vitis_Libraries/tree/v2025.1_update2/codec/L2/demos/resize)) | 완료 |
| [Quantitative Finance L2](https://github.com/Xilinx/Vitis_Libraries/tree/v2025.1_update2/quantitative_finance/L2) | [`mc_european`](isc/operations/mc_european/README.md) | `xf::fintech::MCEuropeanEngine` | 보유 유럽형 옵션 가격 야간 Monte Carlo 재계산 | ✅ | ✅ ([`L2/tests/MCEuropeanEngine`](https://github.com/Xilinx/Vitis_Libraries/tree/v2025.1_update2/quantitative_finance/L2/tests/MCEuropeanEngine)) | 완료 |
| [Security L1](https://github.com/Xilinx/Vitis_Libraries/tree/v2025.1_update2/security/L1) | [`sha256`](isc/operations/sha256/README.md) | `xf::security::sha256` | 설치 패키지가 공개 다이제스트와 같은지 확인 | ⚠ main: 실패 · 수정 브랜치: ✅ | 미실행 | **미완료** — 공식 라이브러리 테스트가 검증 장치를 지원하지 않음 (플랫폼 자체 테스트는 통과) |
| [Vision L1](https://github.com/Xilinx/Vitis_Libraries/tree/v2025.1_update2/vision/L1) | [`image_gaussian`](isc/operations/image_gaussian/README.md) | `GaussianBlur` | 문서 스캔 이미지의 잡음 제거 (OCR 전처리) | ✅ | 미실행 | **미완료** — 공식 L2 테스트는 U50 미지원·OpenCV C++ 필요. 연산 코드 커밋 전 |
| [Vision L1](https://github.com/Xilinx/Vitis_Libraries/tree/v2025.1_update2/vision/L1) | [`image_resize`](isc/operations/image_resize/README.md) | `resize` (bilinear) | 학습 데이터 이미지를 모델 입력 크기로 일괄 축소 | ✅ | 미실행 | **미완료** — `image_gaussian`과 같음 |
| [Vision L1](https://github.com/Xilinx/Vitis_Libraries/tree/v2025.1_update2/vision/L1) | [`image_sobel`](isc/operations/image_sobel/README.md) | `Sobel` | 제조 라인 영상에서 부품 윤곽(에지) 추출 | ✅ | 미실행 | **미완료** — `image_gaussian`과 같음 |
| [BLAS L1](https://github.com/Xilinx/Vitis_Libraries/tree/v2025.1_update2/blas/L1) | [`gemv_i32`](isc/operations/gemv_i32/README.md) | `gemv` | 대출 심사용 선형 점수 모델로 고객 점수 일괄 계산 | ✅ | 미실행 | **미완료** — 공식 문서가 가리키는 벤치마크가 저장소에 없음. 연산 코드 커밋 전 |

### 2. 시스템 자체 연산



| 종류 | 연산 | 개발 방식 | 사용자가 작성하는 것 | 시나리오 | 플랫폼 자체 테스트 |
|---|---|---|---|---|---|
| Hook 프로파일 | [`filter`](isc/operations/filter/README.md) | Hook형 | 조건식 `return` 한 줄 | 매출 리포트용으로 금액 1,000~5,000원 주문만 추출 | ✅ (`u32`) ⚠ |
| Hook 프로파일 | [`map`](isc/operations/map/README.md) | Hook형 | 변환식 `return` 한 줄 | 공장 온도 센서 값(f32)을 0.5 °C 단위 1바이트 코드로 | ✅ (`f32→u8`) ⚠ |
| Hook 프로파일 | [`aggregate`](isc/operations/aggregate/README.md) | Hook형 | 조건식 `return` 한 줄 | 1,000원 미만 소액 거래(환불 포함)의 건수·합계·최소·최대 | ✅ (`i32`) |
| 플랫폼 커널 | [`vector_topk`](isc/operations/vector_topk/README.md) | 연산 선택형 | 없음 (`k`, `chunk_bytes` 선택) | 사내 문서 검색(RAG)에서 가장 가까운 문서 5개 | ✅ |
| 플랫폼 커널 | [`identity`](isc/operations/identity/README.md) | 연산 선택형 | 없음 | 설치 직후 FPGA 실행 경로 점검 | ✅ |


## 목차

- [**1. 설치 및 환경 구성**](#1-설치-및-환경-구성)
- [**2. 모듈 생성 (스켈레톤)**](#2-모듈-생성-스켈레톤)
- [**3. 로직 작성 (Hook 템플릿)**](#3-로직-작성-hook-템플릿)
- [**4. 빌드·실행·검증**](#4-빌드실행검증)
- [**5. 가속 효과 검증**](#5-가속-효과-검증)
- [**6. 사용자 유의사항**](#6-사용자-유의사항)
- [**7. 지원 도구**](#7-지원-도구)
- [**8. 라이브러리/연산 활용 템플릿**](#8-라이브러리연산-활용-템플릿)

## 1. 설치 및 환경 구성

```bash
# 시스템 설치 및 환경 구성
git clone https://github.com/Adv-FileSystem/ISC_Management_Platform.git
cd ISC_Management_Platform
python3 -m pip install -e .       
source /opt/xilinx/xrt/setup.sh
export VITIS_LIBRARIES=[Vitis Libraries 2025.1 경로]

# 개발 환경 및 FPGA 카드 장착 확인
isc doctor                        
```

| 항목 | 요구 버전 |
|---|---|
| OS / 커널 | Ubuntu 22.04 LTS  |
| Python / PyYAML | `>= 3.10` / `>= 5.4` |
| XRT / Vitis / Vitis Libraries | `2025.1` |
| FPGA 카드 | AMD Alveo U50 |
| 타깃 플랫폼 이름 | `xilinx_u50_gen3x16_xdma_5_202210_1` |
| 배포용 패키지 (Deployment Target Platform) | `xilinx-u50-gen3x16-xdma_2022.2_2022_1015_0317-all.deb.tar.gz` |
| 개발용 패키지 (Development Target Platform) | `xilinx-u50-gen3x16-xdma-5-202210-1-dev_1-3499627_all.deb` |

- **타깃 플랫폼**: 카드에 미리 올라가는 기본 하드웨어(셸)입니다. PCIe·DMA·HBM 연결을 담당하며, 사용자 커널은 이 셸 위에 붙습니다. 빌드할 때 `v++ --platform`에 이 이름을 넘기고, 각 연산의 `operation.yaml`(`build.platform`)에 고정되어 있습니다. 카드에 올라간 셸과 이름이 같아야 실행됩니다.
- **배포용 패키지**: 카드에서 **실행**하기 위한 패키지입니다. 셸 이미지를 카드에 기록(플래시)하고 XRT가 카드를 인식하게 합니다. `--target hw` 실행에 필요합니다.
- **개발용 패키지**: 커널을 **빌드**하기 위한 패키지입니다. `v++`가 읽는 플랫폼 정의 파일(`.xpfm`)이 들어 있어 `hw` 빌드와 `hw_emu`(카드 없이 하는 시뮬레이션)에 필요합니다.
- 두 패키지가 항상 모두 필요한 것은 아닙니다. 쓰려는 타깃에 따라 다릅니다.

| 사용 방식 | 개발용 패키지 | 배포용 패키지 |
|---|---|---|
| `--target hw_emu`만 사용 (카드 없이 개발) | 필요 | 불필요 |
| `--target hw` 사용 (카드에서 실행) | 필요 (`hw` 빌드와 대조용 `hw_emu` 실행에 사용) | 필요 |

- `isc run --target hw`는 실행 전에 같은 입력을 `hw_emu`로 먼저 돌리므로([4절](#4-빌드실행검증)), 카드에서 실행하는 PC에는 두 패키지가 모두 있어야 합니다.

참고: [U50 플랫폼 명세](https://docs.amd.com/r/en-US/ug1120-alveo-platforms/U50-Gen3x16-XDMA-base_5-Platform) ·
[XRT 지원 OS](https://docs.amd.com/r/2023.1-English/ug1451-xrt-release-notes/XRT-Operating-System-Support) ·
[U50 패키지 다운로드](https://www.amd.com/en/support/downloads/alveo-previous-downloads.html/accelerators/alveo/u50.html)

## 2. 모듈 생성 (스켈레톤)

### 2.1. 연산 선택형


| 연산 | 기능 | 입력 → 출력 | 파라미터 (범위, 기본값) |
|---|---|---|---|
| `lz4` | 블록 단위 LZ4 압축 | 임의 파일 → 블록 헤더 + LZ4 블록 | `block_size`: 1024–65536, 기본 65536 |
| `snappy_compress` | 64 KiB 블록 단위 Snappy 압축 | 임의 파일(1바이트 이상) → 블록 헤더 + Snappy 블록 | 없음 |
| `crc32` / `adler32` | 체크섬 | 임의 파일(1바이트 이상) → 4바이트 (big-endian) | 없음 |
| `graph_twohop` | 정점 쌍별 2-hop 경로 수 (+ 사용자 함수 `isc_score`) | `u32 V, E, P, reserved`, CSR `offset[V+1]`, `index[E]`, 쌍 `(src, dst)[P]` → `u32 count[P]` (사용자 로직이 있으면 `isc_score` 반환값) | 없음 |
| `graph_renumber` | 묶음 번호를 처음 나온 순서대로 0부터 다시 매김 | `u32 N, i32 id[N]` (0 ≤ id < N) → `u32 묶음 수, i32 new_id[N]` | 없음 |
| `image_bicubic` | 8비트 회색조 영상 bicubic 축소 | `u32 rows, u32 cols, u8 pixels` → 같은 형식 | `out_rows`, `out_cols`: 1–8192 (입력 크기 이하), 기본 256 |
| `mc_european` | 유럽형 옵션 Monte Carlo 가격 | `u32 count, u32 reserved, f32 option[count][8]` → `f32 price[count]` | `samples`: 1024–1048576, 기본 4096; `time_steps`: 1–100, 기본 1 |
| `vector_topk` | int8 행렬과 질의 벡터의 정수 내적 상위 k개 | `u32 rows, u32 dim, i8 query[dim], i8 db[rows][dim]` → `(u32 id, i32 score)` k개 | `k`: 1–16, 기본 10; `chunk_bytes`: 4096–134217728, 기본 16777216 |

```bash
isc describe [연산 이름]                                    # 입출력·파라미터 확인
isc new [모듈 이름] --operation [연산 이름] [--set 파라미터=값]
```

```yaml
# modules/[모듈 이름]/module.yaml
module:
  id: [모듈 이름]        # 변경 금지
  name: [표시 이름]      # 수정 가능
  version: "0.1.0"       # 수정 가능
operation:
  id: [연산 이름]        # 변경 금지
  version: [연산 버전]   # 변경 금지 (생성 시 자동 기입)
parameters:              # 수정 가능 (isc describe에 표시된 항목만)
  [파라미터 이름]: [값]
```

- 대괄호(`[ ]`)는 실제 값으로 바꿉니다. 모듈 이름은 소문자로 시작하고 소문자·숫자·밑줄만 씁니다.
- 입력은 확장자와 관계없이 원시 바이트로 전달됩니다. 정수·실수는 모두 little-endian이며(체크섬 출력만 big-endian), 필드별 세부 형식과 입력 생성 스크립트는 각 연산 문서의 연산 예시와 입력 준비 단계에 있습니다.
- 위 표의 파라미터는 모두 호스트 쪽 값이라 바꿔도 재빌드하지 않습니다. 재빌드가 필요한 파라미터는 `isc describe`에 `[rebuild required]`로 표시됩니다.

#### 사용자 로직 (선택, 사용자 함수 자리가 있는 연산만)

| 연산 | 작성할 함수 | 호출 시점 |
|---|---|---|
| `graph_twohop` | `uint32_t isc_score(uint32_t src, uint32_t dst, uint32_t count)` | `xf::graph::twoHop`이 공통 이웃 수를 센 뒤 쌍마다 1번. 반환값이 그 쌍의 출력 |

```bash
isc new [모듈 이름] --operation [연산 이름] --logic           # 시작 파일 logic.hpp를 모듈에 넣음 → return 줄 수정
isc new [모듈 이름] --operation [연산 이름] --logic 파일.hpp   # 미리 고친 파일로 만들기 (시작 파일: isc template [연산 이름] -o 파일.hpp)
```

```yaml
operation:
  id: [연산 이름]
  version: [연산 버전]
  logic: logic.hpp       # 사용자 로직 파일. 없으면 원래 연산 그대로
```

- 로직 파일이 없거나 시작 파일 그대로면 원래 연산과 같은 결과가 나옵니다. 라이브러리 함수 자체(예: 경로 수 세는 방식)는 바뀌지 않습니다.
- 작성 규칙은 Hook형과 같습니다(3절). 함수 이름·인자·반환 타입은 고정이고 인자 이름만 바꿀 수 있습니다.

### 2.2. Hook형

| 프로파일 | 작성할 함수 | 출력 |
|---|---|---|
| `filter` | `bool isc_filter(T value)` | `true`인 값만, 입력 순서대로 |
| `map` | `U isc_map(T value)` | 변환된 U 값, 개수 동일 |
| `aggregate` | `bool isc_where(T value)` | `count, sum, min, max` (little-endian `u64` 4개) |

```bash
isc new-custom [모듈 이름] --profile [프로파일] --type [T] [--out-type U]
```

```yaml
# modules/[모듈 이름]/module.yaml
module:
  id: [모듈 이름]        # 변경 금지
  name: [표시 이름]      # 수정 가능
  version: "0.1.0"       # 수정 가능
hook:
  profile: [프로파일]    # 변경 금지
  version: [프로파일 버전] # 변경 금지 (생성 시 자동 기입)
  source: logic.hpp      # 변경 금지
```
- 타입: `u8 u16 u32 u64 i8 i16 i32 i64 f32` (`aggregate`는 정수만). 입력은 T 값이 little-endian으로 이어진 파일입니다.

## 3. 로직 작성 (Hook 템플릿)

- 이 절은 **Hook형 모듈**과, 사용자 함수 자리가 있는 연산 선택형 모듈(현재 `graph_twohop`, 2.1 "사용자 로직")에 해당합니다. 그 밖의 연산 선택형 모듈은 로직 파일이 없고 `module.yaml`의 파라미터만 바꿉니다.

- 시작 파일을 받아 함수 내에 **`return` 줄만** 고칩니다. 타입은 함수 시그니처가 정합니다.
- `return` 줄만으로 충분한 이유: 데이터 읽기·쓰기, 반복문, AXI 인터페이스는 플랫폼의 래퍼 커널(`isc/operations/<프로파일>/kernel/*.cpp.tmpl`)이 맡고, 래퍼가 값 하나마다 사용자 함수를 호출합니다. 그래서 사용자는 "값 하나를 받아 무엇을 돌려줄지"만 쓰면 됩니다. 세 프로파일(`filter` `map` `aggregate`) 모두 같은 구조입니다.
- 한계:
  - 프로파일이 정한 형태(값 하나 → 결과 하나)를 벗어나는 로직은 `return` 줄을 어떻게 써도 만들 수 없습니다. 예: 앞뒤 값을 함께 보는 계산(이동 평균), 여러 컬럼을 묶은 조건, 그룹별 집계. 이런 연산은 현재 지원하지 않습니다.
  - 식이 길어지면 보조 함수나 상수를 파일에 추가해도 됩니다(아래 작성 규칙).
  - 입력·출력 타입을 바꾸려면 시그니처를 직접 고치지 말고 `--type`/`--out-type`으로 시작 파일을 다시 받습니다.
  - `map`에서 `f32 → 정수` 변환은 범위를 먼저 잘라 내야 합니다(범위를 벗어나면 결과가 정해지지 않음, [`map` 문서](isc/operations/map/README.md) 4.2).
- 연산 선택형 중 `graph_twohop`은 라이브러리 함수 뒤에 붙는 `isc_score`의 `return` 줄을 고칩니다([`graph_twohop` 문서](isc/operations/graph_twohop/README.md) 2.3). 그 밖의 연산 선택형(Vitis Libraries 연산, `vector_topk`, `identity`)은 아직 고칠 함수가 없고 `module.yaml`의 파라미터만 바꿉니다.

```bash
isc template filter --type f32 -o clip_filter.hpp
```

```cpp
#pragma once

#include <ap_int.h>
#include <stdint.h>

inline bool isc_filter(float value) {
    return true;   // ISC-TODO: 조건으로 바꾸십시오 (지금은 모든 값을 통과시킵니다)
}
```

세 프로파일의 시작 파일은 머리글 형식이 같고 함수 부분만 다릅니다.

| 프로파일 | 생성 명령 예 | 함수 (생성 결과) | 기본 `return` | 지원 타입 |
|---|---|---|---|---|
| `filter` | `isc template filter --type f32` | `inline bool isc_filter(float value)` | `true` (모두 통과) | 정수 8종 + `f32` |
| `map` | `isc template map --type f32 --out-type u8` | `inline uint8_t isc_map(float value)` | `value` (그대로) | 입력·출력 각각 정수 8종 + `f32` |
| `aggregate` | `isc template aggregate --type i32` | `inline bool isc_where(int32_t value)` | `true` (모두 집계) | 정수 8종 (`f32` 불가) |

> **작성 규칙** — 세 프로파일 모두 같으며 `isc check`가 검사합니다.
> - 함수 이름(`isc_filter`/`isc_map`/`isc_where`)과 인자 이름(`value`)은 바꾸지 않습니다. 인자는 값으로 받고(`const`는 허용) 앞에 `inline`/`static`을 붙일 수 있습니다. → 어기면 `problem`
> - 동적 할당(`new`, `malloc` 등), 파일·소켓 입출력(`fopen`, `open`, `socket`), `#pragma HLS INTERFACE`는 쓸 수 없습니다. 주석 안의 글자는 검사하지 않습니다. → 어기면 `problem`
> - 작성 후 `ISC-TODO` 표식을 지웁니다. → 남아 있으면 `warning` (빌드는 진행됨)
> - "`return` 줄만 고친다"는 권장 사항입니다. 보조 함수나 상수를 추가해도 위 규칙만 지키면 검사를 통과합니다.

입력 파일 예시 (Python):

```python
import struct
values = [0.5, -7.0, 2.25, 3.0, 10.0]
open("features.bin", "wb").write(struct.pack(f"<{len(values)}f", *values))
```

## 4. 빌드·실행·검증

- CLI 명령어 기반 ISC 모듈 빌드, 실행, 검증 절차는 다음과 같음
```text
check → reference(hw_emu) → build → device_wait → device_open → execute → compare → record
```

1. **`compare`는 CPU 결과가 아니라 `hw_emu` 결과와 비교하는 단계입니다.** `--target hw`로 실행하면 hw 빌드 **전에** 같은 입력을 `hw_emu`(카드 없이 하는 시뮬레이션)로 먼저 돌리고(`reference`), 실제 카드 출력이 나오면 두 출력을 바이트 단위로 비교합니다(`compare`).
   - 이 비교로 알 수 있는 것: 실제 하드웨어가 시뮬레이션과 똑같이 동작하는지 (타이밍·메모리 접근 문제 등). `hw_emu`가 실패하면 수십 분 걸리는 hw 빌드를 시작하지 않는 효과도 있습니다.
   - 알 수 없는 것: 로직이 **의도대로** 계산하는지. 로직이 틀리면 두 출력이 똑같이 틀리므로 통과합니다. 정답 확인은 각 연산 문서의 결과 확인 단계(`zlib.crc32` 대조, `baseline.py` 대조 등)로 따로 합니다.
   - `--target hw_emu`(기본값)로 실행하면 비교할 대상이 없어 `compare`는 건너뜁니다.
2. 이 방식(`validation.mode: cross_target`)은 연산별 설정이며, 현재 모든 연산이 이 방식을 씁니다. CPU 결과와 바이트 비교하는 `cpu_baseline` 방식도 있지만, CPU 기준선이 장치와 바이트까지 같은 결과를 내는 연산(`baseline.equivalence: exact`)에서만 쓸 수 있습니다.
3. `--target`을 생략하면 연산이 선언한 첫 타깃으로 실행합니다(현재 모든 연산이 `hw_emu`).
4. 빌드 결과는 지문(연산·버전·빌드 파라미터·타깃·사용자 코드)별로 캐시되어 재사용됩니다.

- **연산 선택형 예제**

 ```bash
isc new compressor --operation lz4 --set block_size=65536
isc check compressor
isc run compressor --input [입력 파일]               # 기본 타깃 (hw_emu)
isc run compressor --input [입력 파일] --target hw
isc records --module compressor                      # 실행 기록 (--csv 로 내보내기)
```

- **Hook형 예제** (모듈을 따로 만들지 않고 로직 파일로 바로 실행)

```bash
isc template filter --type f32 -o clip_filter.hpp    # return 줄 수정
isc run clip_filter.hpp --input features.bin         # modules/clip_filter/ 자동 생성 후 hw_emu 실행
isc run clip_filter.hpp --input features.bin --target hw
```

- **진행 확인**: 다른 터미널에서 `isc status`(최근 작업)와 `isc watch [실행 ID]`(단계별 진행)를 쓰거나, 실행 시 `--progress`를 붙입니다.

```text
artifacts/cache/<지문>/     빌드 결과 (합성 실패 시 생성된 소스와 build.log 확인)
artifacts/outputs/          실행 출력
artifacts/records.jsonl     실행 기록
artifacts/events/           실행 단계 이벤트
```

## 5. 가속 효과 검증

모듈을 FPGA에서 실행하는 것이 CPU보다 실제로 빠른지 측정하고 판정하는 기능입니다. 비교할 모듈과
입력 크기를 정의하고(`isc plan`), 같은 조건에서 FPGA와 CPU 기준선의 실행 시간을 반복 측정한 뒤(`isc bench`),
속도 향상과 가속 가능 여부를 판정합니다(`isc report`).

```bash
isc plan [실험 이름] --modules [모듈1],[모듈2] --sizes 1MB,16MB --target hw
isc bench [실험 이름]
isc report [실험 이름]
```

- **CPU 기준선은 사용자가 작성하지 않습니다.** 연산마다 플랫폼이 CPU 구현을 하나씩 갖고 있습니다(`isc/operations/<연산>/baseline.py`, 예: `crc32` → `zlib.crc32`). `isc describe [연산 이름]`의 `baseline` 줄에서 확인합니다.
- 그래서 가속 판정(speedup)은 **기준선이 있는 연산 선택형 모듈에서만** 나옵니다.

| CPU 기준선 | 연산 |
|---|---|
| 있음 (speedup 계산) | `lz4` `crc32` `adler32` `graph_twohop` `graph_renumber` `mc_european` `vector_topk` (검증 미완료: `sha256` `identity` `image_gaussian` `image_resize` `image_sobel` `gemv_i32`) |
| 없음 (FPGA 시간만 측정) | `snappy_compress` `image_bicubic`, Hook형 `filter` `map` `aggregate` |

- Hook형은 사용자 로직마다 계산이 달라 플랫폼이 미리 CPU 구현을 둘 수 없습니다. 현재는 FPGA 실행 시간만 측정되고 speedup은 나오지 않습니다.
- `mc_european`의 기준선은 Monte Carlo가 아닌 Black-Scholes 해석해라서, speedup은 같은 알고리즘끼리의 비교가 아닙니다. 참고용으로만 봅니다.

## 6. 사용자 유의사항

| 메시지 | 해결 |
|---|---|
| `invalid module id ...` | 소문자로 시작, 소문자·숫자·밑줄만 사용 |
| `... requires an environment variable for include path '${VITIS_LIBRARIES}/...'` | `VITIS_LIBRARIES` 설정 |
| ``unsupported type `...` for the parameter of ...`` | 지원 타입으로 변경 |
| `hook source contains unsupported HLS interface pragmas` | `#pragma HLS INTERFACE` 삭제 |
| `hook source contains unsupported dynamic allocation (or host I/O)` | `new`, `malloc`, `fopen` 등 삭제 |
| `no hook profile matches this logic file` | 함수 이름을 `isc_filter`/`isc_map`/`isc_where`로 맞춤 |
| `warning: ... still has the starter marker ISC-TODO` | `return` 줄 작성 후 표식 삭제 |


1. `isc check`에서 `problem`이 하나라도 있으면 빌드·실행이 진행되지 않습니다
2. 로직을 고치는 동안에는 `hw_emu`로 확인하고 마지막에 `--target hw`로 실행합니다. 상수만 바꿔도 재빌드됩니다.
3. 현재 기본 검증은 `hw`와 `hw_emu` 결과의 일치만 확인합니다([4절](#4-빌드실행검증) 참고). 로직이 의도대로 동작하는지는 작은 입력으로 직접 확인합니다.
4. Hook 입력 크기가 값 크기의 배수가 아니면 남는 끝 바이트는 오류 없이 무시됩니다.
5. 장치는 한 번에 한 실행만 씁니다. 사용 중이면 기다리며, `--device-timeout SEC`로 대기 시간을 제한합니다.
6. Hook 검사는 보안 샌드박스가 아닙니다. 신뢰하는 소스만 빌드합니다.

## 7. 지원 도구

| 구분 | 명령 |
|---|---|
| 환경 | `isc doctor`, `isc devices` |
| 연산 선택형 | `isc operations`, `isc describe <연산>`, `isc new <id> --operation <연산>`, 사용자 로직: `isc template <연산> -o 파일.hpp`, `isc new <id> --operation <연산> --logic [파일.hpp]` |
| Hook형 | `isc profiles`, `isc describe-profile <프로파일>`, `isc template <프로파일> -o 파일.hpp`, `isc new-custom <id> --profile <프로파일>`, `isc new-custom --from 파일.hpp` |
| 모듈 관리 | `isc list`, `isc inspect <id>`, `isc check <id>` |
| 빌드·실행 | `isc build <id>`, `isc run <id 또는 파일.hpp> --input FILE`, `isc validate <id>` |
| 기록·진행 | `isc records`, `isc show <run-id>`, `isc status`, `isc watch <run-id>` |
| 가속 검증 | `isc plan`, `isc experiments`, `isc bench <id>`, `isc report <id>` |
- 모든 명령은 `--json`을 통해 결과 출력이 가능합니다.


| 옵션 | 명령 | 기능 |
|---|---|---|
| `--set 파라미터=값` | `new` | 연산 파라미터 지정 |
| `--type T`, `--out-type U` | `template`, `new-custom` | Hook 입력·출력 타입 지정 |
| `--from 파일.hpp` | `new-custom` | 작성한 로직 파일로 모듈 생성 |
| `--target hw_emu\|hw` | `build`, `run` | 실행 대상 (기본은 연산이 선언한 첫 타깃, 현재 `hw_emu`) |
| `--input FILE`, `--output FILE` | `run` | 입력·출력 파일 |
| `--no-validate` | `run` | 결과 검증 생략 |
| `--device-timeout SEC` | `run`, `bench` | 장치 대기 시간 제한 |
| `--progress` | `build`, `run`, `bench` | 진행 단계를 터미널에 출력 |

- 주로 사용되는 옵션은 다음과 같으며, 나머지 옵션은 `isc [명령] --help`로 확인합니다.

## 8. 라이브러리/연산 활용 템플릿
- 유저가 연산 응용에 활용할 수 있는 라이브러리 - 연산별 템플릿을 제공합니다.
- 연산별 사용 예시는 아래와 같은 순서로 정리되어 있으며, 관련 경로(`isc/operations/<연산>/README.md`)에서 확인 가능합니다.
1. 연산 예시 (1.1 기존 방식의 CPU 코드, 1.2 ISC 전주기 시스템 기반의 방식, 입력·출력)
2. 개발 절차 (2.1 입력 준비, 2.2 모듈 만들기, 2.3 로직 작성, 2.4 검사) — 단계마다 사용자가 할 일을 명시
3. 실행 방법 (에뮬레이션 실행, 결과 확인, FPGA 카드 실행, 실행 기록의 CLI 명령)
- 검증 기록은 각 문서 맨 위 표의 링크(`verification/u50_vitis_libraries/results/<연산>.md`)에서 확인합니다.
- 모든 연산 문서(18개)가 이 순서로 정리되어 있습니다. 각 문서는 입력 생성 스크립트(`examples/make_input.py`)와 결과 확인 스크립트(`examples/check_output.py`)의 전체 코드와 고치는 곳을 함께 보여 줍니다.


### 8.2. 연산별 예시 목록

| 연산 (라이브러리) | 예시 시나리오 | 만드는 모듈 | 사용자가 작성하는 파일 | 입력 → 출력 | 결과 확인 기준 |
|---|---|---|---|---|---|
| [`lz4`](isc/operations/lz4/README.md) (Data Compression) | 접근 로그 압축 보관 | `log_lz4` | 없음 (`block_size` 지정) | 로그 파일 → 블록 헤더 + LZ4 블록 | `lz4.block`으로 풀어 원본과 동일, 압축률 |
| [`crc32`](isc/operations/crc32/README.md) (Data Compression) | 적재 파일 무결성 검사 | `lake_crc` | 없음 | 256 KiB 파일 → 4바이트 (big-endian) | `zlib.crc32`와 동일 |
| [`adler32`](isc/operations/adler32/README.md) (Data Compression) | 파일 동기화용 블록 체크섬 | `transfer_adler` | 없음 | 256 KiB 블록 → 4바이트 (big-endian) | `zlib.adler32`와 동일 |
| [`snappy_compress`](isc/operations/snappy_compress/README.md) (Data Compression) | Kafka 이벤트 메시지 묶음 압축 | `kafka_snappy` | 없음 | 이벤트 묶음 200 KB → 블록 헤더 + Snappy 블록 | 풀어서 원본과 동일 (`check_output.py`) |
| [`graph_twohop`](isc/operations/graph_twohop/README.md) (Graph) | 금융 이상거래 탐지: 공유 기기 수로 위험 점수 | `fraud_link` | `logic.hpp` (`isc_score`의 `return` 줄) | 계좌 800·기기 1,200 그래프 + 500쌍 → 쌍별 위험 점수 | `baseline.py` 결과에 같은 규칙을 적용한 값과 동일 |
| [`graph_renumber`](isc/operations/graph_renumber/README.md) (Graph) | 원본 ID 연속 번호화 | `member_renumber` | 없음 | ID 5,000개 → 묶음 수 + 새 번호 | `baseline.py`와 동일, 일대일 대응 |
| [`image_bicubic`](isc/operations/image_bicubic/README.md) (Codec) | 학습 이미지 일괄 축소 | `train_resize` | 없음 (`out_rows`·`out_cols` 지정) | 512×512 회색조 → 256×256 | 같은 커널의 C 시뮬레이션과 동일 |
| [`mc_european`](isc/operations/mc_european/README.md) (Quantitative Finance) | 보유 옵션 가격 재계산 | `option_book` | 없음 | 옵션 8건 → 가격 8개 | Black-Scholes 대비 `\|d\|/S` ≤ 1% (공식 판정식) |
| [`filter`](isc/operations/filter/README.md) (플랫폼 Hook) | 금액 1,000 이상 5,000 미만 주문 추출 | `amount_filter` | `amount_filter.hpp` (`return` 한 줄) | u32 10,000개 → 조건을 만족한 u32 | Python 필터와 동일 |
| [`map`](isc/operations/map/README.md) (플랫폼 Hook) | 온도(f32)를 0.5 °C 단위 1바이트 코드로 변환 | `temp_code` | `temp_code.hpp` (`return` 한 줄) | f32 10,000개 → u8 10,000개 | Python 변환과 동일 |
| [`aggregate`](isc/operations/aggregate/README.md) (플랫폼 Hook) | 1,000 미만 거래(환불 포함)의 건수·합계·최소·최대 | `small_tx` | `small_tx.hpp` (`return` 한 줄) | i32 10,000개 → 32바이트 | Python 집계와 동일 |
| [`vector_topk`](isc/operations/vector_topk/README.md) (플랫폼 커널) | 문서 임베딩 상위 5개 검색 | `doc_search` | 없음 (`k=5` 지정) | 2,000×64 int8 → `(id, score)` 5쌍 | numpy 내적·정렬과 동일 |
| [`identity`](isc/operations/identity/README.md) (플랫폼 커널) | 설치 직후 실행 경로 점검 | `env_check` | 없음 | 텍스트 파일 → 같은 바이트 | `cmp`로 원본과 동일 |
