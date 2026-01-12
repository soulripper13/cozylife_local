# Home Assistant CozyLife Local Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/soulripper13/cozylife_local)

This is a custom integration for Home Assistant to control CozyLife smart devices. It communicates with devices **100% locally**, removing any dependency on cloud services for operation.

This integration was developed to provide a modern, robust, and easy-to-use alternative to older CozyLife integrations, with a focus on local control and a smooth user experience.

---

## ⚠️ Beta / Testing Phase

**This is a new integration and should be considered in a beta testing phase.**

While it has been tested and confirmed to work with multi-gang switches, we are actively looking for testers to help verify its functionality with other CozyLife devices, such as:

-   Single switches
-   Smart plugs
-   Lights (tunable white and RGB)
-   Other CozyLife devices

If you have one of these devices, please install the integration and report your experience! Your feedback is crucial for improving device compatibility.

**When reporting issues or sharing feedback:**
- Check your Home Assistant logs (see [Device Information & Logging](#device-information--logging) section)
- Copy the device discovery information (DID, PID, DPIDs)
- Share whether your device works correctly or not

Please [open an issue](https://github.com/soulripper13/cozylife_local/issues) with this information to help improve device compatibility.

---

## Features

- **100% Local Control:** No cloud connection is required for device operation after setup. All commands are sent directly to your devices on your local network.
- **UI-Based Configuration:** No YAML configuration required.
- **Single IP Setup:** Devices are added one by one using their static IP address.
- **Multi-Gang Switch Support:** Correctly handles multi-button devices (e.g., double rocker switches), creating a separate entity for each switch.
- **Comprehensive Device Logging:** Automatically logs detailed device information (DID, PID, DPIDs, capabilities) to help you understand and troubleshoot your devices.
- **Automatic DPID Detection:** Smart detection of device capabilities including brightness, color, and color temperature.
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
    -   Enter the **single, static IP address** of your CozyLife device. The integration will connect and set it up.
    -   **Developer Method:** To add a device remotely without an active connection (e.g., for development), enter its single, static IP address and check the "Skip validation" box.

## Device Information & Logging

This integration provides **comprehensive logging** to help you understand your device's capabilities and troubleshoot issues. When you add a device, detailed information is automatically logged to Home Assistant's logs.

### Viewing Your Device Information

After adding a device, check your Home Assistant logs to see detailed device information:

1. **Via UI:** Go to `Settings` → `System` → `Logs`
2. **Via configuration.yaml:** Add this to see CozyLife logs:
   ```yaml
   logger:
     default: info
     logs:
       custom_components.cozylife_local: warning
   ```

### What You'll See in the Logs

When you add a device, the logs will show:

```
╔══════════════════════════════════════════════════════════════════
║ Device Discovery Successful!
║
║ Device Model: LED Strip
║ Device ID (DID): 12345678
║ Product ID (PID): d50v0i
║ Device Type Code: 01
║ IP Address: 192.168.1.177
║
║ Supported DPIDs: ['1', '3', '5']
║
║ Device Category: Light
║
║ DPID Capabilities Detected:
║   - DPID 1: Power Switch
║   - DPID 3: Color Temperature OR Brightness (device dependent)
║   - DPID 5: Hue (Color)
╚══════════════════════════════════════════════════════════════════

💡 Setting up LIGHT entity for LED Strip
   ├─ Analyzing light capabilities...
   ├─ DPIDs: ['1', '3', '5']
   ├─ ✓ Brightness: DPID 3
   └─ Supported modes: brightness

✅ CozyLife device setup complete: LED Strip
```

### Understanding DPIDs

DPIDs (Data Point IDs) are the functions your device supports. Common DPIDs include:

| DPID | Function | Description |
|------|----------|-------------|
| `1` | Power Switch | Turn device on/off |
| `2` | Work Mode | Switch between color/white modes |
| `3` | Color Temperature **OR** Brightness | Depends on device model |
| `4` | Brightness | Standard brightness control |
| `5` | Hue | RGB color (Hue component) |
| `6` | Saturation | RGB color (Saturation component) |
| `7` | Color | Alternative color control |
| `8` | Scene | Scene/effect mode |

**Important:** Some devices (especially LED strips with PID `d50v0i`) use DPID `3` for brightness instead of color temperature. The integration automatically detects this.

### Troubleshooting with Logs

If your device isn't working as expected:

1. **Check the device discovery log** to see which DPIDs were detected
2. **Look for warning messages** like:
   - `⚠️  ON/OFF only - no dimming/color capabilities detected` - Device appears as simple switch
   - `⚠️  No switch entities created` - Expected switch DPIDs not found

3. **Share your logs** when reporting issues on GitHub:
   - Copy the device discovery box from your logs
   - Include the DPID list and detected capabilities
   - This helps developers add support for your specific device model

### Example: LED Strip Not Dimming

If your LED strip shows up as on/off only:

1. Check logs for: `DPID Capabilities Detected`
2. Look for DPIDs `3`, `4`, or `5` in the list
3. If present but not detected as brightness, [open an issue](https://github.com/soulripper13/cozylife_local/issues) with your device's PID and DPID list

## Supported Devices

This integration has been tested with and is known to work with:
-   Multi-gang switches (e.g., double rocker switches)
-   Single switches
-   Lights (including tunable white and RGB)

It is expected to work with a wide range of CozyLife devices that use the local TCP protocol.

---

## Common Issues & FAQ

### My LED strip only shows on/off, no brightness control

**Solution:** This was a known issue with devices that use DPID `3` for brightness (like PID: `d50v0i`). Update to the latest version of the integration which includes automatic detection of alternative DPID mappings.

Check your logs for:
```
✓ Brightness: DPID 3
Supported modes: brightness
```

If you still see `ON/OFF only`, please [open an issue](https://github.com/soulripper13/cozylife_local/issues) with your device's log output.

### How do I find my device's IP address?

Check your router's DHCP client list or use a network scanner app. Once found, set a static IP or DHCP reservation for the device to prevent the IP from changing.

### Device not connecting or timing out

1. Verify the device is on the same network as Home Assistant
2. Check the IP address is correct
3. Ensure no firewall is blocking port 5555 (CozyLife protocol)
4. Try pinging the device from Home Assistant's terminal
5. Check Home Assistant logs for detailed error messages

### Where can I find my device's DID and PID?

After adding a device, check `Settings` → `System` → `Logs`. Look for the device discovery box which contains all device information including DID, PID, and DPIDs.

### My switch shows fewer gangs than it actually has

The integration currently defaults to 2-gang switches. If you have 1-gang or 3+ gang switches, please [open an issue](https://github.com/soulripper13/cozylife_local/issues) with your device information from the logs so we can add support.

### Can I control RGB color on my lights?

If your device has DPIDs `5` (Hue) and `6` (Saturation), RGB color control should be automatically enabled. Check the logs for:
```
✓ RGB Color: DPIDs 5 (Hue) + 6 (Saturation)
```

---

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

---

## Credits

This integration builds upon knowledge from the original CozyLife integrations and community contributions. Special thanks to all users who test and provide feedback!

---

## License

This project is licensed under the MIT License - see the LICENSE file for details.

---