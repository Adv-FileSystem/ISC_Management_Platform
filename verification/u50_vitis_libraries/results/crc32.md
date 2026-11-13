# `crc32` 검증 기록

연산 문서: [`isc/operations/crc32/README.md`](../../../isc/operations/crc32/README.md)

## 플랫폼 자체 테스트

| 항목 | 내용 |
|---|---|
| 확인일 | 2026-11-05 |
| 환경 | Ubuntu 22.04.5 LTS (커널 6.8.0-138), Python 3.10.12, Vitis 2025.1, XRT 2.19.194 (2025.1), Vitis Libraries `v2025.1_update2` (커밋 b2c657d), AMD Alveo U50 (`xilinx_u50_gen3x16_xdma_base_5`), 저장소 기준 커밋 `8742866` |
| 실행한 단계 | 당시 문서(시나리오: 야간 백업 파일 손상 확인, 모듈 `backup_crc`, 입력 `backup.bin`)의 명령을 저장소 루트에서 순서대로 그대로 실행 (입력 만들기 → 모듈 생성 → 검사 → hw_emu → 결과 확인 → hw → 결과 확인). 입력 데이터는 지금 연산 문서 3.1과 같음 |
| hw_emu | 통과 — 실행 `20261105T110128-a5031a`, `zlib.crc32`와 일치 (`e3f9cf99`) |
| hw (U50) | 통과 — 실행 `20261105T111257-c36142`, hw 출력 = hw_emu 출력(플랫폼 자동 대조) = CPU 참조 (`zlib.crc32`) |
| 확인하지 않은 것 | 성능(가속 효과)은 측정하지 않음. 문서의 입력 한 가지로만 확인 |

## 공식 라이브러리 테스트

Vitis Libraries 공식 테스트를 고치지 않고 U50에서 빌드·실행했습니다. **입력은 공식 테스트에 들어 있는 데이터**이며 연산 문서의 예시 데이터가 아닙니다.

| 항목 | 내용 |
|---|---|
| 공식 테스트 | `security/L1/benchmarks/crc32` (Vitis Libraries `v2025.1_update2`). 공식 테스트의 대상 장치 목록에 U50은 없음 |
| 빌드 | `make all TARGET=hw PLATFORM=xilinx_u50_gen3x16_xdma_5_202210_1`, 이 서버의 메모리 제한 때문에 `VPP_LDFLAGS="--vivado.synth.jobs 4 --vivado.impl.jobs 4"`를 붙임 (2026-11-06, 약 38분). 11-05 첫 빌드는 동시 빌드 중 메모리 부족으로 중단 |
| 실행 | `make run TARGET=hw PLATFORM=xilinx_u50_gen3x16_xdma_5_202210_1` (공식 `test.dat`, `-num 16`) |
| 결과 | **통과** — 2026-11-06, 공식 비교 `Info: Test passed` |
