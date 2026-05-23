"""
Streamlit app: paste any mix of Instagram URLs (posts, reels, IGTV, profiles,
stories) and get back the original profile URL for each one.

Run:
    streamlit run app.py
"""

import io
import re
import time
import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed

# ---------- URL parsing ---------------------------------------------------

URL_RE = re.compile(r"https?://[^\s,;'\"<>()\[\]]+", re.IGNORECASE)

PROFILE_RE = re.compile(
    r"^https?://(?:www\.)?instagram\.com/([A-Za-z0-9_.]+)/?(?:\?.*)?$",
    re.IGNORECASE,
)
POST_RE = re.compile(
    r"^https?://(?:www\.)?instagram\.com/(p|reel|reels|tv)/([^/?#]+)",
    re.IGNORECASE,
)
STORIES_RE = re.compile(
    r"^https?://(?:www\.)?instagram\.com/stories/([A-Za-z0-9_.]+)/",
    re.IGNORECASE,
)
USER_POST_RE = re.compile(
    r"^https?://(?:www\.)?instagram\.com/([A-Za-z0-9_.]+)/(?:p|reel|reels|tv)/",
    re.IGNORECASE,
)

RESERVED = {
    "p", "reel", "reels", "tv", "stories", "explore", "accounts",
    "directory", "about", "developer", "legal", "press", "web",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def extract_urls(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for u in URL_RE.findall(text or ""):
        u = u.rstrip(".,);]")
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def quick_username(url: str) -> str | None:
    """Return username without any network call, when the URL alone has it."""
    m = USER_POST_RE.match(url)
    if m and m.group(1).lower() not in RESERVED:
        return m.group(1)
    m = STORIES_RE.match(url)
    if m:
        return m.group(1)
    m = PROFILE_RE.match(url)
    if m and m.group(1).lower() not in RESERVED:
        return m.group(1)
    return None


def scrape_username(url: str, timeout: int = 10) -> str | None:
    """Fetch a post/reel page and parse the username from og:title."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
    except requests.RequestException:
        return None
    if r.status_code != 200 or not r.text:
        return None

    soup = BeautifulSoup(r.text, "html.parser")
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        # Typical form: "Username (@handle) on Instagram: ..."
        m = re.search(r"\(@([A-Za-z0-9_.]+)\)", og["content"])
        if m:
            return m.group(1)

    # Fallback: alternate handle pattern in raw HTML
    m = re.search(r'"username":"([A-Za-z0-9_.]+)"', r.text)
    if m:
        return m.group(1)
    return None


def resolve(url: str) -> dict:
    original = url.strip()
    kind = "unknown"
    if PROFILE_RE.match(original) and not POST_RE.match(original):
        kind = "profile"
    elif POST_RE.match(original):
        kind = "post"
    elif STORIES_RE.match(original):
        kind = "story"
    elif USER_POST_RE.match(original):
        kind = "user-post"

    uname = quick_username(original)
    if not uname and kind == "post":
        uname = scrape_username(original)

    return {
        "input_url": original,
        "type": kind,
        "username": uname or "",
        "profile_url": f"https://www.instagram.com/{uname}/" if uname else "",
        "status": "ok" if uname else "unresolved",
    }


# ---------- Streamlit UI --------------------------------------------------

st.set_page_config(page_title="Instagram Profile Extractor", page_icon="📸", layout="wide")
st.title("📸 Instagram Profile Extractor")
st.caption(
    "Paste any mix of Instagram URLs — posts, reels, IGTV, stories, profiles. "
    "I'll resolve each one to its original profile URL."
)

with st.form("input_form"):
    text = st.text_area(
        "Paste Instagram URLs (one per line, comma-separated, or wall of text — anything goes)",
        height=220,
        placeholder=(
            "https://www.instagram.com/p/Cxyz.../\n"
            "https://www.instagram.com/reel/Dabc.../\n"
            "https://www.instagram.com/somebrand/\n"
        ),
    )
    col1, col2 = st.columns([1, 3])
    with col1:
        workers = st.slider("Parallel requests", 1, 16, 6)
    with col2:
        submitted = st.form_submit_button("Extract profiles", type="primary", use_container_width=True)

if submitted:
    urls = extract_urls(text)
    if not urls:
        st.warning("No URLs detected in the input.")
        st.stop()

    st.info(f"Found {len(urls)} URL(s). Resolving…")
    progress = st.progress(0.0)
    results: list[dict] = []
    started = time.time()

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(resolve, u): u for u in urls}
        for i, fut in enumerate(as_completed(futures), start=1):
            results.append(fut.result())
            progress.progress(i / len(urls))

    progress.empty()
    elapsed = time.time() - started

    # Preserve original input order
    order = {u: i for i, u in enumerate(urls)}
    results.sort(key=lambda r: order.get(r["input_url"], 0))

    df = pd.DataFrame(results)

    # Unique profile list
    unique_profiles = (
        df.loc[df["username"] != "", ["username", "profile_url"]]
        .drop_duplicates()
        .reset_index(drop=True)
    )

    ok = (df["status"] == "ok").sum()
    bad = (df["status"] == "unresolved").sum()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total URLs", len(df))
    c2.metric("Resolved", ok)
    c3.metric("Unresolved", bad)
    c4.metric("Unique profiles", len(unique_profiles))
    st.caption(f"Done in {elapsed:.1f}s")

    st.subheader("Unique profiles")
    st.dataframe(unique_profiles, use_container_width=True, hide_index=True)

    with st.expander("Per-URL details", expanded=False):
        st.dataframe(df, use_container_width=True, hide_index=True)

    # ---------- Downloads ----------
    csv_bytes = unique_profiles.to_csv(index=False).encode("utf-8")

    xlsx_buf = io.BytesIO()
    with pd.ExcelWriter(xlsx_buf, engine="openpyxl") as writer:
        unique_profiles.to_excel(writer, sheet_name="Profiles", index=False)
        df.to_excel(writer, sheet_name="Details", index=False)
    xlsx_bytes = xlsx_buf.getvalue()

    d1, d2 = st.columns(2)
    d1.download_button(
        "⬇️ Download CSV",
        data=csv_bytes,
        file_name="instagram_profiles.csv",
        mime="text/csv",
        use_container_width=True,
    )
    d2.download_button(
        "⬇️ Download XLSX",
        data=xlsx_bytes,
        file_name="instagram_profiles.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )
