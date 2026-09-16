from __future__ import annotations

import json, secrets, sqlite3, time
from contextlib import closing
from dataclasses import dataclass
from functools import wraps
from pathlib import Path

from bot.services.economy_write_gate import EconomyWriteGate

RANGES = (range(1, 16), range(16, 31), range(31, 46), range(46, 61), range(61, 76))
PATTERNS = ("line",)

@dataclass
class Card:
    card_id: str
    numbers: list[list[int | None]]
    marked: set[int]
    revision: int = 0

    def has_line(self) -> bool:
        marked = self.marked | {self.numbers[2][2]}
        lines = [self.numbers[r] for r in range(5)]
        lines += [[self.numbers[r][c] for r in range(5)] for c in range(5)]
        lines += [[self.numbers[i][i] for i in range(5)], [self.numbers[i][4-i] for i in range(5)]]
        return any(all(value is None or value in marked for value in line) for line in lines)

def generate_card(card_id: str | None = None, rng=None) -> Card:
    rng = rng or secrets.SystemRandom()
    columns = [rng.sample(list(column), 5) for column in RANGES]
    numbers = [[None if r == 2 and c == 2 else columns[c][r] for c in range(5)] for r in range(5)]
    return Card(card_id or secrets.token_hex(8), numbers, {None})

class BingoError(Exception): pass


