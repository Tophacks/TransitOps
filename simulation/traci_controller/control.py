class TransitOpsController:
    """Network-level control hooks applied to a running SUMO simulation."""

    def __init__(self, traci_connection):
        self.traci = traci_connection
        self.priority_actions = 0

    def step(self):
        """
        Apply TransitOps decisions for the current SUMO step.

        The first implementation is intentionally conservative:
        observe the network and expose hooks for dispatch, holding,
        rerouting, and signal priority without pretending the current
        scenario is calibrated GRT service.
        """
        return {
            "priority_actions": self.priority_actions,
        }

    def hold_vehicle(self, vehicle_id, edge_id, position, duration_seconds):
        self.traci.vehicle.setStop(
            vehicle_id,
            edge_id,
            pos=position,
            duration=duration_seconds,
        )

    def change_target(self, vehicle_id, edge_id):
        self.traci.vehicle.changeTarget(vehicle_id, edge_id)

    def set_signal_phase(self, traffic_light_id, phase_index):
        self.traci.trafficlight.setPhase(traffic_light_id, phase_index)
        self.priority_actions += 1
