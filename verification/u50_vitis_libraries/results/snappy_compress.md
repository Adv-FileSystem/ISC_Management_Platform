# `snappy_compress` 검증 기록

연산 문서: [`isc/operations/snappy_compress/README.md`](../../../isc/operations/snappy_compress/README.md)

아래 기록은 연산 문서를 새 양식으로 바꾸기 전(2026-11-10 이전) 문서의 절차와 이름으로 실행한 것입니다.

## 플랫폼 자체 테스트

| 항목 | 내용 |
|---|---|
| 확인일 | 2026-11-06 |
| 환경 | Ubuntu 22.04.5 LTS (커널 6.8.0-138), Vitis 2025.1, XRT 2.19.194, Vitis Libraries `v2025.1_update2` (커밋 b2c657d), AMD Alveo U50 (`xilinx_u50_gen3x16_xdma_base_5`), 저장소 기준 커밋 `8742866` + 이 연산 폴더(커밋 전) |
| 입력 | 4.1의 `kafka_events_200k.bin` (200,074바이트, sha256 `0650df6b…`) |
| hw_emu | 통과 — 실행 `20261106T013637-607798` |
| hw (U50) | **통과** — 실행 `20261106T013637-7367f4`. hw 출력 = hw_emu 출력(플랫폼 자동 대조). 풀어서 원본과 일치(블록 4개, 압축률 2.37) |
| 판정 도구 | 순수 파이썬 해제기 `snappy_raw.py` (`python-snappy` 미설치). 공식 테스트 출력 24 MB로 먼저 검증 |
| 확인하지 않은 것 | 성능(가속 효과). `python-snappy`로 푸는 방법. 연산 문서의 입력 한 가지로만 확인 |

## 플랫폼 자체 테스트 — 1.1 사용자 로직 (`isc_pre`)

연산 1.1은 공식 커널 `xilSnappyCompress`에 넘기기 직전 입력 바이트마다(래퍼 커널 `isc_snappy_compress`) 사용자 함수 `isc_pre(b)`를 호출합니다. 바뀐 바이트는 별도 버퍼(`staged`)에 먼저 쓰고 그 버퍼를 압축하며, 압축 이득이 없어 원본으로 저장하는 블록도 이 버퍼의 바이트를 씁니다. 위 1.0 기록과는 커널 바이너리가 다릅니다.

| 항목 | 내용 |
|---|---|
| 확인일 | 2026-11-11 |
| 환경 | 위와 같음, 작업 브랜치 `feat/operation-user-logic` (커밋 전) |
| 입력 | 연산 문서 2.1의 예시 이벤트 묶음 (200,074바이트, 블록 4개) |
| 사용자 로직 | `return (b >= '0' && b <= '9') ? '#' : b;` (연산 문서 2.3) |
| C 시뮬레이션 (g++, HLS 헤더) | 시작 파일 = 풀었을 때 원본과 일치, `staged` = 원본. 규칙 = 풀었을 때 숫자를 가린 원본과 일치, `staged` = 가린 원본 |
| hw_emu | **통과** — `check_output.py` ROUNDTRIP OK (풀었을 때 = 원본에 같은 규칙을 적용한 값). 압축률 4.65. 같은 출력을 원본과 비교하면 MISMATCH (규칙이 실제로 적용됨) |
| hw_emu (로직 파일 없음) | **통과** — 시작 파일(`return b;`)로 빌드한 출력, `check_output.py --original` ROUNDTRIP OK |
| hw (U50) | 미실행 |
| 참고 | 검증용 별도 작업 공간에서 실행했으므로 저장소의 실행 기록(`isc records`)에는 남아 있지 않습니다 |

## 공식 라이브러리 테스트

Vitis Libraries 공식 테스트를 고치지 않고 U50에서 빌드·실행했습니다. **입력은 공식 테스트에 들어 있는 데이터**이며 연산 문서의 예시 데이터가 아닙니다.

| 항목 | 내용 |
|---|---|
| 공식 테스트 | `data_compression/L2/tests/snappy_compress` (Vitis Libraries `v2025.1_update2`). 공식 테스트의 대상 장치 목록에 U50은 없음 |
| 빌드 | `make all TARGET=hw PLATFORM=xilinx_u50_gen3x16_xdma_5_202210_1`, 이 서버의 메모리 제한 때문에 `VPP_LDFLAGS="--vivado.synth.jobs 4 --vivado.impl.jobs 4"`를 붙임 (2026-11-05, 약 86분) |
| 실행 | `make run TARGET=hw PLATFORM=xilinx_u50_gen3x16_xdma_5_202210_1` (공식 `data/sample.txt`) |
| 결과 | **통과** — 2026-11-05, 압축 후 해제해 원본과 비교 `Compression is successful. No errors found.` |
