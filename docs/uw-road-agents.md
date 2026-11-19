# UW Station road agents

Open **UW Station traffic** from the Waterloo corridor page, or visit `/uw-traffic`. The scenario runs in the browser and uses the corridor's existing following model. It uses the UW Station exit and Phillip / University signal from the corridor geometry. The access lane, in-lane dwell, single monitored southbound lane and signal timing are scenario assumptions.

## Demonstrate both interactions

1. Start at **2.50×** road traffic and wait for the fixture bus to say **Yielding to cars**.
2. Lower the slider to **0.55×** while the clock is running. The existing cars keep their positions and speeds. As arrivals thin, the bus accepts a gap and enters Phillip St.
3. Watch **Cars queued behind bus** when the bus dwells in the travel lane. The queued cars resume after the bus pulls away. At Phillip / University, the signal status also shows the queue at red.

In the deterministic test, lowering traffic at 16 simulated seconds lets the fixture enter at 23 seconds. Two cars queue during its dwell. At constant 2.50×, the bus is still waiting at 66 seconds.

## Bus agent handoff

The shared interface in `frontend/lib/motion/road.ts` is a per-lane, read-only array of `LaneOccupant` records with `id`, `rear`, `front` and `speed`. Bumper positions are metres along the lane, increasing with travel. Speed is metres per second. The lane owner supplies the lane identity by selecting the matching array. A stopped bus publishes the same footprint with zero speed.

The bus and cars use `canMerge(lane, entrant)`. At the merge, pass the proposed footprint in the **destination lane's** coordinates. The rule checks physical clearance and the speed of approaching traffic. Keep the waiting bus off the destination lane. On an accepted merge, commit its footprint before evaluating another entrant or moving following cars. The fixture updates on the existing 0.5-second traffic step and advances the leading vehicle first. Keep the bus in occupancy until its rear clears the lane.

To hook in the real bus agent, replace the fixture's approach, dwell and departure decisions with the agent's decisions. The agent should ask for lane entry through the shared gap check and publish its actual footprint each tick. Use forward lane clearance when advancing it so the bus also respects cars ahead and stop lines. The demo currently exercises this contract independently of corridor timetable playback and SUMO.

Run `npm run test:motion` from `frontend/` with Node 24.
