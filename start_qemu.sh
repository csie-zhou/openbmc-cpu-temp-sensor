#!/usr/bin/env bash
# start_qemu.sh — Boot OpenBMC Romulus in QEMU
# Usage: ./start_qemu.sh [path-to-image]

IMAGE="${1:-$(ls ~/openbmc/build/tmp/deploy/images/romulus/obmc-phosphor-image-romulus.static.mtd 2>/dev/null | head -1)}"

if [ -z "$IMAGE" ]; then
  echo "ERROR: No .mtd image found. Run 'bitbake obmc-phosphor-image' first."
  exit 1
fi

echo "Booting: $IMAGE"
echo "bmcweb will be available at: https://localhost:2443"
echo "Login: root / 0penBmc"
echo "Press Ctrl-A then X to quit QEMU."
echo ""

qemu-system-arm \
  -M romulus-bmc \
  -nographic \
  -drive file="${IMAGE}",format=raw,if=mtd \
  -net nic \
  -net user,hostfwd=tcp:127.0.0.1:2443-:443,hostfwd=tcp:127.0.0.1:2022-:22
