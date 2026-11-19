# Dutton Drive yard (ION OMSF)

The ION Operations, Maintenance and Storage Facility at 518 Dutton Drive,
Waterloo, is part of the simulated network. Trains have persistent IDs. A
fixed number of them are stored there, spares pull out onto the line to take
trips, and faulty trains go back in. Every movement comes from the Python
simulator (`backend/yard_ops.py`), and the map replays it.

- Geometry: `tools/waterloo_geometry/yard.py`, run by `build_network.py` →
  `ion_network.json["yard"]`
- Sources:
  - `sources/ion-network-osm.json`: every OSM way tagged `service=yard`, and both lead switches.
  - `sources/dutton-omsf-osm.json`: the OMSF building (way 754590775, tagged `addr:housenumber=518`, `addr:street=Dutton Drive`), the wash building (754590774) and the site (421343538).
  - Map data © OpenStreetMap contributors (ODbL).
- Simulator: `backend/yard_ops.py`, called from `backend/rail_ops.py`
- Tests: `backend/tests/test_yard.py`, `frontend/e2e/verify-yard.mjs`

## Track graph (verified from OSM before assigning capacity)

The builder connects the 17 yard ways with the following rules:

- A train cannot reverse at a switch. Between consecutive nodes the line may
  turn by at most 40°.
- A storage track is a stub ending at a buffer stop or a mapped track end.
- One mapping error is corrected and reported. Way 750968491 has a node
  (4211177475) where the line doubles back 4.3 m. Real track cannot do that,
  so the node is dropped.

### Leads

Both mapped leads join the **northbound** running track:

| Lead | OSM node | Joins | Points | Out to service | In from service |
| --- | --- | --- | --- | --- | --- |
| North | 7020996626 | north track 14,404 m from Fairway | with northbound traffic | northbound track to the Northfield platform (130 m) | from the Northfield southbound platform over crossover 750968494, then 33 m against the northbound direction to the lead |
| South | 7020977372 | north track 14,021 m | against northbound traffic | against the northbound direction to the Albert St crossover (754590776), then the southbound track to Research and Technology | northbound from Research and Technology, facing into the lead |

The south lead points away from Conestoga and the north lead away from
Fairway. Reaching those terminals from the other lead would need a reversal on
the running line, which the model does not permit. So:

- spares for **Conestoga** trips use the **north** lead;
- spares for **Fairway** trips use the **south** lead, then run to Fairway.

### Storage tracks

Every storage track reaches both leads without a reversal. Each track's
usable length is measured from 5 m short of its end to 8 m (the fouling
distance) before the first switch. Trains are stored nose to tail, 5 m apart.

| Track (OSM way) | Where | End | Usable | Positions |
| --- | --- | --- | --- | --- |
| 750968491 | outdoor | buffer stop | 252 m | 5 |
| 750968493 | outdoor | buffer stop | 244 m | 5 |
| 750968496 | outdoor | buffer stop | 224 m | 4 |
| 1537833917 (via 750968497) | outdoor | track end (no buffer stop mapped) | 226 m | 4 |
| 1537833915 | inside the OMSF building (shop) | track end | 171 m | 3 |
| 750968499 | inside the OMSF building (shop) | track end | 150 m | 3 |
| 750968500 | inside the OMSF building (shop) | track end | 147 m | 3 |
| 1469313205 | inside the OMSF building (shop) | track end | 164 m | 3 |

That is 30 positions: 18 outdoor and 12 in the shop. A stored train can leave
only once every train between it and the lead has gone.

## Fleet and states

GRT runs 14 LRVs. The simulator sizes the in-service fleet from the
timetable: 11 at an 8-minute headway. The remaining 3 start in the yard
(modelled allocation):

| Train | State | Position |
| --- | --- | --- |
| LRV512 | ready (prepared spare) | outdoor track 750968496, position 1 |
| LRV513 | stored | outdoor track 1537833917, position 1 |
| LRV514 | unavailable | shop track 750968500, position 1 |

States are stored, ready, unavailable, pulling out, in service and returning.
Each train keeps a state log, and the live snapshot reports each train's
state, including trains out of service.

## Moves

A yard move is enabled only when its whole route is usable:

1. Nothing blocks the storage position: no train is between it and the lead.
2. Every yard resource on the route is free. That covers the stub, each
   internal switch and the lead, including a lead blocked by the scenario.
3. The main-line block the move enters is empty:
   - the northbound Northfield block, from Research and Technology to
     Northfield, which contains both lead switches;
   - for the south lead, also the southbound Research and Technology block it
     joins over the Albert St crossover.

The pull-out holds the Northfield block, so a northbound train at Research
and Technology waits for it, as for any occupied platform ahead.

Yard resources are released when the train's rear has cleared them by the
fouling distance.

