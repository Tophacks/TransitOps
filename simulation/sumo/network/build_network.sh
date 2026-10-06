#!/usr/bin/env bash
set -euo pipefail

: "${SUMO_HOME:=/usr/share/sumo}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_NETWORK_DIR="${OUT_NETWORK_DIR:-$SCRIPT_DIR}"
OUT_ROUTE_DIR="${OUT_ROUTE_DIR:-$SCRIPT_DIR/../routes}"

mkdir -p "$OUT_NETWORK_DIR" "$OUT_ROUTE_DIR"

BBOX="-80.60,43.39,-80.40,43.54"
PREFIX="waterloo_kitchener"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

echo "Downloading Waterloo–Kitchener OpenStreetMap data..."
cd "$WORK_DIR"

python3 "$SUMO_HOME/tools/osmGet.py"   --bbox "$BBOX"   --prefix "$PREFIX"   --tiles 8

OSM_FILE="${PREFIX}.osm.xml"
NET_FILE="$OUT_NETWORK_DIR/${PREFIX}.net.xml.gz"
ROUTE_FILE="$OUT_ROUTE_DIR/background.rou.xml.gz"

echo "Converting OSM to compressed SUMO network..."
netconvert   --osm-files "$OSM_FILE"   --output-file "$NET_FILE"   --geometry.remove   --roundabouts.guess   --ramps.guess   --junctions.join   --tls.guess-signals   --tls.discard-simple   --tls.join   --remove-edges.isolated

echo "Generating compressed background traffic..."
python3 "$SUMO_HOME/tools/randomTrips.py"   -n "$NET_FILE"   -r "$ROUTE_FILE"   --period 1.4   --validate   --seed 17

test -s "$NET_FILE"
test -s "$ROUTE_FILE"

echo "Ready:"
echo "  $NET_FILE"
echo "  $ROUTE_FILE"
