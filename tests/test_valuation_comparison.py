import unittest
from pathlib import Path
from types import SimpleNamespace

from jinja2 import Environment

from report_app.valuation_comparison import build_comparison


class ComparisonTests(unittest.TestCase):
    def build(self, forecasts=None, price=232, **manual_overrides):
        shares = 37282306
        company = SimpleNamespace(price=price, annual_performance=forecasts if forecasts is not None else [
            {"period": "2026.03", "is_forecast": False, "ordinary_income": 541},
            {"period": "2027.03", "is_forecast": True, "ordinary_income": 1700},
        ])
        manual = {"shares_issued": shares, "treasury_shares": 0,
                  "total_liabilities": 19000, "noncontrolling_interests": 0}
        manual.update(manual_overrides)
        liquidation = {"value": 142 * shares / 1e6, "adjusted_assets": 19000 + 142 * shares / 1e6}
        taachan = {"cash_power_value": 256 * shares / 1e6, "net_cash": -1584,
                   "fcf": 1113, "noncontrolling_interests": 0}
        dcf = {"scenarios": [{"key": "base", "equity_value": 398 * shares / 1e6,
                              "growth": .02, "wacc": .08, "terminal_growth": .005}]}
        return build_comparison(company, manual, "2026.03", liquidation, taachan, dcf)

    def test_company_forecast_is_year_one_not_grown_again(self):
        result = self.build()
        self.assertEqual([y["profit"] for y in result["years"]][:3], [1700, 2040, 2448])
        growth = next(r for r in result["rows"] if r["key"] == "growth")
        self.assertAlmostEqual(growth["value"], 291.1195221471676)
        self.assertAlmostEqual(growth["pct"], (291.1195221471676 - 232) / 232 * 100)

    def test_forecast_missing_does_not_use_actual_profit(self):
        result = self.build(forecasts=[{"period": "2026.03", "is_forecast": False, "ordinary_income": 1700}])
        growth = next(r for r in result["rows"] if r["key"] == "growth")
        self.assertIsNone(growth["value"])
        self.assertIsNone(growth["pct"])
        self.assertEqual(result["years"], [])

    def test_nearest_upcoming_forecast_not_distant_or_old_year(self):
        result = self.build(forecasts=[
            {"period": "2026.03", "is_forecast": True, "ordinary_income": 500},
            {"period": "2028.03", "is_forecast": True, "ordinary_income": 3000},
            {"period": "2027.03", "is_forecast": True, "ordinary_income": 1700},
        ])
        self.assertEqual(result["forecast_period"], "2027.03")
        self.assertEqual(result["years"][0]["profit"], 1700)

    def test_revision_disagreement_blocks_unverified_valuation(self):
        result = self.build(_ir_status={"forecast_revision": {"fy_revised": {"ordinary_income": 2100}}})
        self.assertTrue(result["warnings"])
        self.assertIsNone(next(r for r in result["rows"] if r["key"] == "growth")["value"])

    def test_share_count_and_missing_market_price(self):
        result = self.build(price=None, treasury_shares=None)
        self.assertTrue(all(r["value"] is None for r in result["rows"]))
        self.assertTrue(all(r["delta"] is None for r in result["rows"]))

    def test_cash_and_dcf_are_not_replaced_by_company_profit(self):
        result = self.build()
        rows = {r["key"]: r for r in result["rows"]}
        self.assertAlmostEqual(rows["cash"]["value"], 256)
        self.assertAlmostEqual(rows["dcf"]["value"], 398)
        self.assertAlmostEqual(rows["cash"]["pct"], 24 / 232 * 100)

    def test_negative_forecast_is_not_hidden_or_replaced(self):
        result = self.build(forecasts=[{"period": "2027.03", "is_forecast": True, "ordinary_income": -1700}])
        growth = next(r for r in result["rows"] if r["key"] == "growth")
        self.assertLess(growth["value"], 142)
        self.assertIn("裏付けられません", growth["view"])

    def test_section_renders_at_end_with_formula_and_assumption(self):
        source = (Path(__file__).resolve().parents[1] / "report_app/templates/report_template.html").read_text()
        start = source.index("  {% if price_comparison is defined %}")
        end = source.index('  <div class="disclaimer">', start)
        html = Environment(autoescape=True).from_string(source[start:end]).render(price_comparison=self.build())
        self.assertIn("291.1円", html)
        self.assertIn("1,700.00", html)
        self.assertIn("アプリの成長仮定", html)
        self.assertIn("年数−1", html)
        self.assertGreater(start, source.index('<section class="card glossary">'))
        self.assertNotIn("nan", html)


if __name__ == "__main__":
    unittest.main()
