#!/bin/sh
TEMP_BASE=35000
TEMP_SCALE=450
FREQ_BASE=600000
FREQ_SCALE=6000
prev_idle=0
prev_total=0
while true; do
    read -r cpu user nice system idle iowait irq softirq rest < /proc/stat
    total=$((user + nice + system + idle + iowait + irq + softirq))
    diff_total=$((total - prev_total))
    diff_idle=$((idle - prev_idle))
    [ "$diff_total" -gt 0 ] && \
        load_pct=$(( (diff_total - diff_idle) * 100 / diff_total )) || \
        load_pct=0
    [ "$load_pct" -lt 0   ] && load_pct=0
    [ "$load_pct" -gt 100 ] && load_pct=100
    echo $(( TEMP_BASE + load_pct * TEMP_SCALE )) > /tmp/cpu_temp
    echo $(( FREQ_BASE + load_pct * FREQ_SCALE )) > /tmp/cpu_freq
    prev_total=$total
    prev_idle=$idle
    sleep 2
done
