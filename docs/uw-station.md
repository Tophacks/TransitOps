# UW Station: bus agents, station reports and predictions

University of Waterloo Station is simulated in one-second steps on its real
roadway. Each bus is an agent: dispatch assigns its trip, and the bus decides
how to carry it out from what it can see. Every movement drawn on the map comes
from the simulator's trace (`backend/uw_station.py`).

- Geometry: `tools/waterloo_geometry/build_uw_station.py` → `backend/uw_station.json`
  and `frontend/lib/geo/uw-station.json`.
- Simulator: `backend/uw_station.py`, driven by `backend/simulation_engine.py`.
- Playback: `frontend/lib/motion/uwStation.ts`.
- Tests: `backend/tests/test_uw_station.py` and `frontend/e2e/verify-uw.mjs`.

## The station, from the sources

**Transit Plaza Road** (OSM ways 1040958965 … 1040958964): bus-only (`access=no`, `bus=yes`), two travel lanes, two-way, 321 m from the Ring Road all-way stop to Phillip Street.

**Platform edges:** OSM maps them 7.2–7.5 m from the centreline. So each side has a curb berth lane outside its through lane. The lane widths (3.5 m) are modelled.

**Stops and platforms** come from GRT GTFS `platform_code`. The builder checks each against the OSM stop pole on the same platform. GRT's station map (`uw-station.pdf`) shows the same stops on the same sides.

| Platform | Side (direction of travel) | Stops | Berth positions |
| --- | --- | --- | --- |
| 3 | north-west (south-west-bound) | 1260, 1262 | 4 |
| 4 | south-east (north-east-bound) | 1223 | 3 |
| 5 | north-west | 1264 | 3 |
| 6 | south-east | 1078 | 2 |

Berth positions run back from each stop pole, which marks the front of the first bus. Each position is a 12.2 m bus plus 6 m of room to swing out past the bus in front (modelled). They end at the next pole or the end of the platform.

**Unload-only calls.** GTFS marks drop-off-only calls with `pickup_type=1`. None of the platforms is unload-only for every route, so the drop-off-only calls are kept per route. In the 08:00–09:00 hour:

| Route | Drop-off-only stop | Platform |
| --- | --- | --- |
| 13 | 1223 | 4 |
| 30 | 1078 | 6 |
| 31 | 1260 | 3 |
| 9 | 1262 | 3 |
| 19 | 1264 | 5 |

GO Transit's unload platform (OSM `GO 7`) is mapped but not used, because GO coaches are not simulated.

**Controls on the roadway:**

- the ION level crossing at 26–33 m, with lights and gates (OSM);
- the all-way stop where the service road crosses mid-station (OSM node 8001833438);
- the all-way stop at Ring Road;
- the exit onto Phillip Street, which has no traffic control mapped.

**Other GRT buses** follow every GTFS call at these stops from 08:00 to 09:00 on Wednesday 2 December 2026: 35 trips and 31 vehicle visits. Run minute 0 is taken as 08:00 (modelled). When consecutive trips of one block call at the same stop, the bus stays on the berth through the layover between them.

## The corridor bus's trip at UW

Dispatch assigns the trip when the bus reaches the Ring Road entrance:

1. set down at platform 4 (stop 1223), where GRT routes arriving the same way set down;
2. load at platform 6 (stop 1078);
3. leave by Phillip Street.

The corridor leg is timed to the station's first stop. So the bus enters the roadway the free-flow time (22 s) before that minute.

## What the bus agent decides, and when

The agent re-plans every second from what it can observe: the vehicles in its lanes, the crossing lights, the all-way stops, the berths station control grants it, and the cars on Phillip Street.

| Situation | Decision | Rule (modelled values) |
| --- | --- | --- |
| A vehicle ahead in its lane | slow and follow | 2.5 m standstill gap; each driver's own headway (1.0–1.8 s), pace and reaction ([driver-agents.md](driver-agents.md)); berthed buses are in another lane and do not block it |
| ION crossing lights flashing | stop at the stop line, 6 m before the rails | lights flash while a train's nose is within 260 m until its tail is 50 m past (the map's gate rule), from the rail simulator's train positions; a bus that cannot stop in time continues |
| All-way stop | full stop, 2 s, then go when the junction is clear | |
| Berth | ask station control for its stop's front berth; if occupied or blocked, a compatible alternate (another position at the same stop) | station control grants only a berth the bus can reach: free, nothing stopped behind it in the berth lane, its taper (15 m) still ahead, and no vehicle waiting in the through lane between the bus and the berth. A bus that has to wait does so before the platform, never inside the junction |
| Pull-out | leave only with the through lane clear | beside it and just ahead for its whole length, and no vehicle reaching it within 4 s behind; the bus in the berth ahead matters until the two footprints stop overlapping sideways (12.2 m × 2.6 m) |
| Exit | yield to Phillip Street traffic | the simulated cars on Phillip Street's southbound lane: room for the whole bus ahead, and 2.4 m plus 3 s at the approaching car's speed behind ([driver-agents.md](driver-agents.md)) |

