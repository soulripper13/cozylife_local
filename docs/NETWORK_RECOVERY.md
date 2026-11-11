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

## Comparison with 1.0.16

The 1.0.16 `_send_receive` opens a connection for every request and closes it in
`finally`. Its properties query uses the same command 2 and CRLF framing as
current powered products. A local TCP reference test using that exact historical
module returned state, handled a refused connection, and returned new state after
the server restarted. There is no retained session across those requests.

The current asynchronous reader is retained to accept uncorrelated and partial
reports without delaying controls. That introduces connection ownership absent
from the legacy request lifecycle: after reconnection, a delayed write failure
from a retired socket must not close the replacement or clear its waiters.
All three request paths now retire a failed writer only if it is still current.
This is a concurrency safeguard for Python's asynchronous implementation, not
an additional vendor protocol operation.

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

During setup, entries with incomplete cached identity data must contact the
device before platform creation. If that query fails, setup raises Home
Assistant's `ConfigEntryNotReady` so Home Assistant can retry after connectivity
returns. Previously it returned `False`, leaving a terminal setup failure with
no coordinator or registered device callback to recover it. The outer setup
failure boundary releases resources before the retry. This setup gap also exists
in the older setup approach; it is not evidence that every recovery failure is a
regression introduced by retained connections.

## Regression evidence and limits

`tests/test_network_recovery.py` uses local TCP peers that keep the old session
open and accept writes while withholding usable state. Before the repair, the
next properties query continues using that session; after the repair it opens a
new session and receives state. Cases cover silence, empty/error replies, an
initially refused reconnect followed by recovery, partial zero-valued reports,
and sleeping-sensor isolation. The existing transport tests cover CRLF plugs,
asynchronous replies, control responsiveness, EOF recovery, and lifecycle cleanup.

Additional regressions reproduce delayed old-writer failures in properties,
control, and identity requests; each previously closed the replacement socket.
Another reproduces terminal setup failure without a full identity cache and
checks successful platform setup on the subsequent attempt. A local TCP test
exercises the current coordinator through valid state, a silent session, refused
reconnection, restored connectivity, and fresh relay/voltage/current/power values.
The coordinator test uses lightweight Home Assistant interfaces; it does not run
a complete Home Assistant instance or claim to reproduce the reporter's network.

This reproduces the stale-session failure mode without changing a live network.
It does not establish the exact cause on the device reported in issue #97 or
replace validation on that physical device.
