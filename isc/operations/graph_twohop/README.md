# `graph_twohop` — 금융 이상거래 탐지: 기기를 공유하는 계좌 쌍 찾기 (연산 선택형 + 사용자 로직)

| 개발 방식 | 사용하는 라이브러리 함수 | 검증 상태 |
|---|---|---|
| 연산 선택형 + 사용자 로직 `isc_score` | Vitis Graph L2 `xf::graph::twoHop` (`kernel/twohop_kernel.cpp`). 라이브러리 호출 뒤에 사용자 함수를 실행 | 플랫폼 자체 테스트 ✅ (1.0 사용자 로직 없음: hw_emu·hw / 1.1 사용자 로직: hw_emu, hw 미실행) · 공식 라이브러리 테스트 ✅ (검증 장치 AMD Alveo U50 · [검증 기록](../../../verification/u50_vitis_libraries/results/graph_twohop.md)) |

## 1. 연산 예시

명의도용 계좌나 대포통장은 한 조직이 같은 휴대폰·PC로 여러 계좌를 번갈아 접속하는 경우가 많습니다. 그래서 서로 다른 명의의 두 계좌가 **같은 기기를 여러 대 함께 썼다면** 조사할 가치가 있습니다.

이 연산은 점검할 계좌 쌍마다 두 계좌가 함께 쓴 기기 수를 세고, 그 수에 사용자가 정한 위험 규칙을 적용해 점수를 냅니다. 기기 수는 Vitis Graph 라이브러리가 세고, 위험 규칙은 사용자가 C++ 함수 하나로 작성합니다. 두 단계 모두 FPGA에서 실행됩니다.

### 1.1. 탐지 코드 안에서 직접 계산하는 기존 방식

```python
import pandas as pd

logs = pd.read_csv("device_logs.csv")
devices = logs.groupby("account_id")["device_id"].apply(set)
pairs = [(3, 5), (3, 412), (17, 20)]
shared = [len(devices[a] & devices[b]) for a, b in pairs]
risk = [n * 10 if n >= 3 else 0 for n in shared]
```

### 1.2. ISC 전주기 시스템 기반의 방식

```bash
python3 isc/operations/graph_twohop/examples/make_input.py            # 접속 기록과 점검할 쌍을 입력 파일로 변환
isc new fraud_link --operation graph_twohop --logic                   # 처음 한 번: 모듈과 사용자 로직 파일(logic.hpp) 만들기
# modules/fraud_link/logic.hpp 의 return 줄을 위험 규칙으로 수정
isc check fraud_link                                                  # 사용자 로직 검사
isc run fraud_link --input datasets/graph_twohop/accounts_devices.bin --output risk.bin --target hw
```

- **입력**: 계좌·기기 접속 기록(그래프)과 점검할 계좌 쌍 목록. 2.1의 스크립트가 만들어 줍니다.
  - 계좌와 기기를 모두 정점(vertex)으로 두고, "계좌가 기기로 접속했다"를 두 정점 사이의 연결로 나타냅니다.
  - 파일 형식은 헤더 `u32 V, u32 E, u32 P, u32 0`, `u32 offset[V+1]`, `u32 index[E]`, `u32 pair[P][2]` 순서입니다(little-endian, CSR 형식).
  - 정점 번호는 0부터 V-1까지 빈틈없이 이어져야 합니다. 원래 번호가 띄엄띄엄하면 먼저 다시 매깁니다([`graph_renumber`](../graph_renumber/README.md)).
- **출력**: `u32 score[P]`. 입력한 쌍 순서대로, 사용자 함수 `isc_score`가 돌려준 점수입니다. 1.1의 `risk`와 같은 값입니다.
- 사용자 로직 없이(`--logic` 생략) 만들면 공유 기기 수가 그대로 출력됩니다(1.1의 `shared`).
- 따라 할 절차는 2절(개발 절차)과 3절(실행 방법)에 있습니다.

## 2. 개발 절차

모든 명령은 저장소 루트에서 실행합니다.

### 2.1. 입력 준비

