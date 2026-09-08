#!/usr/bin/env python3
"""Safe, per-player Minecraft identity migration planner.

The default operations are read-only.  Apply/snapshot/rollback require explicit
flags and are additionally blocked when an unsupported critical adapter exists.
"""
from __future__ import annotations
import argparse, csv, hashlib, json, os, re, sys, uuid, secrets, time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

ENGINE = Path('/opt/minecraft/migration-engine')
AUDIT = Path('/opt/minecraft/migration-audit')
DEFAULT_ROOT = Path('/opt/minecraft/crafty/servers/c253fa7e-2bd2-4545-a9ba-b1a8db892197')

def now(): return datetime.now(timezone.utc).isoformat()
def sha256(p):
    h=hashlib.sha256()
    with open(p,'rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()
def read_json(p, default=None):
    try: return json.loads(Path(p).read_text())
    except Exception: return default
def run_dir(run_id): return ENGINE/'runs'/run_id
def acquire_lock(legacy, canonical):
    ld=ENGINE/'state'/'locks'; ld.mkdir(parents=True,exist_ok=True)
    keys=[legacy]+([canonical] if canonical else [])
    held=[]
    try:
        for key in keys:
            p=ld/(key+'.lock')
            fd=os.open(p,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o640)
            os.write(fd,(json.dumps({'created_at':now(),'pid':os.getpid(),'key':key})+'\n').encode()); os.close(fd); held.append(p)
    except FileExistsError:
        for p in held:
            try:p.unlink()
            except OSError:pass
        raise SystemExit('LOCKED: identidade já possui migração em andamento/registrada')
    return held
def append_history(event):
    h=ENGINE/'state'/'history.log'; h.parent.mkdir(parents=True,exist_ok=True)
    with h.open('a') as f: f.write(json.dumps(event,sort_keys=True)+'\n')

def source_files(root, u):
    candidates = {
      'playerdata':[root/'world'/'playerdata'/f'{u}.dat',root/'world'/'players'/'data'/f'{u}.dat'],
      'stats':[root/'world'/'stats'/f'{u}.json',root/'world'/'players'/'stats'/f'{u}.json'],
      'advancements':[root/'world'/'advancements'/f'{u}.json',root/'world'/'players'/'advancements'/f'{u}.json'],
    }
    out={}
    for k,paths in candidates.items(): out[k]=next((p for p in paths if p.exists()),paths[0])
    return out

def inspect_mvi(root, name):
    base=root/'plugins'/'Multiverse-Inventories'; found=[]
    if base.exists():
        for p in base.rglob(f'{name}.json'):
            found.append({'path':str(p.relative_to(root)),'size':p.stat().st_size,'sha256':sha256(p)})
    return found

def inspect_plugins(root, legacy, name):
    """Plan-only inventory. No binary or DB writes are attempted."""
    hits=[]
    for p in (root/'plugins').rglob('*'):
        if not p.is_file() or p.suffix.lower() in {'.jar','.db','.mv','.mv.db','.dat'}: continue
        try:
            data=p.read_bytes()
            if legacy.encode() in data or legacy.replace('-','').encode() in data or name.encode() in data:
                hits.append(str(p.relative_to(root)))
        except (OSError,UnicodeError): pass
    return hits

def build_manifest(args):
    legacy=str(uuid.UUID(args.legacy_uuid)).lower()
    canonical=str(uuid.UUID(args.canonical_uuid)).lower() if args.canonical_uuid else None
    rid=args.run_id or datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
    acquire_lock(legacy,canonical)
    rd=run_dir(rid); rd.mkdir(parents=True,exist_ok=False)
    names=[]
    audit=read_json(AUDIT/'players.json',{}) or {}
    for x in audit.get('players',[]):
        if x.get('legacy_uuid')==legacy: names=x.get('known_names',[]); break
    name=args.legacy_name or (names[0] if names else '')
    files=source_files(Path(args.server_root),legacy)
    manifest={'run_id':rid,'created_at':now(),'operator':os.getenv('USER','unknown'),
      'source_identity':{'legacy_uuid':legacy,'legacy_name':name},
      'target_identity':{'canonical_uuid':canonical,'canonical_name':args.canonical_name},
      'server_uuid':args.server_uuid,'server_root':args.server_root,
      'minecraft_version':None,'paper_version':None,'plugins_detected':[],
      'files_to_change':[],'databases_to_change':[],'adapters':[],
      'preconditions':[],'warnings':[],'conflicts':[],'snapshot_path':None,
      'changes':[],'verification_results':{},'status':'PREPARED'}
    manifest['preconditions']=[
      {'check':'server_root_exists','ok':Path(args.server_root).is_dir()},
      {'check':'server_uuid_expected','ok':args.server_uuid=='c253fa7e-2bd2-4545-a9ba-b1a8db892197'},
      {'check':'legacy_source_exists','ok':any(p.exists() for p in files.values())},
      {'check':'target_identity_explicit','ok':bool(canonical)},
      {'check':'player_offline','ok':False,'reason':'não verificado automaticamente; exigir janela controlada'},
    ]
    for kind,p in files.items():
        rec={'adapter':'vanilla','kind':kind,'source':str(p),'source_exists':p.exists(),
             'source_sha256':sha256(p) if p.exists() else None,
             'target':str(p.with_name(f'{canonical}{p.suffix}')) if canonical else None,
             'target_exists':bool(canonical and p.with_name(f'{canonical}{p.suffix}').exists()),
             'action':'BLOCKED_NO_CANONICAL_UUID' if not canonical else ('BLOCKED_COLLISION' if p.with_name(f'{canonical}{p.suffix}').exists() else 'FILE_TRANSFORM')}
        manifest['files_to_change'].append(rec)
    mvi=inspect_mvi(Path(args.server_root),name)
    manifest['adapters'].append({'name':'Vanilla','status':'SUPPORTED','records':len(files)})
    manifest['adapters'].append({'name':'Multiverse-Inventories','status':'SUPPORTED' if mvi else 'INSPECT_ONLY','records':mvi})
    for n,status,risk in [('SimpleLogin','BLOCKED_UNSUPPORTED','CRITICAL'),('AuraSkills','INSPECT_ONLY','HIGH'),('EliteMobs','INSPECT_ONLY','HIGH'),('Quests','INSPECT_ONLY','HIGH'),('HuskHomes','INSPECT_ONLY','HIGH'),('LuckPerms','BLOCKED_UNSUPPORTED','HIGH'),('UltimateTeams','INSPECT_ONLY','HIGH'),('SimplePets','INSPECT_ONLY','MEDIUM'),('Waypoints','INSPECT_ONLY','MEDIUM'),('MarriageMaster','BLOCKED_UNSUPPORTED','MEDIUM'),('ImageFrame','INSPECT_ONLY','MEDIUM')]:
        manifest['adapters'].append({'name':n,'status':status,'risk':risk})
    if not canonical: manifest['conflicts'].append('canonical_uuid ausente: APPLY impossível')
    if name in {'.Mounkass','Mounkass','.Ming_Gay1134','Ming_Gay1134','.Nickxz8820','Nickxz8820'}:
        d=read_json(AUDIT/'duplicates.json',[]) or []
        if any(name in g.get('names',[]) and legacy in g.get('uuids',[]) for g in d): manifest['conflicts'].append('POSSIBLE_DUPLICATE requer revisão manual')
    manifest['warnings'].append('Produção online-mode=false; UUIDs legados não são inferidos como Mojang')
    (rd/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (rd/'identity-decision.json').write_text(json.dumps({'canonical_uuid':canonical,'canonical_name':args.canonical_name,'legacy_identities':[],'decision':None,'allowed_values':['SINGLE_SOURCE','MERGE','ABORT'],'selected_source':None,'merge_plan_approved':False,'operator':None,'approved_at':None},indent=2)+'\n')
    append_history({'timestamp':now(),'run_id':rid,'operator':manifest['operator'],'command':'prepare','source_uuid':legacy,'target_uuid':canonical,'result':'PREPARED'})
    return rid,manifest

def dry_run(rid):
    rd=run_dir(rid); m=read_json(rd/'manifest.json')
    if not m: raise SystemExit('run_id inexistente')
    blocking=[]
    for f in m['files_to_change']:
        if f['action'].startswith('BLOCKED'): blocking.append(f['action'])
    for a in m['adapters']:
        if a.get('status')=='BLOCKED_UNSUPPORTED': blocking.append(a['name']+': adapter não seguro')
    for a in m.get('adapters',[]):
        a.setdefault('source_records',len(a.get('records',[])) if isinstance(a.get('records'),list) else a.get('records',0))
        a.setdefault('target_records',0); a.setdefault('requires_maintenance',a.get('name') in {'SimpleLogin','LuckPerms','MarriageMaster'})
        a.setdefault('rollback_capability','supported' if a.get('status') not in {'BLOCKED_UNSUPPORTED'} else 'pending')
        a.setdefault('verification_capability','supported')
    m['status']='BLOCKED' if blocking else 'DRY_RUN_OK'; m['conflicts']=sorted(set(m['conflicts']+blocking)); m['dry_run_at']=now()
    (rd/'manifest.json').write_text(json.dumps(m,indent=2)+'\n')
    (rd/'dry-run.json').write_text(json.dumps({'run_id':rid,'status':m['status'],'files':m['files_to_change'],'adapters':m['adapters'],'conflicts':m['conflicts'],'warnings':m['warnings']},indent=2)+'\n')
    append_history({'timestamp':now(),'run_id':rid,'operator':os.getenv('USER','unknown'),'command':'dry-run','source_uuid':m['source_identity']['legacy_uuid'],'target_uuid':m['target_identity']['canonical_uuid'],'result':m['status']})
    return m

def inspect_duplicate(rid):
    rd=run_dir(rid); m=read_json(rd/'manifest.json')
    if not m: raise SystemExit('run_id inexistente')
    legacy=m['source_identity']['legacy_uuid']; name=m['source_identity'].get('legacy_name','')
    groups=read_json(AUDIT/'duplicates.json',[]) or []; group=next((g for g in groups if legacy in g.get('uuids',[])),None)
    players=read_json(AUDIT/'players.json',{}).get('players',[]); members=[]
    for u in (group or {}).get('uuids',[legacy]):
        x=next((p for p in players if p.get('legacy_uuid')==u),None)
        if x: members.append({'legacy_uuid':u,'known_names':x.get('known_names',[]),'classification':x.get('classification'),'worlds':x.get('worlds',[]),'playerdata':x.get('playerdata',{}),'stats':x.get('stats',{}),'advancements':x.get('advancements',{}),'plugin_data_count':len(x.get('plugin_data',[]))})
    report={'run_id':rid,'status':'UNRESOLVED','reason':'nenhum vencedor escolhido automaticamente','group':group,'members':members,'evidence':['comparação read-only de UUIDs, nomes, timestamps, NBT resumido, stats, advancements e referências de plugins da auditoria Fase 5A'],'review_required':True}
    (rd/'duplicate-review.json').write_text(json.dumps(report,indent=2)+'\n'); return report

def pilot_preflight(rid):
    rd=run_dir(rid); m=read_json(rd/'manifest.json')
    if not m: raise SystemExit('run_id inexistente')
    from lib.phase5g1 import OfflineRun, safe, hashes, S, T
    root=Path(m.get('offline_root',m['server_root']))
    try:
        safe(root)
        result=OfflineRun(root).plan()
    except Exception as e:
        result={'status':'BLOCKED','adapters':[],'blockers':[str(e)]}
    decision=read_json(rd/'identity-decision.json',{})
    if (m['source_identity'].get('legacy_uuid')!=S or m['target_identity'].get('canonical_uuid')!=T
        or decision.get('decision')!='SINGLE_SOURCE' or decision.get('selected_source')!=S):
        result.setdefault('blockers',[]).append('IDENTITY_DECISION_MISMATCH')
    result['blockers']=sorted(set(result.get('blockers',[])+[
        a['name'] for a in result.get('adapters',[]) if a.get('status','').startswith('BLOCKED')]))
    baseline_path=ENGINE/'baselines/mounk-pilot-versions.json'
    baseline=read_json(baseline_path,{})
    if not m.get('runtime_versions') and baseline.get('artifacts'):
        m['runtime_versions']=baseline['artifacts']
        m['runtime_versions_source']=str(baseline_path)
        (rd/'manifest.json').write_text(json.dumps(m,indent=2,sort_keys=True)+'\n')
    from lib.pilot import binding, verify_versions, maintenance_gate
    if not m.get('runtime_versions'):
        result['blockers'].append('VERSION_BASELINE_MISSING')
    else:
        try:
            verify_versions(DEFAULT_ROOT,m['runtime_versions'])
        except Exception as e:
            result['blockers'].append(str(e))
    runtime_blockers=[]
    try:
        maintenance_gate(m)
    except Exception as e:
        text=str(e)
        if 'MAINTENANCE_NOT_ACTIVE' in text:
            runtime_blockers.append('MAINTENANCE_NOT_ACTIVE')
        if 'PLAYER_OFFLINE_RUNTIME_CHECK' in text:
            runtime_blockers.append('PLAYER_OFFLINE_RUNTIME_CHECK')
        if not runtime_blockers:
            runtime_blockers.append(text)
    result.update(run_id=rid,runtime_blockers=runtime_blockers)
    if result['blockers']:result['status']='BLOCKED'
    elif runtime_blockers:result['status']='READY_FOR_CONTROLLED_PILOT_PENDING_MAINTENANCE'
    else:result['status']='READY_FOR_CONTROLLED_PILOT'
    result.update(approval_nonce=secrets.token_urlsafe(24),nonce_expires_at=time.time()+900,
                  fingerprints=hashes(root) if root.is_dir() and root.resolve().is_relative_to(ENGINE/'sandbox') else {},
                  versions_sha256=sha256(ENGINE/'config/phase5g1-versions.json'),
                  runtime_versions_sha256=sha256(baseline_path) if baseline_path.exists() else None)
    result['approval_binding']=binding(m,result)
    (rd/'preflight.json').write_text(json.dumps(result,indent=2)+'\n')
    return result

def pilot_apply(rid, nonce, allow_write=False):
    if not allow_write: raise SystemExit('--allow-write obrigatório')
    p=run_dir(rid)/'preflight.json'; x=read_json(p,{})
    if not validate_nonce(x,nonce,rid): raise SystemExit('approval nonce inválido/expirado')
    from lib.phase5g1 import safe, hashes
    from lib.pilot import dispatch
    m=read_json(run_dir(rid)/'manifest.json',{})
    root=safe(Path(m.get('offline_root',m.get('server_root',''))))
    if x.get('blockers'): raise SystemExit('structural blockers: '+str(x['blockers']))
    if hashes(root)!=x.get('fingerprints'): raise SystemExit('fingerprint invalidation')
    if sha256(ENGINE/'config/phase5g1-versions.json')!=x.get('versions_sha256'): raise SystemExit('version mismatch')
    baseline_path=ENGINE/'baselines/mounk-pilot-versions.json'
    if x.get('runtime_versions_sha256') and sha256(baseline_path)!=x.get('runtime_versions_sha256'): raise SystemExit('runtime version baseline mismatch')
    # Runtime maintenance/offline checks are evaluated again by dispatch, not
    # inferred from a stale preflight or an operator-edited offline boolean.
    result=dispatch(m,x,nonce,allow_write)
    print(json.dumps(result,indent=2))
    return result

def validate_nonce(preflight, nonce, run_id=None):
    return bool(preflight and preflight.get('approval_nonce')==nonce and time.time() < preflight.get('nonce_expires_at',0) and (run_id is None or preflight.get('run_id')==run_id))

def relevance_blocks(relevance, ready_names):
    out=[]
    for n,v in relevance.items():
        if v.get('status','').startswith('BLOCKED'): out.append(n+': adapter BLOCKED')
        if v.get('status')=='UNKNOWN' and v.get('risk','HIGH') in ('CRITICAL','HIGH'): out.append(n+': relevance UNKNOWN')
        if v.get('status')=='HAS_DATA' and v.get('migration_required',True) and n not in ready_names: out.append(n+': HAS_DATA sem adapter READY específico')
    return out

def guarded_write(args, action):
    if not args.allow_write: raise SystemExit(f'{action} recusado: use --allow-write explicitamente')
    raise SystemExit(f'{action} bloqueado nesta versão: adapters críticos/produção exigem implementação e janela de manutenção')

def main():
    ap=argparse.ArgumentParser(prog='migrationctl'); ap.add_argument('--server-root',default=str(DEFAULT_ROOT)); ap.add_argument('--server-uuid',default='c253fa7e-2bd2-4545-a9ba-b1a8db892197'); ap.add_argument('--allow-write',action='store_true')
    sp=ap.add_subparsers(dest='cmd',required=True)
    pp=sp.add_parser('prepare'); pp.add_argument('--legacy-uuid',required=True); pp.add_argument('--legacy-name'); pp.add_argument('--canonical-uuid'); pp.add_argument('--canonical-name'); pp.add_argument('--run-id')
    dp=sp.add_parser('dry-run'); dp.add_argument('--run-id',required=True)
    ip=sp.add_parser('inspect-duplicate'); ip.add_argument('--run-id',required=True)
    fp=sp.add_parser('pilot-preflight'); fp.add_argument('--run-id',required=True)
    pa=sp.add_parser('pilot-apply'); pa.add_argument('--run-id',required=True); pa.add_argument('--approval-nonce',required=True); pa.add_argument('--allow-write',action='store_true')
    fc=sp.add_parser('final-cutover')
    fc.add_argument('--source-uuid',default='555dd93f-a696-3e49-978b-398ad2208571')
    fc.add_argument('--source-name',default='Mounkass')
    fc.add_argument('--target-uuid',default='2eab1746-315b-3e81-b640-7f8241d3f2cd')
    fc.add_argument('--target-name',default='Mounk')
    fc.add_argument('--wrong-target-uuid',default='70f73129-6bc7-47af-9248-d0f2ec3a891d')
    fc.add_argument('--confirm-final-cutover')
    fc.add_argument('--sandbox-root')
    fc.add_argument('--execute',action='store_true')
    fc.add_argument('--allow-write',action='store_true')
    for c in ('snapshot','apply','rollback'): q=sp.add_parser(c); q.add_argument('--run-id',required=True); q.add_argument('--allow-write',action='store_true')
    st=sp.add_parser('status'); st.add_argument('--run-id')
    a=ap.parse_args()
    if a.cmd=='prepare': rid,m=build_manifest(a); print(json.dumps({'run_id':rid,'status':m['status'],'manifest':str(run_dir(rid)/'manifest.json')},indent=2)); return
    if a.cmd=='dry-run': print(json.dumps(dry_run(a.run_id),indent=2)); return
    if a.cmd=='inspect-duplicate': print(json.dumps(inspect_duplicate(a.run_id),indent=2)); return
    if a.cmd=='pilot-preflight': print(json.dumps(pilot_preflight(a.run_id),indent=2)); return
    if a.cmd=='pilot-apply': pilot_apply(a.run_id,a.approval_nonce,a.allow_write); return
    if a.cmd=='final-cutover':
        from lib import final_cutover
        if (a.source_uuid,a.source_name,a.target_uuid,a.target_name,a.wrong_target_uuid)!=(
            final_cutover.SOURCE,final_cutover.SOURCE_NAME,final_cutover.TARGET,final_cutover.TARGET_NAME,final_cutover.WRONG_TARGET):
            raise SystemExit('FINAL_CUTOVER_IDENTITY_MISMATCH')
        if a.execute:
            if not a.allow_write: raise SystemExit('--allow-write obrigatório')
            if a.confirm_final_cutover!='Mounk-2eab-final': raise SystemExit('CONFIRM_FINAL_CUTOVER_REQUIRED')
            result = final_cutover.execute_final_cutover(Path(a.server_root))
            print(json.dumps(result,indent=2,sort_keys=True)); return
        print(json.dumps(final_cutover.plan_only(a),indent=2,sort_keys=True)); return
    if a.cmd=='status':
        ds=[run_dir(a.run_id)] if a.run_id else sorted((ENGINE/'runs').glob('*'))
        for d in ds:
            m=read_json(d/'manifest.json'); print(d.name, (m or {}).get('status','unknown'))
        return
    guarded_write(a,a.cmd)
if __name__=='__main__': main()
