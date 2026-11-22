"""一次資料の数表による回帰検証。fixtureは銘柄固有のテスト資料であり製品コードではない。"""
import copy
import json
from datetime import date
from pathlib import Path
import unittest
import tempfile
from unittest.mock import patch

from report_app import statement_review as review, ir_disclosures as ir, valuation, metrics, business_signals, classifier, edinet

FIXTURES = Path(__file__).parent / "fixtures"
SOURCE = {"title": "2027年3月期 第2四半期決算短信", "date": "2026-11-13", "url": "https://example.test/current.pdf"}
ANNUAL = {"title": "2026年3月期 決算短信", "date": "2026-05-14", "url": "https://example.test/annual.pdf"}


class StatementReviewTests(unittest.TestCase):
    def setUp(self):
        self.pages = json.loads((FIXTURES / "4406_interim_tables.json").read_text())
        self.annual_pages = json.loads((FIXTURES / "4406_annual_tables.json").read_text())
        self.current = review.extract_statements(self.pages, SOURCE)
        self.annual = review.extract_statements(self.annual_pages, ANNUAL)
        self.balance = ir.extract_quarterly_balance_sheet(self.pages)
        self.quarter = valuation.compute_quarter_end_reference(self.balance, "2026-03-31", {}, 8460, 1331)

    def ttm(self, current=None, annual=None, quarter=None):
        return review.compute_ttm_reference(current or self.current, annual or self.annual,
            quarter or self.quarter, {"operating_income": 2300}, "2026-03-31")

    def test_exact_4406_ttm_and_preservation_of_existing_quarter_ratio(self):
        out = self.ttm()
        self.assertEqual([out[k] for k in ("operating_income", "depreciation", "ebitda", "ev")], [2007,725,2732,12137])
        self.assertAlmostEqual(out["ev_ebitda"], 4.4425329429)
        self.assertAlmostEqual(out["forecast_ev_ebitda"], 4.012231405)
        self.assertAlmostEqual(self.quarter["ev_ebitda"], 12137 / 1331)

    def test_pl_factors_scope_and_pages(self):
        rows = {r["label"]: r for r in self.current["factors"]}
        self.assertEqual(rows["関係会社株式評価損"]["current"],756)
        self.assertEqual(rows["スクラップ売却益"]["current"],109)
        self.assertEqual(rows["持分法による投資利益"]["current"],89)
        self.assertFalse(rows["持分法による投資利益"]["one_off"])
        self.assertEqual(rows["関係会社株式評価損"]["page"],8)
        self.assertIn("Edenor",self.current["scope_change"]["text"])
        self.assertNotIn("会計方針",self.current["scope_change"]["text"])

    def test_cf_values_and_conversion_ratio(self):
        cf=self.current["cf"]
        self.assertEqual([cf[k]["current"] for k in ("operating_cf","investing_cf","financing_cf","receivables_change","inventory_change","payables_change")],[43,-689,864,-1160,-555,-146])
        self.assertAlmostEqual(self.current["cf_profit_ratio"],43/1020*100)
        self.assertTrue(self.current["low_cf"])

    def test_inventory_and_receivables_include_electronic_claims(self):
        rows=review.working_capital_reference(self.balance)
        self.assertEqual([r["current"] for r in rows],[6145,9777])
        self.assertEqual([r["prior"] for r in rows],[5596,8617])
        self.assertAlmostEqual(rows[0]["growth"],9.810578985)
        self.assertAlmostEqual(rows[1]["growth"],13.461761634)

    def test_cf_continuity_is_explained_with_same_annual_period(self):
        rows=[{"period":"2025.03","cash_balance":2780}, {"period":"2026.03","cash_balance":5532,"operating_cf":1643,"investing_cf":501,"financing_cf":-64}]
        cf=self.annual["cf"]
        details=[{"label":cf[k]["label"],"value":cf[k]["current"]} for k in ("new_consolidation","exchange")]
        check=metrics.check_cashflow_continuity(rows,{"2026.03":details})[-1]
        self.assertEqual([check[k] for k in ("gap","explained","residual")],[672,672,0])
        self.assertIsNone(check["warning"])
        self.assertIsNotNone(metrics.check_cashflow_continuity(rows)[-1]["warning"])

    def test_unknown_unit_is_never_estimated(self):
        pages=[p.replace("百万円", "不明単位") for p in self.pages]
        result=review.extract_statements(pages,SOURCE)
        self.assertFalse(result["cf"])
        self.assertFalse(result["pl"])

    def test_no_cf_does_not_use_pl_depreciation_or_annual_as_interim(self):
        pages=[p if i in (1,5,6,7) else "" for i,p in enumerate(self.pages)]
        current=review.extract_statements(pages,SOURCE)
        self.assertFalse(current["cf"])
        self.assertIsNone(self.ttm(current=current)["ebitda"])

    def test_mismatched_pl_cf_period_and_changed_fiscal_year_are_rejected(self):
        changed=copy.deepcopy(self.current)
        changed["periods"]["cf"]["end"]="2026-06-30"
        self.assertIsNone(self.ttm(current=changed)["ebitda"])
        changed=copy.deepcopy(self.annual)
        changed["periods"]["cf"]["end"]="2025-12-31"
        self.assertIsNone(self.ttm(annual=changed)["ebitda"])

    def test_zero_negative_ebitda_does_not_produce_a_multiple(self):
        changed=copy.deepcopy(self.current)
        changed["pl"]["operating_income"]["current"]=-10000
        self.assertLess(self.ttm(current=changed)["ebitda"],0)
        self.assertIsNone(self.ttm(current=changed)["ev_ebitda"])

    def test_missing_da_is_not_filled_with_zero(self):
        changed=copy.deepcopy(self.current);changed["cf"].pop("depreciation")
        self.assertIsNone(self.ttm(current=changed)["depreciation"])
        self.assertIsNone(self.ttm(current=changed)["ebitda"])

    def test_actual_loss_reconciles_only_a_same_named_earlier_forecast(self):
        old={"date":"2026-08-07","title":"特別損失の計上","text":"2027年3月期第2四半期に関係会社株式評価損として約800百万円を計上する見込み","amount_text":"約800百万円"}
        out=review.reconcile_risk_amounts([old],self.current)[0]
        self.assertIn("756百万円",out["amount_text"])
        self.assertIn("約800百万円",out["amount_text"])
        self.assertEqual(old["amount_text"],"約800百万円")
        other={**old,"text":"工場閉鎖損失の見込み"}
        self.assertEqual(review.reconcile_risk_amounts([other],self.current)[0]["amount_text"],"約800百万円")

    def test_table_factors_connect_to_cyclical_detection(self):
        event={"date":"2026-11-13","title":"関係会社株式評価損","amount_text":"756百万円","page":8,"statement_factor":True,"one_off":True}
        factors=classifier.detect_one_off_factors([event],"2026-11-13")
        self.assertEqual(factors[0]["label"],"関係会社株式評価損 756百万円")
        self.assertEqual(factors[0]["page"],8)

    def test_retries_an_old_negative_edinet_cache(self):
        response=type("Response",(),{"json":lambda self:{"results":[{"secCode":"44060","docTypeCode":"120","docID":"TEST"}]},"raise_for_status":lambda self:None})()
        with patch.object(edinet,"_load_cache",return_value={"4406_2026-03-31":None}),patch.object(edinet,"_save_cache"),patch.object(edinet,"_throttled_get",return_value=response) as get:
            self.assertEqual(edinet.find_yuho_document("4406",date(2026,3,31),"test")["docID"],"TEST")
            self.assertTrue(get.called)

    def test_ir_latest_includes_later_presentation_even_if_tdnet_is_older(self):
        items=[SOURCE,ANNUAL]
        presentation={"title":"2027年3月期上期 決算説明会資料","date":"2026-11-20","url":"https://example.test/presentation.pdf"}
        def pages(url,referer=None):
            return self.pages if url==SOURCE["url"] else self.annual_pages if url==ANNUAL["url"] else ["会社説明資料"]
        with patch.object(ir,"fetch_recent_tdnet_disclosures",return_value={"url":"https://tdnet.test","items":[SOURCE]}),patch.object(ir,"discover_official_ir_index",return_value={"url":"https://example.test/ir","items":items,"all_items":items+[presentation]}),patch.object(ir,"_document_pages",side_effect=pages):
            result=ir.fetch_latest_ir_disclosures("4406",corporate_url="https://example.test",report_date=date(2026,11,21))
        self.assertEqual(result["latest_date"],"2026-11-20")
        self.assertTrue(result["presentation_acquired"])
        self.assertEqual(result["latest_presentation"]["url"],presentation["url"])

    def test_missing_annual_inputs_still_render_a_report_with_quarter_data(self):
        from report_app import report_generator as g, scraper
        from report_app.manual_input import TEMPLATE
        company=scraper.CompanyData(code="TEST",name="Test",price=227,market_cap=8460e6)
        company.annual_performance=[{"period":"2026.03","is_forecast":False,"revenue":32105,"operating_income":576,"ordinary_income":547,"net_income":597},
            {"period":"2027.03","is_forecast":True,"operating_income":2300}]
        company.financial_position=[{"period":"2026.03","total_assets":40345,"equity":19742,"equity_ratio":48.9,"bps":529.55}]
        annual_fields={k:None for k in ("revenue","operating_income","ordinary_income","net_income","eps","dps")}
        company.annual_performance=[{**annual_fields,**r} for r in company.annual_performance]
        company.financial_position[0]["retained_earnings"]=None
        status={"confirmed":True,"latest_date":"2026-11-13","latest_earnings":SOURCE,
            "latest_earnings_original":SOURCE,"quarter_balance":self.balance,
            "statement_review":self.current,"annual_review":self.annual}
        with tempfile.TemporaryDirectory() as directory,patch.object(g.scraper,"fetch_company_data",return_value=company),patch.object(g,"load_or_create_manual_data",return_value=dict(TEMPLATE)),patch.object(g,"_enrich_manual_data_with_edinet",side_effect=lambda code,m,*a:m),patch.object(g.ir_disclosures,"fetch_latest_ir_disclosures",return_value=status),patch.object(g,"OUTPUT_DIR",Path(directory)),patch.object(g.ai_analysis_package,"write_analysis_request"):
            html=g.generate_report("TEST").read_text()
        self.assertIn("12,137",html)
        self.assertIn("4.44",html)
        self.assertIn("取得不可",html)
        self.assertLess(html.index('id="valuation-comparison"'),html.index('class="card glossary"'))

    def test_pdf_transient_failure_is_retried_once(self):
        import requests
        failed=requests.Response();failed.status_code=503
        ok=requests.Response();ok.status_code=200;ok._content=b'%PDF-TEST'
        with patch.object(ir.requests,"get",side_effect=[failed,ok]) as get,patch.object(ir,"_pdf_pages",return_value=["text"]):
            self.assertEqual(ir._document_pages("https://example.test/doc.pdf"),["text"])
            self.assertEqual(get.call_count,2)

    def test_loss_subject_split_across_pdf_lines_is_reconciled(self):
        event=ir._risk_event({"date":"2026-08-07","title":"特別損失の計上","url":"https://example.test/old.pdf"},
            "特別損失 " + "説明"*1000 + " 2027年3月期第2四半期に関係会社株式\n評価損として連結決算で約800百万円を計上する見込み")
        self.assertTrue(any("関係会社株式評価損" in s for s in event["loss_subjects"]))
        self.assertIn("756百万円",review.reconcile_risk_amounts([event],self.current)[0]["amount_text"])

    def test_later_note_dates_cannot_replace_the_financial_statement_period(self):
        pages=self.annual_pages+["連結損益計算書の科目の組替\n2025年11月19日 2026年4月7日 2025年11月19日 2026年4月7日\n営業利益 1 2"]
        out=review.extract_statements(pages,ANNUAL)
        self.assertEqual(out["periods"]["pl"]["end"],"2026-03-31")
        self.assertEqual(out["pl"]["operating_income"]["current"],576)

    def test_wrapped_parent_profit_label_is_not_lost(self):
        pages=["連結損益計算書\n(単位：百万円)\n2025年4月1日 2025年9月30日 2026年4月1日 2026年9月30日\n営業利益 1 2\n親会社株主に帰属する中間純利益又は親会社株主に\n帰属する中間純損失（△） △3 4"]
        self.assertEqual(review.extract_statements(pages,SOURCE)["pl"]["net_income"]["current"],4)

    def corrected_release(self, text, title=None):
        correction={"date":"2026-11-20","title":title or "（訂正）2027年3月期 第2四半期決算短信", "url":"https://example.test/corrected.pdf"}
        def pages_for(item):
            return text if item["url"] == correction["url"] else self.pages
        warnings=[]
        result=ir._select_financial_release(correction,SOURCE,pages_for,warnings)
        return result,warnings

    def test_dividend_only_correction_preserves_full_original_financials(self):
        (source,current,balance),warnings=self.corrected_release(["2027年3月期 第2四半期決算短信の配当状況の注記を訂正します。"])
        self.assertEqual(source["url"],SOURCE["url"])
        self.assertEqual(current["pl"]["operating_income"]["current"],1772)
        self.assertEqual(current["cf"]["operating_cf"]["current"],43)
        self.assertEqual(balance["cash_and_deposits"],5927)
        self.assertTrue(warnings)

    def test_partial_pl_correction_keeps_cf_and_balance_but_withholds_pl(self):
        (_,current,balance),warnings=self.corrected_release(["2027年3月期第2四半期決算短信の中間連結損益計算書の営業利益を訂正します。"])
        self.assertFalse(current["pl"])
        self.assertFalse(current["factors"])
        self.assertEqual(current["cf"]["operating_cf"]["current"],43)
        self.assertEqual(balance["cash_and_deposits"],5927)
        self.assertIsNone(current["cf_profit_ratio"])

    def test_full_corrected_release_replaces_same_period_original(self):
        pages=[p.replace("1,772","1,773") for p in self.pages]
        (source,current,balance),warnings=self.corrected_release(pages)
        self.assertEqual(source["url"],"https://example.test/corrected.pdf")
        self.assertEqual(current["pl"]["operating_income"]["current"],1773)
        self.assertEqual(current["cf"]["operating_cf"]["current"],43)
        self.assertFalse(warnings)

    def test_unknown_or_different_correction_period_does_not_mix_values(self):
        for title,text in [("決算短信の訂正","配当注記の訂正"),
                           ("2026年3月期第1四半期決算短信の訂正","配当注記の訂正")]:
            with self.subTest(title=title):
                (_,current,balance),warnings=self.corrected_release([text],title)
                self.assertFalse(current["pl"])
                self.assertFalse(current["cf"])
                self.assertIsNone(balance)
                self.assertTrue(warnings)

    def test_recent_but_stale_company_ir_does_not_suppress_tdnet(self):
        old={"date":"2026-08-07","title":"2027年3月期 第1四半期決算短信","url":"https://example.test/old.pdf"}
        revision={"date":"2026-11-18","title":"2027年3月期業績予想の修正","url":"https://example.test/revision.pdf"}
        official={"url":"https://example.test/ir","items":[old]}
        tdnet={"url":"https://tdnet.test","items":[revision,SOURCE]}
        def pages(url,referer=None):
            return self.pages if url==SOURCE["url"] else ["2026年11月18日 各位"]
        with patch.object(ir,"discover_official_ir_index",return_value=official),patch.object(ir,"fetch_recent_tdnet_disclosures",return_value=tdnet) as fetch_tdnet,patch.object(ir,"_document_pages",side_effect=pages):
            result=ir.fetch_latest_ir_disclosures("TEST",corporate_url="https://example.test",report_date=date(2026,11,21))
        fetch_tdnet.assert_called_once()
        self.assertEqual(result["latest_earnings_original"]["url"],SOURCE["url"])
        self.assertEqual(result["latest_forecast_revision"]["date"],"2026-11-18")

    def test_recorded_loss_is_not_relabelled_as_a_forecast(self):
        event={"date":"2026-08-07","title":"特別損失の計上", "text":"2027年3月期第2四半期に関係会社株式評価損50百万円を計上しました。", "amount_text":"50百万円"}
        actual=review.reconcile_risk_amounts([event],self.current)[0]
        self.assertEqual(actual["amount_text"],"50百万円")
        self.assertNotIn("forecast_amount_text",actual)

    def test_estimated_loss_needs_the_same_explicit_period_and_amount(self):
        for period,amount in [("2026年3月期第2四半期","約800百万円"),
                              ("2027年3月期第1四半期","約800百万円"),
                              ("対象期間未確認","約800百万円"),
                              ("2027年3月期第2四半期","500百万円")]:
            with self.subTest(period=period,amount=amount):
                event={"date":"2026-08-07","title":"特別損失の計上", "text":period+"に関係会社株式評価損として"+amount+"を計上する見込み", "amount_text":"約800百万円"}
                self.assertEqual(review.reconcile_risk_amounts([event],self.current)[0]["amount_text"],"約800百万円")