**사용자가 할 일**: 계좌·기기 접속 기록과 점검할 계좌 쌍을 이 연산의 입력 파일로 변환합니다. 내 데이터를 쓰려면 [`examples/make_input.py`](examples/make_input.py)의 `make_sample()`이 내 데이터를 읽도록 고칩니다.

먼저 예시 데이터로 해 보려면 아래 명령을 실행합니다.

```bash
python3 isc/operations/graph_twohop/examples/make_input.py           # → datasets/graph_twohop/accounts_devices.bin
```

- 예시 데이터는 계좌 800개와 기기 1,200대로 이루어지며, 매번 같은 데이터가 만들어집니다(고정 시드).
- 그중 5개 조직은 조직마다 계좌 8개가 기기 4대를 돌려 씁니다.
- 점검할 쌍은 500개입니다. 절반은 같은 조직 안의 쌍이고, 나머지 절반은 무작위로 고른 쌍입니다.
- 저장 위치를 바꾸려면 `-o 경로`를 붙입니다.

아래는 [`examples/make_input.py`](examples/make_input.py)의 전체 코드입니다(주석 제외). 고치는 곳은 `make_sample()`뿐이고, 입력 형식을 만드는 `encode()`와 `main()`은 그대로 둡니다. `make_sample()`은 다음 두 값을 돌려주면 됩니다.

- `adj`: 정점마다 연결된 정점 번호의 집합을 담은 목록
- `pairs`: 점검할 `(계좌, 계좌)` 쌍의 목록

```python
import argparse
import random
import struct
from pathlib import Path

DEFAULT_OUTPUT = "datasets/graph_twohop/accounts_devices.bin"


def make_sample():
    rng = random.Random(21)
    accounts, devices = 800, 1200
    adj = [set() for _ in range(accounts + devices)]

    def use(account, device):
        adj[account].add(device); adj[device].add(account)

    rings = [list(range(r * 8, r * 8 + 8)) for r in range(5)]
    in_ring = {a for ring in rings for a in ring}
    for r, ring in enumerate(rings):
        pool = [accounts + r * 4 + k for k in range(4)]
        for a in ring:
            for d in rng.sample(pool, rng.choice([3, 4])):
                use(a, d)
    for a in range(accounts):
        if a not in in_ring:
            for _ in range(rng.choice([1, 2])):
                use(a, accounts + 20 + rng.randrange(devices - 20))

    pairs = set()
    while len(pairs) < 250:
        ring = rng.choice(rings); s, d = rng.sample(ring, 2); pairs.add((s, d))
    while len(pairs) < 500:
        s, d = rng.sample(range(accounts), 2); pairs.add((s, d))
    pairs = sorted(pairs, key=lambda p: rng.random())
    return adj, pairs


def encode(adj: list, pairs: list) -> bytes:
    V = len(adj)
    off = [0]; idx = []
    for u in range(V): idx += sorted(adj[u]); off.append(len(idx))
    return (struct.pack("<IIII", V, len(idx), len(pairs), 0) + struct.pack(f"<{V+1}I", *off)
            + struct.pack(f"<{len(idx)}I", *idx) + b"".join(struct.pack("<II", s, d) for s, d in pairs))


def main() -> None:
    parser = argparse.ArgumentParser(description="graph_twohop 입력 파일 만들기")
    parser.add_argument("-o", "--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    adj, pairs = make_sample()
    data = encode(adj, pairs)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print(f"V={len(adj)} E={sum(len(a) for a in adj)} P={len(pairs)} bytes={len(data)}")


if __name__ == "__main__":
    main()
```

**수정 예**: 내 데이터가 CSV 파일 두 개라고 하겠습니다.

- `device_logs.csv`: 한 줄에 `계좌번호,기기번호`. 계좌는 0~49,999번, 기기는 0~79,999번
- `suspect_pairs.csv`: 한 줄에 `계좌번호,계좌번호`

계좌 번호와 기기 번호가 겹치지 않도록 기기 번호에 계좌 수(50,000)를 더해 정점 번호로 씁니다. 위 전체 코드에서 `make_sample()`만 아래 코드로 바꿉니다.

