# Serial2RUDICS
For TWR Slocum gliders which is connected via a serial port, change to connecting via a RUDICS listener on the dockserver

I run this as a service on a Raspberry Pi 3B running Raspberry Pi OS (Debian Trixie) for multiple serial ports. See `USBToRUDICS@.service` for the service template and `install.py` for automated installation.

We connect pocket simulators, shoebox simulators, and Slocum gliders to the serial ports.

All the output of the simulators is logged.

Initially all output is sent to the dockserver via a RUDICS style connection. The RUDICS connection is dropped after the first dive. Then reestablished upon surfacing. 
The pocket/shoebox/glider is now only connected while on the surface, similar to a real glider.

## Dependencies

- Python 3.13+
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

## Notes

This is a Python 3 program. It has been tested on Raspberry Pi OS (Debian Trixie) running Python 3.13.

The only non-standard Python module you might have to install is pyserial.
