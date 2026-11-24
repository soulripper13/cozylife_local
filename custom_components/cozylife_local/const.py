"""Constants for the CozyLife (New) integration."""

DOMAIN = "cozylife_local"
DISCOVERY_CONTROLLER_ENTRY_TYPE = "discovery_controller"
# Device ID prefix of a manually added sleeping sensor that has not yet
# reported its real device ID.
SLEEPING_SENSOR_ID_PREFIX = "sleeping_sensor_"

# Local DPID names used by currently exposed, catalog-backed entities.
SWITCH = '1'
WORK_MODE = '2'
TEMP = '3'
BRIGHT = '4'
HUE = '5'
SAT = '6'
LIGHT_COUNTDOWN = '13'

# Sensor DPIDs (temperature/humidity sensor, PID=Z4tRml)
SENSOR_TEMPERATURE = '8'   # raw value ÷ 10 = °C
SENSOR_HUMIDITY = '4'      # raw value = %
SENSOR_BATTERY = '9'       # raw value ÷ 10 = %
SENSOR_TYPE_CODE = '03'

# Known sensor PIDs — used as the primary discriminator to identify sensor devices
KNOWN_SENSOR_PIDS = {'Z4tRml'}

# Electrician / smart plug DPIDs
PLUG_COUNTDOWN = '2'        # countdown/timer, seconds
PLUG_POWER_ON_STATE = '18'  # relay restore behavior after power loss, enum
PLUG_LED_STATUS = '19'      # indicator LED behavior, enum
PLUG_ADDED_ENERGY = '26'    # add_ele; not the lifetime consumption counter
PLUG_CURRENT = '27'         # cur_current, mA
PLUG_POWER = '28'           # cur_power, scale 1, W
PLUG_VOLTAGE = '29'         # cur_voltage, scale 1, V
PLUG_OVERCURRENT_PROTECTION = '32'  # overcurrent protection enable flag
PLUG_ENERGY = '42'          # total electricity consumption, raw value / 1000 kWh
PLUG_TIMER_STATUS = '72'    # timer or last-off-status value

# Supported device types (derived from old integration, actual support depends on pid_list)
SWITCH_TYPE_CODE = '00'
LIGHT_TYPE_CODE = '01'
RGB_LIGHT_TYPE_CODE = '02'  # Some RGB lights report type code 02

PLATFORMS = [
    "light",
    "media_player",
    "switch",
    "sensor",
    "binary_sensor",
    "fan",
    "number",
    "select",
]

DEFAULT_MIN_KELVIN = 2000
DEFAULT_MAX_KELVIN = 6500

# Confirmed model-specific color temperature ranges.
# CozyLife lights report DPID 3 on a normalized 0-1000 scale, but do not expose
# the physical Kelvin endpoints over the local protocol.
LIGHT_KELVIN_RANGES = {
    "rju4K7": (2700, 6500),
}
