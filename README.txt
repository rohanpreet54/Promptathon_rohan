============================================================
 NSRH HUB — Distributed Object Storage
 Hackathon demo — local, single-computer edition
============================================================

WHAT THIS IS
------------
NSRH HUB is a demonstration of a distributed object storage
system that runs entirely on ONE computer.

It has two layers, and it is important to understand the
difference between them:

  1. REAL LOCAL BACKEND  (the actual storage layer)
     - server.py is a small Python web server.
     - It ACTUALLY saves every file you upload to your hard
       drive, inside the "nsrh_storage" folder.
     - It ACTUALLY calculates a SHA-256 checksum of every file
       itself (it never just trusts the browser's checksum).
     - It ACTUALLY reads the real file back off disk when you
       click Download.
     - This is your real, persistent, single source of truth.

  2. SIMULATED DISTRIBUTED NODES  (the demo layer)
     - The dashboard shows four storage nodes: Alpha, Beta,
       Gamma and Delta, each with about 2 GB of simulated
       capacity.
     - Replication, node failure, node repair, corruption
       ("bit-rot") and self-healing all happen inside your
       browser's IndexedDB — this is what lets the demo show
       a multi-node cluster using only one physical machine.
     - Failing or "corrupting" a simulated node NEVER touches
       or deletes the real file saved by the Python backend.

In short: server.py + nsrh_storage/ = the real storage engine.
The four nodes on the dashboard = the simulated distributed
layer built on top of it for the demo.

------------------------------------------------------------
HOW TO RUN IT (Windows)
------------------------------------------------------------

1. Put these four files in the SAME folder:
       index.html
       server.py
       start_nsrh.bat
       README.txt   (this file)

2. Double-click start_nsrh.bat

3. A console window will open and start the local server.
   Your default browser should open automatically to:
       http://127.0.0.1:8000

   (If it doesn't open automatically, just open that address
   in your browser manually.)

4. In the dashboard, check the "Local backend connected"
   status pill near the top right — it should be green. If it
   says "Backend offline", make sure the console window from
   start_nsrh.bat is still open and running.

5. Click "Upload Object" and choose any file. Watch the
   progress: the browser reads the file, computes a SHA-256
   checksum, sends the real bytes to the Python backend, and
   the backend independently re-checks the checksum before
   saving it to disk.

6. The real uploaded file will now exist on your computer at:
       nsrh_storage\files\<some-id>.<extension>

   and its metadata (name, size, checksum, etc.) is recorded in:
       nsrh_storage\metadata.json

7. Use the dashboard to demonstrate the rest of the system:
   - Download an object (the backend streams the real file
     back, and the browser re-verifies its SHA-256 before
     letting the download through).
   - Rename or delete an object (both go through the real
     backend, not just the browser).
   - Click "Simulate Failure" on a node to see automatic
     re-replication onto the remaining healthy nodes.
   - Click "Repair Node" to bring a failed node back online.
   - Select an object, then click "Corrupt" on one of its
     replica nodes to simulate silent data corruption
     ("bit-rot"), then click "Verify" to detect it and
     "Repair" to heal it from a healthy replica.
   - Adjust the Replication Factor (1–4 copies) in Settings.

8. Your uploaded files and metadata.json will still be there
   the next time you run start_nsrh.bat — even after closing
   the browser, restarting the server, or restarting your
   computer. The simulated node/replica state (in IndexedDB)
   also persists per-browser unless you click "Reset Cluster
   Data" in Settings.

------------------------------------------------------------
REQUIREMENTS
------------------------------------------------------------
- Windows, with Python 3.13 (or any modern Python 3) installed
  and added to PATH. Get it from https://www.python.org/downloads/
  (check "Add python.exe to PATH" during install).
- No pip installs. No npm. No virtual environment. No database
  to install. server.py uses ONLY Python's standard library.
- An internet connection is used once, just to load React and
  fonts from public CDNs inside index.html — the actual file
  storage, uploads, downloads and API all work fully offline
  on 127.0.0.1.

------------------------------------------------------------
TROUBLESHOOTING
------------------------------------------------------------
"Python was not found"
    Install Python 3 from python.org and make sure "Add
    python.exe to PATH" was checked during setup.

"Backend offline" in the dashboard
    Make sure the start_nsrh.bat console window is still open.
    If you closed it, just double-click start_nsrh.bat again.

"Another program may already be using that port"
    Something else on your computer is already using port 8000.
    Close that program, or open server.py in a text editor and
    change the PORT value near the top, then restart.

Uploads are rejected with a checksum error
    This means the bytes that arrived at the backend didn't
    match what the browser sent (e.g. the connection was
    interrupted). The file is NOT saved when this happens —
    just try the upload again.

------------------------------------------------------------
FILES IN THIS PROJECT
------------------------------------------------------------
index.html        The NSRH HUB dashboard (frontend). Served
                   directly by server.py.
server.py         The real local backend: HTTP server, file
                   storage, SHA-256 verification, metadata.
start_nsrh.bat     Double-click this to launch everything.
README.txt         This file.

nsrh_storage/       Created automatically the first time you
                     run the server.
  files/             The real uploaded files, on disk.
  metadata.json      Metadata about every stored file.
