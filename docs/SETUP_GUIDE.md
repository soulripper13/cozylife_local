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
6. [Analyzing Discovery Logs & Submitting New Models](#6-analyzing-discovery-logs--submitting-new-models)

---

## 1. Pre-Setup Checklist

Before attempting to configure CozyLife Local, make sure you have:
*   A fully functional **Home Assistant** instance.
*   **HACS (Home Assistant Community Store)** installed.
*   A **2.4 GHz Wi-Fi Network** (CozyLife devices do not support 5 GHz Wi-Fi).
*   Administrative access to your home router to configure static IPs (highly recommended).
*   The **CozyLife App** installed on your mobile phone (iOS or Android) for the initial Wi-Fi configuration.

---

## 2. Step-by-Step Onboarding

### Step 2.1: Onboarding with the CozyLife App

> [!IMPORTANT]
> Because CozyLife devices do not have an ethernet port, they must first be joined to your local Wi-Fi network using the official CozyLife app. This integration **cannot** perform the initial wireless pairing.

1. **Factory Reset the Device**: 
   * **Switches/Plugs**: Press and hold the physical button for 5–7 seconds until the LED starts flashing rapidly.
   * **Smart Lights**: Turn the physical light switch off and on 3 to 5 times sequentially until the light bulb flashes rapidly.
2. **Connect via CozyLife App**:
   * Open the app, ensure your phone is connected to your **2.4 GHz Wi-Fi network** with Bluetooth enabled.
   * Click **+** (Add Device) and follow the prompts to input your Wi-Fi credentials.
   * Once successfully added, you can control the device via the CozyLife app.
   * **Note**: Keep the device powered on and connected to the Wi-Fi.

---

### Step 2.2: Assigning Static IPs / DHCP Reservations

Local integration relies on sending network packets directly to your device's IP address. If your router restarts or lease times expire, standard DHCP may assign a new IP address to your CozyLife device, making it appear "Unavailable" in Home Assistant.

> [!TIP]
> Setting up a **DHCP Reservation** (also known as a **Static IP reservation** or **IP binding**) in your router's settings is the most important step to guarantee long-term stability.

1. Log into your home router's admin portal (e.g., `192.168.1.1` or `192.168.0.1`).
2. Navigate to the **DHCP Server / LAN / IP Reservation** settings page.
3. Locate your CozyLife device in the list of active clients (look for its MAC address).
4. Assign a specific, fixed IP address (e.g., `192.168.1.150`) to its MAC address.
5. Save the configuration.

---

### Step 2.3: Locating Your Device's IP Address

You will need the local IP address of your device if you prefer to add it manually or verify its network connectivity.

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
Use tools like **Angry IP Scanner** (PC/Mac) or **Fing** (Mobile App) to scan port `5555`. All online CozyLife devices listen on **port 5555** (both UDP and TCP).

---

## 3. Home Assistant Integration Setup

Once your device is connected to your Wi-Fi and has a reserved IP address, you can add it to Home Assistant:

```
Settings ➔ Devices & Services ➔ + Add Integration ➔ CozyLife Local
```

### Setup Flow Fields:
*   **IP Address (Optional)**:
    *   *Leave empty* to scan the local subnet automatically.
    *   *Enter a single IP* (e.g., `192.168.1.144`) to add that specific device directly.
*   **Network CIDR (Optional)**: The default is `auto` (which scans your Home Assistant instance's default network). You can override this to scan a specific subnet (e.g., `192.168.10.0/24`).
*   **Sleeping temp/humidity sensor (Checkbox)**: Check this box **ONLY** if the IP you entered belongs to a battery-powered environmental sensor. (See [Sleeping Battery-Powered Sensors](#sleeping-battery-powered-sensors-temphumidity) below).
*   **Skip validation (Developer Option)**: Enables advanced users and developers to add a device manually without verification. Great for remote environments or provisioning devices that are temporarily powered down.

After setup, the integration exposes the configured local IP address as a diagnostic sensor on the Home Assistant device page. Use that visible IP when checking DHCP reservations, router firewall rules, or direct port `5555` connectivity.

---

## 4. Device-Specific Guides & Behaviors

### Wall Switches & Multi-Gang Switch Rockers

Multi-gang devices (such as double or triple rocker wall switches) share a single IP address and a single CozyLife chip.

*   **Bitmask Control**: CozyLife controls multi-gang channels via a bitmask on Data Point ID 1 (`DPID 1`).
*   **Auto-Detection**: The integration automatically reads the device's capability maps and count attributes. It determines if it has 1, 2, 3, or more switch gangs.
*   **Individual Entities**: Home Assistant will create a distinct switch entity for each gang (e.g., `Switch 1`, `Switch 2`). You can rename these individually in the Home Assistant UI without losing local link stability.

---

### Smart Plugs & Power Monitoring Outlets

Our integration supports both standard on/off plugs and high-end smart plugs equipped with electricity metering chips.

*   **Metered Plugs**: If the device catalog or DPID response reports DPIDs `26` through `29`, the integration will automatically expose:
    *   **Voltage** (V) - `sensor.cozylife_device_voltage`
    *   **Current** (mA) - `sensor.cozylife_device_current`
    *   **Active Power** (W) - `sensor.cozylife_device_power`
    *   **Total Cumulative Energy** (kWh) - `sensor.cozylife_device_energy`
*   **Home Assistant Energy Dashboard**: The energy entity (`sensor.cozylife_device_energy`) utilizes standard `device_class: energy` and `state_class: total_increasing`. You can directly add it to the Home Assistant Energy Dashboard to track historical consumption!

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

Battery-powered sensors (such as environment sensors and button controllers) use aggressive deep-sleep modes to achieve months or years of battery life. They only wake up for a split second to transmit changes.

> [!WARNING]
> Because these devices sleep, standard pinging or active queries will fail 99% of the time, causing setup timeouts. 

#### Setup Workflow for Battery Sensors:
1. Set a DHCP reservation in your router for the sensor.
2. In the config flow, enter the sensor's IP address.
3. Check the box **"Sleeping temp/humidity sensor"**.
4. The integration will immediately create the Temperature, Humidity, and Battery entities in Home Assistant using **optimistic cached metadata** (without trying to contact the sleeping hardware).
5. **Initial State**: The entities will initially show as `Unavailable` or `Unknown`.
6. **Activating the Sensor**: To push the first set of readings immediately:
   * Press the physical button on the sensor.
   * Or, breathe warm air onto the sensor grill to trigger a delta event (temperature/humidity change) that forces a wake-up broadcast.
7. **Subsequent Polls**: The integration establishes a dedicated polling window based on the device's reported configuration interval. The standard firmware interval is **1800 seconds / 30 minutes**. An experimental option allows **600 seconds / 10 minutes** on compatible firmware.

> [!WARNING]
> The experimental `600s` interval is not guaranteed. In the latest long-run router CSV check for a `Z4tRml` sensor, 58 of 61 post-baseline transitions stayed near 10 minutes, with a best run of 22 consecutive short cycles. Three cycles still fell back to about `1800s` / 30 minutes. Keep the standard `1800s` interval when predictable update timing matters more than faster readings.

#### Restart Behavior

Home Assistant cannot change a sleeping sensor's report interval while the sensor is asleep. After Home Assistant restarts, the integration restores the configured interval from the config entry and begins catch polling, but the value is only pushed to the device when the sensor next wakes.

For the standard `1800s` interval, this usually means the first post-restart update may take up to about 30 minutes unless you wake the sensor manually. For the experimental `600s` interval, the same rule applies: Home Assistant will reinforce the shorter value when the sensor wakes, but the sensor firmware can still occasionally report or revert to `1800s`.

#### Blocking WAN for Experimental Short Intervals

If you are testing the experimental `600s` interval, you can optionally block the sensor's WAN/internet access at the router while keeping local LAN access to Home Assistant open. This reduces external variables while testing, but firmware behavior can still cause a fallback to `1800s`.

Use this network shape:

```text
Home Assistant <---- LAN allowed ----> CozyLife sensor
CozyLife sensor ---- WAN blocked ----> Internet / vendor cloud
```

Before adding any block rule:

1. Create a DHCP reservation for the sensor so its IP address does not change.
2. Confirm Home Assistant and the sensor are on the same LAN, or on routed VLANs that can reach each other.
3. Allow TCP/UDP port `5555` between Home Assistant and the sensor.
4. Block only forwarding from the sensor IP to WAN/internet.
5. Do not enable AP/client isolation or put the sensor on an isolated guest network.

WAN blocking does not guarantee that short intervals will persist. Some firmware may still report or revert to `1800s` even when internet access is blocked.

OpenWrt / iStoreOS UCI example:

```sh
uci add firewall rule
uci set firewall.@rule[-1].name='Block-CozyLife-sensor-WAN'
uci set firewall.@rule[-1].src='lan'
uci set firewall.@rule[-1].dest='wan'
uci set firewall.@rule[-1].src_ip='192.168.3.124'
uci set firewall.@rule[-1].proto='all'
uci set firewall.@rule[-1].target='REJECT'
uci commit firewall
/etc/init.d/firewall reload
```

MikroTik RouterOS example:

```routeros
/ip firewall filter add chain=forward src-address=192.168.3.124 out-interface-list=WAN action=drop comment="Block CozyLife sensor WAN"
```

For TP-Link, ASUS, UniFi, pfSense/OPNsense, Eero, Deco, Google/Nest WiFi, and ISP routers, use the router's client-specific internet block, parental control, traffic rule, or firewall rule feature. The important requirement is to block only internet access for the sensor, not local traffic between Home Assistant and the sensor.

---

## 5. Network Troubleshooting & Router Tuning

If you have completed setup but your devices keep dropping off or showing as `Unavailable`, review these network settings:

*   **AP Isolation (Client Isolation)**: Some routers have a security feature called "Access Point Isolation" or "Wireless Client Isolation" enabled. This prevents Wi-Fi devices from talking directly to each other or to your Home Assistant server. **Ensure this setting is DISABLED** in your router's wireless options.
*   **IGMP Snooping / Multicast**: CozyLife uses UDP multicast broadcasts for auto-discovery. Ensure IGMP Snooping is enabled on your router and network switches to allow local multicast packets to traverse from Wi-Fi to Ethernet seamlessly.
*   **Port 5555**: The CozyLife protocol uses UDP/TCP port `5555`. Ensure that no internal firewalls or parent router rules are blocking traffic on this port between your Home Assistant IP and the device IP range.
*   **IP Ping Test**: If a device is unavailable, open a Terminal/SSH in Home Assistant and run `ping <device_ip>`. If you get no response, the device has either lost power, changed IP address, or is disconnected from the Wi-Fi.

## 6. Analyzing Discovery Logs & Submitting New Models

If your device is added but some entities are missing (e.g., a smart plug that doesn't show its metering data), we need to check the exact **Data Points (DPIDs)** the hardware reports.

### Step 1: Enable CozyLife Debug Logs
Add the following configuration to your `configuration.yaml` file to capture raw device handshakes:

```yaml
logger:
  default: info
  logs:
    custom_components.cozylife_local: debug
```

Restart Home Assistant to apply the logging configuration.

### Step 2: Locate the Handshake Log
Go to `Settings ➔ System ➔ Logs` and search for `cozylife_local`. Look for the successful discovery line:

```text
Successfully discovered device 192.168.1.177 locally: DID=0011223344, PID=xyz987, Type=00, DPIDs=['1', '26', '27', '28', '29']
Detected 1 outlet entity/entities at 192.168.1.177 with DPIDs: ['1', '26', '27', '28', '29']
```

### Step 3: Open a GitHub Issue
If the entity mappings are incorrect:
1. Note your device model (e.g., Dual Socket, RGB Bulb model).
2. Copy the **PID** (Product ID) and **DPIDs** array from your debug logs.
3. Open an issue on our [GitHub Repository](https://github.com/soulripper13/cozylife_local/issues) using the "New Device Request" template.
4. Developers will map the PID and DPIDs in the model catalog to expose the correct entities automatically in the next integration update!
