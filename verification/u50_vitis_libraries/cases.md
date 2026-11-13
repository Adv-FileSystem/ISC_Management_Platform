# 사용자 시나리오 관련 실사례 조사

조사 2026-11-04 · 대상: `README.md` 3절의 사용자 시나리오 US-1 ~ US-8

- 각 사례는 원문(공식 블로그, 논문, 공급사 문서, 당시 보도)을 직접 열어 수치를 확인했다. 원문을 열지 못한 것은 "원문 미확인"으로 표시하고 근거로 쓰지 않는다.
- 성능 수치는 대부분 **공급사·저자 자신의 측정**이다. 측정 조건(CPU, 데이터, 연도)이 서로 달라 사례끼리 비교하면 안 된다.
- "FPGA"는 사례가 FPGA를 썼다고 원문에 적혀 있을 때만 쓴다.

## 요약

구분: **실제 사용**(운영 중인 서비스·제품에서 쓰임) · **상용 제품**(제품은 있으나 고객 도입 사례 미확인) · **공급사 시험·벤치마크** · **연구**(논문·연구소 시제품)

| 시나리오 | 대표 사례 | 구분 | 누가 | 장치에서 처리한 일 | 원문 수치 |
|---|---|---|---|---|---|
| US-1 로그·백업 압축 | Alibaba PolarStore | 실제 사용 | Alibaba Cloud | 저장장치(PolarCSD) 안에서 DB 데이터 압축 | 100 PB 이상, 압축률 3.55, 저장 비용 약 60% 절감 |
| | Microsoft Project Zipline | 상용 설계 공개 | Microsoft Azure | 클라우드 저장 데이터용 압축 알고리즘·하드웨어 설계 공개 | Zlib-L4 64KB 대비 압축률 최대 2배 |
| | Samsung SmartSSD + Eideticom NoLoad | 상용 제품 | Samsung·Xilinx·Eideticom | FPGA 내장 SSD에서 투명 압축 | 최대 10배 압축, 서버 CPU 70% 절감(공급사 주장) |
| US-2 무결성 확인 | LANL Accelerated Box of Flash | 연구(시연) | 미 로스앨러모스 연구소 + Eideticom 등 | 저장 장치에서 체크섬·이레이저 코딩·압축 | 현 시스템 대비 10~30배(연구소 발표) |
| US-3 판매 데이터 분석 | AWS Redshift AQUA | 실제 사용 | Amazon | FPGA 기반 가속으로 스캔·필터·집계를 데이터 가까이에서 처리 | 해당 질의 최대 10배, 코드 수정 없음 |
| | Alibaba PolarDB + 연산형 SSD | 실제 사용 | Alibaba·ScaleFlux | 표 스캔을 저장장치로 내려 보냄(pushdown) | 연산형 저장장치를 쓴 클라우드 DB의 첫 실서비스 배포 |
| | IBM Netezza | 실제 사용 | IBM | 디스크에서 읽어 오는 즉시 FPGA가 불필요한 행·열 제거 | 수치 없음(구조 설명) |
| US-4 추천·관계 분석 | TigerGraph + Xilinx Alveo U50 | 공급사 시험 | TigerGraph | 코사인 유사도, Louvain을 FPGA로 | 150만 명 환자 유사도 430 ms → 10 ms, Louvain 20배 |
| US-5 고객 중복 정리 | Vitis Duplicate Record Match | 공급사 벤치마크 | AMD(Xilinx) | 중복 레코드 매칭을 Alveo U50에서 | 1 GB CSV, Python Dedupe 대비 "5.1배"(표의 시간으로 계산하면 4.26배) |
| US-6 이미지 전처리 | DLBooster | 연구 | Tsinghua·Microsoft Research | 학습·추론용 이미지 디코딩을 FPGA로 | 처리량 1.35~2.4배, CPU 코어 1/10 |
| US-7 수치 계산 | OPM Flow FPGA 선형 해법 | 연구 | TU Delft 등 | 저류층 시뮬레이터의 BiCGStab+ILU0 해법을 HBM FPGA로 | 실제 유전 모델(Norne), 커널 기준 CPU와 비슷·GPU보다 3배 느림 |
| US-8 금융 가격 계산 | J.P. Morgan + Maxeler | 실제 사용 | J.P. Morgan | 신용 파생상품 전체 장부의 위험 계산을 FPGA로 | 약 8시간 → 238초(FPGA 시간 12초) |

