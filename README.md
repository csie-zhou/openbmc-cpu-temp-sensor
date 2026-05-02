# OpenBMC Custom Redfish Sensor Dashboard — QEMU Romulus

Extends [OpenBMC](https://github.com/openbmc/openbmc)'s **bmcweb** HTTP
service with two custom Redfish sensor endpoints, backed by a `mock_thermal.sh`
simulation and visualised by a live terminal dashboard (`redfish_monitor.py`).
Runs entirely on QEMU — no hardware required.

## What this project demonstrates

- **BMC firmware development** — adding Redfish sensor resources to bmcweb (C++)
  using the modern handler pattern (`handleCpuTempGet`, `requestRoutesCpuTemp`)
- **Redfish protocol** — two DSP0268-compliant Sensor resources across the
  `temperature/` and `frequency/` subtrees
- **Yocto / BitBake** — patching upstream recipes via `.bbappend` without forking
- **Hardware abstraction** — mock sensors with the same sysfs integer format;
  one-line path swap to deploy on real AST2500 hardware
- **Redfish client development** — stdlib Python dashboard consuming the live API

## New endpoints

```
GET /redfish/v1/Chassis/chassis/Sensors/temperature/cpu_temp
GET /redfish/v1/Chassis/chassis/Sensors/frequency/cpu_freq
```

Both return Redfish DSP0268-compliant JSON. Values are dynamic — they rise
under CPU load and return to idle levels, driven by `mock_thermal.sh`.

## Live dashboard

```bash
python3 scripts/redfish_monitor.py
```

Polls both endpoints every 2 seconds. Shows real-time bar graphs, sparkline
history, min/avg/max session stats, and health indicators (OK / WARNING / CRITICAL).
No third-party dependencies — stdlib only.

## Architecture

```
[ redfish_monitor.py ]  ← Redfish client (runs on host)
         | HTTPS :2443
         v
[ bmcweb C++ ]  ← handleCpuTempGet + handleCpuFreqGet added to sensors.hpp
         |         registered in redfish.cpp
         v
[ /tmp/cpu_temp ]  ← millidegrees Celsius
[ /tmp/cpu_freq ]  ← kHz
         ^
[ mock_thermal.sh ]  ← reads /proc/stat, writes both files every 2s

All running inside QEMU emulating ASPEED AST2500 Romulus BMC
Built with Yocto/BitBake — obmc-phosphor-image, meta-ibm/meta-romulus

Note: QEMU omits ADC hardware emulation so /sys/class/thermal/ is absent.
On real hardware, swap /tmp/cpu_temp for /sys/class/thermal/thermal_zone0/temp
and /tmp/cpu_freq for /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq.
The millidegree/kHz integer format is identical in both cases.
```

## How to build

### 1. Clone OpenBMC
```bash
git clone https://github.com/openbmc/openbmc.git && cd openbmc
```

### 2. Apply the patch
```bash
mkdir -p meta-ibm/meta-romulus/recipes-phosphor/bmcweb/bmcweb/
cp patches/0001-*.patch  meta-ibm/meta-romulus/recipes-phosphor/bmcweb/bmcweb/
cp recipes/bmcweb_%.bbappend  meta-ibm/meta-romulus/recipes-phosphor/bmcweb/
```

### 3. Build
```bash
. setup romulus build && bitbake obmc-phosphor-image
# ~2–6 hours first build; incremental rebuilds ~5 min
```

### 4. Run QEMU
```bash
./start_qemu.sh
```

### 5. Start the mock sensors (inside QEMU)
```bash
# /tmp is tmpfs — writable at runtime despite read-only rootfs
cat scripts/mock_thermal.sh > /tmp/mock_thermal.sh
chmod +x /tmp/mock_thermal.sh && /tmp/mock_thermal.sh &
```

### 6. Launch the dashboard (on host)
```bash
python3 scripts/redfish_monitor.py
```

## Files

| File | Purpose |
|---|---|
| `start_qemu.sh` | Boot the QEMU Romulus image |
| `scripts/mock_thermal.sh` | Writes `/tmp/cpu_temp` + `/tmp/cpu_freq` from `/proc/stat` load |
| `scripts/redfish_monitor.py` | Live terminal dashboard — polls both Redfish endpoints |
| `patches/0001-*.patch` | Adds `handleCpuTempGet` + `handleCpuFreqGet` to bmcweb |
| `recipes/bmcweb_%.bbappend` | Yocto recipe extension applying the patch |
| `demo/curl_output_idle.json` | Endpoint response at idle |
| `demo/curl_output_underload.json` | Endpoint response under CPU load |

## Environment
- OpenBMC commit: 612c7881b6d9908d9deebdb3c0e06a8197f255f4
- QEMU 6.x+ with `romulus-bmc` machine support
- Build host: Ubuntu 22.04 / 24.04
- Dashboard: Python 3.6+, stdlib only
