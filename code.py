import board
import busio
import digitalio
from adafruit_bus_device.spi_device import SPIDevice

import sdcardio
import storage

import os
import wifi
import socketpool
import ssl
import adafruit_requests

import time
import adafruit_ntp
import rtc

import re
import gc

import supervisor # for auto reboot to prevent freezing

from fonts import Roboto50
from fonts import Roboto70
from fonts import Roboto82

def rgb565(color):
    return (color[0] >> 3 << 11) + (color[1] >> 2 << 5) + (color[2] >> 3)

def get_timezone():
    offset = str(session.get("http://worldtimeapi.org/api/ip").json()["utc_offset"])
    return (1 if "+" in offset[0] else -1)*(int(offset[1:3]) * 3600 + int(offset[4:5]) * 60)

def get_weather_backup():
    api_key = 'REDACTED'
    weather_address = f"http://api.openweathermap.org/data/2.5/weather?q={"Melbourne"},{"VIC"},{"AUS"}&appid={api_key}"
    uv_address = f"http://api.openweathermap.org/data/2.5/onecall?lat={"37.8136"}&lon={"144.9631"}&exclude={"minutely,hourly,daily,alerts"}&appid={api_key}"

    weather_json = session.get(weather_address).json()
    uv_json = session.get(uv_address).json()
    end = time.monotonic()

    uv_level = int(uv_json['current']['uvi'])
    if uv_level == 0:
        uv_desc = "No risk"
    elif uv_level > 0 and uv_level <= 2:
        uv_desc = "Low; 60min"
    elif uv_level > 2 and uv_level <= 5:
        uv_desc = "Moderate; 45min"
    elif uv_level > 5 and uv_level <= 7:
        uv_desc = "High; 30min"
    elif uv_level > 7 and uv_level <= 10:
        uv_desc = "Very high; 20min"
    elif uv_level > 11:
        uv_desc = "Extreme; 10min"
    else:
        uv_desc = "Send help"

    weather_type = weather_json['weather'][0]['main']
    weather_desc = weather_json['weather'][0]['description']

    temp_current = round(int(weather_json['main']['temp']) - 273.15)
    temp_fl = round(int(weather_json['main']['feels_like']) - 273.15)
    max_temp = round(int(weather_json['main']['temp_max']) - 273.15)
    min_temp = round(int(weather_json['main']['temp_min']) - 273.15)

    sunset = time.localtime(int(weather_json['sys']['sunset']) + timezone)
    sunrise = time.localtime(int(weather_json['sys']['sunrise']) + timezone)

    humidity = weather_json['main']['humidity']

    return {"weather_type": weather_type, "weather_desc": weather_desc, "temp_current": temp_current, "temp_fl": temp_fl, "max_temp": max_temp, "min_temp": min_temp, "humidity": humidity, "uv_level": uv_level, "uv_desc": uv_desc, "sunset": sunset, "sunrise": sunrise}

def celcius_clean(inp):
    return re.sub(r"[^0-9\.]", "", inp)

class RegexScraper():
    def __init__(self, expression, tied_to, group_no):
        self.expression = re.compile(expression)
        self.tied_to = tied_to
        self.group_no = group_no
        self.found = False

    def find(self, chunk):
        output = False
        if not self.found:
            try:
                output = re.search(self.expression, chunk).group(self.group_no)
                self.found = True
            except:
                pass
        
        return output

headers = {"Accept": "",
        "Accept-Encoding": "",
        "Accept-Language": "",
        "Cache-Control": "",
        "Connection": "",
        "Cookie": "",
        "Host": "www.bom.gov.au",
        "Upgrade-Insecure-Requests": "",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/111.0.0.0 Safari/537.36"
        }

def scrape_html(url, expressions, data_to_modify):
    changes = []
    response = session.get(url, headers=headers, stream=True)

    for chunk in response.iter_content(chunk_size=32768):
        for scraper in expressions:
            found_out = scraper.find(chunk)
            if found_out:
                changes.append([scraper.tied_to, int(round(float(found_out)))])
    response.close()

    # I have no idea why i need to do this, updating it in the loop did not cause the dict to actually update
    for key, val in changes:
        data_to_modify[key] = val
    
    return data_to_modify


