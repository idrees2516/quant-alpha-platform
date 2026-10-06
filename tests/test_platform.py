"""Unit tests for the quant_alpha platform (stdlib unittest, no pytest).

Run:  python -m unittest discover -s tests -v
"""
import math
import unittest

from quant_alpha.alpha.devig import (devig_multiplicative, devig_power,
                                     devig_shin_two_way)
from quant_alpha.arbitrage.detectors import (detect_complementary,
                                             detect_dutch, detect_sports_dutch)
from quant_alpha.arbitrage.engine import ArbEngine
from quant_alpha.arbitrage.fees import (KalshiFee, ProfitFeeOnSettlement,
                                        ZeroFee, fee_models)
from quant_alpha.connectors import fixtures as fx
from quant_alpha.core.config import Config
from quant_alpha.market_making.microstructure import (FillIntensity,
                                                      calibrate_intensity,
                                                      micro_price, book_score)
from quant_alpha.market_making.rewards import (optimal_reward_spread,
                                               reward_rate)
from quant_alpha.market_making.strategies.adaptive import AdaptiveSpread
from quant_alpha.market_making.strategies.avellaneda_stoikov import \
    AvellanedaStoikov
from quant_alpha.market_making.strategies.base import QuoteContext
from quant_alpha.market_making.strategies.glft import GLFTStrategy, solve_glft
from quant_alpha.models.stats import ols, welch_t_test
from quant_alpha.normalize.aggregate import aggregate, build_composites
from quant_alpha.normalize.resolvers import build_clusters
from quant_alpha.core.domain import BookLevel


class TestDevig(unittest.TestCase):
    def test_multiplicative_sums_to_one(self):
        p = devig_multiplicative([0.55, 0.55])
        self.assertAlmostEqual(sum(p), 1.0, places=9)

    def test_shin_two_way(self):
        a, b = devig_shin_two_way(0.55, 0.55)
        self.assertAlmostEqual(a + b, 1.0, places=6)
        self.assertGreater(a, 0.5 - 1e-9)

    def test_power_method(self):
        p = devig_power([0.4, 0.4, 0.4])
        self.assertAlmostEqual(sum(p), 1.0, places=6)


class TestFees(unittest.TestCase):
    def test_kalshi_taker_fee_ceil(self):
        f = KalshiFee()
        self.assertAlmostEqual(f.taker_buy_cost(0.50), 0.50 + 0.04, places=9)
        self.assertAlmostEqual(f.taker_buy_cost(0.535), 0.535 + math.ceil(0.07 * 0.535 * 100) / 100, places=9)

    def test_predictit_settlement_fee(self):
        f = ProfitFeeOnSettlement()
        # buy YES at 0.60, wins: payout 1 - 0.10*(1-0.60) = 0.96
        self.assertAlmostEqual(f.settlement_payout(0.60, won=True), 0.96, places=9)
        self.assertAlmostEqual(f.settlement_payout(0.60, won=False), 0.0)

    def test_fee_models_registry(self):
        fm = fee_models()
        self.assertIsInstance(fm["kalshi"], KalshiFee)
        self.assertIsInstance(fm["predictit"], ProfitFeeOnSettlement)
        self.assertIsInstance(fm["polymarket"], ZeroFee)


