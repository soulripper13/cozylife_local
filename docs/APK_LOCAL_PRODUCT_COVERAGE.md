# APK local product coverage

This integration uses the bundled CozyLife APK catalogue (`model.json`) as the
product inventory. A catalogue entry is eligible for local support only when
the APK marks it as Wi-Fi (`net: "02"`) and there is evidence for the DPID
meaning and a safe Home Assistant entity mapping.

New discovery entries require the Wi-Fi marker. Existing entries retain the
legacy classifier for compatibility with older or incomplete catalogue data.

For the retained door-contact panel, DPID 7 reports open/closed and
DPID 9 reports charge in tenths of a percent. These contact profiles
expose a battery diagnostic when the device advertises DPID 9.

## Adding products

Add new products through a reviewed pull request after verifying them
against a newer CozyLife APK. Confirm the product ID, Wi-Fi transport,
and the panel's DPID read and write behavior for the proposed entities.

## Implemented local families

| APK family | Local entity coverage | Evidence |
| --- | --- | --- |
| Electrical | Relay/outlet switching, metering, and documented plug options | APK catalogue and `electrician_en.md` |
| Light | On/off, brightness, colour, colour temperature, countdown, and power-off-memory configuration when advertised | APK light transport and `light_en.md` |
| Motor | Native fan power, documented percentage/gear speeds, and wind modes | APK catalogue and `motor_en.md` |
| Sensor | Temperature, humidity, battery, contact, water leak, and the Wi-Fi multi-sensor's motion/contact profile | APK catalogue and `sensor_en.md` |
| Home appliance | Documented DPID-1 `switch_power` as a generic switch | APK catalogue and `appliance_en.md` |
| Energy storage | Battery status, battery level/temperature, total and documented branch input/output power, remaining times, fault code, and documented branch-output switches when advertised | APK catalogue and `battery_en.md` |
| Gateway | Documented discovery-mode configuration switch | APK catalogue and `gateway_en.md` |
| Video intercom/doorbell | Reported active-call, battery, low-battery, and motion state when the documented video profile is advertised | CozyLife video project and `doorbell_en.md` |
| Smart speaker | X7 power, volume, play/pause, and Bluetooth/local/line-in source selection | Downloaded CozyLife X7 panel |
| AI lamp | On/off and brightness for the two Wi-Fi AI-lamp profiles | Downloaded CozyLife AI-lamp panel |
| AI companion | S3 expression selection (happy, sad, disappointed, angry, surprised) | Downloaded CozyLife S3 panel |

## Deliberately not guessed

- A Wi-Fi entry with no usable DPID cannot provide a reliable entity merely
  because it appears in the APK catalogue.
- DIY/passthrough products advertise DPID 51 as an opaque JSON configuration
  payload. There is no schema in the APK or published DPID documentation, so
  the integration must not expose an unsafe free-form control.
- Lock-only products commonly advertise only DPID 101. DPID 101 is documented
  as a call-state event for the video protocol, not as an unlock command.
  Lock actuation and video streaming stay out of scope until their local
  encrypted/session protocol is independently verified.
- AI conversation products, other than the two mapped AI-lamp profiles,
  remain in the evidence-gathering queue. Their
  201--203 DPID controls are not mapped in the APK resources or CozyLife's
  published DPID documentation yet. Once recovered from their panel source,
  the integration will add only entities that match verified local behavior.

## Product-audit boundary

The bundled catalogue is refreshed separately from integration releases, so
this document intentionally does not publish fixed profile counts. A profile
is supported only where the current catalogue, APK/panel evidence, and local
entity mapping agree. Catalogue refreshes do not automatically add entities.

The current evidence boundary is:

- Electrical, lights, motors, home appliances, sensors, energy storage,
  gateways, verified video-state profiles, X7 speakers, the two AI-lamp
  profiles, and the S3 expression profile have the mappings described above.
- DIY/passthrough DPID 51 payloads, lock actuation, video streaming, and
  untraced AI conversation controls remain deliberately unsupported.
- A catalogue entry that is marked Wi-Fi is still not enough to expose an
  entity: its DPID meaning and local behavior must be independently verified.

## Powered-metering compatibility invariant

The APK establishes `cmd:2, attr:[0]` as the powered-product state request,
but a successful response may be partial.  A transport change is incomplete
unless it preserves all of the following:

- Keep previously received DPID state when a later listener report is partial.
- Build every known product's capability inventory by merging observed/cached
  DPIDs with its verified APK catalogue profile. This happens at device
  inventory time, before platform setup, so old config entries and new
  discovery entries expose the same supported entity set.
- For a supported plug, request only the catalogue-advertised metering and
  option DPIDs omitted from the `attr:[0]` report. Do not treat a successful
  `res:0` as proof that every advertised DPID was returned.
- Retain the complete fallback policy for currently exposed powered entities:
  power-on state, LED status, overcurrent protection, Current, timer status,
  Voltage, and light countdown.
- Expose DPID 26 (**Energy**) independently from DPID 42 (**Total energy**)
  when both are advertised; neither entity may suppress the other.
- Before release, compare the powered-device DPID and fallback path against
  the last known working implementation (1.0.16), and test a partial response
  that omits DPID 27 (Current) and DPID 29 (Voltage).

This is an evidence-preservation rule, not a product-specific workaround: the
fallback is limited to DPIDs already advertised by the local product catalogue.

### 1.0.16 powered-path preservation audit

| Retained entity family | Targeted fallback DPIDs |
| --- | --- |
| Switch configuration | 18 power-on state, 19 LED status, 32 overcurrent protection |
| Plug telemetry/options | 27 Current, 29 Voltage, 72 timer status |
| Light configuration | 13 countdown |

The CozyLife app uses DPID 3 for its native timer records on compatible plugs.
CozyLife Local intentionally leaves those records untouched; Home Assistant
automations, Schedule helpers, and Timer helpers are the supported scheduling
path.

## Transport rule

The APK uses `cmd:2, attr:[1]` for sleeping-sensor status checks and
`cmd:2, attr:[0]` for a powered product's complete property state. The
integration preserves that split: high-frequency waking-sensor probes never
become a polling scheme for powered products.
