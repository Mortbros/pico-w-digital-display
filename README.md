# pico-w-digital-display
A raspberry pi pico w powered digital clock and information display.

The main code powering the display can be found [here](code.py)

## Data sources

- Weather + UTC offset: [Open-Meteo](https://open-meteo.com/) (free, no API key, ~650 byte responses)
- Backup temperatures: [OpenWeatherMap](https://openweathermap.org/current) current weather API
- Time: NTP (`pool.ntp.org`)

The BOM scrapers were removed in July 2026: BOM retired the pages they read
(`/places/...` and `/vic/forecasts/...`) during their site redesign, and the
new JSON API is restricted to registered users.

## Setting up from scratch

### Hardware

- Raspberry Pi Pico W
- 3.5" 480x320 ILI9488 SPI LCD

The LCD module's SD card slot is **no longer used**: fonts now live on the
Pico's internal flash as packed 4-bit glyphs (they were previously raw
RGB565 on the SD card, but the card shares the SPI bus and is also serviced
over USB, so every glyph read fought the PC for the bus and drawing was
extremely slow). Any card left in the slot is ignored; its chip-select is
held high so it stays off the bus.

| Signal                | Pico pin |
| --------------------- | -------- |
| SPI SCK               | GP10     |
| SPI MOSI              | GP11     |
| SPI MISO              | GP12     |
| LCD CS                | GP9      |
| LCD DC                | GP8      |
| LCD RST               | GP15     |
| SD card CS (unused)   | GP22     |

### 1. Flash CircuitPython

1. Download the latest stable CircuitPython UF2 **for the Pico W** from
   [circuitpython.org/board/raspberry_pi_pico_w](https://circuitpython.org/board/raspberry_pi_pico_w/)
   (this project targets 9.x or newer, and is developed against 10.x).
2. Hold the BOOTSEL button while plugging the Pico into USB. It mounts as a
   drive named `RPI-RP2`.
3. Copy the UF2 onto that drive. The Pico reboots and re-mounts as `CIRCUITPY`.

### 2. Install the libraries

Download the [Adafruit CircuitPython library bundle](https://circuitpython.org/libraries)
matching your CircuitPython major version (e.g. 9.x bundle for 9.x firmware —
`.mpy` files are not compatible across major versions) and copy these into
`CIRCUITPY/lib/`:

- `adafruit_requests.mpy`
- `adafruit_connection_manager.mpy` (dependency of adafruit_requests)
- `adafruit_ntp.mpy`
- `adafruit_bus_device/`

### 3. Copy the application

Copy these from the repo to the root of the CIRCUITPY drive:

- `code.py`
- `glyphs/` — the packed, self-describing font bitmaps (~90 KB), one folder
  per font:

```
CIRCUITPY\glyphs\
├── Roboto50\   # date glyphs
├── Roboto70\   # weather glyphs
├── Roboto82\   # clock glyphs
└── Arial14\    # status line glyphs
```

If an `sd` folder, a `fonts` folder, or other old font copies exist on
CIRCUITPY from earlier versions, delete them — everything the display needs
is in the two items above.

### 4. Configure

Create a `settings.toml` in the root of the CIRCUITPY drive (it is
gitignored, so real credentials never end up in the repo):

```toml
CIRCUITPY_WIFI_SSID = "your-wifi-ssid"
CIRCUITPY_WIFI_PASSWORD = "your-wifi-password"

# Backup weather source, used when Open-Meteo is unreachable
OPENWEATHERMAP_API_KEY = "your-openweathermap-api-key"
OPENWEATHERMAP_LOCATION = "Melbourne,VIC,AU"

# Location for the primary weather source, Open-Meteo
WEATHER_LATITUDE = "-37.8136"
WEATHER_LONGITUDE = "144.9631"

# UTC offset in seconds, used when the timezone lookup fails (36000 = AEST)
UTC_OFFSET_FALLBACK = 36000

# Public DNS server to pin (routers sometimes hand out flaky DNS); "" to disable
STATIC_DNS = "8.8.8.8"

```

Only the Wi-Fi keys are required; everything else has the defaults shown above
baked into `code.py` (the OpenWeatherMap backup is skipped without a key).

### 5. First boot

The Pico runs `code.py` automatically on power-up. The screen shows
"connecting to Wi-Fi..." then "syncing clock (NTP)..." on the status line,
then the clock appears and the weather fills in after the first fetch. For
troubleshooting, watch the serial console (e.g. the Serial tab in
[Mu](https://codewith.mu/) or PuTTY on the Pico's COM port at 115200 baud) —
every error on the status line is also printed there with full detail.

## Status line

Errors are shown on a tiny 14px status line between the clock and the date
(weather fetch problems, Wi-Fi reconnects, NTP failures). The clock keeps
running through any failure; weather slots are blank until data is available.

If something unexpected crashes the program, the full traceback is drawn
across the whole screen in the tiny font (and printed to serial), stays up
for 60 seconds, and then the Pico reboots itself — so a frozen display
always tells you why.


## Font generation

Each font is a folder in [glyphs/](glyphs) holding one raw file per
character, named by character code (e.g. `48` for `0`). Every file is
self-describing: a 2-byte header `[width, bitmap_height]` followed by the
pixels as 4-bit grayscale — two per byte, high nibble first, row-major, rows
padded to whole bytes, white on black. `code.py` reads all font metrics from
these headers at boot and expands the pixels to RGB565 through a lookup
table while drawing, so a glyph's drawn width can never disagree with its
bitmap (a mismatch there shears the image diagonally — the cause of a
long-standing rendering glitch).

New fonts can be generated with [tools/generate_font.py](tools/generate_font.py)
(Pillow-based, run on a PC) — see the examples in its docstring.

The Roboto70 and Roboto82 sets are rendered from
[tools/Roboto-Regular-Custom-Icons.ttf](tools/Roboto-Regular-Custom-Icons.ttf)
— Roboto Regular with the custom weather icons drawn into the arrow
codepoints (`↑←↕↔` etc.) — scaled to match the geometry of the original
bitmaps (which were horizontally condensed relative to the raw font). The
originals live in the legacy [sd-card/fonts](sd-card/fonts) folder; several
of them (most Roboto82 characters and the Roboto70 digits 5-8) were corrupt
at source, which caused years of mystery rendering glitches, so everything
except Roboto50 and Arial14 was regenerated from the TTF in July 2026.

(Historical note: the original Roboto bitmaps were produced with
[micropython-font-to-py](https://github.com/peterhinch/micropython-font-to-py)
— `python font_to_py.py Roboto.ttf 85 font.py -x -c {CHARSET}` — plus a
conversion step to the raw per-character files that is now lost;
`generate_font.py` replaces that whole pipeline.)

### Note about custom icons

The currently committed Roboto70 font ttf file source file was modified to include icons instead of the `↑←↕↔` icons. Regenerating this font will not replicate these icons.

## Other

[https://www.instructables.com/RPi-Pico-35-Inch-320x480-HVGA-TFT-LCD-ILI9488-Bitm/](https://www.instructables.com/RPi-Pico-35-Inch-320x480-HVGA-TFT-LCD-ILI9488-Bitm/)