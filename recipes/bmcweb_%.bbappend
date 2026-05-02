# bmcweb_%.bbappend
# Extends the upstream bmcweb recipe for the Romulus machine target.
# Adds a custom Redfish sensor endpoint that exposes the SoC CPU temperature
# by reading /sys/class/thermal/thermal_zone0/temp.

# Prepend this recipe's directory to the file search path so BitBake
# finds our patch file in the bmcweb/ subdirectory alongside this file.
FILESEXTRAPATHS:prepend := "${THISDIR}/${PN}:"

# Add our patch to the list of sources BitBake will apply during do_patch.
SRC_URI += "file://0001-redfish-add-cpu_temp-and-cpu_freq-sensor-endpoints.patch"
