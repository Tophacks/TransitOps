import type { IonSchedule, RailDecision } from "./motion/ion.ts";

export type Controls = {
  traffic_multiplier: number;
  train_delay: number;
  passenger_demand: number;
  fleet_size: number;
  demand_multiplier: number;
  surge_passengers: number;
  surge_minute: number | null;
  breakdown_minute: number | null;
  emergency_dispatch_minute: number | null;
  seed: number;
  ion_headway?: number;
  /** extra platform hold at an ION terminal (null: none) */
  platform_hold_terminal?: "fairway" | "conestoga" | null;
  platform_hold_platform?: "1" | "2";
  platform_hold_minute?: number;
  platform_hold_minutes?: number;
  /** ION track closure (null: none) */
  closure_track?: "north" | "south" | null;
  closure_from_station?: string;
  closure_to_station?: string;
  closure_from_minute?: number;
  closure_to_minute?: number;
  /** ION train fault: the first train to stop at this terminal from fault_minute is withdrawn */
  fault_terminal?: "fairway" | "conestoga" | null;
  fault_minute?: number;
  /** a Dutton yard lead blocked (scenario) */
  yard_block_lead?: "north" | "south" | null;
  yard_block_from_minute?: number;
  yard_block_to_minute?: number;
  /** UW Station: a berth taken by an out-of-service bus, or the Phillip St exit blocked */
  uw_block?: "berth" | "exit" | null;
  uw_block_berth?: string;
  uw_block_from_minute?: number;
  uw_block_to_minute?: number;
  /** private cars on Phillip Street southbound at the UW exit */
  uw_cars?: "light" | "moderate" | "heavy";
  /** cars in the shared-traffic lanes on Route 7 (King / KCI curbside stop, Fairway bay exit) */
  line_cars?: "light" | "moderate" | "heavy";
  /** opt-in driver errors (a demo on B101's first UW visit, or seeded) */
  driver_error?: "overshoot" | "overshoot_blocked" | "missed_berth" | "wrong_turn" | "missed_stop" | "seeded" | null;
  driver_error_rate?: number;
  error_seed?: number;
  /** a train overshooting a platform (Rail Control recovery) */
  rail_overshoot_station?: string | null;
  rail_overshoot_direction?: "north" | "south";
  rail_overshoot_minute?: number;
  rail_overshoot_metres?: number;
  /** run length from 07:30 (scheduled lines need more than an hour for a round trip) */
  duration_min?: number;
  /** scheduled GRT lines run end to end (backend/lines.py) */
  lines?: string[];
  /** a station delay on a line: the stop's berths unavailable (scenario) */
  line_block_stop?: string | null;
  line_block_from_minute?: number;
  line_block_to_minute?: number;
  /** where the "missed_stop" driver error demo happens */
  missed_stop_stop?: string;
  missed_stop_after_minute?: number;
  /** regional inventory of conventional buses (fleet_size is only the corridor run's buses) */
  fleet_inventory?: number;
  breakdown_kind?: "hold" | "out_of_service";
  breakdown_target?: "corridor" | "line";
  breakdown_vehicle?: string | null;
  /** dispatcher orders from the Fleet panel, applied at their minute */
  commands?: Command[];
  /** incidents during an ordinary run, drawn from the seed (backend/incidents.py) */
  incidents?: "off" | "natural";
  incident_rates?: Record<string, number> | null;
  /** regional spares the adaptive dispatcher may send when a station asks for capacity and the yard is empty */
  capacity_spares?: number;
};

export type Incident = { id: string; minute: number; kind: string; applied?: boolean; note?: string; [key: string]: unknown };
export type RunReport = {
  trips_covered: number; trips_scheduled: number; corridor_riders_delivered: number;
  departure_delay_avg_s: number; departure_delay_max_s: number; missed_stops: number;
  transfers_boarded: number; connections_missed: number; riders_still_waiting: number;
  incidents_applied: number; breakdowns: number;
};

export type Command = { minute: number; action: "dispatch_spare" | "reassign" | "breakdown"; vehicle: string; target: string };

export type StopState = {
  id: string;
  name: string;
  x?: number;
  y?: number;
  lon: number;
  lat: number;
  queue?: number;
  sprites?: number;
};

export type IntersectionState = {
  id: string;
  name: string;
  x?: number;
  y?: number;
  lon: number;
  lat: number;
  phase?: "GREEN" | "RED";
};

export type SegmentMotion = {
  from: string;
  to: string;
  /** whole simulated minutes spent on this segment so far (includes a signal hold) */
  elapsed: number;
  minutes: number;
  hold_minutes: number;
  /** cars queued ahead of the bus at a red signal (0 without a hold) */
  queue_cars?: number;
  length_m: number;
  distance_m: number;
};

export type BusState = {
  id: string;
  lon: number;
  lat: number;
  state: string;
  occupancy: number;
  capacity: number;
  target: string;
  /** distance travelled on the current street segment / segment length */
  progress: number;
  schedule_deviation: number;
  segment?: SegmentMotion | null;
};

