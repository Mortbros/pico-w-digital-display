# Pico W digital clock + weather display on an ILI9488 480x320 SPI LCD.
# Weather is scraped from the BOM, with OpenWeatherMap as a backup source.
# Wi-Fi credentials and the OpenWeatherMap API key live in settings.toml.
#
# Display driver based on:
# https://www.instructables.com/RPi-Pico-35-Inch-320x480-HVGA-TFT-LCD-ILI9488-Bitm/
# Font bitmaps generated with:
# python font_to_py.py Roboto.ttf 85 font.py -x -c {CHARSET}

import gc
import os
import re
import ssl
import time

from adafruit_bus_device.spi_device import SPIDevice
import adafruit_ntp
import adafruit_requests
import board
import busio
import digitalio
from fonts import Roboto50
from fonts import Roboto70
from fonts import Roboto82
import rtc
import sdcardio
import socketpool
import storage
import supervisor
import wifi

# --- Configuration (from settings.toml) --------------------------------------

WIFI_SSID = os.getenv("CIRCUITPY_WIFI_SSID")
WIFI_PASSWORD = os.getenv("CIRCUITPY_WIFI_PASSWORD")
OWM_API_KEY = os.getenv("OPENWEATHERMAP_API_KEY")
OWM_LOCATION = os.getenv("OPENWEATHERMAP_LOCATION") or "Melbourne,VIC,AU"
# Used at startup and whenever worldtimeapi.org is unreachable.
UTC_OFFSET_FALLBACK = int(os.getenv("UTC_OFFSET_FALLBACK") or 36000)  # AEST

TIMEZONE_INTERVAL = 3 * 60 * 60
WEATHER_INTERVAL = 1 * 60 * 60
WEATHER_RETRY_INTERVAL = 5 * 60  # retry sooner when some values failed to fetch

REQUEST_TIMEOUT = 10
REQUEST_RETRIES = 3
MISSING = 99  # sentinel for weather values that could not be fetched

# All glyph bitmaps on the SD card are 85px tall regardless of nominal font size
GLYPH_HEIGHT = 85

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# BOM rejects requests that look like bots; these headers keep it happy
BOM_HEADERS = {
    "Accept": "",
    "Accept-Encoding": "",
    "Accept-Language": "",
    "Cache-Control": "",
    "Connection": "",
    "Cookie": "",
    "Host": "www.bom.gov.au",
    "Upgrade-Insecure-Requests": "",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/111.0.0.0 Safari/537.36",
}

# --- Networking ---------------------------------------------------------------


def connect_wifi():
    while not wifi.radio.connected:
        try:
            wifi.radio.connect(ssid=WIFI_SSID, password=WIFI_PASSWORD)
        except Exception as e:
            print("Wi-Fi connect failed:", e)
            time.sleep(5)


def fetch_json(url):
    """GET a URL and return the parsed JSON, or None if all retries fail."""
    for attempt in range(REQUEST_RETRIES):
        try:
            with session.get(url, timeout=REQUEST_TIMEOUT) as response:
                return response.json()
        except Exception as e:
            print(
                f"Request to {url} failed ({e}), attempt {attempt + 1}/{REQUEST_RETRIES}"
            )
            time.sleep(2 * (attempt + 1))
            connect_wifi()
    return None


def sync_clock():
    """Set the RTC from NTP. Returns True on success."""
    for attempt in range(5):
        try:
            rtc.RTC().datetime = ntp.datetime
            return True
        except Exception as e:
            print(f"NTP sync failed ({e}), attempt {attempt + 1}/5")
            time.sleep(2)
            connect_wifi()
    return False


def get_timezone():
    """Return the local UTC offset in seconds, keeping the old value on failure."""
    data = fetch_json("http://worldtimeapi.org/api/ip")
    if data is not None:
        try:
            return int(data["raw_offset"]) + int(data.get("dst_offset", 0))
        except (KeyError, ValueError) as e:
            print("Unexpected worldtimeapi response:", e)
    print("Timezone lookup failed, keeping UTC offset", timezone)
    return timezone


# --- Weather ------------------------------------------------------------------


def get_weather_backup():
    """Fetch current/min/max temps from OpenWeatherMap, or None on failure."""
    if not OWM_API_KEY:
        print(
            "OPENWEATHERMAP_API_KEY not set in settings.toml; skipping backup weather"
        )
        return None
    url = (
        "http://api.openweathermap.org/data/2.5/weather"
        f"?q={OWM_LOCATION}&units=metric&appid={OWM_API_KEY}"
    )
    data = fetch_json(url)
    if data is None:
        return None
    try:
        main = data["main"]
        return {
            "temp_current": round(main["temp"]),
            "min_temp": round(main["temp_min"]),
            "max_temp": round(main["temp_max"]),
        }
    except KeyError as e:
        print("Unexpected OpenWeatherMap response:", e)
        return None


