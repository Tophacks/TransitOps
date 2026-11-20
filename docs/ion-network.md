# ION network operations (Step 3 of the simulator, #17 part 1)

The simulator's ION trains run on the real track network. A closure, a held
train or a busy section changes what the trains do, and every decision is
recorded for the Decision Inspector.

- Network builder: `tools/waterloo_geometry/build_network.py`, which writes
  `backend/ion_network.json`. The source is `sources/ion-network-osm.json`:
  every ION track way, crossover and Dutton yard track (OpenStreetMap, ODbL,
  fetched 2026-11-19).
- Simulator: `backend/rail_ops.py`. It covers line running, closures,
  single-track sections and block signalling, together with the terminal
  operations from #16.
- Tests: `backend/tests/test_network.py`, plus the terminal tests from #16.

## Tracks

OSM tags each ION way with the direction that normally uses it
(`direction=north|south`). The builder follows each direction's ways from the
Fairway exit switch to the Conestoga entry switch, and back. It never uses a
crossover or yard track, and it fails if either direction is broken.

| Direction | Length | From → to |
| --- | --- | --- |
| Northbound | 15,983 m | Fairway → Conestoga |
| Southbound | 15,765 m | Conestoga → Fairway |

The two directions share no single-track ways. They run on separate streets
in three places:

- uptown Waterloo (Willis Way to Allen);
- downtown Kitchener (northbound past City Hall and Frederick, southbound past
  Victoria Park and Queen);
- Borden to Mill.

Each direction's GTFS stops are projected onto its own OSM track.

## Crossovers: what the geometry supports

A single crossover between parallel tracks lets trains travelling one way
cross from track A to track B, and trains travelling the other way cross from
B to A. Any other move needs a reversal. The builder works out which end of
each crossover lies further north along the line, and so which moves it
allows.

| Site | OSM way | Position (northbound km) | Northbound may cross | Southbound may cross | Electrified | Passenger moves |
| --- | --- | --- | --- | --- | --- | --- |
| South of Mill | 754590784 | 3.40 km | south → north | north → south | **no** | **disabled**: not electrified (electrified=no); mapped as CN-operated per the brief: passenger moves disabled until verified |
| Kitchener Market toward Borden, near Betzner Avenue | 754590778 | 5.43 km | south → north | north → south | yes | enabled |
| Grand River Hospital toward Central, near Green Street | 754590777 | 8.37 km | south → north | north → south | yes | enabled |
| Uptown, Erb and Caroline | 582281483 | 10.14 km | south → north | north → south | yes | enabled |
| South of Dutton, near Albert Street | 754590776 | 13.72 km | south → north | north → south | yes | enabled |
| Northfield Station | 750968494 | 14.44 km | north → south | south → north | yes | enabled |

