import os, shutil, stat, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0,'/opt/minecraft/migration-engine')
from lib.phase5g1 import ENGINE, AdapterError, atomic
from lib.pilot import Publication, write_atomic
from lib.ownership_policy import OwnershipPolicy


def owner(path):
 st=Path(path).stat()
 return st.st_uid,st.st_gid

def mode(path):
 return stat.S_IMODE(Path(path).stat().st_mode)


class OwnershipPolicyTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=ENGINE/'sandbox',prefix='ownership-test-')
  self.top=Path(self.tmp.name)
 def tearDown(self):self.tmp.cleanup()
 def tree(self,root,files):
  root.mkdir(parents=True,exist_ok=True)
  for rel,data in files.items():
   p=root/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)

 def test_atomic_temp_rename_normalizes_and_preserves_existing_mode(self):
  p=self.top/'root/plugins/Test/config.yml';p.parent.mkdir(parents=True);p.write_text('old\n');os.chmod(p,0o600)
  atomic(p,b'new\n')
  self.assertEqual(p.read_bytes(),b'new\n')
  self.assertEqual(owner(p),owner(p.parent))
  self.assertEqual(mode(p),0o600)

 def test_root_owned_snapshot_restore_contract_chowns_to_crafty_identity(self):
  root=self.top/'active';root.mkdir()
  policy=OwnershipPolicy.explicit(root,1000,0)
  calls=[]
  def fake_chown(path,uid,gid):
   calls.append((Path(path).name,uid,gid))
  with mock.patch('lib.ownership_policy.os.chown',side_effect=fake_chown), \
       mock.patch.object(OwnershipPolicy,'check_path',return_value=True):
   write_atomic(root/'level.dat',b'nbt',policy)
  self.assertIn(('level.dat',1000,0),calls)
  self.assertIn(('.level.dat.migration-tmp',1000,0),calls)

 def test_publisher_restores_original_bytes_owner_and_mode_on_failure(self):
  original=self.top/'original';staged=self.top/'staged';destination=self.top/'destination'
  files={
   'world/players/data/player.dat':b'vanilla',
   'plugins/Multiverse-Inventories/playernames.json':b'{"a":"b"}',
   'plugins/AuraSkills/userdata/player.yml':b'uuid: a\n',
   'plugins/Waypoints/waypoints.db':b'sqlite',
   'logs/latest.log':b'log',
   'world/level.dat':b'level',
  }
  self.tree(original,files);shutil.copytree(original,staged);shutil.copytree(original,destination)
  (staged/'plugins/AuraSkills/userdata/player.yml').write_bytes(b'uuid: b\n')
  (staged/'world/level.dat').write_bytes(b'level2')
  os.chmod(destination/'world/level.dat',0o600)
  p=Publication(destination,original,staged)
  self.assertRaisesRegex(AdapterError,'injected',p.apply,lambda:True,'world/level.dat')
  self.assertEqual((destination/'world/level.dat').read_bytes(),b'level')
  self.assertEqual(owner(destination/'world/level.dat'),owner(destination))
  self.assertEqual(mode(destination/'world/level.dat'),0o600)

 def test_publisher_blocks_wrong_owner_before_commit(self):
  original=self.top/'original';staged=self.top/'staged';destination=self.top/'destination'
  self.tree(original,{'plugins/Test/config.yml':b'a'});shutil.copytree(original,staged);shutil.copytree(original,destination)
  (staged/'plugins/Test/config.yml').write_bytes(b'b')
  p=Publication(destination,original,staged)
  with mock.patch('lib.ownership_policy.OwnershipPolicy.check_path',side_effect=AdapterError('OWNERSHIP_INTEGRITY_CHECK: bad owner')):
   self.assertRaisesRegex(AdapterError,'OWNERSHIP_INTEGRITY_CHECK',p.apply,lambda:True)

 def test_policy_copytree_normalizes_root_created_directory(self):
  src=self.top/'src';dst=self.top/'dst'
  self.tree(src,{'plugins/Test/config.json':b'{}','plugins/Test/data.db':b'db','world/level.dat_old':b'old'})
  policy=OwnershipPolicy.detect(self.top)
  policy.copytree(src,dst)
  self.assertEqual(owner(dst),owner(self.top))
  self.assertEqual(owner(dst/'plugins/Test/config.json'),owner(self.top))


if __name__=='__main__':unittest.main()
