# Alveo U50 × Vitis Libraries 연산 검증 (연차보고서 검증 자료)

작성 2026-11-04 · 시나리오 기반 검증 기간 2026-11-04 ~ 11-06 · 보고서 제출 11-13

## 1. 목적과 범위

ISC 전주기 관리 시스템은 **하드웨어 지식이 없는 소프트웨어 개발자가 자기 데이터 처리 작업의 일부를 저장 장치 쪽 FPGA로
옮겨(오프로드) 가속하도록** 돕는 시스템이다. 사용자는 연산을 고르거나 함수 하나만 작성하고, 커널·메모리 연결·호스트 코드·빌드는
시스템이 맡는다. 이 문서는 그런 사용자가 **실제로 이 시스템을 쓰게 될 일상적인 작업**을 시나리오로 정하고(3절), 각 시나리오에 쓰이는
연산이 `docs/research/vitis_libraries_u50_support.md`의 지원 표(10개 라이브러리)에서 우리 U50 환경으로 정답을 내는지, 그리고
CPU보다 빠른지 확인한다(4절).

모든 시나리오에서 확인하는 것은 네 가지다.

| 관점 | 질문 | 확인 방법 |
|---|---|---|
| 정확성 | 결과가 CPU·표준 도구와 같은가 | 바이트 일치, 또는 공식 테스트가 정한 허용오차 |
| 개발 용이성 | 사용자가 커널·호스트 코드를 쓰지 않고 끝낼 수 있는가 | 사용자가 만든 파일과 입력한 명령, 각 단계 소요 시간 기록 |
| 가속 효과 | CPU에서 하던 것보다 빠른가, 아니라면 왜인가 | `isc bench`·`isc report` 판정(가속 이득 / 조건부 / 커널 개선 여지 / 전송 바운드) 또는 공식 호스트의 커널 시간과 CPU 시간 비교 |
| 재현성 | 같은 결과를 나중에 다시 확인할 수 있는가 | 실행 기록(입력·출력 해시, 빌드 지문, 시각)으로 재실행 |

| 항목 | 값 |
|---|---|
| 카드 / 셸 | AMD Alveo U50, `xilinx_u50_gen3x16_xdma_base_5` (개발 플랫폼 `xilinx_u50_gen3x16_xdma_5_202210_1`) |
| 툴 | Vitis 2025.1, XRT 2.19.194 (2025.1) |
| 라이브러리 | Vitis Libraries `v2025.1_update2` (커밋 b2c657d), 수정하지 않은 별도 체크아웃 `vitis_libs_u50v` |
| 호스트 | Ubuntu 22.04, i9-11900K 16스레드, RAM 94 GB, PCIe Gen3 x8 연결 |
| 관리 시스템 | ISC Management Platform (이 저장소) |

## 2. 검증 경로와 등급

| 등급 | 이름 | 절차 | PASS 조건 |
|---|---|---|---|
| **P** | 플랫폼 전주기 | ISC 플랫폼 모듈로 정의 → `isc check` → `hw_emu` 기준 실행 → `hw` 빌드 → U50 실행 → 비교 → `records.jsonl` 기록 | hw 출력 = hw_emu 출력(바이트), hw 출력 = CPU 기준 |
| **V** | 공식 테스트 실카드 | 공식 테스트(소스·입력·골든 무수정)를 우리 셸로 `hw` 빌드 → U50에서 `make run` | 공식 호스트의 골든 비교 PASS (호스트에 비교가 없으면 우리가 CPU 기준과 비교) |
| **B** | 빌드만 성공 | hw 빌드는 되었으나 실행 실패 또는 결과 불일치 | — (원인과 근거 로그 기록) |
| **X** | 미실시 | 시간·환경·플랫폼 제약으로 실행하지 않음 | — (사유, 필요한 작업, 예상 소요 기록) |

