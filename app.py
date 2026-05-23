"""
Streamlit app: paste any mix of Instagram URLs (posts, reels, IGTV, profiles,
stories) and get back the original profile URL for each one.

Uses Playwright (headless Chromium with a mobile UA) to bypass Instagram's
unauthenticated-fetch blank-shell response.

Run:
    py -m streamlit run app.py
"""

import io
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd
import streamlit as st


@st.cache_resource(show_spinner="Installing headless browser (first run only)…")
def ensure_chromium() -> bool:
    """Install Playwright's chromium browser on first run (e.g. Streamlit Cloud)."""
    marker = Path.home() / ".cache" / "ms-playwright" / ".installed"
    if marker.exists():
        return True
    try:
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=True,
            capture_output=True,
            timeout=300,
        )
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
        return True
    except Exception as e:
        st.warning(f"Browser auto-install failed: {e}")
        return False


ensure_chromium()

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

MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
    "Mobile/15E148 Safari/604.1"
)

USERNAME_PATTERNS = [
    re.compile(r'"owner":\{[^}]*"username":"([A-Za-z0-9_.]+)"'),
    re.compile(r'"username":"([A-Za-z0-9_.]+)"'),
    re.compile(r'\(@([A-Za-z0-9_.]+)\)'),
]


def extract_urls(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for u in URL_RE.findall(text or ""):
        u = u.rstrip(".,);]")
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def normalize(url: str) -> str:
    """Canonicalize the URL so /reels/ -> /reel/ and trailing extra paths drop."""
    m = POST_RE.match(url)
    if m:
        kind = m.group(1).lower()
        if kind in ("reels",):
            kind = "reel"
        return f"https://www.instagram.com/{kind}/{m.group(2)}/"
    return url


def quick_username(url: str) -> str | None:
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


def classify(url: str) -> str:
    if USER_POST_RE.match(url):
        return "user-post"
    if POST_RE.match(url):
        return "post"
    if STORIES_RE.match(url):
        return "story"
    if PROFILE_RE.match(url):
        return "profile"
    return "unknown"


def extract_from_html(html: str) -> str | None:
    for pat in USERNAME_PATTERNS:
        m = pat.search(html)
        if m and m.group(1).lower() not in RESERVED:
            return m.group(1)
    return None


def resolve_with_browser(urls: list[str], wait_ms: int, progress_cb) -> dict[str, str | None]:
    """Open one headless Chromium, resolve each URL that needs a page fetch."""
    from playwright.sync_api import sync_playwright

    out: dict[str, str | None] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=MOBILE_UA,
            viewport={"width": 390, "height": 844},
        )
        page = ctx.new_page()
        for i, url in enumerate(urls, start=1):
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(wait_ms)
                out[url] = extract_from_html(page.content())
            except Exception:
                out[url] = None
            progress_cb(i, len(urls))
        browser.close()
    return out


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
            "https://www.instagram.com/p/DUuwNpHkXEH/\n"
            "https://www.instagram.com/reel/DG_FLH7MplK/\n"
            "https://www.instagram.com/somebrand/\n"
        ),
    )
    col1, col2 = st.columns([1, 3])
    with col1:
        wait_ms = st.slider("Page wait (ms)", 1500, 6000, 3000, 500,
                            help="How long to let Instagram's JS hydrate before reading the page.")
    with col2:
        submitted = st.form_submit_button("Extract profiles", type="primary", use_container_width=True)

if submitted:
    urls = extract_urls(text)
    if not urls:
        st.warning("No URLs detected in the input.")
        st.stop()

    # Normalize + classify, separate quick wins from URLs that need browser fetch
    rows: list[dict] = []
    needs_browser: list[str] = []
    for raw in urls:
        nu = normalize(raw)
        kind = classify(nu)
        uname = quick_username(nu)
        rows.append({"input_url": raw, "normalized": nu, "type": kind, "username": uname})
        if not uname and kind in ("post", "user-post", "unknown"):
            if nu not in needs_browser:
                needs_browser.append(nu)

    st.info(
        f"Found {len(urls)} URL(s). "
        f"{len(urls) - len(needs_browser)} resolved instantly, "
        f"{len(needs_browser)} need a headless browser fetch."
    )

    if needs_browser:
        progress = st.progress(0.0, text="Launching headless Chromium…")

        def cb(i, total):
            progress.progress(i / total, text=f"Fetched {i}/{total}")

        started = time.time()
        try:
            resolved = resolve_with_browser(needs_browser, wait_ms, cb)
        except Exception as e:
            st.error(
                f"Playwright failed to launch: {e}\n\n"
                "If this is the first run, install the browser:\n\n"
                "`py -m playwright install chromium`"
            )
            st.stop()
        progress.empty()

        for r in rows:
            if not r["username"] and r["normalized"] in resolved:
                r["username"] = resolved[r["normalized"]]

        st.caption(f"Browser pass done in {time.time() - started:.1f}s")

    for r in rows:
        r["profile_url"] = f"https://www.instagram.com/{r['username']}/" if r["username"] else ""
        r["status"] = "ok" if r["username"] else "unresolved"

    df = pd.DataFrame(rows)

    unique_profiles = (
        df.loc[df["username"].astype(bool), ["username", "profile_url"]]
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
