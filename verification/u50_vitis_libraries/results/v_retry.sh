#!/bin/bash
# 현재 V 스케줄러가 끝나면, 메모리 부족으로 실패한 V 빌드 4건을 동시 3개 제한으로 다시 빌드한다
cd /home/hun/dev/workspace/ISC_Management_Platform/verification/u50_vitis_libraries
while pgrep -f "tools/sched.py --vendor sec_adler32" >/dev/null; do sleep 60; done
echo "$(date '+%m-%d %H:%M:%S') previous scheduler done; retry starts"
exec python3 -u tools/sched.py --vendor sec_crc32,comp_gzip_hbm,graph_renumber,graph_merge --max-total 3 --min-free-gb 40 --gap-min 10
