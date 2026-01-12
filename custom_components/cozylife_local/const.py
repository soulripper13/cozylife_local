"""Constants for the CozyLife (New) integration."""

DOMAIN = "cozylife_local"

# DPID mapping (example, these should be confirmed from pid_list)
# These are taken from the old integration's const.py and will need to be dynamically
# mapped based on the device's PID and the fetched pid_list.

# Standard DPID mapping (newer devices)
SWITCH = '1'
WORK_MODE = '2'
TEMP = '3'      # Color temperature (on standard devices)
BRIGHT = '4'    # Brightness (on standard devices)
HUE = '5'       # Hue (on RGB devices with standard mapping)
SAT = '6'       # Saturation (on RGB devices with standard mapping)

# Alternative DPID mappings for older/different device models
# Some LED strips (like pid: d50v0i) use different DPID numbers
BRIGHT_ALT = '3'   # Alternative brightness DPID (used by some LED strips)
COLOR_MODE = '5'   # Some devices use DPID 5 for color/mode selection

# Supported device types (derived from old integration, actual support depends on pid_list)
SWITCH_TYPE_CODE = '00'
LIGHT_TYPE_CODE = '01'
RGB_LIGHT_TYPE_CODE = '02'  # RGB lights (some devices use type 02 for RGB/color lights)
SUPPORT_DEVICE_CATEGORY = [SWITCH_TYPE_CODE, LIGHT_TYPE_CODE, RGB_LIGHT_TYPE_CODE]

PLATFORMS = ["light", "switch"] # Supported Home Assistant platforms
