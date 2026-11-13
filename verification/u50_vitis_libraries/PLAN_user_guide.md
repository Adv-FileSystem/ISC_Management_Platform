# 사용자 가이드 개선 계획 (초안, 2026-11-04)

## 1단계 분석 결과 (부분)

- 저장소에 추적되는 파일은 `isc/`, `README.md`, `pyproject.toml`, `.gitignore`뿐이다. `docs/`, `tests/`, `scripts/`, `modules/`, `datasets/`, `artifacts/`는 `.gitignore` 대상이라, 여기에 쓴 문서·예제는 GitHub에 올라가지 않는다.
  → 새 문서는 추적되는 새 폴더(제안: `guide/`)에, 예제·샘플 데이터 생성기는 `examples/`에 둔다.
- 실제 CLI 하위 명령(`isc/cli.py`): doctor, devices, list, operations, profiles, describe, describe-profile, template, new, new-custom, inspect, check, build, run, validate, plan, experiments, bench, report, records, show, status, watch. README 7절 표와 일치한다.
- README 1절의 XRT 요구 버전 `2023.1 (2.15.225)`은 이 PC의 실제 XRT `2.19.194 (2025.1)`와 다르다. 확인 후 수정 필요.
- 이번 세션에서 `gemv_i32`, `image_gaussian`, `image_resize`, `image_sobel` 연산을 `isc/operations/`에 추가했다(미커밋). hw_emu는 통과했지만 U50 hw 검증은 진행 중이다. 검증이 끝나기 전에는 README 지원 연산 표에 넣지 않는다.
- `isc bench`는 형식 없는 바이트 입력만 만들 수 있어, 영상·행렬 연산의 가속 판정에는 바로 쓸 수 없다.

## 2단계 계획 (확인 필요)

| 구분 | 내용 |
|---|---|
| 유지 | README 1~7절 본문 전체 |
| 수정 | XRT 버전 표기, "지원 범위" 표에 상태 열(검증 완료, 연동 필요 등)과 튜토리얼 링크 추가 |
| 추가 (README) | "연산별 활용 시나리오·튜토리얼" 목차 표, 문제 해결·검증 결과 문서 링크 |
| 새 문서 `guide/` | `common.md`(공통 환경·흐름), `ops/<연산>.md`(공통 템플릿 ①~⑦), `verification.md`(검증 결과표, `verification/u50_vitis_libraries/results/summary.md` 기반), `troubleshooting.md`, `cases.md`(활용 사례·출처) |
| 예제 `examples/` | 연산별 입력 생성 스크립트(`verification/.../tools/gen_inputs.py`에서 분리) |
| 연산별 템플릿 | ① 활용 시나리오 ② 연산 소개 ③ 사전 준비 ④ 모듈 개발 ⑤ 실행 ⑥ 결과 확인 ⑦ 문제 해결 |

## 남은 작업

1. 1단계 분석 마무리: 연산별 입출력 형식·파라미터를 `operation.yaml`과 대조하고, 기존 예제를 재사용할 수 있는지 확인
2. 계획 확정 후 기존 연산 9종(adler32, crc32, sha256, identity, lz4, vector_topk, filter, map, aggregate) 튜토리얼 작성. 명령과 출력 예시는 실제로 실행해 확인한 것만 넣는다.
3. 추가 연산 4종과 공식 테스트 결과를 상태 5분류(검증 완료 / 실행 실패 / 연동 필요 / 환경 제약 / 미검증)로 정리
4. README 통합, 링크·명령 재현 점검
