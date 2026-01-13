"""Constants for the CozyLife (New) integration."""

DOMAIN = "cozylife_local"

# DPID mapping (example, these should be confirmed from pid_list)
# These are taken from the old integration's const.py and will need to be dynamically
# mapped based on the device's PID and the fetched pid_list.

# Standard DPID mapping (based on original CozyLife protocol)
SWITCH = '1'      # Power on/off
WORK_MODE = '2'   # Work mode (0=white/temp, 1=color/scene)
TEMP = '3'        # Color temperature
BRIGHT = '4'      # Brightness
HUE = '5'         # Hue (color)
SAT = '6'         # Saturation (color)

# Supported device types (derived from old integration, actual support depends on pid_list)
SWITCH_TYPE_CODE = '00'
LIGHT_TYPE_CODE = '01'
RGB_LIGHT_TYPE_CODE = '02'  # RGB lights (some devices use type 02 for RGB/color lights)
SUPPORT_DEVICE_CATEGORY = [SWITCH_TYPE_CODE, LIGHT_TYPE_CODE, RGB_LIGHT_TYPE_CODE]

PLATFORMS = ["light", "switch"] # Supported Home Assistant platforms
