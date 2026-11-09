# `identity` — 설치 직후 FPGA 실행 경로 점검 (연산 선택형)

| 개발 방식 | 사용자가 작성하는 것 | 커널 | 파라미터 | 플랫폼 자체 테스트 | 공식 라이브러리 테스트 |
|---|---|---|---|---|---|
| 연산 선택형 | 없음 (`isc new` 한 줄) | 플랫폼 자체 HLS 커널 (`kernel/identity.cpp`, 입력을 출력으로 복사) | 없음 | ⚠ main: hw_emu 대조 실패 · 수정 브랜치: ✅ 통과 | — 해당 없음 |

> **알려진 문제 — main 기준 (2026-11-05 확인)**: main의 커널은 `hw_emu`에서 일부 입력 길이(17·33·65바이트)의 **마지막 바이트를 `00`으로**
> 돌려줍니다. 카드(`hw`) 출력은 정상이지만, 플랫폼이 hw 출력을 hw_emu 출력과 대조하므로 이 문서의 예시(17바이트)는 main에서
> `isc run --target hw`가 **대조 실패(`failed`)** 로 기록됩니다 (실행 `20261105T111257-aaac8e`).
> 커널을 32바이트 단위로 복사하도록 고친 수정은 브랜치 `fix/identity-hw-emu-last-byte`(커밋 `8b72302`)에 있고, **아직 main에 합치지 않았습니다**.
> 아래 4절의 출력과 7.1절의 통과 기록은 이 수정 브랜치에서 실행한 결과입니다. main에서는 4.5·4.6의 `cmp` 결과로 판정하십시오.

## 1. 시나리오

| 항목 | 내용 |
|---|---|
| 사용자 | 이 플랫폼을 처음 설치한 개발자 |
| 상황 | 설치 직후 FPGA 실행 경로(전송 → 커널 → 회수)가 끝까지 되는지 점검한다. 실제 연산을 만들기 전에 "모듈 생성 → 빌드 → FPGA 실행 → 결과 회수 → 기록"이 되는지 본다 |
| 가속하는 단계 | 데이터 왕복 (계산 없음) |
| 입력 → 출력 | 아무 파일 → 같은 파일 |
| 완료 기준 | `cmp`로 원본과 일치 |

가속 효과를 기대하는 연산이 아닙니다. 경로 점검과 플랫폼 동작 확인용입니다.

## 2. 연산 요약

| 항목 | 내용 |
|---|---|
| 입력 | 아무 파일 (원시 바이트). 빈 파일이면 빈 출력 |
| 출력 | 입력과 같은 바이트 |
| 파라미터 | 없음 |

작은 예: 입력 `hello` → 출력 `hello`

## 3. 준비

