# Fleet map: destinations outside the US

Added 2026-10-11 (`web/src/components/USMap.tsx`).

* The map now fits itself to the stations it is given. While every station is in the contiguous US it draws the same US view as before (fixed projection, US outline). As soon as any station is
  outside it (the API's `/fleet` or `/stations` reporting a latitude and longitude), it switches to a view fitted to all stations over the Natural Earth 110m coastline (public domain, a lazily
  loaded 17 KB chunk, `web/src/lib/worldLand.ts`, regenerated from `land-110m.json`), with a latitude/longitude grid. Labels flip to the left near the right edge; routes are arcs and aircraft in flight sit on their arc.
* **The simulated fleet is unchanged: 8 US stations.** Adding stations to the generator would change rotations, the golden scenario and several tests, so we did not. To show that the map copes, the
  Fleet overview has a button, **Show sample international destinations**, that adds LHR, CDG, YYZ and MEX with dashed sample routes (JFK-LHR, ATL-CDG, ORD-YYZ, MIA-MEX). They are drawn in blue, labeled
  "sample destination", have no aircraft and no legs, and are not in the simulation.
* Tested in a browser: the US view (8 stations, no coastline drawn), the sample view (12 stations, all inside the map), and an API that reports Tokyo, Sydney, Sao Paulo and Dubai in addition (12 stations, all inside the map).
