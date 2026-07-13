# pico-w-digital-display
A raspberry pi pico w powered digital clock and information display.

The main code powering the display can be found [here](code.py)

## Setup

Create a `settings.toml` in the root of the CIRCUITPY drive (it is gitignored, so real credentials never end up in the repo):

```toml
CIRCUITPY_WIFI_SSID = "your-wifi-ssid"
CIRCUITPY_WIFI_PASSWORD = "your-wifi-password"

# Backup weather source, used when BOM scraping fails
OPENWEATHERMAP_API_KEY = "your-openweathermap-api-key"
OPENWEATHERMAP_LOCATION = "Melbourne,VIC,AU"

# UTC offset in seconds, used when worldtimeapi.org is unreachable (36000 = AEST)
UTC_OFFSET_FALLBACK = 36000
```


## Font generation

[https://github.com/peterhinch/micropython-font-to-py](https://github.com/peterhinch/micropython-font-to-py)

`python font_to_py.py test1.ttf 85 font_test.py -x -c {CHARSET}`

## Other

[https://www.instructables.com/RPi-Pico-35-Inch-320x480-HVGA-TFT-LCD-ILI9488-Bitm/](https://www.instructables.com/RPi-Pico-35-Inch-320x480-HVGA-TFT-LCD-ILI9488-Bitm/)