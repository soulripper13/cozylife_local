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

If you have one of these devices, please install the integration and report your experience! Your feedback is crucial for improving device compatibility. Please [open an issue](https://github.com/soulripper13/cozylife_local/issues) to share your device's discovered DPIDs, report any bugs, or confirm success.

---

## Features

- **100% Local Control:** No cloud connection is required for device operation after setup. All commands are sent directly to your devices on your local network.
- **UI-Based Configuration:** No YAML configuration required.
- **Single IP Setup:** Devices are added one by one using their static IP address.
- **Multi-Gang Switch Support:** Correctly handles multi-button devices (e.g., double rocker switches), creating a separate entity for each switch.
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

## Supported Devices

This integration has been tested with and is known to work with:
-   Multi-gang switches (e.g., double rocker switches)
-   Single switches
-   Lights (including tunable white and RGB)

It is expected to work with a wide range of CozyLife devices that use the local TCP protocol.

---

*This integration was developed with the assistance of an AI software engineering agent.*