## US-1 서비스 로그·백업 파일 압축 저장

**Alibaba PolarStore (USENIX FAST 2026).** Alibaba Cloud의 클라우드 DB PolarDB 저장 계층이다. 저장장치 PolarCSD 안의 하드웨어 압축과 가벼운 소프트웨어 압축을 함께 쓴다.
원문: "currently deployed on thousands of storage servers within PolarDB, managing over 100 PB of data", 압축률 3.55, 저장 비용 약 60% 절감.
→ 우리 시나리오의 "저장 전에 압축해 용량을 줄인다"를 실서비스 규모로 보여 준다. 압축을 저장장치가 맡아 서버 CPU를 쓰지 않는다는 점이 같다.

**Microsoft Project Zipline (Azure 블로그, 2019-03-14, Kushagra Vaid).** Azure 클라우드 저장 데이터를 겨냥한 압축 알고리즘과 하드웨어 구현을 OCP에 공개했다.
원문: "up to 2X high compression ratios versus the commonly used Zlib-L4 64KB model". 처리량 수치와 Azure 실배포 여부는 블로그에 명시되어 있지 않다.

**Samsung SmartSSD + Eideticom NoLoad (Blocks & Files, 2020-11-10).** 4 TB U.2 SSD에 Xilinx Kintex UltraScale+ KU15P FPGA를 넣은 연산형 저장장치(CSD)다.
Eideticom 주장: NoLoad가 "up to 10x compression at line-rate while using 70 per cent less server CPU resource", 응용 수정 없음.
같은 기사에서 Xilinx는 Bigstream과 결합해 Apache Spark 분석이 최대 10배 빨라진다고 주장한다.
→ 우리 U50 환경이 흉내 내는 구조(저장장치 옆 FPGA)와 가장 가깝다. 다만 SmartSSD는 FPGA가 플래시에 직접 붙고, 우리 U50은 PCIe 카드다.

## US-2 백업·전송 파일 무결성 확인

**LANL Accelerated Box of Flash, ABOF (EurekAlert, 2022-03-23).** 미국 로스앨러모스 국립연구소가 NVIDIA, Eideticom, Aeon Computing, SK hynix와 만든 네트워크 저장장치다.
원문: 현재 구현은 "compression, erasure coding and checksums" 세 기능을 전용 가속기로 처리하고, ZFS 파일시스템의 성능 핵심 기능을 오프로드한다. 연구소 발표 수치는 "10 to 30 times the performance of current systems"이고, 응용이나 저장 소프트웨어를 크게 바꾸지 않아도 된다.
→ 체크섬을 저장장치 쪽에서 계산하는 실제 시스템 사례다. 원문은 체크섬 종류(CRC32 등)를 밝히지 않는다.

## US-3 판매 데이터 분석 질의

**AWS Redshift AQUA (AWS News Blog, 2021-04-14, Jeff Barr).** Redshift 데이터 웨어하우스의 질의 가속 계층이다.
원문: AQUA는 "pushes the computation needed to handle reduction and aggregation queries closer to the data"이고, AWS Nitro System과 "custom FPGA-based acceleration"을 쓴다. 큰 스캔, 집계, `LIKE`·`SIMILAR_TO` 필터 질의가 최대 10배 빨라지고, "without any code changes"로 쓸 수 있다.
→ "조인·필터를 데이터 가까이에서 하고 결과만 받는다"는 US-3의 목적과 같다. 사용자가 코드를 바꾸지 않는다는 점은 우리 시스템의 개발 용이성 목표와 같다.

**Alibaba PolarDB + 연산형 SSD (USENIX FAST 2020, Alibaba·ScaleFlux).** 클라우드 DB가 "table scan" 같은 데이터 집약 작업을 저장 노드로 내려 보내고, 각 저장장치가 I/O 경로에서 스캔을 처리한다.
원문: Alibaba Cloud 배포 사례이며 "the first real-world deployment of cloud-native databases with computational storage drives"라고 적었다.