class DemandReviewTests(unittest.TestCase):
    def rows(self,text):return business_signals.extract_business_signals(text,SOURCE)

    def test_world_economy_with_long_period_prefix_is_macro(self):
        self.assertEqual(self.rows("当中間連結会計期間における世界経済は、AI関連需要の増加が続きました。")[0]["category"],"マクロ環境")

    def test_price_and_revenue_not_confused_with_quantity(self):
        r=self.rows("自動車産業向け製品につきましては、適正な販売価格を維持したことにより、売上高は前年同期を上回りました。")[0]
        self.assertIn("価格転嫁",r["category"])
        self.assertEqual(r["quantity_direction"],"記載なし")
        self.assertEqual(r["revenue_direction"],"改善・増加")

    def test_joint_quantity_revenue_and_supply_stoppage_are_extracted(self):
        r=self.rows("繊維油剤原料をはじめとする工業向け天然高級アルコールにおきましては、関連会社の生産及び供給が停止したため、数量、売上高ともに前年同期を大きく下回りました。")[0]
        self.assertIn("天然高級アルコール",r["product"])
        self.assertEqual(r["quantity_direction"],r["revenue_direction"])
        self.assertIn("減少",r["revenue_direction"])
        self.assertTrue(r["temporary"])
        r=self.rows("電子材料向け製品につきましては、数量、売上高ともに前年同期を上回りました。")[0]
        self.assertEqual(r["quantity_direction"],"改善・増加")

    def test_quantity_decrease_and_revenue_increase_are_separate(self):
        r=self.rows("販売数量は前年同期を下回ったものの、価格是正に努めた結果、売上高は前年同期を上回りました。")[0]
        self.assertEqual(r["quantity_direction"],"減少・低下")
        self.assertEqual(r["revenue_direction"],"改善・増加")


if __name__ == "__main__":
    unittest.main()