class TestArbDetectors(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = Config.load()
        mkts = fx.fixture_markets()
        sports = fx.fixture_sports()
        cls.clusters = build_clusters(mkts, sports)
        cls.aggs = aggregate(cls.clusters)

    def test_sports_dutch_found(self):
        found = False
        for am in self.aggs:
            for o in detect_sports_dutch(am):
                found = True
                self.assertGreater(o.edge, 0.01)
                self.assertLess(o.net_cost, 1.0)
        self.assertTrue(found, "planted sports Dutch book must be detected")

    def test_polymarket_dutch_found(self):
        found = any(detect_dutch(am, 0.0) for am in self.aggs)
        self.assertTrue(found, "planted in-venue 4-way Dutch book must be detected")

    def test_complement_requires_yes_no(self):
        # candidate pairs (e.g. Vance+Newsom) must NOT be flagged as complements
        for am in self.aggs:
            for o in detect_complementary(am, 0.0):
                keys = {l.outcome_key.lower() for l in o.legs}
                if len(o.legs) == 2 and o.legs[0].venue == o.legs[1].venue:
                    self.assertTrue(keys & {"yes", "no"},
                                    f"non-complement pair flagged: {keys}")

    def test_engine_confirmation_semantics(self):
        cfg = Config.load(overrides={"offline": True})
        engine = ArbEngine(cfg)
        r1 = engine.scan(self.aggs)
        self.assertEqual(len(r1.actionable), 0, "first scan: only candidates")
        r2 = engine.scan(self.aggs)
        self.assertGreater(len(r2.actionable), 0, "second scan: confirmed")


class TestStrategies(unittest.TestCase):
    def _ctx(self, inventory=0, sigma=0.08, fair=0.55, A=1.2, k=120.0):
        return QuoteContext(
            venue="polymarket", market_id="t", outcome_key="Yes", tick=0.01,
            fair=fair, book_mid=fair, micro_price=fair, sigma=sigma, tau_h=12.0,
            inventory=inventory, max_inventory=100, quote_size=10,
            intensity=FillIntensity(A, k))

    def test_as_quotes_bounded_and_centered(self):
        s = AvellanedaStoikov()
        q = s.quotes(self._ctx())
        self.assertIsNotNone(q.bid)
        self.assertGreaterEqual(q.bid, 0.01)
        self.assertLessEqual(q.ask, 0.99)
        self.assertLess(q.bid, fair := 0.55)
        self.assertGreater(q.ask, fair)
        self.assertGreaterEqual(q.ask - q.bid, 2 * 0.01, "min 2-tick spread")

    def test_as_skews_away_from_inventory(self):
        s = AvellanedaStoikov()
        mid = s.quotes(self._ctx(inventory=0))
        long = s.quotes(self._ctx(inventory=80))
        # long inventory -> both quotes shift DOWN
        self.assertLess(long.bid, mid.bid)
        self.assertLess(long.ask, mid.ask)

    def test_glft_dp_converges_and_monotone(self):
        sol = solve_glft(A=1.5, k=120.0, gamma=0.8, sigma_p=0.02, Q=100, s=10)
        self.assertTrue(sol.converged)
        self.assertIn(0, sol.bid_half)
        # bid half-spread widens with inventory (long -> reluctant to buy)
        seq = [q for q in sorted(sol.bid_half) if q >= 0]
        for a, b in zip(seq, seq[1:]):
            self.assertGreaterEqual(sol.bid_half[b], sol.bid_half[a] - 1e-9)
        # ask half-spread narrows with inventory (long -> eager to sell)
        seqa = [q for q in sorted(sol.ask_half) if q >= 0]
        for a, b in zip(seqa, seqa[1:]):
            self.assertLessEqual(sol.ask_half[b], sol.ask_half[a] + 1e-9)

    def test_glft_strategy_quotes(self):
        s = GLFTStrategy()
        q = s.quotes(self._ctx())
        self.assertIsNotNone(q.bid)
        self.assertLess(q.bid, 0.55)
        self.assertGreater(q.ask, 0.55)

    def test_adaptive_earnings_mode(self):
        s = AdaptiveSpread()
        ctx = self._ctx()
        ctx.reward_params = {"enabled": 1.0, "pool_usd_per_hour": 500 / 24,
                             "max_spread": 0.03, "size_cap": 300.0,
                             "spread_power": 2.0, "book_score": 500.0}
        q = s.quotes(ctx)
        self.assertIsNotNone(q.bid)
        self.assertLess(q.bid, q.ask)

    def test_optimal_reward_spread_in_bounds(self):
        ctx = self._ctx()
        ctx.reward_params = {"enabled": 1.0, "pool_usd_per_hour": 20.0,
                             "max_spread": 0.03, "size_cap": 300.0,
                             "spread_power": 2.0, "book_score": 400.0}
        h = optimal_reward_spread(ctx)
        self.assertIsNotNone(h)
        self.assertGreaterEqual(h, ctx.tick)
        self.assertLessEqual(h, 0.03)

    def test_reward_rate_zero_outside_max_spread(self):
        ctx = self._ctx()
        ctx.reward_params = {"enabled": 1.0, "pool_usd_per_hour": 20.0,
                             "max_spread": 0.03, "size_cap": 300.0,
                             "spread_power": 2.0}
        self.assertEqual(reward_rate(ctx, 0.05), 0.0)
        self.assertGreater(reward_rate(ctx, 0.01), 0.0)


class TestMicrostructure(unittest.TestCase):
    def test_micro_price_weights_opposite_size(self):
        bids = [BookLevel(0.50, 100.0)]
        asks = [BookLevel(0.52, 300.0)]
        mp = micro_price(bids, asks)
        # heavy resting ASK size -> micro-price pulled toward the bid
        self.assertLess(mp, 0.51)
        self.assertGreater(mp, 0.50)

    def test_intensity_calibration_stable(self):
        mk = [m for m in fx.fixture_markets() if m.venue == "polymarket"][0]
        oq = mk.outcomes[0]
        i1 = calibrate_intensity(oq, oq.mid, 24.0)
        i2 = calibrate_intensity(oq, oq.mid, 24.0)
        self.assertEqual((i1.A, i1.k), (i2.A, i2.k), "must be deterministic")
        self.assertGreaterEqual(i1.k, 20.0)
        self.assertLessEqual(i1.k, 800.0)

    def test_book_score(self):
        bids = [BookLevel(0.49, 200.0), BookLevel(0.47, 100.0)]
        asks = [BookLevel(0.51, 200.0)]
        bs, as_ = book_score(bids, asks, 0.50, 0.03, 300.0, 2.0)
        self.assertGreater(bs, 0.0)
        # level at 3c away earns nothing
        self.assertAlmostEqual(bs, 200 * (1 - 0.01 / 0.03) ** 2, places=6)


class TestAggregation(unittest.TestCase):
    def test_clusters_match_fed_across_venues(self):
        clusters = build_clusters(fx.fixture_markets(), fx.fixture_sports())
        fed = [c for c in clusters if "fed" in c.title.lower() or "rate" in c.title.lower()]
        self.assertTrue(fed, "FED event must form a cluster")
        venues = {m.venue for m in fed[0].markets}
        self.assertIn("polymarket", venues)
        self.assertIn("kalshi", venues)

    def test_consensus_sums_to_one(self):
        aggs = aggregate(build_clusters(fx.fixture_markets(), fx.fixture_sports()))
        for am in aggs:
            self.assertAlmostEqual(sum(am.consensus), 1.0, places=6)
            self.assertTrue(all(0 < c < 1 for c in am.consensus))

    def test_composites_have_fee_adjusted_asks(self):
        aggs = aggregate(build_clusters(fx.fixture_markets(), fx.fixture_sports()))
        kalshi_comps = [c for am in aggs for c in am.composites
                        if c.asks and c.asks[0].venue == "kalshi"]
        self.assertTrue(kalshi_comps)
        for c in kalshi_comps:
            self.assertGreaterEqual(c.asks[0].eff_price, c.asks[0].price,
                                    "kalshi taker fee must raise eff ask")


class TestStats(unittest.TestCase):
    def test_ols_recovers_betas(self):
        y = [1.0 + 2.0 * x + 0.01 * (1 if i % 2 else -1)
             for i, x in enumerate(range(20))]
        X = [[float(x)] for x in range(20)]
        res = ols(y, X)
        self.assertAlmostEqual(res.betas[0], 1.0, places=1)
        self.assertAlmostEqual(res.betas[1], 2.0, places=2)
        self.assertGreater(res.r_squared, 0.99)

    def test_t_test_significance(self):
        xs = [0.5 + 0.1 * i for i in range(30)]
        t = welch_t_test(xs, 0.0)
        self.assertLess(t.p_value, 1e-6)
        t0 = welch_t_test([1.01, 0.99] * 15, 1.0)
        self.assertGreater(t0.p_value, 0.05)


if __name__ == "__main__":
    unittest.main()
