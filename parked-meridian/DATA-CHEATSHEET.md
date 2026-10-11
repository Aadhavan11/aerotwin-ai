# Data and tooling cheat sheet (Project Meridian)

Checked **2026-10-09 (evening, ET) from a US home network**. "Verified" means we ran it and saw the result listed.
Everything marked *from search results* or *from memory* is **unverified**: read the source before relying on it.

## 1. Live feeds we checked

| Feed | Request | Result |
|---|---|---|
| **Weather: METAR** (airport obs) | `https://aviationweather.gov/api/data/metar?ids=KATL&format=json` | **200**, JSON list. Keys include `icaoId, obsTime, temp, dewp, altim, clouds, fltCat, lat, lon, rawOb`. |
| **Weather: G-AIRMET** | `https://aviationweather.gov/api/data/gairmet?format=json` | **200**, 38 items at the time. Keys include `base, coords, due_to, expireTime, forecastHour, geom, geometryType, fzlbase, fzltop`. |
| **Weather: SIGMET** | `https://aviationweather.gov/api/data/airsigmet?format=json` | **200** (about 10 KB) |
| **Weather: PIREP** | `.../pirep?format=json&age=1` | **400** with our guessed parameters. Read the OpenAPI spec at `https://aviationweather.gov/data/api/` for the right ones. |
| **Flights: OpenSky (anonymous)** | `https://opensky-network.org/api/states/all?lamin=33&lomin=-85&lamax=35&lomax=-83` | **200**, 84 aircraft over Georgia; header `x-rate-limit-remaining: 398` of **400 credits/day** anonymous. |
| **NWP: NOAA GFS** (public S3, no auth) | `https://noaa-gfs-bdp-pds.s3.amazonaws.com/?list-type=2&prefix=gfs.&delimiter=/&max-keys=3` | **200** |
| **NWP: NOAA HRRR** (CONUS, public S3) | `https://noaa-hrrr-bdp-pds.s3.amazonaws.com/?list-type=2&prefix=hrrr.&delimiter=/&max-keys=3` | **200** |
| NTSB accident datasets | `https://data.ntsb.gov/avdata/` | 200 (the page; bulk file is `avall.zip`) |
| BTS TranStats (On-Time performance, has tail numbers) | `https://www.transtats.bts.gov/` | 200 (the site; we did not download data) |
| FAA Service Difficulty Reports | `https://www.faa.gov/av-info/download_SDR` | **403 to scripts**: download by hand in a browser (yearly CSVs). |
| ICAO engine emissions databank | `https://www.easa.europa.eu/domains/environment/icao-aircraft-engine-emissions-databank` | 301 redirect (fine) |

### Working snippets (all ran successfully)

```python
import json, urllib.request
UA = "meridian-hackathon (your@email)"          # AWC asks for a custom User-Agent
def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r), r.headers

metar, _ = get("https://aviationweather.gov/api/data/metar?ids=KATL&format=json")
print(metar[0]["rawOb"])

states, hdr = get("https://opensky-network.org/api/states/all?lamin=33&lomin=-85&lamax=35&lomax=-83")
print(len(states["states"]), "aircraft; credits left:", hdr.get("x-rate-limit-remaining"))
FIELDS = ["icao24","callsign","origin_country","time_position","last_contact","lon","lat","baro_alt_m",
          "on_ground","velocity_ms","true_track","vert_rate_ms","sensors","geo_alt_m","squawk","spi","position_source"]
first = dict(zip(FIELDS, states["states"][0]))
```

Note the units OpenSky returns are **SI** (metres, m/s). Convert to ft/kt only at the display edge.

## 2. Rate limits and terms (from search results, unverified: confirm on the official pages)

- **AWC Data API:** no key; keep to about 100 requests/minute; set a custom User-Agent; prefer the cache files for
  bulk data; poll each endpoint about once a minute at most. Up to 15 days of history.
- **OpenSky:** current docs say **OAuth2 client-credentials only** for logged-in use (create an "API client" in your
  account; the client id ends in `-api-client`). Credits per day: anonymous 400, standard 4,000, active feeder
  8,000. Credits are tracked separately for `/states`, `/tracks` and `/flights`. `/states/all` costs 1 credit for a
  box up to 25 square degrees and up to 4 for the whole world. When out: HTTP 429 with
  `X-Rate-Limit-Retry-After-Seconds`. Free for research/non-commercial use: say "non-commercial prototype".
  Historical data (Trino) needs an approved account and has its own limits.
