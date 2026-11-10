# `lz4` 검증 기록

연산 문서: [`isc/operations/lz4/README.md`](../../../isc/operations/lz4/README.md)

아래 기록은 연산 문서를 새 양식으로 바꾸기 전(2026-11-10 이전) 문서의 절차와 이름으로 실행한 것입니다.

## 플랫폼 자체 테스트

| 항목 | 내용 |
|---|---|
| 확인일 | 2026-11-05 |
| 환경 | Ubuntu 22.04.5 LTS (커널 6.8.0-138), Python 3.10.12, Vitis 2025.1, XRT 2.19.194 (2025.1), Vitis Libraries `v2025.1_update2` (커밋 b2c657d), AMD Alveo U50 (`xilinx_u50_gen3x16_xdma_base_5`), 저장소 기준 커밋 `8742866` |
| 실행한 단계 | 연산 문서의 명령을 저장소 루트에서 순서대로 그대로 실행 (입력 만들기 → 모듈 생성 → 검사 → hw_emu → 결과 확인 → hw → 결과 확인) |
| hw_emu | 통과 — 실행 `20261105T110128-d5da24`, Python `lz4.block`으로 풀어 원본과 일치 (압축률 5.20) |
| hw (U50) | 통과 — 실행 `20261105T111257-a41f1a`, hw 출력 = hw_emu 출력(플랫폼 자동 대조) = CPU 참조 (Python `lz4.block`으로 풀어 원본과 일치) |
| 확인하지 않은 것 | 성능(가속 효과)은 측정하지 않음. 연산 문서의 입력 한 가지로만 확인 |

## 공식 라이브러리 테스트

Vitis Libraries 공식 테스트를 고치지 않고 U50에서 빌드·실행했습니다. **입력은 공식 테스트에 들어 있는 데이터**이며 연산 문서의 예시 데이터가 아닙니다.

| 항목 | 내용 |
|---|---|
| 공식 테스트 | `data_compression/L2/tests/lz4_compress` (Vitis Libraries `v2025.1_update2`, 같은 태그의 로컬 클론 `Vitis_Libraries_2025.1`) |
| 빌드 | 2026-07-10에 그 클론에서 미리 빌드해 둔 `compress.xclbin`을 사용. 이 빌드의 명령·소요 시간은 이번 검증 기록에 없음 |
| 실행 | `make run TARGET=hw PLATFORM=xilinx_u50_gen3x16_xdma_5_202210_1` (공식 `data/sample.txt`) |
| 결과 | **통과** — 2026-11-05, 압축 후 해제해 원본과 비교 `Compression is successful. No errors found.` |
