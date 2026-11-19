# Waterloo corridor geometry

`build_corridor.py` produces `backend/waterloo_corridor.json` and an identical
`frontend/lib/geo/waterloo-corridor.json`. Both the Python simulator and the
map read it, so a bus is in the same place in the API response and on screen.

## Sources

| Data | Source | Used for |
| --- | --- | --- |
| GRT bus and ION GTFS (`bus/` and `lrt/` feeds, service from 2026-11-16) | Region of Waterloo Open Data, via the snapshot branch `2026-07-05_2028-07-03` of [jason897/grt-gtfs-history](https://github.com/jason897/grt-gtfs-history) | Street paths, curb stops, ION track and stations, context routes |
| Lanelet2 lane map of the roads around campus (`geoscenarioserver/scenarios/maps/experimental_maps/lanelet2_roads_around_campus.osm`) | University of Waterloo WISE Lab, [GeoScenario Server](https://github.com/rodrigoqueiroz/geoscenarioserver) (MIT) | Independent check of the campus parts of the paths only; nothing from it is shipped |

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
5. Extracts both ION tracks end to end (Fairway to Conestoga, 16 stations
   each way, taken from the ION timetable), removing the out-and-back spurs
   GTFS uses to touch each platform, and simplifies them to 0.25 m. Curve
   speed caps are computed on the dense track first, so no curve is lost.
6. Adds context routes for the map through campus, Laurier and Uptown: the
   most frequent GTFS shape in each direction of

   | Route | Why |
   | --- | --- |
   | 201 iXpress Fischer-Hallman | Columbia St, UW Station, King St north |
   | 202 iXpress University | University Ave past UW and Wilfrid Laurier |
   | 31 Columbia | Columbia St through the campus villages |
   | 7 King | King St through Uptown and Waterloo Public Square |

   cut exactly at the edges of a Waterloo box (43.452–43.492 N,
   80.505–80.570 W) and simplified to 1.5 m from the GTFS vertices, with the
   stops each direction serves. The builder stops if a route leaves the box
   and comes back, which would split it. (At 80.566 W the 201 did, for 120 m
   at the Columbia St / Fischer-Hallman corner.)

## Regenerate

```bash
git clone --depth 1 --branch 2026-07-05_2028-07-03 https://github.com/jason897/grt-gtfs-history /tmp/gtfs
git clone --depth 1 https://github.com/rodrigoqueiroz/geoscenarioserver /tmp/gss
python3 -I tools/waterloo_geometry/build_corridor.py /tmp/gtfs \
  --lanelets /tmp/gss/geoscenarioserver/scenarios/maps/experimental_maps/lanelet2_roads_around_campus.osm
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
- ION: northbound 16.1 km and southbound 15.9 km (255 and 204 points after
  simplifying the GTFS vertices to 0.25 m). Platforms sit within 6.5 m
  (northbound) and 8.7 m (southbound, Mill Station) of the extracted track.
  The tightest bends are the Fairway terminal loop (81–84° within 20 m) and
  King St onto Erb St at Waterloo Public Square.
- Context routes: 5.0–7.3 km each inside Waterloo, 14–40 points after
  simplifying (from 65–83 GTFS vertices). Against the lanelet map the
  202 sits a median 1.2–2.2 m (p95 under 3.8 m) from a same-direction lane,
  and the 201 and 31 a median 0.8 m (p95 2.4 m) in one direction (the lanelet map
  does not reach King St, so the 7 has no lanelet figure). In the other
  direction the 201 and 31 read 5.2 m (p95 8.0 m): on Columbia St W GRT draws
  both directions of these routes on one line, so a bus placed right of it
  fits one direction's lanes and sits across the median from the other's.
  The routes are drawn on the map only; simulated buses still use the
  corridor paths above.
- The whole file is 61 KiB (the campus corridor alone was 24 KiB).

## Independent check

`verify_gtfs.py` (written with Codex) re-derives everything above from the
GTFS without using the builder's code. It checks that:

- the two copies are byte-identical, and the file is under 100 KiB
- each ION direction runs its 16 stations in timetable order, Fairway to
  Conestoga, with platforms within 10 m of the track
- each ION track is within 0.5% of the de-spurred GTFS length and within
  0.5 m of the GTFS shape, both ways
- every context route uses its most frequent shape and stays within 1.7 m of
  the GTFS shape inside the box, both ways
- every route's stops are in trip order and within 25 m of its path

`.github/workflows/geometry-check.yml` runs it in CI against the same GTFS
snapshot:

```bash
python3 -I tools/waterloo_geometry/verify_gtfs.py /tmp/gtfs backend/waterloo_corridor.json
python3 -m unittest discover -s tools/waterloo_geometry/tests
```

Current output: 67 checks pass. It also compares the snapshot with the
Region's current static feeds (`--official-zip`, `--official-ion-zip`); they
match.
