# Drivers, private cars and recovery through Control

Each simulated bus and train driver decides how to drive from what it can see. Drivers can make occasional, opt-in human mistakes. Control decides how each one recovers. Every movement on the map comes from the simulator's trace.

- Drivers and errors: `backend/drivers.py`.
- Phillip Street traffic: `backend/road_traffic.py`, with geometry from `tools/waterloo_geometry/build_uw_roads.py` → `backend/uw_roads.json` and `frontend/lib/geo/uw-roads.json`.
- Bus decisions and recovery: `backend/uw_station.py`.
- Train recovery: `backend/rail_ops.py`, with platform areas from `tools/waterloo_geometry/build_ion_platforms.py` → `backend/ion_platforms.json`.
- Playback: `frontend/lib/motion/uwStation.ts` and `frontend/lib/motion/ion.ts`. The Decision Inspector is `frontend/app/components/DecisionInspector.tsx`.
- Tests: `backend/tests/test_drivers.py` and `frontend/e2e/verify-drivers.mjs`.

## One clock, one occupancy model

The private-car pilot (`feat/uw-private-car-agents`, `docs/uw-road-agents.md`) runs Phillip Street in the browser on its own 0.5 s clock, so its cars never met the scheduled buses. This branch ports the pilot's lane rules to Python and runs them on UW Station's 1 s clock, inside the corridor simulation. Cars and buses now share one lane state, which the bus agents read and the map draws:

