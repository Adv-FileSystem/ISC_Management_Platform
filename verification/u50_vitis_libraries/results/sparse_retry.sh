#!/bin/bash
# Sparse 유사도 V 빌드는 11-05 병렬 빌드 중 합성 작업이 메모리 부족으로 죽고 멈춰서 종료했다(11-06 10:13, 사용자 승인).
# GQE 단독 빌드(gqe_solo.sh, pid 2543291)가 끝난 뒤 단독으로 다시 빌드하고 카드에서 실행한다.
cd /home/hun/dev/workspace/ISC_Management_Platform/verification/u50_vitis_libraries
while kill -0 2543291 2>/dev/null; do sleep 60; done
echo "$(date '+%m-%d %H:%M:%S') sparse solo build (free $(awk '/MemAvailable/{printf "%d", $2/1048576}' /proc/meminfo) GB)"
python3 -u tools/sched.py --vendor graph_sparse_sim --max-total 1 --min-free-gb 40 --gap-min 0
echo "$(date '+%m-%d %H:%M:%S') run graph_sparse_sim"
python3 -u tools/vqueue.py run --only graph_sparse_sim 2>&1 | tail -5
echo "$(date '+%m-%d %H:%M:%S') sparse solo done"
