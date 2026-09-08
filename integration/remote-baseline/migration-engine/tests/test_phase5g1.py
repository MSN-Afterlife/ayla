"""Fixtures are fresh copies of Phase 5G; no fabricated plugin schemas."""
import sys, unittest, tempfile, time
from pathlib import Path
sys.path.insert(0,'/opt/minecraft/migration-engine')
from lib.phase5g1 import *
import migrationctl
SUPPLEMENT=ENGINE/'supplements/phase5g2-imageframe'

class Phase5G1Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=ENGINE/'sandbox',prefix='phase5g1-test-')
  self.top=Path(self.tmp.name);self.root=self.top/'root';shutil.copytree(BASE,self.root);shutil.copytree(SUPPLEMENT,self.root,dirs_exist_ok=True)
 def tearDown(self):self.tmp.cleanup()
 def cycle(self,cls):
  initial=hashes(self.root);a=cls(self.root);a.snapshot(self.top/'snapshot');a.apply();self.assertTrue(a.verify());a.rollback();self.assertTrue(a.verify_rollback());self.assertEqual(hashes(self.root),initial);return a
 def test_elitemobs_all_state(self):
  a=self.cycle(EliteMobsAdapter);self.assertEqual(a.original['PlayerData'][next(i for i,r in enumerate(a.original['PlayerData']) if r['PlayerUUID']==S)]['CurrencyCents'],100)
 def test_huskhomes_relations(self):
  a=self.cycle(HuskHomesAdapter);self.assertEqual(a.plan()['relations'][S]['huskhomes_homes'],1)
 def test_waypoints_two_owners_and_metadata(self):
  a=self.cycle(WaypointsAdapter);self.assertEqual(a.plan()['relations'][S]['waypoints'],2)
 def test_vanilla_nbt_and_inventory(self):
  a=VanillaAdapter(self.root);src=self.root/'world/players/data'/(S+'.dat');before=gzip.decompress(src.read_bytes());after=gzip.decompress(a.outputs['world/players/data/'+T+'.dat'])
  # UUID is one explicit 16-byte root field; all remaining bytes including
  # inventory, ender chest and nested item UUIDs remain identical.
  restored=after.replace(uuid.UUID(T).bytes,uuid.UUID(S).bytes,1).replace(b'\x08\x00\x0dlastKnownName\x00\x05Mounk',b'\x08\x00\x0dlastKnownName\x00\x08Mounkass',1)
  self.assertEqual(restored,before)
  self.cycle(VanillaAdapter)
 def test_mvi_all_worlds_and_name_index(self):
  a=MVIAdapter(self.root);self.assertEqual(len(a.outputs),6);a.snapshot(self.top/'snap');a.apply();self.assertTrue(a.verify());d=json.loads((self.root/'plugins/Multiverse-Inventories/playernames.json').read_text());self.assertEqual(d[T],TN);self.assertEqual(d[S],SN);a.rollback();self.assertTrue(a.verify_rollback())
 def test_auraskills_all_progress_preserved(self):
  a=AuraSkillsAdapter(self.root);a.snapshot(self.top/'snap');a.apply();s=yaml.safe_load((self.root/'plugins/AuraSkills/userdata'/(S+'.yml')).read_text());t=yaml.safe_load((self.root/'plugins/AuraSkills/userdata'/(T+'.yml')).read_text());t['uuid']=S;self.assertEqual(s,t);a.rollback();self.assertTrue(a.verify_rollback())
 def test_quests_exact_source_no_progress(self):
  self.assertEqual(QuestsAdapter(self.root).plan()['status'],'NO_DATA');self.cycle(QuestsAdapter)
 def test_quests_unknown_progress_fails_closed(self):
  p=self.root/'plugins/Quests/data'/(S+'.yml');p.write_text(p.read_text()+'completed-Quests: [quest1]\n');self.assertEqual(QuestsAdapter(self.root).plan()['status'],'BLOCKED')
 def test_quests_target_collision(self):
  p=self.root/'plugins/Quests/data';shutil.copy2(p/(S+'.yml'),p/(T+'.yml'));self.assertEqual(QuestsAdapter(self.root).plan()['status'],'BLOCKED')
 def no_data_target(self,cls,table):
  a=cls(self.root);p=a.paths()[0]
  with sqlite3.connect(p) as c:
   cols=[r[1] for r in c.execute('PRAGMA table_info("'+table+'")')];row=list(c.execute('SELECT * FROM "'+table+'" WHERE uuid=?',(S,)).fetchone());row[cols.index('uuid')]=T
   c.execute('INSERT INTO "'+table+'" VALUES ('+','.join('?' for _ in cols)+')',row)
  c.close()
  self.assertEqual(cls(self.root).plan()['status'],'BLOCKED')
 def test_teams_target_collision(self):self.no_data_target(UltimateTeamsAdapter,'ultimateteams_users')
 def test_pets_target_collision(self):self.no_data_target(SimplePetsAdapter,'simplepets_players')
 def test_uncheckpointed_wal_blocks_immutable_inspection(self):
  p=UltimateTeamsAdapter(self.root).paths()[0];c=sqlite3.connect(p)
  try:
   c.execute('UPDATE ultimateteams_users SET username=? WHERE uuid=?',('Changed',S));c.commit()
   self.assertRaisesRegex(AdapterError,'uncheckpointed WAL',UltimateTeamsAdapter(self.root).plan)
  finally:c.close()
 def test_teams_no_relations(self):
  self.assertEqual(UltimateTeamsAdapter(self.root).plan()['team_count'],0);self.cycle(UltimateTeamsAdapter)
 def test_pets_decoded_empty_lists(self):
  self.assertEqual(SimplePetsAdapter(self.root).plan()['status'],'NO_DATA');self.cycle(SimplePetsAdapter)
 def test_simplelogin_transition_untouched(self):
  self.assertEqual(SimpleLoginAdapter(self.root).plan()['status'],'TRANSITION_PRESERVE');self.cycle(SimpleLoginAdapter)
 def test_marriage_no_data(self):self.assertEqual(MarriageMasterAdapter(self.root).plan()['status'],'NO_DATA');self.cycle(MarriageMasterAdapter)
 def test_imageframe_creator_authority_migrates(self):
  a=self.cycle(ImageFrameAdapter);plan=a.plan();self.assertEqual(plan['status'],'READY');self.assertEqual(plan['source_records'],13);self.assertEqual(plan['target_records'],0)
 def test_imageframe_player_preferences_preserved(self):
  a=ImageFrameAdapter(self.root);before=(self.root/'plugins/ImageFrame/players'/(S+'.json')).read_bytes();a.snapshot(self.top/'if-snap');a.apply()
  self.assertEqual((self.root/'plugins/ImageFrame/players'/(S+'.json')).read_bytes(),before);self.assertFalse((self.root/'plugins/ImageFrame/players'/(T+'.json')).exists())
 def test_imageframe_target_authority_collision(self):
  p=self.root/'plugins/ImageFrame/data/0/data.json';d=json.loads(p.read_text());d['creator']=T;p.write_bytes(js(d));self.assertEqual(ImageFrameAdapter(self.root).plan()['status'],'BLOCKED')
 def test_imageframe_absent_is_no_data(self):
  shutil.rmtree(self.root/'plugins/ImageFrame');self.assertEqual(ImageFrameAdapter(self.root).plan()['status'],'NO_DATA')
 def test_luckperms_actual_h2_readonly(self):
  before=hashes(self.root);d=LuckPermsAdapter(self.root).plan();self.assertEqual(d['status'],'NO_DATA');self.assertEqual(d['source_nodes'],0);self.assertEqual(d['target_nodes'],0);self.assertEqual(d['source_primary'],'default');self.assertEqual(hashes(self.root),before);self.cycle(LuckPermsAdapter)
 def test_luckperms_logical_api_fixture(self):
  helper=ENGINE/'tools/luckperms-migration-helper'
  p=subprocess.run(['java','-cp',str(helper/'classes')+':'+str(helper/'lib/*'),'LogicalMigrationTest',str(self.top/'lp.json')],capture_output=True,text=True)
  self.assertEqual(p.returncode,0,p.stderr);self.assertIn('PASS',p.stdout)
 def test_all_available_apply_rollback(self):
  a=OfflineRun(self.root);v=a.apply(self.top/'snap');self.assertEqual(len(v),13);self.assertTrue(all(v.values()));self.assertTrue(a.rollback())
 def test_full_apply_blocks_missing_imageframe(self):
  shutil.rmtree(self.root/'plugins/ImageFrame');v=OfflineRun(self.root).apply(self.top/'snap');self.assertTrue(v['ImageFrame'])
 def test_commit_state_and_idempotency(self):
  a=OfflineRun(self.root);a.apply(self.top/'snap');b=hashes(self.root);self.assertEqual(OfflineRun(self.root).plan()['status'],'ALREADY_MIGRATED');self.assertRaises(AdapterError,OfflineRun(self.root).apply,self.top/'again');self.assertEqual(hashes(self.root),b)
 def test_failure_global_rollback_including_failing_adapter(self):
  a=OfflineRun(self.root);b=hashes(self.root);self.assertRaises(AdapterError,a.apply,self.top/'snap',False,'ImageFrame');self.assertEqual(hashes(self.root),b)
 def collision(self,cls,table,key):
  a=cls(self.root)
  with sqlite3.connect(a.db) as c:
   cols=[r[1] for r in c.execute('PRAGMA table_info("'+table+'")')];row=list(c.execute('SELECT * FROM "'+table+'" WHERE "'+key+'"=?',(S,)).fetchone());row[cols.index(key)]=T
   c.execute('INSERT INTO "'+table+'" VALUES ('+','.join('?' for _ in cols)+')',row)
  before=hashes(self.root);b=cls(self.root);self.assertEqual(b.plan()['status'],'BLOCKED');b.snapshot(self.top/'snap');self.assertRaises(AdapterError,b.apply);self.assertEqual(hashes(self.root),before)
 def test_elitemobs_collision(self):self.collision(EliteMobsAdapter,'PlayerData','PlayerUUID')
 def test_huskhomes_collision(self):self.collision(HuskHomesAdapter,'huskhomes_users','uuid')
 def test_waypoints_collision(self):self.collision(WaypointsAdapter,'player_data','id')
 def test_vanilla_collision(self):
  p=self.root/'world/players/data'/(T+'.dat');p.write_bytes(b'conflicting inventory');a=VanillaAdapter(self.root);self.assertEqual(a.plan()['status'],'BLOCKED');self.assertRaises(AdapterError,OfflineRun(self.root).apply,self.top/'snap',True);self.assertEqual(p.read_bytes(),b'conflicting inventory')
 def test_mvi_collision(self):
  p=self.root/'plugins/Multiverse-Inventories/groups/default/Mounk.json';p.write_text('{"conflict":true}');self.assertEqual(MVIAdapter(self.root).plan()['status'],'BLOCKED')
 def test_aura_collision(self):
  p=self.root/'plugins/AuraSkills/userdata'/(T+'.yml');p.write_text('skills: {}');self.assertEqual(AuraSkillsAdapter(self.root).plan()['status'],'BLOCKED')
 def test_fingerprint_invalidated_between_snapshot_apply(self):
  a=EliteMobsAdapter(self.root);a.snapshot(self.top/'snap')
  with sqlite3.connect(a.db) as c:c.execute('UPDATE PlayerData SET CurrencyCents=999 WHERE PlayerUUID=?',(S,))
  self.assertRaisesRegex(AdapterError,'fingerprint',a.apply)
 def test_schema_version_mismatch(self):
  a=EliteMobsAdapter(self.root)
  with sqlite3.connect(a.db) as c:c.execute('ALTER TABLE PlayerData ADD COLUMN new_state TEXT')
  self.assertRaisesRegex(AdapterError,'version mismatch',EliteMobsAdapter,self.root)
 def test_config_version_mismatch(self):
  p=self.root/'plugins/HuskHomes/config.yml';p.write_text(p.read_text()+'\n# changed\n');self.assertRaisesRegex(AdapterError,'version mismatch',OfflineRun(self.root).plan)
 def test_committed_fingerprint_invalidation(self):
  a=OfflineRun(self.root);a.apply(self.top/'snap');(self.root/'unexpected').write_text('x');self.assertRaisesRegex(AdapterError,'fingerprint',OfflineRun(self.root).plan)
 def test_snapshot_checksum_required_for_rollback(self):
  a=EliteMobsAdapter(self.root);a.snapshot(self.top/'snap');a.apply();(a.snap/a.relative).write_bytes(b'bad');self.assertRaisesRegex(AdapterError,'fingerprint',a.rollback)
 def test_nonce_wrong_expired_and_cross_run(self):
  x=dict(approval_nonce='n',nonce_expires_at=time.time()+10,run_id='r');self.assertTrue(migrationctl.validate_nonce(x,'n','r'));self.assertFalse(migrationctl.validate_nonce(x,'n','other'));self.assertFalse(migrationctl.validate_nonce(x,'x','r'));x['nonce_expires_at']=0;self.assertFalse(migrationctl.validate_nonce(x,'n','r'))
 def test_cli_write_flag_required(self):self.assertRaisesRegex(SystemExit,'allow-write',migrationctl.pilot_apply,'unused','n')
 def test_blocked_summary_includes_luckperms(self):self.assertIn('LuckPerms: adapter BLOCKED',migrationctl.relevance_blocks({'LuckPerms':{'status':'BLOCKED'}},set()))
 def test_no_active_root(self):self.assertRaises(AdapterError,safe,Path('/opt/minecraft/crafty/servers/active'))
 def test_symlink_escape(self):
  p=self.top/'link';p.symlink_to(BASE,target_is_directory=True);self.assertRaises(AdapterError,safe,p)
 def test_planner_has_no_storage_side_effects(self):
  before=hashes(self.root);OfflineRun(self.root).plan();self.assertEqual(before,hashes(self.root))

if __name__=='__main__':unittest.main()