```python
def make_sample():
    accounts, devices = 50000, 80000
    adj = [set() for _ in range(accounts + devices)]
    for line in open("/data/fraud/device_logs.csv"):
        a, d = map(int, line.split(","))
        adj[a].add(accounts + d); adj[accounts + d].add(a)
    pairs = [tuple(map(int, line.split(","))) for line in open("/data/fraud/suspect_pairs.csv")]
    return adj, pairs
```

### 2.2. 모듈 만들기

**사용자가 할 일**: 모듈 이름을 정합니다. 위험 규칙을 넣으려면 `--logic`을 붙입니다. 이 연산에는 따로 정할 파라미터가 없습니다.

```bash
isc new fraud_link --operation graph_twohop --logic
```

- `modules/fraud_link/` 폴더에 `module.yaml`과 사용자 로직 파일 `logic.hpp`가 만들어집니다. `module.yaml`의 `operation.logic: logic.hpp` 항목은 이 모듈이 `logic.hpp`를 쓴다는 뜻입니다.
- 로직 파일을 먼저 작성한 뒤 모듈을 만들어도 됩니다.
  1. `isc template graph_twohop -o risk.hpp`로 시작 파일을 받습니다.
  2. `risk.hpp`를 2.3처럼 고칩니다.
  3. `isc new fraud_link --operation graph_twohop --logic risk.hpp`로 모듈을 만듭니다.
- 위험 규칙 없이 공유 기기 수만 필요하면 `--logic`을 빼고 만들고, 2.3은 건너뜁니다.

### 2.3. 로직 작성

**사용자가 할 일**: `modules/fraud_link/logic.hpp`를 열어 함수 `isc_score`의 `return` 줄을 위험 규칙으로 바꾸고, 그 줄의 `ISC-TODO` 주석을 지웁니다.

아래는 "공유 기기가 3대 이상이면 기기 수 × 10점, 아니면 0점" 규칙으로 고친 파일 전체입니다.

```cpp
#pragma once

#include <stdint.h>

inline uint32_t isc_score(uint32_t src, uint32_t dst, uint32_t count) {
    return count >= 3 ? count * 10 : 0;
}
```

- **호출 시점**: 라이브러리 함수 `xf::graph::twoHop`이 모든 쌍의 공유 기기 수를 센 뒤, 쌍마다 한 번씩 호출됩니다. 함수가 돌려준 값이 그 쌍의 출력이 됩니다.
- **입력값**: `src`와 `dst`는 쌍을 이루는 두 계좌의 번호이고, `count`는 두 계좌가 함께 쓴 기기 수입니다.
- **바꾸면 안 되는 것**: 함수 이름(`isc_score`), 인자 3개의 타입, 반환 타입(`uint32_t`). 인자 이름은 바꿔도 됩니다.
- **쓸 수 있는 것**: 비교·사칙·비트 연산, 상수, 조건식. 보조 함수나 상수를 더 정의해도 됩니다. 예:
  - `return count >= 2 ? count : 0;` — 2대 이상 공유한 쌍만 남기기
  - `return src < 100 ? count * 2 : count;` — 특정 계좌 범위에 가중치 주기
- **쓸 수 없는 것**: 동적 메모리 할당(`new`, `malloc`), 파일·네트워크 입출력(`fopen`, `open`, `socket`), `#pragma HLS INTERFACE`, 다른 쌍의 값 참조.
- 파일을 고치지 않고 두면(`return count;`) 원래 연산과 같은 결과, 즉 공유 기기 수가 그대로 나옵니다.

### 2.4. 검사

**사용자가 할 일**: 빌드하기 전에 로직 파일을 검사합니다.

```bash
isc check fraud_link
```