export type Yard = {
  id: string;
  name: string;
  lon: number;
  lat: number;
  x?: number;
  y?: number;
};

export type Network = {
  yard: Yard;
  stops: StopState[];
  intersections: IntersectionState[];
  route_order: string[];
  passengers_per_sprite?: number;
  corridor_name?: string;
};

/** An ION train in a minute snapshot (backend/simulation_engine.py). */
export type TrainState = {
  /** the physical train (vehicle ID) */
  id: string;
  /** the trip it is running */
  trip_id?: string;
  terminal?: string | null;
  activity?: string;
  /** null for a train out of service (in the Dutton yard, pulling out or returning) */
  direction: "north" | "south" | null;
  /** fleet state: stored, ready, unavailable, pulling out, in service, returning */
  state?: string;
  in_service?: boolean;
  /** metres along the track of the train's nose */
  nose_m: number;
  /** station it is standing at, if any */
  station: string | null;
  next_station: string;
  lon: number;
  lat: number;
  delay_min: number;
  hold: string;
  /** riders still to transfer to the bus, by bus stop (CENTRAL / UW) */
  transfers_ahead: Record<string, number>;
};

export type Snapshot = {
  minute: number;
  waiting: number;
  stops: StopState[];
  intersections: IntersectionState[];
  buses: BusState[];
  trains: TrainState[];
};

export type Metrics = {
  passengers_arrived: number;
  passengers_boarded: number;
  completed_trips: number;
  left_waiting: number;
  average_wait: number;
  max_queue: number;
  buses_staged: number;
  signal_priority_requests: number;
  ion_transfers: number;
  ion_transfers_boarded: number;
  average_transfer_wait: number;
  ion_trains_peak: number;
  ion_max_delay: number;
  connection_holds?: number;
  connection_hold_minutes?: number;
  hold_onboard_rider_minutes?: number;
  hold_downstream_rider_minutes?: number;
  total_wait_rider_minutes?: number;
  total_transfer_wait_rider_minutes?: number;
};

export type SimulationEvent = {
  minute: number;
  message: string;
  kind: string;
  bus_id?: string;
  train_id?: string;
  reason?: string;
};

export type CaseResult = {
  metrics: Metrics;
  events: SimulationEvent[];
  timeline: Snapshot[];
  /** this case's ION run (adaptive: dispatch may reassign trips; baseline: holds only) */
  ion?: IonSchedule;
  /** bus decisions: UW Station agents, station reports, holds and capacity requests */
  decisions?: RailDecision[];
  /** GRT garage pull-outs and returns and each garage's inventory (backend/garage_ops.py) */
  garages?: { vehicles: import("./motion/garages.ts").GarageVehicle[];
    inventory: Record<string, { t: number; count: number; present: string[]; event: string }[]> };
  /** incidents drawn from the seed, and whether each applied in this run */
  incidents?: Incident[];
  /** the run report (acceptance 5) */
  report?: RunReport;
  /** scheduled GRT lines end to end (backend/lines.py) */
  lines?: import("./motion/lines.ts").LinesResult | null;
  /** the regional fleet: inventory, garages, every vehicle's states, spares sent (backend/fleet.py) */
  fleet?: import("./motion/lines.ts").FleetData;
  /** every vehicle on UW Station's roadway, second by second (backend/uw_station.py) */
  uw?: { block: Record<string, unknown> | null; vehicles: import("./motion/uwStation.ts").UwVehicle[];
    cars?: import("./motion/uwStation.ts").UwCar[]; car_demand?: string;
    errors?: { t: number; vehicle_id: string; kind: string; detail: string }[];
    metrics: { corridor_departures: number; mean_station_s: number; corridor_queue_s: number;
      corridor_stopped_by: Record<string, number>; grt_buses: number; grt_queue_s: number } | null };
};

export type SimulationResponse = {
  scenario: Controls;
  /** drawn once from the seed before either policy acts; both runs get this list */
  incidents?: { mode: string; seed: number; list: Incident[] };
  network: Network;
  ion: IonSchedule;
  baseline: CaseResult;
  smart: CaseResult;
  impact: {
    average_wait_reduction: number;
    additional_completed_trips: number;
    max_queue_reduction: number;
    fewer_left_waiting: number;
    transfer_wait_reduction: number;
  };
};

export type Selection =
  | { type: "bus"; data: BusState }
  | { type: "stop"; data: StopState }
  | { type: "intersection"; data: IntersectionState }
  | { type: "yard"; data: Yard }
  | { type: "train"; data: TrainState }
  | null;

export type LiveSumoVehicle = {
  id: string;
  type: string;
  speed_mps: number;
  road_id: string;
  lane_id: string;
  lon: number | null;
  lat: number | null;
};

export type LiveSumoSnapshot = {
  step: number;
  sim_time: number;
  vehicles: LiveSumoVehicle[];
  signals: { id: string; phase: number; state: string }[];
  arrived: string[];
  departed: string[];
};