- 판정은 정수·바이트 결과는 완전 일치, 부동소수 결과는 공식 테스트가 정한 허용오차를 쓴다.
- 모든 빌드·실행은 시각, 소요 시간, 종료 코드, 로그 경로를 남긴다 (`results/builds.jsonl`, `results/runs.jsonl`, 플랫폼 `artifacts/records.jsonl`).
- 빌드 로그에서 **달성 클럭(자동 축소 여부)** 을 함께 기록한다. 타이밍 미달로 클럭이 내려가면 결과가 맞아도 표에 적는다.
- 카드 실행은 플랫폼과 같은 장치 잠금(`artifacts/locks/xrt-hw-0.lock`)을 잡고 한 번에 하나씩 한다.

## 3. 사용자 시나리오

### 3.0 모든 시나리오에 공통인 사용 흐름

사용자가 시스템에서 거치는 단계는 같다. 시나리오마다 다른 것은 데이터, 고르는 연산, 확인 기준이다.

| 단계 | 사용자가 하는 일 | 명령 | 시스템이 하는 일 |
|---|---|---|---|
| ① 찾기 | 자기 작업에 맞는 연산이 있는지 본다 | `isc operations`, `isc describe <연산>` | 입출력 형식, 파라미터, CPU 기준선 유무 안내 |
| ② 만들기 | 연산을 고르거나(연산 선택형) 조건 함수 하나를 쓴다(Hook형) | `isc new` / `isc template` + `isc run 로직.hpp` | 모듈 폴더, 커널 래퍼, 메모리 연결, 호스트 어댑터 생성 |
| ③ 검사 | 설정이 맞는지 본다 | `isc check` | 환경 변수, 타입, 금지 구문, 입력 형식 검사 |
| ④ 작게 확인 | 작은 샘플로 동작을 본다 | `isc run --target hw_emu` | 에뮬레이션 빌드(수 분)와 실행 |
| ⑤ 실카드 실행 | 실제 데이터로 돌린다 | `isc run --target hw` | hw_emu 기준 결과를 먼저 만들고, hw 빌드(수십 분, 캐시), 카드 실행, 두 결과 바이트 비교 |
| ⑥ 가속 판정 | CPU보다 빠른지 본다 | `isc plan` → `isc bench` → `isc report` | 크기별 반복 측정, CPU 기준선 대비 배율, 전송 한계로 본 최대 배율, 판정 |
| ⑦ 기록 확인 | 나중에 같은 결과를 확인한다 | `isc records`, `isc show <실행 ID>` | 입력·출력 해시, 빌드 지문, 단계별 시간 |

플랫폼 연산이 아직 없는 경우(여러 커널이 이어진 설계 등)는 같은 시나리오를 공식 테스트로 실카드 검증하고,
플랫폼에 넣는 데 필요한 것을 제약사항으로 적는다(2절의 V 경로).

**가속 판정 방법.** 플랫폼 `isc report`의 규칙을 모든 시나리오에 같게 쓴다.

- 속도 배율 = CPU 기준선 시간(중앙값) ÷ 장치 처리 시간(호스트→장치 전송 + 커널 + 장치→호스트 전송, 중앙값). 장치를 여는 시간(수 초, 1회성)은 따로 적는다.
- 전송 한계 배율 = CPU 시간 ÷ 전송 시간. 커널이 0초라고 가정한 상한이며, 1보다 작으면 커널을 아무리 개선해도 이득이 없다(전송 바운드).
- 판정: 모든 크기에서 1.2배 이상이면 "가속 이득 있음", 일부 크기에서만 1배 이상이면 "조건부", 그 밖에는 커널 개선 여지 또는 전송 바운드.
- `isc bench`는 형식 없는 바이트 입력(0, 무작위, 텍스트)만 만들 수 있어 압축·체크섬에만 바로 쓴다. 영상·행렬 연산은 `isc run --target hw` 실행 기록의
  전송·커널 시간과 같은 입력의 CPU 기준선 시간으로 같은 공식을 적용한다(`tools/accel.py`). 공식 테스트는 호스트가 출력한 커널 시간과 CPU 표준 구현 시간을
  비교하며, CPU 구현을 쉽게 마련할 수 있는 연산만 한다.
- 이 PC의 U50은 PCIe Gen3 x8로 연결되어 있어(방향당 이론 7.88 GB/s) 전송 바운드 판정이 x16 연결보다 불리하게 나온다. 결과표에 함께 적는다.