- 함수 이름·인자·반환 타입이 다르거나 쓸 수 없는 문법이 있으면 `problem`이 표시되고, 고칠 때까지 빌드와 실행이 진행되지 않습니다.
- `ISC-TODO` 주석이 남아 있으면 `warning`이 표시됩니다. 빌드는 진행됩니다.
- FPGA 회로로 변환(합성)되는지는 3절의 첫 에뮬레이션 빌드에서 확인됩니다. 빌드가 실패하면 오류 메시지에 빌드 로그의 끝부분과 로직 파일 경로가 함께 표시됩니다. 전체 로그는 `artifacts/cache/` 아래 해당 빌드 폴더의 `build.log`에 있습니다.

## 3. 실행 방법

**사용자가 할 일**:

- `--input`에 2.1에서 만든 파일을, `--output`에 결과를 저장할 경로를 지정합니다.
- 결과 확인 스크립트 [`examples/check_output.py`](examples/check_output.py)의 `[바꾸는 곳]`에 있는 `score()`를 2.3과 같은 규칙으로 맞춥니다. 2.3의 예시 규칙을 그대로 썼다면 고칠 것이 없습니다.
- 2.2에서 `--logic` 없이 모듈을 만들었다면, 결과 확인 명령에 `--original`을 붙입니다.

```bash
# 1) 에뮬레이션 실행: FPGA 카드 없이 동작을 확인 (처음 한 번 빌드에 수 분)
isc run fraud_link --input datasets/graph_twohop/accounts_devices.bin --output datasets/graph_twohop/fraud_link.emu.bin

# 2) 결과 확인: CPU로 계산한 값과 비교, MATCH가 나오면 정상
python3 isc/operations/graph_twohop/examples/check_output.py datasets/graph_twohop/fraud_link.emu.bin --input datasets/graph_twohop/accounts_devices.bin

# 3) FPGA 카드 실행 (처음 한 번 빌드에 약 50분). 카드 출력이 에뮬레이션 출력과 같은지 플랫폼이 자동으로 비교
isc run fraud_link --input datasets/graph_twohop/accounts_devices.bin --output datasets/graph_twohop/fraud_link.hw.bin --target hw
python3 isc/operations/graph_twohop/examples/check_output.py datasets/graph_twohop/fraud_link.hw.bin --input datasets/graph_twohop/accounts_devices.bin

# 4) 실행 기록 보기
isc records --module fraud_link
isc show [실행 ID]
```

결과 확인 스크립트는 CPU로 공유 기기 수를 센 뒤, 아래 `score()` 함수로 점수를 매겨 FPGA 출력과 비교합니다. `score()`는 2.3의 규칙을 Python으로 옮긴 것이므로, 2.3의 규칙을 바꾸면 이 함수도 함께 바꿉니다.

```python
def score(src: int, dst: int, count: int) -> int:
    return count * 10 if count >= 3 else 0
```

- 결과 확인 스크립트는 점수가 0이 아닌 쌍의 수(`nonzero`)와 점수가 가장 높은 쌍 5개를 보여 줍니다. 예시 데이터에서는 같은 조직에 속한 쌍만 점수를 받습니다.
- **규칙을 바꿀 때**: `logic.hpp`를 고치고 `isc run`을 다시 실행하면, 바뀐 로직으로 자동으로 다시 빌드합니다. 상수 하나만 바꿔도 새로 빌드하므로, 에뮬레이션으로 먼저 확인한 다음 FPGA 카드에서 실행합니다.
- **다른 데이터로 실행할 때**: 2.1처럼 입력 파일을 만든 뒤 `--input`과 `--output`만 바꿔 실행합니다. 로직이 같으면 다시 빌드하지 않습니다.
- **성능 측정**: 사용자 로직이 들어간 모듈은 `isc bench`에서 CPU 기준선과 비교하지 않습니다. CPU 기준선은 원래 연산만 계산하기 때문입니다. 가속 효과를 측정하려면 `--logic` 없이 만든 모듈을 씁니다.
- **FPGA 카드용 빌드는 한 번에 하나씩**: 다른 연산의 카드용 빌드와 동시에 돌리면 메모리가 부족해 멈출 수 있습니다.
- 터미널 출력을 파일로도 남기려면 명령 뒤에 `2>&1 | tee [로그 파일]`을 붙입니다.