def get_weather():
    output = {"current_today": 99, "low_today": 99, "high_today": 99, "chance_of_rain": 99, "uv_index": 99}
    # Get min and max temp from brunswick BOM data page
    brunswick_response = session.get("http://www.bom.gov.au/places/vic/brunswick", headers = headers, stream=True)
    
    inside_summary_re = re.compile('id="summary-1"')
    current_temp_re = re.compile('class=\"airT\">(\d+\.?\d+)')
    low_high_container_re = re.compile('class=\"extT\"')
    low_high_temp_re = re.compile('(\d+\.?\d+).*C')
        
    last_line = b''
    inside_summary = False
    low_high_containter_occurences = 0
    # Used to capture the first two 
    # Process response in chunks
    for chunk in brunswick_response.iter_content(chunk_size=32768):  # Adjust chunk_size as needed
        lines = chunk.split(b'\n')
        
        for i, line in enumerate(lines[0:-1]):
            # print(f"{inside_summary_re=} {current_temp_re=} {low_temp_re=} {high_temp_re=}")
            # print(it, output, inside_summary, re.search(current_temp_re, line), re.search(low_temp_re, line), re.search(high_temp_re, line))
            if i == 0:
                line = last_line + line
            if re.search(inside_summary_re, line):
                inside_summary = True
            if inside_summary:
                current = re.search(current_temp_re, line)
                low_high_container = re.search(low_high_container_re, line)
                low_high = re.search(low_high_temp_re, line)
                # print(f"{output}")
                # print(f"{current=}\t{low_high_container=}\t{low_high=}\t{low_high_containter_occurences=}")

                if current and output["current_today"] == 99: # capture current temp only once
                    output["current_today"] = round(float(current.group(1)))

                if re.search(low_high_temp_re, line) and i < (len(lines) - 1 - 5):
                    line = line + lines[i + 1] + lines[i + 2] + lines[i + 3] + lines[i + 4] + lines[i + 5] # INEFFICIENCY HERE: reads the next 5 line 2 times

                # print(f"{low_high_container=}\t{low_high=}\t{low_high_containter_occurences=}\t{output['low_today']=}")
                # if low_high_containter_occurences > 0:
                #     print(line)
                if low_high_container:
                    low_high_containter_occurences += 1

                if low_high and low_high_containter_occurences == 1 and output["low_today"] == 99:
                    output["low_today"] = round(float(low_high.group(1)))
                elif low_high and low_high_containter_occurences == 2 and output["high_today"] == 99:
                    output["high_today"] = round(float(low_high.group(1)))

                if output["current_today"] != 99 and output["low_today"] != 99 and output["high_today"] != 99:
                    inside_summary = False

        last_line = lines[-1]
        gc.collect()

    brunswick_response.close()

    # Obtain Rainfall %, UV data, and backup temps from general melbourne BOM data page
    melbourne_rain_obj = RegexScraper('class=\"forecast\".*?(class=\"min\".*?\d+\.?\d?)?.*?class=\"max\".*?(\d+\.?\d?).*?Chance of any rain:.*?class=\"pop\".*?(\d+\.?\d?)', 'chance_of_rain', 3)
    melbourne_maxtemp_obj = RegexScraper('class=\"forecast\".*?(class=\"min\".*?\d+\.?\d?)?.*?class=\"max\".*?(\d+\.?\d?).*?Chance of any rain:.*?class=\"pop\".*?(\d+\.?\d?)', 'high_today', 2)
    melbourne_uv_obj = RegexScraper('UV Index.*?(\d+\.?\d?)', 'uv_index', 1)


    output = scrape_html("http://www.bom.gov.au/vic/forecasts/melbourne.shtml", [melbourne_rain_obj, melbourne_maxtemp_obj, melbourne_uv_obj], output)

    if output["low_today"] == 99 or output["high_today"] == 99 or output["current_today"] == 99 or output["uv_index"] == 99 or output["chance_of_rain"] == 99:
        # TODO: make it notify me somehow that this failed
        weather = get_weather_backup()
        if output["low_today"] == 99:
            output["low_today"] = weather["min_temp"]
            print("using backup low_today")
        if output["high_today"] == 99:
            output["high_today"] = weather["max_temp"]
            print("using backup high_today")
        if output["current_today"] == 99:
            output["current_today"] = weather["temp_current"]
            print("using backup current_today")
        if output["uv_index"] == 99:
            output["uv_index"] = weather["uv_level"]
            print("using backup uv_index")
    print("Obtained weather at", time.time())
    return output

def get_rendered_width(text, font):
    width = 0
    for c in text:
        try:
            char_ord = ord(c)
            char_width = font.width[char_ord]
        except KeyError: # If character ord not present in font file, try upper and lowercase and use 0 if all fails
            try:
                if c.isupper():
                    char_ord = ord(c.lower())
                else: # Using else here because the output of isupper and islower is a Bool
                    char_ord = ord(c.upper())
                char_width = font.width[char_ord]
            except KeyError:
                char_ord = 48 # ASCII for 0 (included in all fonts)
                char_width = font.width[char_ord]
        width += char_width
    return width

