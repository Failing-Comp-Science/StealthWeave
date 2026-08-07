SECURE STEGANOGRAPHY DEMO PAYLOAD
=================================

WHAT THIS FOLDER CONTAINS
-------------------------
  payload.html  - The payload file to hide inside images/audio/video.
  payload.jpg   - IDENTICAL content, but named with a .jpg extension on
                  purpose. This shows how attackers disguise a hidden
                  payload to look like a harmless image.

WHAT THE PAYLOAD DOES
---------------------
When the file is opened, it opens a single browser tab showing:

    "THIS IS HOW HACKERS GET YOU. STAY SAFE."

It is a plain web page. That is ALL it does. There is:
  - NO code execution on the computer or phone
  - NO file deletion, modification, network access or data collection
  - NO self-replication / spread
  - NO requests to any server (fully offline)

SAFETY
------
This payload cannot harm a system. It is ordinary text (HTML) shown in a
browser. Opening it on Windows, macOS, Linux, Android or iPhone is safe.

HOW TO USE (with the existing Harpocrates tool)
-----------------------------------------------
1. Encode: embed payload.html into your cover image/audio file.
2. Extract: run the decoder/extractor to pull the hidden bytes back out
   and save them as payload.html.
3. Click the extracted file:
     - Computer: double-click it, it opens a browser tab -> message fires.
     - Phone: tap the file, choose "open in browser", message shows.
4. payload.jpg is for demonstrating extension spoofing: rename the
   extracted bytes to payload.jpg and show how humans get fooled
   (photo viewers may just show an error - that is part of the demo:
   browsers render the real content).

IMPORTANT HONESTY NOTE FOR YOUR TEACHER
---------------------------------------
Media files (images/videos) DO NOT run code when viewed. A real attacker's
trick is that the hidden bytes they plant are a FILE (HTML, script, link,
zip) that a victim is tricked into opening or running. That is what this
demo faithfully shows: hidden HTML that fires once you open it - the exact
thing the message warns about - made completely safe so it can be shown in
class.

Suggested demo script (2 minutes)
---------------------------------
1. Extract payload.html from a stego image with your tool.
2. Double-click it -> browser tab + alert appear.
3. Open payload.jpg with a browser (or try to open it as an image) to show
   how attackers fool people with fake file types.
4. Walk through the phish: the page only displays the message and closes;
   no damage, no spread - just the lesson.