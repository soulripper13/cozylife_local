# CozyLife Local: Device Setup & Troubleshooting Guide

Welcome to the comprehensive setup and troubleshooting guide for **CozyLife Local** in Home Assistant. This integration allows you to control your CozyLife-compatible smart plugs, multi-gang switches, lights, and battery-powered sensors **100% locally**, removing any cloud dependency.

To get the most out of your local smart home setup and avoid connection drops, follow this detailed, step-by-step guide.

---

## Table of Contents
1. [Pre-Setup Checklist](#1-pre-setup-checklist)
2. [Step-by-Step Onboarding](#2-step-by-step-onboarding)
   * [Step 2.1: Onboarding with the CozyLife App](#step-21-onboarding-with-the-cozylife-app)
   * [Step 2.2: Assigning Static IPs / DHCP Reservations](#step-22-assigning-static-ips--dhcp-reservations)
   * [Step 2.3: Locating Your Device's IP Address](#step-23-locating-your-devices-ip-address)
3. [Home Assistant Integration Setup](#3-home-assistant-integration-setup)
4. [Device-Specific Guides & Behaviors](#4-device-specific-guides--behaviors)
   * [Wall Switches & Multi-Gang Switch Rockers](#wall-switches--multi-gang-switch-rockers)
   * [Smart Plugs & Power Monitoring Outlets](#smart-plugs--power-monitoring-outlets)
   * [Smart Bulbs, Ceiling Lights & LED Strips](#smart-bulbs-ceiling-lights--led-strips)
   * [Sleeping Battery-Powered Sensors (Temp/Humidity)](#sleeping-battery-powered-sensors-temphumidity)
5. [Network Troubleshooting & Router Tuning](#5-network-troubleshooting--router-tuning)
6. [Safe Diagnostics & Device Reports](#6-safe-diagnostics--device-reports)

---

## 1. Pre-Setup Checklist

Before attempting to configure CozyLife Local, make sure you have:
*   A fully functional **Home Assistant** instance.
*   **HACS (Home Assistant Community Store)** installed, if you want managed installation and updates. Manual ZIP installation is also supported.
*   A Wi-Fi network compatible with your particular device. Many CozyLife Wi-Fi products use 2.4 GHz; follow the instructions for the hardware you own.
*   Administrative access to your router if you want to create a DHCP reservation (optional, but useful for troubleshooting).
*   The **CozyLife App** installed on your mobile phone (iOS or Android) for the initial Wi-Fi configuration.

---

## 2. Step-by-Step Onboarding

### Step 2.1: Onboarding with the CozyLife App

> [!IMPORTANT]
> Most CozyLife Wi-Fi products must first be joined to the local network with the official CozyLife app. This integration **cannot** perform the initial wireless pairing. Follow the reset and pairing instructions supplied with your specific hardware.

1. **Factory Reset the Device**: 
   * **Switches/Plugs**: Follow the manufacturer's reset sequence; many use a long press until the LED flashes.
   * **Smart Lights**: Follow the manufacturer's reset sequence; many use repeated power cycles until the bulb flashes.
2. **Connect via CozyLife App**:
   * Open the app and ensure your phone is connected to the network required by the device's pairing instructions.
   * Click **+** (Add Device) and follow the prompts to input your Wi-Fi credentials.
   * Once successfully added, you can control the device via the CozyLife app.
   * **Note**: Keep the device powered on and connected to the Wi-Fi.

---

### Step 2.2: Assigning Static IPs / DHCP Reservations

The integration sends local traffic to each device's current IP address. When a configured device makes a later UDP/6095 announcement, the shared discovery listener refreshes that address automatically. A DHCP reservation is therefore optional, not a requirement for normal address changes.

> [!TIP]
> A **DHCP reservation** (also called a static IP reservation or IP binding) can still make router-side diagnosis easier, especially on networks that block broadcasts between segments.

1. Log into your home router's admin portal (e.g., `192.168.1.1` or `192.168.0.1`).
2. Navigate to the **DHCP Server / LAN / IP Reservation** settings page.
3. Locate your CozyLife device in the list of active clients (look for its MAC address).
4. Assign a specific, fixed IP address (e.g., `192.168.1.150`) to its MAC address.
5. Save the configuration.

---

### Step 2.3: Locating Your Device's IP Address

You may need the local IP address to verify network connectivity or a DHCP reservation. Automatic discovery does not require entering it into the integration.

#### Method A: Inside the CozyLife App
1. Open the CozyLife App and tap on your device.
2. Click the **three dots `...` / Settings** icon in the top right corner.
3. Tap **Device Information**.
4. Scroll down to find the listed **IP Address** (e.g., `192.168.1.45`).

#### Method B: Router Client List
Look for hostnames representing popular IoT chips used by CozyLife. Common hostnames include:
*   `HF-LPT230` or similar prefix
*   `ESP_XXXXXX` (for ESP8266/ESP32 variants)
*   `hanyu_smart_device`
*   `CozyLife_XXXXXX`

#### Method C: Network Scanner Tools
Use tools like **Angry IP Scanner** (PC/Mac) or **Fing** (Mobile App) only as a supplementary reachability check. A TCP/5555 scan cannot substitute for CozyLife's UDP/6095 discovery, and a sleeping battery sensor may not accept a TCP connection while it is asleep.

---

## 3. Home Assistant Integration Setup

Once your device is connected to Wi-Fi, add CozyLife Local to Home Assistant:

```
Settings ➔ Devices & Services ➔ + Add Integration ➔ CozyLife Local
```

Click **Submit**. The integration creates one persistent discovery controller with no IP address, CIDR, or sensor-profile fields. It sends the APK-style UDP/6095 discovery frame at startup and then every 10 seconds. A newly announced supported Wi-Fi device appears in Home Assistant's normal discovered-device card; click **Add** to create its entry from the captured DID, PID, and IP plus the APK model catalogue DPID profile. Adding does not require a synchronous TCP inventory reply.

For a supported sleeping temperature/humidity sensor, the same saved announcement creates the entry and then begins 250 ms known-address status probing. This is a local connection-attempt cadence, not a claimed device wake or reporting schedule.

After setup, the integration exposes the configured local IP address as a diagnostic sensor on the Home Assistant device page. Use that visible IP when checking DHCP reservations, router firewall rules, or direct port `5555` connectivity.

---

## 4. Device-Specific Guides & Behaviors

### Wall Switches & Multi-Gang Switch Rockers

Multi-gang devices (such as double or triple rocker wall switches) share a single IP address and a single CozyLife chip.

*   **Bitmask Control**: CozyLife controls multi-gang channels via a bitmask on Data Point ID 1 (`DPID 1`).
*   **Auto-Detection**: The integration automatically reads the device's capability maps and count attributes. It determines if it has 1, 2, 3, or more switch gangs.
*   **Individual Entities**: Home Assistant will create a distinct switch entity for each gang (e.g., `Switch 1`, `Switch 2`). You can rename these individually in the Home Assistant UI without losing local link stability.
*   **Switch Options**: When the device reports the required DPIDs, the integration exposes Power-on State (`DPID 18`) and LED Status (`DPID 19`).

### Powered-Device Availability

Supported powered devices are checked locally in the background. If a light, plug, or switch loses physical power and no longer responds to its local connection, Home Assistant marks its entities unavailable after the next failed health check. This is distinct from normal control: turning a device on or off does not wait behind the background availability check.

---

### Smart Plugs & Power Monitoring Outlets

Our integration supports both standard on/off plugs and high-end smart plugs equipped with electricity metering chips.

*   **Metered Plugs**: If the device's local DPID response reports metering DPIDs, the integration exposes the values it recognizes. Total energy consumption is reported by DPID `42`.
    *   **Voltage** (V) - `sensor.cozylife_device_voltage`
    *   **Current** (mA) - `sensor.cozylife_device_current`
    *   **Active Power** (W) - `sensor.cozylife_device_power`
*   **Device-reported Energy** (kWh) - `sensor.cozylife_device_energy`
*   **Home Assistant Energy Dashboard**: The energy entity (`sensor.cozylife_device_energy`) utilizes standard `device_class: energy` and `state_class: total_increasing`. You can directly add it to the Home Assistant Energy Dashboard to track historical consumption!

If a compatible plug reports both DPID 26 (**Energy**) and DPID 42 (**Total energy**), the integration exposes them independently. Do not assume the two counters have the same source or reset behavior.

---

### Smart Bulbs, Ceiling Lights & LED Strips

CozyLife smart lights can range from basic dimmable warm white to fully addressable RGB strip lights.

*   **Color Temp Settings**: During setup or via **Options**, you can configure the specific Kelvin range (default: `2000K` to `6500K`) to match your bulb's hardware capabilities.
*   **Work Mode Management**: 
    *   Some lights require toggling the work mode (DPID 2) when moving between white light and RGB color.
    *   The integration features automatic state corrections. When you set an RGB color, it correctly preserves work mode states to prevent standard bulb firmware from glitching or flashing dark blue.
*   **State Transitions**: Color changes and brightness steps are computed with high fidelity to provide smooth local dimming.

---

### Sleeping Battery-Powered Sensors (Temp/Humidity)

Battery-powered sensors can sleep between reports. When one answers the integration's local UDP discovery request, Home Assistant shows its normal discovered-device card. Click **Add**; the saved announcement supplies the product identity and local address, so the integration creates the Temperature, Humidity, and Battery entities without waiting for a second response.

The integration keeps the APK-derived local status transport available for valid reports, but does not configure a report interval or claim a fixed wake cadence. The device controls when it is available.

### Scheduling

Use Home Assistant automations, Schedule helpers, or Timer helpers for recurring and one-time control. CozyLife Local does not create per-device schedule entities and does not write to or clear the CozyLife app's native DPID 3 timer records. Existing timers configured in the CozyLife app are left unchanged.

---

## 5. Network Troubleshooting & Router Tuning

If you have completed setup but your devices keep dropping off or showing as `Unavailable`, review these network settings:

*   **AP Isolation (Client Isolation)**: Some routers have a security feature called "Access Point Isolation" or "Wireless Client Isolation" enabled. This prevents Wi-Fi devices from talking directly to each other or to your Home Assistant server. **Ensure this setting is DISABLED** in your router's wireless options.
*   **UDP Discovery Broadcasts**: CozyLife discovery sends UDP broadcasts to port `6095`. After the one-time discovery controller is added, it keeps one shared local listener active and presents an announced, unconfigured device for confirmation. If discovery does not find a device, confirm that broadcasts can cross the relevant LAN segment; a device that never answers local discovery cannot be identified automatically.
*   **TCP Port 5555**: Local device status and control use TCP port `5555`. Ensure that no internal firewall or parent-router rule blocks traffic between Home Assistant and the device IP range.
*   **IP Ping Test**: A successful ping can confirm basic reachability, but a failed ping does not prove a CozyLife device is offline: devices may ignore ICMP and battery sensors can sleep between reports.

## 6. Safe Diagnostics & Device Reports

If your device is added but some entities are missing (e.g., a smart plug that doesn't show its metering data), we need to check the exact **Data Points (DPIDs)** the hardware reports.

### Step 1: Enable CozyLife Debug Logs
Open the affected CozyLife Local config entry, choose **Configure**, and enable **Verbose integration debug logs**. Reproduce the issue, then turn the option off when finished.

The integration summarizes protocol shape and DPID names only. It intentionally omits local IP addresses, device IDs, and raw state values from its debug output. You may also use Home Assistant's normal logger configuration if required, but do not publish private network details.

### Step 2: Locate the Handshake Log
Go to `Settings ➔ System ➔ Logs` and search for `cozylife_local`. Useful lines include local transport success or failure, discovery routing, and received DPID names:

```text
[COZYLIFE] Successfully fetched state with DPIDs: ['1', '2', '26', '27', '28', '29', '42']
Received CozyLife frame: {'cmd': 2, 'res': 0, 'msg': {'attr': [1, 2, 26, 27, 28, 29, 42]}}
```

### Step 3: Open a GitHub Issue
If the entity mappings are incorrect:
1. Note your device model (e.g., Dual Socket, RGB Bulb model).
2. Include the **integration version**, device name/model, expected and actual entities, and the safe DPID list from the logs.
3. Open an issue on our [GitHub Repository](https://github.com/soulripper13/cozylife_local/issues). There is no required issue template.
4. Remove local IP addresses and device IDs from screenshots or any additional text before posting.

Developers will compare the report against the bundled APK catalogue and verified local entity mappings. A catalogue entry alone does not guarantee that an unmapped local control is safe to expose.
