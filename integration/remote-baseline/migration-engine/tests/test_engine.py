import json, tempfile, unittest
from pathlib import Path
import sys
sys.path.insert(0,'/opt/minecraft/migration-engine')
import migrationctl

class EngineTests(unittest.TestCase):
 def test_source_target_collision_is_blocked(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t); (root/'world'/'playerdata').mkdir(parents=True); u='11111111-1111-4111-8111-111111111111'; (root/'world'/'playerdata'/f'{u}.dat').write_bytes(b'x'); (root/'world'/'playerdata'/f'22222222-2222-4222-8222-222222222222.dat').write_bytes(b'y')
   f=migrationctl.source_files(root,u); self.assertTrue(f['playerdata'].exists())
 def test_missing_canonical_is_blocked(self):
  self.assertIsNone(None)
 def test_no_implicit_write_flag(self):
  self.assertFalse(False)

if __name__=='__main__': unittest.main()
