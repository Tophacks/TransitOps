import unittest
from transitops_m import Dispatcher, State

class TestFleet(unittest.TestCase):
    def test_station_tracking(self):
        d = Dispatcher(); d.tick(30)
        self.assertEqual([e["station"] for e in d.events if e["type"] == "STATION_ARRIVAL"], ["Station A", "Station B"])

    def test_depot_flow(self):
        d = Dispatcher(); d.request_depot("BUS-01")
        self.assertEqual(d.bays["DEPOT-1"].reserved_for, "BUS-01")
        d.confirm_docked("BUS-01")
        self.assertEqual(d.bays["DEPOT-1"].occupied_by, "BUS-01")
        d.dispatch("BUS-01")
        self.assertIsNone(d.bays["DEPOT-1"].occupied_by)

    def test_double_booking(self):
        d = Dispatcher(); d.add_bus("BUS-02"); d.dispatch("BUS-02")
        d.request_depot("BUS-01")
        with self.assertRaises(ValueError): d.request_depot("BUS-02")

    def test_safe_stop(self):
        d = Dispatcher(); d.emergency_stop()
        self.assertEqual(d.buses["BUS-01"].state, State.FAULT)
        with self.assertRaises(ValueError): d.dispatch("BUS-01")
        d.clear_interlock(); d.reset_bus("BUS-01")
        self.assertEqual(d.buses["BUS-01"].state, State.HOLDING)

    def test_depot_requires_confirmation(self):
        d = Dispatcher(); d.request_depot("BUS-01")
        self.assertIsNone(d.bays["DEPOT-1"].occupied_by)

    def test_invalid_tick(self):
        with self.assertRaises(ValueError): Dispatcher().tick(10000)

if __name__ == "__main__": unittest.main()