**IBM Netezza (i-programmer, 2011-06-23).** IBM Netezza High Capacity Appliance는 FPGA로 "filter out extraneous data as early in the data stream as possible"한다. 디스크에서 읽는 즉시 거르므로 CPU·메모리·네트워크가 불필요한 데이터를 처리하지 않는다. 제거 비율 수치는 이 기사에 없다.
→ 저장 쪽 FPGA 필터의 고전적인 상용 사례다. 우리 플랫폼 Hook형 `filter`가 같은 역할을 한다.

## US-4 추천·관계 분석

**TigerGraph + Xilinx Alveo U50 (The Next Platform, 2021-10-28, Timothy Prickett Morgan).** 그래프 DB TigerGraph가 Xilinx와 코사인 유사도와 Louvain 모듈성 최적화를 FPGA 로직으로 옮겼다.
시험 서버: AMD EPYC 7713 2개 + **Alveo U50 5장**. 데이터: Synthea 합성 환자 기록.
원문 수치: 비슷한 환자 상위 1,000명 찾기가 150만 명 기준 CPU 430 ms → FPGA 10 ms, 1,500만 명 기준 1.6 s → 33 ms(FPGA 5장, 48배). Louvain은 응답 시간 20배, 그룹 정확도 18% 향상.
→ 우리와 같은 카드(U50)와 같은 라이브러리 계열(Vitis Graph의 cosine similarity, Louvain)이다. US-4의 "비슷한 사용자 찾기"는 이 사례와 같은 계산이다.

## US-5 고객 데이터 중복 정리

**AMD Vitis Duplicate Record Match (공식 벤치마크 문서 2021.2).** 색인·조건 비교·쌍 생성·점수·군집화 모듈로 중복 레코드를 찾는다. Alveo U50 270 MHz에서 검증되었다.
원문: 약 1,000만 줄(약 1 GB) CSV, FPGA 8,215.56 s vs Python Dedupe 35,030.751 s(Xeon E5-2690 v4), 처리량 124.64 MB/s.
문서는 "Accelaration Ratio: 5.1X"라고 적었지만, 같은 표의 시간으로 나누면 4.26배다.
→ 실서비스 도입 사례는 찾지 못했다. 공급사 벤치마크만 근거로 쓸 수 있다.

## US-6 AI 학습용 이미지 전처리

**DLBooster (ICPP 2019, Tsinghua University·Microsoft Research).** 학습·추론에 앞선 이미지 전처리(디코딩)를 FPGA로 옮긴 온라인 전처리 계층이다.
원문 문제 제기: GPU 클러스터에서 AlexNet을 학습할 때, Caffe가 LMDB로 데이터를 읽으면 합성 데이터 대비 학습 처리량이 약 30% 떨어진다. 실행 중 CPU로 데이터를 해석하면 학습 성능이 약 25%에 그치거나, 그 차이를 메우려 GPU당 CPU 코어를 12개 넘게 써야 한다.
원문 결과: 기존 방식 대비 이미지 처리 처리량 1.35~2.4배, CPU 코어는 1/10만 사용, 온라인 추론 지연 1/3 감소. 모든 전처리를 FPGA로 옮기는 것은 비효율적이라 디코딩 위주로 골라 옮겼다고 적었다.
→ US-6의 사용자(AI 모델 개발자)와 목적(학습 서버 CPU 병목 해소)이 같다. 우리 플랫폼에는 디코딩→크기 조정 연결 실행이 아직 없다는 제약과도 맞닿아 있다.

## US-7 공학 시뮬레이션·수치 계산

**OPM Flow 저류층 시뮬레이터의 FPGA 선형 해법 (arXiv 2101.01745, ACM TRETS 2022, TU Delft 등).** 오픈소스 저류층 시뮬레이터 OPM Flow에 ILU0 전처리 BiCGStab 해법을 HBM 탑재 FPGA 커널로 만들어 통합했다.
원문: 실제 유전 모델 Norne(10^5개 이상 격자, 격자당 미지수 3개)으로 평가했다. 커널만 비교하면 FPGA가 CPU와 비슷하고 GPU보다 3배 느렸다(arXiv 판 평가).
→ 수치 해법은 FPGA가 항상 이기지 않는다는 근거로도 쓸 수 있다.
→ "큰 연립방정식을 반복해서 푼다"는 US-7과 같은 계산 구조(Krylov 반복 해법 + 전처리)다. 우리가 검증하는 CG + Jacobi와 같은 계열이다.

