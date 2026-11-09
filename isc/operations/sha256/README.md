# `sha256` — 배포 파일 다이제스트 대조 (연산 선택형)

| 개발 방식 | 사용자가 작성하는 것 | 커널 | 파라미터 | U50 검증 |
|---|---|---|---|---|
| 연산 선택형 | 없음 (`isc new` 한 줄) | Vitis Security L1 SHA-256 단계 (`preProcessing`·`generateMsgSchedule`·`sha256Digest`, `kernel/sha256_kernel.cpp`) | 없음 | [검증 기록](#7-검증-기록) |

> **2026-11-05 수정**: 이전 커널은 256바이트를 넘는 입력에서 hw_emu 시뮬레이터가 종료되고(`Can't parse message of type "xclReadAddrKernelCtrl_response"` → `SIMULATION EXITED`),
> U50에서는 `execute` 단계가 끝나지 않았습니다. 원인은 커널 내부 스트림 교착 두 가지(커널의 입력 전송 순서, Vitis `sha256_top`의 32블록 초과 메시지)였고,
> 커밋 `625d3e1`(브랜치 `fix/sha256-dataflow-deadlock`)에서 고쳤습니다. 이 문서의 출력과 [검증 기록](#7-검증-기록)은 수정된 커널 기준입니다.
> 원인 분석과 크기별 검증 결과: `verification/sha256/README.md` (같은 브랜치).

## 1. 시나리오

| 항목 | 내용 |
|---|---|
| 사용자 | 소프트웨어 배포 담당자 |
| 상황 | 배포 파일(설치 패키지, 이미지)을 서버에 올린 뒤, 공개한 SHA-256 다이제스트와 서버의 파일이 같은지 확인한다 |
| 이 연산으로 하는 일 | 파일을 FPGA로 보내 32바이트 SHA-256 다이제스트를 받는다 |
| 완료 기준 | FPGA 다이제스트가 같은 파일의 `sha256sum` 결과와 같다 |

## 2. 연산 요약

| 항목 | 내용 |
|---|---|
| 입력 | 아무 파일 (원시 바이트, 형식 무관). 빈 파일 허용 |
| 출력 | 32바이트 이진 다이제스트 (`sha256sum`이 16진수로 보여 주는 값의 원래 바이트) |
| 파라미터 | 없음 |
| 한계 | 다이제스트는 "파일이 같은가"만 알려 줌. 누가 만들었는지(서명·출처 인증)는 보장하지 않음 |

작은 예: 입력 `abc`(ASCII 3바이트) → `ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad`

## 3. 준비

- 공통 설치와 환경 확인: [README 1절](../../../README.md#1-설치-및-환경-구성)
- 이 연산은 빌드에 **`VITIS_LIBRARIES`** 가 필요합니다: `export VITIS_LIBRARIES=[Vitis Libraries 2025.1 경로]`
- 모든 명령은 **저장소 루트**에서 실행합니다. 실습 파일은 `datasets/sha256/`(git에 올라가지 않음)에 만듭니다.

## 4. 개발 절차

### 4.1. 입력 만들기와 기준값

배포 파일 대신 100,000바이트 무작위 파일(고정 시드)을 만들고, 기준값을 `sha256sum`으로 구합니다.

```bash
mkdir -p datasets/sha256
python3 - <<'EOF'
import random
open("datasets/sha256/release.bin", "wb").write(random.Random(3).randbytes(100_000))
EOF
sha256sum datasets/sha256/release.bin
```

```text
7797dff7d2096d2c76dab4f8ea8958d31faa80991945d7988b3cc0651acae850  datasets/sha256/release.bin
```

### 4.2. 모듈 만들기

```bash
isc describe sha256                         # 입력·출력 형식 확인 (선택)
isc new release_sha --operation sha256
```

생성되는 `modules/release_sha/module.yaml` — 고칠 것이 없습니다.

```yaml
module:
  id: release_sha       # 변경 금지
  name: release_sha     # 수정 가능
  version: 0.1.0        # 수정 가능
operation:
  id: sha256            # 변경 금지
  version: '1.0'        # 변경 금지
```

### 4.3. 검사

```bash
isc check release_sha
```

```text
release_sha: PASS
```

### 4.4. 실행

다른 연산과 같은 순서입니다. 먼저 `hw_emu`로 확인하고, `--target hw`를 붙여 카드에서 실행하면 플랫폼이 hw와 hw_emu 결과를 자동으로 대조합니다.

```bash
isc run release_sha --input datasets/sha256/release.bin --output datasets/sha256/result.bin
isc run release_sha --input datasets/sha256/release.bin --output datasets/sha256/result_hw.bin --target hw
```

실제 출력 (발췌):

```text
release_sha [hw_emu] succeeded  run_id=20261105T135349-9b3bfe
  device     : [0] xilinx_u50_gen3x16_xdma_5_202210_1  (emulated)
  build      : cache hit  fbd3b57f3daf34674ee6bbf4cde894b7
  input      : 100000 bytes  /home/.../datasets/sha256/release.bin
  output     : 32 bytes  /home/.../datasets/sha256/result.bin
  validation : PASS  (cross_target) ran on the reference target itself; nothing to compare

release_sha [hw] succeeded  run_id=20261105T135431-da2d59
  device     : [0] xilinx_u50_gen3x16_xdma_base_5
  build      : cache hit  e7ba7e442d457afcc45a2d4192a09284
  input      : 100000 bytes  /home/.../datasets/sha256/release.bin
  output     : 32 bytes  /home/.../datasets/sha256/result_hw.bin
  validation : PASS  (cross_target) matches reference target output (32 bytes)
```

처음 실행하면 빌드를 먼저 합니다(hw_emu 수 분, hw 약 45분). hw_emu의 `validation : PASS`는 hw_emu가 자기 자신을 기준으로 삼았다는 뜻이므로, 값은 다음 단계에서 확인합니다.
진행 단계는 다른 터미널의 `isc watch [실행 ID]`로 볼 수 있습니다.

### 4.5. 결과 확인

출력 32바이트를 16진수로 바꿔 4.1의 `sha256sum` 값과 비교합니다.

```bash
python3 - <<'EOF'
import hashlib
data = open("datasets/sha256/release.bin", "rb").read()
got = open("datasets/sha256/result_hw.bin", "rb").read().hex()
print(f"fpga sha256 = {got}")
print("MATCH" if got == hashlib.sha256(data).hexdigest() else "MISMATCH")
EOF
```

```text
fpga sha256 = 7797dff7d2096d2c76dab4f8ea8958d31faa80991945d7988b3cc0651acae850
MATCH
```


명령줄에서만 보려면 `xxd -p -c 32 datasets/sha256/result_hw.bin`의 출력을 `sha256sum` 값과 눈으로 비교해도 됩니다.

### 4.6. 기록 확인

```bash
isc records --module release_sha
isc show [실행 ID]          # 입력·출력 sha256, 빌드 지문, 단계별 시간
```

## 5. 내 데이터에 적용하기

| 바꿀 것 | 방법 |
|---|---|
| 확인할 파일 | `--input`에 배포 파일 경로. 모듈은 그대로 재사용 |
| 공개된 다이제스트와 비교 | 4.5 코드의 `hashlib.sha256(data).hexdigest()` 대신 공개된 16진수 문자열(소문자)을 넣음 |
| 여러 파일 | 파일마다 `--input`·`--output`을 바꿔 반복 실행. 실행 기록에 입력 sha256(플랫폼이 CPU로 계산)도 남으므로 함께 대조 가능 |

## 6. 문제 해결

| 증상 | 원인 | 해결 |
|---|---|---|
| hw_emu에서 `Can't parse message of type "xclReadAddrKernelCtrl_response" ...` 후 `SIMULATION EXITED`, 또는 hw 실행이 `execute` 단계에서 끝나지 않음 | 수정 전 커널(커밋 `625d3e1` 이전)로 실행 | 수정된 커널이 있는 저장소에서 다시 실행(커널이 바뀌면 빌드 지문이 달라져 자동으로 재빌드). hw 실행이 `Ctrl+C`로 멈추지 않으면 해당 `isc` 프로세스를 종료 |
| `... requires an environment variable for include path '${VITIS_LIBRARIES}/...'` (`isc check`) | `VITIS_LIBRARIES` 미설정 | `export VITIS_LIBRARIES=[경로]` 후 다시 검사 |
| `sha256sum`과 다름 | 출력을 텍스트로 읽음 | 출력은 이진 32바이트. `.hex()`나 `xxd -p -c 32`로 16진수 변환 |

## 7. 검증 기록

| 항목 | 내용 |
|---|---|
| 확인일 | 2026-11-05 |
| 환경 | Ubuntu 22.04.5 LTS (커널 6.8.0-138), Python 3.10.12, Vitis 2025.1, XRT 2.19.194 (2025.1), Vitis Libraries `v2025.1_update2` (커밋 b2c657d), AMD Alveo U50 (`xilinx_u50_gen3x16_xdma_base_5`), 저장소 기준 커밋 `625d3e1` (브랜치 `fix/sha256-dataflow-deadlock`) |
| 실행한 단계 | 입력 만들기(4.1과 같은 시드) → 검사 → hw_emu → hw → 결과 확인을 저장소 루트에서 이 문서의 명령 그대로 실행. 모듈은 같은 날 4.2의 `isc new`로 만든 `release_sha`를 그대로 사용 |
| hw_emu | 통과 — 실행 `20261105T135349-9b3bfe`, `hashlib.sha256`과 일치 (`7797dff7…acae850`) |
| hw (U50) | 통과 — 실행 `20261105T135431-da2d59`, hw 출력 = hw_emu 출력(플랫폼 자동 대조) = CPU 참조 (`hashlib.sha256`). 빌드 `e7ba7e44…` 타이밍 충족(커널 300 MHz) |
| 추가 크기 확인 | 0, 1, 3(`abc`), 55, 56, 64, 1,984, 2,048, 4,096, 100,000바이트 — hw_emu·hw 모두 `hashlib.sha256`과 일치, hw는 플랫폼 대조도 전부 PASS (`verification/sha256/README.md`) |
| 확인하지 않은 것 | 성능(가속 효과)은 측정하지 않음. 가장 큰 입력은 100,000바이트 |
