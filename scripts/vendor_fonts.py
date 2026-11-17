"""One-off font vendoring: download OFL woff2 latin subsets from Google Fonts
into assets/fonts/, and produce a TTF of Space Grotesk 700 for ogimg.py.
The vendored files are committed; sitegen.py copies the woff2 files into site/fonts/.
"""
import re
import urllib.request
from pathlib import Path

OUT = Path("assets/fonts")
OUT.mkdir(parents=True, exist_ok=True)
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0 Safari/537.36")

CSS_URL = ("https://fonts.googleapis.com/css2"
           "?family=Space+Grotesk:wght@500;700"
           "&family=Inter:wght@400;500;600"
           "&family=JetBrains+Mono:wght@400;600&display=swap")

req = urllib.request.Request(CSS_URL, headers={"User-Agent": UA})
css = urllib.request.urlopen(req, timeout=60).read().decode()

# Blocks look like: /* latin */ @font-face { ... font-family:'X'; font-weight:N; src:url(...woff2) ...}
faces = []
for m in re.finditer(r"/\*\s*([a-z-]+)\s*\*/\s*@font-face\s*\{([^}]+)\}", css):
    subset, body = m.group(1), m.group(2)
    if subset != "latin":
        continue
    fam = re.search(r"font-family:\s*'([^']+)'", body).group(1)
    wght = re.search(r"font-weight:\s*(\d+)", body).group(1)
    url = re.search(r"src:\s*url\(([^)]+\.woff2)\)", body).group(1)
    faces.append((fam, wght, url))

print("latin faces found:", len(faces))
for fam, wght, url in faces:
    slug = fam.lower().replace(" ", "") + "-" + wght + ".woff2"
    dest = OUT / slug
    data = urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60).read()
    dest.write_bytes(data)
    print(f"  {slug:28s} {len(data):7,d} B")

print("done ->", OUT)
