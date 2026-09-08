from pathlib import Path
import datetime, hashlib, json, shutil, time

from .phase5g1 import ENGINE, S, T, SN, TN, js
from .adapters import AdapterError
from .ownership_policy import OwnershipPolicy

SERVER_ROOT = Path('/opt/minecraft/crafty/servers/c253fa7e-2bd2-4545-a9ba-b1a8db892197')

def sha256(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()

def registry():
 return json.loads((ENGINE/'config/phase5g1-versions.json').read_text())

def required_registry_files():
 r=registry()
 out=set(r.get('configs',{}))
 out.update({
  'plugins/EliteMobs/data/player_data.db',
  'plugins/HuskHomes/HuskHomesData.db',
  'plugins/Waypoints/waypoints.db',
 })
 return sorted(out)

def snapshot_relpaths(root=SERVER_ROOT):
 rels=set(required_registry_files())
 rels.update({
  'plugins/LuckPerms/luckperms-h2-v2.mv.db',
  'plugins/LuckPerms/contexts.json',
  'plugins/UltimateTeams/UltimateTeamsData.db',
  'plugins/SimplePets/storage.db',
  'plugins/MarriageMaster/database.db',
  'plugins/SimpleLogin/passwords.db',
  'plugins/Multiverse-Inventories/groups.yml',
  'plugins/Multiverse-Inventories/playernames.json',
  'plugins/ImageFrame/config.yml',
  'plugins/ImageFrame/data/data.json',
  'plugins/ImageFrame/data/deletedMaps.bin',
 })
 for folder,suffix in [('data','.dat'),('stats','.json'),('advancements','.json')]:
  for u in [S,T]:
   rels.add(f'world/players/{folder}/{u}{suffix}')
 for glob in [
  'plugins/AuraSkills/userdata/*.yml',
  'plugins/Quests/data/*.yml',
  'plugins/Multiverse-Inventories/**/*.json',
  'plugins/ImageFrame/data/**',
  'plugins/ImageFrame/players/*.json',
 ]:
  for p in Path(root).glob(glob):
   if p.is_file():rels.add(str(p.relative_to(root)))
 return sorted(rels)

def copy_snapshot_file(root, snap_root, rel):
 src=Path(root)/rel
 if not src.exists():return False
 if src.is_symlink():raise AdapterError('SNAPSHOT_REFUSED_SYMLINK: '+rel)
 dst=Path(snap_root)/rel
 OwnershipPolicy.detect(ENGINE).copy2(src,dst)
 return True

def snapshot_hashes(root):
 return {str(p.relative_to(root)):sha256(p) for p in sorted(Path(root).rglob('*')) if p.is_file()}

def completeness_check(snap_root):
 required=required_registry_files()
 missing=[p for p in required if not (Path(snap_root)/p).is_file()]
 present=[p for p in required if (Path(snap_root)/p).is_file()]
 return {'name':'SNAPSHOT_COMPLETENESS_CHECK','required_config_files_count':len(registry().get('configs',{})),
         'required_files_count':len(required),'present_required_files':present,
         'missing_files':missing,'status':'PASS' if not missing else 'FAIL'}

def build_final_snapshot(run_id, root=SERVER_ROOT):
 policy=OwnershipPolicy.detect(ENGINE)
 ts=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
 snap=ENGINE/'snapshots/pilot-mounk'/ts
 snap_root=snap/'root'
 sandbox=ENGINE/'sandbox'/('phase5h-final-'+ts)/'offline-root'
 copied=[]; absent=[]
 for rel in snapshot_relpaths(root):
  if copy_snapshot_file(root,snap_root,rel):copied.append(rel)
  else:absent.append(rel)
 check=completeness_check(snap_root)
 if check['status']!='PASS':
  policy.ensure_dir(snap)
  (snap/'SNAPSHOT_COMPLETENESS_CHECK.json').write_bytes(js(check))
  policy.normalize_path(snap/'SNAPSHOT_COMPLETENESS_CHECK.json')
  raise AdapterError('SNAPSHOT_COMPLETENESS_CHECK failed: '+json.dumps(check['missing_files']))
 policy.copytree(snap_root,sandbox)
 if snapshot_hashes(snap_root)!=snapshot_hashes(sandbox):raise AdapterError('snapshot restore mismatch')
 files=[{'path':p,'size':(snap_root/p).stat().st_size,'sha256':sha256(snap_root/p)} for p in sorted(snapshot_hashes(snap_root))]
 (snap/'checksums.sha256').write_text(''.join(f"{x['sha256']}  root/{x['path']}\n" for x in files))
 policy.normalize_path(snap/'checksums.sha256')
 baseline=ENGINE/'baselines/mounk-pilot-versions.json'
 manifest={'phase':'5H','run_id':run_id,'source':S,'source_name':SN,'target':T,'target_name':TN,
           'created_at':time.time(),'snapshot_root':str(snap_root),'sandbox_offline_root':str(sandbox),
           'file_count':len(files),'total_bytes':sum(x['size'] for x in files),
           'baseline_versions':str(baseline),'baseline_sha256':sha256(baseline),
           'completeness_check':check,'absent_optional_files':absent,'files':files}
 (snap/'manifest.json').write_bytes(js(manifest))
 policy.normalize_path(snap/'manifest.json')
 return {'snapshot':str(snap),'snapshot_root':str(snap_root),'sandbox_offline_root':str(sandbox),
         'file_count':len(files),'total_bytes':manifest['total_bytes'],'completeness_check':check}
