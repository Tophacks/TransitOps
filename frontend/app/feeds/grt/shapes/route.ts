import type { GrtMode, GrtShapes } from "../../../../lib/grtTypes";
import { getSchedule } from "../../../../lib/grt/schedule.ts";

const MAX_IDS = 80;

/**
 * Simplified GTFS route shapes for the live map:
 * GET /feeds/grt/shapes?mode=bus&id=10068&id=100009
 */
export async function GET(request: Request) {
  const url = new URL(request.url);
  const mode: GrtMode = url.searchParams.get("mode") === "ion" ? "ion" : "bus";
  const ids = [...new Set(url.searchParams.getAll("id"))].slice(0, MAX_IDS);
  const schedule = await getSchedule(mode);
  if (!schedule) return Response.json({ error: "GRT schedule unavailable" }, { status: 503, headers: { "Cache-Control": "no-store" } });
  const body: GrtShapes = { mode, shapes: {} };
  for (const id of ids) {
    const path = schedule.shapes.get(id);
    if (path) body.shapes[id] = path;
  }
  // Shapes change only with a new GTFS release.
  return Response.json(body, { headers: { "Cache-Control": "public, max-age=3600, s-maxage=3600" } });
}
