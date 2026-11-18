import { useEffect, useState } from "react";
import type { GrtFleet } from "./grtTypes";

const UNAVAILABLE: GrtFleet = {
  feeds: { bus: "unavailable", ion: "unavailable" },
  vehicles: [],
};

export function useGrtFleet(enabled: boolean) {
  const [fleet, setFleet] = useState<GrtFleet | null>(null);

  useEffect(() => {
    if (!enabled) {
      setFleet(null);
      return;
    }
    const controller = new AbortController();
    let refresh: ReturnType<typeof setTimeout>;

    async function poll() {
      try {
        const response = await fetch("/feeds/grt", {
          signal: AbortSignal.any([controller.signal, AbortSignal.timeout(20_000)]),
        });
        if (!response.ok) throw new Error("GRT request failed");
        const data: GrtFleet = await response.json();
        if (!controller.signal.aborted) setFleet(data);
      } catch {
        if (!controller.signal.aborted) setFleet(UNAVAILABLE);
      } finally {
        if (!controller.signal.aborted) refresh = setTimeout(poll, 20_000);
      }
    }

    void poll();
    return () => {
      controller.abort();
      clearTimeout(refresh);
    };
  }, [enabled]);

  return fleet;
}