### 3.1 US-1 서비스 로그·백업 파일 압축 저장

| 항목 | 내용 |
|---|---|
| 사용자 | 웹 서비스의 백엔드·인프라 개발자 |
| 상황 | 서비스 로그(텍스트)가 하루 수 GB씩 쌓이고, 저장 전에 압축한다. 지금은 서버 CPU가 gzip을 돌린다 |
| 목적 | 압축을 저장 장치 쪽에서 처리해 서버 CPU를 서비스에 돌리고 저장 용량을 줄인다. 압축본은 표준 도구로 풀려야 한다 |
| 쓰는 연산 | LZ4, GZIP/ZLIB, Snappy, LIBZ API(기존 zlib 사용 프로그램을 고치지 않고 가속) — Data Compression |
| 시스템 사용 | `isc describe lz4` → `isc new log_lz4 --operation lz4` → 작은 로그로 hw_emu → 8 MiB 로그로 hw → 1·16·64 MiB로 bench/report |
| 확인할 것 | 압축본을 CPU 표준 구현으로 풀면 원본과 같다 / 압축률 / CPU 압축 대비 처리 시간 |
| 성공 기준 | 왕복 바이트 일치, 압축률과 가속 판정이 기록됨 |
| 세부 검증 | CP-1 ~ CP-5 |

### 3.2 US-2 백업·전송 파일 무결성 확인

| 항목 | 내용 |
|---|---|
| 사용자 | 스토리지·백업 운영자 |
| 상황 | 백업하거나 다른 서버로 보낸 대용량 파일이 깨지지 않았는지 체크섬으로 확인한다 |
| 목적 | 체크섬 계산을 저장 장치 쪽에서 처리하고, 값은 기존 도구(zlib, cksum류)와 같아야 기존 기록과 맞춰 볼 수 있다 |
| 쓰는 연산 | CRC32, Adler-32 — Security |
| 시스템 사용 | `isc new backup_crc --operation crc32` → hw_emu → hw → 1 B·3 B·256 KiB·64 MiB 파일 확인 → bench/report |
| 확인할 것 | 모든 크기에서 표준 값과 같다 / CPU 대비 처리 시간 |
| 성공 기준 | 경계 크기(1 B, 3 B)까지 표준 값과 일치 |
| 세부 검증 | SE-1, SE-2 |

### 3.3 US-3 판매 데이터 분석 질의

| 항목 | 내용 |
|---|---|
| 사용자 | 데이터 분석가, 사내 리포트를 만드는 백엔드 개발자 |
| 상황 | 주문(orders)·주문상세(lineitem) 표로 "기간별 매출" 같은 질의를 매일 돌린다. 표가 커서 조인·필터에 시간이 든다 |
| 목적 | 조인·사전 필터·분할을 저장 장치 쪽에서 하고 결과만 받는다. 단순 조건(예: 금액 1,000 이상 5,000 미만)은 직접 조건식을 써서 걸러낸다 |
| 쓰는 연산 | 해시 조인, 블룸 필터, 해시 파티셔닝, GQE 3-in-1 — Database. 사용자 조건식은 플랫폼 Hook형 `filter`·`aggregate`(2026-10 hw 검증 기록 있음) |
| 시스템 사용 | 조건 필터: `isc template filter --type u32 -o amount.hpp`에서 `return` 줄만 작성 → `isc run amount.hpp --input 금액.bin --target hw`. 조인·분할: 공식 GQE 테스트 |
| 확인할 것 | CPU로 같은 질의를 돌린 결과와 행·합계가 같다 |
| 성공 기준 | 행 단위 일치 |
| 세부 검증 | DB-1 ~ DB-4, DB-ref |

### 3.4 US-4 추천·관계 분석

