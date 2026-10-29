# ISC Management Platform

## 개요

ISC가 제공하는 FPGA 연산을 선택해 모듈을 만들고 **검사 → 빌드 → 실행 → 검증 →
기록**하는 로컬 관리 플랫폼입니다. 사용자는 HLS 커널, AXI 인터페이스, 메모리 뱅크,
XRT 어댑터를 작성하지 않습니다. 일반 사용자는 플랫폼에서 제공하는 연산과 파라미터를
선택합니다. 제한된 사용자 정의가 필요하면 Hook 프로파일이 정한 함수 본문만 작성하고,
최상위 커널과 데이터 이동 코드는 플랫폼에 맡깁니다. 현재 범위는 단일 노드 로컬 실행이며
AMD Alveo U50/XRT 환경을 대상으로 합니다.

## 목차

- [1. 설치 및 환경 구성](#1-설치-및-환경-구성)
- [2. 모듈 개발 가이드라인](#2-모듈-개발-가이드라인)
- [3. 모듈 예제 및 실행 방법](#3-모듈-예제-및-실행-방법)
- [4. 가속 효과 검증](#4-가속-효과-검증)
- [5. 명령어 참고](#5-명령어-참고)

## 1. 설치 및 환경 구성

### 1.1. ISC 플랫폼 설치

```bash
git clone https://github.com/Adv-FileSystem/ISC_Management_Platform.git
cd ISC_Management_Platform
python3 -m pip install -e .
isc doctor
```

- 설치하지 않고 실행하려면 저장소 루트에서 `isc` 대신 `python3 -m isc`를 사용합니다.
- `isc doctor` 명령어를 통해 현재 개발 환경에서 사용 가능한 FPGA 카드가 있는지 조회합니다.

### 1.2. ISC 지원 환경

| 항목              | 요구 버전                                                          |
| --------------- | -------------------------------------------------------------- |
| 운영체제            | `Ubuntu Server 22.04.1 LTS`                                    |
| Linux 커널        | `5.15.0-50-generic` GA 커널, HWE 제외                              |
| Python          | `>= 3.10`                                                      |
| PyYAML          | `>= 5.4`                                                       |
| XRT             | `2023.1` (`2.15.225`)                                          |
| Vitis           | `2025.1`                                                       |
| Vitis Libraries | `2025.1`, Vitis Libraries 기반 연산 빌드에 필요                         |
| CPU             | `x86_64`                                                       |
| FPGA            | AMD Alveo U50                                                  |
| 개발 플랫폼          | `xilinx_u50_gen3x16_xdma_5_202210_1`                           |
| 실제 장치 실행        | `xilinx-u50-gen3x16-xdma_2022.2_2022_1015_0317-all.deb.tar.gz` |
| 빌드·에뮬레이션        | `xilinx-u50-gen3x16-xdma-5-202210-1-dev_1-3499627_all.deb`     |

- [U50 플랫폼 명세](https://docs.amd.com/r/en-US/ug1120-alveo-platforms/U50-Gen3x16-XDMA-base_5-Platform)
- [XRT 지원 OS](https://docs.amd.com/r/2023.1-English/ug1451-xrt-release-notes/XRT-Operating-System-Support)
- [U50 패키지 다운로드](https://www.amd.com/en/support/downloads/alveo-previous-downloads.html/accelerators/alveo/u50.html)

### 1.3. 설치 상태 확인

```bash
uname -m                         # x86_64
lsb_release -ds                  # Ubuntu 22.04.1 LTS
uname -r                         # 5.15.0-50-generic
python3 --version                # 3.10 이상
source /opt/xilinx/xrt/setup.sh
xbutil examine                   # XRT 2.15.225 / Branch 2023.1
python3 -c 'import pyxrt, yaml; print("runtime: ok")'
```

## 2. 모듈 개발 가이드라인

사용자가 선택한 ISC 연산에 따라 기본 스켈레톤 구조가 제공됩니다. 대괄호(`[ ]`)로
표시한 부분은 사용자가 실제 값으로 바꿉니다.

### 2.1. 지원 연산

ISC 개발 플랫폼에서 지원되는 연산은 다음과 같습니다.

| 연산 | 기능 | 사용자 설정 |
|---|---|---|
| `adler32` | 파일의 Adler-32 체크섬 계산 | 없음 |
| `crc32` | 파일의 표준 CRC-32 체크섬 계산 | 없음 |
| `identity` | 입력을 변경 없이 복사 | 없음 |
| `lz4` | 파일을 블록 단위로 압축 | `block_size`: 1024–65536, 기본 65536 |
| `sha256` | 파일의 SHA-256 다이제스트 계산 | 없음 |
| `vector_topk` | 질의 벡터와 int8 행렬의 정수 내적 상위 k개 검색 (RAG 검색 단계의 검증용) | `k`: 1–16, 기본 10; `chunk_bytes`: 4096–134217728, 기본 16777216 |

```bash
isc operations
isc describe [연산 이름]
```

- 파라미터는 값을 바꿀 때 재빌드가 필요한 것과 필요 없는 것으로 나뉩니다.
`isc describe`는 재빌드가 필요한 파라미터에 `[rebuild required]`를 표시합니다.
`lz4`의 `block_size`는 호스트 어댑터만 읽으므로 재빌드 없이 값을 바꿀 수 있습니다.
- 모든 연산은 입력 파일을 원시 바이트(byte)로 받습니다. `vector_topk`만 그 바이트에 구조(헤더 + 행렬)가 있습니다.
- CRC-32와 Adler-32는 최소 1바이트 입력이 필요하며 각각 4바이트 big-endian 결과를 출력합니다.
- SHA-256은 빈 파일도 지원하고 32바이트 다이제스트를 출력합니다.
- `identity`와 `vector_topk`를 제외한 연산의 신규 빌드에는 `VITIS_LIBRARIES` 환경변수가 필요합니다.
- `vector_topk`의 `k`와 `chunk_bytes`는 호스트 어댑터만 읽으므로 값을 바꿔도 재빌드하지 않습니다.

`vector_topk`는 구조 있는 입력(`isc_vectors_i8`)을 받습니다. 리틀 엔디안이고 행 우선입니다.

| 위치 | 내용 |
|---|---|
| `u32 rows` | 데이터베이스 벡터 수 |
| `u32 dim` | 차원, 1–2048 |
| `i8 query[dim]` | 질의 벡터 |
| `i8 db[rows][dim]` | 데이터베이스 |

출력(`isc_topk_pairs`)은 `k`개의 `(u32 id, i32 score)`입니다. 점수는 정수 내적이고 순위는 점수 내림차순,
같으면 id 오름차순이며, 모자란 칸은 `id = 0xFFFFFFFF`, `score = -2147483648`입니다. 부동소수점이 없고
순위가 전순서라서 `hw_emu`, `hw`, CPU 기준선이 같은 바이트를 내야 하고, `hw` 실행은 매번 `hw_emu`
출력과 바이트 대조합니다(CPU 기준선과의 일치는 단위 테스트와 인수 절차에서 확인합니다). 헤더가 말하는
크기와 실제 크기가 다른 입력은 빌드를 시작하기 전에 거절합니다.

`hw` 실행의 기본 검증(`cross_target`)은 같은 입력으로 `hw_emu`를 먼저 돌리므로 검증할 입력은 작게
잡습니다(예: 4096 × 128, 약 0.5MB). 4096 × 128에서 `hw_emu` 빌드는 약 3분, 실행은 약 25초입니다
(2026-10-06, U50 플랫폼 `xilinx_u50_gen3x16_xdma_5_202210_1`). 큰 데이터베이스는
`isc run --no-validate`로 돌리고 출력을 기준선과 직접 대조합니다. 합성 입력은 다음 명령으로 만듭니다.

```bash
python3 scripts/make_vector_input.py --rows 4096 --dim 128 --seed 1 --plant 8 -o datasets/vec_4k_128.bin
```

`--plant 8`은 질의에 잡음을 더한 근접 복사본 8개를 데이터베이스에 심고 그 id를 stderr에 알립니다.
순서와 무관하게 검색 결과의 앞 8개가 심은 id들과 같아야 합니다(`--plant` ≤ `k`이고 dim이 32 이상일 때.
심은 id는 id 순으로 알리고 결과는 점수 순이라 집합으로 비교하며, dim이 16 이하이면 잡음 때문에 자주
어긋납니다).

### 2.2. 스켈레톤 구조

```bash
isc new [모듈 이름] --operation [연산 이름]
```

- `[모듈 이름]`은 영문 소문자로 시작하고 소문자·숫자·밑줄만 사용합니다.
- 연산 파라미터가 있으면 `--set [파라미터 이름]=[값]`을 추가합니다. 생성되는
스켈레톤은 다음 두 파일로 구성됩니다.

```text
modules/[모듈 이름]/
├── module.yaml  # 사용자가 설정하는 모듈 명세
└── README.md    # 생성된 모듈의 사용 안내
```

- 커널, 어댑터, 메모리와 빌드 설정은 ISC 내부 연산 패키지가 관리하므로 스켈레톤에
포함되지 않습니다.

### 2.3. 사용자 수정 범위

생성 후에는 `module.yaml`에서 다음 세 항목만 수정할 수 있습니다.

| 수정 항목 | 내용 |
|---|---|
| `module.name` | 화면에 표시할 이름 |
| `module.version` | 모듈 버전, `MAJOR.MINOR.PATCH` 권장 |
| `parameters` | `isc describe [연산 이름]`에 표시된 설정 |

- 모듈 생성 시 지정된 `module.id`, `operation.id`, `operation.version`은 변경하지 않습니다.
- 필요한 동작은 연산 파라미터를 통해 설정합니다.

### 2.4. 사용자 Hook 프로파일

내장 연산의 전체 구조를 바꾸지 않고 작은 계산만 사용자 코드로 쓰려면 Hook 프로파일을
사용합니다. 사용자는 **함수 하나**만 쓰고, 래퍼 커널·메모리 배치·호스트 어댑터는 플랫폼이
함수의 타입에 맞춰 생성합니다.

| 프로파일 | 사용자가 쓰는 함수 | 입력 | 출력 |
|---|---|---|---|
| `filter` | `bool isc_filter(T value)` | T 값의 나열 | `true`를 돌려준 값만, 입력 순서대로 |
| `map` | `U isc_map(T value)` | T 값의 나열 | 변환된 값(U), 개수 동일 |
| `aggregate` | `bool isc_where(T value)` | 정수 T 값의 나열 | 32바이트: `count, sum, min, max` |

- 타입 T, U는 `u8 u16 u32 u64 i8 i16 i32 i64 f32`이고 `ap_uint<32>`, `uint32_t`, `float` 같은
  표기를 모두 쓸 수 있습니다. `aggregate`는 정수 타입만 받습니다.
- `aggregate`의 출력은 little-endian `u64` 네 개입니다. 부호 있는 타입의 `sum`은 64비트로
  감기고 `min`과 `max`는 부호 확장됩니다. 조건을 만족한 값이 없으면 모두 0입니다.
- 입력 파일은 T 값이 little-endian으로 이어진 바이트열이어야 합니다.
- 이전 이름 `table_filter_u32`는 `filter`의 별칭으로 남아 있고, 기존 모듈은 그대로 동작합니다.

```bash
isc profiles                                          # 프로파일 목록
isc describe-profile filter                           # 시그니처와 지원 타입
isc template filter --type f32 -o clip_filter.hpp     # 타입이 채워진 시작 파일 받기
isc template map --type f32 --out-type u8 -o quantize.hpp
```

시작 파일에는 입력·출력 형식, 예시, 쓸 수 없는 것이 주석으로 들어 있습니다. 사용자는
`return` 줄만 고칩니다.

```cpp
inline bool isc_filter(float value) {
    return value >= -3.0f && value <= 3.0f;
}
```

`isc check`는 함수 시그니처(주석은 제외), 모듈 밖을 가리키는 소스 경로, 동적 메모리·호스트
입출력 및 사용자 AXI 인터페이스 pragma를 검사합니다. 시작 파일의 `ISC-TODO` 표식이 남아
있으면 경고합니다. 실제 HLS 합성 가능성은 `isc build`의 Vitis 컴파일 단계에서 최종 확인됩니다.
빌드할 때 플랫폼은 생성한 래퍼 커널과 `logic.hpp`를 캐시 디렉터리(`artifacts/cache/<지문>/`)에
함께 배치하며, 사용자 코드나 타입이 바뀌면 지문이 달라져 이전 바이너리를 재사용하지 않습니다.
합성 오류가 나면 그곳의 생성된 소스를 직접 볼 수 있고, 오류 메시지 끝에 그 경로가 붙습니다.

모듈을 먼저 만들 수도 있습니다.

```bash
isc new-custom price_filter --profile filter --type f32
```

Hook 검사는 합성 실수를 빠르게 찾는 사전 검사이며 보안 샌드박스가 아닙니다. Hook 빌드는
로컬 사용자가 신뢰하는 소스만 대상으로 하며, 외부에서 받은 코드를 실행해야 한다면 별도의
격리된 빌드 환경이 필요합니다.

### 2.5. 로직 파일만 쓰고 바로 실행

모듈을 먼저 만들지 않고, 함수 하나를 담은 로직 파일만 써서 바로 실행할 수 있습니다.

```bash
isc template filter --type f32 -o clip_filter.hpp     # 시작 파일 (이미 있으면 덮어쓰지 않습니다)
# clip_filter.hpp 의 return 줄을 조건으로 바꾼다
isc run clip_filter.hpp --input features.bin          # hw_emu 에서 실행 (기본)
isc run clip_filter.hpp --input features.bin --target hw
```

`isc run`은 파일에 정의된 함수 이름으로 프로파일을 알아내고, 시그니처에서 타입을 읽고, 파일
이름(`clip_filter`)을 모듈 id로 삼아 `modules/clip_filter/`를 만들거나 갱신합니다. 이어서
`isc check`와 같은 정적 검사를 통과해야만 빌드, 실행, 검증, 기록을 수행하므로 잘못된 로직
때문에 긴 빌드를 기다리지 않습니다. 이 검사는 `isc run <모듈 id>`에도 똑같이 적용됩니다.

- 로직 파일이 원본입니다. 같은 파일로 다시 실행하면 모듈의 `logic.hpp`가 그 내용으로 갱신됩니다.
  내용이 바뀌었으면 빌드 지문이 달라져 다시 빌드하고, 바뀌지 않았으면 빌드 결과를 재사용합니다.
  `modules/` 안의 `logic.hpp` 자체를 넘기면 그 모듈을 그대로 실행합니다.
- 모듈만 만들고 실행은 나중에 하려면 `isc new-custom --from clip_filter.hpp`를 씁니다.
  파일 이름이 모듈 id 규칙(소문자로 시작, 소문자·숫자·밑줄)에 맞지 않으면
  `isc new-custom clip --from Clip-Filter.hpp`처럼 id를 직접 줍니다.
- 이미 있는 모듈 id가 다른 종류의 모듈이면 덮어쓰지 않고 오류를 냅니다.
- 현재 읽을 수 있는 로직 파일은 `.hpp`(HLS C++ 함수)입니다.
- 로직이 바뀔 때마다 재빌드가 필요하고 `hw` 빌드는 오래 걸리므로, 로직을 고치는 동안에는 기본 타깃인
  `hw_emu`로 확인한 뒤 마지막에 `--target hw`로 실행하는 것을 권장합니다. 임계값 같은 상수를
  바꿔도 재빌드됩니다(런타임 파라미터는 아직 없습니다).
- `--target hw`로 실행하면 ISC가 hw 이미지를 빌드하기 **전에** 같은 입력을 `hw_emu`로 먼저 실행합니다.
  로직이나 합성에 문제가 있으면 수십 분짜리 `hw` 빌드를 시작하기 전에 알 수 있고, `hw` 실행이 끝난 뒤에
  두 출력을 비교합니다.
- 검증은 `hw`와 `hw_emu` 결과의 일치만 확인합니다. 조건이 의도한 대로 동작하는지는 이 검증이 대신하지
  못합니다. Hook에는 CPU 기준선이 없어 `isc bench`의 가속 판정도 나오지 않습니다(시간 측정은 됩니다).
- 생성 코드를 CPU에서 검사하려면 Vitis의 `ap_int.h`와 `g++`가 필요합니다
  (`ISC_HLS_INCLUDE=<ap_int.h가 있는 디렉터리> python3 -m unittest discover -s tests`).

## 3. 모듈 예제 및 실행 방법

다음은 LZ4 기반 `compressor` 모듈을 생성하고 에뮬레이션과 실제 장치에서 실행한 뒤
결과와 기록을 확인하는 예제입니다.

### 1단계: 모듈 생성

먼저 연산의 입력·출력과 파라미터를 확인하고 모듈을 생성합니다.

```bash
isc describe lz4
isc new compressor --operation lz4 --set block_size=65536
```

생성된 `modules/compressor/module.yaml`은 다음과 같습니다.

```yaml
module:
  id: compressor
  name: Document Compressor
  version: "0.1.0"
operation:
  id: lz4
  version: "1.0"
parameters:
  block_size: 65536
```

### 2단계: 설정 및 환경 확인

적용된 설정과 실행 환경을 검사합니다.

```bash
isc inspect compressor
isc check compressor
```

### 3단계: 모듈 실행

먼저 `hw_emu`에서 실행한 후 같은 입력을 사용해 `hw`에서 실행합니다. 아래 예제는
빌드 과정을 명시적으로 보여줍니다. `isc run`은 빌드 결과가 없으면 자동으로 빌드합니다.

```bash
isc build compressor --target hw_emu
isc run compressor --input [입력 파일] --target hw_emu
isc build compressor --target hw
isc run compressor --input [입력 파일] --target hw
```

입력은 확장자와 관계없이 파일 경로로 지정하며, ISC는 파일 전체를 원시 바이트로
전달합니다. `hw` 실행 시 ISC는 먼저(hw 이미지를 빌드하기 전에) 같은 입력을 `hw_emu`로
실행하고, `hw` 출력이 나오면 그 결과와 바이트 단위로 비교합니다. `hw_emu` 실행이 실패하면
`hw` 빌드는 시작되지 않습니다.

장치는 한 번에 한 실행만 쓸 수 있습니다. 다른 실행이 같은 장치를 쓰고 있으면 끝날 때까지
기다리고, 기다리는 동안 누가 쓰고 있는지(`run_id`, `pid`, 경과 시간)를 stderr 로 알립니다.
`--device-timeout SEC` 를 주면 그 시간이 지나도 장치를 얻지 못할 때 실행이 실패합니다(기본은
기다림). 기다린 시간은 `device_wait_ms`, 장치를 열고 이미지를 올린 시간은 `device_open_ms`
로 기록되고, `total_ms` 에는 둘 다 포함됩니다. 에뮬레이션(`hw_emu`)과 실물(`hw`)은 서로 다른
예약이라 기다리지 않습니다. 먼저 온 순서는 보장하지 않으며, 예약은 같은 PC 의 로컬 디스크에
있는 파일 락(`artifacts/locks/`)이라 네트워크 파일시스템에서는 믿을 수 없습니다.

### 4단계: 산출물 및 실행 기록

빌드 결과, 실행 출력과 기록은 다음 위치에 저장됩니다.

```text
artifacts/cache/<fingerprint>/   연산·버전·빌드 파라미터·타깃별 빌드 결과
artifacts/outputs/               기본 실행 출력 위치
artifacts/records.jsonl          실행 기록
artifacts/locks/                 장치 예약 파일 (쓰는 중인 실행이 한 줄 JSON 으로 적혀 있음)
```

같은 연산 버전, 빌드 파라미터, 타깃과 툴체인으로 만든 모듈은 같은 빌드 결과를
재사용합니다. `block_size`처럼 호스트에서만 쓰이는 파라미터는 값이 서로 달라도
장치 바이너리가 같으므로 빌드를 함께 재사용합니다.
실행 기록은 다음 명령으로 확인하거나 CSV 파일로 내보낼 수 있습니다.

```bash
isc records --module compressor
isc records --module compressor --csv > runs.csv
isc show [실행 ID]
```

### 진행 중인 작업 확인

hw 빌드는 약 40분이 걸립니다. 실행이 오래 걸릴 때는 다른 터미널에서 어디까지 왔는지 볼 수 있습니다.

```bash
isc status                 # 최근 작업: 상태, 지금 단계, 경과 시간, 마지막 진행
isc watch [실행 ID]        # 그 작업의 단계와 진행을 끝날 때까지 따라감
isc run compressor --input [입력 파일] --target hw --progress   # 실행하는 터미널의 stderr 로 같은 내용을 출력
```

`isc run`, `isc build`, `isc bench`는 시작할 때 실행 ID를 갖고, 단계가 바뀔 때마다
`artifacts/events/<실행 ID>.jsonl`에 한 줄씩 남깁니다(`isc build`는 `records.jsonl`에는 쓰지 않습니다).
`isc run`의 단계는 `check → reference → build → device_wait → device_open → execute → compare → record`
이고, 건너뛴 단계는 `skipped`로 남습니다. `hw` 실행에서 `reference`(hw_emu)가 실패하면 `build` 이후
단계는 아예 나오지 않습니다.

- v++ 빌드 중에는 v++가 직접 알려 주는 값만 진행률로 냅니다(합성 `N of M jobs`, 구현 `N of 6 tasks`).
  퍼센트를 지어내지 않습니다. 남은 시간(`eta_s`)은 같은 연산·타깃의 이전 빌드에서 그 단계가 걸린 시간
  (`artifacts/build_durations.jsonl`)의 최근 5건 중앙값에서 지금까지 흐른 시간을 뺀 **추정**이고,
  이력이 없으면 나오지 않습니다.
- 실행하는 프로세스가 사라졌는데 `finished`가 없고 90초 넘게 조용하면 `isc status`가 그 작업을
  `lost`로 표시합니다(같은 PC에서 돈 작업만 판정합니다).
- 이벤트 파일은 한 줄이 JSON 하나인 추가 전용 파일입니다(스키마 v1: 필드는 추가만 되고, 모르는 종류는
  무시하면 됩니다). `tail -f`나 다른 도구로 읽을 수 있습니다.
- 이벤트를 쓰지 못해도 작업은 실패하지 않습니다(처음 한 번 경고만 냅니다).
- `isc build`는 이제 v++ 출력을 흘려 읽어 `build.log`에 **빌드 도중** 기록하므로, 빌드가 실패해도 그때까지의
  로그가 남습니다.

## 4. 가속 효과 검증

모든 연산이 FPGA 에서 이득을 보지는 않습니다. 입력을 장치로 옮기고 결과를 받아오는
비용이 CPU 가 그 일을 전부 해치우는 시간보다 클 수 있고, 그런 연산은 커널을 아무리
최적화해도 이득이 없습니다. `plan → bench → report` 는 그 판단을 근거와 함께
내리기 위한 주기입니다.

```text
계획          측정            판정
isc plan  →  isc bench   →  isc report
```

### 4.1. 왜 `isc run` 을 반복하면 안 되는가

`isc run` 은 호출마다 디바이스를 열고 장치 이미지를 올립니다. 이 셋업은 수 초가
걸리는 1 회성 비용인데, 셸 루프로 N 번 돌면 그 비용을 N 번 다시 냅니다. 거기서 나온
수치로 CPU 와 비교하면 FPGA 는 언제나 집니다.

`isc bench` 는 모듈당 디바이스를 **한 번만** 열고 그 세션 안에서 반복 실행합니다.
오픈에 걸린 시간은 `device_open_ms` 로 따로 기록되어 표본에서 빠집니다. 장치가 다른 실행에
쓰이고 있으면 `isc run` 과 같이 기다리며(`--device-timeout` 으로 제한), 기다린 시간은
`device_wait_ms` 로만 남고 표본에는 들어가지 않습니다.

### 4.2. 1단계: 실험 정의

비교할 모듈과 입력 크기를 정합니다.

```bash
isc new copier    --operation identity
isc new hasher    --operation sha256
isc new compressor --operation lz4

isc plan accel_scan \
  --modules copier,hasher,compressor \
  --sizes 64KB,1MB,16MB,128MB \
  --target hw --repeat 10 --warmup 2
```

`experiments/accel_scan/experiment.yaml` 이 만들어집니다. 이 파일이 실험의
정의이자 재현 근거이므로 버전 관리에 포함하십시오.

```yaml
experiment:
  id: accel_scan
  name: accel_scan
matrix:
  modules: [copier, hasher, compressor]
  input_sizes: [64KB, 1MB, 16MB, 128MB]
  target: hw
measure:
  repeat: 10
  warmup: 2
  input_fill: text
  baseline: true
  verify: true
```

- `input_fill` 은 생성되는 입력의 성질입니다. `text`(압축 가능, 기본값),
`random`(비압축), `zeros`(최대 압축). 압축 연산의 결과는 여기에 크게 좌우되므로
기본값에 맡기지 말고 목적에 맞게 고르십시오.
- `target` 은 `hw` 여야 합니다. 에뮬레이션 시간은 성능 수치가 아니며,
`hw_emu` 로 측정하면 리포트가 경고를 붙입니다.

### 4.3. 2단계: 측정

```bash
isc bench accel_scan --dry-run   # 무엇을 재게 되는지 먼저 확인
isc bench accel_scan
```

각 셀(모듈 × 입력 크기)마다 `warmup` 회를 버리고 `repeat` 회를 측정하며, 같은
회차 규약으로 CPU 기준선도 함께 잽니다. 정확성은 셀마다 한 번만 확인합니다 —
성능 반복에 검증을 섞으면 측정이 오염됩니다.

원본 표본은 `artifacts/benchmarks/<실험>-<시각>.json` 에, 요약 한 줄은
`artifacts/records.jsonl` 에 남습니다.

### 4.4. 3단계: 판정

```bash
isc report accel_scan
isc report accel_scan --csv > accel_scan.csv
```

```text
MODULE         OP           INPUT     CPU ms    FPGA ms     KERNEL        H2D        D2H  SPEEDUP  CEILING V
------------------------------------------------------------------------------------------------------------
copier         identity       8MB      0.405      6.580      0.050      1.049      1.049    0.06x    0.19x +
hasher         sha256         8MB      3.833     10.194      0.050      1.049      0.000    0.38x    3.65x +
```

핵심은 마지막 `CEILING` 열입니다.

    CEILING = CPU 시간 / (H2D + D2H)

커널이 0 초에 끝난다고 가정한 **상한 가속비**입니다. 이 값이 1 미만이면 입력을
장치로 옮기는 것만으로 이미 CPU 전체 계산보다 느리다는 뜻이고, 그렇다면 커널을
아무리 개선해도 그 연산은 가속 대상이 아닙니다. 위 예에서 `identity` 는 상한이
0.19x 이므로 가속 불가로 단정할 수 있고, `sha256` 은 현재 0.38x 로 느리지만 상한이
3.65x 이므로 병목이 전송이 아니라 커널입니다.

리포트가 내리는 판정은 다섯 가지입니다.

| 판정 | 뜻 | 다음 행동 |
|---|---|---|
| 가속 이득 있음 | 모든 크기에서 1.2x 이상 | 상시 오프로드 |
| 조건부 이득 | 교차점 이상에서만 이득 | 크기로 분기해 큰 입력만 오프로드 |
| 커널 개선 여지 있음 | 지금은 느리지만 상한이 높음 | 커널 최적화에 투자 |
| 가속 불가 (전송 바운드) | 상한이 1 미만 | 대상에서 제외 |
| 판정 보류 | 기준선 없음 또는 정확성 실패 | 원인 해결 후 재측정 |

`V` 열은 정확성입니다. `+` 는 CPU 기준선과 일치, `-` 는 불일치, `.` 은 대조하지
않음입니다. 정확성이 깨진 셀이 있으면 성능 판정은 내려지지 않습니다.

### 4.5. CPU 기준선

각 연산은 CPU 참조 구현(`baseline.py`)을 함께 제공합니다.

| 연산 | CPU 기준선 | 정확성 대조 |
|---|---|---|
| `identity` | 메모리 복사 | 가능 |
| `crc32` | `zlib.crc32` | 가능 |
| `adler32` | `zlib.adler32` | 가능 |
| `sha256` | `hashlib.sha256` | 가능 |
| `vector_topk` | `numpy` 정수 내적 + `lexsort` (`pip install numpy` 필요) | 가능 |
| `lz4` | `lz4.block` (`pip install lz4` 필요) | 불가 |

`vector_topk`의 기준선은 정확성 대조(단위 테스트와 인수 절차)용이며 `isc bench` 대상이 아닙니다 — 벤치
입력은 구조 없는 바이트라 이 연산의 입력 검사가 거절합니다.

LZ4 는 CPU 구현과 커널이 서로 다른, 그러나 둘 다 올바른 블록을 냅니다. 그래서
성능 비교에만 쓰이고 바이트 대조에는 쓰이지 않습니다. 기준선에 필요한 패키지가
없으면 다른 알고리즘으로 대체하지 않고 해당 셀을 "기준선 없음"으로 남깁니다 —
틀린 speedup 을 내는 것보다 비교하지 않는 편이 낫습니다.

정확성 대조가 가능한 연산은 `hw` 실행 검증에도 이 기준선을 쓸 수 있습니다. 기본
검증 방식인 `cross_target` 은 `hw_emu` 를 별도 프로세스로 한 번 더 실행하므로
느립니다. 이 실행은 `hw` 빌드 전에 먼저 돌기 때문에, 실패하면 `hw` 빌드를 건너뜁니다.
연산이 `validation.mode: cpu_baseline` 을 선언하면 그 대신 CPU 참조와 대조하고, 그 기준선도
빌드 전에 먼저 계산합니다(기준선을 쓸 수 없으면 빌드 전에 멈춥니다).

## 5. 지원 명령어

| 명령                                     | 기능                |
| -------------------------------------- | ----------------- |
| `isc doctor`                           | 실행 환경과 장치 확인      |
| `isc devices`                          | 사용 가능한 장치 조회 (`--json` 은 장치마다 안정적인 `id` 포함) |
| `isc operations`                       | 지원 연산 목록 조회       |
| `isc describe <operation>`             | 연산의 입력·출력·파라미터 설명 |
| `isc new <id> --operation <operation>` | 연산 기반 사용자 모듈 생성   |
| `isc profiles`                         | 사용자 Hook 프로파일 목록 조회 |
| `isc describe-profile <profile>`       | Hook 함수·입출력 규격 설명  |
| `isc new-custom <id> --profile <profile>` | 제한된 사용자 Hook 모듈 생성 |
| `isc new-custom [id] --from 로직.hpp`  | 작성한 로직 파일로 Hook 모듈 생성·갱신 |
| `isc template <profile> [--type T] -o 파일.hpp` | 타입이 채워진 Hook 시작 파일만 쓰기 (모듈은 만들지 않음) |
| `isc list`                             | 생성된 모듈 목록         |
| `isc inspect <id>`                     | 모듈의 적용된 설정 확인     |
| `isc check <id>`                       | 모듈과 환경 정적 검사      |
| `isc build <id>`                       | 연산 커널 빌드 및 캐시     |
| `isc run <id 또는 로직.hpp> --input FILE` | 검사·빌드·실행·검증·기록 (`hw` 는 빌드 전에 `hw_emu` 로 먼저 실행, 로직 파일이면 모듈도 자동 생성) |
| `isc validate <id> ...`                | 기존 출력 재검증         |
| `isc records`, `isc show <run-id>`     | 실행 기록 조회          |
| `isc status`, `isc watch <run-id>`     | 진행 중인 작업의 단계·진행 조회 (`--progress`로 실행 중 stderr 출력) |
| `isc plan <id> --modules … --sizes …`  | 가속 검증 실험 정의       |
| `isc experiments`                      | 실험 목록 조회          |
| `isc bench <id>`                       | 실험 매트릭스 측정        |
| `isc report <id>`                      | speedup·교차점·판정 조회  |

모든 명령은 전역 `--json` 옵션으로 기계 판독 가능한 결과를 출력합니다.
