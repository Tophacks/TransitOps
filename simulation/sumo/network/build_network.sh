#!/usr/bin/env bash
set -euo pipefail

: "${SUMO_HOME:=/usr/share/sumo}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Approximate Waterloo–Kitchener regional extent.
# west,south,east,north
BBOX="-80.60,43.39,-80.40,43.54"
PREFIX="waterloo_kitchener"

echo "Downloading OpenStreetMap network for ${BBOX}"
python3 "$SUMO_HOME/tools/osmGet.py" \
  --bbox "$BBOX" \
  --prefix "$PREFIX" \
  --tiles 4

echo "Building SUMO road network"
python3 "$SUMO_HOME/tools/osmBuild.py" \
  --osm-file "${PREFIX}.osm.xml" \
  --vehicle-classes road \
  --netconvert-options junctions.join=true,roundabouts.guess=true,ramps.guess=true

NET_FILE="${PREFIX}.net.xml"

if [[ ! -f "$NET_FILE" ]]; then
  echo "Expected network file $NET_FILE was not created."
  exit 1
fi

echo "Generating background road traffic"
python3 "$SUMO_HOME/tools/randomTrips.py" \
  -n "$NET_FILE" \
  -r ../routes/background.rou.xml \
  --period 1.4 \
  --validate \
  --seed 17

echo "SUMO network ready: $SCRIPT_DIR/$NET_FILE"