The Fairway and Conestoga scissors crossovers (#16) support both diagonals.
Cross-checks:

- **CartoMetro track diagram (2019), Green Street:** a single crossover from
  the northbound track on the Grand River Hospital side down to the southbound
  track toward Central, as the OSM geometry says.
- **Betzner Avenue:** the 2019 diagram does not reach it, so the OSM geometry
  alone is used.
- **Regional imagery:** not checked.

## Single-track working: which closures can be worked around

With one track closed, its trains can use the other track only if two
crossovers are available, both in their direction of travel: one that takes
them over to the other track before the closed stretch, and a later one that
brings them back after it. Only crossovers that are electrified and enabled
for passengers count.

Every mid-line crossover except Northfield is angled for northbound trains to
cross from the southbound track to the northbound one. Two things follow:

- **Only the southbound track can be worked around mid-line.** Southbound
  trains cross to the northbound track at Northfield and back at a later
  crossover.
- **Closing the northbound track** would need a crossover at Fairway, the
  terminal, and that is not modelled here. No mid-line diversion exists for
  it.

| Crosses at → back at | From → to | Shared single track | Whole route | Stops on the way (other track's platforms marked) |
| --- | --- | --- | --- | --- |
| Northfield Station → South of Dutton, near Albert Street | Northfield → Research and Technology | 0.7 km | 1.8 km | — |
| Northfield Station → Uptown, Erb and Caroline | Northfield → Willis Way | 4.2 km | 4.7 km | Research and Technology (nb platform), University Of Waterloo (nb platform), Laurier-Waterloo Park (nb platform) |
| Northfield Station → Grand River Hospital toward Central, near Green Street | Northfield → Central | 6.0 km | 7.1 km | Research and Technology (nb platform), University Of Waterloo (nb platform), Laurier-Waterloo Park (nb platform), Waterloo Public Square (nb platform), Allen (nb platform), Grand River Hospital (nb platform) |
| Northfield Station → Kitchener Market toward Borden, near Betzner Avenue | Northfield → Borden | 9.0 km | 9.7 km | Research and Technology (nb platform), University Of Waterloo (nb platform), Laurier-Waterloo Park (nb platform), Waterloo Public Square (nb platform), Allen (nb platform), Grand River Hospital (nb platform), Central (nb platform), Kitchener City Hall (nb platform), Frederick (nb platform), Kitchener Market (nb platform) |

For a closure, rail control picks the shortest diversion that covers the
closed stretch:

- **Grand River Hospital – Central (southbound track):** the closure includes
  the Green St crossover, so diverted trains run on the northbound track from
  Northfield to Betzner, 9.0 km of single track.
- **Kitchener Market – Borden:** no valid diversion in either direction. The
  only crossover south of Market that could take southbound trains back is
  the CN one south of Mill, which is disabled, and the northbound track has
  no mid-line diversion at all. Trains hold at the closure until it reopens.

### Turnbacks

A train can turn back at a platform only if an enabled crossover lies behind
it that takes trains of the opposite direction onto their own track. The
train reverses on its own track to reach it. A crossover alone does not make
a valid turnback. The builder lists every platform's turnback, or why it has
none. For example:

- **Southbound at Kitchener Market:** reverse 2.4 km to Green St. This is
  valid, but the train rejects it because it would strand riders beyond the
  closure; no bus bridge is modelled.
- **Northbound at Borden:** no valid turnback, because there is no crossover
  behind it that suits.

## Rail control rules (modelled)

| Rule | Value |
| --- | --- |
| Section | The whole single-track section is one resource, from the first crossover to the second |
| Who must reserve it | Diverted trains; trains that normally use the shared track, whenever their run could overlap the closure (from closure start minus the diverted trains' lead time until the last diverted train has cleared) |
| Direction | One direction at a time; trains in the same direction may follow |
| Priority | First come, first served. No train enters while one waits in the other direction |
| Release | When the train's rear clears the far crossover (plus fouling), worked out as the train actually runs |
| Where trains wait | At the last platform before the section (Northfield southbound; the platform before Betzner northbound) |
| After reopening | Waiting diverted trains run normally. Opposing trains stop reserving once no diverted train remains |
| Block signalling | A train leaves a platform only when the next one is empty. Trains never share a stopping point |
| Diversion speed | 25 km/h over each crossover, held until the whole train is clear |

## Acceptance

Southbound track closed between Grand River Hospital and Central; same
timetable and demand.

| Closure | Diverted train (enters) | Opposing train waits | Late departures | Avg departure delay |
| --- | --- | --- | --- | --- |
| 15:00–45:00 | LRV507 at 23:48 | LRV507 waits 16 min 27 s at Northfield until LRV504's rear clears; LRV506 then waits at Borden until LRV507 clears at 44:09 | 6 | 384 s |
| 25:00–45:00 | LRV509 at 35:48 | LRV508 waits at Borden until 56:09 | 4 | 219 s |
| 10:00–30:00 | LRV505 at 15:48 | LRV504 waits at Borden until 36:09 | 8 | 486 s |

A 9 km single-track section carries about one train every 21 minutes,
alternating by direction. The rest wait at the platforms before it, and the
Decision Inspector explains why.
