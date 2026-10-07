"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export type SumoVehicle = {
  id: string;
  type: string;
  speed_mps: number;
  road_id: string;
  lane_id: string;
  lon: number | null;
  lat: number | null;
};

export type SumoSignal = {
  id: string;
  phase: number;
  state: string;
};

export type SumoSnapshot = {
  step: number;
  sim_time: number;
  vehicles: SumoVehicle[];
  signals: SumoSignal[];
  arrived: string[];
  departed: string[];
};

type StreamState = "idle" | "connecting" | "building_network" | "running" | "complete" | "error";

export function useSumoStream() {
  const socketRef = useRef<WebSocket | null>(null);
  const [snapshot, setSnapshot] = useState<SumoSnapshot | null>(null);
  const [state, setState] = useState<StreamState>("idle");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const configuredUrl = process.env.NEXT_PUBLIC_SUMO_WS_URL ?? "";
  const [url, setUrl] = useState(configuredUrl);

  useEffect(() => {
    if (configuredUrl) {
      setUrl(configuredUrl);
      return;
    }

    if (window.location.hostname === "localhost") {
      setUrl("ws://localhost:8001/ws/simulation");
    }
  }, [configuredUrl]);

  const stop = useCallback(() => {
    socketRef.current?.close();
    socketRef.current = null;
    setState("idle");
  }, []);

  const start = useCallback(() => {
    if (!url) {
      setError("SUMO service URL is not configured.");
      setState("error");
      return;
    }

    socketRef.current?.close();
    setSnapshot(null);
    setError("");
    setMessage("Connecting to SUMO service…");
    setState("connecting");

    const socket = new WebSocket(url);
    socketRef.current = socket;

    socket.onmessage = (event) => {
      const payload = JSON.parse(event.data);

      if (payload.type === "simulation") {
        setSnapshot(payload.data as SumoSnapshot);
        setState("running");
        setMessage("Live SUMO + TraCI traffic");
        return;
      }

      if (payload.type === "status") {
        setMessage(payload.message ?? "");
        if (payload.status === "building_network") setState("building_network");
        if (payload.status === "starting") setState("connecting");
        if (payload.status === "complete") setState("complete");
        return;
      }

      if (payload.type === "error") {
        setError(payload.message ?? "SUMO simulation failed.");
        setState("error");
      }
    };

    socket.onerror = () => {
      setError("Could not connect to the SUMO simulation service.");
      setState("error");
    };

    socket.onclose = () => {
      socketRef.current = null;
      setState((current) =>
        current === "complete" || current === "error" ? current : "idle"
      );
    };
  }, [url]);

  useEffect(() => stop, [stop]);

  return {
    snapshot,
    state,
    message,
    error,
    url,
    available: Boolean(url),
    start,
    stop,
  };
}
