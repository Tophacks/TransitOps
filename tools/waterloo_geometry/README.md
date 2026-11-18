# Waterloo corridor geometry

`build_corridor.py` produces `backend/waterloo_corridor.json` and an identical
`frontend/lib/geo/waterloo-corridor.json`. Both the Python simulator and the
map read it, so a bus is in the same place in the API response and on screen.

## Sources

| Data | Source | Used for |
| --- | --- | --- |
| GRT bus and ION GTFS (`bus/` and `lrt/` feeds, service from 2026-11-16) | Region of Waterloo Open Data, via the snapshot branch `2026-07-05_2028-07-03` of [jason897/grt-gtfs-history](https://github.com/jason897/grt-gtfs-history) | Street paths, curb stops, ION track and platforms |
| Lanelet2 lane map of the roads around campus (`lanelet2_roads_around_campus.osm`) | University of Waterloo WISE Lab, [GeoScenario Server](https://github.com/rodrigoqueiroz/geoscenarioserver) (MIT) | Independent check of the campus path only; nothing from it is shipped |

Contains information provided by the Regional Municipality of Waterloo under
the Region's Open Data Licence.

## What the builder does

1. Nodes every GRT bus shape into a directed street graph. Buses can change
   streets only where GRT routes actually meet, and never U-turn mid-block.
2. Routes each simulator segment between real curb stops, on the side of the
   street each stop serves:

   | Simulator stop | GRT stop |
   | --- | --- |
   | `UW` | 1078 University of Waterloo Station (bus loop, eastbound bay) |
   | `WATERLOO_SQUARE` | 4048 Waterloo Public Square Station (King St southbound) |
   | `GRAND_RIVER_HOSPITAL` | 2540 Grand River Hospital Station (King St southbound) |
   | `CENTRAL` | 1901 Central Station (King St southbound) |
   | `YARD` | 3227 Victoria / Joseph (northbound curb, staging point) |

   The return leg (`CENTRAL -> UW`) goes back up King Street through Uptown
   (GRT route 7 northbound), then loops via Columbia St and the campus road so
   it reaches the UW bay facing the right way.
3. Rounds every corner with a tangent arc (11 m radius for a 12 m bus), so a
   bus turns inside the intersection instead of cutting across the block.
4. Records curve speed caps from the path curvature, the stop line of the
   signal the simulator applies to each segment, and how far each stop pole
   sits from the street centreline (so the bus pulls in to the curb).
5. Extracts both ION tracks from the GTFS rail shapes between Allen and
   Research & Technology, removing the out-and-back spurs GTFS uses to touch
   each platform.

## Regenerate

```bash
git clone --depth 1 --branch 2026-07-05_2028-07-03 https://github.com/jason897/grt-gtfs-history /tmp/gtfs
python3 -I tools/waterloo_geometry/build_corridor.py /tmp/gtfs \
  --lanelets path/to/lanelet2_roads_around_campus.osm
cd backend && python3 parity_fixture.py   # refresh the browser parity fixture
```

## Verification (current output)

- Lengths: UW → Waterloo Public Square 2.79 km; → Grand River Hospital
  1.25 km; → Central 1.25 km; Central → UW 7.29 km; yard → Central 0.30 km.
- Campus turns: right 89° from the bus loop onto Phillip St, left 87° from
  Phillip St onto University Ave, right 109° from University Ave onto King St.
- Against the lanelet map, on roads it covers, the bus centre (1.9 m right of
  the centreline) is a median 0.9–1.9 m and a 95th percentile 2.8–4.2 m from
  the nearest same-direction lane centre. Larger gaps occur only where the
  path leaves the mapped roads (the UW Station bus loop, the campus road off
  Columbia St and one Uptown junction).
- ION platforms sit 0.1–6.5 m from the extracted track. The sharpest bend is
  King St onto Erb St at Waterloo Public Square (about 88° within 20 m).
