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
- SPI SD card reader (can be the one built into the LCD module) with a
  FAT32-formatted SD card

Everything shares one SPI bus, with separate chip-select pins:

| Signal     | Pico pin |
| ---------- | -------- |
| SPI SCK    | GP10     |
| SPI MOSI   | GP11     |
| SPI MISO   | GP12     |
| LCD CS     | GP9      |
| LCD DC     | GP8      |
| LCD RST    | GP15     |
| SD card CS | GP22     |

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

### 3. Prepare the SD card

The font bitmaps are far too large for the Pico's RAM (and would crowd its
~1 MB flash), so the display streams each glyph from the SD card as it draws.
`code.py` mounts the card at `/sd` on boot.

The SD card is an ordinary FAT32 volume, and there are two ways to get files
onto it:

- **Through the Pico**: while the card is mounted by `code.py`, CircuitPython
  (9+) exposes it as a second USB drive next to `CIRCUITPY` (two drive
  letters, both named after the Pico). Only one side may have write access
  to a FAT volume, so this drive is only writable from the PC because
  `code.py` mounts the card `readonly=True` — if it ever shows up
  write-protected, an older `code.py` on the Pico is holding the write claim
  (or, in a card reader, the physical lock switch on the card is on).
- **In a PC card reader**: mounts like any USB stick.

Copy this repo's [sd-card](sd-card) folder contents onto that drive, so the
card root looks like:

```
E:\
└── fonts\
    ├── Roboto50\   # date glyphs
    ├── Roboto70\   # weather glyphs
    ├── Roboto82\   # clock glyphs
    └── Arial14\    # status line glyphs
```

Each folder holds one raw RGB565 file per character, named by its character
code (see [Font generation](#font-generation)).

### 4. Copy the application

1. Copy `code.py` and the `fonts/` folder (the Python width tables — the
   bitmaps live on the SD card) from this repo to the root of the CIRCUITPY
   drive.
2. Create an empty folder named `sd` in the root of the CIRCUITPY drive
   (CircuitPython 9 requires the mount point to exist).

### 5. Configure

Create a `settings.toml` in the root of the CIRCUITPY drive (it is
gitignored, so real credentials never end up in the repo):

```toml
CIRCUITPY_WIFI_SSID = "your-wifi-ssid"
CIRCUITPY_WIFI_PASSWORD = "your-wifi-password"

# Backup weather source, used when BOM scraping fails
OPENWEATHERMAP_API_KEY = "your-openweathermap-api-key"
OPENWEATHERMAP_LOCATION = "Melbourne,VIC,AU"

# Location for the primary weather source, Open-Meteo
WEATHER_LATITUDE = "-37.8136"
WEATHER_LONGITUDE = "144.9631"

# UTC offset in seconds, used when worldtimeapi.org is unreachable (36000 = AEST)
UTC_OFFSET_FALLBACK = 36000

# Public DNS server to pin (routers sometimes hand out flaky DNS); "" to disable
STATIC_DNS = "8.8.8.8"

```

Only the Wi-Fi keys are required; everything else has the defaults shown above
baked into `code.py` (the OpenWeatherMap backup is skipped without a key).

### 6. First boot

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
The status font was generated with Pillow into the SD card format used by the
display (one raw RGB565 file per character, see `sd-card/fonts/Arial14`).


## Font generation

Fonts come in two parts that must stay in sync:

- **Bitmaps on the SD card** ([sd-card/fonts](sd-card/fonts)): one raw file
  per character, named by character code (e.g. `48` for `0`). Each file is
  `width x bitmap_height` pixels of big-endian RGB565, row-major, white on
  black. The display copies a file straight to the LCD to draw a character.
- **Width tables on the Pico** ([fonts/](fonts)): a small Python module per
  font (`name`, `height`, `bitmap_height`, and a `width` dict mapping
  character code to pixel width) that `code.py` uses for layout.

All fonts can be (re)generated with [tools/generate_font.py](tools/generate_font.py)
(Pillow-based, run on a PC), which writes both parts at once — see the
examples in its docstring. All three Roboto sets use 85px-tall bitmaps
(`bitmap_height = 85`) with the glyph drawn in the top `height` rows; the
"size" in the folder name is the nominal size used for layout. The bitmaps
are checked into [sd-card/fonts](sd-card/fonts), so a fresh SD card only
needs the folder copied over — regeneration is only needed for new fonts,
sizes, or characters.

(Historical note: the original Roboto bitmaps were produced with
[micropython-font-to-py](https://github.com/peterhinch/micropython-font-to-py)
— `python font_to_py.py Roboto.ttf 85 font.py -x -c {CHARSET}` — plus a
conversion step to the raw per-character files that is now lost;
`generate_font.py` replaces that whole pipeline.)

## Other

[https://www.instructables.com/RPi-Pico-35-Inch-320x480-HVGA-TFT-LCD-ILI9488-Bitm/](https://www.instructables.com/RPi-Pico-35-Inch-320x480-HVGA-TFT-LCD-ILI9488-Bitm/)