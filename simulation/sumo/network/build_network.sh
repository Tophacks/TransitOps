#!/usr/bin/env bash
set -euo pipefail

: "${SUMO_HOME:=/usr/share/sumo}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_NETWORK_DIR="${OUT_NETWORK_DIR:-$SCRIPT_DIR}"
OUT_ROUTE_DIR="${OUT_ROUTE_DIR:-$SCRIPT_DIR/../routes}"

mkdir -p "$OUT_NETWORK_DIR" "$OUT_ROUTE_DIR"

BBOX="-80.60,43.39,-80.40,43.54"
PREFIX="waterloo_kitchener"
TILES="${SUMO_OSM_TILES:-4}"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

echo "Downloading Waterloo–Kitchener OpenStreetMap data in $TILES tile(s)..."
cd "$WORK_DIR"

python3 "$SUMO_HOME/tools/osmGet.py" \
  --bbox="$BBOX" \
  --prefix="$PREFIX" \
  --tiles="$TILES" \
  --gzip

if [[ "$TILES" -eq 1 ]]; then
  OSM_FILES="${PREFIX}_bbox.osm.xml.gz"
  test -s "$OSM_FILES"
else
  shopt -s nullglob
  OSM_PARTS=( "${PREFIX}"*_"${TILES}".osm.xml.gz )
  shopt -u nullglob

  if [[ "${#OSM_PARTS[@]}" -ne "$TILES" ]]; then
    echo "Expected $TILES OSM tile files, found ${#OSM_PARTS[@]}."
    printf 'Found: %s\n' "${OSM_PARTS[@]:-none}"
    exit 1
  fi

  OSM_FILES="$(IFS=,; echo "${OSM_PARTS[*]}")"
fi

NET_FILE="$OUT_NETWORK_DIR/${PREFIX}.net.xml.gz"
ROUTE_FILE="$OUT_ROUTE_DIR/background.rou.xml.gz"

echo "Converting OSM tiles to compressed SUMO network..."
netconvert \
  --osm-files "$OSM_FILES" \
  --output-file "$NET_FILE" \
  --geometry.remove \
  --roundabouts.guess \
  --ramps.guess \
  --junctions.join \
  --tls.guess-signals \
  --tls.discard-simple \
  --tls.join \
  --remove-edges.isolated

echo "Generating compressed background traffic..."
python3 "$SUMO_HOME/tools/randomTrips.py" \
  -n "$NET_FILE" \
  -r "$ROUTE_FILE" \
  --period=1.4 \
  --validate \
  --seed=17

test -s "$NET_FILE"
test -s "$ROUTE_FILE"

echo "Ready:"
echo "  $NET_FILE"
echo "  $ROUTE_FILE"
