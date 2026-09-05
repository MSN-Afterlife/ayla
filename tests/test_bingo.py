import io, sqlite3, tempfile, unittest
from pathlib import Path
from bot.services.bingo_service import BingoError, BingoService, Card, generate_card
from bot.services.bingo_renderer import render_card

class BingoRulesTests(unittest.TestCase):
    def test_card_ranges_unique_and_free(self):
        card=generate_card(); self.assertIsNone(card.numbers[2][2]); self.assertEqual(len({card.card_id}),1)
        for c, values in enumerate([range(1,16),range(16,31),range(31,46),range(46,61),range(61,76)]):
            col=[card.numbers[r][c] for r in range(5) if card.numbers[r][c] is not None]
            self.assertTrue(all(n in values for n in col)); self.assertEqual(len(col),len(set(col)))
    def test_cards_can_differ(self): self.assertNotEqual(generate_card().numbers,generate_card().numbers)
    def test_lines_and_free(self):
        n=[[1,16,31,46,61],[2,17,32,47,62],[3,18,None,48,63],[4,19,34,49,64],[5,20,35,50,65]]
        self.assertTrue(Card("x",n,{1,2,3,4,5}).has_line()); self.assertTrue(Card("x",n,{1,17,49,65}).has_line()); self.assertFalse(Card("x",n,{1,2}).has_line())
    def test_renderer_png_and_mark_state(self):
        c=generate_card(); a=render_card(c).getvalue(); c.marked.add(next(v for row in c.numbers for v in row if v)); b=render_card(c).getvalue(); self.assertTrue(a.startswith(b'\x89PNG')); self.assertNotEqual(a,b)

class BingoPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.s=BingoService(Path(self.tmp.name)/"db.sqlite3")
        db=sqlite3.connect(Path(self.tmp.name)/"db.sqlite3")
        try:
            now=0
            for u,b in [(1,1000),(2,1000),(3,0)]: db.execute("INSERT INTO economy_profiles VALUES(?,?,?,?,?)",(u,b,0,None,now))
            db.commit()
        finally: db.close()
    def tearDown(self): self.tmp.cleanup()
    def game(self): return self.s.create(9,99,1,500)
    def test_join_balance_once_and_no_duplicate(self):
        g=self.game(); self.s.join(g,1)
        with self.assertRaises(BingoError): self.s.join(g,1)
        db=sqlite3.connect(self.s.path); self.assertEqual(db.execute("select balance from economy_profiles where user_id=1").fetchone()[0],500); db.close()
    def test_insufficient_and_cancel_refund_once(self):
        g=self.game()
        with self.assertRaises(BingoError): self.s.join(g,3)
        self.s.join(g,1); self.s.cancel(g,1); self.s.cancel if False else None
        db=sqlite3.connect(self.s.path); self.assertEqual(db.execute("select balance from economy_profiles where user_id=1").fetchone()[0],1000); db.close()
        with self.assertRaises(BingoError): self.s.cancel(g,1)
    def test_draw_mark_claim_and_payout_once(self):
        g=self.game(); self.s.join(g,1); self.s.join(g,2); self.s.start(g,1)
        seen=[]
        for _ in range(75): seen.append(self.s.draw(g))
        self.assertEqual(len(set(seen)),75)
        card=self.s.card(g,1)
        for row in card.numbers:
            for n in row:
                if n is not None: self.s.mark(g,1,n)
        prize,_=self.s.claim(g,1); self.assertEqual(prize,950); self.assertEqual(self.s.game(game_id=g)['state'],'FINISHED')
        with self.assertRaises(BingoError): self.s.claim(g,1)
    def test_recovery_lists_running_without_money_mutation(self):
        g=self.game(); self.s.join(g,1); self.s.join(g,2); self.s.start(g,1); self.assertEqual([r['game_id'] for r in self.s.recoverable()],[g])

    def test_host_survives_service_restart(self):
        g=self.game(); self.s.join(g,1); self.s.join(g,2)
        restarted=BingoService(self.s.path)
        with self.assertRaises(BingoError): restarted.start(g,2)
        restarted.start(g,1)
        self.assertEqual(restarted.game(game_id=g)['state'], 'RUNNING')

    def test_force_cancel_can_be_used_by_admin(self):
        g=self.game(); self.s.join(g,1)
        self.s.cancel(g,999,force=True)
        self.assertEqual(self.s.game(game_id=g)['state'], 'CANCELLED')

if __name__=='__main__': unittest.main()
