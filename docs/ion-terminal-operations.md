# ION terminal operations (simulator Step 1)

The Python simulator runs ION with physical trains that turn back through the
real trackwork at Fairway and Conestoga. Three levels of control make the
decisions, and every decision is recorded and replayed on the map.

- Simulator: `backend/rail_ops.py` (with `backend/ion.py` for the timetable)
- Terminal geometry: `tools/waterloo_geometry/build_terminals.py` →
  `backend/ion_terminals.json` and `frontend/lib/geo/ion-terminals.json`
- Map playback: `frontend/lib/motion/ion.ts`, `terminals.ts`, `profile.ts`
- Decision Inspector: `frontend/app/components/DecisionInspector.tsx`
- Comparison: `python3 backend/compare_rail.py`

## Control levels

| Level | Owns | Decides |
| --- | --- | --- |
| Vehicle agent (`Train`) | Persistent vehicle ID (`LRV501`…), separate from trip IDs (`ION-N3`). Position, speed, footprint (41.8 m, five sections), current and next trip, schedule deviation, and the planned movement (line runs, terminal moves, stands). | Approaching a terminal it asks for a route 400 m before the home signal. Told to wait, it stops at the signal, or 15 m behind a train already waiting there. Held at a platform, it stays and tells dispatch. |
| Station control (`StationControl`) | Each terminal's interlocking: reservations and occupancy per resource (switches, straight and crossover legs, the diamond, the platform tracks). | Grants a move only when none of its resources is held by another train. Reservations start at the grant. Each resource is released when the train's rear has cleared it by the fouling distance. Occupancy (nose in to rear out) is recorded separately. Platforms are tried in preference order. |
| Dispatch (`RailOps.review` / `assign`) | Trip assignment across trains, departure spacing. | Assigns outbound trips first in, first out. Holds a departure for the timetable, the turnaround, 3-minute spacing and timetable order. When a departure is threatened it may give the trip to another train already ready at the terminal. |

### Priority and tie-breaking (deterministic)

1. Events at the same instant run in this order: resource releases, then
   departure attempts, then scheduled-departure checks, then approaches, then
   route requests. Ties are broken by insertion order.
2. When space is released, waiting departures are served before waiting
   arrivals, because a departure frees a platform. Departures go in order of
   their trip's scheduled time, then vehicle ID.
3. Approaching trains are served first come, first served. They share the
   running track and cannot overtake.
4. Platforms are tried in a fixed order:
   - Fairway: platform 1 (straight in), then platform 2 (over crossover 754590786).
   - Conestoga: platform 2 (straight in), then platform 1 (over crossover 836238401).

   The alternate platform is used only through a mapped route.

The same scenario and seed give the same decisions. The tests compare two
runs byte for byte, and the browser check scrubs backwards and compares.

### Dispatch reassignment rule

At a trip's scheduled departure time, and when a train is held at a platform,
dispatch estimates when the assigned train can leave. It takes the latest of:
the timetable, the turnaround, the hold, spacing and timetable order.

It gives the trip to another train only if all of these hold:

- the other train is standing at the same terminal;
- the other train's own next trip leaves later;
- neither trip has a scenario hold;
- the swap gets the trip out at least 2 minutes earlier (`SWAP_GAIN_S`).

The two trains then exchange trips. The rejected options are recorded with
their reasons, including "send a spare", which is never feasible yet: the
Dutton Drive yard is not connected to the simulated track.

## Terminal trackwork and moves

| Terminal | Crossover (OSM) | Diamond | Platform tracks (OSM) | Speeds |
| --- | --- | --- | --- | --- |
| Fairway | 754590785, 754590786 | 7049507419 | 1290108394 + 836238397 (1), 836238396 + 1223195057 (2), ending at buffer stops | 25 km/h through the interlocking (modelled, no maxspeed tagged) |
| Conestoga | 836238400, 836238401 | 7805105214 | 836238399 (1), 836238398 (2), ending at their mapped stop positions | 10 km/h (mapped `maxspeed=10` on the crossover, straight and platform ways) |

