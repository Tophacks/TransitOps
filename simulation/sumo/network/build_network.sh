#!/usr/bin/env bash
set -euo pipefail

: "${SUMO_HOME:=/usr/share/sumo}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_NETWORK_DIR="${OUT_NETWORK_DIR:-$SCRIPT_DIR}"
OUT_ROUTE_DIR="${OUT_ROUTE_DIR:-$SCRIPT_DIR/../routes}"

mkdir -p "$OUT_NETWORK_DIR" "$OUT_ROUTE_DIR"

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

cat > "$WORK_DIR/corridor.nod.xml" <<'EOF'
<nodes>
    <node id="uw" x="0" y="0" type="priority"/>
    <node id="tls1" x="0" y="150" type="traffic_light"/>
    <node id="uptown" x="0" y="300" type="priority"/>
    <node id="tls2" x="0" y="450" type="traffic_light"/>
    <node id="hospital" x="0" y="600" type="priority"/>
    <node id="tls3" x="0" y="750" type="traffic_light"/>
    <node id="central" x="0" y="900" type="priority"/>

    <node id="w1" x="-220" y="150" type="priority"/>
    <node id="e1" x="220" y="150" type="priority"/>
    <node id="w2" x="-220" y="450" type="priority"/>
    <node id="e2" x="220" y="450" type="priority"/>
    <node id="w3" x="-220" y="750" type="priority"/>
    <node id="e3" x="220" y="750" type="priority"/>
</nodes>
EOF

cat > "$WORK_DIR/corridor.edg.xml" <<'EOF'
<edges>
    <edge id="m0" from="uw" to="tls1" numLanes="1" speed="13.9"/>
    <edge id="m1" from="tls1" to="uptown" numLanes="1" speed="13.9"/>
    <edge id="m2" from="uptown" to="tls2" numLanes="1" speed="13.9"/>
    <edge id="m3" from="tls2" to="hospital" numLanes="1" speed="13.9"/>
    <edge id="m4" from="hospital" to="tls3" numLanes="1" speed="13.9"/>
    <edge id="m5" from="tls3" to="central" numLanes="1" speed="13.9"/>

    <edge id="r5" from="central" to="tls3" numLanes="1" speed="13.9"/>
    <edge id="r4" from="tls3" to="hospital" numLanes="1" speed="13.9"/>
    <edge id="r3" from="hospital" to="tls2" numLanes="1" speed="13.9"/>
    <edge id="r2" from="tls2" to="uptown" numLanes="1" speed="13.9"/>
    <edge id="r1" from="uptown" to="tls1" numLanes="1" speed="13.9"/>
    <edge id="r0" from="tls1" to="uw" numLanes="1" speed="13.9"/>

    <edge id="w1_in" from="w1" to="tls1" numLanes="1" speed="11.1"/>
    <edge id="e1_out" from="tls1" to="e1" numLanes="1" speed="11.1"/>
    <edge id="e1_in" from="e1" to="tls1" numLanes="1" speed="11.1"/>
    <edge id="w1_out" from="tls1" to="w1" numLanes="1" speed="11.1"/>

    <edge id="w2_in" from="w2" to="tls2" numLanes="1" speed="11.1"/>
    <edge id="e2_out" from="tls2" to="e2" numLanes="1" speed="11.1"/>
    <edge id="e2_in" from="e2" to="tls2" numLanes="1" speed="11.1"/>
    <edge id="w2_out" from="tls2" to="w2" numLanes="1" speed="11.1"/>

    <edge id="w3_in" from="w3" to="tls3" numLanes="1" speed="11.1"/>
    <edge id="e3_out" from="tls3" to="e3" numLanes="1" speed="11.1"/>
    <edge id="e3_in" from="e3" to="tls3" numLanes="1" speed="11.1"/>
    <edge id="w3_out" from="tls3" to="w3" numLanes="1" speed="11.1"/>
</edges>
EOF

NET_FILE="$OUT_NETWORK_DIR/waterloo_corridor.net.xml.gz"
ROUTE_FILE="$OUT_ROUTE_DIR/waterloo_corridor.rou.xml"

echo "Building Waterloo Corridor Test Scenario..."
"$SUMO_HOME/bin/netconvert"   --node-files "$WORK_DIR/corridor.nod.xml"   --edge-files "$WORK_DIR/corridor.edg.xml"   --output-file "$NET_FILE"   --no-turnarounds true   --tls.default-type static

cat > "$ROUTE_FILE" <<'EOF'
<routes>
    <vType id="car" vClass="passenger" accel="2.6" decel="4.5" length="4.8" maxSpeed="13.9" sigma="0.5"/>
    <vType id="transit_bus" vClass="bus" accel="1.3" decel="3.5" length="12" maxSpeed="12.5" sigma="0.2"/>

    <route id="north" edges="m0 m1 m2 m3 m4 m5"/>
    <route id="south" edges="r5 r4 r3 r2 r1 r0"/>

    <route id="cross1_e" edges="w1_in e1_out"/>
    <route id="cross1_w" edges="e1_in w1_out"/>
    <route id="cross2_e" edges="w2_in e2_out"/>
    <route id="cross2_w" edges="e2_in w2_out"/>
    <route id="cross3_e" edges="w3_in e3_out"/>
    <route id="cross3_w" edges="e3_in w3_out"/>

    <flow id="northCars" type="car" route="north" begin="0" end="900" period="8"/>
    <flow id="southCars" type="car" route="south" begin="0" end="900" period="9"/>
    <flow id="cross1A" type="car" route="cross1_e" begin="0" end="900" period="14"/>
    <flow id="cross1B" type="car" route="cross1_w" begin="4" end="900" period="15"/>
    <flow id="cross2A" type="car" route="cross2_e" begin="2" end="900" period="13"/>
    <flow id="cross2B" type="car" route="cross2_w" begin="7" end="900" period="16"/>
    <flow id="cross3A" type="car" route="cross3_e" begin="3" end="900" period="15"/>
    <flow id="cross3B" type="car" route="cross3_w" begin="9" end="900" period="17"/>

    <vehicle id="B101" type="transit_bus" route="north" depart="0" departLane="best"/>
    <vehicle id="B102" type="transit_bus" route="south" depart="35" departLane="best"/>
    <vehicle id="B103" type="transit_bus" route="north" depart="85" departLane="best"/>
</routes>
EOF

test -s "$NET_FILE"
test -s "$ROUTE_FILE"

echo "Ready:"
echo "  $NET_FILE"
echo "  $ROUTE_FILE"
