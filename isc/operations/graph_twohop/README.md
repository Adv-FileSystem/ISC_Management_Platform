# `graph_twohop` — SNS 친구 추천 후보의 "친구의 친구" 수 세기 (연산 선택형)

| 개발 방식 | 사용자가 작성하는 것 | 커널 | 파라미터 | 플랫폼 자체 테스트 | 공식 라이브러리 테스트 |
|---|---|---|---|---|---|
| 연산 선택형 | 없음 (`isc new` 한 줄) | Vitis Graph L2 `xf::graph::twoHop` (`kernel/twohop_kernel.cpp`, 공식 테스트 커널과 같은 본문) | 없음 | ✅ 통과 (2026-11-05) | ✅ 통과 (2026-11-05) |

> 이 연산은 시나리오 검증용 **프로토타입**입니다. 공식 커널을 그대로 감쌌고, 커널 이름만 다릅니다.

## 1. 시나리오

| 항목 | 내용 |
|---|---|
| 사용자 | SNS 서비스의 추천 기능 개발자 |
| 상황 | SNS에서 "친구의 친구" 수를 세어 친구 추천 후보 점수로 사용한다 |
| 가속하는 단계 | 정점 쌍별 2-hop 경로 수 계산 |
| 입력 → 출력 | 친구 그래프 + 정점 쌍 목록 → 쌍별 경로 수 |
| 완료 기준 | CPU 계산과 일치 |

## 2. 연산 요약

| 항목 | 내용 |
|---|---|
| 입력 | 리틀 엔디안 이진 파일: `u32 V, u32 E, u32 P, u32 0` + `u32 offset[V+1]` + `u32 index[E]` + `u32 pair[P][2]` (`src, dst`, 0부터) |
| 그래프 | CSR 형식. 무방향 친구 관계는 양방향 간선 두 개로 넣음. `offset`은 0에서 시작해 `E`로 끝나고 줄어들지 않아야 함 |
| 출력 | `u32 count[P]` — 쌍마다 `src → x → dst` 경로 수 (= 공통 친구 수) |
| 파라미터 | 없음 |
| CPU 참조 | `baseline.py` (이웃 목록 순회). 공식 테스트 데이터의 golden 1,000쌍과 같은 값이 나오는 것을 확인함 |
| 메모리 | 포트 6개를 HBM 뱅크 0~5에 하나씩 연결 (`operation.yaml`) |

작은 예: 친구 관계 `0–1, 0–2, 1–3, 2–3`에서 쌍 `(0, 3)`의 값은 2 (0→1→3, 0→2→3)

## 3. 준비

