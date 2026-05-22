# Home Assistant CozyLife Local Integration
[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![Support Development](https://img.shields.io/badge/Support-Development-FF5E5B?style=for-the-badge&logo=ko-fi&logoColor=white)](https://ko-fi.com/soulripper13)

<div align="center">
  <img src="https://dummyimage.com/800x60/0d1117/ffffff&text=CozyLife%20Local+-+100%25%20Local%20Home%20Assistant%20Integration" alt="Hero Banner">
  <br><br>
  <strong>100% local Home Assistant integration for CozyLife smart devices. Control your switches, lights, and sensors without any cloud dependency.</strong> 
  <br><br> 
  <a href="https://ko-fi.com/soulripper13">
    <img src="https://storage.ko-fi.com/cdn/kofi5.png?v=6" alt="Support CozyLife Local on Ko-fi" width="220">
  </a>
</div>

---




## ⚠️ Beta / Testing Phase

**This is a new integration and should be considered in a beta testing phase.**

While it has been tested and confirmed to work with multi-gang switches, lights, sensors, and metered outlets, device models vary by PID and DPID layout. We are actively looking for testers to help verify additional CozyLife devices, especially:

-   Single switches
-   Smart plugs and outlets, including power-monitoring plugs
-   Lights (tunable white and RGB)
-   Temperature/humidity/battery sensors
-   Door/window, motion, water leak, smoke, and occupancy sensors
-   Other CozyLife devices

If you have one of these devices, please install the integration and report your experience! Your feedback is crucial for improving device compatibility.

**When reporting issues or sharing feedback:**
- Check your Home Assistant logs (see [Device Information & Logging](#device-information--logging) section)
- Copy the device discovery information (DID, PID, DPIDs)
- Share whether your device works correctly or not

Please [open an issue](https://github.com/soulripper13/cozylife_local/issues) with this information to help improve device compatibility.


## Features

- **100% Local Control:** No cloud connection is required for device operation after setup. All commands are sent directly to your devices on your local network.
- **UI-Based Configuration:** No YAML configuration required.
- **Auto Discovery:** Can scan the local IPv4 network and show the number of CozyLife devices found during setup.
- **Single IP Setup:** Devices can still be added one by one using their static IP address.
- **Sleeping Sensor Setup:** Battery temperature/humidity sensors can be added by static IP even when asleep; the integration creates cached metadata and updates values when the sensor wakes.
- **Multi-Gang Switch Support:** Correctly handles multi-button devices (e.g., double rocker switches), creating a separate entity for each switch.
- **Smart Plug & Outlet Support:** Creates outlet entities and exposes energy, current, power, and voltage sensors when the device reports metering DPIDs.
- **Sensor Support:** Supports temperature, humidity, battery, and selected binary sensor devices.
- **Device Discovery Logging:** Automatically logs device information (DID, PID, Type Code, and DPIDs) to help you understand and troubleshoot your devices.
- **Catalog-Based Device Detection:** Uses the bundled CozyLife model catalog plus reported DPIDs to avoid misclassifying motors, sensors, switches, outlets, and lights.
- **Automatic DPID Detection:** Smart detection of device capabilities including switch gangs, outlet metering, brightness, color, color temperature, and sensor values.
- **Developer Mode:** Includes a "Skip validation" option for developers or advanced users who need to set up devices remotely.

## Installation

### Prerequisites

- You must have [HACS (Home Assistant Community Store)](https://hacs.xyz/) installed on your Home Assistant instance.
- Your CozyLife devices must be connected to the same local network as your Home Assistant instance and have static IP addresses (or DHCP reservations) to prevent them from changing.

### Installation via HACS

1.  **Add Custom Repository:**
    -   Go to your HACS page in Home Assistant.
    -   Click on "Integrations".
    -   Click the three dots in the top right corner and select "Custom repositories".
    -   In the "Repository" field, paste the URL of this GitHub repository.
    -   In the "Category" field, select "Integration".
    -   Click "Add".

2.  **Install the Integration:**
    -   The "CozyLife Local" integration will now appear in your HACS integrations list.
    -   Click on it and then click "Download".
    -   Confirm the download and wait for it to complete.

3.  **Restart Home Assistant:**
    -   After installation, you **must restart Home Assistant** for the integration to be loaded. Go to `Settings` -> `System` and click the `Restart` button.

## Configuration

Once installed and restarted, you can add your CozyLife devices.

1.  **Navigate to Integrations:** Go to `Settings` -> `Devices & Services`.
2.  **Add Integration:** Click the `+ ADD INTEGRATION` button.
3.  **Search:** Search for "CozyLife Local" and click on it.
4.  **Setup:**
    -   Leave **IP address** empty and submit to scan the local network automatically.
    -   The integration will show how many CozyLife devices were found, then let you select one to add.
    -   To scan a specific subnet, set **Network CIDR** to something like `192.168.1.0/24`. The default `auto` scans Home Assistant's local IPv4 network.
    -   To add a device manually, enter the **single, static IP address** of your CozyLife device. The integration will connect and set it up.
    -   For battery temperature/humidity sensors that sleep most of the time, enter the device's static IP address and enable **Sleeping temp/humidity sensor**. This creates the temperature, humidity, and battery entities without contacting the device first; values will appear after the sensor next wakes/responds.
    -   Lights may prompt for minimum and maximum color temperature values.
    -   Sleeping sensors may prompt for report interval and sensitivity settings. The minimum report interval is 1800 seconds.
    -   **Developer Method:** To add a device remotely without an active connection (e.g., for development), enter its single, static IP address and check the "Skip validation" box.

### Device Options

After a device is added, open the integration entry's options to adjust device-specific behavior:

-   **Lights:** minimum and maximum Kelvin range.
-   **Sensor devices:** report interval, temperature sensitivity, and humidity sensitivity.
-   Battery temperature/humidity sensors use a minimum report interval of 1800 seconds because the CozyLife app and device firmware do not persist lower values.
-   **All devices:** optional debug logging.

## Device Information & Logging

This integration logs the identifiers and DPIDs needed to understand your device's capabilities and troubleshoot issues. When you add a device through normal discovery or manual IP setup, discovery information is automatically logged to Home Assistant's logs. Sleeping sensor setup creates cached metadata without waking the device, so it may not have a real DID in the logs at setup time.

### Viewing Your Device Information

After adding a device, check your Home Assistant logs to see detailed device information:

1. **Via UI:** Go to `Settings` → `System` → `Logs`
2. **Via configuration.yaml:** Add this to see CozyLife logs:
   ```yaml
   logger:
     default: info
     logs:
       custom_components.cozylife_local: info
   ```

### What You'll See in the Logs

When you add a device, the logs will show:

```
Successfully discovered device 192.168.1.177 locally: DID=12345678, PID=abc123, Type=00, DPIDs=['1', '26', '27', '28', '29']
Detected 1 outlet entity/entities at 192.168.1.177 with DPIDs: ['1', '26', '27', '28', '29']
```

### Understanding DPIDs

DPIDs (Data Point IDs) are the functions your device supports. Common DPIDs include:

| DPID | Function | Description |
|------|----------|-------------|
| `1` | Power / switch bitmask | Light power, outlet power, or switch gang bitmask depending on device |
| `2` | Work mode / countdown | Light mode on many lights; gang 1 countdown on many switches |
| `3` | Color temperature | Warm to cool white, mapped to the configured Kelvin range |
| `4` | Brightness / humidity / countdown | Brightness on lights, humidity on environment sensors, or gang 2 countdown on switches |
| `5` | Hue | RGB color hue on supported lights |
| `6` | Saturation / motion / countdown | RGB saturation, motion status, or gang 3 countdown depending on device |
| `7` | Contact / color | Door/window contact on supported sensors; alternative color control on some lights |
| `8` | Temperature / scene | Temperature on environment sensors; scene/effect mode on some lights |
| `9` | Battery | Battery level on supported sensors |
| `10` | Moisture | Water leak status on supported sensors |
| `11` | Smoke | Smoke alarm status on supported sensors |
| `14` | Sensor report interval | Reporting interval for supported sleeping sensors; 1800 seconds is the observed minimum |
| `24` | Humidity sensitivity | Sensitivity setting for supported environment sensors |
| `25` | Temperature sensitivity | Sensitivity setting for supported environment sensors |
| `26` | Energy / sensor-specific value | Total energy for metered plugs, in kWh; may appear as an unused or model-specific value on some sensors |
| `27` | Current | Current for metered plugs, in mA |
| `28` | Power | Power for metered plugs, in W |
| `29` | Voltage | Voltage for metered plugs, in V |
| `30` | Plug fault | Fault state reported by some metered plugs |
| `101` | Occupancy | Occupancy/proximity status on supported radar sensors |

### Understanding Device Type Codes

Your device's Type Code and PID catalog entry determine how it is set up in Home Assistant:

| Type Code | Category | Description |
|-----------|----------|-------------|
| `00` | Electrical | Wall switches, multi-gang switches, smart plugs, and outlets |
| `01` | Light | Basic lights, tunable white lights, RGB bulbs, LED strips, and ceiling lights |
| `02` | Motor | Recognized from the catalog to prevent false light/switch setup; motor entities are not currently exposed |
| `03` | Sensor | Temperature/humidity sensors and selected binary sensors |
| `05` | Home Appliances | Generic power devices may expose one switch entity when supported |
| `19` | Outdoor Travel | Generic power devices may expose one switch entity when supported |

**Note:** Some older or unknown RGB lights report Type Code `02`. The integration still supports this as a fallback when the bundled catalog does not identify the PID as a motor.

### Troubleshooting with Logs

If your device isn't working as expected:

1. **Check the device discovery log** to see which DPIDs were detected
2. **Look for warning messages** like:
   - `Failed to query DPID list from device` - The integration could reach the device, but could not get its capability list
   - `does not support switch entities` - The device was classified as something other than a supported switch/outlet
   - `has no supported sensor DPIDs` - The integration identified the device, but there is no implemented entity mapping for its reported DPIDs

3. **Share your logs** when reporting issues on GitHub:
   - Copy the device discovery lines from your logs
   - Include the DPID list and detected capabilities
   - This helps developers add support for your specific device model

### Example: LED Strip Not Dimming

If your LED strip shows up as on/off only:

1. Check logs for: `Successfully discovered device`
2. Look for DPIDs `3`, `4`, `5`, or `6` in the list
3. If present but not detected as brightness, [open an issue](https://github.com/soulripper13/cozylife_local/issues) with your device's PID and DPID list

## Supported Devices

This integration exposes Home Assistant entities for:

-   Multi-gang switches (e.g., double rocker switches)
-   Single switches
-   Smart plugs and outlets
-   Metered smart plugs with energy, current, power, and voltage sensors
-   Lights, including on/off, dimmable, tunable white, RGB, LED strip, and ceiling light models
-   Temperature/humidity/battery sensors, including known PID `Z4tRml`
-   Door/window contact and magnetic sensors when the device reports the expected contact DPID
-   Motion sensors when the device reports the expected motion DPID
-   Water leak sensors when the device reports the expected moisture DPID
-   Smoke sensors when the device reports the expected smoke DPID
-   Proximity/radar occupancy sensors when the device reports the expected occupancy DPID

The bundled CozyLife model catalog includes additional categories such as motors, cameras/locks, gateways, AI conversation devices, and smart speakers. Those categories may be detected for classification, but this integration does not currently expose full Home Assistant entities for them unless listed above.


## Common Issues & FAQ

### My LED strip only shows on/off, no brightness control

**Solution:** Update to the latest version of the integration, then check the device discovery logs for the PID, Type Code, and DPIDs. Supported lights are now identified using the bundled model catalog first, with a fallback for older unknown RGB lights that report Type Code `02`.

For typical RGB and tunable-white lights, the important DPIDs are:
- `1` for power
- `3` for color temperature
- `4` for brightness
- `5` for hue
- `6` for saturation

Check your logs for:
```
Device Type Code: 01
Supported DPIDs: ['1', '2', '3', '4', '5', '6']
```

If your light has brightness/color DPIDs but is still exposed as on/off only, please [open an issue](https://github.com/soulripper13/cozylife_local/issues) with your device's log output.

### LED strip blinks dark blue when changing colors

**Solution:** This was a known issue with incorrect work mode and DPID handling. Update to the latest version of the integration, which includes:
- Fixed work mode handling for RGB color changes (keeps work mode at 0)
- Corrected DPID mappings to match CozyLife protocol standard
- Added RGB color correction for accurate color reproduction

The issue was caused by setting work mode to 1 when changing colors, when it should remain at 0. This has been fixed to match the correct protocol behavior.

### How do I find my device's IP address?

Check your router's DHCP client list or use a network scanner app. Once found, set a static IP or DHCP reservation for the device to prevent the IP from changing.

### Device not connecting or timing out

1. Verify the device is on the same network as Home Assistant
2. Check the IP address is correct
3. Ensure no firewall is blocking port 5555 (CozyLife protocol)
4. Try pinging the device from Home Assistant's terminal
5. Check Home Assistant logs for detailed error messages

### Where can I find my device's DID and PID?

After adding a device through normal discovery or manual IP setup, check `Settings` → `System` → `Logs`. Look for the `Successfully discovered device` line, which contains DID, PID, Type Code, and DPIDs. Sleeping sensor setup may not show a real DID because it intentionally avoids contacting the device while it is asleep.

### My switch shows fewer gangs than it actually has

The integration detects switch count from countdown DPIDs (`2`, `4`, `6`, `8`, `10`, `12`, `14`, `16`) and can also infer counts from known model names. If the detected count is wrong, please [open an issue](https://github.com/soulripper13/cozylife_local/issues) with your PID, model name, and DPID list.

### My smart plug does not show energy/power sensors

Metering sensors are created only when the device is classified as an electrical switch/outlet and reports the metering DPIDs:

| Sensor | DPID |
|--------|------|
| Energy | `26` |
| Current | `27` |
| Power | `28` |
| Voltage | `29` |

If your plug has different metering DPIDs, open an issue with the discovery log so a mapping can be added.

### My battery sensor is unavailable after restart

Some CozyLife sensors sleep for long periods. The integration restores cached device metadata at startup, polls frequently until the first successful response, then schedules future polling windows around the configured report interval. For known battery temperature/humidity sensors, the CozyLife app and device firmware use a minimum report interval of 1800 seconds. Values may remain empty until the device wakes and reports successfully.

### My door/motion/water/smoke/radar sensor was detected but no entity appeared

Binary sensor support is intentionally conservative. The integration currently requires a recognizable model name plus an expected DPID mapping, so unsupported PIDs may be classified but not yet exposed as entities. Share the PID, model name, and DPID list in an issue to add support safely.

### Can I control RGB color on my lights?

If your device is classified as a light and has DPIDs `5` (Hue) and `6` (Saturation), RGB color control should be automatically enabled. Check the discovery log for both DPIDs:
```
DPIDs=['1', '2', '3', '4', '5', '6']
```


## Contributing

Contributions are welcome! If you'd like to improve this integration:

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test with your devices
5. Submit a pull request

When reporting issues, always include:
- Your device's log output (DID, PID, DPIDs)
- Home Assistant version
- Integration version
- Description of the issue


## Credits

This integration builds upon knowledge from the original CozyLife integrations and community contributions. Special thanks to all users who test and provide feedback!

---
## Support the Project

This project is developed and maintained in spare time and is provided free to the community.

If you find it useful and would like to support ongoing development, maintenance, and improvements, any contribution is appreciated — but never required ❤️

### Ways to Support

* **Ko-fi**
  [https://ko-fi.com/soulripper13](https://ko-fi.com/soulripper13)

* **PayPal**
  [https://paypal.me/SKatoaroo](https://paypal.me/SKatoaroo)

* **Bitcoin (BTC)**
  `bc1qvu8a9gdy3dcxa94jge7d3rd7claapsydjsjxn0`

* **Solana (SOL)**
  `4jvCR2YFQLqguoyz9qAMPzVbaEcDsG5nzRHFG8SeaeBK`

You can also help by:

* Reporting bugs
* Submitting pull requests
* Suggesting features
* Helping other users
* Starring the repository ⭐

Thank you for being part of the community.
