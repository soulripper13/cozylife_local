"""Constants for the CozyLife (New) integration."""

DOMAIN = "cozylife_local"

# DPID mapping (example, these should be confirmed from pid_list)
# These are taken from the old integration's const.py and will need to be dynamically
# mapped based on the device's PID and the fetched pid_list.
SWITCH = '1'
WORK_MODE = '2'
TEMP = '3'
BRIGHT = '4'
HUE = '5'
SAT = '6'

# Supported device types (derived from old integration, actual support depends on pid_list)
SWITCH_TYPE_CODE = '00'
LIGHT_TYPE_CODE = '01'
SUPPORT_DEVICE_CATEGORY = [SWITCH_TYPE_CODE, LIGHT_TYPE_CODE]

PLATFORMS = ["light", "switch"] # Supported Home Assistant platforms
