# Fakeage - a Local area network (e.g. WiFi) Fibbage-like lying game

---------------------------------
How to play:

Add your questions (tab separated questions and answers) to questions.tsv.
Launch fakeage_server.exe, direct browsers to http://[my.local.ip]:8000/
Have one browser tab as the 'viewer' on the tv/projector/main display. Click the >NEXT button on the footer bar to advance through lying-lie selection-scoring rounds. 

All input from players will be butchered to remove all non [A-Z] [0-9] characters to preserve the developer's sanity, and converted to UPPERCASE.
Supports players rejoining with the same names, supports liking of submissions. 
1 point for getting the correct answer, 1 point for fooling others. 


--------------------------------------------

To build or dev:

Two main components are fakeage_server.py, which contains the server code, and index.html, which contains the viewer/player javascript stuff. 

Reel in horror at the spaghetti code in the python side, get further enraged by the code on the browser side. 

Requires a few python2 libs, e.g. the imports from fakeage_server.py:

import pyqrcode
from unidecode import unidecode 

To run a server, launch fakeage_server.py, or fakeage_server.exe if you do not wish to get the libraries for python

Standalone exe built with pyinstaller --onefile fakeage_server.py

Have fun!

------------------------------------

![game looks](https://raw.githubusercontent.com/Beherith/fakeage/master/screenshot.PNG)
