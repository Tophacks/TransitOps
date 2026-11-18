import assert from "node:assert/strict";
import { test } from "node:test";
import { strToU8, zipSync } from "fflate";
import GtfsRealtimeBindings from "gtfs-realtime-bindings";
import { GET } from "../app/feeds/grt/route.ts";
import { GET as getShapes } from "../app/feeds/grt/shapes/route.ts";

const { FeedMessage } = GtfsRealtimeBindings.transit_realtime;

test("GRT feeds handle omitted timestamps, stop sequences and partial outages", async (t) => {
  const published = 1_794_960_000;
  const schedule = zipSync({
    "routes.txt": strToU8("route_id,route_short_name\nroute,31\n"),
    "stops.txt": strToU8("stop_id,stop_name\nuw,University of Waterloo\n"),
    "trips.txt": strToU8("trip_id,route_id,trip_headsign,shape_id\ntrip,route,Columbia,near\nother,route,Columbia,far\n"),
    "stop_times.txt": strToU8("trip_id,stop_id,stop_sequence\ntrip,uw,0\ntrip,uw,1\n"),
    // "near" runs past the vehicles; "far" is the same route a few km away.
    "shapes.txt": strToU8([
      "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence",
      "near,43.4700,-80.5450,1", "near,43.4700,-80.5400,2", "near,43.4700,-80.5350,3",
      "far,43.4300,-80.5000,1", "far,43.4350,-80.5000,2",
    ].join("\n") + "\n"),
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
      // No trip id: matched to the nearest shape of its route.
      { id: "3", vehicle: { position: vehicle.position, vehicle: { id: "33" }, trip: { routeId: "route" } } },
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
    ["bus", "33", "31", null, null],
    ["ion", "31", "31", "Columbia", "University of Waterloo"],
    ["ion", "33", "31", null, null],
  ]);
  assert.deepEqual(fleet.vehicles.filter((v) => v.mode === "bus").map(({ id, tripId, shapeId, shapeMatch, progress, timestamp }) =>
    [id, tripId, shapeId, shapeMatch, progress, timestamp]), [
    ["31", "trip", "near", "trip", 0, published],
    ["33", null, "near", "route", null, published],
  ]);

  const shapes = await (await getShapes(new Request("http://localhost/feeds/grt/shapes?mode=bus&id=near&id=missing"))).json();
  assert.deepEqual(Object.keys(shapes.shapes), ["near"]);
  // Collinear points simplify away; distances are cumulative metres.
  assert.equal(shapes.shapes.near.length, 2);
  assert.ok(Math.abs(shapes.shapes.near[1][2] - 806.6) < 2, `${shapes.shapes.near[1][2]} m`);
  await GET();
  assert.equal(fetch.mock.callCount(), 4);

  now += 16_000;
  ionDown = true;
  const partial = await (await GET()).json();
  assert.deepEqual(partial.feeds, { bus: "current", ion: "unavailable" });
  assert.equal(partial.vehicles.length, 2);

  now += 121_000;
  const expired = await GET();
  assert.equal(expired.headers.get("Cache-Control"), "no-store");
  assert.deepEqual(await expired.json(), { feeds: { bus: "stale", ion: "unavailable" }, vehicles: [] });
});