- 공통 설치와 환경 확인: [README 1절](../../../README.md#1-설치-및-환경-구성). `isc doctor`에서 `backend xrt: ok`와 카드가 보여야 합니다.
- 이 연산은 `VITIS_LIBRARIES`가 필요 없습니다.
- 모든 명령은 **저장소 루트**에서 실행합니다. 실습 파일은 `datasets/identity/`(git에 올라가지 않음)에 만듭니다.

```bash
isc doctor
```

```text
python    : 3.10.12
backend xrt: ok -- v++, emconfigutil and pyxrt available
    [0] xilinx_u50_gen3x16_xdma_base_5  0000:03:00.1
```

## 4. 개발 절차

### 4.1. 입력 만들기

```bash
mkdir -p datasets/identity
printf 'hello isc\nline 2\n' > datasets/identity/hello.txt
```

### 4.2. 모듈 만들기

```bash
isc new env_check --operation identity
```

```text
created /home/.../ISC_Management_Platform/modules/env_check
  README.md
  module.yaml

operation: identity
next: isc check env_check
```

생성되는 `modules/env_check/module.yaml` — 고칠 것이 없습니다.

```yaml
module:
  id: env_check         # 변경 금지
  name: env_check       # 수정 가능
  version: 0.1.0        # 수정 가능
operation:
  id: identity          # 변경 금지
  version: '1.0'        # 변경 금지
```

### 4.3. 검사

```bash
isc check env_check
```

```text
env_check: PASS
```

### 4.4. 에뮬레이션 실행 (hw_emu)

```bash
isc run env_check --input datasets/identity/hello.txt --output datasets/identity/result.bin
```

실제 출력 (발췌, hw_emu 빌드가 이미 있어 `cache hit`. 처음 실행이면 `built`로 나오고 빌드 시간이 더해짐):

```text
env_check [hw_emu] succeeded  run_id=20261105T133808-d4f9b7
  device     : [0] xilinx_u50_gen3x16_xdma_5_202210_1  (emulated)
  build      : cache hit  f9018e2c9f427a4840f922b1bcc7e28e
  input      : 17 bytes  /home/.../datasets/identity/hello.txt
  output     : 17 bytes  /home/.../datasets/identity/result.bin
  validation : PASS  (cross_target) ran on the reference target itself; nothing to compare
```

### 4.5. 결과 확인

```bash
cmp datasets/identity/hello.txt datasets/identity/result.bin && echo identical
```

실제 출력:

```text
identical
```

### 4.6. U50 실행 (hw)

```bash
isc run env_check --input datasets/identity/hello.txt --output datasets/identity/result_hw.bin --target hw
cmp datasets/identity/hello.txt datasets/identity/result_hw.bin && echo identical
```

실제 출력 (발췌, 처음 실행이라 hw 빌드 포함 — 이 환경에서 약 45분):

```text
env_check [hw] succeeded  run_id=20261105T133707-c73bc2
  device     : [0] xilinx_u50_gen3x16_xdma_base_5
  build      : cache hit  08901678f1e6a914097eab31e95e5854
  input      : 17 bytes  /home/.../datasets/identity/hello.txt
  output     : 17 bytes  /home/.../datasets/identity/result_hw.bin
  validation : PASS  (cross_target) matches reference target output (17 bytes)
identical
```

위 실행은 hw 빌드를 `isc build env_check --target hw`로 먼저 해 둔 상태라 `cache hit`입니다. 처음 실행이면 `built`로 나오고 hw 빌드(이 환경에서 약 45분)가 더해집니다.
`validation : PASS`는 카드 출력이 같은 입력의 hw_emu 출력(기준 실행 `20261105T133707-000c0f`)과 바이트까지 같다는 뜻이고, `cmp`는 원본과 같음을 확인합니다.

### 4.7. 기록 확인

```bash
isc records --module env_check
isc show [실행 ID]          # 입력·출력 sha256, 장치 정보, 단계별 시간
```

여기까지 되면 플랫폼의 전체 흐름이 동작하는 것입니다. 다음은 하려는 작업에 맞는 연산을 [지원 범위 표](../../../README.md#지원-범위)에서 고릅니다.

## 5. 내 환경 점검에 쓰기

| 확인할 것 | 방법 |
|---|---|
| 큰 전송 | 큰 파일(예: 수십 MB)을 `--target hw`로 보내 `cmp`. hw_emu는 시뮬레이션이라 큰 파일에 쓰지 않음 |
| 단계별 시간 | `isc show [실행 ID]`의 `host_to_device_ms`, `kernel_ms`, `device_to_host_ms` |
| 장치 대기 | 다른 실행이 장치를 쓰면 기다림. `--device-timeout SEC`로 제한 |

## 6. 문제 해결

| 증상 | 원인 | 해결 |
|---|---|---|
| `isc doctor`에 카드가 없음 | XRT 미설정 또는 장치 실행 패키지 미설치 | `source /opt/xilinx/xrt/setup.sh`, [README 1절](../../../README.md#1-설치-및-환경-구성)의 패키지 확인 |
| `invalid module id ...` | 모듈 이름 규칙 위반 | 소문자로 시작, 소문자·숫자·밑줄만 |
| 장치 대기 메시지 `waiting for device ...: reserved by run ...` | 다른 실행이 장치 사용 중 | 기다리거나 `--device-timeout SEC` |
| `validation : FAIL ... reference target 0x00, this target ...` (마지막 바이트) | main 커널의 hw_emu 마지막 바이트 문제 (맨 위 알려진 문제) | 카드 출력은 `cmp`로 확인. 수정 브랜치 `fix/identity-hw-emu-last-byte`(커밋 `8b72302`)의 코드로 실행하면 hw_emu·hw 모두 새로 빌드되고 대조가 통과함 |

## 7. 검증 기록

### 7.1. 플랫폼 자체 테스트

| 항목 | 내용 |
|---|---|
| 확인일 | 2026-11-05 |
| 환경 | Ubuntu 22.04.5 LTS (커널 6.8.0-138), Python 3.10.12, Vitis 2025.1, XRT 2.19.194 (2025.1), Vitis Libraries `v2025.1_update2` (커밋 b2c657d), AMD Alveo U50 (`xilinx_u50_gen3x16_xdma_base_5`), 코드 커밋 `8b72302` (브랜치 `fix/identity-hw-emu-last-byte`) |
| 실행한 단계 | 이 문서의 명령을 순서대로 그대로 실행 (hw 빌드는 4.6 전에 `isc build env_check --target hw`로 따로 실행) |
| hw_emu | **통과** — 실행 `20261105T133808-d4f9b7`, `cmp`로 원본과 일치. 길이별 추가 확인(`isc run` 후 `cmp`): 1·16·17·31·33·64·65·100·129·257·544·1056·2080·4096·4097·4128·100000바이트 모두 일치 |
| hw (U50) | **통과** — 실행 `20261105T133707-c73bc2`, `validation : PASS (cross_target)`, `cmp`로 원본과 일치 |
| 수정 내용 | 커널이 32바이트(256비트) 단위로 복사하고 `output_size`는 별도 AXI 포트(`gmem2`)를 씀. 어댑터가 버퍼를 32바이트 배수로 맞추고 `output_size`만큼 돌려줌. 입출력 형식은 같음 |
| 이전 기록 | 수정 전 커널: hw_emu 실행 `20261105T110128-f1f047`(17바이트 중 마지막 바이트 `00`), hw 실행 `20261105T111257-aaac8e`(출력 정상이나 대조 `failed`) |
| 확인하지 않은 것 | 성능, 큰 파일의 hw 전송, 17바이트 외 길이의 hw 실행 |

### 7.2. 공식 라이브러리 테스트

**해당 없음** — 이 연산에 대응하는 Vitis Libraries 공식 테스트가 없습니다 (플랫폼 전용 연산).
