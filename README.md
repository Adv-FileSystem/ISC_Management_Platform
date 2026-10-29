# ISC Management Platform

## 개요

ISC가 제공하는 FPGA 연산으로 모듈을 만들고 **검사 → 빌드 → 실행 → 검증 → 기록**하는 로컬 관리
플랫폼입니다. 사용자는 HLS 커널, AXI 인터페이스, 메모리 뱅크, XRT 어댑터를 작성하지 않습니다.
현재 범위는 단일 노드 로컬 실행이며 AMD Alveo U50/XRT 환경을 대상으로 합니다.

| 개발 방식 | 사용자가 하는 일 | 제공 항목 |
|---|---|---|
| 연산 선택형 | 연산을 고르고 `module.yaml`의 파라미터 설정 | `adler32` `crc32` `identity` `lz4` `sha256` `vector_topk` |
| Hook형 | 프로파일을 고르고 함수 하나의 `return` 줄 작성 | `filter` `map` `aggregate` |

```text
[작업 디렉터리]/
├── modules/
│   ├── compressor/       # 연산 선택형: module.yaml, README.md
│   └── clip_filter/      # Hook형: module.yaml, logic.hpp, README.md
├── experiments/          # 가속 효과 검증 실험 정의
└── artifacts/            # 빌드 결과·출력·실행 기록 (플랫폼이 생성)
```