class RegexScraper:
    """Remembers the first match of an expression across streamed HTML chunks."""

    def __init__(self, expression, tied_to, group_no):
        self.expression = re.compile(expression)
        self.tied_to = tied_to
        self.group_no = group_no
        self.found = False

    def find(self, chunk):
        if self.found:
            return None
        match = re.search(self.expression, chunk)
        if match is None:
            return None
        self.found = True
        return match.group(self.group_no)


def scrape_html(url, scrapers, data_to_modify):
    changes = []
    with session.get(
        url, headers=BOM_HEADERS, stream=True, timeout=REQUEST_TIMEOUT
    ) as response:
        for chunk in response.iter_content(chunk_size=32768):
            for scraper in scrapers:
                found = scraper.find(chunk)
                if found:
                    changes.append((scraper.tied_to, round(float(found))))
    gc.collect()

    # Applying changes after the loop; updating the dict inside it did not stick
    for key, val in changes:
        data_to_modify[key] = val


def scrape_brunswick(output):
    """Scrape current/low/high temps from the Brunswick BOM data page into output."""
    inside_summary_re = re.compile(r'id="summary-1"')
    current_temp_re = re.compile(r'class="airT">(\d+\.?\d+)')
    low_high_container_re = re.compile(r'class="extT"')
    low_high_temp_re = re.compile(r"(\d+\.?\d+).*C")

    last_line = b""
    inside_summary = False
    low_high_container_occurrences = 0

    with session.get(
        "http://www.bom.gov.au/places/vic/brunswick",
        headers=BOM_HEADERS,
        stream=True,
        timeout=REQUEST_TIMEOUT,
    ) as response:
        for chunk in response.iter_content(chunk_size=32768):
            lines = chunk.split(b"\n")

            for i, line in enumerate(lines[0:-1]):
                if i == 0:
                    line = last_line + line
                if re.search(inside_summary_re, line):
                    inside_summary = True
                if not inside_summary:
                    continue

                current = re.search(current_temp_re, line)
                if (
                    current and output["current_today"] == MISSING
                ):  # capture current temp only once
                    output["current_today"] = round(float(current.group(1)))

                # The low/high values span several lines, so glue the next few on
                if re.search(low_high_temp_re, line) and i < (len(lines) - 1 - 5):
                    line = (
                        line
                        + lines[i + 1]
                        + lines[i + 2]
                        + lines[i + 3]
                        + lines[i + 4]
                        + lines[i + 5]
                    )

                if re.search(low_high_container_re, line):
                    low_high_container_occurrences += 1

                low_high = re.search(low_high_temp_re, line)
                if (
                    low_high
                    and low_high_container_occurrences == 1
                    and output["low_today"] == MISSING
                ):
                    output["low_today"] = round(float(low_high.group(1)))
                elif (
                    low_high
                    and low_high_container_occurrences == 2
                    and output["high_today"] == MISSING
                ):
                    output["high_today"] = round(float(low_high.group(1)))

                if MISSING not in (
                    output["current_today"],
                    output["low_today"],
                    output["high_today"],
                ):
                    inside_summary = False

            last_line = lines[-1]
            gc.collect()
    gc.collect()


def get_weather():
    output = {
        "current_today": MISSING,
        "low_today": MISSING,
        "high_today": MISSING,
        "chance_of_rain": MISSING,
        "uv_index": MISSING,
    }

    try:
        scrape_brunswick(output)
    except Exception as e:
        print("BOM Brunswick scrape failed:", e)

    forecast_re = (
        r'class="forecast".*?(class="min".*?\d+\.?\d?)?.*?class="max".*?(\d+\.?\d?)'
        r'.*?Chance of any rain:.*?class="pop".*?(\d+\.?\d?)'
    )
    try:
        scrape_html(
            "http://www.bom.gov.au/vic/forecasts/melbourne.shtml",
            [
                RegexScraper(forecast_re, "chance_of_rain", 3),
                RegexScraper(forecast_re, "high_today", 2),
                RegexScraper(r"UV Index.*?(\d+\.?\d?)", "uv_index", 1),
            ],
            output,
        )
    except Exception as e:
        print("BOM Melbourne scrape failed:", e)

    if MISSING in (output["current_today"], output["low_today"], output["high_today"]):
        backup = get_weather_backup()
        if backup:
            for key, backup_key in (
                ("current_today", "temp_current"),
                ("low_today", "min_temp"),
                ("high_today", "max_temp"),
            ):
                if output[key] == MISSING:
                    output[key] = backup[backup_key]
                    print("using backup", key)

    print("Obtained weather at", time.time())
    return output


