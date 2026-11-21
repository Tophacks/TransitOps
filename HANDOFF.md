# Handoff: TransitOps (as of 2026-11-21)

This note is for picking the work up in a new session. Start by reading it, then `docs/` for the area you touch.

## Where things stand

`main` is at 467d8ad, after PR #23 (Route 7 driven by the simulator) was merged. No PRs are open.

Merged, in order: #16–#19 (UW Station, drivers, pilot lanes), #20 (GTFS line model, Route 7 end to end), #21 (regional fleet, garages, spares), #22 (natural incidents, fresh seeds, replay), #23 (Route 7 driving).

What the simulator does today:

| Area | Doc |
| --- | --- |
| ION trains, track, closures, single-track sections | `docs/ion-network.md`, `docs/ion-terminal-operations.md`, `docs/ion-yard.md` |
| UW Station buses, cars on Phillip St, lane rules | `docs/uw-station.md`, `docs/driver-agents.md` |
| Bus connection holds at UW and Central | `docs/bus-connection-holds.md` |
| Garages, regional fleet, spares | `docs/bus-garages.md`, `docs/regional-fleet.md` |
| Incidents | `docs/incidents.md` |
| Route 7 from GTFS, delays carrying forward | `docs/regional-lines.md` |
| Route 7 driven (braking, creep, stop, doors, gap waits, map) | `docs/route7-driving.md` |

## Next task: PR E (not started)

Make routes **31, 201 and 202** full lines, like Route 7, and add station operations at **Central, Fairway and Conestoga**.

What already exists:
- `tools/waterloo_geometry/build_lines.py --routes 7,31` builds any GTFS route into the same structure (`backend/lines.json`, `frontend/lib/geo/lines.json`).
- `NextRouteTests` in `backend/tests/test_lines.py` runs Route 31 in memory.
- The driver (`backend/line_driver.py`) and zones (`lines.py`, `road_traffic.Lane`) are route-agnostic. Zones are defined per pattern in `ZONES` in `build_lines.py`.

To do:
1. Build 31/201/202 and verify them against GTFS: stops within 40 m of the shape and in order, blocks without overlaps, ION links. Run `build_lines.py --check`.
2. Make sure `LineService` runs several routes together, including interlined blocks and shared stops/berths.
3. Add station operations at Central, Fairway and Conestoga: berths per platform, buses queueing for a berth, layovers. Name the buses holding a berth in each decision, as Route 7 does.
4. Map labels ("31 …", "201 …"), the scenario panel and presets.
5. Tests, an e2e suite (copy the pattern of `frontend/e2e/verify-route7-driving.mjs`), docs and a GIF.
6. Recalibrate `RUN_SHARE`/`CATCH_UP` per route if needed, and document the results honestly.

## Known open items

- **Towards Fairway, Route 7 arrives +3.2 min late (median).** The scheduled run times are shorter than the corridor motion rules allow. This is documented, not hidden; the layover absorbs it. If GRT's real times are achievable, something in the motion model is too slow: look at curve caps or cruising caps.
- With Central blocked for 20 min, 15/16 trips are covered: B109's 09:20 trip leaves after the run ends. This is expected.
- The phone e2e creep can read up to 0.85 m/s. That's a reconstruction artefact; the test allows 0.9.

## The user's standing rules

- **Draft PRs only.** The user merges and deploys. Never merge, never force-push.
- Reviewable commits. Label anything modelled (not observed) in code comments and docs.
- Credit **OSM (ODbL)** and **GTFS (Region of Waterloo Open Data Licence)** wherever their data is used.
- Never put model identifiers in commits, PRs or code.
- End commit messages with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: <this session's link>
  ```
- End PR bodies with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`, a blank line, then the session link.
- Don't commit `frontend/next-env.d.ts`, `frontend/tsconfig.json`, `frontend/AGENTS.md` or `frontend/public/pyodide/`. The dev server and build regenerate them, so revert or delete them before committing.
- Never ship the cartometro diagram.
- GitHub GraphQL is blocked, so `gh pr create` fails. Create PRs with:
  ```
  gh api repos/Tophacks/TransitOps/pulls -f title=... -f head=BRANCH -f base=main -F draft=true -F body=@FILE
  ```

## Running things

GTFS is not in the repo. Builders expect it at `/home/user/gtfs/bus` and `/home/user/gtfs/lrt`. To get it:
```
git clone --depth 1 --branch 2026-07-05_2028-07-03 https://github.com/jason897/grt-gtfs-history /home/user/gtfs
```

Backend (Python 3.13):
```
cd backend && python3 -m uvicorn main:app --port 8000
```
Restart it after every backend change.

Frontend:
```
cd frontend && npm install && npx next dev -p 3000
```

Tests:
- Backend, about 7 min: `cd backend && python3 -m pytest -q` (191 pass).
- Motion: `cd frontend && npm run test:motion` (28 pass).
- Types: `npx tsc --noEmit -p .`
- e2e (each needs both servers running): `node e2e/verify-<name>.mjs` for route7-driving, fleet, drivers, uw, garages, rail, incidents, network, and others. All pass on `main`.

GIFs: `node e2e/record-motion.mjs OUT.gif '{...}'` (options are documented at the top of the file). For true 1×, set `fps` = 1/`step`. Hide `.geoMapControls` and use `map.setPadding({right:420})` so the Decision Inspector doesn't cover the vehicle.

## Gotchas learned the hard way

- **`next build` writes into `.next`, which the dev server also uses.** Running a build while e2e suites run against the dev server causes random page errors. Afterwards, stop the dev server, `rm -rf .next`, and restart it.
- The page sends its own defaults and seed. To reproduce what the UI shows, open `?e2e=1&fixed=1` and click the preset (see `verify-route7-driving.mjs`); a bare API call gives different vehicle IDs.
- `main.run_case(main.SimulationRequest(...), True)` is the quickest way to run a full scenario in Python.
- `road_traffic.Lane` with name `"lane"` keeps Phillip Street's original RNG seed. Don't change it, or the UW results drift.
- `line_driver.simplify` tolerances (3 cm, 0.03 m/s) were tuned so the map's reconstruction matches the simulator. Loosening them puts visible jumps in speed.