| 항목 | 내용 |
|---|---|
| 사용자 | 추천·소셜 서비스 개발자 |
| 상황 | 사용자·상품·친구 관계를 그래프로 저장해 두고 주기적으로 분석한다 |
| 목적 | 영향력 순위(PageRank), 비슷한 사용자·상품 찾기(코사인 유사도), 친구의 친구 수(2-hop), 커뮤니티 묶기(Louvain)와 그 전처리(정점 번호 재할당, 병합)를 장치에서 처리 |
| 쓰는 연산 | PageRank, 코사인 유사도(General·Sparse·Dense), 2-hop, Louvain, Renumber, Merge — Graph |
| 시스템 사용 | 공식 테스트(그래프 형식 입력과 다중 HBM 포트가 필요해 플랫폼 연산은 아직 없음) |
| 확인할 것 | CPU 그래프 계산(공식 골든)과 순위·유사도·개수가 같다 |
| 성공 기준 | 공식 골든 비교 통과 |
| 세부 검증 | GR-1 ~ GR-7 |

### 3.5 US-5 고객 데이터 중복 정리

| 항목 | 내용 |
|---|---|
| 사용자 | CRM·마케팅 데이터 담당자 |
| 상황 | 같은 고객이 이름·주소 철자를 조금씩 다르게 여러 번 등록되어 있다 |
| 목적 | 중복 레코드를 찾아 하나로 묶어 고객 수와 발송 대상을 바로잡는다 |
| 쓰는 연산 | 중복 레코드 매칭(색인, 조건 비교, 쌍 생성, 유사도 점수, 군집화) — Data Analytics |
| 시스템 사용 | 공식 테스트 |
| 확인할 것 | 찾은 중복 군집이 공식 골든과 같다 |
| 성공 기준 | 골든 비교 통과 |
| 세부 검증 | DA-1 |

### 3.6 US-6 AI 학습용 이미지 전처리

| 항목 | 내용 |
|---|---|
| 사용자 | AI 모델 개발자 (하드웨어 지식 없음) |
| 상황 | 저장된 JPEG 사진 수십만 장을 학습 입력으로 바꾼다: 디코딩 → 크기 축소 → 잡음 제거, 필요하면 윤곽 특징 추출 |
| 목적 | 전처리를 저장 장치 쪽에서 처리해 학습 서버 CPU 병목을 줄인다 |
| 쓰는 연산 | JPEG 디코딩, Bicubic resize — Codec / Gaussian 필터, Bilinear resize, Sobel 기울기, ISP 파이프라인 — Vision |
| 시스템 사용 | `isc describe image_resize` → `isc new prep_resize --operation image_resize --set out_rows=1080 --set out_cols=1920` → 공식 샘플 영상으로 hw_emu → 4K 영상으로 hw → bench/report. Gaussian·Sobel도 같은 방법 |
| 확인할 것 | OpenCV·PIL 결과와 공식 testbench 허용오차 안에서 같다 / 장당 처리 시간이 CPU보다 짧은가 |
| 성공 기준 | 허용오차 통과(Gaussian ≤ 2, resize ≤ 5, Sobel 0), 가속 판정 기록 |
| 세부 검증 | CO-1, CO-2, VI-1 ~ VI-4 |
| 제약 | 디코딩 → 크기 조정을 한 번에 잇는 실행(파이프라인)은 플랫폼에 아직 없어 단계마다 따로 실행한다 |

### 3.7 US-7 공학 시뮬레이션·수치 계산

| 항목 | 내용 |
|---|---|
| 사용자 | 구조·열 해석 연구자, 수치 계산을 쓰는 엔지니어 |
| 상황 | 큰 연립방정식 Ax=b를 반복해서 풀고, 행렬-벡터 곱을 대량으로 계산한다(신경망 추론의 완전연결층 포함) |
| 목적 | 반복 계산을 장치에서 처리해 해석 시간을 줄인다 |
| 쓰는 연산 | CG solver(GEMV 기반, Jacobi 전처리), FCN — HPC / GEMV — BLAS |
| 시스템 사용 | GEMV: `isc new sim_gemv --operation gemv_i32` → hw_emu → hw → bench/report. CG solver: 공식 테스트(커널 7개가 이어진 설계) |
| 확인할 것 | numpy·정답 해와 같다(정수 GEMV는 바이트 일치, CG는 허용오차 1e-8) |
| 성공 기준 | 일치, 가속 판정 기록 |
| 세부 검증 | HP-1, HP-2, BL-1 |

