"""Prepared controlled-pilot dispatcher. Never invoked during Phase 5G.1.

Adapters transform a copied staging tree. Publication requires an exact approved
input fingerprint, binary fingerprints, maintenance and the target Paper process
to be stopped. No server lifecycle command exists.
"""
import hashlib, json, os, re, shutil, socket, subprocess, time
from pathlib import Path
from .phase5g1 import ENGINE,S,T,CLASSES,OfflineRun,AdapterError,hashes,js,safe
from .ownership_policy import CHECK_NAME, OwnershipPolicy, mode_for_publish

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def binding(manifest,preflight):
 payload={'manifest':manifest,'fingerprints':preflight['fingerprints'],
          'versions_sha256':preflight['versions_sha256'],
          'runtime_versions_sha256':preflight.get('runtime_versions_sha256'),
          'implementation':{str(p.relative_to(ENGINE)):digest(p) for p in [ENGINE/'migrationctl.py',ENGINE/'lib/phase5g1.py',ENGINE/'lib/pilot.py']}}
 return hashlib.sha256(js(payload)).hexdigest()
def relative(root,key):
 p=Path(key)
 if p.is_absolute() or '..' in p.parts:raise AdapterError('unsafe relative path')
 out=Path(root)/p
 if not out.resolve().is_relative_to(Path(root).resolve()):raise AdapterError('path escape')
 if any(x.is_symlink() for x in [out,*out.parents]):raise AdapterError('symlink refused')
 if out.exists() and out.stat().st_nlink!=1:raise AdapterError('hard link refused')
 return out

def paper_processes(server_root):
 server_root=Path(server_root).resolve()
 out=[]
 for proc in Path('/proc').iterdir():
  if not proc.name.isdigit():continue
  try:
   raw=(proc/'cmdline').read_bytes()
   cwd=(proc/'cwd').resolve()
   cwd_denied=False
  except FileNotFoundError:continue
  except PermissionError:
   try:raw=(proc/'cmdline').read_bytes()
   except FileNotFoundError:continue
   cwd=None;cwd_denied=True
  parts=[os.fsdecode(x) for x in raw.split(b'\0') if x]
  exe=Path(parts[0]).name if parts else ''
  if exe not in ('java','javaw') or 'paper.jar' not in parts:continue
  if cwd==server_root or cwd_denied:
   out.append({'pid':int(proc.name),'cmdline':parts,'cwd':str(cwd) if cwd else None,'cwd_denied':cwd_denied})
 return out

def host_port_listener(port=25565):
 proc=subprocess.run(['ss','-ltnp',f'( sport = :{port} )'],capture_output=True,text=True)
 text=(proc.stdout or '')+(proc.stderr or '')
 lines=[x for x in text.splitlines() if 'LISTEN' in x]
 docker_publish=False
 docker_ps=subprocess.run(['docker','ps','--format','{{.Names}} {{.Ports}}'],capture_output=True,text=True)
 if docker_ps.returncode==0:
  docker_publish=any(f':{port}->' in x or f':{port}-' in x for x in docker_ps.stdout.splitlines())
 docker=any('docker-proxy' in x for x in lines)
 java=any('java' in x.lower() or 'paper' in x.lower() for x in lines)
 return {'present':bool(lines),'docker_proxy_only':bool(lines) and (docker or docker_publish) and not java,
         'active_backend':java,'docker_publish':docker_publish,'raw':lines}

def craft_container_paper_pids(server_uuid='c253fa7e-2bd2-4545-a9ba-b1a8db892197'):
 proc=subprocess.run(['docker','exec','crafty','sh','-lc',"ps -eo pid,args | grep '[p]aper.jar' || true"],capture_output=True,text=True)
 rows=[]
 for line in proc.stdout.splitlines():
  if 'paper.jar' in line:rows.append(line.strip())
 return rows

def storage_open_by_java(server_root):
 server_root=Path(server_root).resolve()
 proc=subprocess.run(['lsof','+D',str(server_root/'plugins'),str(server_root/'world/players')],capture_output=True,text=True)
 lines=[x for x in proc.stdout.splitlines() if 'java' in x.lower()]
 return lines

def backend_accepts_minecraft(host='127.0.0.1',port=25565):
 try:
  with socket.create_connection((host,port),timeout=2) as s:
   # Minecraft legacy ping. docker-proxy with no backend usually resets/refuses;
   # a Paper backend normally answers with a kick/status payload.
   s.sendall(b'\xfe\x01')
   data=s.recv(16)
   return bool(data)
 except OSError:
  return False

