import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
class MapLayerStaticTests(unittest.TestCase):
 def test_map_layers_distinguish_simulation_from_basemap(self):
  text=(ROOT/'app/components/GeospatialMap.tsx').read_text()
  self.assertIn('© OpenStreetMap contributors',text)
  self.assertIn('NOT live transit feeds',text)
  self.assertIn('stop-to-stop route is illustrative',text)
  self.assertIn('"sumo-traffic"',text)
  self.assertIn('map.remove()',text)
 def test_toggle_and_fallback(self):
  text=(ROOT/'app/page.tsx').read_text()
  self.assertIn('<GeospatialMap',text)
  self.assertIn('<RegionalMap',text)
  self.assertIn('Geographic map',text)
if __name__=='__main__':unittest.main()
