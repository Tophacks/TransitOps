import { useCallback, useEffect, useRef, useState } from "react";

import { PlaybackClock } from "./motion/clock.ts";

/** Simulated seconds per real second offered in the playback controls. */
export const PLAYBACK_SPEEDS = [1, 10, 30, 60] as const;
const UI_REFRESH_MS = 125;

/**
 * React wrapper around PlaybackClock. The map reads `clock.time()` every
 * animation frame; React state (`seconds`) refreshes ~8 times a second for the
 * timeline, metrics and inspector.
 */
export function usePlayback(initialSpeed: number) {
  const clockRef = useRef<PlaybackClock | null>(null);
  if (!clockRef.current) {
    clockRef.current = new PlaybackClock(0);
    clockRef.current.setSpeed(initialSpeed);
  }
  const clock = clockRef.current;
  const [seconds, setSeconds] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeedState] = useState(initialSpeed);

  useEffect(() => {
    if (!playing) return;
    let frame = 0;
    let last = 0;
    const tick = (now: number) => {
      if (now - last >= UI_REFRESH_MS) {
        last = now;
        setSeconds(clock.time());
        if (!clock.playing) {
          clock.pause();
          setPlaying(false);
          return;
        }
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [clock, playing]);

  const load = useCallback((durationSeconds: number) => {
    clock.pause();
    clock.setDuration(durationSeconds);
    clock.seek(0);
    setSeconds(0);
    setPlaying(false);
  }, [clock]);

  const play = useCallback(() => {
    clock.play();
    setSeconds(clock.time());
    setPlaying(true);
  }, [clock]);

  const pause = useCallback(() => {
    clock.pause();
    setSeconds(clock.time());
    setPlaying(false);
  }, [clock]);

  const seek = useCallback((t: number) => {
    clock.seek(t);
    setSeconds(clock.time());
  }, [clock]);

  const setSpeed = useCallback((rate: number) => {
    clock.setSpeed(rate);
    setSpeedState(rate);
  }, [clock]);

  return { clock, seconds, playing, speed, load, play, pause, seek, setSpeed };
}
