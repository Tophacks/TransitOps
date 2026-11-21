# Scheduled lines: Route 7 King, end to end

Route 7 runs as a complete scheduled line in both directions, on the simulation's one-second clock, the same clock as UW Station's buses and cars. One physical bus works each block, with its own driver. What happens at one stop carries on to the next stops, to the riders' connections with ION, and to the bus's next trip.

- Data: `tools/waterloo_geometry/build_lines.py` → `backend/lines.json` and `frontend/lib/geo/lines.json`.
- Simulator: `backend/lines.py`, driven by `backend/simulation_engine.py`.
- Driving: `backend/line_driver.py`. How a bus accelerates, brakes, stops and pulls out is in [route7-driving.md](route7-driving.md).
- Playback: `frontend/lib/motion/lines.ts`.
- Tests: `backend/tests/test_lines.py`, `backend/tests/test_route7_driving.py`, `frontend/e2e/verify-fleet.mjs` and `frontend/e2e/verify-route7-driving.mjs`.

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
- each trip's stops lie within 40 m of its shape, in order (the worst is 16.2 m, after smoothing);
- a block's trips never overlap in time;
- a stop is linked to an ION station only if it names the station and is within 300 m of one of its platforms (20 stops, 11 stations);
- a corridor stop is linked by GTFS stop ID: Waterloo Public Square 4048, Grand River Hospital 2540 and Central Station 1901.

Route, trip, stop and vehicle IDs stay distinct. A trip belongs to a block, and a vehicle is assigned to the block by the regional fleet ([regional-fleet.md](regional-fleet.md)).

GTFS gives whole minutes, so several stops in a row can share one. `sched` spreads each minute change over the stops since the previous change, in proportion to distance. The published times are kept alongside, in `gtfs`.

## What each bus does

| Situation | Decision | Rule |
| --- | --- | --- |
| Between stops | drive the segment | the corridor buses' motion ([route7-driving.md](route7-driving.md)), at a cruising cap fitted to scheduled time × 0.68 (the rest of the schedule is dwell); stretched by traffic relative to 1.25× (the scheduled condition), by the driver's pace and by seeded variation (σ 0.08); a late driver tries to recover half the lateness on each segment |
| A stop with riders waiting or getting off | pull in | GRT stops on request; a stop with nobody is passed |
| Berth taken or blocked | wait for a berth | 1 berth at a street stop, 2 at a station (modelled); the decision names the buses holding them |
| Pulling out where traffic is modelled | wait for a gap | King / KCI (curbside) and the Fairway bay exit, by the UW Station lane rules; the decision names the cars |
| Doors | board and alight, once the bus is stationary | 1 s after stopping, 3 s to open, 1.5 s per rider off, 2.5 s per rider on, 3 s to close; 70 riders at most (38 seated) |
| A station or terminal (timepoint) | hold rather than leave early | |
| End of a trip | lay over, then start the block's next trip | at least 3 min of recovery; the next trip leaves late if the bus arrived late, and dispatch records why |
| A block trip on a route that is not modelled | worked off the modelled network | the bus comes back with its lateness, less the slack in the scheduled layovers (modelled) |

When the run starts at 07:30, buses already under way are placed where the schedule has them, on time.

The running-time share of 0.68, with the half catch-up, is a calibration. Towards Conestoga the median trip reaches its terminal on schedule (+0.1 min, seed 17). Towards Fairway it arrives +3.2 min late, because the scheduled run times there are shorter than the motion rules allow; the layover absorbs it ([route7-driving.md](route7-driving.md#calibration)).

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

- **For 10 minutes:** B109 waits 278 s for a berth at Central and leaves 218 s late. With the extra time the motion rules need towards Fairway, it reaches Fairway 434 s late, and the layover absorbs most of it: its next trip leaves 24 s late. Missed ION connections rise from 23 to 31.
- **For 20 minutes:** the same bus waits 878 s and leaves Central 833 s late. It leaves Fairway on its next trip 11.7 min late and reaches Conestoga 15.1 min late, and its trip after that (4223676, due 09:20) does not leave before the run ends. Missed connections rise to 60.

A driver error, `missed_stop`, misses the lane change into the curb and passes a stop. The riders waiting there take the next bus. Riders who wanted to get off ride to the next stop. The trip continues from where the bus is.

## Results (120 min from 07:30, seed 17, default settings)

| Run | Trips covered | Departure delay avg / max | Terminal arrival delay avg | Median arrival towards Conestoga / Fairway | Missed stops | ION → bus transfers boarded | Connections to ION made / missed | Riders still waiting |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Route 7 | 16/16 | 18 s / 34 s | 106 s | +0.1 / +3.2 min | 0 | 242 | 127 / 23 | 65 |
| Central blocked 07:50–08:00 | 16/16 | 18 s / 34 s | 118 s | +0.1 / +3.3 min | 0 | 242 | 119 / 31 | 65 |
| Central blocked 07:50–08:10 | 15/16 | 60 s / 699 s | 238 s | +0.1 / +3.6 min | 0 | 242 | 90 / 60 | 74 |
| Missed stop at Grand River Hospital | 16/16 | 18 s / 34 s | 106 s | +0.1 / +3.0 min | 1 | 242 | 128 / 22 | 65 |
| Route 7 breakdown, spare from a garage | 16/16 | 52 s / 387 s | 166 s | +0.0 / +2.9 min | 0 | 243 | 104 / 42 | 67 |
| Traffic 1.6× | 16/16 | 18 s / 34 s | 162 s | +1.5 / +3.9 min | 0 | 242 | 103 / 43 | 68 |
| Light shared traffic | 16/16 | 18 s / 34 s | 105 s | +0.1 / +3.2 min | 0 | 242 | 127 / 23 | 65 |
| Heavy shared traffic | 16/16 | 18 s / 34 s | 107 s | +0.1 / +3.4 min | 0 | 240 | 127 / 23 | 69 |

With the 20-minute block, B109's trip 4223676 (due 09:20) leaves after the run ends, so 15 of 16 trips are covered.

At 1.6× traffic the buses arrive later, but departure delays do not change: the delay is less than the layover at each terminal.

## The next route through the same model

`build_lines.py --routes 7,31` builds any GTFS route into the same structure. `NextRouteTests` builds Route 31 in memory and runs it.

The remaining work for routes 31, 201 and 202 is verifying them against GTFS and adding the station operations they serve. That is the next PR.

## Modelled, not observed

| Item | Value |
| --- | --- |
| Run share of scheduled time | 0.68, catching up half the lateness per segment (calibration) |
| Scheduled traffic | the API's 1.25× |
| Variation | log-normal σ 0.08 per segment, seeded |
| Berths | 1 per street stop, 2 per station |
| Dwell | 3 s doors, 1.5 s per alighting rider, 2.5 s per boarding rider, 3 s to close |
| Capacity | 70 riders (38 seated) |
| Minimum layover | 3 min |
| Demand | 4 / 12 riders an hour per direction (street / station); 0.5 per ION arrival; 35 % on to ION |
| Walk | 1.2 m/s plus 30 s from platform to street |

Contains information provided by the Regional Municipality of Waterloo under the Region's Open Data Licence (GRT GTFS bus and LRT feeds).