At both terminals a platform is reached straight in and left over a crossover
branch, or the other way round. Two moves conflict when they share a resource.
The only compatible pair is the straight-in arrival with the straight-out
departure. Conflicts in `ion_terminals.json`:

- Fairway: `arrive:1 × arrive:2, depart:1`; `arrive:2 × arrive:1, depart:1, depart:2`; …
- Conestoga: `arrive:1 × arrive:2, depart:1, depart:2`; `arrive:2 × arrive:1, depart:2`; …

Each move is one path from the neighbouring station (Block Line or
Northfield). The GTFS line path is joined onto the OSM running track at a
"handover" point, chosen where the two run within 2.5 m and 6 degrees of each
other, 120–480 m before the first switch. The trackwork follows from there.
On the map the GTFS line rails stop at the handovers, and the terminal
trackwork is drawn from OSM.

## Modelled, not observed

| Item | Value | Why it is modelled |
| --- | --- | --- |
| Minimum turnaround | 4 min from stopping to departing | No published ION procedure |
| Cab change | lights off 40 s after stopping, on at the other end 100 s later | No published procedure |
| Platform preference | straight-in platform first | Keeps the crossover free for departures. Actual practice was not verified (no footage checked) |
| Home signal | 30 m before the first switch | OSM maps departure signals at Conestoga (`CSM 2S/4S`), not home signals |
| Route request point | 400 m before the home signal | Lets a clear route avoid braking; not a real interlocking timing |
| Stopping point | 7 m short of the track end | Not mapped |
| Approach speed | 12 m/s from the handover to the home signal | Not mapped |
| Swap threshold | 2 min | Policy choice |
| Fleet | sized from the actual round trip: 11 trains at an 8-minute headway | Real fleet is 14 LRVs (spares and maintenance not modelled) |
| Vehicle IDs | `LRV501…` | Illustrative, not real fleet assignments |
| Platform hold | the train at (or next to stop at) the platform cannot leave until the hold ends | Scenario input ("door fault") |

## Rail-to-bus connections (seconds, not minutes)

Transfer riders become eligible to board at the exact second their train
stops, plus a walk to the bus stop: 60 s at Central and at UW (modelled). A bus
reaching a stop at minute M:

- opens its doors at M:00;
- boards everyone already eligible;
- keeps boarding riders who become eligible while it dwells;
- closes its doors at M:55 and pulls out at M+1:00 (`DOOR_CLOSE_S` = 5 s).

The simulator no longer rounds a rider's arrival down to the minute. The old
model did, which let riders board up to 59 s before their train arrived.
`tests/test_transfer_boarding.py` checks the boundary at 1 s before and 1 s
after the doors close, the moment of closing itself, riders already waiting,
and that no transfer rider boards before their train stops.

## Comparison

`python3 backend/compare_rail.py` uses the page's default scenario: traffic
1.15×, a 4-minute scenario delay on the transfer-wave trains, 110 transfer
riders, 3 buses and seed 17. The adaptive bus controller runs in every case.

| Hold | Rail policy | Late departures (>1 min) | Missed (>3 min) | Avg / max departure delay | Signal queueing | Trips reassigned | Transfer riders reaching the bus late | Rider-minutes of transfer delay | Avg transfer wait for the bus | Avg boarding wait |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| no hold | holds only | 2 | 1 | 24 s / 4.0 min | 0 s | 0 | 0 | 0 | 22.23 min | 22.18 min |
| no hold | dispatch | 2 | 1 | 24 s / 4.0 min | 0 s | 0 | 0 | 0 | 22.23 min | 22.18 min |
| Fairway platform 2, 12 min from 20:00 | holds only | 4 | 2 | 68 s / 8.0 min | 100 s | 0 | 3 | 19 | 21.50 min | 22.08 min |
| Fairway platform 2, 12 min from 20:00 | dispatch | 3 | 2 | 36 s / 4.0 min | 0 s | 1 | 2 | 6 | 22.23 min | 22.21 min |
| Conestoga platform 1, 14 min from 20:00 | holds only | 5 | 4 | 132 s / 14.0 min | 473 s | 0 | 6 | 54 | 21.90 min | 22.08 min |
| Conestoga platform 1, 14 min from 20:00 | dispatch | 4 | 3 | 52 s / 4.0 min | 0 s | 2 | 4 | 14 | 22.39 min | 22.17 min |

