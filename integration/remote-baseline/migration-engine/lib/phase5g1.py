"""Phase 5G.1 adapters. All mutations restricted to engine sandboxes.
Schema-bound identity changes; no active server writer is enabled here.
"""
from pathlib import Path
import base64, copy, gzip, hashlib, io, json, os, shutil, sqlite3, struct, subprocess, uuid
import yaml
from contextlib import closing
from .adapters import AdapterError
from .ownership_policy import CHECK_NAME, OwnershipPolicy, mode_for_publish
ENGINE=Path('/opt/minecraft/migration-engine')
BASE=ENGINE/'snapshots/phase5g/20260905T003724Z'
DEFAULT_S='555dd93f-a696-3e49-978b-398ad2208571'
DEFAULT_T='70f73129-6bc7-47af-9248-d0f2ec3a891d'
DEFAULT_SN='Mounkass'
DEFAULT_TN='Mounk'
S=DEFAULT_S; T=DEFAULT_T
SN=DEFAULT_SN; TN=DEFAULT_TN

def configure_identity(source_uuid=DEFAULT_S,target_uuid=DEFAULT_T,source_name=DEFAULT_SN,target_name=DEFAULT_TN):
 global S,T,SN,TN
 S=str(source_uuid);T=str(target_uuid);SN=str(source_name);TN=str(target_name)

def identity_state():
 return dict(source_uuid=S,target_uuid=T,source_name=SN,target_name=TN)
def safe(p):
 p=Path(p)
 if not p.resolve().is_relative_to(ENGINE/'sandbox') or any(x.is_symlink() for x in [p,*p.parents]): raise AdapterError('sandbox path required')
 return p

def hashes(root):
 for p in Path(root).rglob('*'):
  if p.is_symlink() or (p.is_file() and p.stat().st_nlink!=1):raise AdapterError('linked file refused')
 return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(root).rglob('*')) if p.is_file()}
def atomic(p,data):
 p=safe(p);anchor=p.parent
 while not anchor.exists():anchor=anchor.parent
 policy=OwnershipPolicy.detect(anchor);policy.check_path(anchor)
 policy.ensure_dir(p.parent);tmp=p.with_name(p.name+'.phase5g1-tmp');mode=mode_for_publish(p)
 with tmp.open('xb') as f:f.write(data);f.flush();os.fsync(f.fileno())
 os.chmod(tmp,mode);policy.normalize_path(tmp)
 os.replace(tmp,p)
 policy.normalize_path(p)
 policy.check_path(p)
