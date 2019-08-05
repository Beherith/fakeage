# Fakeage - a Local area network (e.g. WiFi) Fibbage-like lying game



To build or dev:

Two main components are fakeage_server.py, which contains the server code, and index.html, which containst the viewer/player javascript stuff. 

The players connect to the server using their browsers via websockets.

-----------------------------------

Requires a few python2 libs, e.g. the imports from fakeage_server.py

Simple python websockets lying game

to run a server, launch fakeage_server.py, or fakeage_server.exe if you do not wish to get the libraries for python

standalone exe built with pyinstaller --onefile fakeage_server.py

Then navigate your brower to http://[my.local.ip]:8000/

Have fun!

------------------------------------

![game looks](https://raw.githubusercontent.com/Beherith/fakeage/master/screenshot.PNG)
