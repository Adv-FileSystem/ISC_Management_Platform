# `graph_twohop` 검증 기록

연산 문서: [`isc/operations/graph_twohop/README.md`](../../../isc/operations/graph_twohop/README.md)

아래 기록은 연산 문서를 새 양식으로 바꾸기 전(2026-11-10 이전) 문서의 절차와 이름으로 실행한 것입니다.

## 플랫폼 자체 테스트

| 항목 | 내용 |
|---|---|
| 확인일 | 2026-11-05 |
| 환경 | Ubuntu 22.04.5 LTS (커널 6.8.0-138), Vitis 2025.1, XRT 2.19.194, Vitis Libraries `v2025.1_update2` (커밋 b2c657d), AMD Alveo U50 (`xilinx_u50_gen3x16_xdma_base_5`), 저장소 기준 커밋 `8742866` + 이 연산 폴더(커밋 전) |
| 입력 | 4.1의 `friends_1000_fof.bin` (39,868바이트, sha256 `514c0a06…`) |
| hw_emu | 통과 — 실행 `20261105T215826-200853` |
| hw (U50) | **통과** — 실행 `20261105T215826-1c5266`. hw 출력 = hw_emu 출력(플랫폼 자동 대조) = CPU 참조(`baseline.py`). 500쌍 중 0이 아닌 값 262쌍 |
| 참고 | 첫 hw 빌드는 다른 빌드와 동시에 돌다 메모리 부족으로 실패했고, 단독으로 다시 빌드해 통과 |
| 확인하지 않은 것 | 성능(가속 효과). 연산 문서의 입력 한 가지로만 확인 |

## 플랫폼 자체 테스트 — 1.1 사용자 로직 (`isc_score`)

연산 1.1은 `xf::graph::twoHop` 뒤에 사용자 함수 `isc_score(src, dst, count)`를 쌍마다 호출합니다. 위 1.0 기록과는 커널 바이너리가 다릅니다.

| 항목 | 내용 |
|---|---|
| 확인일 | 2026-11-10 |
| 환경 | 위와 같음, 작업 브랜치 `feat/operation-user-logic` (커밋 전) |
| 입력 | 연산 문서 2.1의 `accounts_devices.bin` (계좌 800·기기 1,200·쌍 500, 22,300바이트) |
| 사용자 로직 | `return count >= 3 ? count * 10 : 0;` (연산 문서 2.3) |
| hw_emu | **통과** — 출력 = CPU 참조(`baseline.py`) 공유 기기 수에 같은 규칙을 적용한 값 (`check_output.py` MATCH). 500쌍 중 0이 아닌 값 220쌍, 최대 40 |
| hw_emu (로직 파일 없음) | **통과** — 시작 파일(`return count;`)로 빌드한 출력 = CPU 참조 공유 기기 수 그대로 (`check_output.py --original` MATCH). 0이 아닌 값 251쌍, 최대 4. 로직이 없으면 원래 연산과 같은 결과 |
| hw (U50) | 미실행 |
| 참고 | 검증용 별도 작업 공간에서 실행했으므로 저장소의 실행 기록(`isc records`)에는 남아 있지 않습니다 |

## 공식 라이브러리 테스트

Vitis Libraries 공식 테스트를 고치지 않고 U50에서 빌드·실행했습니다. **입력은 공식 테스트에 들어 있는 데이터**이며 연산 문서의 예시 데이터가 아닙니다.

| 항목 | 내용 |
|---|---|
| 공식 테스트 | `graph/L2/tests/twoHop` (Vitis Libraries `v2025.1_update2`) |
| 빌드 | `make all TARGET=hw PLATFORM=xilinx_u50_gen3x16_xdma_5_202210_1` (2026-11-04, 약 67분) |
| 실행 | `make run TARGET=hw PLATFORM=xilinx_u50_gen3x16_xdma_5_202210_1` (공식 `data/` 그래프와 쌍 목록) |
| 결과 | **통과** — 2026-11-05, 공식 golden 비교 `Info: Test passed` |
