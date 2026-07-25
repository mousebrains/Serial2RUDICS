# Serial2RUDICS
For TWR Slocum gliders which is connected via a serial port, change to connecting via a RUDICS listener on the dockserver

I run this as a service on a Raspberry Pi 3B running Raspberry Pi OS (Debian Trixie) for multiple serial ports. See `USBToRUDICS@.service` for the service template and `install.py` for automated installation.

We connect pocket simulators, shoebox simulators, and Slocum gliders to the serial ports.

All the output of the simulators is logged.

Initially all output is sent to the dockserver via a RUDICS style connection. The RUDICS connection is dropped after the first dive. Then reestablished upon surfacing. 
The pocket/shoebox/glider is now only connected while on the surface, similar to a real glider.

## Dependencies

- Python 3.11+ (Debian Bookworm ships 3.11; Trixie ships 3.13). CI tests 3.11, 3.12, and 3.13.
- [pyserial](https://pypi.org/project/pyserial/) (`pip install pyserial` or `pip install -r requirements.txt`)

## Installation

The service defaults to running as the current user with logs written to `~/logs/`. **Run `install.py` as your normal user (not via `sudo`)** — it invokes `sudo` internally for the privileged steps. Running the script itself under `sudo` would cause the service to run as root with logs in `/root/logs/`.

1. Add your user to the `dialout` group (for serial port access):
   ```
   sudo usermod -aG dialout $USER
   ```

2. Install the systemd service template and create the log directory:
   ```
   python3 install.py --hostname <dockserver> --port 6565
   ```

3. Install the udev rule to auto-start on USB-serial plug-in:
   ```
   sudo cp 99-ttyusb.rules /etc/udev/rules.d/
   sudo udevadm control --reload
   ```

If the dockserver is not directly reachable, see
[Tunneling through an ssh proxy host](#tunneling-through-an-ssh-proxy-host).

## Tunneling through an ssh proxy host

When the dockserver is only reachable via an intermediate host you can ssh to,
`install.py --tunnelProxy` installs `rudics-tunnel.service`: a single
`ssh -L 127.0.0.1:16565:<dockserver>:6565` forward shared by every port
instance, and points the port services at the local end of it. No change to
`serial2RUDICS.py` — each connection it makes to the local port becomes its own
ssh channel to the dockserver, so the per-surfacing connect/disconnect cycle
behaves exactly as it does on a direct connection.

```
python3 install.py --hostname <dockserver> --port 6565 \
    --tunnelProxy <user>@<proxyhost>
```

`--hostname`/`--port` still mean the dockserver. The generated units become
`--host=127.0.0.1 --port=16565` for the port services, with the real endpoint
carried inside the tunnel. Change the local port with `--tunnelLocalPort`.
The port instances get `Wants=`/`After=rudics-tunnel.service` — deliberately
not `Requires=`, so a tunnel restart does not tear down all ten port services;
they get ECONNREFUSED on the local forward and retry.

### One-time setup on the proxy host

The service runs `ssh` with `BatchMode=yes` and `StrictHostKeyChecking=yes`, so
the key must be passphrase-less and the host key already known — it cannot
prompt, and `ProtectSystem=strict` keeps `~/.ssh` read-only:

```
ssh-keygen -t ed25519 -N '' -f ~/.ssh/id_rudics       # override with --tunnelKey
ssh-keyscan -H <proxyhost> >> ~/.ssh/known_hosts
ssh-copy-id -i ~/.ssh/id_rudics <user>@<proxyhost>
```

Then restrict that key on the proxy host so it can open nothing but this one
forward (OpenSSH 7.2+), in the proxy's `~/.ssh/authorized_keys`:

```
restrict,port-forwarding,permitopen="<dockserver>:6565" ssh-ed25519 AAAA... rudics-tunnel
```

`install.py` only enables the tunnel unit — nothing starts it, since udev has
no reason to. Start it once the key is in place:

```
sudo systemctl start rudics-tunnel.service
ss -lnt | grep 16565                                  # forward is listening
journalctl -u rudics-tunnel.service -f
```

### Liveness, and the one case the tunnel hides

Direct-connect, `SO_KEEPALIVE` probes the dockserver end to end and catches a
half-open connection in ~150s. Through a tunnel the socket terminates at the
local ssh client, so that only proves ssh is alive on this host. The failure
cases split:

| Failure | Detected by | Latency |
| --- | --- | --- |
| Path to the proxy host dies | `ServerAliveInterval=30` × `ServerAliveCountMax=3`; ssh exits, channels close, EOF to the app | ~90s |
| Dockserver process dies | FIN/RST closes the ssh channel, EOF to the app | immediate |
| Silent partition between proxy and dockserver | nothing — the channel stays open | `--idleTimeout` (3600s) |

Only the third case is a regression against direct connection. Lowering
`--timeout` (which sets `--idleTimeout`) to ~900 for tunneled installs bounds
it; the cost is a redial after 15 quiet minutes, which the existing reconnect
logic already handles. Note that the per-port logs will read
`Connected to 127.0.0.1:16565` rather than naming the dockserver.

## Usage

serial2RUDICS.py --host=localhost --port=6565 --serial=/dev/ttyUSB0

To see all the command line options use:

serial2RUDICS.py --help

## Operation

Each USB serial device gets its own systemd template instance (e.g. `USBToRUDICS@ttyUSB0.service`). The udev rule starts/stops them as USB-serial devices are plugged in.

### Viewing logs

```
journalctl -u USBToRUDICS@ttyUSB0.service -f       # live log via syslog
tail -f ~/logs/ttyUSB0.log                         # per-port rotating log
sudo systemctl status 'USBToRUDICS@ttyUSB*'        # all instances
```

### Notable flags

| Flag | Default | Purpose |
| --- | --- | --- |
| `--host`, `--port` | — / 6565 | Dockserver RUDICS endpoint |
| `--serial` | — | Serial device, e.g. `/dev/ttyUSB0` |
| `--baudrate` | 115200 | Serial baudrate |
| `--idleTimeout` | 3600 | Drop RUDICS after this many idle seconds |
| `--rudicsBaudrate` | (unset) | Optional outbound rate limit toward RUDICS |
| `--rudicsMaxOpenTime` | 86400 | Force a reconnect after this many seconds |
| `--triggerOn`, `--triggerOff` | (defaults) | Regex patterns over serial output that open/close the RUDICS session |
| `--maxBuffer` | 10485760 | Bytes buffered while waiting for RUDICS; lower for tight-RAM hosts |
| `--binary` | (unset) | Dump every chunk in both directions to this file (heavy SD I/O) |
| `--disconnected` | False | Start with RUDICS disconnected, wait for `triggerOn` |

### Trigger / suppression behavior

Default triggers open RUDICS on `surface_<n>: ... Picking iridium or freewave` or `abort_the_mission`, and close it on `surface_<n>: ... Waiting for final gps fix`. Custom patterns are appendable via repeated `--triggerOn`/`--triggerOff`.

Trigger matching is suppressed during file transfers to avoid spurious closes:
- Lines that contain non-text bytes (zmodem framing)
- After a `type` or `cat` command at a `GliderDos` prompt, until `LOG FILE CLOSED` or the next `GliderDos` prompt
- Within 30 seconds of any binary data on the wire (active zmodem session)

## Notes

This is a Python 3 program. It has been tested on Raspberry Pi OS (Debian Trixie) running Python 3.13, and CI runs the test suite on Python 3.11, 3.12, and 3.13.

The only non-standard Python module you might have to install is pyserial.
