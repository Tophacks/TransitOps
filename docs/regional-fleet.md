# The regional fleet and the spares dispatch sends

Every modelled vehicle has one identity across the inventory, the map and the dispatch logs. Each bus also has a garage, a state, an assignment and a driver. Only the buses active in the run drive. The others' status comes from their block's schedule, and is labelled that way.

- Inventory and dispatch: `backend/fleet.py`.
- Spare routes: `tools/waterloo_geometry/build_deadheads.py` → `backend/deadheads.json`.
- Fleet panel: `frontend/app/components/FleetPanel.tsx`; playback in `frontend/lib/motion/lines.ts`.
- Tests: `backend/tests/test_fleet.py` and `frontend/e2e/verify-fleet.mjs`.

## Inventory

| Fleet | Count | Source |
| --- | --- | --- |
| Conventional buses | 300 by default (`fleet_inventory`, 230–400) | grt.ca, About GRT: "more than 300 buses" |
| MobilityPLUS | 35, M01–M35, kept apart: never fixed-route capacity | the same page |
| ION light rail vehicles | 15 (`rail_ops.py`, Dutton yard) | the same page; PR #17 |

Conventional buses are numbered B101 onwards, garage by garage. These are modelled fleet numbers, not GRT's. The garage shares are modelled because GRT doesn't publish them: 45 % Strasburg / Chandler, 35 % Northfield Drive and 20 % Conestoga Boulevard, which is 135 / 105 / 60 of 300. The garages are the OSM sites verified in #18.

`fleet_size` is no longer read as the inventory. It is the number of buses assigned to this run's corridor service: Strasburg's first buses (B101, B102 …), staged at Victoria / Joseph.

## A day's assignments

1. Every one of the day's 215 GTFS blocks, across all routes, gets one bus from the garage nearest its first stop. If that garage has none left, the bus comes from the next nearest. This is modelled, as in #18.
2. A seeded 8 % of each garage's remaining buses is in maintenance.
3. The rest are ready spares. A bus held for a block that starts more than 3 hours later also counts as a ready spare. If it is sent out, its block passes to another spare, and the change is logged.

With 300 buses and seed 17, all 215 blocks are covered: up to 175 run at once around 08:00, 24 are in maintenance and 58 are spares.

## Spares on real roads

Relief points are every ION station stop on Route 7's two main variants, served in the line's direction, plus Central Station, where corridor buses enter service. `build_deadheads.py` routes from each garage to each relief point, back, and between relief points: 500 routes, all legal, on #18's road graph and rules.

| From | To relief points |
| --- | --- |
| Northfield Drive | 3.9–14.0 min (nearest Conestoga Station) |
| Strasburg Road | 3.9–11.0 min (nearest Borden Station) |
| Conestoga Boulevard | 12.7–19.3 min (nearest Fairway, by Hespeler Road, Highway 401 and Highway 8) |

When a spare is needed, dispatch compares the first ready spare of each garage. Each takes 5 min to sign on and leave (modelled), plus its route, and dispatch sends the one that arrives first. The decision lists every garage, with the reason any of them can't help.

## Breakdowns (acceptance 4)

`breakdown_kind: out_of_service` withdraws the bus instead of the original six-minute roadside hold. Its riders walk the remaining distance to the next stop at 1.2 m/s and wait there. They are not counted as boarding twice.

**On the corridor** (three assigned buses, breakdown at minute 15, seed 17): B101 breaks down. Dispatch compares B205 from Strasburg (6.7 min out of service) with B271 from Northfield (10.3 min); Conestoga Boulevard has no ready spare. B205 leaves Strasburg at 07:50 by Strasburg Road, Ottawa Street and Charles Street, and enters service at Central at 07:56. B205 was held for block 393428 at 13:45, so that block passes to B235. Strasburg ends the hour with one bus fewer in the garage and one more out, and every garage's in + out equals its allocation.

**On Route 7** (breakdown at minute 20): B246 breaks down 153 m before Borden Station, and its 16 riders walk on to Borden. B205 leaves Strasburg at 07:55, is at Borden at 07:59, picks up 46 riders and carries on with the same trip, 9.4 min late.

| Corridor, 60 min, three buses, seed 17 | Riders boarded | ION transfers boarded | Left waiting | Total rider-wait (min) |
| --- | --- | --- | --- | --- |
| No breakdown | 293 | 90 | 195 | 9425.2 |
| Roadside hold at minute 15 | 253 | 70 | 235 | 9784.4 |
| Withdrawn at minute 15, spare from Strasburg | 233 | 78 | 271 | 10210.1 |

## Dispatcher orders (the Fleet panel)

An order is an input to the run, `{minute, action, vehicle, target}`. A seed and its orders replay exactly. Each order is checked, and the result is recorded with the reasons:

| Order | Checks | If accepted |
| --- | --- | --- |
| `dispatch_spare` to the corridor, or to a Route 7 block | the bus is a ready spare (MobilityPLUS refused; buses in service or held for a block refused); a legal road route exists from its garage | it signs on, deadheads and joins the corridor at Central, or relieves the block's bus at the next relief point, where the relieved bus waits for it, the riders change buses and it returns to its garage |
| `reassign` a Route 7 bus to the corridor | the bus is in service on a modelled line; a road route exists | it leaves the line at its next relief point, where its riders wait for the next bus; the order lists the trips the block loses |
| `breakdown` | the bus is in service in this run | as above |

## Modelled, not observed

| Item | Value |
| --- | --- |
| Garage shares | 45 / 35 / 20 % (Strasburg / Northfield / Conestoga Boulevard) |
| Block → garage | nearest to the block's first stop, else the next nearest |
| Maintenance | 8 % of each garage, seeded |
| Spare horizon | a bus held for a block more than 3 h away counts as a ready spare |
| Sign-on and leave | 5 min |
| Road speeds | mapped `maxspeed`, else by road class, plus 6 s per turn sharper than 45° (#18) |
| Schedule-only buses | pull-out and return estimated at 30 km/h over 1.4 × the straight-line distance |
| Walk after a breakdown | 1.2 m/s |

Map data © OpenStreetMap contributors (ODbL). Contains information provided by the Regional Municipality of Waterloo under the Region's Open Data Licence (GRT GTFS).
