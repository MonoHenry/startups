import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import radar_rondas as rr  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
TODAY = dt.date(2026, 11, 17)


def ex(title, source="X", dollar="USD"):
    return rr.extract({"title": title, "link": "", "source": source}, dollar)


class TestExtract(unittest.TestCase):
    # Real headlines seen in the feeds on 2026-11-17.
    def test_company_names(self):
        cases = {
            "Leeds AI spinout adsilico raises £10M to test medical devices on virtual patients": "adsilico",
            "Nous lands $17M to bring AI-powered household management to more homes": "Nous",
            "London's Model ML raises $250M from General Atlantic and QED to put AI agents inside banks": "Model ML",
            "Ex-H Company researchers' Remanence raises $30M to turn AI agent data into models companies own": "Remanence",
            "Defence unicorn Tiberius raises $85M to scale its long-range ramjet artillery shell": "Tiberius",
            "RHISE AI raises €2.66M with backing from an early Google investor to build Europe's personal AI": "RHISE AI",
            "Polish Asynchronics lands $3.1M seed led by b2venture for spacecraft digital twins": "Asynchronics",
            "Exclusive: Nubank Alum Raises $14M For Colombia's Palomma To Tackle Rent Payments, Landlord Financing": "Palomma",
            "Verafin alum-led Verlo nabs $4.5 million to build AI operating system for wealth managers": "Verlo",
            "Heylist closes $3.1 million to fuel the niche creator economy": "Heylist",
            "Corma hits $1B valuation three months after stealth as NEA leads $125M Series A for defencive AI": "Corma",
            "Fintech paranaense Xpto capta R$ 30 milhões em rodada Série A": "Xpto",
            "Startup Lumen raises $20M Series A to fund expansion": "Lumen",
        }
        for title, company in cases.items():
            with self.subTest(title=title):
                r = ex(title)
                self.assertIsNotNone(r)
                self.assertEqual(r["company"], company)

    def test_not_startup_rounds(self):
        for title in [
            "Helsing backer Plural raises $1.1B to fund Europe's next moonshots",
            "Plural raises fresh €1bn fund to back 'non-obvious companies'",
            "Proximal Ventures raises debut fund to back startups bringing AI to drug development",
            "Serena Williams winds down her VC firm, launches new fund, seeks $200M",
            "Norwest pushes deeper into AI with new $4B fund",
            "European tech weekly recap: €1.5B in funding and Q3 2026 highlights",
            "Europe's biggest seed rounds of Q3 2026: Top tech startups to watch",
            "ICEYE bids €2.05B for Avio to bring satellites and rockets under one European roof",
            "Marcha Ventures destinará R$ 10 milhões em aportes a startups e deep techs nos próximos três anos",
            "Sequoia shutting down its Scouts program marks the end of an era",
            "Venture Kick backs CHD with €160K to advance remote pregnancy monitoring",
        ]:
            with self.subTest(title=title):
                self.assertIsNone(ex(title))

    def test_valuation_is_not_the_round(self):
        r = ex("Corma hits $1B valuation three months after stealth as NEA leads $125M Series A for defencive AI")
        self.assertEqual((r["round"], r["amount"], r["amount_usd_m"]), ("Series A", "US$125M", 125.0))

    def test_amounts_and_currencies(self):
        self.assertEqual(ex("Leeds AI spinout adsilico raises £10M to test")["amount"], "£10M")
        self.assertEqual(ex("RHISE AI raises €2.66M with backing")["amount"], "€2.66M")
        self.assertEqual(ex("Heylist closes $3.1 million", "BetaKit", "CAD")["amount"], "CA$3.1M")
        r = ex("Fintech paranaense Xpto capta R$ 1,5 milhão em rodada seed")
        self.assertEqual((r["amount"], r["round"]), ("R$1.5M", "Seed"))
        self.assertEqual(ex("Acme raises $1.1B growth round")["amount"], "US$1.1B")
        self.assertEqual(ex("Acme raises $500K pre-seed")["amount"], "US$500K")

    def test_rounds(self):
        self.assertEqual(ex("Acme raises $5M pre-seed")["round"], "Pre-seed")
        self.assertEqual(ex("Acme raises $5M pre-Series A")["round"], "Pre-Series A")
        self.assertEqual(ex("Acme raises $40M Series C")["round"], "Series C")
        self.assertEqual(ex("Acme capta R$ 30 milhões em Série B")["round"], "Series B")


class TestFilter(unittest.TestCase):
    def rec(self, rnd, usd):
        return {"round": rnd, "amount_usd_m": usd}

    def test_post_seed_rule(self):
        self.assertTrue(rr.is_post_seed(self.rec("Series A", 3), 8, False))
        self.assertFalse(rr.is_post_seed(self.rec("Seed", 30), 8, False))     # stage label wins over size
        self.assertTrue(rr.is_post_seed(self.rec("Seed", 30), 8, True))
        self.assertFalse(rr.is_post_seed(self.rec("Pre-Series A", 9), 8, False))
        self.assertTrue(rr.is_post_seed(self.rec("", 13), 8, False))          # unlabeled but big
        self.assertFalse(rr.is_post_seed(self.rec("", 2.9), 8, False))
        self.assertFalse(rr.is_post_seed(self.rec("", None), 8, False))


class TestEndToEnd(unittest.TestCase):
    def fake_fetch(self, url):
        if url == "rss":
            return (FIX / "sample_rss.xml").read_bytes()
        if url == "atom":
            return (FIX / "sample_atom.xml").read_bytes()
        raise ConnectionError("blocked")

    def test_run_twice(self):
        feeds = {"Sample RSS": "rss", "Sample Atom": "atom", "Broken": "broken"}
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            res = rr.run(out, TODAY, feeds=feeds, fetcher=self.fake_fetch)
            names = [r["company"] for r in res["post"]]
            # Sorted by size; Model ML appears twice in the feed but is listed once,
            # and the labeled "Series B" from the second headline fills the gap.
            self.assertEqual(names, ["Model ML", "Corma", "adsilico", "Xpto"])
            self.assertEqual(res["post"][0]["round"], "Series B")
            self.assertEqual([r["company"] for r in res["other"]], ["Asynchronics"])
            self.assertEqual(len(res["errors"]), 1)                          # Broken feed logged, run continues
            self.assertNotIn("Old Startup", res["markdown"])                 # older than --days
            md = (out / "daily" / "2026-11-17.md").read_text(encoding="utf-8")
            self.assertIn("| [Model ML](https://example.com/2) | Series B | US$250M |", md)
            self.assertIn("linkedin.com/search/results/people/?keywords=Model+ML+founder", md)

            # Next day: same feed content -> nothing new (dedupe through data/companies.csv).
            res2 = rr.run(out, TODAY + dt.timedelta(days=1), feeds=feeds, fetcher=self.fake_fetch)
            self.assertEqual(res2["post"], [])
            self.assertEqual(res2["other"], [])


if __name__ == "__main__":
    unittest.main()