With no hold, dispatch has nothing to reassign, so the two rows are identical.

**A local decision that changes a downstream outcome.** In the Fairway case:

1. LRV501 is held at platform 2.
2. LRV503 arrives on platform 1, because the held platform is occupied.
3. At 24:00 dispatch gives LRV501's trip ION-N3 to LRV503.
4. ION-N3 leaves at 27:00 instead of 32:00.
5. It reaches Central and UW 5.0 minutes earlier.

In the Conestoga case, two swaps get ION-S2 and ION-S3 to UW and Central
10.5 and 5.5 minutes earlier.

The swapped trips carry one transfer rider each, so the effect on bus waits is
small. It also goes either way, because buses fill in queue order: an earlier
rider can take a seat that a wave rider would otherwise have had.

### The two transfer waves, at exact times (no hold)

| Wave | Train stops | Riders eligible | Buses at that stop | Outcome |
| --- | --- | --- | --- | --- |
| ION-N0 (LRV506) at Central, 30 riders | 21:00.5 | 22:00.5 | B103 doors 20:00–20:55, departs 21:00; next bus B101 at 53:00 | **Missed connection.** The train stops 5.5 s after B103's doors close. The 30 riders wait for B101 at 53:00 |
| ION-N0 at UW, 13 riders | 30:38.7 | 31:38.7 | B101 doors close 30:55; B102 39:00 (full); B103 50:00 | B101 left before the train arrived; all 13 board B103 |
| ION-N2 (LRV510) at Central, 24 riders | 35:00.5 | 36:00.5 | B101 at 53:00 (full) | No bus with room within the hour |
| ION-N2 at UW, 10 riders | 44:38.7 | 45:38.7 | B103 at 50:00 | 3 board, then the bus is full |

### Against the existing model

On `main`, the ION-N0 train reached Central at 20:45. The old model rounded its
riders down to 20:00, so they boarded B103 at 20:00, 45 s before their train
arrived. The ION-N2 wave was rounded from 34:45 to 34:00 the same way.

Average transfer wait for the bus, same scenario, no hold:

| Train times | Old boarding (minute rounding) | Exact boarding, 0 s walk | Exact boarding, 60 s walk |
| --- | --- | --- | --- |
| Existing model (`main`) | 11.47 min | 11.11 min | 20.66 min |
| Physical trains (this branch) | 22.59 min | 22.33 min | **22.23 min** |

- **Most of the gap was minute rounding.** With exact timing and the modelled
  walk, the existing model's own train times give 20.66 min, against
  11.47 min under the old rounding.
- **The remaining +1.6 min is not a missed connection.** At the default walk,
  both train timings miss B103 at Central. The difference comes from bus
  capacity at UW: B102 fills at 39:00, so more of the ION-N0 UW wave waits for
  B103 at 50:00. B102's queue differs because the terminal moves add about
  15 s, which moves one small arrival across the end of the hour. That
  redistributes the share of transfer riders spread over the non-wave trips.
- **The miss caused by the new geometry.** With no walk, the old train times
  make B103 (20:45 is before the doors close at 20:55) and the new ones do not
  (21:00.5). The real platform start, 72 m further back, and 25 km/h through
  the interlocking add about 16 s, which is enough to miss it.

Track geometry and train travel times are unchanged by the boarding fix.
