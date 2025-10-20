from __future__ import annotations

import os
import requests
import uvicorn
import asyncio
import random
from datetime import datetime, timezone, time, date, timedelta
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from typing import Union, Optional

from selenium import webdriver
from selenium.common import TimeoutException, NoSuchElementException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


# --- Configuration ---
URL = "https://www.radia.sk/radia/beta/playlist"
REQUEST_TIMEOUT_SEC = 28
REQUEST_TIMEOUT_SEC_HTTP = 28
LOCAL_TZ = ZoneInfo("Europe/Bratislava")
RADIO_NAME = "Beta"

# --- Shared State ---
is_radio_playing = True

# --- Pydantic Models ---
class Song(BaseModel):
    radio: str = Field(RADIO_NAME, description="The name of the radio station.")
    interpreters: str = Field(..., description="The name of the song's interpreters.")
    title: str = Field(..., description="The title of the song.")
    start_time: time = Field(..., description="Local time (Europe/Bratislava) when the song started.")
    timestamp: datetime = Field(..., description="The full ISO 8601 timestamp of when the data was fetched.")

class Silence(BaseModel):
    radio: str = Field(RADIO_NAME, description="The name of the radio station.")
    is_playing: bool = Field(False, description="False indicates no song is currently playing.")
    message: str = Field("Could not find a currently playing song.", description="Human-friendly message.")
    timestamp: datetime = Field(..., description="When this was checked (UTC).")

class ListenerStats(BaseModel):
    listeners: int = Field(..., description="Generated number of listeners.")
    timestamp: datetime = Field(..., description="The full ISO 8601 timestamp of when the data was fetched.")

ResponseModel = Union[Song, Silence]

# --- API Application ---
app = FastAPI(
    title="Radio Song Generator API",
    description="API to get the current song from Radio Beta or silence status.",
    version="2.0.0-docker"
)

# --- Selenium Driver Setup ---
def _build_driver() -> webdriver.Chrome:
    """Configures and builds the Selenium Chrome driver for a Docker environment."""
    opts = webdriver.ChromeOptions()
    opts.add_argument("--headless=new")
    opts.add_argument("--disable-gpu")
    # --- CRUCIAL OPTIONS FOR DOCKER & AZURE ---
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    # --- END CRUCIAL OPTIONS ---
    opts.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")
    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(REQUEST_TIMEOUT_SEC)
    return driver

# --- Scraping Logic ---

def fetch_html_static(url: str) -> Optional[BeautifulSoup]:
    """Loads the static part of an HTML page for the playlist table."""
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT_SEC_HTTP)
        response.raise_for_status()
        return BeautifulSoup(response.content, 'html.parser')
    except requests.RequestException as e:
        print(f"Error loading static page {url} with Requests: {e}")
        return None

def scrape_onair_dynamic(driver: webdriver.Chrome) -> Optional[dict]:
    """Uses Selenium to scrape the dynamic 'On Air' block."""
    wait = WebDriverWait(driver, REQUEST_TIMEOUT_SEC)
    block_xpath = "//div[contains(@class, 'radio_profil_play')]"
    wait.until(EC.presence_of_element_located((By.XPATH, block_xpath)))
    onair_block = driver.find_element(By.XPATH, block_xpath)
    try:
        interpret_el = onair_block.find_element(By.XPATH, ".//span[contains(@class,'interpret')]")
        titul_el = onair_block.find_element(By.XPATH, ".//span[contains(@class,'titul')]")
        interpret_txt = interpret_el.text.strip()
        titul_txt = titul_el.text.strip()
    except (NoSuchElementException, TimeoutException):
        return None

    t_norm = " ".join(titul_txt.lower().split())
    silence_patterns = {"nehrá žiadna pesnička", "je dočasne nedostupná"}
    if t_norm in silence_patterns or not interpret_txt or not titul_txt:
        return None
    return {"interpreters": interpret_txt, "title": titul_txt}

