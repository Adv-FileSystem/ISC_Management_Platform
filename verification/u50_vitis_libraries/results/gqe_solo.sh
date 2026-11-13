#!/bin/bash
# GQE 3건은 Makefile 자체 설정(합성·구현 16병렬)으로 빌드해야 해서, 다른 빌드가 하나도 없을 때 하나씩 단독으로 빌드·실행한다
cd /home/hun/dev/workspace/ISC_Management_Platform/verification/u50_vitis_libraries
busy() { pgrep -f "tools/sched.py|tools/pchain.py|v_retry.sh|make all TARGET=hw|isc run .*--target hw" >/dev/null; }
for job in db_gqe_join db_gqe_bloom db_gqe_part; do
  while busy; do sleep 120; done
  echo "$(date '+%m-%d %H:%M:%S') solo build $job (free $(awk '/MemAvailable/{printf "%d", $2/1048576}' /proc/meminfo) GB)"
  python3 -u tools/vqueue.py build --only $job 2>&1 | tail -3
  echo "$(date '+%m-%d %H:%M:%S') run $job"
  python3 -u tools/vqueue.py run --only $job 2>&1 | tail -5
done
echo "$(date '+%m-%d %H:%M:%S') gqe solo done"
