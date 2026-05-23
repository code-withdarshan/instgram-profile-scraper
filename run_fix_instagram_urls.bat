@echo off
REM Double-click this file to convert post/reel URLs in google.csv into profile URLs.
REM It writes google_profiles_only.csv in this same folder.

cd /d "%~dp0"

where python >nul 2>&1
if %errorlevel%==0 (
    python fix_instagram_urls.py
    goto end
)

where py >nul 2>&1
if %errorlevel%==0 (
    py fix_instagram_urls.py
    goto end
)

echo Python was not found on this PC.
echo Install it from https://www.python.org/downloads/ then double-click this file again.

:end
echo.
pause