def try_get_start_time_from_playlist(soup: BeautifulSoup, artist: str, title: str) -> Optional[time]:
    """Reads the static playlist table to find the matching song's start time."""
    playlist_table = soup.find('div', id='playlist_table')
    if not playlist_table: return None

    song_rows = playlist_table.find_all('a', class_='datum_cas_skladba')
    if not song_rows: return None

    norm = lambda s: " ".join((s or "").strip().casefold().split())
    n_artist, n_title = norm(artist), norm(title)

    for row in song_rows[:5]: # Check the top 5 recent songs
        time_span = row.find('span', class_='cas')
        artist_span = row.find('span', class_='interpret')
        title_span = row.find('span', class_='titul')

        if not all([time_span, artist_span, title_span]): continue

        if norm(artist_span.get_text()) == n_artist and norm(title_span.get_text()) == n_title:
            try:
                hh, mm = time_span.get_text(strip=True).split(":")[:2]
                return time(int(hh), int(mm), tzinfo=LOCAL_TZ)
            except Exception as e:
                print(f"Error parsing time '{time_span.get_text(strip=True)}': {e}")
                return None
    
    print("WARNING: Exact match for the current song not found in the playlist table.")
    return None

# --- API Endpoint ---
@app.get("/now-playing", response_model=ResponseModel)
def now_playing() -> ResponseModel:
    global is_radio_playing
    ts_utc = datetime.now(timezone.utc)
    driver = None
    try:
        driver = _build_driver()
        driver.get(URL)
        onair = scrape_onair_dynamic(driver)
        if onair is None:
            is_radio_playing = False
            return Silence(is_playing=False, message="Nothing is playing right now.", timestamp=ts_utc)
    except (TimeoutException, WebDriverException) as e:
        is_radio_playing = False
        print(f"CRITICAL ERROR: {type(e).__name__} during Selenium operation. Error: {e}")
        return Silence(is_playing=False, message="Upstream page unavailable.", timestamp=ts_utc)
    finally:
        if driver:
            driver.quit()
    
    soup = fetch_html_static(URL)
    start_t = None
    if soup:
        start_t = try_get_start_time_from_playlist(soup, onair["interpreters"], onair["title"])
    
    if start_t is None:
        start_t = datetime.now(LOCAL_TZ).time().replace(second=0, microsecond=0, tzinfo=LOCAL_TZ)
        print("WARNING: Using current local time as fallback for start time.")

    is_radio_playing = True
    return Song(
        interpreters=onair["interpreters"],
        title=onair["title"],
        start_time=start_t,
        timestamp=ts_utc,
    )

# --- WebSocket Listeners Endpoint ---
last_listeners = 0
def generate_listeners_stats() -> ListenerStats:
    global last_listeners, is_radio_playing
    ts_utc = datetime.now(timezone.utc)

    if not is_radio_playing:
        last_listeners = 0
        return ListenerStats(listeners=0, timestamp=ts_utc)

    now_local = datetime.now(LOCAL_TZ)
    current_hour = now_local.hour
    min_base, max_base = get_hourly_base_range(current_hour)
    
    if last_listeners == 0:
        new_listeners = random.randint(min_base, max_base)
    else:
        max_change = int((max_base - min_base) * 0.15) + 2
        change = random.randint(-max_change, max_change)
        new_listeners = last_listeners + change

    new_listeners = max(min_base, min(new_listeners, max_base))
    last_listeners = new_listeners
    return ListenerStats(listeners=new_listeners, timestamp=ts_utc)

def get_hourly_base_range(hour: int) -> tuple[int, int]:
    """ Returns realistic listener ranges based on real data for Radio Beta. """
    if 0 <= hour <= 5: return 10, 40
    elif 6 <= hour <= 8: return 100, 150
    elif 9 <= hour <= 11: return 80, 130
    elif 12 <= hour <= 14: return 70, 110
    elif 15 <= hour <= 18: return 120, 180
    elif 19 <= hour <= 22: return 50, 90
    elif hour == 23: return 30, 60
    else: return 50, 80

@app.websocket("/listeners")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print(f"WebSocket connection accepted: {websocket.client}")
    try:
        while True:
            stats = generate_listeners_stats()
            await websocket.send_json(stats.model_dump(mode='json'))
            await asyncio.sleep(15)
    except WebSocketDisconnect:
        print(f"WebSocket disconnected: {websocket.client}")
    except Exception as e:
        print(f"WebSocket error: {e}")

# --- Main Entry Point ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)