| Pilot rule (`road.ts` / `laneTraffic.ts`) | Here (`road_traffic.py`) |
| --- | --- |
| `followingSpeed`: headway, 1.8 m/s² acceleration, 4.0 m/s² braking | `following_speed`, using each driver's headway and pace |
| `mergeBlockers`: room for the whole bus ahead; behind, 2.4 m plus 3 s at the approaching vehicle's speed | `Phillip.merge_blockers`; the blocking cars are named in the decision |
| University Ave signal: stop at its line on red | the same 54 s cycle, red 11–34 s (the pilot's values, modelled) |
| Lanes `phillip-in` → `phillip-out`, merge from `phillip-right` | one southbound lane, 516 m; the merge is at 134 m and the stop line at 493 m, from the pilot's centrelines |

The modelled gap stream at the Phillip Street exit (600 vehicles/h, 6 s critical gap) is gone. A bus now turns out only when the actual cars leave it room.

The ION crossing, berth assignment, passenger transfers and dispatch are unchanged. The existing UW and rail tests still pass.

Car demand is a scenario input: light (250 cars/h), moderate (500) or heavy (900) southbound, with seeded arrivals. The map draws the simulator's cars on Phillip Street and hides the decorative traffic there, so no car is drawn twice.

## What a driver sees and decides

Each second, every driver observes:

- its lane;
- the next stop or turn;
- the vehicles around it;
- the crossing lights and the Phillip Street signal;
- the berth Control has granted.

It then follows, brakes, changes lanes, enters a berth, waits for a gap, or asks Control. Each decision records what the driver saw, the options it weighed, what it chose, any error, what Control allowed, and the effect on service. The Decision Inspector shows these in the panels **What it saw**, **What went wrong**, **What Control allowed** and **Effect on service**.

Drivers differ. Each profile is drawn once from the run's seed and the vehicle's ID (`Driver`). The ranges are modelled:

| Trait | Range | Effect |
| --- | --- | --- |
| Pace | 88–100 % of the speed limit | cruising speed on the roadway and on Phillip Street |
| Headway preference | 1.0–1.8 s | following distance |
| Reaction | 0, 1 or 2 s | delay before moving off once the way ahead opens |

## Opt-in human errors

Errors are off unless the scenario asks for them (`driver_error` in `/api/simulate`).

- **seeded**: every opportunity is drawn at `driver_error_rate` from `error_seed`. The same seed gives the same mistakes and the same decisions.
- **demo presets**: B101 makes one named error on its first UW visit.

| Error | Where | What happens next |
| --- | --- | --- |
| overshoot | stopping at a berth | the bus stops 3–9 m (demo: 6 m) past the stopping point. The doors stay closed, because the boarding area is more than 1.5 m away. The driver asks Control to reverse |
| missed_berth | turning into the platform 4 berth | the bus is still in the through lane. A reverse is not allowed outside a berth lane, so it continues and sets down at platform 6 instead |
| wrong_turn | leaving by Phillip Street | the bus turns left onto northbound Phillip Street. The turn is legal, but the plan was to turn right. Control re-plans from where the bus actually is |

The wrong-turn re-route is planned on OSM's directed road graph. It respects one-way streets, access tags and turn restrictions, and allows no U-turns except at dead ends. It runs Phillip Street → Columbia Street West → Lester Street → Hickory Street West → Albert Street → Young Street West → King Street North → King Street South, to the corridor stop at Waterloo Public Square (GRT 4048): 2.76 km.

Its posted-speed time is 4.5 min. It is slowed by the same congestion ratio the corridor's own UW → Waterloo Public Square leg has in the scenario, so a wrong turn is never a shortcut (modelled). The trip then continues from Waterloo Public Square.

## Bus recovery through Control

After an overshoot, the bus stops and asks Control to reverse. Control grants a reverse only if both of these hold:

1. **The area permits it.** Reversing is allowed only inside a platform's berth lane (a modelled rule; GRT practice unverified).
2. **The whole swept path can be reserved.** That path is from the bus's nose back to the berth's stopping point, plus 1 m. It must be clear of:
   - vehicles in the berth lane;
   - a bus behind that is entering this platform's berth lane;
   - any bus behind, inside the station, bound for this platform (modelled rule);
   - other reservations.

When Control grants the reverse, the path is reserved. The bus backs at 1 m/s and opens its doors only once it is within 1.5 m of the stopping point. Other buses do not enter the reserved path.

When Control denies it, the bus tries the alternatives in this order:

1. pull forward to a free berth ahead at the same stop;
2. set down at the next platform (riders who were to board here are not picked up);
3. record the missed stop (`metrics.missed_stops`) and continue when cleared.

## Train recovery through Rail Control

In a scenario (`rail_overshoot_station`, direction, minute, metres), a train overshoots a platform. It creeps past at 1.2 m/s and stops, and the driver asks for a recovery move. Rail Control takes 10 s to authorize (modelled). It checks:

- **Area.** No reverse along a street-running platform. A platform counts as street-running when the track runs along a GRT bus street at two or more of four points 20–40 m either side of it (`build_ion_platforms.py`). This is a *simulation assumption*: GRT/Keolis practice is unverified.
- **Occupancy.** No train in the block behind, over the swept path plus a margin.
- **Reservations.** No other train authorized into that block.
- **Single-track sections and switches.** The reverse must not foul a single-track section that another train is using, or a crossover set for a diversion.

When the reverse is granted, the train backs at 1.0 m/s to the stopping point, then opens its doors.

When it is denied, the train holds, or Rail Control authorizes a forward alternative (a *simulation assumption*): it continues to the next platform with its doors closed. The skipped station is recorded on the trip (`trip.skipped`), and its riders wait for the next train.

## Scenarios

The presets are on the map's control panel. All use seed 17.

| Preset | Shows |
| --- | --- |
| UW exit, heavy traffic | B101 waits 22 s at the Phillip exit, blocked by car-371 to car-376. In light traffic the same departure waits 4 s, blocked by car-126 |
| Bus overshoot, reverse granted | B101 overshoots P6-1078-1 by 6 m; reverse granted, swept path reserved |
| Bus overshoot, reverse denied | a route 9 bus behind is bound for platform 6, so Control denies the reverse and the missed stop at 1078 is recorded |
| Bus misses a berth | B101 misses the platform 4 berth and sets down at platform 6 |
| Bus takes the wrong turn | B101 turns left onto northbound Phillip and is re-routed to Waterloo Public Square |
| Train overshoot, reverse granted | LRV502 overshoots Research and Technology by 12 m; reverse granted, 32 s late there |
| Train overshoot on the street | LRV504 overshoots Waterloo Public Square: reverse denied (street-running), and it continues to Laurier-Waterloo Park with its doors closed |

## Effect on service

Default API scenario: 4 buses, traffic ×1.25, ION delay 4 min, seed 17. All rows are with control on.

| Run | ION transfers boarded | Riders boarded | Left waiting | Total rider-wait (min) | Avg transfer wait (min) | Missed stops |
| --- | --- | --- | --- | --- | --- | --- |
| main | 107 | 330 | 158 | 8970.1 | 14.91 | – |
| this branch, moderate cars | 107 | 330 | 158 | 9086.8 | 15.16 | 0 |
| light cars | 107 | 330 | 158 | 9046.8 | 15.16 | 0 |
| heavy cars | 107 | 330 | 158 | 9086.8 | 15.16 | 0 |
| overshoot, reverse granted | 107 | 330 | 158 | 9091.4 | 15.17 | 0 |
| overshoot, bus behind (denied) | 85 | 307 | 181 | 9521.6 | 14.85 | 1 |
| missed berth | 107 | 330 | 158 | 9002.4 | 14.90 | 0 |
| wrong turn | 107 | 330 | 158 | 9086.8 | 15.16 | 0 |
| seeded errors (rate 0.2, seed 17) | 107 | 330 | 158 | 9102.9 | 15.21 | 0 |
| train overshoot, Research and Technology | 107 | 330 | 158 | 9086.8 | 15.16 | 0 |
| train overshoot, Waterloo Public Square | 107 | 330 | 158 | 9087.3 | 15.17 | 0 |

How to read the rows:

- **main vs this branch.** UW departures are now measured past the University Ave signal, where the bus leaves the modelled roads, instead of at the Transit Plaza exit. Buses also wait for the actual cars. The first three UW exits move from 35:33 / 43:11 / 54:08 to 36:40 / 44:09 / 54:51. The resulting minute shifts downstream add 117 rider-minutes in total.
- **Traffic.** Heavy cars cost each departure a few seconds to 22 s. Those seconds do not cross the corridor's one-minute steps, so they barely move the corridor metrics.
- **Denied reverse.** This is the costly case. The 22 ION transfers waiting at stop 1078 are not picked up.
- **Wrong turn.** The re-route takes 589 s from the exit. B101 reaches Waterloo Public Square at 45:22, which is the same corridor minute (46) as on its normal leg, so there is no downstream effect in this scenario. Buses behind it are not held: once a bus's whole length is past the exit, it no longer occupies Transit Plaza Road.
- **Train recoveries.** A granted reverse costs that trip 32 s at the platform. The street-running skip leaves that station's riders for the next train. Neither changes the bus transfer counts in this scenario, and no following train is delayed.

## Modelled, not observed

| Item | Value |
| --- | --- |
| Car demand | 250 / 500 / 900 cars/h southbound on Phillip Street, seeded |
| Signal at University Ave | the pilot's 54 s cycle, red 11–34 s |
| Driver ranges | pace 88–100 %, headway 1.0–1.8 s, reaction 0–2 s |
| Error sizes | overshoot 3–9 m (seeded), 6 m (demo); train demo 12 m |
| Bus reversing | berth lanes only; 1 m/s; doors within 1.5 m of the stopping point |
| Bus behind blocks a reverse | any bus inside the station bound for the same platform |
| Wrong-turn timing | posted speeds slowed by the corridor leg's congestion ratio |
| Train recovery | creep 1.2 m/s, reverse 1.0 m/s, 10 s authorization |
| Street-running platforms | geometric test against GRT bus streets (simulation assumption) |
| Forward alternative | next platform with the doors closed (simulation assumption) |

Map data © OpenStreetMap contributors (ODbL). Contains information provided by the Regional Municipality of Waterloo under the Region's Open Data Licence (GRT GTFS).
