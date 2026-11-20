# GRT bus garages

The simulator runs GRT's three bus garages. In the simulated hour, each garage
sends a bus out of service to the first stop of its block and receives another
back from the last stop of its block. The buses drive real roads, and each
keeps one vehicle ID throughout.

- Geometry and routes: `tools/waterloo_geometry/build_garages.py` →
  `backend/bus_garages.json` and `frontend/lib/geo/bus-garages.json`.
- Simulator: `backend/garage_ops.py`, run at the end of every simulation.
- Playback: `frontend/lib/motion/garages.ts`.
- Tests: `backend/tests/test_garages.py` and `frontend/e2e/verify-garages.mjs`.

## Sites (OSM, checked on the Region's 2026 imagery)

| Garage | OSM | Address | Bus position (chosen on the imagery) |
| --- | --- | --- | --- |
| Northfield Drive | way 1010416379, "Grand River Transit Northfield Drive Maintenance Facility" (`addr:housenumber=300`, Northfield Drive East) | 300 Northfield Drive East, Waterloo | south apron of the garage building (node 9322733749) |
| Strasburg / Chandler | way 166966500, the industrial site holding building 68282507 "Strasburg Transit Garage" (250 Strasburg Road). It runs through to Chandler Drive on its west side | 250 Strasburg Road / 85 Chandler Drive, Kitchener | parking rows east of the garage (node 6084834696) |
| Conestoga Boulevard | way 827706044, the industrial site holding the address point 460 Conestoga Boulevard (node 4035136813) | 460 Conestoga Boulevard, Cambridge | west side of the garage building (node 416249602) |

Each site was overlaid on the Region of Waterloo's 2026 orthoimagery
(`gis.regionofwaterloo.ca`, `waimagery/Imagery_2026`), with the mapped
driveways and roads on top:

- **Northfield:** the garage building has a south bus apron. A road runs along its north and east sides. A separate staff car park lies to the south.
- **Strasburg:** the garage building has bus parking rows on its east side.
- **Conestoga Boulevard:** buses stand beside the garage building.

The imagery is used only to check the sites; it is not redistributed.

## Driveways and permitted movements

A driveway is a node where one of a site's service roads meets a public road.
Its permitted movements are read from the mapped road: one-way tags and turn
restrictions. A two-way road allows left and right turns, in and out. None of
these driveways carries a turn restriction in OSM.

| Garage | Driveway node | Road | Exits | Entries |
| --- | --- | --- | --- | --- |
| Northfield | 9322733751 | Northfield Drive East | left, right | left, right |
| Northfield | 7455462041 | University Avenue East | left, right | left, right |
| Northfield | 443120684 | Toman Drive at Northfield Drive East | left or right onto Northfield Drive East, right onto Toman Drive | left or right from Northfield Drive East, left from Toman Drive |
| Strasburg | 6084834693, 2292047535, 4588147352 | Strasburg Road | left, right | left, right |
| Strasburg | 822174554, 4588146906 | Chandler Drive | left, right | left, right |
| Strasburg | 332089954 | Chandler Drive at Strasburg Road | left onto Chandler Drive, right onto Strasburg Road | left from Strasburg Road, right from Chandler Drive |
| Conestoga Blvd | 416303308, 416220397, 11090720475 | Conestoga Boulevard | left, right | left, right |
| Conestoga Blvd | 1215577555, 11090720476, 13094856838 | Bishop Street | left, right | left, right |

At Northfield, the Toman Drive driveway serves the staff car park. It has no legal route from the bus apron, so dispatch rejects it and says so.

## Roads

Out-of-service routes are planned on a directed graph of the OSM roads around each site and stop:

- one-way streets and roundabouts are respected;
- roads closed to traffic are skipped unless they are open to buses;
- every turn restriction is applied (`no_*`, `only_*`);
- U-turns are allowed only at dead ends;
- service roads are used only inside a garage site, or on the terminal or campus roads that lead to a service stop.

Speeds are the mapped `maxspeed`, or a default by road class, plus 6 s per turn sharper than 45° (both modelled).

Dispatch plans each move once through each of the garage's driveways, with the
others closed, and takes the fastest legal one. The options and times are in
the Decision Inspector.

## The hour (07:30–08:30, Wednesday 2 December 2026)