def shutdown_gate_status(manifest):
 listener=host_port_listener()
 backend=paper_processes(manifest['server_root'])
 container=craft_container_paper_pids(Path(manifest['server_root']).name)
 open_java=storage_open_by_java(manifest['server_root'])
 handshake=backend_accepts_minecraft()
 active_backend=bool(backend or container or open_java or handshake or listener['active_backend'])
 return {'host_port_listener':'YES' if listener['present'] else 'NO',
         'backend_process':'YES' if active_backend else 'NO',
         'docker_proxy_only':'YES' if listener['docker_proxy_only'] else 'NO',
         'shutdown_gate':'PASS' if not active_backend else 'BLOCK',
         'details':{'host_listener':listener,'host_paper_processes':backend,
                    'container_paper_processes':container,'java_storage_handles':open_java,
                    'minecraft_handshake_backend':handshake}}

def maintenance_gate(manifest):
 state_path=ENGINE/'state/maintenance.json'
 try:state=json.loads(state_path.read_text())
 except (FileNotFoundError,ValueError):raise AdapterError('MAINTENANCE_NOT_ACTIVE')
 if (not state.get('active') or state.get('run_id')!=manifest['run_id'] or
     state.get('server_root')!=manifest['server_root'] or state.get('source')!=S or state.get('target')!=T or
     state.get('expires_at',0)<=time.time()):raise AdapterError('MAINTENANCE_NOT_ACTIVE')
 shutdown=shutdown_gate_status(manifest)
 if shutdown['shutdown_gate']!='PASS':
  raise AdapterError('PLAYER_OFFLINE_RUNTIME_CHECK: Paper backend active; offline publication refused')
 return True

def verify_versions(root,artifacts):
 required={'Paper'}|{c.name for c in CLASSES if c.name!='Vanilla'}
 if set(artifacts)!=required:raise AdapterError('VERSION_BASELINE_MISSING')
 if len({e['path'] for e in artifacts.values()})!=len(artifacts):raise AdapterError('VERSION_BASELINE_INVALID: duplicate artifact paths')
 for name,entry in artifacts.items():
  p=relative(root,entry['path'])
  if p.suffix!='.jar':raise AdapterError('VERSION_BASELINE_INVALID: binary JAR required')
  if not p.is_file() or digest(p)!=entry['sha256']:raise AdapterError('VERSION_MISMATCH: '+name)
 return True

def write_atomic(destination,data,policy=None,mode_source=None):
 """Only the explicitly invoked publisher calls this; tests use a sandbox."""
 destination=Path(destination);policy=policy or OwnershipPolicy.detect(destination.parent)
 policy.check_path(destination.parent)
 if destination.exists():policy.check_path(destination)
 tmp=destination.with_name('.'+destination.name+'.migration-tmp');mode=mode_for_publish(destination,mode_source)
 with tmp.open('xb') as f:f.write(data);f.flush();os.fsync(f.fileno())
 os.chmod(tmp,mode);policy.normalize_path(tmp)
 os.replace(tmp,destination)
 policy.normalize_path(destination)
 policy.check_path(destination)
 fd=os.open(destination.parent,os.O_RDONLY|os.O_DIRECTORY)
 try:os.fsync(fd)
 finally:os.close(fd)

class Publication:
 """Publish already verified adapter outputs, restore all changed files on error."""
 def __init__(self,destination,original,staged):
  self.destination=Path(destination);self.original=Path(original);self.staged=Path(staged)
  expected=Path('/opt/minecraft/crafty/servers/c253fa7e-2bd2-4545-a9ba-b1a8db892197')
  if self.destination!=expected and not self.destination.resolve().is_relative_to(ENGINE/'sandbox'):raise AdapterError('unapproved server root')
  self.policy=OwnershipPolicy.detect(self.destination)
  self.before=hashes(original);self.after=hashes(staged)
  self.changed=sorted(k for k in self.before.keys()|self.after.keys() if self.before.get(k)!=self.after.get(k))
  if any(k not in self.after for k in self.changed):raise AdapterError('deletions forbidden in pilot')
 def check_inputs(self):
  self.policy.check_path(self.destination)
  for k,h in self.before.items():
   p=relative(self.destination,k)
   self.policy.check_path(p)
   if not p.is_file() or digest(p)!=h:raise AdapterError('FINGERPRINT_INVALIDATION: '+k)
  for k in self.changed:
   p=relative(self.destination,k)
   if p.parent.exists():self.policy.check_path(p.parent)
   if k not in self.before and p.exists():raise AdapterError('TARGET_COLLISION: '+k)
 def apply(self,gate,fail_after=None):
  gate();self.check_inputs();attempted=[]
  try:
   for k in self.changed:
    gate();p=relative(self.destination,k)
    # Recheck immediately before the atomic replacement.
    if k in self.before:
     if digest(p)!=self.before[k]:raise AdapterError('FINGERPRINT_INVALIDATION: '+k)
     self.policy.check_path(p)
    elif p.exists():raise AdapterError('TARGET_COLLISION: '+k)
    attempted.append(k);write_atomic(p,relative(self.staged,k).read_bytes(),self.policy,relative(self.staged,k))
    if k==fail_after:raise AdapterError('injected publication failure')
   for k,h in self.after.items():
    p=relative(self.destination,k)
    self.policy.check_path(p)
    if digest(p)!=h:raise AdapterError('GLOBAL_VERIFY_FAILED')
  except Exception:
   for k in reversed(attempted):
    p=relative(self.destination,k)
    if k in self.before:write_atomic(p,relative(self.original,k).read_bytes(),self.policy,relative(self.original,k))
    elif p.exists():self.policy.remove_file(p)
   self.check_inputs();raise
  return {'status':'COMMIT_STATE_VERIFIED','changed_paths':self.changed}

