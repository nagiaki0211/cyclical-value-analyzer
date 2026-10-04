"""
計算式・データ定義・表示ロジックの回帰テスト。

追加依存を増やさないよう標準ライブラリの unittest のみで書いている。
実行方法:
    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from report_app import ai_analysis_package, advanced_metrics, business_signals, classifier, edinet, grading, ir_disclosures, metrics, scraper, summary, valuation
from report_app.report_generator import _build_price_basis, _build_section_numbers, _check_source_dates
from report_app.scraper import _parse_market_cap


def sample_manual(**overrides) -> dict:
    """新日本理化(4406) 2026.03期の実データに基づくテスト用データ(単位: 百万円)。"""
    base = {
        "cash_and_deposits": 5685.0,
        "securities": None,               # 流動資産の有価証券は無し
        "investment_securities_noncurrent": 10503.0,
        "receivables": 7453.0,
        "electronically_recorded_receivables": 1164.0,
        "receivables_prev_year": 7570.0,
        "inventory": 5596.0,
        "inventory_prev_year": 5448.0,
        "other_current_assets": 186.0,
        "current_assets": 20084.0,
        "tangible_fixed_assets": 9018.0,
        "intangible_fixed_assets": 111.0,
        "investments_other": 11131.0,
        "fixed_assets": 20260.0,
        "current_liabilities": 10292.0,
        "fixed_liabilities": 9065.0,
        "total_liabilities": 19357.0,
        "noncontrolling_interests": 0.0,
        "goodwill": 0.0,
        "interest_bearing_debt": 7269.0,
        "depreciation_amortization": 755.0,
        "capital_expenditure_tangible": 322.0,
        "capital_expenditure_intangible": 85.0,
        "increase_in_working_capital": None,
        "income_taxes": 32.0,
        "income_before_taxes": 679.0,
        "income_taxes_prev_year": 155.0,
        "income_before_taxes_prev_year": 723.0,
        "extraordinary_income": 825.0,
        "extraordinary_loss": 693.0,
        "shares_issued": 37286906.0,
        "treasury_shares": 4600.0,
    }
    base.update(overrides)
    return base


def sample_latest(**overrides) -> dict:
    base = {
        "revenue": 32105.0,
        "operating_income": 576.0,
        "ordinary_income": 547.0,
        "net_income": 597.0,
        "equity": 19742.0,
        "equity_ratio": 48.9,
        "total_assets": 40345.0,
        "operating_cf": 1643.0,
        "investing_cf": 501.0,
        "financing_cf": -64.0,
        "eps": 16.0,
        "dps": 4.5,
    }
    base.update(overrides)
    return base


class TestBalanceSheetIdentity(unittest.TestCase):
    """1. 資産合計 = 負債合計 + 純資産合計"""

    def test_assets_equal_liabilities_plus_equity(self):
        manual, latest = sample_manual(), sample_latest()
        total_assets = manual["current_assets"] + manual["fixed_assets"]
        liabilities_plus_equity = manual["total_liabilities"] + latest["equity"]
        # 開示上の端数(繰延資産・非支配株主持分等)を許容する範囲で一致を確認する。
        self.assertLess(
            abs(total_assets - liabilities_plus_equity) / total_assets, 0.05,
            f"資産合計 {total_assets} と 負債+純資産 {liabilities_plus_equity} が乖離しています",
        )

    def test_reported_total_assets_matches_sum(self):
        manual, latest = sample_manual(), sample_latest()
        computed = manual["current_assets"] + manual["fixed_assets"]
        self.assertLess(abs(computed - latest["total_assets"]) / latest["total_assets"], 0.02)


class TestQuickRatio(unittest.TestCase):
    """2. 当座比率が流動比率を不自然に上回らない"""

    def test_quick_ratio_not_above_current_ratio(self):
        health = metrics.compute_health_metrics(sample_latest(), sample_manual())
        self.assertLessEqual(health["quick_ratio"]["value"], health["current_ratio"]["value"])
        self.assertEqual(metrics.check_ratio_consistency(health), [])

    def test_quick_assets_exclude_noncurrent_securities(self):
        """投資有価証券(固定資産)が当座資産に混入しないこと。"""
        quick = metrics.compute_quick_assets(sample_manual())
        self.assertAlmostEqual(quick["value"], 5685.0 + 7453.0 + 1164.0)

    def test_4406_quick_ratio_includes_electronic_receivables(self):
        health = metrics.compute_health_metrics(sample_latest(), sample_manual())
        self.assertAlmostEqual(health["quick_ratio"]["value"], 139.0, places=1)
        self.assertAlmostEqual(
            sum(c["value"] for c in health["quick_ratio"]["components"]),
            5685.0 + 7453.0 + 1164.0,
        )

    def test_consistency_check_detects_inverted_ratios(self):
        """定義上ありえない大小関係を検知して警告を出すこと。"""
        broken = {"current_ratio": {"value": 100.0}, "quick_ratio": {"value": 150.0}}
        self.assertTrue(metrics.check_ratio_consistency(broken))

    def test_inventory_is_never_in_quick_assets(self):
        manual = sample_manual()
        quick = metrics.compute_quick_assets(manual)
        self.assertNotIn(manual["inventory"], [c["value"] for c in quick["components"]])


class TestRiskAmountExtraction(unittest.TestCase):
    """定性リスクの金額は、単位混じり表記と複数金額を正しく扱う。"""

    def test_mixed_japanese_units_and_keyword_proximity(self):
        text = (
            "事業譲渡の対価は14億2千3百万円であり、これに伴い"
            "特別損失38億8千8百万円を計上しました。"
        )
        self.assertEqual(
            edinet.extract_disclosed_amount(text, ("特別損失",)),
            "3,888百万円",
        )

    def test_consolidated_amount_is_preferred_in_ir_event(self):
        item = {
            "title": "特別損失の計上に関するお知らせ",
            "date": "2026-08-07",
            "url": "https://example.com/disclosure.pdf",
        }
        event = ir_disclosures._risk_event(
            item,
            "特別損失として個別651百万円、連結約800百万円を計上する見込みです。",
        )
        self.assertEqual(event["amount_text"], "約800百万円")


class TestDcf(unittest.TestCase):
    """3. 弱気DCF ≦ 標準DCF ≦ 強気DCF / 4. WACC > 永久成長率"""

    def setUp(self):
        self.dcf = valuation.compute_dcf(sample_latest(), sample_manual(), 37282306.0)

    def test_scenarios_are_monotonic(self):
        values = [s["equity_value"] for s in self.dcf["scenarios"]]
        self.assertEqual(values, sorted(values), "弱気 ≦ 標準 ≦ 強気 が成立していません")
        self.assertTrue(self.dcf["monotonic_ok"])

    def test_wacc_greater_than_terminal_growth(self):
        for scenario in self.dcf["scenarios"]:
            self.assertGreater(scenario["wacc"], scenario["terminal_growth"])

    def test_terminal_growth_above_wacc_returns_na(self):
        scenario = {"wacc": 0.05, "terminal_growth": 0.06, "growth": 0.02}
        result = valuation._run_dcf_scenario(scenario, 1000.0, 100.0, 50.0, None)
        self.assertIsNone(result.get("equity_value"))
        self.assertIn("永久成長率", result["error"])

    def test_simple_fcf_excludes_all_investing_cashflow(self):
        """投資有価証券売却収入等を含む投資CF全体を継続FCFに使わない。"""
        self.assertAlmostEqual(self.dcf["base_fcf"], 1643.0 - 322.0 - 85.0)
        large_sale_proceeds = valuation.compute_dcf(
            sample_latest(investing_cf=9999.0), sample_manual(), None
        )
        self.assertAlmostEqual(large_sale_proceeds["base_fcf"], 1236.0)
        self.assertEqual(large_sale_proceeds["fcf_kind"], "簡易FCF")
        self.assertTrue(any("厳密なFCFFではありません" in w for w in large_sale_proceeds["warnings"]))

    def test_missing_intangible_capex_is_zero_when_tangible_is_disclosed(self):
        """4228型: 設備投資の片方だけが開示されてもDCF全体を落とさない。"""
        manual = sample_manual(
            capital_expenditure_tangible=-4215.0,
            capital_expenditure_intangible=None,
        )
        result = valuation.compute_dcf(sample_latest(operating_cf=7000.0), manual, None)
        self.assertTrue(result["available"])
        self.assertAlmostEqual(result["base_fcf"], 2785.0)
        self.assertEqual(result["fcf_components"]["capex_intangible"], 0.0)

    def test_ifrs_total_capex_supports_dcf(self):
        """7203型: CapitalExpendituresIFRSの合算値を使用する。"""
        manual = sample_manual(
            capital_expenditure_tangible=None,
            capital_expenditure_intangible=None,
            capital_expenditure_total=6059779.0,
        )
        result = valuation.compute_dcf(sample_latest(operating_cf=9000000.0), manual, None)
        self.assertTrue(result["available"])
        self.assertAlmostEqual(result["base_fcf"], 2940221.0)
        self.assertEqual(result["fcf_components"]["capex_total_reported"], 6059779.0)

    def test_strict_fcff_formula(self):
        manual = sample_manual(increase_in_working_capital=100.0, effective_tax_rate=0.30)
        result = valuation.compute_dcf(sample_latest(), manual, None)
        expected = 576.0 * (1 - 0.30) + 755.0 - 322.0 - 85.0 - 100.0
        self.assertAlmostEqual(result["base_fcf"], expected)
        self.assertTrue(result["is_strict_fcff"])

    def test_wacc_is_labelled_scenario_assumption_when_inputs_missing(self):
        self.assertEqual(self.dcf["wacc_basis"]["kind"], "シナリオ仮定")

    def test_wacc_components_are_reproducible(self):
        manual = sample_manual(
            cost_of_equity=0.08, pre_tax_cost_of_debt=0.02,
            equity_market_value=8690.0, effective_tax_rate=0.30,
        )
        result = valuation.compute_dcf(sample_latest(), manual, None)
        basis = result["wacc_basis"]
        expected = basis["equity_weight"] * 0.08 + basis["debt_weight"] * 0.02 * 0.70
        self.assertEqual(basis["kind"], "計算値")
        self.assertAlmostEqual(basis["value"], expected)

    def test_terminal_value_is_discounted(self):
        for scenario in self.dcf["scenarios"]:
            self.assertLess(scenario["pv_terminal"], scenario["terminal_value"])

    def test_equity_value_bridge(self):
        """株主価値 = 企業価値 + 現金 − 有利子負債(ネットデットの符号方向)。"""
        for scenario in self.dcf["scenarios"]:
            expected = (
                scenario["enterprise_value"] + self.dcf["cash"] - self.dcf["debt"]
                - self.dcf["noncontrolling_interests"]
            )
            self.assertAlmostEqual(scenario["equity_value"], expected, places=6)

    def test_noncontrolling_interests_are_deducted_from_equity_value(self):
        without = valuation.compute_dcf(
            sample_latest(), sample_manual(noncontrolling_interests=0.0), None
        )
        with_nci = valuation.compute_dcf(
            sample_latest(), sample_manual(noncontrolling_interests=4884.0), None
        )
        self.assertAlmostEqual(
            without["scenarios"][0]["equity_value"]
            - with_nci["scenarios"][0]["equity_value"],
            4884.0,
        )

    def test_enterprise_value_is_sum_of_present_values(self):
        for scenario in self.dcf["scenarios"]:
            self.assertAlmostEqual(
                scenario["enterprise_value"],
                scenario["pv_forecast"] + scenario["pv_terminal"], places=6,
            )

    def test_negative_fcf_is_flagged_not_computed(self):
        result = valuation.compute_dcf(
            sample_latest(operating_cf=-500.0, investing_cf=-200.0), sample_manual(), None
        )
        self.assertFalse(result["available"])
        self.assertEqual(result["scenarios"], [])
        self.assertTrue(result["warnings"])

    def test_per_share_value_uses_share_count(self):
        scenario = self.dcf["scenarios"][0]
        expected = scenario["equity_value"] * 1e6 / 37282306.0
        self.assertAlmostEqual(scenario["per_share"], expected, places=6)

    def test_sensitivity_table_marks_invalid_combinations(self):
        sensitivity = self.dcf["sensitivity"]
        self.assertEqual(len(sensitivity["rows"]), len(valuation.SENSITIVITY_WACCS))
        for row in sensitivity["rows"]:
            for value, growth in zip(row["cells"], sensitivity["terminal_growths"]):
                if row["wacc"] <= growth:
                    self.assertIsNone(value)
                else:
                    self.assertIsNotNone(value)


class TestValuationRatios(unittest.TestCase):
    """5. EV/EBITDAの表示値が構成要素から再計算できる"""

    def setUp(self):
        latest, manual = sample_latest(), sample_manual()
        self.ratios = advanced_metrics.compute_valuation_ratios(
            market_cap=8690.0,
            revenue=latest["revenue"],
            operating_cf=latest["operating_cf"],
            interest_bearing_debt=manual["interest_bearing_debt"],
            cash_and_deposits=manual["cash_and_deposits"],
            operating_income=latest["operating_income"],
            depreciation_amortization=manual["depreciation_amortization"],
            noncontrolling_interests=manual["noncontrolling_interests"],
            period_label="2026.03",
            debt_breakdown=[
                {"label": "短期借入金", "value": 260.0},
                {"label": "1年内返済予定の長期借入金", "value": 2063.0},
                {"label": "長期借入金", "value": 4946.0},
            ],
        )

    def test_ev_is_reproducible_from_components(self):
        c = self.ratios["components"]
        self.assertAlmostEqual(
            self.ratios["ev"], c["market_cap"] + c["interest_bearing_debt"]
            + c["noncontrolling_interests"] - c["cash_and_deposits"]
        )

    def test_ebitda_is_reproducible_from_components(self):
        c = self.ratios["components"]
        self.assertAlmostEqual(
            self.ratios["ebitda"], c["operating_income"] + c["depreciation_amortization"]
        )

    def test_ev_ebitda_matches_manual_calculation(self):
        self.assertAlmostEqual(self.ratios["ev_ebitda"], self.ratios["ev"] / self.ratios["ebitda"])
        # 8,690 + 7,269 − 5,685 = 10,274 ／ 576 + 755 = 1,331 → 約7.72倍
        self.assertAlmostEqual(self.ratios["ev_ebitda"], 7.72, places=1)

    def test_noncontrolling_interests_are_added_to_ev(self):
        values = dict(self.ratios["components"])
        with_nci = advanced_metrics.compute_valuation_ratios(
            market_cap=values["market_cap"], revenue=values["revenue"],
            operating_cf=values["operating_cf"],
            interest_bearing_debt=values["interest_bearing_debt"],
            cash_and_deposits=values["cash_and_deposits"],
            operating_income=values["operating_income"],
            depreciation_amortization=values["depreciation_amortization"],
            noncontrolling_interests=4884.0,
        )
        self.assertAlmostEqual(with_nci["ev"] - self.ratios["ev"], 4884.0)

    def test_debt_breakdown_sums_to_total(self):
        c = self.ratios["components"]
        self.assertAlmostEqual(
            sum(row["value"] for row in c["debt_breakdown"]), c["interest_bearing_debt"]
        )

    def test_period_label_is_present(self):
        """6. 実績値と会社予想値のラベルが付いている(期間の混在防止)。"""
        self.assertEqual(self.ratios["period_label"], "2026.03")
        self.assertEqual(self.ratios["basis"], "実績")

    def test_psr_and_pcfr(self):
        c = self.ratios["components"]
        self.assertAlmostEqual(self.ratios["psr"], c["market_cap"] / c["revenue"])
        self.assertAlmostEqual(self.ratios["pcfr"], c["market_cap"] / c["operating_cf"])


class TestPerPbrDefinition(unittest.TestCase):
    """6. PER = 株価 ÷ EPS / 7. PBR = 株価 ÷ BPS"""

    def test_per_matches_price_divided_by_forecast_eps(self):
        price, forecast_eps, reported_per = 233.0, 21.5, 10.9
        self.assertAlmostEqual(price / forecast_eps, reported_per, delta=reported_per * 0.05)

    def test_pbr_matches_price_divided_by_bps(self):
        price, bps = 233.0, 529.55
        self.assertEqual(round(price / bps, 2), 0.44)

    def test_displayed_pbr_uses_same_price_and_bps(self):
        company = SimpleNamespace(
            price=233.0, pbr=0.42, per=None, annual_performance=[]
        )
        basis = _build_price_basis(company, {"2026.03": {"bps": 529.55}}, "2026.03")
        self.assertEqual(round(basis["pbr_value"], 2), 0.44)
        self.assertIn("2026.03", basis["pbr_basis"])

    def test_q1_bps_can_support_042_when_period_is_explicit(self):
        company = SimpleNamespace(
            price=233.0, pbr=0.42, per=None, annual_performance=[]
        )
        basis = _build_price_basis(company, {"2027.03 Q1": {"bps": 555.87}}, "2027.03 Q1")
        self.assertEqual(round(basis["pbr_value"], 2), 0.42)
        self.assertIn("2027.03 Q1", basis["pbr_basis"])


class TestQuarterlyAnalysis(unittest.TestCase):
    """8. TTM / 9. YoY / 10. QoQ の定義"""

    def setUp(self):
        self.quarters = [
            {"period": f"Q{i}", "revenue": float(100 + i * 10),
             "operating_income": float(10 + i), "net_income": float(5 + i)}
            for i in range(8)
        ]
        self.result = metrics.compute_quarterly_analysis(self.quarters)

    def test_ttm_is_sum_of_trailing_four_quarters(self):
        for i, row in enumerate(self.result):
            if i < 3:
                self.assertIsNone(row["ttm_revenue"], "4四半期未満ではTTMを算出しない")
            else:
                expected = sum(q["revenue"] for q in self.quarters[i - 3:i + 1])
                self.assertAlmostEqual(row["ttm_revenue"], expected)

    def test_yoy_compares_with_four_quarters_ago(self):
        for i, row in enumerate(self.result):
            if i < 4:
                self.assertIsNone(row["yoy_revenue"])
            else:
                prev = self.quarters[i - 4]["revenue"]
                expected = (self.quarters[i]["revenue"] - prev) / prev * 100
                self.assertAlmostEqual(row["yoy_revenue"], expected)

    def test_qoq_compares_with_previous_quarter(self):
        for i, row in enumerate(self.result):
            if i < 1:
                self.assertIsNone(row["qoq_revenue"])
            else:
                prev = self.quarters[i - 1]["revenue"]
                expected = (self.quarters[i]["revenue"] - prev) / prev * 100
                self.assertAlmostEqual(row["qoq_revenue"], expected)

    def test_march_year_end_labels_all_quarters(self):
        quarters = [
            {"period": period, "revenue": 100.0, "operating_income": 10.0, "net_income": 5.0}
            for period in ("25.04-06", "25.07-09", "25.10-12", "26.01-03")
        ]
        result = metrics.compute_quarterly_analysis(quarters, 3)
        self.assertEqual(
            [row["fiscal_quarter_label"] for row in result],
            ["2026.03期 1Q", "2026.03期 2Q", "2026.03期 3Q", "2026.03期 4Q"],
        )

    def test_non_march_year_end_is_supported(self):
        quarters = [
            {"period": period, "revenue": 100.0, "operating_income": 10.0, "net_income": 5.0}
            for period in ("25.01-03", "25.04-06", "25.07-09", "25.10-12")
        ]
        result = metrics.compute_quarterly_analysis(quarters, 12)
        self.assertEqual(
            [row["fiscal_quarter_label"] for row in result],
            ["2025.12期 1Q", "2025.12期 2Q", "2025.12期 3Q", "2025.12期 4Q"],
        )

    def test_announcement_date_is_preserved_by_scraper(self):
        soup = BeautifulSoup(
            """
            <h2>第１四半期累計決算【実績】</h2><h3>業績推移</h3>
            <table><tr><td>26.04-06</td><td>9392</td><td>1021</td><td>1085</td>
            <td>1027</td><td>28.45</td><td>10.9%</td><td>26/08/07</td></tr></table>
            """,
            "html.parser",
        )
        company = scraper.CompanyData(code="4406")
        scraper._parse_quarterly_performance(soup, company)
        self.assertEqual(company.quarterly_performance[0]["announced_on"], "2026-08-07")


class TestGrowthChangeHandling(unittest.TestCase):
    """赤字をまたぐ比較では、意味のない増減率を表示・判定しない。"""

    def test_loss_to_profit_is_labelled_turnaround(self):
        change = metrics.analyze_growth_change(50.0, -100.0)
        self.assertEqual(change["display"], "黒字転換")
        self.assertEqual(change["direction"], "改善")
        self.assertIsNone(change["value"])
        self.assertFalse(change["comparable"])

    def test_continuing_loss_shows_narrowing_or_widening(self):
        narrower = metrics.analyze_growth_change(-60.0, -100.0)
        wider = metrics.analyze_growth_change(-150.0, -100.0)
        self.assertIn("赤字幅縮小", narrower["display"])
        self.assertEqual(narrower["direction"], "改善")
        self.assertIn("赤字幅拡大", wider["display"])
        self.assertEqual(wider["direction"], "悪化")
        self.assertIsNone(narrower["value"])
        self.assertIsNone(wider["value"])

    def test_tiny_base_is_flagged_as_hard_to_compare(self):
        change = metrics.analyze_growth_change(100.0, 5.0)
        self.assertIn("比較困難", change["display"])
        self.assertEqual(change["status"], "比較困難")
        self.assertFalse(change["comparable"])

    def test_quarterly_yoy_uses_same_turnaround_rule(self):
        quarters = [
            {"period": f"Q{i}", "revenue": 100.0, "operating_income": value,
             "net_income": value}
            for i, value in enumerate((-10.0, 1.0, 2.0, 3.0, 20.0))
        ]
        latest = metrics.compute_quarterly_analysis(quarters)[-1]
        self.assertEqual(latest["yoy_operating_income_display"], "黒字転換")
        self.assertIsNone(latest["yoy_operating_income"])


class TestCashConversionCycle(unittest.TestCase):
    """売上債権・棚卸資産・仕入債務の平均残高から簡易CCCを算出する。"""

    def test_ccc_uses_average_balances_and_includes_electronic_receivables(self):
        manual = sample_manual(
            cost_of_sales=24000.0,
            electronically_recorded_receivables_prev_year=1000.0,
            trade_payables=6000.0,
            trade_payables_prev_year=5000.0,
        )
        result = metrics.compute_cash_conversion_cycle(sample_latest(), manual)
        self.assertTrue(result["available"])
        average_receivables = ((7453.0 + 1164.0) + (7570.0 + 1000.0)) / 2
        expected_receivable_days = average_receivables / 32105.0 * 365
        expected_inventory_days = ((5596.0 + 5448.0) / 2) / 24000.0 * 365
        expected_payable_days = ((6000.0 + 5000.0) / 2) / 24000.0 * 365
        self.assertAlmostEqual(result["receivable_days"], expected_receivable_days)
        self.assertAlmostEqual(result["inventory_days"], expected_inventory_days)
        self.assertAlmostEqual(result["payable_days"], expected_payable_days)
        self.assertAlmostEqual(
            result["ccc"],
            expected_receivable_days + expected_inventory_days - expected_payable_days,
        )

    def test_ccc_reports_missing_inputs_instead_of_guessing(self):
        result = metrics.compute_cash_conversion_cycle(sample_latest(), sample_manual())
        self.assertFalse(result["available"])
        self.assertIn("取得不能", result["reason"])


class TestUnitConversion(unittest.TestCase):
    """11. 百万円・億円・円の単位換算が正しい"""

    def test_market_cap_parsing_handles_decimals_and_trillions(self):
        self.assertAlmostEqual(_parse_market_cap("86.9億円"), 8.69e9)
        self.assertAlmostEqual(_parse_market_cap("44兆1,498億円"), 44.1498e12)
        self.assertIsNone(_parse_market_cap(""))

    def test_market_cap_converts_to_millions_consistently(self):
        """時価総額(円)を百万円に揃えないと、清算価値・DCFとの比較が破綻する。"""
        market_cap_yen = _parse_market_cap("86.9億円")
        self.assertAlmostEqual(market_cap_yen / 1e6, 8690.0)

    def test_per_share_value_converts_millions_to_yen(self):
        dcf = valuation.compute_dcf(sample_latest(), sample_manual(), 37282306.0)
        scenario = dcf["scenarios"][0]
        # 株主価値(百万円) → 円に換算してから株数で割る
        self.assertAlmostEqual(
            scenario["per_share"], scenario["equity_value"] * 1_000_000 / 37282306.0, places=6
        )


class TestForecastLabelling(unittest.TestCase):
    """12. 実績値と会社予想値のラベルが付いている"""

    def test_forecast_rows_are_excluded_from_actual_periods(self):
        merged = {
            "2025.03": {"is_forecast": False, "revenue": 100.0},
            "2026.03": {"is_forecast": False, "revenue": 110.0},
            "2027.03": {"is_forecast": True, "revenue": 120.0},
        }
        self.assertEqual(metrics.latest_actual_period(merged), "2026.03")
        self.assertNotIn("2027.03", metrics.ordered_actual_periods(merged))

    def test_cycle_signals_label_forecast_separately(self):
        merged = {
            "2025.03": {"is_forecast": False, "ordinary_income": 1195.0, "operating_income": 829.0},
            "2026.03": {"is_forecast": False, "ordinary_income": 547.0, "operating_income": 576.0},
        }
        annual = [{"period": "2027.03", "is_forecast": True, "operating_income": 1500.0}]
        signals = classifier.build_cycle_signals(merged, None, annual)
        bases = {s["basis"] for s in signals}
        self.assertIn("会社予想", bases)
        self.assertIn("実績", bases)

    def test_quarterly_cycle_signal_uses_fiscal_quarter_label(self):
        quarters = [
            {
                "period": period, "revenue": 100.0 + i * 10,
                "operating_income": 10.0 + i, "net_income": 5.0 + i,
                "announced_on": "2026-08-07" if i == 4 else None,
            }
            for i, period in enumerate(
                ("25.04-06", "25.07-09", "25.10-12", "26.01-03", "26.04-06")
            )
        ]
        analysis = metrics.compute_quarterly_analysis(quarters, 3)
        signals = classifier.build_cycle_signals({}, analysis, None)
        quarterly = next(s for s in signals if s["name"] == "直近四半期(前年同期比)")
        self.assertEqual(quarterly["period"], "2027.03期 1Q")
        self.assertEqual(quarterly["period_range"], "2026.04～2026.06")
        self.assertEqual(quarterly["announced_on"], "2026-08-07")

    def test_all_cycle_signal_periods_have_correct_ranges(self):
        merged = {
            "2025.03": {"is_forecast": False, "ordinary_income": 100.0, "operating_income": 100.0},
            "2026.03": {"is_forecast": False, "ordinary_income": 90.0, "operating_income": 90.0},
        }
        annual = [{"period": "2027.03", "is_forecast": True, "operating_income": 120.0}]
        quarters = [
            {
                "period": period, "revenue": 100.0 + i * 10,
                "operating_income": 10.0 + i, "net_income": 5.0 + i,
                "announced_on": "2026-08-07" if i == 4 else None,
            }
            for i, period in enumerate(
                ("25.04-06", "25.07-09", "25.10-12", "26.01-03", "26.04-06")
            )
        ]
        analysis = metrics.compute_quarterly_analysis(quarters, 3)
        signals = {s["name"]: s for s in classifier.build_cycle_signals(merged, analysis, annual)}
        self.assertEqual(signals["過去通期(経常利益)"]["period_range"], "2025.04～2026.03")
        self.assertEqual(signals["直近四半期(前年同期比)"]["period_range"], "2026.04～2026.06")
        self.assertEqual(signals["TTM(直近12か月累計売上高)"]["period_range"], "2025.07～2026.06")
        self.assertEqual(signals["会社予想(営業利益)"]["period_range"], "2026.04～2027.03")


class TestCycleJudgment(unittest.TestCase):
    """好況・回復判定と事業構成変化の注記を一元的に扱う。"""

    @staticmethod
    def signal(name, direction, **extra):
        basis = "会社予想" if name.startswith("会社予想") else "実績"
        return {"name": name, "direction": direction, "basis": basis, **extra}

    def test_boom_phase_requires_historical_peak(self):
        signals = [
            self.signal("過去通期(経常利益)", "改善", is_historical_peak=False),
            self.signal("直近四半期(前年同期比)", "改善"),
        ]
        result = classifier.summarize_cycle_signals(signals)
        self.assertNotIn("②好況期", result["label"])

        signals[0]["is_historical_peak"] = True
        result = classifier.summarize_cycle_signals(signals)
        self.assertIn("②好況期", result["label"])

    def test_prior_loss_and_improvement_is_recovery(self):
        signals = [
            self.signal(
                "過去通期(経常利益)", "改善",
                previous_loss=True, is_historical_peak=False,
            ),
            self.signal("直近四半期(前年同期比)", "改善"),
        ]
        result = classifier.summarize_cycle_signals(signals)
        self.assertIn("①回復期", result["label"])

    def test_legacy_classifier_also_handles_loss_to_profit_as_recovery(self):
        merged = {
            "2025.03": {"ordinary_income": -100.0, "net_income": -80.0, "dps": 0.0},
            "2026.03": {"ordinary_income": 50.0, "net_income": 40.0, "dps": 2.0},
        }
        result = classifier.classify_cyclical_value("機械", merged, pbr=0.8)
        self.assertIn("①回復期", result["phase"])
        self.assertIn("黒字転換", result["detail"])

    def test_sub_one_pbr_and_improvement_is_recovery(self):
        signals = [
            self.signal("過去通期(経常利益)", "改善", is_historical_peak=False),
            self.signal("直近四半期(前年同期比)", "改善"),
        ]
        result = classifier.summarize_cycle_signals(signals, pbr=0.8)
        self.assertIn("①回復期", result["label"])

    def test_sales_decline_from_divestiture_is_annotated(self):
        signals = [
            self.signal("過去通期(経常利益)", "悪化", is_historical_peak=False),
            self.signal(
                "直近四半期(前年同期比)", "悪化", revenue_direction="悪化",
            ),
        ]
        risks = [{"text": "一部事業から撤退", "keywords": ["事業譲渡"]}]
        result = classifier.summarize_cycle_signals(signals, risk_events=risks)
        self.assertIn("事業構成の変化", result["detail"])

    def test_temporary_demand_is_added_to_cycle_judgment(self):
        signals = [self.signal("直近四半期(前年同期比)", "改善")]
        business = [{"temporary": True, "evidence": "一時的な需要による販売の増加"}]
        result = classifier.summarize_cycle_signals(
            signals, pbr=0.8, business_signals=business
        )
        self.assertIn("一時要因を含む", result["label"])
        self.assertEqual(result["temporary_evidence"], ["一時的な需要による販売の増加"])


class TestGrading(unittest.TestCase):
    """13. 情報未入力の項目が最高評価にならない"""

    def test_shareholder_return_withholds_grade_when_data_missing(self):
        result = grading.grade_shareholder_return(
            eps=16.0, dps=4.5, manual={}, merged=None, ordered_periods=None,
        )
        self.assertIsNone(result["grade"], "判定材料が不足している場合にA評価を出してはいけない")
        self.assertIn("評価保留", result["grade_label"])

    def test_shareholder_return_uses_multiple_factors(self):
        merged = {
            "2023.03": {"dps": 0.0}, "2024.03": {"dps": 0.0},
            "2025.03": {"dps": 4.0}, "2026.03": {"dps": 4.5},
        }
        result = grading.grade_shareholder_return(
            eps=16.0, dps=4.5, manual=sample_manual(treasury_stock_purchase=0.0),
            merged=merged, ordered_periods=list(merged), dividend_yield=1.93,
            free_cash_flow=2144.0,
        )
        labels = [c["label"] for c in result["checks"]]
        self.assertIn("直近の開示期間に無配なし", labels)
        self.assertIn("自社株買いの実施", labels)
        # 無配の期があるため、配当性向が適正でも最高評価にはならない
        self.assertNotEqual(result["grade"], "A")

    def test_metric_group_grade_uses_graded_scores(self):
        health = metrics.compute_health_metrics(sample_latest(), sample_manual())
        graded = grading.grade_financial_health(health)
        self.assertIsNotNone(graded["grade"])
        self.assertGreater(graded["evaluated"], 0)
        self.assertLessEqual(graded["score_ratio"], 1.0)


class TestProfitQuality(unittest.TestCase):
    """14. 特別損益を含む場合に利益の質へ警告が出る"""

    def test_material_extraordinary_items_are_flagged(self):
        adjusted = advanced_metrics.compute_adjusted_profit(
            sample_latest(), sample_manual(), 0.3062
        )
        self.assertTrue(adjusted["available"])
        self.assertTrue(adjusted["is_material"], "特別損益が大きい場合は警告対象になるべき")

    def test_adjusted_profit_excludes_one_off_items(self):
        adjusted = advanced_metrics.compute_adjusted_profit(
            sample_latest(), sample_manual(), 0.3062
        )
        self.assertLess(adjusted["adjusted_net_income"], sample_latest()["net_income"])

    def test_fscore_warns_it_is_not_official_piotroski(self):
        merged = {
            "2025.03": {"is_forecast": False, "revenue": 32703.0, "operating_income": 829.0,
                        "net_income": 522.0, "total_assets": 37519.0, "operating_cf": -224.0},
            "2026.03": {"is_forecast": False, "revenue": 32105.0, "operating_income": 576.0,
                        "net_income": 597.0, "total_assets": 40345.0, "operating_cf": 1643.0},
        }
        adjusted = advanced_metrics.compute_adjusted_profit(sample_latest(), sample_manual(), 0.3062)
        fscore = advanced_metrics.compute_simplified_fscore(
            merged, ["2025.03", "2026.03"], manual=sample_manual(), adjusted=adjusted
        )
        self.assertTrue(any("正式なPiotroski" in w for w in fscore["warnings"]))
        self.assertTrue(any("一過性" in w for w in fscore["warnings"]))

    def test_fscore_includes_operating_profit_checks(self):
        merged = {
            "2025.03": {"is_forecast": False, "revenue": 32703.0, "operating_income": 829.0,
                        "net_income": 522.0, "total_assets": 37519.0, "operating_cf": -224.0},
            "2026.03": {"is_forecast": False, "revenue": 32105.0, "operating_income": 576.0,
                        "net_income": 597.0, "total_assets": 40345.0, "operating_cf": 1643.0},
        }
        fscore = advanced_metrics.compute_simplified_fscore(
            merged, ["2025.03", "2026.03"], manual=sample_manual()
        )
        labels = [c["label"] for c in fscore["checks"]]
        self.assertIn("営業利益が前年比プラス", labels)
        self.assertIn("営業利益率が前期より改善", labels)
        self.assertIn("売上債権の増加率が売上高成長率以下", labels)
        # 営業利益は減益なので、この項目は「満たさない」と判定されるべき
        operating_check = next(c for c in fscore["checks"] if c["label"] == "営業利益が前年比プラス")
        self.assertFalse(operating_check["passed"])


class TestTaxRateSelection(unittest.TestCase):
    """ROICの税率選択(単年度の異常値をそのまま使わない)"""

    def test_abnormal_single_year_rate_falls_back_to_statutory(self):
        tax = advanced_metrics.resolve_effective_tax_rate(sample_manual())
        self.assertAlmostEqual(tax["rate"], advanced_metrics.STATUTORY_EFFECTIVE_TAX_RATE)
        self.assertIn("法定実効税率", tax["basis"])
        self.assertIsNotNone(tax["warning"])

    def test_user_specified_rate_takes_precedence(self):
        tax = advanced_metrics.resolve_effective_tax_rate(sample_manual(effective_tax_rate=0.25))
        self.assertAlmostEqual(tax["rate"], 0.25)
        self.assertEqual(tax["basis"], "ユーザー指定税率")

    def test_normalized_multi_year_rate_is_used_when_in_range(self):
        manual = sample_manual(
            income_taxes=300.0, income_before_taxes=1000.0,
            income_taxes_prev_year=310.0, income_before_taxes_prev_year=1000.0,
        )
        tax = advanced_metrics.resolve_effective_tax_rate(manual)
        self.assertAlmostEqual(tax["rate"], 610.0 / 2000.0)
        self.assertIn("正常化", tax["basis"])


class TestSectionNumbering(unittest.TestCase):
    """15. 欠番のない見出し番号になる"""

    def test_numbers_are_sequential_when_sections_hidden(self):
        numbers = _build_section_numbers(
            {"quarterly": False, "segments": False, "shareholders": False,
             "risk_events": False, "breakeven": False}
        )
        values = sorted(numbers.values())
        self.assertEqual(values, list(range(1, len(values) + 1)), "見出し番号に欠番があります")

    def test_numbers_are_sequential_when_all_sections_shown(self):
        numbers = _build_section_numbers(
            {"quarterly": True, "segments": True, "shareholders": True,
             "risk_events": True, "breakeven": True}
        )
        values = sorted(numbers.values())
        self.assertEqual(values, list(range(1, len(values) + 1)))

    def test_hidden_sections_are_not_numbered(self):
        numbers = _build_section_numbers(
            {"quarterly": True, "segments": False, "shareholders": True,
             "risk_events": False, "breakeven": False}
        )
        self.assertNotIn("segments", numbers)
        self.assertIn("quarterly", numbers)


class TestLiquidationValue(unittest.TestCase):
    """簡易修正純資産(資産の二重計上を検知する)"""

    def test_no_double_counting_with_corrected_securities_field(self):
        result = valuation.compute_liquidation_value(
            sample_manual(securities=0.0)
        )
        self.assertFalse(result["double_count_warning"])

    def test_double_counting_is_detected(self):
        """投資有価証券を流動の有価証券としても計上すると警告が出ること。"""
        result = valuation.compute_liquidation_value(sample_manual(securities=10503.0))
        self.assertTrue(result["double_count_warning"])

    def test_rows_sum_to_adjusted_assets(self):
        result = valuation.compute_liquidation_value(sample_manual(securities=0.0))
        self.assertAlmostEqual(sum(r["value"] for r in result["rows"]), result["adjusted_assets"])

    def test_electronic_receivables_are_included_with_receivables_haircut(self):
        result = valuation.compute_liquidation_value(sample_manual(securities=0.0))
        row = next(r for r in result["rows"] if r["label"] == "電子記録債権")
        self.assertEqual(row["book_value"], 1164.0)
        self.assertEqual(row["rate"], 0.85)
        without = valuation.compute_liquidation_value(
            sample_manual(securities=0.0, electronically_recorded_receivables=0.0)
        )
        self.assertAlmostEqual(result["value"] - without["value"], 1164.0 * 0.85)
        self.assertAlmostEqual(
            result["value"], result["adjusted_assets"] - result["total_liabilities"]
            - result["noncontrolling_interests"]
        )

    def test_unreflected_costs_are_disclosed(self):
        result = valuation.compute_liquidation_value(sample_manual(securities=0.0))
        self.assertTrue(result["unreflected_costs"])

    def test_noncontrolling_interests_are_deducted(self):
        before = valuation.compute_liquidation_value(
            sample_manual(securities=0.0, noncontrolling_interests=0.0)
        )
        after = valuation.compute_liquidation_value(
            sample_manual(securities=0.0, noncontrolling_interests=4884.0)
        )
        self.assertAlmostEqual(before["value"] - after["value"], 4884.0)


class TestBreakevenValidation(unittest.TestCase):
    def test_weak_sales_profit_relationship_is_not_displayed(self):
        annual = [
            {"period": "2023.03", "revenue": 100.0, "operating_income": 10.0},
            {"period": "2024.03", "revenue": 120.0, "operating_income": 30.0},
            {"period": "2025.03", "revenue": 140.0, "operating_income": 5.0},
            {"period": "2026.03", "revenue": 160.0, "operating_income": 40.0},
        ]
        result = advanced_metrics.compute_breakeven_analysis(annual)
        self.assertFalse(result["available"])
        self.assertIn("回帰分析", result["reason"])


class TestNetCash(unittest.TestCase):
    """ネットキャッシュ(黒字/赤字と混同しない)"""

    def test_net_cash_is_cash_minus_debt(self):
        result = valuation.compute_net_cash(sample_latest(), sample_manual())
        self.assertAlmostEqual(result["narrow"], 5685.0 + 0.0 - 7269.0)

    def test_net_debt_is_labelled(self):
        result = valuation.compute_net_cash(sample_latest(), sample_manual())
        self.assertEqual(result["label"], "ネットデット")

    def test_profitable_company_with_net_debt_is_not_called_safe(self):
        """営業CFが黒字でも、ネットキャッシュがマイナスならネットデットと表示する。"""
        result = valuation.compute_net_cash(sample_latest(operating_cf=1643.0), sample_manual())
        self.assertLess(result["narrow"], 0)
        self.assertFalse(result["depletion_applicable"])

    def test_adjusted_net_cash_includes_investment_securities(self):
        result = valuation.compute_net_cash(sample_latest(), sample_manual())
        self.assertAlmostEqual(result["adjusted"], result["narrow"] + 10503.0)


class TestInterestBearingDebtTags(unittest.TestCase):
    """有利子負債の構成科目(1年内返済予定の長期借入金の欠落を防ぐ)"""

    def test_current_portion_of_long_term_loans_is_included(self):
        self.assertIn(
            "CurrentPortionOfLongTermLoansPayable",
            edinet.INTEREST_BEARING_DEBT_JGAAP_SUM_TAGS,
        )

    def test_securities_tag_points_to_current_assets(self):
        """流動資産の有価証券タグを使う(投資有価証券は別フィールド)。"""
        tags = [tag for _, tag in edinet.FIELD_TAG_CANDIDATES["securities"]]
        self.assertIn("ShortTermInvestmentSecurities", tags)
        self.assertNotIn("InvestmentSecurities", tags)

    def test_noncurrent_investment_securities_has_its_own_field(self):
        tags = [tag for _, tag in edinet.FIELD_TAG_CANDIDATES["investment_securities_noncurrent"]]
        self.assertIn("InvestmentSecurities", tags)

    def test_company_specific_factory_closure_loss_tag_is_resolved(self):
        facts = {
            "company-specific:LossOnFactoryClosureEL": {"CurrentYearDuration": 504.0}
        }
        detail = edinet.extract_balance_sheet_detail(facts, None)
        self.assertEqual(detail["factory_closure_loss"], 504.0)

    def test_ifrs_capital_expenditures_tag_is_resolved(self):
        facts = {"jpigp_cor:CapitalExpendituresIFRS": {"CurrentYearDuration": 6059779.0}}
        detail = edinet.extract_balance_sheet_detail(facts, None)
        self.assertEqual(detail["capital_expenditure_total"], 6059779.0)


class TestGenericCompanyDiscovery(unittest.TestCase):
    def test_corporate_url_is_parsed_without_ticker_mapping(self):
        soup = BeautifulSoup(
            '<h2>4228積水化成品工業</h2><a href="https://www.example.co.jp/">https://www.example.co.jp/</a>',
            "html.parser",
        )
        company = scraper.CompanyData(code="4228")
        scraper._parse_basic_info(soup, company)
        self.assertEqual(company.corporate_url, "https://www.example.co.jp/")

    def test_price_market_timestamp_and_close_are_preserved(self):
        soup = BeautifulSoup(
            '<div id="stockinfo_i1"><span class="kabuka">2,955円</span>'
            '<time datetime="2026-10-02T15:30+09:00">15:30</time></div>',
            "html.parser",
        )
        company = scraper.CompanyData(code="7942")
        scraper._parse_basic_info(soup, company)
        self.assertEqual(company.price_date, "2026-10-02")
        self.assertEqual(company.price_time, "15:30")
        self.assertEqual(company.price_type, "終値")


class TestDisclosureAndCycleConsistency(unittest.TestCase):
    def test_ir_index_excludes_future_sources_and_reports_latest_date(self):
        html = """
        <ul>
          <li>2026-08-07 <a href="/latest.pdf">天然高級アルコール事業からの撤退</a></li>
          <li>2026-10-01 <a href="/future.pdf">将来の開示</a></li>
        </ul>
        """
        rows = ir_disclosures.parse_ir_index(
            html, "https://example.com/ir", date(2026, 9, 22)
        )
        self.assertEqual([r["date"] for r in rows], ["2026-08-07"])
        self.assertEqual(rows[0]["url"], "https://example.com/latest.pdf")

    def test_fullwidth_document_date_is_supported(self):
        self.assertEqual(
            ir_disclosures._parse_date("２０２６年３月３０日"), date(2026, 3, 30)
        )

    def test_ir_index_does_not_assign_release_date_to_footer_navigation(self):
        html = """
        <div>
          <div class="release">2026/07/31 <a href="/result.pdf">第1四半期 決算短信</a></div>
          <div><div><div><a href="/contact.html">お問い合わせ</a></div></div></div>
        </div>
        """
        rows = ir_disclosures.parse_ir_index(
            html, "https://example.com/ir/release.html", date(2026, 10, 4)
        )
        self.assertEqual([row["title"] for row in rows], ["第1四半期 決算短信"])

    def test_forecast_revision_extracts_first_and_second_half(self):
        page = """
        第２四半期（中間期）連結業績予想数値の修正
        前回発表予想（A） 82,000 3,900 4,000 2,900 110.66
        今回修正予想（B） 86,000 7,000 7,100 5,500 209.87
        増減額
        （ご参考）前期中間期実績 80,000 3,076 3,100 2,500 95.00
        通期連結業績予想数値の修正
        前回発表予想（A） 164,000 7,000 7,200 5,000 190.79
        今回修正予想（B） 168,000 10,000 10,200 7,500 286.18
        増減額
        （ご参考）前期実績 155,149 7,765 7,952 6,242 238.00
        """
        result = ir_disclosures.extract_forecast_revision(
            [page], {"title": "業績予想の修正", "date": "2026-07-31", "url": "https://example.com/a.pdf"}
        )
        self.assertEqual(result["h1_initial"]["operating_income"], 3900.0)
        self.assertEqual(result["h1_revised"]["operating_income"], 7000.0)
        self.assertEqual(result["h2_initial"]["operating_income"], 3100.0)
        self.assertEqual(result["h2_revised"]["operating_income"], 3000.0)
        self.assertAlmostEqual(result["h2_operating_income_yoy"], -36.02047, places=4)
        self.assertTrue(result["warning"])

    def test_report_source_date_cannot_be_in_the_future(self):
        sources = [{"source_name": "テスト開示", "document_date": "2026-09-23"}]
        self.assertTrue(_check_source_dates(sources, date(2026, 9, 22)))
        self.assertEqual(_check_source_dates(sources, date(2026, 9, 23)), [])

    def test_detail_and_summary_use_same_cycle_judgment(self):
        judgment = {"label": "①回復初期の可能性", "detail": "直近は改善"}
        cyclical = {
            "matched": True, "phase": judgment["label"], "detail": judgment["detail"]
        }
        text = summary.build_summary(
            company_name="テスト社", asset_type={"matched": False},
            profit_type={"matched": False}, cyclical_type=cyclical,
            grades=[], a_grade_items=[], danger_flags=[],
        )
        self.assertIn(judgment["label"], text)
        self.assertNotIn("③後退期", text)


class TestBusinessSignalExtraction(unittest.TestCase):
    """受注・需要・価格転嫁は、会社が明示した範囲だけを抽出する。"""

    def test_make_to_stock_company_does_not_show_fake_order_amount(self):
        text = (
            "２）受注状況 当社グループは、需要予測に基づく見込生産を行っているため、"
            "該当事項はありません。３）販売実績"
        )
        result = business_signals.extract_order_disclosure(text)
        self.assertEqual(result["status"], "not_applicable_make_to_stock")
        self.assertIn("定量開示なし", result["label"])

    def test_4228_price_revision_extracts_product_amount_and_start_date(self):
        text = (
            "対象製品：発泡ポリスチレンシート。実施時期：2026年4月21日以降の出荷分より。"
            "発泡ポリスチレンシートの価格改定は120円／kgの値上げとします。"
        )
        source = {"title": "価格改定", "date": "2026-03-30", "url": "https://example.com/a.pdf"}
        rows = business_signals.extract_business_signals(text, source)
        price = next(row for row in rows if row["category"] == "価格転嫁")
        self.assertEqual(price["product"], "発泡ポリスチレンシート")
        self.assertEqual(price["revision_amount"], "120円/kg")
        self.assertEqual(price["confidence"], "A: 数値を会社資料で確認")

    def test_price_revision_date_is_not_guessed_from_another_sentence(self):
        text = (
            "対象製品：発泡ポリスチレンシート。"
            "2026年4月21日以降の出荷分より価格改定を実施し、120円／kg値上げします。"
        )
        rows = business_signals.extract_business_signals(
            text, {"title": "価格改定", "date": "2026-03-30", "url": "x"}
        )
        self.assertEqual(rows[0]["effective_date"], "2026-04-21")

    def test_4406_qualitative_pass_through_keeps_amount_undisclosed(self):
        text = (
            "自動車産業向け製品及び電子材料向け製品につきましては、需要が堅調に推移しました。"
            "適正な販売価格への転嫁を機動的に進めたことにより、売上高は前年同期を上回りました。"
        )
        rows = business_signals.extract_business_signals(
            text, {"title": "第1四半期決算短信", "date": "2026-08-07", "url": "x"}
        )
        price = next(row for row in rows if row["category"] == "価格転嫁")
        self.assertIsNone(price["revision_amount"])
        self.assertEqual(price["confidence"], "B: 会社の定性説明")

    def test_unpassed_percentage_is_not_treated_as_price_increase(self):
        text = (
            "年間ベースで見た場合、約15％程度は価格転嫁が追いつかない可能性があると"
            "見込んでおります。"
        )
        rows = business_signals.extract_business_signals(
            text, {"title": "決算説明会質疑応答", "date": "2026-05-16", "url": "x"}
        )
        price = next(row for row in rows if row["category"] == "価格転嫁")
        self.assertIsNone(price["revision_amount"])
        self.assertEqual(price["transfer_gap"], "15%")
        self.assertEqual(price["confidence"], "A: 数値を会社資料で確認")

    def test_advance_demand_is_flagged_as_temporary(self):
        rows = business_signals.extract_business_signals(
            "水産用途は先入れ需要により出荷数量が前年を上回りました。",
            {"title": "第1四半期決算短信", "date": "2026-07-31", "url": "x"},
        )
        self.assertTrue(rows[0]["temporary"])

    def test_numeric_disclosure_is_not_dropped_by_newer_qualitative_rows(self):
        qualitative = [
            {"category": "価格転嫁", "product": f"製品{i}",
             "source_date": "2026-08-01", "evidence": f"根拠{i}",
             "confidence": "B: 会社の定性説明"}
            for i in range(20)
        ]
        numeric = [{
            "category": "価格転嫁", "product": "数値製品",
            "source_date": "2026-03-30", "evidence": "120円/kg",
            "confidence": "A: 数値を会社資料で確認",
        }]
        merged = business_signals.merge_signals(qualitative, numeric)
        self.assertTrue(any(row["product"] == "数値製品" for row in merged))


class TestAiAnalysisPackage(unittest.TestCase):
    def test_request_keeps_source_date_url_and_no_guessing_rule(self):
        text = ai_analysis_package.build_analysis_request(
            code="4228",
            company_name="積水化成品工業",
            report_filename="4228_report.html",
            generated_at="2026-09-27 10:00",
            data_sources=[{
                "source_name": "企業公式IR",
                "document_date": "2026-07-31",
                "source_url": "https://example.com/ir.pdf",
            }],
            ir_status={"latest_date": "2026-07-31"},
            business_monitor={
                "order_disclosure": {
                    "label": "見込生産型のため受注高・受注残の定量開示なし",
                    "evidence": "主として見込生産",
                },
                "signals": [{
                    "category": "需要・販売数量",
                    "product": "水産用途",
                    "temporary": True,
                    "evidence": "先入れ需要により前年を上回る",
                    "source_title": "第1四半期決算短信",
                    "source_date": "2026-07-31",
                    "source_url": "https://example.com/q1.pdf",
                }],
            },
        )
        self.assertIn("4228 積水化成品工業", text)
        self.assertIn("https://example.com/q1.pdf", text)
        self.assertIn("2026-07-31", text)
        self.assertIn("一時要因あり", text)
        self.assertIn("推測で補完しない", text)

    def test_request_path_is_next_to_report(self):
        path = ai_analysis_package.analysis_request_path(
            Path("output/4406_新日本理化_report.html")
        )
        self.assertEqual(path.name, "4406_新日本理化_AI分析依頼書.md")

    def test_download_filenames_include_code_and_romanized_company_name(self):
        report_name, request_name = ai_analysis_package.download_filenames(
            Path("output/4406_新日本理化_report.html")
        )
        self.assertEqual(report_name, "4406_shinnihonrika_report.html")
        self.assertEqual(request_name, "4406_shinnihonrika_AI_analysis_request.md")

    def test_download_filenames_normalize_full_width_latin_name(self):
        report_name, request_name = ai_analysis_package.download_filenames(
            Path("output/7942_ＪＳＰ_report.html")
        )
        self.assertEqual(report_name, "7942_jsp_report.html")
        self.assertEqual(request_name, "7942_jsp_AI_analysis_request.md")

    def test_project_instructions_forbid_unverified_estimates(self):
        text = ai_analysis_package.project_instructions_text()
        self.assertIn("確認できない項目", text)
        self.assertIn("補完や推測をしない", text)
        self.assertIn("資料名", text)


class TestTaachanDcf(unittest.TestCase):
    """書籍(たーちゃん式)の企業価値評価"""

    def setUp(self):
        self.result = valuation.compute_dcf_taachan(sample_latest(), sample_manual(), 6525.0)

    def test_book_constants_are_unchanged(self):
        """書籍記載の前提値(R=10%・成長20%・係数0.6・5年)を勝手に変えない。"""
        self.assertAlmostEqual(valuation.TAACHAN_DISCOUNT_RATE, 0.10)
        self.assertAlmostEqual(valuation.TAACHAN_PROFIT_GROWTH, 0.20)
        self.assertAlmostEqual(valuation.TAACHAN_PROFIT_FACTOR, 0.60)
        self.assertEqual(valuation.TAACHAN_YEARS, 5)

    def test_cash_power_model_matches_the_book_formula(self):
        expected_factor = 1 / 0.10
        self.assertAlmostEqual(self.result["cash_power_factor"], expected_factor)
        self.assertAlmostEqual(
            self.result["cash_power_value"],
            self.result["net_cash"] + self.result["fcf"] * expected_factor,
        )

    def test_liquidation_growth_model_matches_the_book_formula(self):
        expected_factor = sum(0.6 * (1.2 / 1.1) ** n for n in range(1, 6))
        self.assertAlmostEqual(self.result["liquidation_growth_factor"], expected_factor)
        self.assertAlmostEqual(
            self.result["liquidation_growth_value"],
            6525.0 + sample_latest()["ordinary_income"] * expected_factor,
        )

    def test_models_have_names_that_describe_what_they_measure(self):
        self.assertEqual(self.result["cash_power_label"], "現金力モデル")
        self.assertEqual(self.result["liquidation_growth_label"], "清算価値＋成長モデル")
        self.assertNotIn("bear_case", self.result)
        self.assertNotIn("bull_case", self.result)

    def test_fcff_uses_resolved_tax_rate_instead_of_zero(self):
        manual = sample_manual(increase_in_working_capital=100.0, effective_tax_rate=0.30)
        result = valuation.compute_dcf_taachan(sample_latest(), manual, 6525.0)
        expected_fcf = 576.0 * (1 - 0.30) + 755.0 - (322.0 + 85.0) - 100.0
        self.assertAlmostEqual(result["fcf"], expected_fcf)
        self.assertAlmostEqual(result["tax_rate"], 0.30)

    def test_missing_data_is_reported_not_guessed(self):
        result = valuation.compute_dcf_taachan(sample_latest(), sample_manual(), None)
        self.assertIsNone(result["liquidation_growth_value"])
        self.assertIn("清算価値", result["liquidation_growth_reason"])


class TestEarningsValueGrading(unittest.TestCase):
    """項目2では、測定対象の異なる2モデルを独立に評価する。"""

    def test_each_model_is_scored_independently(self):
        result = grading.grade_earnings_value(None, 5000.0, {
            "cash_power_value": 4000.0,
            "liquidation_growth_value": 8000.0,
        })
        self.assertEqual(result["score"], 1)
        self.assertEqual(result["max_score"], 2)

    def test_below_both_scores_full_marks(self):
        result = grading.grade_earnings_value(None, 1000.0, {
            "cash_power_value": 8000.0,
            "liquidation_growth_value": 4000.0,
        })
        self.assertEqual(result["score"], 2)

    def test_above_both_scores_zero(self):
        result = grading.grade_earnings_value(None, 9000.0, {
            "cash_power_value": 8000.0,
            "liquidation_growth_value": 4000.0,
        })
        self.assertEqual(result["score"], 0)

    def test_available_model_is_used_when_the_other_is_missing(self):
        result = grading.grade_earnings_value(None, 5000.0, {
            "cash_power_value": 8000.0,
            "liquidation_growth_value": None,
        })
        self.assertEqual(result["score"], 1)
        self.assertEqual(result["max_score"], 1)


if __name__ == "__main__":
    unittest.main()
