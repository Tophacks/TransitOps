class TransitOpsController:
    """Small but real TransitOps control loop for the corridor demo."""

    def __init__(self, traci_connection):
        self.traci = traci_connection
        self.priority_actions = 0
        self.last_priority = {}
        self.recent_actions = []

    def _green_phase_for_link(self, tls_id, link_index):
        programs = self.traci.trafficlight.getAllProgramLogics(tls_id)
        if not programs:
            return None

        program = programs[0]
        for index, phase in enumerate(program.phases):
            if link_index < len(phase.state) and phase.state[link_index] in "Gg":
                return index
        return None

    def step(self):
        sim_time = self.traci.simulation.getTime()
        actions = []

        for vehicle_id in self.traci.vehicle.getIDList():
            if not vehicle_id.startswith("B"):
                continue

            next_tls = self.traci.vehicle.getNextTLS(vehicle_id)
            if not next_tls:
                continue

            tls_id, link_index, distance, state = next_tls[0]
            if distance > 70 or state in "Gg":
                continue

            last = self.last_priority.get(tls_id, -999)
            if sim_time - last < 15:
                continue

            phase_index = self._green_phase_for_link(tls_id, link_index)
            if phase_index is None:
                continue

            self.traci.trafficlight.setPhase(tls_id, phase_index)
            self.traci.trafficlight.setPhaseDuration(tls_id, 12)
            self.last_priority[tls_id] = sim_time
            self.priority_actions += 1

            action = {
                "time": sim_time,
                "vehicle": vehicle_id,
                "signal": tls_id,
                "distance_m": round(distance, 1),
                "action": "signal_priority",
            }
            actions.append(action)
            self.recent_actions.append(action)
            self.recent_actions = self.recent_actions[-12:]

        return {
            "priority_actions": self.priority_actions,
            "actions": actions,
            "recent_actions": self.recent_actions,
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
