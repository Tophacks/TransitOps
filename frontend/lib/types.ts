export type Controls = {
  traffic_multiplier: number;
  train_delay: number;
  passenger_demand: number;
  fleet_size: number;
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

export type BusState = {
  id: string;
  lon: number;
  lat: number;
  state: string;
  occupancy: number;
  capacity: number;
  target: string;
  progress: number;
  schedule_deviation: number;
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

export type Snapshot = {
  minute: number;
  waiting: number;
  stops: StopState[];
  intersections: IntersectionState[];
  buses: BusState[];
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
};

export type SimulationEvent = {
  minute: number;
  message: string;
  kind: string;
};

export type CaseResult = {
  metrics: Metrics;
  events: SimulationEvent[];
  timeline: Snapshot[];
};

export type SimulationResponse = {
  scenario: Controls;
  network: Network;
  baseline: CaseResult;
  smart: CaseResult;
  impact: {
    average_wait_reduction: number;
    max_queue_reduction: number;
    fewer_left_waiting: number;
  };
};

export type Selection =
  | { type: "bus"; data: BusState }
  | { type: "stop"; data: StopState }
  | { type: "intersection"; data: IntersectionState }
  | { type: "yard"; data: Yard }
  | null;
