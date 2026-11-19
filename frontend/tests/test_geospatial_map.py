import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
class MapLayerStaticTests(unittest.TestCase):
 def test_map_layers_distinguish_simulation_from_basemap(self):
  text=(ROOT/'app/components/GeospatialMap.tsx').read_text()
  self.assertIn('AttributionControl',text)
  self.assertIn('Python simulated bus positions',text)
  self.assertIn('Simulated · not live vehicles',text)
  self.assertNotIn('illustrative',text)
  self.assertIn('ION trains from the same Python simulator',text)
  self.assertNotIn('stop-to-stop route',text)
  self.assertIn('"vehicle-bodies"',text)
  self.assertIn('"sumo-traffic"',text)
  self.assertIn('map.remove()',text)
 def test_live_vehicles_are_labelled_live_gps(self):
  text=(ROOT/'app/components/GeospatialMap.tsx').read_text()
  self.assertIn('live GPS',text)
  self.assertIn('Live GPS ·',text)
  self.assertIn('"live-bodies"',text)
 def test_toggle_and_fallback(self):
  text=(ROOT/'app/page.tsx').read_text()
  self.assertIn('<GeospatialMap',text)
  self.assertIn('<RegionalMap',text)
  self.assertIn('Geographic map',text)
if __name__=='__main__':unittest.main()
