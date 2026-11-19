import type { IonSchedule } from "./motion/ion.ts";

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
};

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
  id: string;
  direction: "north" | "south";
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
};

export type SimulationResponse = {
  scenario: Controls;
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
