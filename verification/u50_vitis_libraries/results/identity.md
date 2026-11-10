# `identity` 검증 기록

연산 문서: [`isc/operations/identity/README.md`](../../../isc/operations/identity/README.md)

아래 기록은 연산 문서를 새 양식으로 바꾸기 전(2026-11-10 이전) 문서의 절차와 이름으로 실행한 것입니다.

## 플랫폼 자체 테스트

| 항목 | 내용 |
|---|---|
| 확인일 | 2026-11-05 |
| 환경 | Ubuntu 22.04.5 LTS (커널 6.8.0-138), Python 3.10.12, Vitis 2025.1, XRT 2.19.194 (2025.1), Vitis Libraries `v2025.1_update2` (커밋 b2c657d), AMD Alveo U50 (`xilinx_u50_gen3x16_xdma_base_5`), 코드 커밋 `8b72302` (브랜치 `fix/identity-hw-emu-last-byte`) |
| 실행한 단계 | 연산 문서의 명령을 순서대로 그대로 실행 (hw 빌드는 4.6 전에 `isc build env_check --target hw`로 따로 실행) |
| hw_emu | **통과** — 실행 `20261105T133808-d4f9b7`, `cmp`로 원본과 일치. 길이별 추가 확인(`isc run` 후 `cmp`): 1·16·17·31·33·64·65·100·129·257·544·1056·2080·4096·4097·4128·100000바이트 모두 일치 |
| hw (U50) | **통과** — 실행 `20261105T133707-c73bc2`, `validation : PASS (cross_target)`, `cmp`로 원본과 일치 |
| 수정 내용 | 커널이 32바이트(256비트) 단위로 복사하고 `output_size`는 별도 AXI 포트(`gmem2`)를 씀. 어댑터가 버퍼를 32바이트 배수로 맞추고 `output_size`만큼 돌려줌. 입출력 형식은 같음 |
| 이전 기록 | 수정 전 커널: hw_emu 실행 `20261105T110128-f1f047`(17바이트 중 마지막 바이트 `00`), hw 실행 `20261105T111257-aaac8e`(출력 정상이나 대조 `failed`) |
| 확인하지 않은 것 | 성능, 큰 파일의 hw 전송, 17바이트 외 길이의 hw 실행 |

## 공식 라이브러리 테스트

**해당 없음** — 이 연산에 대응하는 Vitis Libraries 공식 테스트가 없습니다 (플랫폼 전용 연산).
