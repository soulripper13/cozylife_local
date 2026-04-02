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

# Sensor DPIDs (temperature/humidity sensor, PID=Z4tRml)
SENSOR_TEMPERATURE = '8'   # raw value ÷ 10 = °C
SENSOR_HUMIDITY = '4'      # raw value = %
SENSOR_BATTERY = '9'       # raw value ÷ 10 = %

# Supported device types (derived from old integration, actual support depends on pid_list)
SWITCH_TYPE_CODE = '00'
LIGHT_TYPE_CODE = '01'
RGB_LIGHT_TYPE_CODE = '02'  # RGB lights use type code 02
SUPPORT_DEVICE_CATEGORY = [SWITCH_TYPE_CODE, LIGHT_TYPE_CODE, RGB_LIGHT_TYPE_CODE]

PLATFORMS = ["light", "switch", "sensor"]

DEFAULT_MIN_KELVIN = 2000
DEFAULT_MAX_KELVIN = 6500
