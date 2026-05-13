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