## US-8 금융 상품 가격 계산

**J.P. Morgan + Maxeler (insideHPC 기사, Linux.com 재게재, 2011-07-12).** J.P. Morgan이 전체 신용 장부의 위험 계산을 Maxeler FPGA 시스템으로 옮겼다.
원문: 전체 위험 계산이 약 8시간(현재가치 계산은 약 1시간) 걸리던 것이 약 238초가 되었고, 그중 FPGA 시간은 12초다. 근거는 Computerworld UK 보도와 J.P. Morgan 담당자의 Stanford 강연이다.
→ "매일 밤 다시 계산하는 일을 줄인다"는 US-8의 목적과 같다. 다만 이 사례는 신용 파생상품 위험 모델이고, 우리가 검증하는 Monte Carlo 유럽형 옵션 엔진과는 계산이 다르다.

## 사례에서 공통으로 보이는 것 (시나리오에 반영할 점)

1. **결과만 돌려받는 구조가 이득의 핵심이다.** AQUA, Netezza, PolarDB는 모두 데이터를 저장 쪽에서 줄인 뒤 결과만 보낸다. 우리 측정에서 압축·체크섬처럼 데이터를 그대로 왕복시키는 연산이 전송 바운드로 나올 가능성이 큰 것과 같은 이유다.
2. **사용자 코드를 바꾸지 않는 것을 강조한다.** AQUA("without any code changes"), Eideticom(응용 수정 없음), ABOF(응용 변경 없음)가 모두 내세운다. 우리 시스템의 "연산을 고르거나 함수 하나만 작성"과 같은 방향이다.
3. **계산이 무거운 단계만 골라 옮긴다.** DLBooster는 디코딩 위주로 골랐다. 우리 플랫폼의 가속 판정(전송 한계 배율)이 이 판단을 도와준다.
4. **수치는 공급사 측정이 대부분이다.** 연차보고서에는 "사례에서 보고된 값"으로 인용하고, 우리 결과는 우리 측정으로만 쓴다.

## 원문을 확인하지 못해 쓰지 않은 것

- Swarm64(PostgreSQL을 Alveo로 가속, 2019): 보도자료 원문(BusinessWire)이 403으로 열리지 않았고, 사본 링크는 엉뚱한 사이트로 넘어갔다.
- SNIA 연산형 저장장치 활용 사례 분류: snia.org가 403으로 열리지 않았다.
- CTAccel 이미지 처리(CIP): 원문이 Intel FPGA 제품만 설명하고, Alveo 지원은 확인되지 않았다.
- Netezza의 "불필요한 데이터 90~95% 제거" 수치: 슬라이드 자료에만 있고 IBM 원문(백서)은 403이었다.

## 참고문헌