class ILI9488():
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
        self.buffer = bytearray(self.buffer_size*2)

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

        self.write_cmd(0XC5)
        self.write_data(0x00)
        self.write_data(0x1e)
        self.write_data(0x80)

        self.write_cmd(0xB1)
        self.write_data(0xB0)

        self.write_cmd(0XE0)
        self.write_data(0x00)
        self.write_data(0x13)
        self.write_data(0x18)
        self.write_data(0x04)
        self.write_data(0x0F)
        self.write_data(0x06)
        self.write_data(0x3a)
        self.write_data(0x56)
        self.write_data(0x4d)
        self.write_data(0x03)
        self.write_data(0x0a)
        self.write_data(0x06)
        self.write_data(0x30)
        self.write_data(0x3e)
        self.write_data(0x0f)

        self.write_cmd(0XE1)
        self.write_data(0x00)
        self.write_data(0x13)
        self.write_data(0x18)
        self.write_data(0x01)
        self.write_data(0x11)
        self.write_data(0x06)
        self.write_data(0x38)
        self.write_data(0x34)
        self.write_data(0x4d)
        self.write_data(0x06)
        self.write_data(0x0d)
        self.write_data(0x0b)
        self.write_data(0x31)
        self.write_data(0x37)
        self.write_data(0x0f)

        self.write_cmd(0X3A)
        self.write_data(0x55)

        self.write_cmd(0x11)
        time.sleep(0.12)
        self.write_cmd(0x29)

        self.write_cmd(0xB6)
        self.write_data(0x00)
        self.write_data(0x62)

        self.write_cmd(0x36)
        self.write_data(0xe8)

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
        self.write_data(x1 >> 8 & 0xff)
        self.write_data(x1 & 0xff)
        self.write_data(x2-1 >> 8 & 0xff)
        self.write_data(x2-1 & 0xff)

        self.write_cmd(0x2B)
        self.write_data(y1 >> 8 & 0xff)
        self.write_data(y1 & 0xff)
        self.write_data(y2-1 >> 8 & 0xff)
        self.write_data(y2-1 & 0xff)

        self.write_cmd(0x2C)

    def rect(self, x1, y1, x2, y2, color):
        for i in range(self.buffer_size):
            self.buffer[2*i] = color >> 8 & 0xff
            self.buffer[2*i+1] = color & 0xff
        chunks, rest = divmod((x2-x1) * (y2-y1), self.buffer_size)

        self.set_block(x1, y1, x2, y2)
        if chunks:
            for count in range(chunks):
                self.write_data(self.buffer)
        if rest != 0:
            self.write_data(memoryview(self.buffer)[:rest*2])

    def print(self, text, old_text, x, y, width, font):
        cursor = x
        old_text_pad = f"{old_text[0:len(text)]:<{len(text)}}"
        for i in range(len(text)):
            if text[i] != old_text_pad[i]:
                for j in range(i, len(text)):
                    # lcd.set_block(cursor, y, cursor+font.width[ord(text[j])], y+font.height)
                    # print(cursor+font.width[ord(text[j])]) 
                    try:
                        char_ord = ord(text[j])
                        char_width = font.width[char_ord]
                    except KeyError: # If character ord not present in font file, try upper and lowercase and use 0 if all fails
                        try:
                            if text[j].isupper():
                                char_ord = ord(text[j].lower())
                            else: # Using else here because the output of isupper and islower is a Bool
                                char_ord = ord(text[j].upper())
                            char_width = font.width[char_ord]
                        except KeyError:
                            char_ord = 48 # ASCII for 0 (included in all fonts)
                            char_width = font.width[char_ord]
                    # print(f"{cursor=} {y=} {cursor+char_width=} {char_ord=} {text[j]=}")
                    lcd.set_block(cursor, y, cursor+char_width, y+85)
                    with open(f"/sd/fonts/{font.name}{font.height}/{char_ord}", "rb") as f:
                        lcd.write_data(f.read())
                    cursor += char_width
                # self.rect(cursor, y, x+width, y+font.height, rgb565((0, 0, 0)))
                self.rect(cursor, y, x+width, y+85, rgb565((0, 0, 0)))
                return
            cursor += font.width[ord(text[i])]

spi = busio.SPI(board.GP10, board.GP11, board.GP12)

sdcard = sdcardio.SDCard(spi, board.GP22)
vfs = storage.VfsFat(sdcard)
storage.mount(vfs, "/sd")