### 3.8 US-8 금융 상품 가격 계산

| 항목 | 내용 |
|---|---|
| 사용자 | 금융 리스크 분석가 |
| 상황 | 매일 밤 보유한 유럽형 옵션의 가격을 Monte Carlo 시뮬레이션으로 다시 계산한다 |
| 목적 | 시뮬레이션을 장치에서 돌려 야간 배치 시간을 줄인다 |
| 쓰는 연산 | MCEuropeanEngine 등 가격 산정 엔진 — Quantitative Finance |
| 시스템 사용 | 공식 테스트 |
| 확인할 것 | 공식 골든 가격과 상대오차 2% 이내 |
| 성공 기준 | 모든 테스트 케이스 허용오차 통과 |
| 세부 검증 | QF-1, QF-2 |

### 3.9 시나리오와 지원 표의 대응

| 라이브러리 | 시나리오 | 이번 검증 경로 |
|---|---|---|
| Database | US-3 판매 데이터 분석 | 공식 테스트 (+ 플랫폼 Hook형 조건 필터·집계) |
| Graph | US-4 추천·관계 분석 | 공식 테스트 |
| Data Compression | US-1 로그 압축 | 플랫폼 `lz4` + 공식 테스트 |
| Data Analytics | US-5 고객 중복 정리 | 공식 테스트 |
| Codec | US-6 이미지 전처리 | 공식 테스트 |
| HPC | US-7 수치 계산 | 공식 테스트 |
| Security | US-2 무결성 확인 | 플랫폼 `crc32`·`adler32` + 공식 테스트 |
| Vision | US-6 이미지 전처리 | 플랫폼 `image_gaussian`·`image_resize`·`image_sobel` |
| Quantitative Finance | US-8 금융 가격 계산 | 공식 테스트 |
| BLAS | US-7 수치 계산 | 플랫폼 `gemv_i32` |

## 4. 세부 검증 항목 (연산별)

각 사용자 시나리오에 쓰이는 연산을 아래 항목으로 확인한다. 입력 크기는 공식 테스트 기본값이다. "경로"는 2절의 검증 등급이며, 결과는 6절에 적는다.

### 4.1 Database (GQE 3-in-1, L2)

| ID | 연산 | 시나리오 | 경로 |
|---|---|---|---|
| DB-1 | 해시 조인 | `gqeJoin`: 호스트가 TPC-H 형태 orders·lineitem 표를 생성(`-scale 10000`)해 GQE 커널(join 모드)로 조인하고, CPU 해시 조인 결과와 행 단위 비교 | V |
| DB-2 | 블룸 필터 | `gqeJoin_bloomfilter`: 같은 3-in-1 커널을 bloom-filter 모드로 실행(`-scale 100000`), CPU 결과와 비교. DB-1과 커널 소스·컴파일 플래그(PART_FLOW=0)가 같으므로 DB-1 xclbin으로도 실행해 본다 | V |
| DB-3 | 해시 파티셔닝 | `gqePart`: 3-in-1 커널을 partition 모드(PART_FLOW=1)로 빌드, `-scale 100000` 표를 분할해 CPU 분할 결과와 비교 | V |
| DB-4 | 3-in-1 조합 실행 | DB-1·DB-2를 **한 xclbin**에서 연속 실행해 한 커널이 두 연산을 처리함을 확인. 파티셔닝은 공식 빌드가 별도 플래그라 같은 xclbin으로는 확인 불가(제약으로 기록) | V |
| DB-ref | L1 해시 조인 v3 (참고) | `L1/benchmarks/hash_join_v3_sc`(2026-08 빌드 재사용, 220 MHz): TPC-H lineitem 6,001,215행 ⋈ orders 227,597행, 수익 합계를 CPU와 비교 | V |

### 4.2 Graph (L2)

