# `identity` — 설치 직후 FPGA 실행 경로 점검 (연산 선택형)

| 개발 방식 | 사용하는 라이브러리 함수 | 검증 상태 |
|---|---|---|
| 연산 선택형 | 없음 — 플랫폼 자체 HLS 커널 (`kernel/identity.cpp`, 입력을 출력으로 복사) | 플랫폼 자체 테스트 ✅ · 공식 라이브러리 테스트 해당 없음 (플랫폼 전용) (검증 장치 AMD Alveo U50 · [검증 기록](../../../verification/u50_vitis_libraries/results/identity.md)) |

## 1. 연산 예시

이 플랫폼을 처음 설치한 개발자가 실제 연산을 만들기 전에, FPGA 실행 경로(호스트 → 카드 전송 → 커널 → 회수)와 플랫폼 흐름(모듈 생성 → 빌드 → 실행 → 기록)이 끝까지 되는지 점검합니다. 입력을 그대로 돌려받으므로 결과가 맞는지 바로 알 수 있습니다. 가속 효과를 기대하는 연산이 아닙니다.

### 1.1. 설치 점검 스크립트 안에서 직접 확인하는 기존 방식

```bash
xrt-smi examine                     # 카드와 셸이 보이는지 확인 (XRT 도구)
xrt-smi validate --device [BDF]     # XRT 기본 검증 커널 실행
```

### 1.2. ISC 전주기 시스템 기반의 방식

```bash
isc doctor                                                                         # 개발 환경과 카드 확인
isc new env_check --operation identity                                             # 처음 한 번: 모듈 만들기
isc run env_check --input hello.txt --output hello.out --target hw                 # 플랫폼 흐름 전체로 카드 왕복
cmp hello.txt hello.out && echo identical                                          # 원본과 같으면 경로가 정상
```

- 입력: 아무 파일 (원시 바이트). 빈 파일이면 빈 출력
- 출력: 입력과 같은 바이트
- 1.1은 XRT 자체가 동작하는지를 보고, 1.2는 이 플랫폼의 모듈 생성·빌드·실행·기록까지 함께 봅니다
- 실제로 따라 할 절차는 2절(개발 절차)과 3절(실행 방법)에 있습니다.

## 2. 개발 절차

모든 명령은 저장소 루트에서 실행합니다. 이 연산은 `VITIS_LIBRARIES`가 필요 없습니다.

### 2.1. 입력 준비

**사용자가 할 일**: 왕복시킬 파일을 정합니다. 아무 파일이나 되고 변환할 필요가 없습니다. 이 연산에는 입력 생성 스크립트가 없습니다.

```bash
mkdir -p datasets/identity
printf 'hello isc\nline 2\n' > datasets/identity/hello.txt          # 17바이트 예시
```

- 큰 전송을 점검하려면 큰 파일(예: 수십 MB)을 씁니다. 이때는 hw_emu를 쓰지 않고 3절의 `--target hw`로만 실행합니다.

### 2.2. 모듈 만들기

**사용자가 할 일**: 모듈 이름을 정합니다. 이 연산은 파라미터가 없습니다.

```bash
isc new env_check --operation identity
```

`modules/env_check/module.yaml`이 만들어지며 고칠 것은 없습니다.

### 2.3. 로직 작성

**사용자가 할 일**: 없음. 입력을 그대로 돌려주는 점검용 연산이라 사용자 로직 자리를 두지 않습니다.

### 2.4. 검사

**사용자가 할 일**: 빌드하기 전에 모듈 설정을 검사합니다. 문제가 없으면 `PASS`가 표시됩니다.

```bash
isc check env_check
```

## 3. 실행 방법

**사용자가 할 일**: `--input`에 2.1의 파일, `--output`에 결과를 저장할 경로를 지정하고, `cmp`에 같은 두 파일을 지정합니다.

```bash
# 에뮬레이션 실행 (카드 없이, 처음 한 번 수 분 빌드)
isc run env_check --input datasets/identity/hello.txt --output datasets/identity/hello.emu

# 결과 확인: 원본과 바이트 비교 — identical이면 정상
cmp datasets/identity/hello.txt datasets/identity/hello.emu && echo identical

# FPGA 카드 실행 (hw) (처음 한 번 hw 빌드 약 45분) — 카드 출력이 에뮬레이션 출력과 같은지 플랫폼이 자동 비교
isc run env_check --input datasets/identity/hello.txt --output datasets/identity/hello.hw --target hw
cmp datasets/identity/hello.txt datasets/identity/hello.hw && echo identical

# 실행 기록 (입력·출력 sha256, 장치 정보, 단계별 시간)
isc records --module env_check
isc show [실행 ID]
```

- `isc show`의 `host_to_device_ms`, `kernel_ms`, `device_to_host_ms`로 단계별 시간을 볼 수 있습니다.
- 다른 실행이 장치를 쓰고 있으면 기다립니다. `--device-timeout SEC`로 대기 시간을 제한합니다.
- 여기까지 되면 플랫폼의 전체 흐름이 동작하는 것입니다. 다음은 하려는 작업에 맞는 연산을 [지원 범위 표](../../../README.md#지원-범위)에서 고릅니다.
- 터미널 출력을 파일로도 남기려면 명령 뒤에 `2>&1 | tee [로그 파일]`을 붙입니다.
