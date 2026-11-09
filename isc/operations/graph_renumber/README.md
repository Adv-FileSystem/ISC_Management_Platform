# `graph_renumber` — 그래프 분석 전 원본 ID를 0부터 연속 번호로 바꾸기 (연산 선택형)

| 개발 방식 | 사용자가 작성하는 것 | 커널 | 파라미터 | 플랫폼 자체 테스트 | 공식 라이브러리 테스트 |
|---|---|---|---|---|---|
| 연산 선택형 | 없음 (`isc new` 한 줄) | Vitis Graph L2 `initRenumber` + `renumberCore` (`kernel/renumber_kernel.cpp`, 공식 테스트 커널과 같은 호출) | 없음 | ✅ 통과 (2026-11-06) | ✅ 통과 (2026-11-06) |

> 이 연산은 시나리오 검증용 **프로토타입**입니다. 공식 커널을 그대로 감쌌습니다.

## 1. 시나리오

| 항목 | 내용 |
|---|---|
| 사용자 | 회원 관계 그래프를 분석하는 데이터 엔지니어 |
| 상황 | 그래프 분석 전에 듬성듬성한 원본 ID(예: 회원번호)를 0부터 연속 번호로 바꿔 메모리를 아낀다 |
| 가속하는 단계 | ID 재번호화 |
| 입력 → 출력 | 원본 ID 목록 → 새 번호 대응표 |
| 완료 기준 | 대응이 일대일이고 공식 골든과 일치 |

플랫폼 자체 테스트에서는 시나리오 입력에 대한 공식 golden이 없으므로, 공식 호스트와 같은 규칙(처음 등장한 순서대로 번호)으로 따로 작성한 CPU 참조와 비교하고 일대일 대응을 따로 확인합니다. 공식 golden과의 비교는 7.2의 공식 라이브러리 테스트에서 했습니다.

## 2. 연산 요약

| 항목 | 내용 |
|---|---|
| 입력 | 리틀 엔디안 이진 파일: `u32 N` + `i32 id[N]` |
| 출력 | `u32 clusters` + `i32 new_id[N]` — 입력 순서 그대로, 각 원본 ID의 새 번호(0 ~ clusters-1) |
| 번호 규칙 | 처음 등장한 순서대로 0, 1, 2, … (공식 호스트의 `renumberClustersContiguously`와 같은 규칙) |
| 파라미터 | 없음 |
| **한계** | 원본 ID는 **0 이상 N 미만**이어야 합니다. 커널 내부 대응표가 N칸이기 때문입니다. 실제 회원번호처럼 N보다 큰 ID는 그대로 넣을 수 없습니다 (5절) |
| CPU 참조 | `baseline.py` |
| 메모리 | 포트 5개를 HBM 뱅크 8·0·2·4·6에 연결 (`operation.yaml`) |

작은 예: `N=4`, `id = [3, 3, 1, 3]` → `clusters=2`, `new_id = [0, 0, 1, 0]`

## 3. 준비