| ID | 연산 | 시나리오 | 경로 |
|---|---|---|---|
| GR-1 | 다중 채널 PageRank | `pagerank_multi_channels`: 공식 그래프 데이터로 PageRank 반복 후 공식 골든과 비교. 단일 채널 `benchmarks/pagerank`(2026-08 빌드)도 함께 실행 | V |
| GR-2 | 코사인 유사도 (General) | `general_similarity`: CSR 희소 그래프, 정점 3 기준 상위 32개 유사도를 골든과 비교 | V |
| GR-3 | 코사인 유사도 (Sparse / Dense) | `sparse_similarity`(상위 32) + `dense_similarity`(2026-08 빌드) 골든 비교 | V |
| GR-4 | 2-Hop 경로 수 | `twoHop`: CSR 그래프와 정점 쌍 목록으로 2-hop 경로 수를 세어 골든과 비교 | V |
| GR-5 | Louvain 모듈성 최적화 | `louvain_fast`: 공식 테스트 허용 장치는 U55C뿐이지만 `conn_u50.cfg`가 있으므로 U50 빌드를 시도, 모듈성 Q 값을 CPU와 비교 | V 시도 |
| GR-6 | 정점 번호 재할당 | `renumber`: as-Skitter 그래프의 군집 ID를 재번호화해 골든과 비교 | V |
| GR-7 | 그래프 병합 | `merge`: 공식 예제 그래프(offset/edge/weight/cluster)를 병합해 결과 파일을 골든과 비교 | V |

### 4.3 Data Compression

| ID | 연산 | 시나리오 | 경로 |
|---|---|---|---|
| CP-1 | GZIP 압축·해제 | `L3/demos/gzip_hbm`: `sample.txt`를 압축 → 해제 → 원본과 바이트 비교. 압축 결과는 CPU `gzip -d`로도 풀어 비교 | V |
| CP-2 | ZLIB 압축·해제 | 같은 xclbin(GZIP/ZLIB 겸용)으로 zlib 형식 실행, CPU `zlib.decompress`로 확인 | V |
| CP-3 | LIBZ API 가속 | `L3/demos/libzso`: libz.so 대체 라이브러리로 deflate/inflate 호출, 입출력 비교 | V 시도 |
| CP-4 | LZ4 압축·해제 | 플랫폼 내장 `lz4` 연산(Vitis L1 `lz4_compress.hpp` 래핑, 2026-08 hw 검증 기록 있음) + 공식 `L2/tests/lz4_compress`(2026-07 빌드) | P + V |
| CP-5 | Snappy 압축·해제 | `L2/tests/snappy_compress`: 공식 허용 장치는 U250 등이라 U50 빌드를 시도, CPU snappy 해제로 확인 | V 시도 |

### 4.4 Data Analytics

| ID | 연산 | 시나리오 | 경로 |
|---|---|---|---|
| DA-1 | 중복 레코드 매칭 | `L2/demos/text/dup_match`: `test.csv` 레코드의 중복 군집을 찾아 `golden.txt`와 비교. 인덱스·조건 비교·쌍 생성·유사도 점수·군집화는 이 파이프라인의 내부 단계라 따로 검증하지 않음(표 비고와 같음) | V |

### 4.5 Codec

| ID | 연산 | 시나리오 | 경로 |
|---|---|---|---|
| CO-1 | JPEG 디코딩 | `L2/demos/jpegDec`: `t0.jpg`를 디코딩. 공식 호스트에 정답 비교가 없으므로 출력 YUV를 CPU 디코더(PIL)와 비교(IDCT 차이로 ±소수 단계 허용, 최대 오차 기록) | V |
| CO-2 | 이미지 크기 조정 (Bicubic) | `L2/demos/resize`: `t0.raw`의 앞 512×512 바이트(공식 호스트와 같게) → 64×64(공식 인자), 256×256, 300×300. 공식 호스트에 정답 비교가 없고 `description.json`이 적은 `t0_resized.raw`는 저장소에 없다. 라이브러리에 별도 CPU 참조 구현도 없어서, **같은 커널 소스를 CPU에서 C 시뮬레이션한 결과와 바이트 비교**한다(`tools/csim/codec_resize_tb.cpp`). OpenCV `INTER_CUBIC`과는 좌표 정렬·가중치 정의가 달라 참고값으로만 기록 | V |

