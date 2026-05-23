"""
Convert Instagram post/reel URLs to profile URLs in google.csv.

Reads google.csv (the uploaded file), finds any URL of the form
  https://www.instagram.com/p/<id>/...     (post)
  https://www.instagram.com/reel/<id>/...  (reel)
  https://www.instagram.com/reels/<id>/... (reels)
  https://www.instagram.com/tv/<id>/...    (igtv)
and replaces it with the profile URL https://www.instagram.com/<username>/ .

The username is taken from the "VuuXrf" column of the same row, which Google
renders as "Instagram · <username>". Profile URLs are left untouched.

Output: google_profiles_only.csv next to this script.
"""

import csv
import re
import sys
from pathlib import Path

# --- locate input ---------------------------------------------------------
HERE = Path(__file__).resolve().parent
candidates = [
    HERE.parent / "uploads" / "google.csv",  # cowork session layout
    HERE / "google.csv",                      # script + csv side by side
    Path.cwd() / "google.csv",                # current working dir
]
src = next((p for p in candidates if p.exists()), None)
if src is None:
    print("Could not find google.csv. Place it next to this script and re-run.")
    sys.exit(1)

dst = HERE / "google_profiles_only.csv"

POST_RE = re.compile(
    r"^https?://(?:www\.)?instagram\.com/(?:p|reel|reels|tv)/[^/?#]+",
    re.IGNORECASE,
)
PROFILE_RE = re.compile(
    r"^https?://(?:www\.)?instagram\.com/([A-Za-z0-9_.]+)/?",
    re.IGNORECASE,
)
USERNAME_FROM_VUUXRF = re.compile(r"Instagram\s*[·•\-:|]\s*([A-Za-z0-9_.]+)")

URL_COLUMNS = {"zReHs href", "vzmbzf href", "rIRoqf href"}

def username_from_row(row, fieldnames):
    # Prefer VuuXrf, fall back to other text columns containing "Instagram · ..."
    for col in ("VuuXrf", "LC20lb", "VwiC3b", "VwiC3b 2", "VwiC3b 3"):
        if col in fieldnames:
            v = row.get(col) or ""
            m = USERNAME_FROM_VUUXRF.search(v)
            if m:
                return m.group(1)
    # Fallback: any profile URL already present in the row
    for col in URL_COLUMNS:
        if col in fieldnames:
            v = row.get(col) or ""
            if not POST_RE.match(v):
                m = PROFILE_RE.match(v)
                if m:
                    return m.group(1)
    return None

def fix_url(url, username):
    if not url:
        return url
    if POST_RE.match(url) and username:
        return f"https://www.instagram.com/{username}/"
    return url

with src.open("r", encoding="utf-8", newline="") as f:
    reader = csv.DictReader(f)
    rows = list(reader)
    fieldnames = reader.fieldnames or []

changed = 0
unresolved = 0
for row in rows:
    uname = username_from_row(row, fieldnames)
    for col in URL_COLUMNS:
        if col not in fieldnames:
            continue
        original = row.get(col) or ""
        if POST_RE.match(original):
            if uname:
                new = fix_url(original, uname)
                if new != original:
                    row[col] = new
                    changed += 1
            else:
                unresolved += 1

with dst.open("w", encoding="utf-8", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)

print(f"Read:        {src}")
print(f"Wrote:       {dst}")
print(f"URLs fixed:  {changed}")
print(f"Unresolved:  {unresolved}  (post/reel URLs where no username was found)")