Each decision is recorded once, when it starts, with the options it weighed. Its outcome, including how long the bus waited, is added when it ends. The map's Decision Inspector shows them under **UW Station**.

## Station reports

Every minute, UW and Central report:

- the **confirmed queue**: riders already able to board, including ION transfer riders;
- **predicted transfer arrivals** over the next 10 minutes;
- the **buses coming**, with their free seats;
- **blocked berths** (UW).

From these the station recommends either a bounded hold of up to 3 minutes for a bus at the stop with free seats, or extra capacity when riders exceed the seats coming by more than 10. Each new recommendation is a decision record. Dispatch answers it:

- **Holds:** the connection-hold decision at the doors' closing second. It weighs transfer rider-minutes saved against the delay to riders onboard and waiting downstream, and its trigger cites the station's recommendation.
- **Capacity:** release a reserve bus now, or keep the scheduled release. A bus released from the yard reaches Central first, so it can answer only a request from a station it reaches within the 10-minute horizon. In practice that is Central. Spacing and reserve rules still apply.

## Predictions use only what is known at the time

The hold decision used to read the rail run's actual future arrival times. It
now uses `IonPlan.predict_arrival(trip, station, now)`:

- **Confirmed:** the train is already at the station.
- **Predicted, train under way:** the last station it has reached, plus the timetabled running time from there.
- **Predicted, not yet departed:** its scheduled departure plus any announced hold, plus the running time.

Rider counts per train are the modelled transfer demand. After a hold, the
decision's outcome compares the prediction with what happened. For example, at
Central with the default scenario: "ION-N0 riders eligible 22:01 (predicted
22:00)".

## Comparison: policies off and on

Default scenario: 3 buses, traffic 1.15×, 110 transfer riders, a 4-minute train delay, seed 17.

- **Policies off** (the fixed schedule): no station reports, holds or capacity requests.
- **Policies on:** those, plus the adaptive controller.

Agent behaviour on the roadway is the same in both. It is driving, not policy.

| Scenario | Policies | UW departures | Mean time in station | Corridor buses stopped (s) | Other GRT buses stopped (s) | Transfers boarded (of 110) | Riders still waiting | Onboard delay from holds (rider-min) | Total waiting (rider-min) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| no blockage | off | 3 | 170 s | 40 (follow 6, merge 25, stop 9) | 251 | 69 | 176 | 0 | 9008 |
| no blockage | on | 3 | 176 s | 46 (follow 4, merge 33, stop 9) | 251 | 84 | 167 | 28 | 7978 |
| platform 6 front berth blocked 10–58 | off | 3 | 170 s | 26 (follow 12, merge 5, stop 9) | 274 | 69 | 176 | 0 | 9005 |
| platform 6 front berth blocked 10–58 | on | 3 | 173 s | 24 (follow 10, merge 5, stop 9) | 274 | 84 | 167 | 28 | 7975 |
| Phillip St exit blocked 10–58 | off | 3 | 1234 s | 552 (follow 494, merge 32, stop 9, wait for berth 17) | 16982 | 37 | 290 | 0 | 10323 |
| Phillip St exit blocked 10–58 | on | 3 | 1214 s | 479 (follow 421, merge 32, stop 9, wait for berth 17) | 16982 | 61 | 281 | 28 | 9125 |

**Blocked berth.** All three corridor buses (B101 at 30:32, B102 at 39:33 and B103) find platform 6's front berth held by the out-of-service bus. Each asks for and takes the rear berth. The station costs them almost nothing, because the alternate berth works.

**Blocked exit.** Buses finish loading and wait at the berth with their doors closed, and the queue backs up behind them. Leaving by Ring Road is listed and rejected: there is no space to turn a 12.2 m bus on the two-lane roadway.

**Policies on.** Holds board 15 more transfer riders (24 with the exit blocked) for 28 rider-minutes of onboard delay.

The fixed controller's "adaptive must not be worse" check now compares total waiting rider-minutes. It also allows riders left waiting to rise by at most one on average. See `tests/test_controller_policy.py`. With the station modelled, the event preset leaves 1.0 more rider waiting on average while total waiting falls.

## Modelled, not observed

| Item | Value |
| --- | --- |
| Speed on the roadway | 25 km/h; 1.0 m/s² acceleration, 1.5 m/s² braking |
| Gaps | 2.5 m standstill, 1.0–1.8 s headway per driver; 4 s clearance behind for a pull-out |
| Berths | 12.2 m bus, plus 6 m of swing-out room between stacked positions; 15 m taper in, 6 m out |
| Doors | open 4 s after stopping; set-down 1.5 s per rider (at least 8 s); loading dwell 60 s as on the corridor; doors close 5 s before pulling out |
| Phillip Street | 250 / 500 / 900 cars/h southbound (light / moderate / heavy, seeded); the pilot's signal cycle at University Ave |
| Other GRT buses | 12.2 m each; 20 s minimum dwell where GTFS gives none |
| Corridor buses' berth plan | set down on platform 4, load on platform 6 |
| Minute 0 | 08:00 on a GTFS Wednesday |
| Blockages | scenario inputs |
