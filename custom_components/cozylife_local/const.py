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
SENSOR_TYPE_CODE = '03'

# Known sensor PIDs — used as the primary discriminator to identify sensor devices
KNOWN_SENSOR_PIDS = {'Z4tRml'}

# Electrician / smart plug DPIDs
PLUG_ENERGY = '26'          # add_ele, scale 3, kWh
PLUG_CURRENT = '27'         # cur_current, mA
PLUG_POWER = '28'           # cur_power, scale 1, W
PLUG_VOLTAGE = '29'         # cur_voltage, scale 1, V
PLUG_FAULT = '30'           # fault enum

# Supported device types (derived from old integration, actual support depends on pid_list)
SWITCH_TYPE_CODE = '00'
LIGHT_TYPE_CODE = '01'
RGB_LIGHT_TYPE_CODE = '02'  # Some RGB lights report type code 02
SUPPORT_DEVICE_CATEGORY = [SWITCH_TYPE_CODE, LIGHT_TYPE_CODE, RGB_LIGHT_TYPE_CODE]

PLATFORMS = ["light", "switch", "sensor", "binary_sensor"]

DEFAULT_MIN_KELVIN = 2000
DEFAULT_MAX_KELVIN = 6500

SENSOR_REPORT_INTERVAL_DPID = '14'
MIN_SENSOR_REPORT_INTERVAL = 600  # seconds
STANDARD_SENSOR_REPORT_INTERVAL = 1800  # seconds
DEFAULT_SENSOR_REPORT_INTERVAL = STANDARD_SENSOR_REPORT_INTERVAL
DEFAULT_SENSOR_POLL_INTERVAL = 5  # seconds

SENSOR_TEMP_SENSITIVITY_DPID = '25'   # ÷10 = °C, range 5-30
SENSOR_HUMIDITY_SENSITIVITY_DPID = '24'  # raw %, range 5-30