- **Do not scrape** FlightAware, Flightradar24, ADS-B Exchange or similar (terms of service).

## 3. Libraries (checked on PyPI 2026-10-09)

| Package | Version | License | Python | Use |
|---|---|---|---|---|
| `openap` | 2.6.2 | **LGPL-3.0** (one mirror said GPL; PyPI says LGPL) | >= 3.11 | Open aircraft performance: fuel flow, drag, emissions (verify which aircraft types are supported at hour 0) |
| `pycontrails` | 0.64.0 | Apache-2.0 | >= 3.12 | Contrail physics (CoCiP), GFS/ERA5 loaders. ERA5 needs credentials: do not depend on it. |
| `ortools` | 9.15 | Apache-2.0 | >= 3.9 | CP-SAT solver for the maintenance planner |

`pip install openap pycontrails ortools` (use Python 3.12 or newer). **Do not use BADA** (EUROCONTROL licence,
per-project, not redistributable).

## 4. Datasets for the aircraft twin

- **NASA C-MAPSS** (turbofan run-to-failure, FD001-FD004) and **N-CMAPSS** ("Run-to-Failure Simulation Under Real
  Flight Conditions", NASA NTRS 20205001125). NASA's catalog page was reported unavailable while mirrors exist
  (Kaggle/Hugging Face/IEEE DataPort): record the exact source and licence you use.
- **FAA SDR** (yearly CSVs, download by hand), **NTSB** (`avall.zip`), public **Airworthiness Directives** (FAA DRS:
  no public API found).
- **Maintenance manuals:** the real ones (Boeing/Airbus/airline) are licensed and off limits. Build the **Synthetic
  Maintenance Manual** described in the plan (§6.4).

## 5. Constants and rules of thumb (from memory: verify before putting them on a slide)

- CO2 emission index: **3.16 kg CO2 per kg jet fuel** (ICAO/CORSIA). Water vapor EI about 1.23.
- Contrails persist only in **ice-supersaturated** air (RH over ice >= 100%) and when the Schmidt-Appleman criterion
  is met. **Night** contrails warm; some daytime ones cool. NWP humidity is biased dry in these regions.
- **Contrail-only optimization can raise NOx** (2026 TU Delft/DLR preprint, below): show NOx and state the metric.
- Typical MEL repair-interval categories: A (as stated), B 3 days, C 10 days, D 120 days. **Check the MMEL.**
- Eastbound cruising levels are odd (FL310, 330...), westbound even (FL320, 340...) in RVSM airspace.
- Convert once, at the edge: **SI inside**, UTC everywhere, WGS-84.

## 6. Reading list (the research behind the plan)

- Event listing (third-party mirror, unverified): https://us-hackathons.77-90-0-90.sslip.io/events/hacklanta (official: hacklanta.dev and Devpost)
- OpenSky REST docs: https://openskynetwork.github.io/opensky-api/rest.html
- AWC Data API: https://aviationweather.gov/data/api/
- Google/American Airlines contrail trial (2026): https://arxiv.org/pdf/2603.06909 and https://news.aa.com/sustainability/environment/contrail-avoidance
- Contrail vs NOx trade-off: https://jecats.copernicus.org/preprints/jecats-2026-13/jecats-2026-13.pdf
- pycontrails: https://github.com/contrailcirrus/pycontrails
- OpenAP: https://research.tudelft.nl/en/publications/openap-an-open-source-aircraft-performance-model-for-air-transpor
- ICAO engine emissions databank: https://www.easa.europa.eu/domains/environment/icao-aircraft-engine-emissions-databank
- NASA C-MAPSS: https://data.nasa.gov/dataset/c-mapss-aircraft-engine-simulator-data
- FAA SDR: https://www.faa.gov/av-info/download_SDR ; NTSB: https://data.ntsb.gov/avdata/
- FAA reliability program guidance AC 120-17B: https://www.faa.gov/documentLibrary/media/Advisory_Circular/AC_120-17B_Ed_Upd_(7-9-26).pdf
