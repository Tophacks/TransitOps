import assert from "node:assert/strict";
import { test } from "node:test";
import { strToU8, zipSync } from "fflate";
import GtfsRealtimeBindings from "gtfs-realtime-bindings";
import { GET } from "../app/feeds/grt/route.ts";

const { FeedMessage } = GtfsRealtimeBindings.transit_realtime;

test("GRT feeds handle omitted timestamps, stop sequences and partial outages", async (t) => {
  const published = 1_794_960_000;
  const schedule = zipSync({
    "routes.txt": strToU8("route_id,route_short_name\nroute,31\n"),
    "stops.txt": strToU8("stop_id,stop_name\nuw,University of Waterloo\n"),
    "trips.txt": strToU8("trip_id,route_id,trip_headsign\ntrip,route,Columbia\n"),
    "stop_times.txt": strToU8("trip_id,stop_id,stop_sequence\ntrip,uw,0\n"),
  });
  const vehicle = {
    trip: { tripId: "trip" },
    vehicle: { id: "31" },
    position: { latitude: 43.47, longitude: -80.54 },
    currentStopSequence: 0,
  };
  const positions = FeedMessage.encode({
    header: { gtfsRealtimeVersion: "2.0", timestamp: published },
    entity: [
      { id: "1", vehicle },
      { id: "2", vehicle: { ...vehicle, vehicle: { id: "32" }, timestamp: published - 180 } },
    ],
  }).finish();
  let now = published * 1000 + 1_000;
  let ionDown = false;
  t.mock.method(Date, "now", () => now);
  const fetch = t.mock.method(globalThis, "fetch", async (url) => {
    if (url.endsWith("vehiclepositions/2") && ionDown) return new Response(null, { status: 503 });
    return new Response(url.includes("/staticfeeds/") ? schedule : positions);
  });

  const fleet = await (await GET()).json();
  assert.deepEqual(fleet.feeds, { bus: "current", ion: "current" });
  assert.deepEqual(fleet.vehicles.map(({ mode, id, route, headsign, stop }) =>
    [mode, id, route, headsign, stop]), [
    ["bus", "31", "31", "Columbia", "University of Waterloo"],
    ["ion", "31", "31", "Columbia", "University of Waterloo"],
  ]);
  await GET();
  assert.equal(fetch.mock.callCount(), 4);

  now += 16_000;
  ionDown = true;
  const partial = await (await GET()).json();
  assert.deepEqual(partial.feeds, { bus: "current", ion: "unavailable" });
  assert.equal(partial.vehicles.length, 1);

  now += 121_000;
  const expired = await GET();
  assert.equal(expired.headers.get("Cache-Control"), "no-store");
  assert.deepEqual(await expired.json(), { feeds: { bus: "stale", ion: "unavailable" }, vehicles: [] });
});
