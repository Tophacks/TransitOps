# UW Station lane pilot

Traffic on **/uw-traffic** moves through a directed lane graph. The map draws the same lane centerlines and vehicle footprints used by the simulator. Start it from the **UW Station traffic** link on the corridor homepage or run `npm run dev` from `frontend/` and open the route. The pilot has its own clock and road state.

## Source-backed topology

OpenStreetMap objects and the Region's 2026 aerial imagery were checked on November 19, 2026. The GRT station diagram confirms stop 1223 on the south curb and shows where the transit roadway joins Ring Road and Phillip Street. The regional image shows the paved connections and station curbs. Lane centerlines are approximate offsets from mapped road centerlines. [GRT station diagram](https://media-003-ca.cdn.govstack.com/regionofwaterloo-018-ca/media/z1vbothz/uw-station.pdf) · [Regional aerial crop](https://gis.regionofwaterloo.ca/waimagery/rest/services/Imagery_2026/ImageServer/exportImage?bbox=-80.5423,43.47285,-80.53785,43.47532&bboxSR=4326&imageSR=4326&size=1400,1080&adjustAspectRatio=false&format=jpg&f=image) · [Region maps portal](https://www.regionofwaterloo.ca/government-and-council/about-waterloo-region/community-profile/maps/)

| Lane IDs | Enabled movement and controls | Evidence |
| --- | --- | --- |
| `phillip-in` → `phillip-out` | Cars and buses may continue southbound in the same through lane. Stop at red before University Avenue. | OSM [approach](https://www.openstreetmap.org/way/1451375463), [receiving road](https://www.openstreetmap.org/way/1455699342) and [signal](https://www.openstreetmap.org/node/114158636). The aerial crop shows the through alignment across the terminal junction. |
| `ring-entry` → `berth-1223` | Eastbound bus entry from the Ring Road junction, then straight into the curbside berth. Entry begins stopped at the junction boundary. | OSM [all-way stop](https://www.openstreetmap.org/node/298025667), [west access](https://www.openstreetmap.org/way/1040958965), [two-way Transit Plaza Road](https://www.openstreetmap.org/way/137281106) and [GRT route 30](https://www.openstreetmap.org/relation/13671507). The roadway is marked `access=no`, `motor_vehicle=permit`, `bus=yes`. Private cars are excluded. |
| `berth-1223` → `plaza-east` | Eastbound dwell at stop 1223 in the bus lane. A bus stops before continuing through the internal crossroad. | GRT station diagram, aerial crop, OSM [stop 1223](https://www.openstreetmap.org/node/9536179464), [station roadway](https://www.openstreetmap.org/way/1361353507) and [internal all-way stop](https://www.openstreetmap.org/node/8001833438). |
| `plaza-east` → `phillip-right` → `phillip-out` | Eastbound buses turn right into the southbound lane at the terminal mouth. The bus yields to through traffic. | OSM [exit](https://www.openstreetmap.org/way/1040958964), [merge node](https://www.openstreetmap.org/node/298025663) and [GO 25 departure](https://www.openstreetmap.org/relation/20750580), which follows the exit onto the southbound receiving road. The aerial crop corroborates the curb opening. |

The fixture connects these movements into one trip. The model uses conservative yield control at the Phillip exit because the available sources do not resolve the exact sign or stop-line placement. The University's red/green timing, stop-line setback, speed settings and berth dwell are simulation parameters. The west gate begins at the Ring Road junction with a one-second stop. Ring Road traffic, the ION crossing and the station's internal cross traffic require external dispatch or interlocking before this pilot is used for real bus movements. Westbound station lanes, left turns, passing, other berths and other exits have no transitions in the pilot. No public road stop or separate public bay is assumed at UW.

## Runnable scenarios

| Scenario | How to run | Seed 42 result |
| --- | --- | --- |
| UW Station | Set **0.55×**, then **Restart**. Repeat at **2.50×** with the same seed. | Light traffic enters at 97 s. Heavy traffic enters at 116 s and records 17 s waiting, with blocker IDs beginning at `car-21`. Lowering demand during the heavy-run wait advances the merge. |
| Synthetic · shared travel lane | Select the scenario at **2.50×**. | Cars queue behind the dwelling bus and resume when it pulls away. |
| Synthetic · separate pullout bay | Select the scenario at **2.50×**. | Cars continue through while the bus dwells in the bay. The bus yields when rejoining. |

Run `npm run test:motion` from `frontend/` with Node 24. The same scenario, seed and demand inputs on the same ticks replay identically.

## Integration note for Claude

- Use the **same** `LaneTraffic` instance for buses and cars. Register a bus with `addVehicle({ id, kind: "bus", route: UW_BUS_ROUTE, front, length, speed })`. `front` is the nose in metres along the first lane. A blocked spawn returns `false` and can be retried. Invalid lane sequences throw.
- Before each 0.5 s tick, read `occupancy(laneId)` and issue `setMotion(id, { stopAt: { laneId: "berth-1223", front: UW_BERTH_FRONT } })` for dwell. Send `setMotion(id, {})` to release, or include a `speedLimit` in m/s. Replace the fixture's orders in `UwTraffic` with these decisions. The run loop calls `step(redLaneIds)` once per tick.
- Let the lane engine advance positions and commit transitions. `frame().vehicles` gives each nose lane, longitudinal position, speed and total length. `occupancy(laneId)` gives the occupied bumper interval on that lane. During a transition the same ID appears on each lane still covered by the body. `frame().waiting` and `mergeEvents()` give reason and blocking IDs. The reservation releases when the rear clears the turn connector.