- 공통 설치와 환경 확인: [README 1절](../../../README.md#1-설치-및-환경-구성)
- 빌드에 **`VITIS_LIBRARIES`** 가 필요합니다: `export VITIS_LIBRARIES=[Vitis Libraries 2025.1 경로]`
- 모든 명령은 **저장소 루트**에서 실행합니다. 실습 파일은 `datasets/pscen/`(git에 올라가지 않음)에 만듭니다.

## 4. 개발 절차

### 4.1. 입력 만들기

친구 1,000명 그래프(고정 시드)와 추천 후보 500쌍을 만듭니다. 쌍의 절반은 "친구의 친구이지만 아직 친구가 아닌" 사람, 나머지 절반은 무작위 대조군입니다.

```bash
mkdir -p datasets/pscen
python3 - <<'EOF'
import random, struct
rng = random.Random(11); V = 1000
adj = [set() for _ in range(V)]
for u in range(V):                      # 사람마다 무작위 친구 4명 (양방향)
    for _ in range(4):
        w = rng.randrange(V)
        if w != u: adj[u].add(w); adj[w].add(u)
off = [0]; idx = []
for u in range(V): idx += sorted(adj[u]); off.append(len(idx))
rng = random.Random(12); pairs = set()
while len(pairs) < 250:                 # 친구의 친구(아직 친구 아님) = 추천 후보
    s = rng.randrange(V); f = rng.choice(sorted(adj[s])); d = rng.choice(sorted(adj[f]))
    if d != s and d not in adj[s]: pairs.add((s, d))
while len(pairs) < 500:                 # 무작위 대조군
    s, d = rng.randrange(V), rng.randrange(V)
    if s != d and d not in adj[s]: pairs.add((s, d))
pairs = sorted(pairs, key=lambda p: rng.random())
data = (struct.pack("<IIII", V, len(idx), len(pairs), 0) + struct.pack(f"<{V+1}I", *off)
        + struct.pack(f"<{len(idx)}I", *idx) + b"".join(struct.pack("<II", s, d) for s, d in pairs))
open("datasets/pscen/friends_1000_fof.bin", "wb").write(data)
print(f"V={V} E={len(idx)} P={len(pairs)} bytes={len(data)}")
EOF
```

```text
V=1000 E=7962 P=500 bytes=39868
```

### 4.2. 모듈 만들기

```bash
isc describe graph_twohop                   # 입력·출력 형식 확인 (선택)
isc new friend_2hop --operation graph_twohop
```

생성되는 `modules/friend_2hop/module.yaml` — 고칠 것이 없습니다.

```yaml
module:
  id: friend_2hop
  name: friend_2hop
  version: 0.1.0
operation:
  id: graph_twohop
  version: '1.0'
```

### 4.3. 검사

```bash
isc check friend_2hop
```

### 4.4. 에뮬레이션 실행 (hw_emu)

```bash
isc run friend_2hop --input datasets/pscen/friends_1000_fof.bin --output datasets/pscen/friend_2hop.emu.bin
```

처음 실행하면 hw_emu 빌드(수 분)를 한 뒤 실행합니다.

### 4.5. 결과 확인

연산 폴더의 CPU 참조(`baseline.py`)로 같은 입력을 계산해 바이트 비교합니다.

```bash
python3 - <<'EOF'
import sys, struct
sys.path.insert(0, "isc/operations/graph_twohop")
from baseline import compute
inp = open("datasets/pscen/friends_1000_fof.bin", "rb").read()
got = open("datasets/pscen/friend_2hop.emu.bin", "rb").read()
ref = compute(inp, {})
counts = struct.unpack(f"<{len(got) // 4}I", got)
print("pairs", len(counts), "nonzero", sum(1 for c in counts if c), "max", max(counts))
print("MATCH" if got == ref else "MISMATCH")
EOF
```

```text
pairs 500 nonzero 262 max 2
MATCH
```

### 4.6. U50 실행 (hw)

```bash
isc run friend_2hop --input datasets/pscen/friends_1000_fof.bin --output datasets/pscen/friend_2hop.hw.bin --target hw
```

플랫폼이 hw_emu 기준 출력을 먼저 만들고 → hw 빌드 → 카드 실행 → 두 출력을 바이트 비교합니다. 이 서버에서 처음 실행(빌드 포함)은 약 50분 걸렸습니다. 4.5의 코드에서 `friend_2hop.emu.bin`을 `friend_2hop.hw.bin`으로 바꿔 한 번 더 확인합니다.

실제 출력 (발췌):

```text
  input      : 39868 bytes  .../datasets/pscen/friends_1000_fof.bin
  output     : 2000 bytes  .../friend_2hop_friends_1000_fof.hw.bin
  kernel_ms  : 1.335
  validation : PASS  (cross_target) matches reference target output (2000 bytes)
```

### 4.7. 기록 확인

```bash
isc records --module friend_2hop
isc show [실행 ID]
```

## 5. 내 데이터에 적용하기

| 바꿀 것 | 방법 |
|---|---|
| 그래프 | 사용자 ID를 0부터 연속 번호로 바꾼 뒤(필요하면 [`graph_renumber`](../graph_renumber/README.md)) CSR로 저장. 무방향이면 양방향 간선을 모두 넣음 |
| 후보 쌍 | 점수를 매길 `(사용자, 후보)` 쌍을 파일 끝에 넣고 헤더의 `P`를 맞춤 |
| 결과 사용 | 출력 순서는 입력 쌍 순서와 같음. 값이 클수록 공통 친구가 많음 |

## 6. 문제 해결

| 증상 | 원인 | 해결 |
|---|---|---|
| `graph_twohop header says V=..., E=..., P=... = ... bytes; received ...` | 헤더 수와 파일 길이가 다름 | 헤더의 `V·E·P`와 배열 길이를 맞춤 |
| `graph_twohop offset must start at 0, end at E and never decrease` | CSR `offset`이 잘못됨 | `offset[0]=0`, `offset[V]=E`, 단조 증가로 만듦 |
| `graph_twohop vertex id out of range` | `index`나 쌍에 `V` 이상인 ID | ID를 0~V-1로 재번호화 |
| `isc check`에서 include 경로 오류 | `VITIS_LIBRARIES` 미설정 | `export VITIS_LIBRARIES=[경로]` |
| hw 빌드가 메모리 부족으로 중단 | 다른 hw 빌드와 동시에 실행 | 한 번에 하나씩 빌드 (이 서버에서 동시 빌드 중 한 번 발생) |

## 7. 검증 기록

### 7.1. 플랫폼 자체 테스트

| 항목 | 내용 |
|---|---|
| 확인일 | 2026-11-05 |
| 환경 | Ubuntu 22.04.5 LTS (커널 6.8.0-138), Vitis 2025.1, XRT 2.19.194, Vitis Libraries `v2025.1_update2` (커밋 b2c657d), AMD Alveo U50 (`xilinx_u50_gen3x16_xdma_base_5`), 저장소 기준 커밋 `8742866` + 이 연산 폴더(커밋 전) |
| 입력 | 4.1의 `friends_1000_fof.bin` (39,868바이트, sha256 `514c0a06…`) |
| hw_emu | 통과 — 실행 `20261105T215826-200853` |
| hw (U50) | **통과** — 실행 `20261105T215826-1c5266`. hw 출력 = hw_emu 출력(플랫폼 자동 대조) = CPU 참조(`baseline.py`). 500쌍 중 0이 아닌 값 262쌍 |
| 참고 | 첫 hw 빌드는 다른 빌드와 동시에 돌다 메모리 부족으로 실패했고, 단독으로 다시 빌드해 통과 |
| 확인하지 않은 것 | 성능(가속 효과). 이 문서의 입력 한 가지로만 확인 |

### 7.2. 공식 라이브러리 테스트

Vitis Libraries 공식 테스트를 고치지 않고 U50에서 빌드·실행했습니다. **입력은 공식 테스트에 들어 있는 데이터**이며 위 시나리오 데이터가 아닙니다.

| 항목 | 내용 |
|---|---|
| 공식 테스트 | `graph/L2/tests/twoHop` (Vitis Libraries `v2025.1_update2`) |
| 빌드 | `make all TARGET=hw PLATFORM=xilinx_u50_gen3x16_xdma_5_202210_1` (2026-11-04, 약 67분) |
| 실행 | `make run TARGET=hw PLATFORM=xilinx_u50_gen3x16_xdma_5_202210_1` (공식 `data/` 그래프와 쌍 목록) |
| 결과 | **통과** — 2026-11-05, 공식 golden 비교 `Info: Test passed` |