def js(x):return (json.dumps(x,indent=2,sort_keys=True)+'\n').encode()
def dbrows(p):
 wal=Path(str(p)+'-wal')
 if wal.exists() and wal.stat().st_size:raise AdapterError('offline SQLite copy contains uncheckpointed WAL: '+str(p))
 with closing(sqlite3.connect(f'file:{p}?mode=ro&immutable=1',uri=True)) as c:
  c.row_factory=sqlite3.Row
  return {t:[dict(r) for r in c.execute('SELECT * FROM "'+t+'"')] for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")}
def normalized(x):return {t:sorted(rows,key=repr) for t,rows in x.items()}
def schema(p):
 with closing(sqlite3.connect(f'file:{p}?mode=ro&immutable=1',uri=True)) as c:return c.execute("SELECT name,sql FROM sqlite_master WHERE type IN ('table','index','trigger') ORDER BY name").fetchall()

class Adapter:
 name='';status='READY'
 def __init__(self,root):self.root=safe(root);self.before={};self.expected={}
 def plan(self):return {'name':self.name,'status':self.status,'source_records':0,'target_records':0}
 def paths(self):return []
 def snapshot(self,dst):
  self.snap=safe(dst);self.snap.mkdir(parents=True,exist_ok=False)
  self.before={str(p.relative_to(self.root)):p.read_bytes() if p.exists() else None for p in self.paths()}
  for n,b in self.before.items():
   if b is not None:atomic(self.snap/n,b)
  atomic(self.snap/'manifest.json',js({n:hashlib.sha256(b).hexdigest() if b is not None else None for n,b in self.before.items()}))
 def apply(self):pass
 def verify(self):return all((self.root/n).read_bytes()==b for n,b in self.before.items() if b is not None)
 def rollback(self):
  manifest=json.loads((self.snap/'manifest.json').read_text())
  for n,h in manifest.items():
   if h is not None and hashlib.sha256((self.snap/n).read_bytes()).hexdigest()!=h:raise AdapterError('snapshot fingerprint invalid')
  for n,h in manifest.items():
   p=safe(self.root/n)
   if h is None:
    if p.exists():OwnershipPolicy.detect(self.root).remove_file(p)
   else:atomic(p,(self.snap/n).read_bytes())
 def verify_rollback(self):return all((not (self.root/n).exists()) if b is None else (self.root/n).read_bytes()==b for n,b in self.before.items())

class RelationalAdapter(Adapter):
 relative='';keys={};names={}
 def __init__(self,root):
  super().__init__(root);self.db=safe(self.root/self.relative)
  self.original=dbrows(self.db);self.schema=schema(self.db)
  registry=json.loads((ENGINE/'config/phase5g1-versions.json').read_text())
  if [list(r) for r in self.schema]!=registry['schemas'][self.name]:raise AdapterError(self.name+': schema/version mismatch')
 def paths(self):return [self.db]
 def plan(self):
  d=dbrows(self.db);counts={u:{t:sum(any(r[k]==u for k in ks) for r in d[t]) for t,ks in self.keys.items()} for u in [S,T]}
  s=sum(counts[S].values());t=sum(counts[T].values())
  return dict(name=self.name,status='BLOCKED' if t else ('READY' if s else 'NO_DATA'),source_records=s,target_records=t,relations=counts,storage=self.relative)
 def transformed(self):
  d=copy.deepcopy(self.original)
  for t,ks in self.keys.items():
   for row in d[t]:
    owned=any(row[k]==S for k in ks)
    for k in ks:
     if row[k]==S:row[k]=T
    if owned and t in self.names:row[self.names[t]]=TN
  return d
 def apply(self):
  if not self.before:raise AdapterError('snapshot required')
  if self.plan()['status']=='BLOCKED':raise AdapterError('target collision')
  if schema(self.db)!=self.schema:raise AdapterError('schema/version mismatch')
  if dbrows(self.db)!=self.original:raise AdapterError('fingerprint invalidated')
  with closing(sqlite3.connect(self.db)) as c:
   c.execute('PRAGMA foreign_keys=ON');c.execute('BEGIN IMMEDIATE');c.execute('PRAGMA defer_foreign_keys=ON')
   for table,keys in self.keys.items():
    for key in keys:
     if table in self.names:
      c.execute(f'UPDATE "{table}" SET "{key}"=?, "{self.names[table]}"=? WHERE "{key}"=?',(T,TN,S))
     else:c.execute(f'UPDATE "{table}" SET "{key}"=? WHERE "{key}"=?',(T,S))
   if c.execute('PRAGMA foreign_key_check').fetchall():raise AdapterError('foreign key violation')
   if c.execute('PRAGMA integrity_check').fetchone()!=('ok',):raise AdapterError('integrity violation')
   c.commit()
 def verify(self):
  with closing(sqlite3.connect(f'file:{self.db}?mode=ro&immutable=1',uri=True)) as c:
   valid=not c.execute('PRAGMA foreign_key_check').fetchall() and c.execute('PRAGMA integrity_check').fetchone()==('ok',)
  return valid and normalized(dbrows(self.db))==normalized(self.transformed()) and schema(self.db)==self.schema
class EliteMobsAdapter(RelationalAdapter):
 name='EliteMobs';relative='plugins/EliteMobs/data/player_data.db';keys={'PlayerData':['PlayerUUID']};names={'PlayerData':'DisplayName'}
class HuskHomesAdapter(RelationalAdapter):
 name='HuskHomes';relative='plugins/HuskHomes/HuskHomesData.db'
 keys={'huskhomes_users':['uuid'],'huskhomes_user_cooldowns':['player_uuid'],'huskhomes_teleports':['player_uuid'],'huskhomes_homes':['owner_uuid']};names={'huskhomes_users':'username'}
class WaypointsAdapter(RelationalAdapter):
 name='Waypoints';relative='plugins/Waypoints/waypoints.db'
 keys={'player_data':['id'],'player_data_typed':['playerId'],'folders':['owner'],'waypoints':['owner'],'waypoint_meta':['playerId'],'selected_waypoints':['playerId'],'compass_storage':['playerId'],'waypoint_shares':['owner','sharedWith']}

# Fully parse NBT, preserving every payload byte outside explicit identity fields.
def nbt_transform(data):
 raw=gzip.decompress(data);f=io.BytesIO(raw);patch=[];identity=[]
 def read(n):
  b=f.read(n)
  if len(b)!=n:raise AdapterError('truncated NBT')
  return b
 def number(fmt):return struct.unpack(fmt,read(struct.calcsize(fmt)))[0]
 def string():return read(number('>H')).decode('utf-8')
 def payload(tag,path):
  start=f.tell()
  if tag in {1:1,2:2,3:4,4:8,5:4,6:8}:read({1:1,2:2,3:4,4:8,5:4,6:8}[tag])
  elif tag==7:read(number('>i'))
  elif tag==8:string()
  elif tag==9:
   typ=number('>B');n=number('>i')
   for i in range(n):payload(typ,path+(str(i),))
  elif tag==10:
   while True:
    typ=number('>B')
    if typ==0:break
    name=string();payload(typ,path+(name,))
  elif tag in (11,12):read(number('>i')*(4 if tag==11 else 8))
  else:raise AdapterError('invalid NBT tag')
  end=f.tell()
  if path==('UUID',):
   if tag!=11 or raw[start:end]!=struct.pack('>i',4)+uuid.UUID(S).bytes:raise AdapterError('unexpected NBT UUID')
   patch.append((start,end,struct.pack('>i',4)+uuid.UUID(T).bytes));identity.append(path)
  if path==('bukkit','lastKnownName'):
   value=TN.encode();patch.append((start,end,struct.pack('>H',len(value))+value))
 if number('>B')!=10:raise AdapterError('NBT root must be compound')
 string();payload(10,())
 if f.tell()!=len(raw):raise AdapterError('trailing NBT bytes')
 if not identity:raise AdapterError('missing NBT identity')
 for a,b,v in sorted(patch,reverse=True):raw=raw[:a]+v+raw[b:]
 return gzip.compress(raw,mtime=0)

class FileSetAdapter(Adapter):
 def __init__(self,root):super().__init__(root);self.outputs=self.prepare()
 def prepare(self):return {}
 def paths(self):return [self.root/p for p in self.outputs]
 def plan(self):
  conflicts=[p for p in self.outputs if (self.root/p).exists() and p not in getattr(self,'shared',set())]
  return dict(name=self.name,status='BLOCKED' if conflicts else 'READY',source_records=len(self.outputs),target_records=len(conflicts),collision=conflicts)
 def apply(self):
  if not self.before:raise AdapterError('snapshot required')
  if self.plan()['status']=='BLOCKED':raise AdapterError('target collision')
  for p,b in self.outputs.items():atomic(self.root/p,b)
 def verify(self):return all((self.root/p).read_bytes()==b for p,b in self.outputs.items())
class VanillaAdapter(FileSetAdapter):
 name='Vanilla'
 def prepare(self):
  out={}
  for folder,suffix in [('data','.dat'),('stats','.json'),('advancements','.json')]:
   p=Path('world/players')/folder/(S+suffix);b=(self.root/p).read_bytes()
   if suffix=='.dat':b=nbt_transform(b)
   else:json.loads(b)
   out[str(p.with_name(T+suffix))]=b
  return out
class AuraSkillsAdapter(FileSetAdapter):
 name='AuraSkills'
 def prepare(self):
  p=Path('plugins/AuraSkills/userdata')/(S+'.yml');d=yaml.safe_load((self.root/p).read_text())
  if d['uuid']!=S:raise AdapterError('AuraSkills UUID mismatch')
  d['uuid']=T;return {str(p.with_name(T+'.yml')):yaml.safe_dump(d,sort_keys=False).encode()}
class MVIAdapter(FileSetAdapter):
 name='Multiverse-Inventories'
 def prepare(self):
  root=self.root/'plugins/Multiverse-Inventories';out={}
  for p in root.rglob(SN+'.json'):
   json.loads(p.read_text());out[str(p.with_name(TN+'.json').relative_to(self.root))]=p.read_bytes()
  p=root/'players'/(S+'.json');d=json.loads(p.read_text());d['playerData']['lastKnownName']=TN
  out[str(p.with_name(T+'.json').relative_to(self.root))]=js(d)
  p=root/'playernames.json';d=json.loads(p.read_text())
  if T in d or TN in d.values():raise AdapterError('MVI name/UUID collision')
  d[T]=TN;key=str(p.relative_to(self.root));self.shared={key};out[key]=js(d);return out

class QuestsAdapter(Adapter):
 name='Quests'
 def paths(self):return list((self.root/'plugins/Quests').rglob('*yml'))
 def plan(self):
  p=self.root/'plugins/Quests/data';f=p/(S+'.yml');d=yaml.safe_load(f.read_text()) if f.exists() else {}
  empty={'currentQuests':[],'currentStages':[],'quest-points':0,'lastKnownName':SN}
  # Unknown progress/history fields fail closed instead of silently dropping them.
  status='NO_DATA' if not d or d==empty else 'BLOCKED'
  if (p/(T+'.yml')).exists():status='BLOCKED'
  return dict(name=self.name,status=status,source_records=int(bool(d)),target_records=int((p/(T+'.yml')).exists()),reason='target collision' if (p/(T+'.yml')).exists() else ('empty active/stages; zero points; no history fields' if status=='NO_DATA' else 'nonempty schema requires Quests progress adapter'))
class UltimateTeamsAdapter(Adapter):
 name='UltimateTeams'
 def paths(self):return [self.root/'plugins/UltimateTeams/UltimateTeamsData.db']
 def plan(self):
  d=dbrows(self.paths()[0]);teams=d['ultimateteams_teams'];users=d['ultimateteams_users'];members=[]
  for row in teams:
   data=json.loads(row['data']);
   if S in json.dumps(data):members.append(row['id'])
  return dict(name=self.name,status='NO_DATA' if not teams and not any(r['uuid']==T for r in users) else 'BLOCKED',source_records=sum(r['uuid']==S for r in users),target_records=sum(r['uuid']==T for r in users),team_count=len(teams),source_team_ids=members,reason='target collision' if any(r['uuid']==T for r in users) else ('zero teams: no owner/admin/member/invite relations' if not teams else 'team schema needs validation'))
class SimplePetsAdapter(Adapter):
 name='SimplePets'
 def paths(self):return [self.root/'plugins/SimplePets/storage.db']
 def plan(self):
  rows=dbrows(self.paths()[0])['simplepets_players'];src=[r for r in rows if r['uuid']==S];target=[r for r in rows if r['uuid']==T]
  empty=all(json.loads(base64.b64decode(r[k],validate=True))==[] for r in src for k in ['UnlockedPets','PetName','NeedsRespawn','SavedPets'])
  return dict(name=self.name,status='NO_DATA' if empty and not target else 'BLOCKED',source_records=len(src),target_records=len(target),reason='target collision' if target else ('all four base64 JSON arrays empty' if empty else 'nonempty pets require progress adapter'))
class SimpleLoginAdapter(Adapter):
 name='SimpleLogin';status='TRANSITION_PRESERVE'
 def paths(self):return [self.root/'plugins/SimpleLogin/passwords.db']
class MarriageMasterAdapter(Adapter):
 name='MarriageMaster'
 def paths(self):return [self.root/'plugins/MarriageMaster/database.db']
 def plan(self):
  rows=dbrows(self.paths()[0])['marry_players'];n=sum(r['uuid']==S for r in rows)
  return dict(name=self.name,status='NO_DATA' if not n else 'BLOCKED',source_records=n,target_records=sum(r['uuid']==T for r in rows))
class ImageFrameAdapter(Adapter):
 name='ImageFrame'
 def _map_files(self):return sorted((self.root/'plugins/ImageFrame/data').glob('*/data.json'))
 def _player(self,u):return self.root/'plugins/ImageFrame/players'/(u+'.json')
 def _scan(self):
  base=self.root/'plugins/ImageFrame'
  if not base.exists():return {'missing':True,'maps':[],'source_player':False,'target_player':False,'source_refs':0,'target_refs':0}
  maps=[]
  for p in self._map_files():
   d=json.loads(p.read_text())
   if set(d) != {'creationTime','creator','ditheringType','hasAccess','height','index','mapdata','name','type','url','width'}:raise AdapterError('ImageFrame schema mismatch: '+str(p.relative_to(self.root)))
   if not isinstance(d.get('hasAccess'),dict):raise AdapterError('ImageFrame hasAccess schema mismatch')
   if not isinstance(d.get('mapdata'),list) or any(set(x)!={'image','mapid','markers'} for x in d['mapdata']):raise AdapterError('ImageFrame mapdata schema mismatch')
   rel=str(p.relative_to(self.root));maps.append({'path':rel,'creator':d['creator'],'hasAccess':dict(d['hasAccess']),'index':d['index'],'name':d['name'],'map_count':len(d['mapdata'])})
  source=sum(1 for m in maps if m['creator']==S)+sum(1 for m in maps if S in m['hasAccess'])
  target=sum(1 for m in maps if m['creator']==T)+sum(1 for m in maps if T in m['hasAccess'])
  return {'missing':False,'maps':maps,'source_player':self._player(S).exists(),'target_player':self._player(T).exists(),'source_refs':source,'target_refs':target}
 def paths(self):return [self.root/m['path'] for m in self._scan()['maps']]
 def plan(self):
  x=self._scan()
  if x['missing']:return dict(name=self.name,status='NO_DATA',source_records=0,target_records=0,storage='plugins/ImageFrame/ absent from offline tree')
  created=[m for m in x['maps'] if m['creator']==S]
  grants=[m for m in x['maps'] if S in m['hasAccess']]
  status='READY' if (created or grants) and not x['target_refs'] else ('NO_MIGRATION_REQUIRED' if x['source_player'] or not x['source_refs'] else 'NO_DATA')
  if x['target_refs']:status='BLOCKED'
  return dict(name=self.name,status=status,source_records=x['source_refs'],target_records=x['target_refs'],storage='plugins/ImageFrame file JSON',schema='data/<index>/data.json: creator UUID grants implicit ALL; hasAccess UUID map grants explicit permissions; players/<uuid>.json stores preferences only',source_created=[{'path':m['path'],'index':m['index'],'name':m['name'],'map_count':m['map_count']} for m in created],source_access=[{'path':m['path'],'index':m['index'],'name':m['name'],'permission':m['hasAccess'][S]} for m in grants],source_player_preferences=x['source_player'],target_player_preferences=x['target_player'],reason='creator controls ownership/full access; player JSON is preference-only and is preserved' if status=='READY' else ('target ImageFrame authority collision' if status=='BLOCKED' else 'no functional authority to migrate'))
 def transformed(self,p):
  d=json.loads(p.read_text())
  if d.get('creator')==S:d['creator']=T
  acc=d.get('hasAccess',{})
  if S in acc:
   if T in acc:raise AdapterError('ImageFrame target access collision: '+str(p.relative_to(self.root)))
   acc[T]=acc.pop(S)
  if d.get('creator')==T:
   acc.pop(T,None)
  return js(d)
 def apply(self):
  if not self.paths():return
  if not self.before:raise AdapterError('snapshot required')
  if self.plan()['status']=='BLOCKED':raise AdapterError('target collision')
  for p in self.paths():atomic(p,self.transformed(p))
 def verify(self):
  for p in self.paths():
   d=json.loads(p.read_text())
   if d.get('creator')==S or S in d.get('hasAccess',{}):return False
  return True
class LuckPermsAdapter(Adapter):
 name='LuckPerms'
 def paths(self):return [self.root/'plugins/LuckPerms/luckperms-h2-v2.mv.db']
 def plan(self):
  # READ ONLY SQL is inspection only. Never update MVStore or SQL user tables.
  helper=ENGINE/'tools/luckperms-migration-helper'
  proc=subprocess.run(['java','-cp',str(helper/'classes')+':'+str(helper/'lib/h2-2.1.214.jar'),'InspectH2',str(self.paths()[0])],capture_output=True,text=True)
  if proc.returncode:raise AdapterError('H2 read-only inspection failed')
  d=json.loads(proc.stdout)
  status='NO_DATA' if d['source_nodes']==0 and d['source_primary'] in (None,'default') and d['target_nodes']==0 and d['target_players']==0 else 'BLOCKED'
  return dict(name=self.name,status=status,source_records=d['source_players'],target_records=d['target_players'],**d,reason='default user cache only; no assigned nodes; source preserved' if status=='NO_DATA' else 'API application required')

CLASSES=[VanillaAdapter,MVIAdapter,SimpleLoginAdapter,AuraSkillsAdapter,EliteMobsAdapter,QuestsAdapter,HuskHomesAdapter,LuckPermsAdapter,UltimateTeamsAdapter,SimplePetsAdapter,WaypointsAdapter,MarriageMasterAdapter,ImageFrameAdapter]
class OfflineRun:
 def __init__(self,root):self.root=safe(root);self.adapters=[]
 def plan(self):
  registry=json.loads((ENGINE/'config/phase5g1-versions.json').read_text())
  for path,h in registry['configs'].items():
   if not (self.root/path).is_file() or hashlib.sha256((self.root/path).read_bytes()).hexdigest()!=h:raise AdapterError('configuration/version mismatch: '+path)
  marker=self.root.parent/(self.root.name+'-commit.json')
  if marker.exists():
   m=json.loads(marker.read_text())
   if m['fingerprints']==hashes(self.root):return {'status':'ALREADY_MIGRATED','adapters':m['adapters'],'blockers':m.get('blockers',[])}
   raise AdapterError('committed state fingerprint invalidated')
  self.adapters=[c(self.root) for c in CLASSES];matrix=[a.plan() for a in self.adapters]
  blockers=[a['name'] for a in matrix if a['status']=='BLOCKED']
  return dict(status='BLOCKED' if blockers else 'READY_FOR_CONTROLLED_PILOT_PENDING_MAINTENANCE',adapters=matrix,blockers=blockers,runtime_blockers=['PLAYER_OFFLINE_RUNTIME_CHECK','MAINTENANCE_NOT_ACTIVE'])
 def apply(self,snapshot_dir,allow_partial_test=False,fail_after=None):
  self.initial=hashes(self.root);plan=self.plan()
  if plan['status']=='ALREADY_MIGRATED':raise AdapterError('already migrated')
  if plan['blockers'] and not (allow_partial_test and plan['blockers']==['ImageFrame']):raise AdapterError('structural blockers: '+str(plan['blockers']))
  self.active=[a for a in self.adapters if a.name not in plan['blockers']]
  safe(snapshot_dir)
  self.complete=Path(snapshot_dir)/'complete'
  OwnershipPolicy.detect(self.root).copytree(self.root,Path(snapshot_dir)/'complete')
  if hashes(Path(snapshot_dir)/'complete')!=self.initial:raise AdapterError('complete snapshot mismatch')
  for i,a in enumerate(self.active):a.snapshot(Path(snapshot_dir)/str(i))
  if hashes(self.root)!=self.initial:raise AdapterError('fingerprint invalidation')
  try:
   for a in self.active:
    a.apply()
    if not a.verify():raise AdapterError('verify '+a.name)
    if a.name==fail_after:raise AdapterError('injected failure after '+a.name)
   self.verify={a.name:a.verify() for a in self.active}
   expected=dict(self.initial)
   for a in self.active:
    for p in a.paths():
     if p.exists():expected[str(p.relative_to(self.root))]=hashlib.sha256(p.read_bytes()).hexdigest()
   if hashes(self.root)!=expected:raise AdapterError('unexpected global write')
   atomic(self.root.parent/(self.root.name+'-commit.json'),js(dict(fingerprints=hashes(self.root),adapters=plan['adapters'],blockers=plan['blockers'])))
  except Exception:
   self.rollback();raise
  return self.verify
 def rollback(self):
  for a in reversed(self.active):a.rollback()
  if hashes(self.complete)!=self.initial:raise AdapterError('complete rollback snapshot corrupt')
  # Recover writes outside an adapter's declared paths after a global failure.
  for p in self.root.rglob('*'):
   if p.is_file() and str(p.relative_to(self.root)) not in self.initial:OwnershipPolicy.detect(self.root).remove_file(safe(p))
  for n,h in self.initial.items():
   p=self.root/n
   if not p.exists() or hashlib.sha256(p.read_bytes()).hexdigest()!=h:atomic(p,(self.complete/n).read_bytes())
  if not all(a.verify_rollback() for a in self.active) or hashes(self.root)!=self.initial:raise AdapterError('global rollback mismatch')
  marker=self.root.parent/(self.root.name+'-commit.json')
  if marker.exists():OwnershipPolicy.detect(self.root).remove_file(marker)
  return True