def _economic_write(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._write_gate.write():
            return method(self, *args, **kwargs)
    return wrapped

class BingoService:
    def __init__(self, database_path: str | Path, entry_sink_percent: int = 5, max_players: int = 20, min_players: int = 2, write_gate: EconomyWriteGate | None = None):
        self.path = Path(database_path); self.path.parent.mkdir(parents=True, exist_ok=True)
        self._write_gate = write_gate or EconomyWriteGate.bootstrap_open(self.path.with_name(self.path.name + ".economy_gate.json"))
        self.entry_sink_percent, self.max_players, self.min_players = entry_sink_percent, max_players, min_players
        self._initialize()

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=30); db.row_factory = sqlite3.Row; return db
    def _initialize(self):
        with closing(self._connect()) as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS bingo_games (game_id TEXT PRIMARY KEY, guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL UNIQUE, message_id INTEGER, host_id INTEGER NOT NULL, state TEXT NOT NULL, entry INTEGER NOT NULL, pot INTEGER NOT NULL DEFAULT 0, sink INTEGER NOT NULL DEFAULT 0, prize INTEGER NOT NULL DEFAULT 0, draw_order TEXT NOT NULL, draw_position INTEGER NOT NULL DEFAULT 0, drawn TEXT NOT NULL DEFAULT '[]', winner_id INTEGER, winner_pattern TEXT, created_at INTEGER NOT NULL, started_at INTEGER, finished_at INTEGER);
            CREATE TABLE IF NOT EXISTS bingo_players (game_id TEXT NOT NULL, user_id INTEGER NOT NULL, card_id TEXT NOT NULL, numbers TEXT NOT NULL, marked TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0, paid INTEGER NOT NULL DEFAULT 0, refunded INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(game_id,user_id), UNIQUE(game_id,card_id));
            CREATE TABLE IF NOT EXISTS bingo_events (id INTEGER PRIMARY KEY AUTOINCREMENT, game_id TEXT NOT NULL, event_type TEXT NOT NULL, user_id INTEGER, payload TEXT, created_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS economy_profiles (user_id INTEGER PRIMARY KEY, balance INTEGER NOT NULL DEFAULT 0, daily_streak INTEGER NOT NULL DEFAULT 0, last_daily_at INTEGER, updated_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS economy_transactions (idempotency_key TEXT PRIMARY KEY, user_id INTEGER NOT NULL, amount INTEGER NOT NULL, kind TEXT NOT NULL, created_at INTEGER NOT NULL);
            ''')
    def create(self, guild_id, channel_id, host_id, entry):
        if entry <= 0: raise BingoError("A entrada precisa ser maior que zero.")
        game_id = secrets.token_hex(12); now = int(time.time())
        with closing(self._connect()) as db:
            try: db.execute("INSERT INTO bingo_games(game_id,guild_id,channel_id,host_id,state,entry,draw_order,created_at) VALUES(?,?,?,?,?,?,?,?)", (game_id,guild_id,channel_id,host_id,"WAITING",entry,json.dumps([]),now)); db.commit()
            except sqlite3.IntegrityError: raise BingoError("Ja existe um Bingo neste canal.")
        return game_id
    def set_message(self, game_id, message_id):
        with closing(self._connect()) as db:
            db.execute("UPDATE bingo_games SET message_id=? WHERE game_id=?", (message_id, game_id)); db.commit()
    @_economic_write
    def cancel(self, game_id, actor_id, force=False):
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE"); g=db.execute("SELECT * FROM bingo_games WHERE game_id=?",(game_id,)).fetchone()
            if not g or g['state'] != 'WAITING' or (not force and g['host_id'] != actor_id): raise BingoError("A sala so pode ser cancelada pelo host ou por um administrador enquanto aguarda jogadores.")
            now=int(time.time())
            for p in db.execute("SELECT * FROM bingo_players WHERE game_id=? AND refunded=0",(game_id,)).fetchall():
                key=f"bingo:{game_id}:refund:{p['user_id']}"; db.execute("UPDATE economy_profiles SET balance=balance+?,updated_at=? WHERE user_id=?",(g['entry'],now,p['user_id'])); db.execute("INSERT OR IGNORE INTO economy_transactions VALUES(?,?,?,?,?)",(key,p['user_id'],g['entry'],'credit',now)); db.execute("UPDATE bingo_players SET refunded=1 WHERE game_id=? AND user_id=?",(game_id,p['user_id']))
            db.execute("UPDATE bingo_games SET state='CANCELLED',finished_at=? WHERE game_id=?",(now,game_id)); db.commit()
    @_economic_write
    def leave(self, game_id, user_id):
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE"); g=db.execute("SELECT * FROM bingo_games WHERE game_id=?",(game_id,)).fetchone(); p=db.execute("SELECT * FROM bingo_players WHERE game_id=? AND user_id=?",(game_id,user_id)).fetchone()
            if not g or g['state'] != 'WAITING' or not p: raise BingoError("Voce nao pode sair desta sala agora.")
            now=int(time.time()); key=f"bingo:{game_id}:refund:{user_id}"; db.execute("UPDATE economy_profiles SET balance=balance+?,updated_at=? WHERE user_id=?",(g['entry'],now,user_id)); db.execute("INSERT OR IGNORE INTO economy_transactions VALUES(?,?,?,?,?)",(key,user_id,g['entry'],'credit',now)); db.execute("DELETE FROM bingo_players WHERE game_id=? AND user_id=?",(game_id,user_id)); db.execute("UPDATE bingo_games SET pot=pot-? WHERE game_id=?",(g['entry'],game_id)); db.commit()
    def recoverable(self):
        with closing(self._connect()) as db: return db.execute("SELECT * FROM bingo_games WHERE state='RUNNING' ORDER BY created_at").fetchall()
    def game(self, game_id=None, channel_id=None):
        with closing(self._connect()) as db:
            return db.execute("SELECT * FROM bingo_games WHERE game_id = ? OR channel_id = ?", (game_id, channel_id)).fetchone()
    @_economic_write
    def join(self, game_id, user_id):
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE"); game=db.execute("SELECT * FROM bingo_games WHERE game_id=?",(game_id,)).fetchone()
            if not game or game['state'] != 'WAITING': raise BingoError("Esta sala nao esta recebendo jogadores.")
            if db.execute("SELECT 1 FROM bingo_players WHERE game_id=? AND user_id=?",(game_id,user_id)).fetchone(): raise BingoError("Voce ja esta nesta sala.")
            if db.execute("SELECT COUNT(*) n FROM bingo_players WHERE game_id=?",(game_id,)).fetchone()['n'] >= self.max_players: raise BingoError("A sala esta cheia.")
            card=generate_card(); now=int(time.time()); key=f"bingo:{game_id}:entry:{user_id}"
            row=db.execute("SELECT balance FROM economy_profiles WHERE user_id=?",(user_id,)).fetchone()
            if not row or row['balance'] < game['entry']: raise BingoError("Saldo insuficiente.")
            db.execute("UPDATE economy_profiles SET balance=balance-?,updated_at=? WHERE user_id=?",(game['entry'],now,user_id))
            db.execute("INSERT INTO economy_transactions(idempotency_key,user_id,amount,kind,created_at) VALUES(?,?,?,'debit',?)",(key,user_id,game['entry'],now))
            db.execute("INSERT INTO bingo_players(game_id,user_id,card_id,numbers,marked,paid) VALUES(?,?,?,?,?,1)",(game_id,user_id,card.card_id,json.dumps(card.numbers),json.dumps([])))
            db.execute("UPDATE bingo_games SET pot=pot+? WHERE game_id=?",(game['entry'],game_id)); db.commit()
    def start(self, game_id, actor_id):
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE"); game=db.execute("SELECT * FROM bingo_games WHERE game_id=?",(game_id,)).fetchone()
            if not game or game['state'] != 'WAITING' or game['host_id'] != actor_id: raise BingoError("Somente o host pode iniciar uma sala valida.")
            if db.execute("SELECT COUNT(*) n FROM bingo_players WHERE game_id=?",(game_id,)).fetchone()['n'] < self.min_players: raise BingoError(f"Sao necessarios pelo menos {self.min_players} jogadores.")
            order=list(range(1,76)); secrets.SystemRandom().shuffle(order); now=int(time.time()); db.execute("UPDATE bingo_games SET state='RUNNING',draw_order=?,started_at=? WHERE game_id=?",(json.dumps(order),now,game_id)); db.commit()
    def draw(self, game_id):
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE"); g=db.execute("SELECT * FROM bingo_games WHERE game_id=?",(game_id,)).fetchone()
            if not g or g['state'] != 'RUNNING' or g['draw_position'] >= 75: return None
            order=json.loads(g['draw_order']); number=order[g['draw_position']]; drawn=json.loads(g['drawn'])+[number]; db.execute("UPDATE bingo_games SET draw_position=draw_position+1,drawn=? WHERE game_id=?",(json.dumps(drawn),game_id)); db.commit(); return number
    def mark(self, game_id, user_id, number, marked=True):
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE"); g=db.execute("SELECT * FROM bingo_games WHERE game_id=?",(game_id,)).fetchone(); p=db.execute("SELECT * FROM bingo_players WHERE game_id=? AND user_id=?",(game_id,user_id)).fetchone()
            if not g or g['state'] != 'RUNNING' or not p: raise BingoError("Esta acao nao esta disponivel agora.")
            numbers=json.loads(p['numbers']); values={v for row in numbers for v in row if v}; drawn=set(json.loads(g['drawn'])); marks=set(json.loads(p['marked']))
            if number not in values: raise BingoError("Esse numero nao esta na sua cartela.")
            if marked and number not in drawn: raise BingoError("Esse numero ainda nao foi sorteado.")
            marks.add(number) if marked else marks.discard(number); db.execute("UPDATE bingo_players SET marked=?,revision=revision+1 WHERE game_id=? AND user_id=?",(json.dumps(sorted(marks)),game_id,user_id)); db.commit()
    @_economic_write
    def claim(self, game_id, user_id):
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE"); g=db.execute("SELECT * FROM bingo_games WHERE game_id=?",(game_id,)).fetchone(); p=db.execute("SELECT * FROM bingo_players WHERE game_id=? AND user_id=?",(game_id,user_id)).fetchone()
            if not g or g['state'] != 'RUNNING' or not p: raise BingoError("Essa partida nao esta ativa.")
            card=Card(p['card_id'],json.loads(p['numbers']),set(json.loads(p['marked'])))
            if not card.has_line(): raise BingoError("❌ Isso definitivamente nao e um bingo.")
            sink=g['pot']*self.entry_sink_percent//100; prize=g['pot']-sink; now=int(time.time())
            db.execute("UPDATE bingo_games SET state='FINISHED',winner_id=?,winner_pattern='line',sink=?,prize=?,finished_at=? WHERE game_id=?",(user_id,sink,prize,now,game_id))
            db.execute("UPDATE economy_profiles SET balance=balance+?,updated_at=? WHERE user_id=?",(prize,now,user_id)); db.execute("INSERT INTO economy_transactions VALUES(?,?,?,?,?)",(f"bingo:{game_id}:prize",user_id,prize,'credit',now)); db.commit(); return prize, card
    def card(self, game_id, user_id):
        with closing(self._connect()) as db:
            p=db.execute("SELECT * FROM bingo_players WHERE game_id=? AND user_id=?",(game_id,user_id)).fetchone()
            if not p: raise BingoError("Voce nao esta nesta partida.")
            return Card(p['card_id'],json.loads(p['numbers']),set(json.loads(p['marked'])),p['revision'])