- 공통 설치와 환경 확인: [README 1절](../../../README.md#1-설치-및-환경-구성)
- 빌드에 **`VITIS_LIBRARIES`** 가 필요합니다: `export VITIS_LIBRARIES=[Vitis Libraries 2025.1 경로]`
- 모든 명령은 **저장소 루트**에서 실행합니다. 실습 파일은 `datasets/pscen/`(git에 올라가지 않음)에 만듭니다.

## 4. 개발 절차

### 4.1. 입력 만들기

회원 5,000명이 0~4999 범위의 성긴 그룹 번호 300종 중 하나를 가진 목록을 만듭니다(고정 시드).

```bash
mkdir -p datasets/pscen
python3 - <<'EOF'
import random, struct
rng = random.Random(3); n = 5000
groups = rng.sample(range(n), 300)          # 성긴 그룹 번호 300종 (0 <= id < N)
ids = [rng.choice(groups) for _ in range(n)]
open("datasets/pscen/members_5000.bin", "wb").write(struct.pack("<I", n) + struct.pack(f"<{n}i", *ids))
print("ids", n, "distinct", len(set(ids)))
EOF
```

```text
ids 5000 distinct 300
```

### 4.2. 모듈 만들기

```bash
isc describe graph_renumber                 # 입력·출력 형식 확인 (선택)
isc new member_renumber --operation graph_renumber
```

생성되는 `modules/member_renumber/module.yaml` — 고칠 것이 없습니다.

```yaml
module:
  id: member_renumber
  name: member_renumber
  version: 0.1.0
operation:
  id: graph_renumber
  version: '1.0'
```

### 4.3. 검사

```bash
isc check member_renumber
```

### 4.4. 에뮬레이션 실행 (hw_emu)

```bash
isc run member_renumber --input datasets/pscen/members_5000.bin --output datasets/pscen/member_renumber.emu.bin
```

### 4.5. 결과 확인

CPU 참조와 바이트 비교하고, 원본 ID와 새 번호가 일대일인지 확인합니다.

```bash
python3 - <<'EOF'
import sys, struct
sys.path.insert(0, "isc/operations/graph_renumber")
from baseline import compute
inp = open("datasets/pscen/members_5000.bin", "rb").read()
got = open("datasets/pscen/member_renumber.emu.bin", "rb").read()
n = struct.unpack_from("<I", inp)[0]; old = struct.unpack_from(f"<{n}i", inp, 4)
clusters = struct.unpack_from("<I", got)[0]; new = struct.unpack_from(f"<{n}i", got, 4)
pairs = set(zip(old, new))
one_to_one = len(pairs) == len(set(old)) == len({b for _, b in pairs}) and set(new) == set(range(clusters))
print("ids", n, "distinct old", len(set(old)), "clusters", clusters, "one-to-one", one_to_one)
print("first 5:", [f"{a}->{b}" for a, b in list(zip(old, new))[:5]])
print("MATCH" if got == compute(inp, {}) else "MISMATCH")
EOF
```

```text
ids 5000 distinct old 300 clusters 300 one-to-one True
first 5: ['4921->0', '1503->1', '2786->2', '4855->3', '1386->4']
MATCH
```

### 4.6. U50 실행 (hw)

```bash
isc run member_renumber --input datasets/pscen/members_5000.bin --output datasets/pscen/member_renumber.hw.bin --target hw
```

플랫폼이 hw_emu 기준 출력을 먼저 만들고 → hw 빌드 → 카드 실행 → 두 출력을 바이트 비교합니다. 이 서버에서 처음 실행(빌드 포함)은 약 53분 걸렸습니다. 4.5의 코드에서 `member_renumber.emu.bin`을 `member_renumber.hw.bin`으로 바꿔 한 번 더 확인합니다.

실제 출력 (발췌):

```text
  input      : 20004 bytes  .../datasets/pscen/members_5000.bin
  output     : 20004 bytes  .../member_renumber_members_5000.hw.bin
  kernel_ms  : 0.726
  validation : PASS  (cross_target) matches reference target output (20004 bytes)
```

### 4.7. 기록 확인

```bash
isc records --module member_renumber
isc show [실행 ID]
```

## 5. 내 데이터에 적용하기

| 바꿀 것 | 방법 |
|---|---|
| ID 목록 | 정점(또는 그룹) ID 열을 `i32`로 저장하고 앞에 개수 `N`을 붙임 |
| N보다 큰 원본 ID | 커널 제약(0 ≤ id < N) 때문에 그대로는 안 됨. CPU에서 먼저 범위를 줄이는 단계가 필요함 (그 단계에서 이미 번호가 정해지므로 이 연산의 이득이 줄어듦) |
| 대응표 저장 | 출력의 `new_id[i]`가 입력 `id[i]`의 새 번호. `(id, new_id)` 쌍을 중복 없이 모으면 대응표 |

## 6. 문제 해결

| 증상 | 원인 | 해결 |
|---|---|---|
| `graph_renumber ids must satisfy 0 <= id < N (the kernel's map tables have N slots)` | 입력 ID가 범위를 벗어남 | 5절의 "N보다 큰 원본 ID" 참고 |
| `graph_renumber header says N=... = ... bytes; received ...` | 헤더 `N`과 파일 길이가 다름 | `4 + 4·N` 바이트로 맞춤 |
| `isc check`에서 include 경로 오류 | `VITIS_LIBRARIES` 미설정 | `export VITIS_LIBRARIES=[경로]` |
| hw 빌드가 메모리 부족으로 중단 | 다른 hw 빌드와 동시에 실행 | 한 번에 하나씩 빌드 (이 서버에서 동시 빌드 중 한 번 발생) |

## 7. 검증 기록

### 7.1. 플랫폼 자체 테스트

| 항목 | 내용 |
|---|---|
| 확인일 | 2026-11-06 |
| 환경 | Ubuntu 22.04.5 LTS (커널 6.8.0-138), Vitis 2025.1, XRT 2.19.194, Vitis Libraries `v2025.1_update2` (커밋 b2c657d), AMD Alveo U50 (`xilinx_u50_gen3x16_xdma_base_5`), 저장소 기준 커밋 `8742866` + 이 연산 폴더(커밋 전) |
| 입력 | 4.1의 `members_5000.bin` (20,004바이트, sha256 `bbc89c67…`) |
| hw_emu | 통과 — 실행 `20261106T004235-a842ca` |
| hw (U50) | **통과** — 실행 `20261106T004235-45d7ae`. hw 출력 = hw_emu 출력(플랫폼 자동 대조) = CPU 참조(`baseline.py`). 묶음 300개, 일대일 대응 확인 |
| 참고 | 첫 hw 빌드는 다른 빌드와 동시에 돌다 메모리 부족으로 실패했고, 단독으로 다시 빌드해 통과 |
| 확인하지 않은 것 | 성능(가속 효과). 이 문서의 입력 한 가지로만 확인. 시나리오 입력에 대한 공식 golden은 없음 |

### 7.2. 공식 라이브러리 테스트

Vitis Libraries 공식 테스트를 고치지 않고 U50에서 빌드·실행했습니다. **입력은 공식 테스트에 들어 있는 데이터**이며 위 시나리오 데이터가 아닙니다.

| 항목 | 내용 |
|---|---|
| 공식 테스트 | `graph/L2/tests/renumber` (Vitis Libraries `v2025.1_update2`) |
| 빌드 | `make all TARGET=hw PLATFORM=xilinx_u50_gen3x16_xdma_5_202210_1`, 이 서버의 메모리 제한 때문에 `VPP_LDFLAGS="--vivado.synth.jobs 4 --vivado.impl.jobs 4"`를 붙임 (2026-11-06, 약 68분). 11-05 첫 빌드는 동시 빌드 중 메모리 부족으로 중단 |
| 실행 | `make run TARGET=hw PLATFORM=xilinx_u50_gen3x16_xdma_5_202210_1` (공식 `data/as-Skitter.txt`) |
| 결과 | **통과** — 2026-11-06, 묶음 6,183개, 공식 비교 `Info: Test passed` |