lcd = ILI9488(spi, board.GP9, board.GP15, board.GP8)
lcd.rect(0, 0, 480, 320, rgb565((0, 0, 0)))

weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

wifi.radio.connect(ssid=os.getenv("CIRCUITPY_WIFI_SSID"), password=os.getenv("CIRCUITPY_WIFI_PASSWORD"))
pool = socketpool.SocketPool(wifi.radio)
session = adafruit_requests.Session(pool, ssl.create_default_context())

ntp = adafruit_ntp.NTP(pool, server="pool.ntp.org", tz_offset=0)
rtc.RTC().datetime = ntp.datetime

timezone_duration = 3*60*60
timezone_prev_time = -timezone_duration

weather_duration = 1*60*60
# weather_duration = 1*60
weather_prev_time = -weather_duration

# reload_duration = 12*60*60 # Reload the script every 12 hours to prevent freezing
# reload_prev_time = time.monotonic() # +1 to prevent reload when it boots up

timezone = get_timezone()
weather = get_weather()

time_str_old = ""
date_str_old = ""
temp_high_old = ""
temp_cur_old = ""
temp_low_old = ""
# temp_fl_old = ""
chance_of_rain_old = ""
uv_index_old = ""


while True:
    try:
        now = time.monotonic()
        # if now >= reload_prev_time + reload_duration:
        #     supervisor.reload()

        if now >= timezone_prev_time + timezone_duration:
            timezone = get_timezone()  
            timezone_prev_time = now

        if now >= weather_prev_time + weather_duration:
            # before = int(time.time())
            weather = get_weather()
            # after = int(time.time())
            # time_taken = after - before
            # print("Time taken:", time_taken)
            weather_prev_time = now

        lt = time.localtime(time.time() + timezone)

        time_str = f"{lt.tm_hour:02}:{lt.tm_min:02}:{lt.tm_sec:02}"
        lcd.print(time_str, time_str_old, 16, 0, 480, Roboto82)
        time_str_old = time_str

        date_str = f"{weekdays[lt.tm_wday]} {lt.tm_mday:02}/{lt.tm_mon:02}/{lt.tm_year:02}"
        date_str_width = get_rendered_width(date_str, Roboto50)
        if date_str_width > 480: # Shorten date string if it is too long to render
            date_str = date_str[0:-4] + date_str[-2:] # Convert Mon 01/01/1234 -> Mon 01/01/34
            date_str_width = get_rendered_width(date_str, Roboto50)
        elif date_str_width == 480:
            date_str_width = 480
        date_str_x_pos = (480 - date_str_width) // 2
        if date_str_x_pos < 0:
            date_str_x_pos = 0
        lcd.print(date_str, date_str_old, date_str_x_pos, 100, 480, Roboto50)
        date_str_old = date_str

        temp_high = f'↑{weather["high_today"]}'
        lcd.print(temp_high, temp_high_old, 0, 160, 176, Roboto70)
        temp_high_old = temp_high

        temp_cur = f'←{weather["current_today"]}'
        lcd.print(temp_cur, temp_cur_old, 261, 160, 176, Roboto70)
        temp_cur_old = temp_cur

        chance_of_rain = f'↕{weather["chance_of_rain"]}%'
        lcd.print(chance_of_rain, chance_of_rain_old, 0, 242, 176, Roboto70)
        chance_of_rain_old = chance_of_rain

        uv_index = f'↔{weather["uv_index"]}'
        lcd.print(uv_index, uv_index_old, 261, 242, 176, Roboto70)
        uv_index_old = uv_index
    except Exception as e:
        print(e)
        supervisor.reload()
        pass
        # n = 13 # chars per line on error message print
        # error = str(repr(e))
        # print(error)
        # lines = [error[i:i+n] for i in range(0, len(error), n)]
        # print("------", lines, "------")
        # draw_height = 0
        # for line in lines:
        #     print(f"{line=} {draw_height=} {Roboto50=}")
        #     lcd.print(line, "", 0, draw_height, 400, Roboto50)
        #     draw_height += 50
        #     if draw_height >= 450:
        #         break
        # print(error)
        # input("ERRORED, waiting for input...")
# TODO: ignore keyerror if char not present in font bitmap. manually replace all chars that are not valid e.g ASCII char 109
# python font_to_py.py test1.ttf 85 font_test.py -x -c {CHARSET}

# https://www.instructables.com/RPi-Pico-35-Inch-320x480-HVGA-TFT-LCD-ILI9488-Bitm/