A returning train leaves the terminal only when its complete route into the
yard is usable: a free position, the lead, and the yard switches. Until then
it stays on its terminal platform.

## Dispatch: yard release against reassignment

When a trip has no train, dispatch looks at that trip and the next one from
the same terminal. This happens when a train is withdrawn, or when a trip is
due and no train is assigned to it.

Dispatch compares three kinds of plan:

- **Line trains only:** the trains already running towards the terminal take
  the trips in arrival order, each departing at arrival plus the 4-minute
  turnaround. Every later departure then runs one train short.
- **A spare for this trip:** the earliest feasible yard release.
- **A line train for this trip and a spare for the next one.**

The earliest feasible yard release is the sum of:

- preparation: 3 min for a ready spare, 20 min for a stored train (both modelled);
- the wait for the lead and yard route if they are held or blocked;
- the pull-out to the line;
- the run to the terminal;
- the turnaround.

Dispatch picks the plan with the least total lateness over the two trips. On a
tie it keeps the trains on the line. Unavailable trains, and spares via the
wrong lead, are listed as rejected options with the reason.

When a trip is threatened by a hold or a late arrival, the existing review
(keep, or swap with a train at the terminal) now also lists each yard spare
and its release time. A spare is taken only if it saves at least 2 minutes.

### Example: fault at Conestoga from 15:00 (default scenario, seed 17)

1. LRV502 stops at Conestoga at 19:28. Its fault is found (scenario), and it
   is withdrawn.
2. ION-S3 (28:00) has no train. Dispatch compares the plans:

   | Plan | ION-S3 | ION-S4 | Late in all |
   | --- | --- | --- | --- |
   | Line trains only (LRV504, then LRV506) | 31:28 | 43:28 | 10.9 min |
   | Ready spare LRV512 for ION-S3 | 32:36 | 36:00 | 4.6 min |
   | LRV504 for ION-S3, LRV512 for ION-S4 (**chosen**) | 31:28 | 36:00 | 3.5 min |
   | Stored LRV513 (20 min preparation) | | | 17.1–21.6 min |
   | LRV514 | unavailable (shop) | | |
   | Spare via the south lead | rejected: points away from Conestoga | | |

3. LRV512 is ready at 22:28. It waits on its storage track because LRV504 is
   in the northbound Northfield block.
4. LRV512 pulls out at 24:59 through the north lead and stops at the Northfield
   northbound platform at 27:39. It runs to Conestoga and leaves on ION-S4 at
   36:00, on time.
5. LRV502 leaves Conestoga out of service. At Northfield it waits for
   LRV512's block, then crosses over at 28:39 and is on shop track 750968500
   at 31:01.

With the north lead blocked from 10:00 to 50:00 (track work), every spare's
release is 50:00 or later (ION-S4 by LRV512 at 60:08). Dispatch reassigns
LRV504 and nothing leaves the yard. LRV502 stays on its Conestoga platform
until the lead reopens, and enters the yard at 58:54.

### Comparison

Matched runs: 4-minute transfer-wave delay, 110 transfer riders, seed 17.

| Scenario | Rail policy | Departures | Late (>1 min) | Missed (>3 min) | Avg / max delay | Spares sent | Faulty train in yard |
| --- | --- | --- | --- | --- | --- | --- | --- |
| no fault | holds only | 15 | 2 | 1 | 24 s / 4.0 min | 0 | - |
| no fault | dispatch | 15 | 2 | 1 | 24 s / 4.0 min | 0 | - |
| fault at Conestoga from 15:00 | holds only | 15 | 6 | 5 | 104 s / 7.5 min | 0 | 29:12 |
| fault at Conestoga from 15:00 | dispatch | 15 | 3 | 2 | 38 s / 4.0 min | LRV512 for ION-S4 | 31:01 |
| fault + north lead blocked 10:00–50:00 | holds only | 15 | 6 | 5 | 109 s / 7.5 min | 0 | 58:54 |
| fault + north lead blocked 10:00–50:00 | dispatch | 15 | 6 | 5 | 109 s / 7.5 min | 0 | 58:54 |

With the lead blocked, dispatch has no better option than the line trains, so
both policies give the same result.

## Modelled, not observed

| Item | Value |
| --- | --- |
| Yard allocation | 14 LRVs in all: the timetable fleet in service, then one ready spare, stored trains, and the last in the shop |
| Storage positions of individual trains | as in the table above |
| Preparation | 3 min ready, 20 min stored |
| Yard speed | 15 km/h (no `maxspeed` mapped) |
| Stop short of the track end / gap between stored trains | 5 m / 5 m |
| Shop tracks | inferred from the tracks inside the mapped OMSF building |
| Fault | scenario input: the first train to stop at the chosen terminal from the chosen minute |
| Blocked lead | scenario input (track work) |
| Vehicle IDs | `LRV501`…`LRV514` are illustrative, not real fleet numbers |
