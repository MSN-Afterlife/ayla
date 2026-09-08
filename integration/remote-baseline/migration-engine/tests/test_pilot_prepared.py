"""Publication primitives tested ONLY on fresh copies of Phase 5G.
The CLI pilot-apply and dispatch are never executed by this suite.
"""
import sys,unittest,tempfile,hashlib
from pathlib import Path
sys.path.insert(0,'/opt/minecraft/migration-engine')
from lib.phase5g1 import *
from lib.pilot import Publication,binding,verify_versions,relative
SUPPLEMENT=ENGINE/'supplements/phase5g2-imageframe'
class PilotPreparedTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=ENGINE/'sandbox',prefix='publish-test-');self.top=Path(self.tmp.name)
  self.original=self.top/'original';self.staged=self.top/'staged';self.destination=self.top/'destination'
  for p in [self.original,self.staged,self.destination]:
   shutil.copytree(BASE,p);shutil.copytree(SUPPLEMENT,p,dirs_exist_ok=True)
 def tearDown(self):self.tmp.cleanup()
 def prepare(self):
  OfflineRun(self.staged).apply(self.top/'snapshot')
  return Publication(self.destination,self.original,self.staged)
 def test_publisher_commit_real_adapter_outputs(self):
  p=self.prepare();r=p.apply(lambda:True);self.assertEqual(r['status'],'COMMIT_STATE_VERIFIED');self.assertEqual(hashes(self.destination),hashes(self.staged))
 def test_publisher_failure_restores_original_bytes(self):
  p=self.prepare();self.assertRaises(AdapterError,p.apply,lambda:True,p.changed[-1]);self.assertEqual(hashes(self.destination),hashes(self.original))
 def test_publisher_collision_before_any_write(self):
  p=self.prepare();(self.destination/'world/players/data'/(T+'.dat')).write_bytes((self.original/'world/players/data'/(S+'.dat')).read_bytes());before=hashes(self.destination)
  self.assertRaisesRegex(AdapterError,'COLLISION',p.apply,lambda:True);self.assertEqual(hashes(self.destination),before)
 def test_publisher_fingerprint_invalidation(self):
  p=self.prepare();q=self.destination/'plugins/Quests/data'/(S+'.yml');q.write_text(q.read_text()+'\n');before=hashes(self.destination)
  self.assertRaisesRegex(AdapterError,'FINGERPRINT',p.apply,lambda:True);self.assertEqual(hashes(self.destination),before)
 def test_publisher_gate_failure_before_write(self):
  p=self.prepare();before=hashes(self.destination)
  def gate():raise AdapterError('MAINTENANCE_NOT_ACTIVE')
  self.assertRaisesRegex(AdapterError,'MAINTENANCE',p.apply,gate);self.assertEqual(hashes(self.destination),before)
 def test_publisher_gate_failure_midway_restores(self):
  p=self.prepare();calls=[]
  def gate():
   calls.append(1)
   if len(calls)==3:raise AdapterError('runtime changed')
  self.assertRaisesRegex(AdapterError,'runtime',p.apply,gate);self.assertEqual(hashes(self.destination),hashes(self.original))
 def test_approval_binding_changes_with_identity_or_input(self):
  m={'run_id':'r','source':S,'target':T};p={'fingerprints':hashes(self.original),'versions_sha256':'v'};b=binding(m,p)
  m['target']=S;self.assertNotEqual(binding(m,p),b);m['target']=T;p['fingerprints']['injected']='changed';self.assertNotEqual(binding(m,p),b)
 def test_binary_versions_missing_blocks(self):self.assertRaisesRegex(AdapterError,'VERSION_BASELINE_MISSING',verify_versions,self.destination,{})
 def test_binary_versions_detect_relevant_jar_change(self):
  artifacts={}
  for name in {'Paper'}|{c.name for c in CLASSES if c.name!='Vanilla'}:
   rel='paper.jar' if name=='Paper' else 'plugins/'+name+'.jar'
   p=self.destination/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes((name+'-v1').encode())
   artifacts[name]={'path':rel,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
  self.assertTrue(verify_versions(self.destination,artifacts))
  (self.destination/'plugins/ImageFrame.jar').write_bytes(b'changed')
  self.assertRaisesRegex(AdapterError,'VERSION_MISMATCH: ImageFrame',verify_versions,self.destination,artifacts)
 def test_publication_rejects_other_root(self):self.assertRaises(AdapterError,Publication,Path('/etc'),self.original,self.staged)
 def test_publication_rejects_escape(self):self.assertRaises(AdapterError,relative,self.destination,'../escape')
if __name__=='__main__':unittest.main()
