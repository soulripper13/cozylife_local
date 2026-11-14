# DreamColor whole-strip colour

## Product and APK trace

The APK-derived model catalogue maps PID `v3sjfa` (Smart colorful lights) to
the `DreamColor_deviceMic` panel `2021081318240040`. Its bundled version
`1.0.1.0` contains the following paths in `index.worker.js`:

- The light DPID table maps `dream_static_color` to 16, `work_mode` to 2,
  hue/saturation to 5/6, and brightness to 4 (around lines 1704–1721).
- `onColorChange` calls `sendLedControlMessage(true)` for whole-strip control
  (around line 13827).
- `sendLedControlMessage` serializes hue to four hexadecimal digits and
  saturation scaled by ten to four more. `getColorTemp` normally appends
  `ffff`. It sends one `controlFast` command with attributes `[1, 2, 16]`
  and data `{1: 1, 2: 0, 16: str}` (around lines 13955–13991 and 14082).
- `dream_static_color` decodes a 12-character whole-strip value using the first
  four digits for hue and the next four for saturation, dividing saturation by
  ten. Longer values have separate per-segment handling. Hue and saturation
  both equal to `ffff` enter a separate white/temperature path (around 14381).

The panel's final four digits are not a general brightness field: brightness
is DPID 4. Do not interpret `ffff` as a measured HSV brightness value.

Panel source SHA-256:
`3aa1b9d243deeed4968585005627d034d781383b2d3a44f92d128dcebfda462d`.

## Integration behavior and validation

Valid whole-strip reports use exactly 12 hexadecimal characters, hue 0–360,
and saturation 0–1000. These reports take precedence over DPID 5/6 and stale
colour-temperature data. The final four characters are validated as hexadecimal
but are not exposed as brightness. White/temperature sentinel values and segment
arrays are not decoded as whole-strip HS colour.

Colour commands selected by a valid whole-strip report send DPID 16 alongside
work mode 0 in one request. Ordinary lights keep their existing DPID 5/6
conversion. A catalogue advertisement or malformed DPID 16 value alone does
not switch colour encoders.

The contributor verified bidirectional colour control on a physical v3sjfa strip
with firmware 1.0.3. Tests cover report parsing, command composition, mode
selection, malformed/segment/sentinel fallback, and the ordinary light paths.
The APK trace corroborates the wire format; it does not establish that every
DreamColor firmware ignores DPID 5/6.