- 사용자가 수정하는 파일은 `module.yaml`과 `logic.hpp`뿐입니다. 커널, 호스트 어댑터, 빌드 설정은 플랫폼이 관리합니다.
- [**사용자 유의사항**](#6-사용자-유의사항)을 꼭 확인하시기 바랍니다.

**목차**: [1. 설치](#1-설치-및-환경-구성) · [2. 모듈 생성](#2-모듈-생성-스켈레톤) ·
[3. 로직 작성](#3-로직-작성-hook-템플릿) · [4. 빌드·실행·검증](#4-빌드실행검증) ·
[5. 가속 효과 검증](#5-가속-효과-검증) · [6. 유의사항](#6-사용자-유의사항) · [7. 명령어](#7-명령어-참고)

## 1. 설치 및 환경 구성

```bash
git clone https://github.com/Adv-FileSystem/ISC_Management_Platform.git
cd ISC_Management_Platform
python3 -m pip install -e .        # 설치 없이 쓰려면 isc 대신 python3 -m isc
source /opt/xilinx/xrt/setup.sh
export VITIS_LIBRARIES=[Vitis Libraries 2025.1 경로]   # adler32, crc32, lz4, sha256 빌드에 필요
isc doctor                         # 환경과 FPGA 카드 확인
```

| 항목 | 요구 버전 |
|---|---|
| OS / 커널 | Ubuntu Server 22.04.1 LTS / `5.15.0-50-generic` (HWE 제외) |
| Python / PyYAML | `>= 3.10` / `>= 5.4` |
| XRT / Vitis / Vitis Libraries | `2023.1` (`2.15.225`) / `2025.1` / `2025.1` |
| FPGA / 개발 플랫폼 | AMD Alveo U50 / `xilinx_u50_gen3x16_xdma_5_202210_1` |
| 장치 실행 패키지 | `xilinx-u50-gen3x16-xdma_2022.2_2022_1015_0317-all.deb.tar.gz` |
| 빌드·에뮬레이션 패키지 | `xilinx-u50-gen3x16-xdma-5-202210-1-dev_1-3499627_all.deb` |

참고: [U50 플랫폼 명세](https://docs.amd.com/r/en-US/ug1120-alveo-platforms/U50-Gen3x16-XDMA-base_5-Platform) ·
[XRT 지원 OS](https://docs.amd.com/r/2023.1-English/ug1451-xrt-release-notes/XRT-Operating-System-Support) ·
[U50 패키지 다운로드](https://www.amd.com/en/support/downloads/alveo-previous-downloads.html/accelerators/alveo/u50.html)

## 2. 모듈 생성 (스켈레톤)

대괄호(`[ ]`)는 실제 값으로 바꿉니다. 모듈 이름은 소문자로 시작하고 소문자·숫자·밑줄만 씁니다.

### 2.1. 연산 선택형

| 연산 | 기능 | 파라미터 |
|---|---|---|
| `adler32` / `crc32` | 체크섬 (4바이트 big-endian, 입력 1바이트 이상) | 없음 |
| `sha256` | SHA-256 다이제스트 (32바이트) | 없음 |
| `identity` | 입력 그대로 복사 | 없음 |
| `lz4` | 블록 단위 압축 | `block_size`: 1024–65536, 기본 65536 |
| `vector_topk` | int8 행렬과 질의 벡터의 정수 내적 상위 k개 | `k`: 1–16, 기본 10; `chunk_bytes`: 4096–134217728 |

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
  version: "1.0"         # 변경 금지
parameters:              # 수정 가능 (isc describe에 표시된 항목만)
  [파라미터 이름]: [값]
```

- 입력은 확장자와 관계없이 원시 바이트로 전달됩니다. `vector_topk` 입력은 `u32 rows, u32 dim, i8 query[dim], i8 db[rows][dim]`(little-endian)이고, 출력은 `k`개의 `(u32 id, i32 score)`입니다.
- `isc describe`에서 `[rebuild required]`가 없는 파라미터(`block_size`, `k`, `chunk_bytes`)는 바꿔도 재빌드하지 않습니다.

### 2.2. Hook형

| 프로파일 | 작성할 함수 | 출력 |
|---|---|---|
| `filter` | `bool isc_filter(T value)` | `true`인 값만, 입력 순서대로 |
| `map` | `U isc_map(T value)` | 변환된 U 값, 개수 동일 |
| `aggregate` | `bool isc_where(T value)` | `count, sum, min, max` (little-endian `u64` 4개) |

- 타입: `u8 u16 u32 u64 i8 i16 i32 i64 f32` (`aggregate`는 정수만). 입력은 T 값이 little-endian으로 이어진 파일입니다.

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
  version: "1.0"         # 변경 금지
  source: logic.hpp      # 변경 금지
```

## 3. 로직 작성 (Hook 템플릿)

시작 파일을 받아 **`return` 줄만** 고칩니다. 타입은 함수 시그니처가 정합니다.

```bash
isc template filter --type f32 -o clip_filter.hpp
```

```cpp
// ISC Hook 시작 파일 — 프로파일 filter (f32 값의 조건 필터)
// 입력: f32 값(little-endian)이 이어진 파일
// 출력: 함수가 true 를 돌려준 f32 값만, 입력 순서대로
// 고치는 곳: 아래 함수의 return 줄만. 함수 이름과 인자 이름(value)은 바꾸지 마십시오.
// 쓸 수 없는 것: 동적 할당, 파일 입출력, HLS INTERFACE 지시어.
#pragma once
#include <ap_int.h>
#include <stdint.h>

inline bool isc_filter(float value) {
    return true;   // ISC-TODO: 조건으로 바꾸십시오 → 예: return value >= -3.0f && value <= 3.0f;
}
```

`map`(`inline uint8_t isc_map(float value)`)과 `aggregate`(`inline bool isc_where(int32_t value)`)도 같은 형식입니다.

> **작성 규칙**
> - 함수 이름과 인자 이름(`value`)은 바꾸지 않습니다.
> - 동적 할당(`new`, `malloc`), 파일·소켓 입출력, `#pragma HLS INTERFACE`는 쓸 수 없습니다.
> - 작성 후 `ISC-TODO` 표식을 지웁니다.

입력 파일 예시 (Python):

```python
import struct
values = [0.5, -7.0, 2.25, 3.0, 10.0]
open("features.bin", "wb").write(struct.pack(f"<{len(values)}f", *values))   # f32 → "f", i32 → "i", u8 → "B"
```

## 4. 빌드·실행·검증

```text
check → reference(hw_emu) → build → device_wait → device_open → execute → compare → record
```

- `hw` 실행 시 hw 빌드 **전에** 같은 입력을 `hw_emu`로 먼저 돌려, 실패하면 약 40분짜리 hw 빌드를 시작하지 않습니다. 끝나면 두 출력을 바이트 단위로 비교합니다.
- 빌드 결과는 지문(연산·버전·빌드 파라미터·타깃·사용자 코드)별로 캐시되어 재사용됩니다.

**연산 선택형 예제**

```bash
isc new compressor --operation lz4 --set block_size=65536
isc check compressor
isc run compressor --input [입력 파일]               # hw_emu (기본)
isc run compressor --input [입력 파일] --target hw
isc records --module compressor                      # 실행 기록 (--csv 로 내보내기)
```

**Hook형 예제** (모듈을 따로 만들지 않고 로직 파일로 바로 실행)

```bash
isc template filter --type f32 -o clip_filter.hpp    # return 줄 수정
isc run clip_filter.hpp --input features.bin         # modules/clip_filter/ 자동 생성 후 hw_emu 실행
isc run clip_filter.hpp --input features.bin --target hw
```

**진행 확인**: 다른 터미널에서 `isc status`(최근 작업)와 `isc watch [실행 ID]`(단계별 진행)를 쓰거나, 실행 시 `--progress`를 붙입니다.

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

## 6. 사용자 유의사항

**자주 보는 오류** (`isc check`에서 `problem`이 하나라도 있으면 빌드·실행이 진행되지 않습니다)

| 메시지 | 해결 |
|---|---|
| `invalid module id ...` | 소문자로 시작, 소문자·숫자·밑줄만 사용 |
| `... requires an environment variable for include path '${VITIS_LIBRARIES}/...'` | `VITIS_LIBRARIES` 설정 |
| ``unsupported type `...` for the parameter of ...`` | 지원 타입으로 변경 |
| `hook source contains unsupported HLS interface pragmas` | `#pragma HLS INTERFACE` 삭제 |
| `hook source contains unsupported dynamic allocation (or host I/O)` | `new`, `malloc`, `fopen` 등 삭제 |
| `no hook profile matches this logic file` | 함수 이름을 `isc_filter`/`isc_map`/`isc_where`로 맞춤 |
| `warning: ... still has the starter marker ISC-TODO` | `return` 줄 작성 후 표식 삭제 |

**꼭 확인할 사항**
1. 로직을 고치는 동안에는 `hw_emu`로 확인하고 마지막에 `--target hw`로 실행합니다. 상수만 바꿔도 재빌드됩니다.
2. 기본 검증은 `hw`와 `hw_emu` 결과의 일치만 확인합니다. 로직이 의도대로 동작하는지는 작은 입력으로 직접 확인합니다.
3. Hook 입력 크기가 값 크기의 배수가 아니면 남는 끝 바이트는 오류 없이 무시됩니다.
4. 장치는 한 번에 한 실행만 씁니다. 사용 중이면 기다리며, `--device-timeout SEC`로 대기 시간을 제한합니다.
5. Hook 검사는 보안 샌드박스가 아닙니다. 신뢰하는 소스만 빌드합니다.

## 7. 명령어 참고

모든 명령은 `--json`으로 기계 판독 가능한 결과를 출력합니다.

| 구분 | 명령 |
|---|---|
| 환경 | `isc doctor`, `isc devices` |
| 연산 선택형 | `isc operations`, `isc describe <연산>`, `isc new <id> --operation <연산>` |
| Hook형 | `isc profiles`, `isc describe-profile <프로파일>`, `isc template <프로파일> -o 파일.hpp`, `isc new-custom <id> --profile <프로파일>`, `isc new-custom --from 파일.hpp` |
| 모듈 관리 | `isc list`, `isc inspect <id>`, `isc check <id>` |
| 빌드·실행 | `isc build <id>`, `isc run <id 또는 파일.hpp> --input FILE`, `isc validate <id>` |
| 기록·진행 | `isc records`, `isc show <run-id>`, `isc status`, `isc watch <run-id>` |
| 가속 검증 | `isc plan`, `isc experiments`, `isc bench <id>`, `isc report <id>` |

**자주 쓰는 옵션**

| 옵션 | 명령 | 기능 |
|---|---|---|
| `--set 파라미터=값` | `new` | 연산 파라미터 지정 |
| `--type T`, `--out-type U` | `template`, `new-custom` | Hook 입력·출력 타입 지정 |
| `--from 파일.hpp` | `new-custom` | 작성한 로직 파일로 모듈 생성 |
| `--target hw_emu\|hw` | `build`, `run` | 실행 대상 (기본 `hw_emu`) |
| `--input FILE`, `--output FILE` | `run` | 입력·출력 파일 |
| `--no-validate` | `run` | 결과 검증 생략 |
| `--device-timeout SEC` | `run`, `bench` | 장치 대기 시간 제한 |
| `--progress` | `build`, `run`, `bench` | 진행 단계를 터미널에 출력 |

나머지 옵션은 `isc [명령] --help`로 확인합니다.