# --- Display ------------------------------------------------------------------


def rgb565(color):
    return (color[0] >> 3 << 11) + (color[1] >> 2 << 5) + (color[2] >> 3)


BLACK = rgb565((0, 0, 0))


def char_metrics(c, font):
    """Return (ord, width) for a character, falling back to the opposite case,
    then to '0' (present in every font) if the glyph is missing."""
    char_ord = ord(c)
    if char_ord in font.width:
        return char_ord, font.width[char_ord]
    swapped = ord(c.lower() if c.isupper() else c.upper())
    if swapped in font.width:
        return swapped, font.width[swapped]
    return 48, font.width[48]


def get_rendered_width(text, font):
    return sum(char_metrics(c, font)[1] for c in text)


class ILI9488:
    def __init__(self, spi, cs, rst, dc):
        self.width = 480
        self.height = 320

        self.cs = digitalio.DigitalInOut(cs)
        self.rst = digitalio.DigitalInOut(rst)
        self.rst.direction = digitalio.Direction.OUTPUT
        self.dc = digitalio.DigitalInOut(dc)
        self.dc.direction = digitalio.Direction.OUTPUT

        self.spi = SPIDevice(spi, self.cs, baudrate=60000000)

        self.buffer_size = 2048
        self.buffer = bytearray(self.buffer_size * 2)

        self.init_display()

    def init_display(self):
        self.rst.value = True
        time.sleep(0.005)
        self.rst.value = False
        time.sleep(0.01)
        self.rst.value = True
        time.sleep(0.005)

        self.write_cmd(0x21)

        self.write_cmd(0xC2)
        self.write_data(0x33)

        self.write_cmd(0xC5)
        self.write_data(0x00)
        self.write_data(0x1E)
        self.write_data(0x80)

        self.write_cmd(0xB1)
        self.write_data(0xB0)

        self.write_cmd(0xE0)
        for b in (
            0x00,
            0x13,
            0x18,
            0x04,
            0x0F,
            0x06,
            0x3A,
            0x56,
            0x4D,
            0x03,
            0x0A,
            0x06,
            0x30,
            0x3E,
            0x0F,
        ):
            self.write_data(b)

        self.write_cmd(0xE1)
        for b in (
            0x00,
            0x13,
            0x18,
            0x01,
            0x11,
            0x06,
            0x38,
            0x34,
            0x4D,
            0x06,
            0x0D,
            0x0B,
            0x31,
            0x37,
            0x0F,
        ):
            self.write_data(b)

        self.write_cmd(0x3A)
        self.write_data(0x55)

        self.write_cmd(0x11)
        time.sleep(0.12)
        self.write_cmd(0x29)

        self.write_cmd(0xB6)
        self.write_data(0x00)
        self.write_data(0x62)

        self.write_cmd(0x36)
        self.write_data(0xE8)

    def write_cmd(self, cmd):
        self.dc.value = False
        with self.spi as spi:
            if type(cmd) in (bytes, bytearray, memoryview):
                spi.write(cmd)
            else:
                spi.write(bytes([cmd]))

    def write_data(self, buf):
        self.dc.value = True
        with self.spi as spi:
            if type(buf) in (bytes, bytearray, memoryview):
                spi.write(buf)
            else:
                spi.write(bytes([buf]))

    def set_block(self, x1, y1, x2, y2):
        self.write_cmd(0x2A)
        self.write_data(x1 >> 8 & 0xFF)
        self.write_data(x1 & 0xFF)
        self.write_data(x2 - 1 >> 8 & 0xFF)
        self.write_data(x2 - 1 & 0xFF)

        self.write_cmd(0x2B)
        self.write_data(y1 >> 8 & 0xFF)
        self.write_data(y1 & 0xFF)
        self.write_data(y2 - 1 >> 8 & 0xFF)
        self.write_data(y2 - 1 & 0xFF)

        self.write_cmd(0x2C)

    def rect(self, x1, y1, x2, y2, color):
        for i in range(self.buffer_size):
            self.buffer[2 * i] = color >> 8 & 0xFF
            self.buffer[2 * i + 1] = color & 0xFF
        chunks, rest = divmod((x2 - x1) * (y2 - y1), self.buffer_size)

        self.set_block(x1, y1, x2, y2)
        for _ in range(chunks):
            self.write_data(self.buffer)
        if rest != 0:
            self.write_data(memoryview(self.buffer)[: rest * 2])

    def print(self, text, old_text, x, y, width, font):
        """Draw text, redrawing only from the first character that changed."""
        cursor = x
        old_text_pad = f"{old_text[0 : len(text)]:<{len(text)}}"
        for i in range(len(text)):
            if text[i] != old_text_pad[i]:
                for j in range(i, len(text)):
                    char_ord, char_width = char_metrics(text[j], font)
                    self.set_block(cursor, y, cursor + char_width, y + GLYPH_HEIGHT)
                    with open(
                        f"/sd/fonts/{font.name}{font.height}/{char_ord}", "rb"
                    ) as f:
                        self.write_data(f.read())
                    cursor += char_width
                # Blank out whatever the old (possibly longer) text left behind
                self.rect(cursor, y, x + width, y + GLYPH_HEIGHT, BLACK)
                return
            cursor += char_metrics(text[i], font)[1]