def dispatch(manifest,preflight,nonce,allow_write):
 if not allow_write:raise AdapterError('--allow-write required')
 lock=ENGINE/'state/pilot-publication.lock'
 try:fd=os.open(lock,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 except FileExistsError:raise AdapterError('PILOT_LOCKED')
 try:
  os.write(fd,str(os.getpid()).encode())
  return _dispatch_locked(manifest,preflight,nonce,allow_write)
 finally:
  os.close(fd);lock.unlink()

def _dispatch_locked(manifest,preflight,nonce,allow_write):
 # This entry point is preparation for a later approved pilot. Phase 5G.1 never
 # calls it. The CLI supplies a validated nonce and explicit write flag.
 if not allow_write:raise AdapterError('--allow-write required')
 if preflight.get('approval_nonce')!=nonce or preflight.get('nonce_expires_at',0)<=time.time() or preflight.get('run_id')!=manifest['run_id']:raise AdapterError('INVALID_NONCE')
 if preflight.get('approval_binding')!=binding(manifest,preflight):raise AdapterError('APPROVAL_FINGERPRINT_INVALIDATION')
 if preflight.get('status')=='ALREADY_MIGRATED':raise AdapterError('ALREADY_MIGRATED')
 if preflight.get('blockers'):raise AdapterError('STRUCTURAL_BLOCKERS: '+str(preflight['blockers']))
 if manifest['source_identity']['legacy_uuid']!=S or manifest['target_identity']['canonical_uuid']!=T:raise AdapterError('IDENTITY_MISMATCH')
 rid=manifest['run_id']
 if not re.fullmatch('[A-Za-z0-9_-]+',rid):raise AdapterError('invalid run id')
 destination=Path(manifest['server_root']);gate=lambda:maintenance_gate(manifest)
 gate();verify_versions(destination,manifest.get('runtime_versions',{}))
 workspace=safe(ENGINE/'sandbox'/('pilot-'+rid))
 # Creating exclusively prevents a used nonce/run from silently being replayed.
 workspace.mkdir(exist_ok=False)
 original=workspace/'original';original.mkdir();staged=workspace/'staged'
 expected={k:h for k,h in preflight['fingerprints'].items() if k!='checksums.sha256'}
 for k,h in expected.items():
  p=relative(destination,k)
  wal=Path(str(p)+'-wal')
  if wal.exists() and wal.stat().st_size:raise AdapterError('UNSNAPSHOTTED_SQLITE_WAL: '+k)
  if not p.is_file() or digest(p)!=h:raise AdapterError('FINGERPRINT_INVALIDATION: '+k)
  out=relative(original,k);OwnershipPolicy.detect(original).copy2(p,out)
 if hashes(original)!=expected:raise AdapterError('SNAPSHOT_FAILED')
 OwnershipPolicy.detect(original).copytree(original,staged)
 # All real adapters must be READY/NO_DATA/preserve; no partial-test bypass.
 run=OfflineRun(staged);plan=run.plan()
 if plan['blockers']:raise AdapterError('STRUCTURAL_BLOCKERS: '+str(plan['blockers']))
 candidates={str(p.relative_to(staged)) for a in run.adapters for p in a.paths()}
 candidates.add('plugins/Quests/data/'+T+'.yml')
 for k in candidates:
  if k not in expected and relative(destination,k).exists():raise AdapterError('TARGET_COLLISION: '+k)
 run.apply(workspace/'adapter-snapshots')
 publisher=Publication(destination,original,staged)
 gate();verify_versions(destination,manifest['runtime_versions']);publisher.check_inputs()
 used=ENGINE/'runs'/rid/'approval-used.json'
 with used.open('x') as f:json.dump({'run_id':rid,'binding':preflight['approval_binding'],'time':time.time()},f)
 OwnershipPolicy.detect(used.parent).normalize_path(used)
 result=publisher.apply(gate)
 (ENGINE/'runs'/rid/'pilot-result.json').write_bytes(js(result))
 return result
