import sqlite3, tempfile, unittest
from pathlib import Path
import sys; sys.path.insert(0,'/opt/minecraft/migration-engine')
from lib.adapters import AtomicFileAdapter, JsonNameAdapter, SQLiteKeyAdapter, MigrationCoordinator, AdapterError
from migrationctl import validate_nonce, relevance_blocks
import time

class AdapterTests(unittest.TestCase):
 def test_multiverse_multiworld_and_collision(self):
  with tempfile.TemporaryDirectory() as t:
   r=Path(t); s=r/'Mounkass.json'; z=r/'Mounk.json'; s.write_text('{"inventory":[1],"world":"world"}')
   a=JsonNameAdapter(s,z); self.assertEqual(a.plan()['status'],'READY'); a.apply(r/'snap'); self.assertTrue(a.verify()); self.assertRaises(AdapterError,JsonNameAdapter(s,z).apply,r/'snap2')
 def test_sql_transaction_and_target_collision(self):
  with tempfile.TemporaryDirectory() as t:
   db=Path(t)/'x.db'; c=sqlite3.connect(db); c.execute('create table users(uuid text primary key, value text)'); c.execute('insert into users values("a","v")'); c.commit(); c.close()
   a=SQLiteKeyAdapter(db,'users','uuid','a','b'); self.assertEqual(a.plan()['status'],'READY'); a.apply(); self.assertEqual(sqlite3.connect(db).execute('select uuid from users').fetchone()[0],'b')
 def test_coordinator_rolls_back_file_on_failure(self):
  with tempfile.TemporaryDirectory() as t:
   r=Path(t); s=r/'s'; z=r/'z'; s.write_text('x'); z.write_text('existing'); a=AtomicFileAdapter(s,z)
   self.assertRaises(AdapterError,MigrationCoordinator([a]).apply_fixture,r/'snap'); self.assertEqual(z.read_text(),'existing')
 def test_incomplete_snapshot_blocked(self):
  with tempfile.TemporaryDirectory() as t:
   r=Path(t); s=r/'s'; z=r/'z'; s.write_text('x'); a=AtomicFileAdapter(s,z); self.assertRaises(AdapterError,a.rollback,r/'missing')
 def test_secret_not_in_plan(self):
  with tempfile.TemporaryDirectory() as t:
   r=Path(t); s=r/'s'; z=r/'z'; s.write_text('password=secret'); p=AtomicFileAdapter(s,z).plan(); self.assertNotIn('secret',str(p))
 def test_nonce_expiration_and_binding(self):
  x={'approval_nonce':'n','nonce_expires_at':time.time()+10,'run_id':'r'}
  self.assertTrue(validate_nonce(x,'n','r')); self.assertFalse(validate_nonce(x,'bad','r'))
  self.assertFalse(validate_nonce({'approval_nonce':'n','nonce_expires_at':time.time()-1,'run_id':'r'},'n','r'))
 def test_relevance_policy(self):
  self.assertEqual(relevance_blocks({'M':{'status':'NO_DATA'}},set()),[])
  self.assertEqual(relevance_blocks({'M':{'status':'GLOBAL_ONLY'}},set()),[])
  self.assertTrue(relevance_blocks({'M':{'status':'UNKNOWN','risk':'HIGH'}},set()))
  self.assertTrue(relevance_blocks({'M':{'status':'HAS_DATA','migration_required':True}},set()))

if __name__=='__main__': unittest.main()
