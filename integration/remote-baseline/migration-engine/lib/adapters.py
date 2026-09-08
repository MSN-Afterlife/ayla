"""Transactional adapters used by fixture tests and future maintenance runs.

They operate on a caller-supplied root only; the CLI never invokes them against
the live server.  Every mutating operation requires an explicit snapshot path.
"""
from __future__ import annotations
import json, os, shutil, sqlite3, tempfile
from pathlib import Path

class AdapterError(RuntimeError): pass

class AtomicFileAdapter:
    def __init__(self, source: Path, target: Path): self.source,self.target=Path(source),Path(target)
    def plan(self):
        return {'source_exists':self.source.exists(),'target_exists':self.target.exists(),
                'collision':self.source.exists() and self.target.exists(),
                'status':'BLOCKED' if self.source.exists() and self.target.exists() else ('READY' if self.source.exists() else 'NOOP'),
                'rollback':'supported','verify':'supported'}
    def snapshot(self, dst):
        if self.source.exists():
            dst=Path(dst); dst.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(self.source,dst)
    def apply(self, snapshot):
        if not self.source.exists() or self.target.exists(): raise AdapterError('source ausente ou target collision')
        Path(snapshot).parent.mkdir(parents=True,exist_ok=True); shutil.copy2(self.source,snapshot)
        tmp=self.target.with_name('.'+self.target.name+'.tmp'); shutil.copy2(self.source,tmp); os.replace(tmp,self.target)
        with open(self.target,'rb') as f: os.fsync(f.fileno())
    def verify(self): return self.target.exists() and self.target.read_bytes()==self.source.read_bytes()
    def rollback(self, snapshot):
        if not Path(snapshot).exists(): raise AdapterError('snapshot incompleto')
        if self.target.exists(): self.target.unlink()

class JsonNameAdapter(AtomicFileAdapter):
    def __init__(self, source, target): super().__init__(source,target)
    def verify(self):
        try: return json.loads(self.target.read_text())==json.loads(self.source.read_text())
        except Exception: return super().verify()

class SQLiteKeyAdapter:
    """Fixture-safe transaction; production DBs require maintenance/API wrappers."""
    def __init__(self, db, table, key_column, source, target): self.db=Path(db); self.table=table; self.key=key_column; self.source=source; self.target=target
    def plan(self):
        if not self.db.exists(): return {'status':'NOOP','source_records':0,'target_records':0,'collision':False,'rollback':'supported','verify':'supported'}
        c=sqlite3.connect(f'file:{self.db}?mode=ro',uri=True); q=f'SELECT "{self.key}",COUNT(*) FROM "{self.table}" WHERE "{self.key}" IN (?,?) GROUP BY "{self.key}"'; rows=dict(c.execute(q,(self.source,self.target)).fetchall()); c.close(); s,t=rows.get(self.source,0),rows.get(self.target,0)
        return {'status':'BLOCKED' if s and t else 'READY','source_records':s,'target_records':t,'collision':bool(s and t),'rollback':'supported','verify':'supported'}
    def apply(self):
        c=sqlite3.connect(self.db); c.execute('BEGIN IMMEDIATE')
        try:
            s,t=c.execute(f'SELECT COUNT(*), SUM(CASE WHEN "{self.key}"=? THEN 1 ELSE 0 END) FROM "{self.table}" WHERE "{self.key}" IN (?,?)',(self.target,self.source,self.target)).fetchone()
            if t: raise AdapterError('target collision')
            c.execute(f'UPDATE "{self.table}" SET "{self.key}"=? WHERE "{self.key}"=?',(self.target,self.source)); c.commit()
        except Exception: c.rollback(); raise
        finally: c.close()

class MigrationCoordinator:
    def __init__(self, adapters): self.adapters=adapters
    def plan(self): return [a.plan() for a in self.adapters]
    def apply_fixture(self, snapshot_dir):
        applied=[]
        try:
            for i,a in enumerate(self.adapters):
                s=Path(snapshot_dir)/str(i)
                if hasattr(a,'apply'): a.apply(s) if isinstance(a,AtomicFileAdapter) else a.apply()
                applied.append(a)
            if not all(a.verify() for a in applied if hasattr(a,'verify')): raise AdapterError('verify falhou')
        except Exception:
            for a in reversed(applied):
                if isinstance(a,AtomicFileAdapter): a.rollback(Path(snapshot_dir)/str(self.adapters.index(a)))
            raise