No GRT block starts its first trip between 08:05 and 08:55 on a weekday, so an
08:00 hour has no pull-outs. 07:30–08:30 is the only window in which all three
garages both send and receive a bus. The run's minute 0 is 07:30 for the
garages and for UW Station's background buses.

Which garage runs which block is not in GTFS. Each block is assigned to the
garage nearest its first stop (pull-out) or last stop (return). This assignment
is modelled.

| Garage | Move | Block | Route and trip | GTFS service stop (verified) | Deadhead | Driveway |
| --- | --- | --- | --- | --- | --- | --- |
| Northfield | out | 393529 | 201 to Conestoga College | first stop 1127 Conestoga Station, departs 08:00 | 2.6 km, 4.5 min: Northfield Dr E, Kraus Dr | 9322733751 (Northfield Dr E) |
| Northfield | in | 393598 | 13, ends at UW Station | last stop 1223 University of Waterloo Station, 08:15 | 5.9 km, 7.0 min from the UW Station exit: Phillip St, Columbia St W, King St N, Northfield Dr E | 9322733751 |
| Strasburg | out | 393512 | 35 to Central Station | first stop 1675 Sunrise Centre Station, departs 07:40 | 3.2 km, 4.9 min: Strasburg Rd, Ottawa St S | 6084834693 (Strasburg Rd) |
| Strasburg | in | 393510 | 35, ends at Sunrise Centre | last stop 1675 Sunrise Centre Station, 08:19 | 3.2 km, 5.0 min | 6084834693 |
| Conestoga Blvd | out | 394447 | 61 to Conestoga College | first stop 1063 Cambridge Centre Station, departs 08:00 | 1.6 km, 2.9 min: Conestoga Blvd, Bishop St, Hespeler Rd | 416303308 (Conestoga Blvd) |
| Conestoga Blvd | in | 393474 | 201, ends at Conestoga College Doon | last stop 1732 Conestoga College Doon Campus Door 3, 07:51 | 10.6 km, 13.9 min: Doon Valley Dr, Homer Watson Blvd, Fountain St S, King St E, Bishop St, Conestoga Blvd | 416303308 |

The Cambridge test needed roads and a stop outside the existing corridor:
Cambridge Centre Station (stop 1063), Hespeler Road, Bishop Street and
Conestoga Boulevard, plus the Doon campus roads, Homer Watson Boulevard,
Fountain Street and King Street for the return. All of them are in the routing
data (`sources/garages-osm.json`).

## Operations

- **Pull-out:** the bus leaves early enough to reach its first stop 2 minutes before departure (modelled layover). It runs out of service, waits at the stop, then runs its first trip along the GTFS shape at the GTFS stop times.
- **Return:** the bus runs its last trip, waits 1 minute at the last stop (modelled), then deadheads in.
- **Route 13 at UW Station:** the Northfield return is the bus UW Station simulates. It sets down at stop 1223 at 45:00, leaves the station by Phillip Street at 46:06, and from there drives to Northfield. It is in the garage at 53:03.
- **States:**
  - a pulled-out bus: in garage → pulling out → layover → in service;
  - a returning bus: in service → returning → in garage.

Inventories (modelled: buses at each garage at 07:30) and the scenario's buses:

| Garage | 07:30 | Pull-out | Return | 08:30 |
| --- | --- | --- | --- | --- |
| Northfield Drive | 60 | GRT-393529 at 23:32 (59 left) | GRT-393598 at 53:03 (60) | 60 |
| Strasburg Road | 110 | GRT-393512 at 03:05 (109) | GRT-393510 at 54:59 (110) | 110 |
| Conestoga Boulevard | 40 | GRT-394447 at 25:07 (39) | GRT-393474 at 35:56 (40) | 40 |

Times are on the run's clock (minute 0 = 07:30).

## Modelled, not observed

| Item | Value |
| --- | --- |
| Block-to-garage assignment | nearest garage to the block's first or last stop |
| Inventories | 60 (Northfield), 110 (Strasburg), 40 (Conestoga Blvd) buses at 07:30 |
| Bus positions in the garages | one node per garage on its bus apron, chosen on the imagery |
| Layovers | 2 min at the first stop; 1 min at the last stop |
| Road speeds where no `maxspeed` is mapped | motorway 100, trunk 80, primary and secondary 60, tertiary and unclassified 50, residential 40, links 40–60, service 15 km/h |
| Turn penalty | 6 s per turn sharper than 45° |
| Hour | 07:30–08:30 GTFS time is minute 0–60 of the run |
