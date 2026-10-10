# Glossary (so nobody has to be an aviation person)

## Flying and routing

| Term | Meaning |
|---|---|
| **Flight level (FL)** | Altitude in hundreds of feet at a standard pressure setting. FL350 = 35,000 ft. |
| **RVSM** | Reduced Vertical Separation Minimum: 1,000 ft spacing between about FL290 and FL410. |
| **Semicircular rule** | Direction of travel decides which levels are legal: eastbound odd (FL310, 330), westbound even (FL320, 340). Our router must obey it. |
| **Great circle** | Shortest path on the globe; our baseline route. |
| **Lateral / vertical profile** | Where the route goes sideways, and which levels it flies. We optimize both. |
| **Pareto front** | The set of options where improving one thing (CO2e) costs another (fuel/time). We show it as a curve with a slider. |
| **OFP** | Operational flight plan: what dispatch hands the crew. |
| **OCC / dispatcher** | Airline operations control center; the person who plans and releases flights. |
| **ETOPS** | Rules for twin-engine flights far from airports. Out of scope. |

## Weather

| Term | Meaning |
|---|---|
| **METAR / SPECI** | Current airport weather observation (coded text; the API gives JSON). |
| **TAF** | Airport forecast. |
| **SIGMET** | Warning of hazardous weather for aircraft (convective, turbulence, ice, ash). |
| **G-AIRMET** | Graphical advisories (turbulence, icing, mountain obscuration, low visibility) for the lower 48. |
| **PIREP** | Pilot report. |
| **NWP / GFS / HRRR** | Numerical weather prediction. GFS is global; HRRR is high-resolution for the continental US. |
| **GRIB2** | The file format of weather model grids. |

## Climate

| Term | Meaning |
|---|---|
| **CO2e** | CO2-equivalent: other warming effects converted to the CO2 that would warm the same amount. |
| **GWP100 / ATR20** | Two ways to convert (100-year global warming potential; 20-year average temperature response). The answer depends on the choice, so always state it. |
| **Contrail** | Condensation trail behind a jet. Persistent contrails form cirrus clouds that warm the planet. |
| **ISSR** | Ice-supersaturated region: air humid enough for contrails to persist. |
| **Schmidt-Appleman criterion (SAC)** | The temperature/humidity condition for a contrail to form at all. |
| **CoCiP** | Contrail Cirrus Prediction model (in `pycontrails`). |
| **NOx / nvPM** | Nitrogen oxides / non-volatile particulate matter from engines. NOx has altitude-dependent warming and cooling effects. |
| **LTO cycle** | Landing and take-off cycle (below about 3,000 ft). The ICAO emissions databank covers only this. |
| **EI** | Emission index: grams of a pollutant per kilogram of fuel. |
| **SAF** | Sustainable aviation fuel. |

## Aircraft data

| Term | Meaning |
|---|---|
| **ADS-B** | Aircraft broadcast of position/velocity about once a second. Public receivers feed OpenSky. **No fuel or engine data.** |
| **ICAO24** | The aircraft's 24-bit hex address in ADS-B (maps to a registration/tail number via a database). |
| **ACARS** | Datalink for short messages between aircraft and ground. |
| **OOOI** | Out, Off, On, In: the four gate/runway events of a flight. |
| **QAR / FDR / FDM** | Quick Access Recorder / Flight Data Recorder / Flight Data Monitoring. Hundreds to thousands of parameters, **offloaded at the gate**, so batch, hours later. |
| **AHM** | Aircraft health monitoring (fault messages and trends). |
| **Tail number** | The registration of one specific aircraft (N-number in the US). We use fictional `SIM-xxx` ones. |
| **BADA / OpenAP** | Aircraft performance models. BADA is licensed (avoid); OpenAP is open (use). |

## Maintenance

| Term | Meaning |
|---|---|
| **ATA chapter** | Standard numbering of aircraft systems (21 air conditioning, 32 landing gear, 36 pneumatics, 72 engine). |
| **AMM / MPD / IPC / SRM** | Maintenance manual, planning document, parts catalog, structural repair manual. Licensed and customer-specific: **do not ingest**. |
| **MEL / MMEL** | (Master) Minimum Equipment List: which broken items an aircraft may fly with, and for how long. Repair categories typically A (as stated), B (3 days), C (10), D (120). Verify per type. |
| **AD / SB** | Airworthiness Directive (mandatory) / Service Bulletin (manufacturer advice). |
| **A-check / C-check** | Scheduled inspections of increasing depth. Intervals are airline- and type-specific. |
| **CAMP / CASS** | Continuous airworthiness maintenance program / continuing analysis and surveillance. Airline-run, FAA-approved. |
| **Reliability program** | The airline's way of adjusting maintenance intervals from data (FAA AC 120-17B). |
| **Part 121 / Part 145** | US rules for scheduled airlines / for repair stations. |
| **AOG** | Aircraft on ground: grounded and costing money. |
| **MCC** | Maintenance control center: decides what gets fixed where and when. |
| **RUL** | Remaining useful life. We output P10 / P50 / P90. |
| **EGT margin** | How far an engine's exhaust gas temperature is from its limit; it shrinks as the engine wears. |
| **C-MAPSS / N-CMAPSS** | NASA's simulated engine run-to-failure datasets. |
| **Digital twin** | A software model of one specific aircraft whose state follows how it is flown and what its sensors say. |

## Optimization and engineering

| Term | Meaning |
|---|---|
| **DP (dynamic programming)** | How the router finds the best path through a grid of positions and levels. |
| **CP-SAT** | Google OR-Tools constraint solver; the planner uses it. |
| **Monotone safety rule** | The system may only recommend maintenance **earlier** than an approved interval, never later. |
| **Adapter** | The code that turns a real or simulated feed into our standard message. "Swap the adapter, keep the platform." |
| **Envelope** | Our standard wrapper around every message (id, type, tail, timestamps, `synthetic` flag, payload). |
| **IC-1...IC-5** | Integration checkpoints in the schedule. |
