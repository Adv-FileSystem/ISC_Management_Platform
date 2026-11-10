# `graph_renumber` 검증 기록

연산 문서: [`isc/operations/graph_renumber/README.md`](../../../isc/operations/graph_renumber/README.md)

아래 기록은 연산 문서를 새 양식으로 바꾸기 전(2026-11-10 이전) 문서의 절차와 이름으로 실행한 것입니다.

## 플랫폼 자체 테스트

| 항목 | 내용 |
|---|---|
| 확인일 | 2026-11-06 |
| 환경 | Ubuntu 22.04.5 LTS (커널 6.8.0-138), Vitis 2025.1, XRT 2.19.194, Vitis Libraries `v2025.1_update2` (커밋 b2c657d), AMD Alveo U50 (`xilinx_u50_gen3x16_xdma_base_5`), 저장소 기준 커밋 `8742866` + 이 연산 폴더(커밋 전) |
| 입력 | 4.1의 `members_5000.bin` (20,004바이트, sha256 `bbc89c67…`) |
| hw_emu | 통과 — 실행 `20261106T004235-a842ca` |
| hw (U50) | **통과** — 실행 `20261106T004235-45d7ae`. hw 출력 = hw_emu 출력(플랫폼 자동 대조) = CPU 참조(`baseline.py`). 묶음 300개, 일대일 대응 확인 |
| 참고 | 첫 hw 빌드는 다른 빌드와 동시에 돌다 메모리 부족으로 실패했고, 단독으로 다시 빌드해 통과 |
| 확인하지 않은 것 | 성능(가속 효과). 연산 문서의 입력 한 가지로만 확인. 시나리오 입력에 대한 공식 golden은 없음 |

## 공식 라이브러리 테스트

Vitis Libraries 공식 테스트를 고치지 않고 U50에서 빌드·실행했습니다. **입력은 공식 테스트에 들어 있는 데이터**이며 연산 문서의 예시 데이터가 아닙니다.

| 항목 | 내용 |
|---|---|
| 공식 테스트 | `graph/L2/tests/renumber` (Vitis Libraries `v2025.1_update2`) |
| 빌드 | `make all TARGET=hw PLATFORM=xilinx_u50_gen3x16_xdma_5_202210_1`, 이 서버의 메모리 제한 때문에 `VPP_LDFLAGS="--vivado.synth.jobs 4 --vivado.impl.jobs 4"`를 붙임 (2026-11-06, 약 68분). 11-05 첫 빌드는 동시 빌드 중 메모리 부족으로 중단 |
| 실행 | `make run TARGET=hw PLATFORM=xilinx_u50_gen3x16_xdma_5_202210_1` (공식 `data/as-Skitter.txt`) |
| 결과 | **통과** — 2026-11-06, 묶음 6,183개, 공식 비교 `Info: Test passed` |