[1] Q. Hu, X. Yang, F. Li, J. Li, Y. Lin, Y. Zhou, Y. Zhu, J. Zhang, R. Xie, L. Zhou, B. Wu, W. Zhou, "PolarStore: High-Performance Data Compression for Large-Scale Cloud-Native Databases," *24th USENIX Conference on File and Storage Technologies (FAST '26)*, Santa Clara, CA, 2026.
[2] K. Vaid, "Hardware innovation for data growth challenges at cloud scale (Project Zipline)," Microsoft Azure Blog, Mar. 14, 2019.
[3] Blocks & Files, "Xilinx takes Samsung computational storage drive to market" (SmartSSD), Nov. 10, 2020. (기사 제목·저자 표기는 원문에서 다시 확인 필요)
[4] Los Alamos National Laboratory, "Accelerated Box of Flash: Powerful computational storage for big data projects," EurekAlert!, Mar. 23, 2022.
[5] J. Barr, "New – AQUA (Advanced Query Accelerator) for Amazon Redshift," AWS News Blog, Apr. 14, 2021.
[6] W. Cao, Y. Liu, Z. Cheng, N. Zheng, W. Li, W. Wu, L. Ouyang, P. Wang, Y. Wang, R. Kuan, Z. Liu, F. Zhu, T. Zhang, "POLARDB Meets Computational Storage: Efficiently Support Analytical Workloads in Cloud-Native Relational Database," *18th USENIX Conference on File and Storage Technologies (FAST '20)*, pp. 29–41, 2020.
[7] "IBM Netezza – Data analysis in hardware," I Programmer, Jun. 23, 2011.
[8] T. P. Morgan, "The Accelerated Path to Petabyte-Scale Graph Databases," The Next Platform, Oct. 28, 2021.
[9] AMD (Xilinx), "Duplicate Record Match," Vitis Libraries Data Analytics Benchmark, release 2021.2.
[10] Y. Cheng, D. Li, Z. Guo, B. Jiang, J. Lin, X. Fan, J. Geng, X. Yu, W. Bai, L. Qu, R. Shu, P. Cheng, Y. Xiong, J. Wu, "DLBooster: Boosting End-to-End Deep Learning Workflows with Offloading Data Preprocessing Pipelines," *Proc. 48th International Conference on Parallel Processing (ICPP 2019)*, ACM, 2019. DOI: 10.1145/3337821.3337892
[11] T. Hogervorst, R. Nane, G. Marchiori, T. D. Qiu, M. Blatt, A. B. Rustad, "Hardware Acceleration of High-Performance Computational Flow Dynamics Using High-Bandwidth Memory-Enabled Field-Programmable Gate Arrays," *ACM Transactions on Reconfigurable Technology and Systems*, vol. 15, no. 2, art. 20, pp. 1–35, 2022. DOI: 10.1145/3476229 (preprint arXiv:2101.01745)
[12] S. Weston, J.-T. Marin, J. Spooner, O. Pell, O. Mencer, "Accelerating the computation of portfolios of tranched credit derivatives," *2010 IEEE Workshop on High Performance Computational Finance (WHPCF)*, pp. 1–8, 2010. DOI: 10.1109/WHPCF.2010.5671822
[13] S. Weston, J. Spooner, S. Racanière, O. Mencer, "Rapid computation of value and risk for derivatives portfolios," *Concurrency and Computation: Practice and Experience*, vol. 24, no. 8, pp. 880–894, 2012. DOI: 10.1002/cpe.1778
[14] insideHPC, "JP Morgan Uses FPGAs Accelerators for Risk Analysis in Near Real-time," Jul. 12, 2011 (Linux.com 재게재).

학술 논문은 [1] [6] [10] [11] [12] [13]이고, 나머지는 공식 블로그·문서·보도다.

## 출처 URL

- Alibaba PolarStore, USENIX FAST 2026: https://www.usenix.org/conference/fast26/presentation/hu
- Microsoft Project Zipline, Azure 블로그: https://azure.microsoft.com/en-us/blog/hardware-innovation-for-data-growth-challenges-at-cloud-scale/
- Samsung SmartSSD, Blocks & Files: https://blocksandfiles.com/2020/11/10/samsung-csd-ssd-xilinx/
- LANL ABOF, EurekAlert: https://www.eurekalert.org/news-releases/947520
- AWS Redshift AQUA, AWS News Blog: https://aws.amazon.com/blogs/aws/new-aqua-advanced-query-accelerator-for-amazon-redshift/
- Alibaba PolarDB + 연산형 SSD, USENIX FAST 2020: https://www.usenix.org/conference/fast20/presentation/cao-wei
- IBM Netezza, i-programmer: https://i-programmer.info/news/84-database/2645-netezza-data-analysis-in-hardware.html
- TigerGraph + Alveo U50, The Next Platform: https://www.nextplatform.com/2021/10/28/the-accelerated-path-to-petabyte-scale-graph-databases/
- Vitis Duplicate Record Match 벤치마크: https://xilinx.github.io/Vitis_Libraries/data_analytics/2021.2/benchmark/dup_match.html
- DLBooster, ICPP 2019 (PDF): https://depctg.github.io/files/dlbooster-icpp2019.pdf
- OPM Flow FPGA 선형 해법, arXiv: https://arxiv.org/abs/2101.01745
- J.P. Morgan + Maxeler, Linux.com(insideHPC 재게재): https://www.linux.com/?p=492058
- J.P. Morgan 학술 논문 [12], IEEE: https://doi.org/10.1109/WHPCF.2010.5671822
- J.P. Morgan 학술 논문 [13]: https://doi.org/10.1002/cpe.1778
- OPM Flow 논문 [11], ACM: https://doi.org/10.1145/3476229
- DLBooster [10], ACM: https://doi.org/10.1145/3337821.3337892
