# Bus connection holds at UW and Central (Step 2, part 1)

Should a bus with free seats wait for ION riders who are about to reach the
stop, given that everyone already onboard (and everyone waiting further along
the route) is delayed by the wait? The simulator answers it with a dispatch
decision at every UW and Central stop, recorded for the Decision Inspector.

- Code: `backend/simulation_engine.py` (`_connection_hold`, `_eta_minutes`)
- Tests: `backend/tests/test_connection_holds.py`,
  `backend/tests/test_transfer_boarding.py`, `frontend/e2e/verify-holds.mjs`
- Comparison: `python3 backend/compare_holds.py`

## The decision

When a bus is about to close its doors at UW or Central, dispatch checks
whether ION riders will become eligible within 3 minutes. Their eligibility is
exact, not rounded: the train's stop in the rail run, plus a modelled 60 s
walk. If any will, it weighs these options:

| Option | Benefit (rider-minutes) | Cost (rider-minutes) |
| --- | --- | --- |
| Depart on time | none | none: the transfer riders wait for the next bus (estimated from the other buses' positions and the scheduled leg times) |
| Hold 1, 2 or 3 min | for each transfer rider eligible before the held doors close, the time between their eligibility and the next bus. Seats go first to riders already waiting | hold length × riders onboard, plus hold length × riders waiting at the bus's next two stops |

Dispatch holds for the option with the largest positive net saving, and
otherwise departs on time. Every option is recorded with its numbers, or with
the reason it is infeasible:

- doors would close before the first rider is eligible;
- no free seats;
- seats taken by riders already waiting;
- net saving not positive.

The adaptive controller uses holds; the scheduled baseline never holds.

Modelled, not observed:
- the 60 s walk and the 5 s door-close margin;
- the 3-minute hold limit;
- whole-minute holds (the bus model steps in minutes);
- counting the next two stops for the downstream cost;
- the next-bus estimate, which uses scheduled rather than congested leg times.
  It predicts B101 at Central at 49:00 when it actually comes at 53:00, so it
  understates the saving.

## Central: the ION-N0 wave

| | Time |
| --- | --- |
| ION-N0 stops at Central | 21:00.5 |
| Its 30 riders become eligible (+60 s walk) | 22:00.5 |
| B103's doors due to close | 20:55 (departure 21:00) |
| B103 held 1 min: doors would close | 21:55 (too early) |
| B103 held 2 min: doors close | 22:55, departs 23:00 |

The decision at 21:00, with 36 free seats and 14 onboard:

- **Depart on time:** 31 transfer riders wait for B101 (predicted 49:00):
  836 rider-min.
- **Hold 1 min:** infeasible. The doors would close at 21:55, before the
  first rider is eligible (22:01).
- **Hold 2 min (chosen):** boards 30 of 31 and saves 810 rider-min. It costs
  28 rider-min onboard plus 178 waiting downstream, for a net of +604.
- **Hold 3 min:** boards 31 of 31 and saves 836. It costs 42 onboard plus 267
  downstream, for a net of +527.

Outcome: B103 is held until 23:00. 30 ION transfer riders and 2 others board
during the hold.

## Results: holds off vs on

Same timetable, demand and seed (page defaults), rail dispatch on.

| Platform hold | Connection holds | Holds taken | Transfer riders boarded | Avg transfer wait (boarded) | Transfer rider-min incl. still waiting | Hold cost: onboard / downstream rider-min | All riders boarded | All rider-min incl. still waiting | Trips completed | Left waiting |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| no hold | off | 0 (0 min) | 69 of 110 | 22.23 min | 2323 | 0 / 0 | 297 | 8764 | 147 | 158 |
| no hold | on | 1 (2 min) | 84 of 110 | 11.12 min | 1320 | 28 / 178 | 288 | 7796 | 138 | 167 |
| Fairway platform 2, 12 min from 20:00 | off | 0 (0 min) | 69 of 110 | 22.23 min | 2317 | 0 / 0 | 299 | 8803 | 149 | 158 |
| Fairway platform 2, 12 min from 20:00 | on | 1 (2 min) | 84 of 110 | 11.12 min | 1314 | 28 / 178 | 288 | 7835 | 138 | 169 |
| Conestoga platform 1, 14 min from 20:00 | off | 0 (0 min) | 68 of 110 | 22.39 min | 2320 | 0 / 0 | 296 | 8816 | 146 | 162 |
| Conestoga platform 1, 14 min from 20:00 | on | 1 (2 min) | 85 of 110 | 11.15 min | 1299 | 28 / 178 | 288 | 7847 | 138 | 170 |

The holds are worth it in every scenario:

- **Transfers:** 15 to 17 more transfer riders board (for example 69 → 84 of
  110 with no platform hold). The average wait of those who board halves
  (22.2 → 11.1 min).
- **All riders:** total waiting, counting riders still waiting when the hour
  ends, falls by about 970 rider-minutes (8,764 → 7,796 with no platform
  hold).
- **The cost:** the one 2-minute hold costs 28 rider-minutes onboard and 178
  for riders waiting downstream. The downstream figure is the estimate made
  at decision time.

The trade-off shows up elsewhere in the table:

- **Seats:** the 30 transfer riders take seats that riders further along the
  route would have used. 9 fewer riders board in total (297 → 288).
- **End of hour:** 9 more are still waiting when the hour ends (158 → 167),
  and 9 fewer trips are completed within it (147 → 138).

Those riders' waits are in the totals above, which still fall. With three
buses on a ~40-minute loop, a full bus is a real constraint. A spare bus
released for the wave would avoid the displacement, and that is the next
dispatch option to test.

## Sensitivity to the walk time (no platform hold)

| Walk | Hold chosen | Transfer riders boarded, off → on | All rider-min incl. still waiting, off → on |
| --- | --- | --- | --- |
| 30 s | 1 min | 70 → 86 | 8,663 → 7,648 |
| 60 s | 2 min | 69 → 84 | 8,764 → 7,796 |
| 90 s | 2 min | 71 → 88 | 8,385 → 7,405 |
| 120 s | 3 min | 65 → 84 | 8,775 → 7,828 |
| 180 s | none (riders eligible after the longest hold) | 64 → 64 | 8,678 → 8,678 |

## Still to do in Step 2

This PR covers connection holds. The rest of Step 2 is still to come:

- UW as one transfer hub with its physical bus platforms and lanes (from
  GRT's station map);
- multiple stopping positions, alternate berths, queueing, and blocked exits;
- boarding, unloading, layover and garage-return states, with signs;
- the overshoot scenario.