# --- Setup --------------------------------------------------------------------

spi = busio.SPI(board.GP10, board.GP11, board.GP12)

sdcard = sdcardio.SDCard(spi, board.GP22)
vfs = storage.VfsFat(sdcard)
storage.mount(vfs, "/sd")

lcd = ILI9488(spi, board.GP9, board.GP15, board.GP8)
lcd.rect(0, 0, 480, 320, BLACK)

connect_wifi()
pool = socketpool.SocketPool(wifi.radio)
session = adafruit_requests.Session(pool, ssl.create_default_context())
ntp = adafruit_ntp.NTP(pool, server="pool.ntp.org", tz_offset=0)

while not sync_clock():
    print("Could not sync clock, retrying in 30s")
    time.sleep(30)

timezone = UTC_OFFSET_FALLBACK
timezone = get_timezone()
weather = get_weather()

next_timezone_sync = time.monotonic() + TIMEZONE_INTERVAL
next_weather_sync = time.monotonic() + (
    WEATHER_RETRY_INTERVAL if MISSING in weather.values() else WEATHER_INTERVAL
)

time_str_old = ""
date_str_old = ""
temp_high_old = ""
temp_cur_old = ""
chance_of_rain_old = ""
uv_index_old = ""

# --- Main loop ------------------------------------------------------------------

while True:
    try:
        now = time.monotonic()

        if now >= next_timezone_sync:
            timezone = get_timezone()
            sync_clock()  # also re-sync the RTC periodically to correct drift
            next_timezone_sync = now + TIMEZONE_INTERVAL

        if now >= next_weather_sync:
            weather = get_weather()
            if MISSING in weather.values():
                next_weather_sync = now + WEATHER_RETRY_INTERVAL
            else:
                next_weather_sync = now + WEATHER_INTERVAL

        lt = time.localtime(time.time() + timezone)

        time_str = f"{lt.tm_hour:02}:{lt.tm_min:02}:{lt.tm_sec:02}"
        lcd.print(time_str, time_str_old, 16, 0, 480, Roboto82)
        time_str_old = time_str

        date_str = f"{WEEKDAYS[lt.tm_wday]} {lt.tm_mday:02}/{lt.tm_mon:02}/{lt.tm_year}"
        date_str_width = get_rendered_width(date_str, Roboto50)
        if date_str_width > 480:  # Shorten date string if it is too long to render
            date_str = date_str[0:-4] + date_str[-2:]  # Mon 01/01/1234 -> Mon 01/01/34
            date_str_width = get_rendered_width(date_str, Roboto50)
        date_str_x_pos = max((480 - date_str_width) // 2, 0)
        lcd.print(date_str, date_str_old, date_str_x_pos, 100, 480, Roboto50)
        date_str_old = date_str

        temp_high = f"↑{weather['high_today']}"
        lcd.print(temp_high, temp_high_old, 0, 160, 176, Roboto70)
        temp_high_old = temp_high

        temp_cur = f"←{weather['current_today']}"
        lcd.print(temp_cur, temp_cur_old, 261, 160, 176, Roboto70)
        temp_cur_old = temp_cur

        chance_of_rain = f"↕{weather['chance_of_rain']}%"
        lcd.print(chance_of_rain, chance_of_rain_old, 0, 242, 176, Roboto70)
        chance_of_rain_old = chance_of_rain

        uv_index = f"↔{weather['uv_index']}"
        lcd.print(uv_index, uv_index_old, 261, 242, 176, Roboto70)
        uv_index_old = uv_index

        time.sleep(0.1)
    except Exception as e:
        # Network problems are handled inside the fetch functions, so anything
        # that lands here is unexpected; reboot rather than freeze.
        print("Unexpected error, reloading:", e)
        time.sleep(5)
        supervisor.reload()