### 4.6 HPC

| ID | 연산 | 시나리오 | 경로 |
|---|---|---|---|
| HP-1 | 켤레기울기법 해법 (CG, GEMV 기반, Jacobi 전처리) | `L2/benchmarks/cg_gemv_jacobi`(2026-08 빌드): 공식 생성기(`genMat.py`)로 N=2048 SPD 행렬(조건수 128)과 정답 x를 만들고 `Ax=b`를 풀어 x와 비교(허용오차 1e-8, 최대 100회). 2025.1 Makefile이 실행 인자와 데이터 생성 규칙이 든 `params.mk`를 include하지 않아 `make -f Makefile -f params.mk`로 실행 | V |
| HP-2 | 완전연결망 연산 (FCN) | `L2/tests/mlp/fcn_1CU`: 공식 허용 장치는 U250. U50 빌드를 시도해 CPU 행렬곱 결과와 비교 | V 시도 |

### 4.7 Security

| ID | 연산 | 시나리오 | 경로 |
|---|---|---|---|
| SE-1 | Adler-32 | (P) 플랫폼 내장 `adler32`: 1 B·3 B·256 KiB(hw_emu 기준 실행 포함)·64 MiB 입력을 `zlib.adler32`와 비교. (V) 공식 `L1/benchmarks/adler32` 골든 비교 | P + V |
| SE-2 | CRC32 | (P) 플랫폼 내장 `crc32`: 같은 입력을 `zlib.crc32`와 비교. (V) 공식 `L1/benchmarks/crc32`(허용 장치에 U50 없음) 빌드 시도 | P + V |

### 4.8 Vision

공식 Vision L2 테스트(656건)의 허용 장치는 zcu104·vck190·aws-vu9p-f1이고 U50이 없다(U50 허용은 L3 예제 17건). 또 L2/L3 호스트는
OpenCV C++ 라이브러리가 필요한데 이 PC에는 없고 관리자 권한도 없다. 그래서 대표 함수를 **플랫폼 연산으로 감싸 P 경로로 검증**한다.
커널 본문과 설정은 공식 L2 예제와 같게 두고, 기준값은 Python OpenCV(로컬 설치)로 만든다. 허용오차는 공식 testbench의
`analyzeDiff` 값(Gaussian 2, resize 5)을 그대로 쓴다. 플랫폼의 CPU 대조는 완전 일치만 지원하므로 허용오차 비교는 `tools/compare.py`가 한다.

| ID | 연산 | 시나리오 | 경로 |
|---|---|---|---|
| VI-1 | 이미지 필터링 | 플랫폼 연산 `image_gaussian`(`xf::cv::GaussianBlur` 3×3, σ=0.5, 가장자리 0): 공식 샘플 영상 128×128(hw_emu 기준 실행 포함), 512×512, 1920×1080을 OpenCV `GaussianBlur`와 비교, 화소 차이 ≤ 2 | P |
| VI-2 | 이미지 변환·크기 조정 | 플랫폼 연산 `image_resize`(`xf::cv::resize` bilinear, 공식 L2 설정: 최대 3840×2160 → 1920×1080): 128×128 → 64×64(hw_emu 기준 실행 포함), 3840×2160 → 1920×1080을 OpenCV `INTER_LINEAR`와 비교, 화소 차이 ≤ 5 | P |
| VI-3 | 특징 추출 (에지 기울기) | 플랫폼 연산 `image_sobel`(`xf::cv::Sobel` 3×3, 8비트 포화, 가장자리 0, 공식 L1 예제 설정): 128×128(hw_emu 기준 실행 포함), 512×512, 1920×1080의 x·y 기울기를 OpenCV `Sobel(CV_8U)`과 비교. 공식 testbench와 같이 차이 0(바이트 일치)을 요구하므로 플랫폼 CPU 대조(exact)를 그대로 쓴다 | P |
| VI-4 | ISP 파이프라인 | `L3/examples/isppipeline`: 여러 함수가 이어진 L3 예제이고 호스트가 OpenCV C++ 필요 | X 예정 |

