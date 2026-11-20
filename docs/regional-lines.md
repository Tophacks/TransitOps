# Scheduled lines: Route 7 King, end to end

Route 7 runs as a complete scheduled line in both directions, on the simulation's one-second clock, the same clock as UW Station's buses and cars. One physical bus works each block, with its own driver. What happens at one stop carries on to the next stops, to the riders' connections with ION, and to the bus's next trip.

- Data: `tools/waterloo_geometry/build_lines.py` → `backend/lines.json` and `frontend/lib/geo/lines.json`.
- Simulator: `backend/lines.py`, driven by `backend/simulation_engine.py`.
- Playback: `frontend/lib/motion/lines.ts`.
- Tests: `backend/tests/test_lines.py` and `frontend/e2e/verify-fleet.mjs`.

## Route 7 from GTFS (verified)

The data comes from GRT GTFS for Wednesday 2 December 2026, service `7-Weekday-1-2026_4-1111100`. That is the day the garages and UW Station use.

| | |
| --- | --- |
| Trips | 150 that day, on 9 blocks |
| Main variants | Conestoga Station (1130) → Fairway Station (3490): 40 stops, 14.4 km, 72 trips. Fairway → Conestoga: 39 stops, 14.2 km, 74 trips |
| Short turns | King / Sydney → Fairway (2 trips); Sydney / Charles → Conestoga (1); Conestoga → Frederick Station (1) |
| Layovers | 5–14 min at the terminals, from the block's trip times |
| Interlining | Route 7's blocks also run trips on routes 1, 6, 14, 21, 29 and 31 |

The builder checks four things:
- each trip's stops lie within 40 m of its shape, in order (the worst is 16.9 m);
- a block's trips never overlap in time;
- a stop is linked to an ION station only if it names the station and is within 300 m of one of its platforms (20 stops, 11 stations);
- a corridor stop is linked by GTFS stop ID: Waterloo Public Square 4048, Grand River Hospital 2540 and Central Station 1901.

Route, trip, stop and vehicle IDs stay distinct. A trip belongs to a block, and a vehicle is assigned to the block by the regional fleet ([regional-fleet.md](regional-fleet.md)).

GTFS gives whole minutes, so several stops in a row can share one. `sched` spreads each minute change over the stops since the previous change, in proportion to distance. The published times are kept alongside, in `gtfs`.

## What each bus does

| Situation | Decision | Rule |
| --- | --- | --- |
| Between stops | run the segment | scheduled time × 0.75 (the rest of the schedule is dwell), stretched by traffic relative to 1.25× (the scheduled condition), by the driver's pace, and by seeded variation (σ 0.08); never faster than 60 km/h |
| A stop with riders waiting or getting off | pull in | GRT stops on request; a stop with nobody is passed |
| Berth taken or blocked | wait for a berth | 1 berth at a street stop, 2 at a station (modelled); the decision names the buses holding them |
| Doors | board and alight | 3 s to open, 1.5 s per rider off, 2.5 s per rider on, 3 s to close; 70 riders at most (38 seated) |
| A station or terminal (timepoint) | hold rather than leave early | |
| End of a trip | lay over, then start the block's next trip | at least 3 min of recovery; the next trip leaves late if the bus arrived late, and dispatch records why |
| A block trip on a route that is not modelled | worked off the modelled network | the bus comes back with its lateness, less the slack in the scheduled layovers (modelled) |

When the run starts at 07:30, buses already under way are placed where the schedule has them, on time.

The running-time share of 0.75 is a calibration. It was chosen so that the median Route 7 trip at the default traffic reaches its terminal on schedule (median arrival +0.1 min, seed 17).

## Riders and connections

Riders are drawn once from the seed, so two policies see the same riders:

| Rider | Rate (modelled) |
| --- | --- |
| Walk-up | 4 an hour per direction at a street stop, 12 at a station; destinations further along the line, weighted 3:1 towards stations |
| From ION | on average 0.5 riders per train arrival per direction at each ION-linked stop, after the measured walk at 1.2 m/s plus 30 s |
| On to ION | 35 % of riders getting off at an ION-linked stop |

A rider continuing by ION catches or misses the train they planned on. The plan is the first train after the bus's scheduled arrival plus the walk. The rider actually catches the first train after the bus's actual arrival plus the walk.

## Delays carry forward (acceptance 1)

The scenario `line_block_stop` makes a stop's berths unavailable for a window. Blocking Central Station (1901) at 07:50, with seed 17:

- **For 10 minutes:** B109 waits 235 s at Central and is 204 s late there. It is still 210 s late at Fairway. Missed ION connections rise from 14 to 20.
- **For 20 minutes:** the same bus waits 835 s and leaves Fairway on its next trip 6.0 min late; the trip after that leaves 1.7 min late. Missed connections rise to 40. The bus behind catches up, and the two leapfrog at the stops (bunching), which no rule asks for.

A driver error, `missed_stop`, misses the lane change into the curb and passes a stop. The riders waiting there take the next bus. Riders who wanted to get off ride to the next stop. The trip continues from where the bus is.

## Results (120 min from 07:30, seed 17, default settings)

| Run | Trips covered | Departure delay avg / max | Terminal arrival delay avg | Missed stops | ION → bus transfers boarded | Connections to ION made / missed | Riders still waiting |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Route 7 | 16/16 | 18 s / 34 s | −2 s | 0 | 240 | 136 / 14 | 72 |
| Central blocked 07:50–08:00 | 16/16 | 18 s / 34 s | 9 s | 0 | 240 | 130 / 20 | 72 |
| Central blocked 07:50–08:10 | 16/16 | 45 s / 390 s | 77 s | 0 | 240 | 110 / 40 | 73 |
| Missed stop at Grand River Hospital | 16/16 | 18 s / 34 s | −2 s | 1 | 240 | 136 / 14 | 72 |
| Route 7 breakdown, spare from a garage | 16/16 | 32 s / 245 s | 48 s | 0 | 244 | 122 / 24 | 68 |
| Traffic 1.6× | 16/16 | 127 s / 599 s | 428 s | 0 | 239 | 37 / 110 | 78 |

## The next route through the same model

`build_lines.py --routes 7,31` builds any GTFS route into the same structure. `NextRouteTests` builds Route 31 in memory and runs it.

The remaining work for routes 31, 201 and 202 is verifying them against GTFS and adding the station operations they serve. That is the next PR.

## Modelled, not observed

| Item | Value |
| --- | --- |
| Run share of scheduled time | 0.75 (calibration) |
| Scheduled traffic | the API's 1.25× |
| Variation | log-normal σ 0.08 per segment, seeded |
| Berths | 1 per street stop, 2 per station |
| Dwell | 3 s doors, 1.5 s per alighting rider, 2.5 s per boarding rider, 3 s to close |
| Capacity | 70 riders (38 seated) |
| Minimum layover | 3 min |
| Demand | 4 / 12 riders an hour per direction (street / station); 0.5 per ION arrival; 35 % on to ION |
| Walk | 1.2 m/s plus 30 s from platform to street |

Contains information provided by the Regional Municipality of Waterloo under the Region's Open Data Licence (GRT GTFS bus and LRT feeds).
