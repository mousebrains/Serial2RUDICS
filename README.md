# Serial2RUDICS
For TWR Slocum gliders which is connected via a serial port, change to connecting via a RUDICS listener on the dockserver

I run this as a service on our SFMC/dockserver server for two serial ports. See comments in USB2ToRUDICS.service for how to install the service on a CentOS 7 system.

We have a pocket and shoebox simulators connected to the serial ports.

All the output of the simulators is logged.

I run this program on a backup SFMC server, where one needs to modify 
/var/opt/sfmc-dockserver/dockServerState.xml.
To deallocate a port from SFMC so this program can use it, you will need to edit 
/var/opt/sfmc-dockserver/dockServerState.xml. 
Comment out the port line for the port you want to use and the gliderLink line for that port. Then restart the dockserver from within SFMC.

Initially all output is sent to the dockserver via a RUDICS style connection. The RUDICS connection is dropped after the first dive. Then reestablished upon surfacing. A pocket/shoebox simulator now is only connected while on the surface, simalar to a real glider.

Usage:

serial2RUDICS --host=localhost --serial=/dev/ttyUSB0