### 4.9 Quantitative Finance

| ID | 연산 | 시나리오 | 경로 |
|---|---|---|---|
| QF-1 | 유럽형 옵션 Monte Carlo 가격 산정 (MCEuropeanEngine) | `L2/tests/MCEuropeanEngine`: 공식 허용 장치는 U250. U50 빌드를 시도해 호스트의 골든 가격과 상대오차 0.02 이내인지 확인 | V 시도 |
| QF-2 | 금융 파생상품 가격 산정 엔진 | QF-1 결과로 대표. 2023.1 known issue(일부 MC 엔진 U50 hw 빌드 실패)는 근거로만 인용 | — |

### 4.10 BLAS

| ID | 연산 | 시나리오 | 경로 |
|---|---|---|---|
| BL-1 | 행렬-벡터 곱 (GEMV) | 공식 문서가 가리키는 `L2/benchmarks/streamingKernel/gemvStreamCh16`이 2024.2·2025.1 저장소에 없다(L3는 CPU·GPU 비교만, L1 `tests/hw/gemv`는 HLS 전용). 그래서 L1 테스트 top(`uut_top.cpp`)과 같은 구성(`gem2Stream`→`gemv`→`writeStream2Vec`, 4 lane)을 플랫폼 연산 `gemv_i32`로 만들고, 공식 L1 테스트와 같은 값 범위(−124~124)의 int32 행렬로 `y = αAx + βy`를 numpy와 **바이트 단위** 비교: 64×130(α=3, β=−2, hw_emu 기준 실행 포함), 1024×4096 | P |

## 5. 실행 운영 (빌드 자원)

- hw 링크 하나가 Vivado 합성 작업을 최대 8개 동시에 띄워 순간 메모리가 약 20 GB까지 오른다. 2026-11-04 16:44에 공식 테스트 빌드 4개와
  플랫폼 빌드 2개를 함께 시작했다가 메모리가 바닥나(94 GB 중 가용 1 GB, 스왑 2 GB 가득) 커널 OOM killer가 gqeJoin·dup_match HLS를 종료시켰다.
- 이후 모든 hw 빌드는 `tools/sched.py` 하나로 관리한다: 동시 5개 이하, 가용 메모리 30 GB 이상, 시작 간격 8분 이상. 공식 테스트 빌드에는
  `VPP_LDFLAGS="--vivado.synth.jobs 4 --vivado.impl.jobs 4"`를 환경으로 넘겨(Makefile의 `+=`에 덧붙음) 합성 동시 작업을 절반으로 줄였다.
  이 옵션은 결과물이 아니라 빌드 병렬도만 바꾼다.
- 카드 실행은 `tools/autorun.py`가 빌드가 끝난 순서대로 장치 잠금을 잡고 한 번에 하나씩 하며, 실행 하나에 30분 제한을 둔다.

## 6. 결과

`results/summary.md`에 연산별 결과(등급, 빌드 시간, 달성 클럭, PASS 근거)를 채운다.

## 7. 재현 방법

```bash
# 환경
source /home/hun/dev/workspace/2025.1/Vitis/settings64.sh
source /opt/xilinx/xrt/setup.sh

# V 경로: 공식 테스트 hw 빌드(병렬) → 카드 실행(직렬, 장치 잠금)
python3 verification/u50_vitis_libraries/tools/vqueue.py build --only db_gqe_join,graph_twohop --parallel 4
python3 verification/u50_vitis_libraries/tools/vqueue.py run   --only db_gqe_join,graph_twohop

# P 경로: 플랫폼 모듈
export VITIS_LIBRARIES=/home/hun/dev/workspace/vitis_libs_u50v
python3 verification/u50_vitis_libraries/tools/gen_inputs.py
isc new u50v_adler32 --operation adler32
isc run u50v_adler32 --input verification/u50_vitis_libraries/inputs/bytes_256k.bin --target hw
isc records --module u50v_adler32
```

작업 목록은 `jobs.json`, 빌드·실행 기록은 `results/*.jsonl`, 로그는 `logs/`(용량 때문에 저장소에는 요약만 올림)에 있다.
