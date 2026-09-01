# ISC Management Platform

## 개요

ISC가 제공하는 FPGA 연산을 선택해 모듈을 만들고 **검사 → 빌드 → 실행 → 검증 →
기록**하는 로컬 관리 플랫폼입니다. 사용자는 HLS 커널, AXI 인터페이스, 메모리 뱅크,
XRT 어댑터를 작성하지 않습니다. 플랫폼에서 제공하는 연산을 선택하고, 해당 연산이
허용한 파라미터와 입력 파일만 지정합니다. 현재 범위는 단일 노드 로컬 실행이며 AMD
Alveo U50/XRT 환경을 대상으로 합니다.

## 목차

- [1. 설치 및 환경 구성](#1-설치-및-환경-구성)
- [2. 모듈 개발 가이드라인](#2-모듈-개발-가이드라인)
- [3. 모듈 예제 및 실행 방법](#3-모듈-예제-및-실행-방법)
- [4. 명령어 참고](#4-명령어-참고)

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

```bash
isc operations
isc describe [연산 이름]
```

- 모든 연산은 입력 파일을 원시 바이트(byte)로 받습니다.
- CRC-32와 Adler-32는 최소 1바이트 입력이 필요하며 각각 4바이트 big-endian 결과를 출력합니다.
- SHA-256은 빈 파일도 지원하고 32바이트 다이제스트를 출력합니다.
- `identity`를 제외한 연산의 신규 빌드에는 `VITIS_LIBRARIES` 환경변수가 필요합니다.

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
전달합니다. `hw` 실행 시 ISC는 기본적으로 같은 입력의 `hw_emu` 결과와 바이트 단위로
비교합니다.

### 4단계: 산출물 및 실행 기록

빌드 결과, 실행 출력과 기록은 다음 위치에 저장됩니다.

```text
artifacts/cache/<fingerprint>/   연산·버전·파라미터별 빌드 결과
artifacts/outputs/               기본 실행 출력 위치
artifacts/records.jsonl          실행 기록
```

같은 연산 버전, 파라미터, 타깃과 툴체인으로 만든 모듈은 같은 빌드 결과를 재사용합니다.
실행 기록은 다음 명령으로 확인하거나 CSV 파일로 내보낼 수 있습니다.

```bash
isc records --module compressor
isc records --module compressor --csv > runs.csv
isc show [실행 ID]
```

## 4. 지원 명령어

| 명령                                     | 기능                |
| -------------------------------------- | ----------------- |
| `isc doctor`                           | 실행 환경과 장치 확인      |
| `isc devices`                          | 사용 가능한 장치 조회      |
| `isc operations`                       | 지원 연산 목록 조회       |
| `isc describe <operation>`             | 연산의 입력·출력·파라미터 설명 |
| `isc new <id> --operation <operation>` | 연산 기반 사용자 모듈 생성   |
| `isc list`                             | 생성된 모듈 목록         |
| `isc inspect <id>`                     | 모듈의 적용된 설정 확인     |
| `isc check <id>`                       | 모듈과 환경 정적 검사      |
| `isc build <id>`                       | 연산 커널 빌드 및 캐시     |
| `isc run <id> --input FILE`            | 빌드·실행·검증·기록       |
| `isc validate <id> ...`                | 기존 출력 재검증         |
| `isc records`, `isc show <run-id>`     | 실행 기록 조회          |

모든 명령은 전역 `--json` 옵션으로 기계 판독 가능한 결과를 출력합니다.
