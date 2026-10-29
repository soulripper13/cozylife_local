# CozyLife Local for Home Assistant

[![GitHub Release](https://img.shields.io/github/release/soulripper13/cozylife_local.svg?style=for-the-badge)](https://github.com/soulripper13/cozylife_local/releases)
[![GitHub License](https://img.shields.io/github/license/soulripper13/cozylife_local.svg?style=for-the-badge)](https://github.com/soulripper13/cozylife_local/blob/main/LICENSE)
[![GitHub Issues](https://img.shields.io/github/issues/soulripper13/cozylife_local.svg?style=for-the-badge)](https://github.com/soulripper13/cozylife_local/issues)
[![GitHub Stars](https://img.shields.io/github/stars/soulripper13/cozylife_local?style=for-the-badge&logo=github)](https://github.com/soulripper13/cozylife_local/stargazers)
![Maintenance](https://img.shields.io/maintenance/yes/2026.svg?style=for-the-badge)
[![Support Development](https://img.shields.io/badge/Support-Development-FF5E5B?style=for-the-badge&logo=ko-fi&logoColor=white)](https://ko-fi.com/soulripper13)
[![Support via PayPal](https://img.shields.io/badge/Support-PayPal-00457C?style=for-the-badge&logo=paypal&logoColor=white)](https://paypal.me/SKatoaroo)

<div align="center">
  <img src="https://raw.githubusercontent.com/soulripper13/cozylife_local/main/assets/hero_banner.jpeg" alt="CozyLife Local Hero Banner" width="100%">
  <br><br>
  <strong>A premium, 100% local Home Assistant integration for CozyLife smart devices. Control your switches, lights, smart plugs, and environment sensors securely without any cloud dependency.</strong> 
  <br><br> 
  
  <a href="https://my.home-assistant.io/redirect/hacs_repository/?owner=soulripper13&repository=cozylife_local&category=integration">
    <img src="https://my.home-assistant.io/badges/hacs_repository.svg" alt="Open in HACS">
  </a>
  <a href="https://my.home-assistant.io/redirect/config_flow_start/?domain=cozylife_local">
    <img src="https://my.home-assistant.io/badges/config_flow_start.svg" alt="Add Integration">
  </a>
</div>

---

## Supported Local Capabilities

**CozyLife Local supports the APK-catalogued Wi-Fi profiles whose local DPIDs have a verified Home Assistant mapping.**

Covered profiles include switches and outlets, metered plugs, lights, fans, environment and safety sensors, supported energy-storage and gateway profiles, video-state profiles, X7 speakers, and the APK-panel-verified AI profiles. Physical device specifications still vary by manufacturer and model layout; a UDP-announcing device must also accept the documented local TCP/5555 protocol before its entities can be available. See the [APK local product coverage](docs/APK_LOCAL_PRODUCT_COVERAGE.md) for the evidence boundary and unsupported controls.

We are still looking for reports to expand coverage for:

*   Single gang wall switches & smart plugs
*   High-power/power-monitoring smart plugs and outlets
*   RGB & Tunable White lights (bulbs, ceiling fixtures, and LED strips)
*   Battery-powered smart sensors (temperature, humidity, motion, door/window, smoke)

> [!NOTE]
> If you have an unsupported device model or experience issues, follow the [Setup & Troubleshooting Guide](docs/SETUP_GUIDE.md#6-safe-diagnostics-and-device-reports). Debug output is deliberately safe to share: do not post your local IP address or device ID.

---

## Key Features

*   ⚡ **100% Local Control**: Zero reliance on the CozyLife cloud. All control commands and sensor status requests happen locally over your Wi-Fi network.
*   📶 **Powered-Device Availability**: Supported powered devices are checked locally in the background and become unavailable after a physical power cut when they no longer respond. Normal controls remain independent of that background check.
*   🔌 **Automatic Local Discovery**: One submitted CozyLife Local entry runs a shared UDP/6095 listener and sends APK-style `cmd:0` discovery broadcasts every 10 seconds. Home Assistant presents each newly announced device through its normal discovered-device card.
*   ⏱️ **Sleeping Sensor Wake Windows**: Supported sleeping temperature/humidity sensors use separate local status probes every 250 ms. This is independent of UDP discovery and is an attempt cadence—not a claim about the device's wake or reporting schedule.
*   📍 **Resilient Local Addressing**: Each device exposes its local IP diagnostically, and the shared listener refreshes known device addresses from APK-style announcements.
*   🧭 **Visible Device IP Diagnostics**: Exposes each configured device's local IP address as a diagnostic sensor in Home Assistant, making router checks and troubleshooting easier.
*   🎛️ **Multi-Gang Switch Bitmasks**: Native, low-level bitmask control on DPID 1 ensures multi-gang rockers (e.g., double or triple rocker switches) act as individual, responsive entities.
*   🧩 **Switch Option Controls**: Exposes Power-on State, LED Status, and overcurrent protection controls only where the individual device reports the required DPIDs.
*   📈 **Smart Energy Metering**: Exposes voltage, current, active power, and separate device-reported energy counters (compatible with the HA Energy Dashboard), plus supported countdown and plug option controls.
*   🔊 **Local Speaker Control**: The APK-catalogued Wi-Fi X7 speaker profiles expose local power, volume, play/pause, and Bluetooth, local-playback, or line-in source controls through one Home Assistant media player.
*   💡 **State & Color-Mode Safeguards**: Smooth transitions and automatic work-mode state preservation prevents smart lights from glitching or flashing dark blue during custom RGB commands.
*   🕒 **Light Countdowns**: Exposes validated local countdown controls on compatible bulbs.
*   🗓️ **Home Assistant Scheduling**: Use Home Assistant automations, Schedule helpers, or Timer helpers for timed control. CozyLife Local does not create a second per-device scheduler.

---

## Quick-Start Guide

For complete, detailed instructions on onboarding new hardware, configuring your router, and optimizing your network, read our [Comprehensive Device Setup Guide](docs/SETUP_GUIDE.md).

### Installation via HACS

1. **Add the custom repository**:
   * Open **HACS** in Home Assistant ➔ Click **Integrations**.
   * Click the three dots in the top-right corner ➔ Select **Custom repositories**.
   * In the **Repository** field, paste: `https://github.com/soulripper13/cozylife_local`
   * Select **Integration** under the Category dropdown ➔ Click **Add**.

2. **Download Integration**:
   * Click on the newly discovered **CozyLife Local** integration card.
   * Click **Download** in the bottom-right corner and select the desired version.

3. **Restart Home Assistant**:
   * Navigate to `Settings` ➔ `System` ➔ Click `Restart` in the top right.

CozyLife Local is installed through HACS as a custom repository. HACS normally offers stable releases; enable prereleases in HACS only when you intentionally want to test one.

### Manual installation

Download the `cozylife_local.zip` asset from the chosen GitHub release, extract it into your Home Assistant configuration directory so that `custom_components/cozylife_local/manifest.json` exists, then restart Home Assistant. Manual installation does not receive HACS updates automatically.

---

## Configuration

Once HACS has completed installation and Home Assistant has restarted:

1. Navigate to `Settings` ➔ `Devices & Services`.
2. Click `+ ADD INTEGRATION` in the bottom-right.
3. Search for **"CozyLife Local"** and click to open the configuration flow.
4. Click **Submit**. This creates the integration's one discovery controller; there are no device IP, CIDR, or sensor-profile fields.

The controller keeps one shared local UDP listener active. A supported Wi-Fi device that announces itself on UDP/6095 appears in Home Assistant's normal discovered-device card. Click **Add** to create its entry from the captured DID, PID, and IP plus the APK model catalogue DPID profile. Adding the entry does not require a synchronous TCP inventory reply; later local state and control use TCP/5555.

When updating an existing installation, restart Home Assistant after HACS has installed the update. Do not remove a working CozyLife config entry merely to update it: removing an entry can discard Home Assistant's entity-registry continuity and history.

### Sleeping Environment Sensors

Battery-powered environment sensors spend most of their time asleep. CozyLife Local keeps the APK-derived local TCP status path available and records valid incoming reports; it does not claim a fixed wake or reporting interval. See the [Sleeping Battery-Powered Sensors](docs/SETUP_GUIDE.md#sleeping-battery-powered-sensors-temphumidity) section for setup details.

---

## Switches and Metered Plugs

### Wall Switches

Multi-gang wall switches use `DPID 1` as a bitmask. Home Assistant exposes each gang as its own switch entity while preserving one local device connection.

When a wall switch reports the required option DPIDs, CozyLife Local exposes:

*   **Power-on State** (`DPID 18`)
*   **LED Status** (`DPID 19`)

Supported Wi-Fi home-appliance profiles use the same documented `DPID 1`
power control and are shown as a generic Home Assistant switch rather than as
an invented appliance type.

### Metered Smart Plugs

Metered plugs expose outlet control plus electricity sensors:

*   **Energy** (`DPID 26`) and **Total energy** (`DPID 42`) independently, when reported, as kWh using `/1000`. Their reset behavior is device-specific.
*   **Current** (`DPID 27`) as raw mA
*   **Power** (`DPID 28`) as raw W
*   **Voltage** (`DPID 29`) as raw V

Compatible plugs can also expose:

*   **Countdown** (`DPID 2`)
*   **Power-on State** (`DPID 18`)
*   **LED Status** (`DPID 19`)
*   **Overcurrent Protection** (`DPID 32`)

### Scheduling

Use Home Assistant automations, Schedule helpers, or Timer helpers for recurring and one-time control. CozyLife Local does not create per-device schedule entities or write to/clear the CozyLife app's native DPID 3 timer records. Timers configured in the CozyLife app are left unchanged.

### Smart Lights

Compatible smart bulbs and light strips expose local light controls plus:

*   **Countdown** (`DPID 13`) exposed as the same seconds-based countdown number pattern used by compatible plugs
*   **Power-off Memory** (`DPID 9`) as a configuration switch where reported

The two APK-panel-verified AI-lamp profiles use this same local light entity.

### Fans

Supported Wi-Fi motor profiles create one native Home Assistant fan entity.

*   **Power** (`DPID 1`)
*   **Speed** using the reported speed profile (`DPID 16`, `4`, or `9`)
*   **Wind Mode** (`DPID 11`) when reported, including Normal, Natural, Sleep, Forest, Maternal and infant, and AI smart

### Sensors and Safety Devices

Supported Wi-Fi sensor profiles expose only their documented local DPIDs:

*   **Temperature**, **Humidity**, and **Battery** (`DPID 8`, `4`, and `9`)
*   **Contact** (`DPID 7`), **Motion** (`DPID 6`), and **Moisture / Water Leak** (`DPID 10`) binary sensors where supported by the profile
*   Wi-Fi multi-sensors combine the relevant temperature, humidity, battery, contact, and motion entities

### Energy Storage and Gateways

Supported energy-storage profiles expose the telemetry they advertise:

*   **Battery Status**, **Battery**, **Battery Temperature**, **Fault Code**, and remaining usable/charging time
*   **Total** and per-branch **Input/Output Power** sensors
*   One **Output** switch for each documented output channel; an unknown bitmask never creates guessed channels

Supported Wi-Fi gateway profiles expose **Discovery Mode** as a configuration switch when `DPID 2` is present.

### Doorbells, Speakers, and AI Profiles

Supported local video-state profiles expose **Call Active**, **Battery**, **Low Battery**, and **Motion** only where their documented DPIDs are advertised. Video streaming and lock control are not exposed.

The two APK-panel-verified X7 Wi-Fi speaker profiles create a Home Assistant media player with **Power**, **Volume**, **Play/Pause**, and **Bluetooth**, **Local Playback**, or **Line-in** source selection. This is local media control, not voice-assistant support.

The panel-verified S3 profile exposes an **Expression** select with Happy, Sad, Disappointed, Angry, and Surprised. Conversation controls are not exposed.

---

## CozyLife Data Point IDs (DPIDs)

CozyLife devices report their functionalities via standard Data Point IDs (DPIDs). Below is a quick-reference mapping of known features exposed by this integration:

| DPID | Target Function | Description |
|------|-----------------|-------------|
| `1` | Power / Switch Bitmask | Master light power, plug power, or multi-gang rocker state bitmask. |
| `2` | Work Mode / Plug Countdown | Light profile settings on bulbs or the countdown control on compatible plugs. |
| `3` | Color Temperature / App Timer Records | White-light warmth on compatible lights; native timer records managed by the CozyLife app on compatible plugs. |
| `4` | Brightness / Humidity | Light intensity scaling on compatible lights or relative humidity on sensors. |
| `5` | Hue | 360-degree color hue control on addressable RGB lights. |
| `6` | Saturation | Color saturation percentage on RGB lights. |
| `7` | Contact | Door/window magnetic contact sensor state. |
| `8` | Temperature | Temperature sensor data in Celsius. |
| `9` | Battery Level | Exposes remaining battery charge on supported portable sensors. |
| `10` | Moisture Status | Water-leak detection and alarm sensor. |
| `13` | Light Countdown | Countdown seconds on compatible lights. |
| `18` | Power-on State | Relay restore behavior after power loss on compatible plugs and wall switches. |
| `19` | LED Status | Indicator LED behavior on compatible plugs and wall switches. |
| `26` | Energy | Independently reported energy counter on compatible metered plugs. |
| `42` | Total Energy Consumption (kWh)| Exposes total energy consumption, fully compatible with HA Energy Dashboard. |
| `27` | Active Current (mA) | Live electrical current monitoring. |
| `28` | Active Power (W) | Live active load power monitoring. |
| `29` | Line Voltage (V) | Live grid voltage monitoring. |
| `32` | Overcurrent Protection | Config switch for compatible metered plugs. |
| `72` | Timer Status | Timer or last-off status on compatible plugs. |

---

## Troubleshooting & FAQ

Please refer to the [Troubleshooting & Network Tuning Section in our Setup Guide](docs/SETUP_GUIDE.md#5-network-troubleshooting--router-tuning) if you encounter:
*   Devices repeatedly dropping offline or showing as `Unavailable`
*   Network firewall questions or router Access Point (AP) isolation settings
*   Multicast auto-discovery failures or IGMP Snooping advice
*   Missing electricity metering or power monitoring entities

---

## Contributing

We welcome contributions. Read [CONTRIBUTING.md](CONTRIBUTING.md) before proposing a catalogue mapping or a new entity. Include the integration version, product name, supported DPIDs, and safe diagnostic output; never publish a local IP address, device ID, or raw state values.

---

## Support the Project

This project is developed and maintained in spare time and is provided free to the community. Any contribution is appreciated — but never required ❤️

### Ways to Support

*   **Ko-fi**: [https://ko-fi.com/soulripper13](https://ko-fi.com/soulripper13)
*   **PayPal**: [https://paypal.me/SKatoaroo](https://paypal.me/SKatoaroo)
*   **Bitcoin (BTC)**: `bc1qvu8a9gdy3dcxa94jge7d3rd7claapsydjsjxn0`
*   **Solana (SOL)**: `4jvCR2YFQLqguoyz9qAMPzVbaEcDsG5nzRHFG8SeaeBK`

Thank you for being part of the CozyLife Local community!
