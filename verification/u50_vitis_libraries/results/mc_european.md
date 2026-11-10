# `mc_european` 검증 기록

연산 문서: [`isc/operations/mc_european/README.md`](../../../isc/operations/mc_european/README.md)

아래 기록은 연산 문서를 새 양식으로 바꾸기 전(2026-11-10 이전) 문서의 절차와 이름으로 실행한 것입니다.

## 플랫폼 자체 테스트

| 항목 | 내용 |
|---|---|
| 확인일 | 2026-11-05 |
| 환경 | Ubuntu 22.04.5 LTS (커널 6.8.0-138), Vitis 2025.1, XRT 2.19.194, Vitis Libraries `v2025.1_update2` (커밋 b2c657d), AMD Alveo U50 (`xilinx_u50_gen3x16_xdma_base_5`), 저장소 기준 커밋 `8742866` + 이 연산 폴더(커밋 전) |
| 입력 | 4.1의 `option_book_8.bin` (264바이트, sha256 `9e513277…`), `samples=4096`, `time_steps=1` |
| hw_emu | 통과 — 실행 `20261105T205122-93697b` |
| hw (U50) | **통과** — 실행 `20261105T205122-fc53c6`. hw 출력 = hw_emu 출력(플랫폼 자동 대조). Black-Scholes 해석해 대비 공식 판정식(`\|d\|/S`) 최대 0.428% (기준 1%). 가격 대비 오차는 최대 6.6%(깊은 외가격 풋 1건), 나머지 7건은 1.65% 이하 |
| 참고 | 첫 hw 빌드는 다른 빌드와 동시에 돌다 메모리 부족으로 실패했고, 단독으로 다시 빌드해 통과 |
| 확인하지 않은 것 | 성능(가속 효과). `samples`를 바꾼 실행. 연산 문서의 입력 한 가지로만 확인 |

## 공식 라이브러리 테스트

Vitis Libraries 공식 테스트를 고치지 않고 U50에서 빌드·실행했습니다. **입력은 공식 테스트에 들어 있는 조건표**이며 연산 문서의 예시 데이터가 아닙니다.

| 항목 | 내용 |
|---|---|
| 공식 테스트 | `quantitative_finance/L2/tests/MCEuropeanEngine` (Vitis Libraries `v2025.1_update2`) |
| 빌드 | `make all TARGET=hw PLATFORM=xilinx_u50_gen3x16_xdma_5_202210_1`, 이 서버의 메모리 제한 때문에 `VPP_LDFLAGS="--vivado.synth.jobs 4 --vivado.impl.jobs 4"`를 붙임 (2026-11-04, 약 46분) |
| 실행 | `make run TARGET=hw PLATFORM=xilinx_u50_gen3x16_xdma_5_202210_1` (공식 `host/test.cpp`의 조건표와 golden) |
| 결과 | **통과** — 2026-11-05, golden 비교(`\|d\|/S ≤ 0.01`) `Info: Test passed` |
