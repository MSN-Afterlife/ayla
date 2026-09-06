import json, shutil, tempfile, unittest
from pathlib import Path
import sys

sys.path.insert(0,'/opt/minecraft/migration-engine')
from lib.phase5g1 import BASE, ENGINE
from lib.phase5h import completeness_check, registry, required_registry_files, snapshot_relpaths

class Phase5HSnapshotTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=ENGINE/'sandbox',prefix='phase5h-snapshot-test-')
  self.root=Path(self.tmp.name)/'root'
  shutil.copytree(BASE,self.root)
 def tearDown(self):self.tmp.cleanup()
 def test_incomplete_phase5h_snapshot_reports_missing_auraskills_config(self):
  old=Path('/opt/minecraft/migration-engine/snapshots/pilot-mounk/20260905T140442Z/root')
  if old.exists():
   check=completeness_check(old)
   self.assertEqual(check['status'],'FAIL')
   self.assertIn('plugins/AuraSkills/config.yml',check['missing_files'])
 def test_snapshot_relpaths_include_all_registry_configs(self):
  rels=set(snapshot_relpaths(self.root))
  configs=set(registry()['configs'])
  self.assertTrue(configs.issubset(rels))
  self.assertEqual(len(configs),9)
 def test_required_registry_files_include_schema_dbs(self):
  files=set(required_registry_files())
  self.assertIn('plugins/EliteMobs/data/player_data.db',files)
  self.assertIn('plugins/HuskHomes/HuskHomesData.db',files)
  self.assertIn('plugins/Waypoints/waypoints.db',files)

if __name__=='__main__':unittest.main()
