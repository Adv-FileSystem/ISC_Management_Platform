# `image_bicubic` 검증 기록

연산 문서: [`isc/operations/image_bicubic/README.md`](../../../isc/operations/image_bicubic/README.md)

## 플랫폼 자체 테스트

| 항목 | 내용 |
|---|---|
| 확인일 | 2026-11-05 |
| 환경 | Ubuntu 22.04.5 LTS (커널 6.8.0-138), Vitis 2025.1, XRT 2.19.194, Vitis Libraries `v2025.1_update2` (커밋 b2c657d), AMD Alveo U50 (`xilinx_u50_gen3x16_xdma_base_5`), 저장소 기준 커밋 `8742866` + 이 연산 폴더(커밋 전) |
| 입력 | 당시 문서(시나리오: 상품 사진 썸네일, 모듈 `product_thumb`)의 입력 `product_512.bin` (262,152바이트, sha256 `d2d22836…`), 512×512 → 256×256. 지금 연산 문서 3.1의 `train_512.bin`과 내용이 같음 |
| hw_emu | 통과 — 실행 `20261105T235433-da9d66` |
| hw (U50) | **통과** — 실행 `20261105T235433-3457ff`. hw 출력 = hw_emu 출력(플랫폼 자동 대조) = CPU C 시뮬레이션 (65,536화소 바이트 일치) |
| 참고 | 1차 빌드는 헤더 경로 오류, 2차는 동시 빌드 중 메모리 부족으로 실패. 경로를 고치고 단독으로 다시 빌드해 통과. 2026-11-09에 C 시뮬레이션 명령을 다시 실행해 hw 출력과 일치함을 재확인 |
| 확인하지 않은 것 | 성능(가속 효과). 문서의 입력 한 가지로만 확인 |

## 공식 라이브러리 테스트

Vitis Libraries 공식 테스트를 고치지 않고 U50에서 빌드·실행했습니다. **입력은 공식 테스트에 들어 있는 데이터**이며 연산 문서의 예시 데이터가 아닙니다.

| 항목 | 내용 |
|---|---|
| 공식 테스트 | `codec/L2/demos/resize` (Vitis Libraries `v2025.1_update2`) |
| 빌드 | `make all TARGET=hw PLATFORM=xilinx_u50_gen3x16_xdma_5_202210_1` (2026-11-04, 약 71분) |
| 실행 | `make run TARGET=hw PLATFORM=xilinx_u50_gen3x16_xdma_5_202210_1` (공식 `images/t0.raw`, 512×512 → 64×64) |
| 결과 | **통과** — 2026-11-05, `PASS: no error found.` / `Info: Test passed` |
