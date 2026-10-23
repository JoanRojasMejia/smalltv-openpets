# GeekMagic SmallTV-Ultra — HTTP API (observed)

Device: `<screen-ip>` · `/v.json` → `{"m":"SmallTV-Ultra","v":"Ultra-V9.0.54"}` · captured 2026-10-22.
Source: the device's own web UI (`settings.html`, `network.html`, `weather.html`, `time.html`, `image.html`, `js/settings.js`).

## Quirks

- No authentication on any endpoint.
- Static pages are served **gzip-compressed without a `Content-Encoding` header** — pipe through `gunzip`.
- `GET /` → `302` to `/settings.html`.
- **Almost every write is a plain `GET /set?...`** — "GET only" is NOT read-only on this device.
- `/set` answers the text `OK` on success.
- ICMP ping did not answer from the test machine while HTTP did.

## Read-only (verified 200)

| Endpoint | Returns |
| --- | --- |
| `GET /v.json` | model and firmware version |
| `GET /space.json` | `{"total":3121152,"free":1252876}` (bytes, filesystem) |
| `GET /album.json` | `{"autoplay":1,"i_i":5}` |
| `GET /city.json` | `{"ct","t","mt","cd","loc"}` (city, current weather code, …) |
| `GET /tz.json` | `{"tz_auto":1,"tz_off":480}` (minutes) |
| `GET /filelist?dir=/image/` | HTML `<table>` of files (name, KB) |
| `GET /filelist?dir=/gif` | HTML `<table>` of files |
| `GET /image/<file>`, `/gif/<file>` | the stored file |

Referenced by the UI but **404 on V9.0.54**: `app.json`, `brt.json`, `timebrt.json`, `theme_list.json`,
`delay.json`, `w_i.json`, `unit.json`, `hour12.json`, `ntp.json`, `day.json`, `timecolor.json`,
`font.json`, `colon.json`, `daytimer.json`.

Sensitive, deliberately not read: `GET /config.json` (returns WiFi SSID **and password** in clear text, keys `a`/`p`),
`GET /key.json` (weather API key).

## Writes (NOT called — documented only)

| Purpose | Request |
| --- | --- |
| Theme (1 Weather Clock, 2 Forecast, 3 Photo Album, 4–6 Time Styles, 7 Simple Weather) | `GET /set?theme=N` |
| Auto-switch themes | `GET /set?theme_list=1,0,1,...&sw_en=0\|1&theme_interval=S` |
| Brightness (-10..100) | `GET /set?brt=N` |
| Night mode | `GET /set?t1=H&t2=H&b1=50&b2=N&en=0\|1` |
| City (name or OpenWeather ID) | `GET /set?cd1=<city>&cd2=1000` |
| Units | `GET /set?w_u=..&t_u=..&p_u=..` |
| Weather interval (min) | `GET /set?w_i=N` |
| Weather / forecast API key | `GET /set?key=...` / `GET /set?fkey=...` |
| Timezone | `GET /set?tz_auto=0\|1&tz_offset=<minutes>` |
| 12h clock / colon blink / font / day format | `GET /set?hour=0\|1` · `colon=0\|1` · `font=N` · `day=N` |
| Clock colors | `GET /set?hc=<#hex>&mc=..&sc=..` |
| NTP server / sync interval | `GET /set?ntp=host` · `GET /set?time_interval=N` |
| Countdown date | `GET /set?yr=Y&mth=M&day=D` |
| Album autoplay | `GET /set?i_i=S&autoplay=0\|1` |
| Show a specific album image / weather GIF | `GET /set?img=/image/x.gif` · `GET /set?gif=/gif/x.gif` |
| Upload file | `POST /doUpload?dir=/image/` or `?dir=/gif` (multipart) |
| Delete file / clear folder | `GET /delete?file=/image/x.jpg` · `GET /set?clear=image\|gif` |
| WiFi delay / credentials | `GET /set?delay=S` · `GET /wifisave?s=<ssid>&p=<pwd>` |
| WiFi scan | `GET /wifi.json?q=1` |
| Reboot / factory reset | `GET /set?reboot=1` · `GET /set?reset=1` |
| Firmware OTA | `/update` (upload form, `.bin`) |

## Media constraints (from the UI)

- Album (`/image/`): **JPG or GIF only, 240×240**. The UI crops JPGs client-side to 240×240 at quality 0.9 before upload; GIFs go as-is.
- Weather-screen GIF (`/gif`): **80×80 GIF**.
- Uploaded file names are trimmed to the last 20 chars when longer than 30; `&` → `_`.
- Filesystem: ~3.0 MB total, ~1.2 MB free at capture time.
