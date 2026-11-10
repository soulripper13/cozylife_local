# Local connection recovery

## APK evidence

The decompiled CozyLife app provides the following relevant call paths under
`com/sibodoit/net/lan/tcp`:

- `TcpSocketManage._init_$lambda$1` releases the socket pool when Android's
  network type changes or the network becomes unavailable.
- `TcpSocketManage.releaseConnect` closes the socket, clears its buffer, and
  removes the cached entry. `getSocket` creates a replacement for a missing or
  closed entry.
- `deamon/abs/BaseTcpWriteClient.handleMessage` writes and flushes asynchronously,
  then reports send success. On an I/O exception it releases the cached socket;
  its limited retry after a network change is separate from receipt of state.

These paths support invalidating a stale cached connection. They do not prove
that a successful write means a device returned state, nor do they specify the
30-second Home Assistant recovery threshold below. The APK's network-change
callback is not available to this Python transport.

## Home Assistant handling

An unanswered powered-device properties request starts a monotonic 30-second
deadline. Repeated writes do not move the deadline. A successful nonempty state
payload clears it, including partial, zero-valued, or uncorrelated reports.
Empty replies and error responses do not clear it.

At the next local operation after expiry, the API closes the old connection and
opens another. A failed connection attempt leaves the next background check free
to retry. No synchronous response wait is added to properties requests or
controls, and control commands are not replayed. UDP presence is not an
availability requirement. Sleeping-sensor status requests do not start this
deadline or change their polling, framing, or TCP timeout behavior.

## Regression evidence and limits

`tests/test_network_recovery.py` uses local TCP peers that keep the old session
open and accept writes while withholding usable state. Before the repair, the
next properties query continues using that session; after the repair it opens a
new session and receives state. Cases cover silence, empty/error replies, an
initially refused reconnect followed by recovery, partial zero-valued reports,
and sleeping-sensor isolation. The existing transport tests cover CRLF plugs,
asynchronous replies, control responsiveness, EOF recovery, and lifecycle cleanup.

This reproduces the stale-session failure mode without changing a live network.
It does not establish the exact cause on the device reported in issue #97 or
replace validation on that physical device.
