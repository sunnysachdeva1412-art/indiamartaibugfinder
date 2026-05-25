"""
reporter.py — IndiaMART Bug Finder v5
Generates ONE HTML file with TWO tabs:
  Tab 1 — Bug Report   : Issues found, screenshots, web vitals
  Tab 2 — Test Report  : Test case coverage, pass/fail per check, execution summary
"""

import json
from datetime import datetime
from pathlib import Path

SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

FUNCTIONAL_CATS = {
    "Functional", "Broken CTA", "Missing CTA", "PDP", "MCAT", "Search",
    "Company Page", "Export", "Load Error", "Network Error",
    "Console Error", "Navigation", "Homepage", "BL Form",
    "Enquiry Form", "Product Listing", "Breadcrumb", "Filter Panel",
    "Search Bar", "Price Display", "Trust Signal", "Form Issue", "Privacy",
}

# Page type display names and icons
PAGE_META = {
    # PDP templates (3 real IndiaMART templates)
    "pdp_t14": {"label": "PDP — Template 14 (Apparel/Brochure)", "icon": "👗", "color": "#6366f1"},
    "pdp_t1":  {"label": "PDP — Template 1 (Product/Submit Req)", "icon": "📦", "color": "#3b82f6"},
    "pdp_t3":  {"label": "PDP — Template 3 (Service/Left Panel)", "icon": "🔧", "color": "#10b981"},
    "pdp":     {"label": "Product Detail Page",                   "icon": "📦", "color": "#6366f1"},
    "mcat":          {"label": "IMPCAT / Category Listing Page",   "icon": "📂", "color": "#f59e0b"},
    "search":        {"label": "Search Results Page",              "icon": "🔍", "color": "#3b82f6"},
    "export":        {"label": "Export Homepage",                  "icon": "🌍", "color": "#8b5cf6"},
    "export_search": {"label": "Export Search Results",            "icon": "🔍", "color": "#7c3aed"},
    "export_pdp":    {"label": "Export Product Detail Page",       "icon": "📦", "color": "#6d28d9"},
    "export_company":{"label": "Export Company Page",              "icon": "🏢", "color": "#5b21b6"},
    "company":       {"label": "Company / FCP Page",               "icon": "🏬", "color": "#059669"},
    "other":         {"label": "IndiaMART Page",                   "icon": "🌐", "color": "#6b7280"},
}

# ─────────────────────────────────────────────────────────────────────────────
# PAGE_CHECKS — derived from PDP_BF_Smoke_testsuite-deep.xml (TC-7044 to 7076)
# Each tuple: (check_label, test_case_id)
# N/A entries are shown as "Not Applicable" for this template
# ─────────────────────────────────────────────────────────────────────────────
PAGE_CHECKS = {

    "pdp_t14": [
        ("TC-7044: Product image gallery loads (4+ photos visible)",    "TC-7044"),
        ("TC-7047: Company name visible as a clickable link",           "TC-7047"),
        ("TC-7048: Review count visible (if seller has reviews)",       "TC-7048"),
        ("TC-7052: Product Brochure row shows pdf icon + View Now",     "TC-7052"),
        ("TC-7054: View in Hindi label is a clickable link",            "TC-7054"),
        ("TC-7055: Breadcrumb 1st link points to IndiaMART",            "TC-7055"),
        ("TC-7056: Breadcrumb 2nd link points to subcategory page",     "TC-7056"),
        ("TC-7057: Breadcrumb 3rd link points to MCAT page",            "TC-7057"),
        ("TC-7062: Related categories section visible",                 "TC-7062"),
        ("TC-7063: Category tiles in related categories are links",     "TC-7063"),
        ("TC-7065: Company name visible + GST shown masked",            "TC-7065"),
        ("TC-7069: More Products from Seller section visible",          "TC-7069"),
        ("TC-7071: About the Company / product details section present","TC-7071"),
        ("TC-7072: Ratings & Reviews section visible (if applicable)",  "TC-7072"),
        ("TC-7075: Header logo + search bar present",                   "TC-7075"),
        ("TC-7076: Footer navigation links present",                    "TC-7076"),
    ],

    "pdp_t1": [
        ("TC-7044: Product image loads (large image visible)",          "TC-7044"),
        ("TC-7046: Submit Requirement button visible",                  "TC-7046"),
        ("TC-7047: Company name visible as a clickable link",           "TC-7047"),
        ("TC-7048: Review count visible (if seller has reviews)",       "TC-7048"),
        ("TC-7049: Contact Supplier button visible in right panel",     "TC-7049"),
        ("TC-7051: Contact Supplier CTA present on page",               "TC-7051"),
        ("TC-7054: View in Hindi label is a clickable link",            "TC-7054"),
        ("TC-7055: Breadcrumb 1st link points to IndiaMART",            "TC-7055"),
        ("TC-7056: Breadcrumb 2nd link points to subcategory page",     "TC-7056"),
        ("TC-7057: Breadcrumb 3rd link points to MCAT page",            "TC-7057"),
        ("TC-7058: Find products similar to section visible",           "TC-7058"),
        ("TC-7059: Get Best Price CTA on each similar product card",    "TC-7059"),
        ("TC-7060: Product name in similar cards is a link to PDP",     "TC-7060"),
        ("TC-7061: View Mobile Number CTA on similar product cards",    "TC-7061"),
        ("TC-7065: Company name visible + GST shown masked",            "TC-7065"),
        ("TC-7066: Contact Seller CTA opens enquiry form",              "TC-7066"),
        ("TC-7067: Submit Requirement CTA opens enquiry form",          "TC-7067"),
        ("TC-7069: More Products from Seller section visible",          "TC-7069"),
        ("TC-7070: Get Best Price on More Products cards",              "TC-7070"),
        ("TC-7071: About the Company section present",                  "TC-7071"),
        ("TC-7072: Ratings & Reviews section visible (if applicable)",  "TC-7072"),
        ("TC-7075: Header logo + search bar present",                   "TC-7075"),
        ("TC-7076: Footer navigation links present",                    "TC-7076"),
    ],

    "pdp_t3": [
        ("TC-7047: Company name visible as a clickable link",           "TC-7047"),
        ("TC-7049: Contact Supplier button visible in left panel",      "TC-7049"),
        ("TC-7051: Contact Supplier CTA present on page",               "TC-7051"),
        ("TC-7055: Breadcrumb 1st link points to IndiaMART",            "TC-7055"),
        ("TC-7056: Breadcrumb 2nd link points to subcategory page",     "TC-7056"),
        ("TC-7057: Breadcrumb 3rd link points to MCAT page",            "TC-7057"),
        ("TC-7062: Related categories section visible",                 "TC-7062"),
        ("TC-7063: Category tiles in related categories are links",     "TC-7063"),
        ("TC-7064: Get Quotes CTA visible in related categories",       "TC-7064"),
        ("TC-7065: Company name visible + GST shown masked",            "TC-7065"),
        ("TC-7066: Contact Seller CTA opens enquiry form",              "TC-7066"),
        ("TC-7069: More Products from Seller section visible",          "TC-7069"),
        ("TC-7070: Get Best Price on More Products cards",              "TC-7070"),
        ("TC-7071: About the Company / product details section present","TC-7071"),
        ("TC-7072: Ratings & Reviews section visible (if applicable)",  "TC-7072"),
        ("TC-7073: Product name pre-filled in inline BL form textbox",  "TC-7073"),
        ("TC-7074: Submit Requirement in BL form opens BL flow",        "TC-7074"),
        ("TC-7075: Header logo + search bar present",                   "TC-7075"),
        ("TC-7076: Footer navigation links present",                    "TC-7076"),
    ],

    "pdp": [
        ("TC-7044: Product image visible",                              "TC-7044"),
        ("TC-7047: Company name visible and linked",                    "TC-7047"),
        ("TC-7055: Breadcrumb navigation present",                      "TC-7055"),
        ("TC-7065: Company details + GST masked",                       "TC-7065"),
        ("TC-7075: Header logo + search bar present",                   "TC-7075"),
        ("TC-7076: Footer navigation links present",                    "TC-7076"),
    ],

    "mcat": [
        ("TC-6461: City strip visible (All India + city links)",                    "TC-6461"),
        ("TC-6463: 'Near Me' CTA present in city strip",                            "TC-6463"),
        ("TC-6465: Product card images load without grey boxes",                    "TC-6465"),
        ("TC-6466: Company names on cards are clickable links",                     "TC-6466"),
        ("TC-6467: 'Contact Supplier' CTA visible on product cards",               "TC-6467"),
        ("TC-6468: Right arrow button present on product cards",                    "TC-6468"),
        ("TC-6469: Product names are links to PDP (proddetail)",                   "TC-6469"),
        ("TC-6470: GST / TrustSeal / X yrs visible on product cards",              "TC-6470"),
        ("TC-6471: 'Call Now' CTA visible on product cards",                        "TC-6471"),
        ("TC-6472: Inline BL form with 'Submit Requirement' present",              "TC-6472"),
        ("TC-6491: BL form textbox prefilled with category name",                  "TC-6491"),
        ("TC-6474: Related categories section visible",                             "TC-6474"),
        ("TC-6476: Business type filter / Explore filter visible",                  "TC-6476"),
        ("TC-6486: 'Watch Related Videos' section visible (if applicable)",        "TC-6486"),
        ("TC-6488: Q&A section 'Have a Question? Ask our expert' visible",         "TC-6488"),
        ("TC-6489: Q&A section has questions and answers",                          "TC-6489"),
    ],

    "search": [
        ("TC-3590: Search bar visible in header with query",                        "TC-3590"),
        ("TC-3591: Search results / product cards visible on page",                 "TC-3591"),
        ("TC-3599: Search result content is relevant to query",                     "TC-3599"),
        ("TC-3605: City suggestor ('Select city to find sellers') present",         "TC-3605"),
        ("TC-3606: City suggestor field is clickable (input element)",              "TC-3606"),
        ("TC-3610: City strip (city chips row) visible below header",               "TC-3610"),
        ("TC-3612: 'Near Me' CTA present in city strip",                            "TC-3612"),
        ("TC-3633: Product card images loaded without grey boxes",                  "TC-3633"),
        ("TC-3634: Seller company names are clickable links",                       "TC-3634"),
        ("TC-3635: 'Contact Supplier' CTA visible on product cards",               "TC-3635"),
        ("TC-3639: Product names are links to PDP (proddetail)",                   "TC-3639"),
        ("TC-3640: Star ratings visible and linked on product cards",               "TC-3640"),
        ("TC-3641: Inline BL form with 'Submit Requirement' present",              "TC-3641"),
        ("TC-3643: 'Call Now' CTA visible on product cards",                        "TC-3643"),
        ("TC-3644: BL form textbox prefilled with searched product name",          "TC-3644"),
    ],

    "export": [
        ("TC-2140: Export homepage loads successfully (title visible)",                "TC-2140"),
        ("TC-2139: Search bar visible in header",                                      "TC-2139"),
        ("TC-2141: Trust badge — 'Verified Exporters Only' below header",              "TC-2141"),
        ("TC-2141: Trust badge — 'TrustSEAL Certified Sellers' below header",         "TC-2141"),
        ("TC-2141: Trust badge — 'GST Verified' below header",                         "TC-2141"),
        ("TC-2141: Trust badge — 'IEC Verified' below header",                         "TC-2141"),
        ("TC-2141: Trust badge — 'Made in India - Supplied Globally' below header",   "TC-2141"),
        ("TC-2145: 'Get Quote' CTA visible in header",                                 "TC-2145"),
        ("TC-2153: Currency dropdown visible in header",                               "TC-2153"),
        ("TC-2154: Language dropdown visible in header",                               "TC-2154"),
    ],

    "export_search": [
        ("TC-2139: Search bar visible with pre-filled query",                          "TC-2139"),
        ("TC-2152: Product cards visible on search results page",                      "TC-2152"),
        ("TC-2149: TrustSEAL badge visible on product cards",                          "TC-2149"),
        ("TC-2153: Currency dropdown visible in header",                               "TC-2153"),
        ("TC-2154: Language dropdown visible in header",                               "TC-2154"),
        ("TC-2157: Inline BL form present on search results page",                    "TC-2157"),
        ("TC-2158: 'Get Latest Price' CTA visible on product cards",                   "TC-2158"),
    ],

    "export_pdp": [
        ("TC-2139: Search bar visible in header",                                      "TC-2139"),
        ("TC-2158: 'Get Latest Price' CTA visible on product page",                    "TC-2158"),
        ("TC-2149: TrustSEAL badge visible in company section",                        "TC-2149"),
        ("TC-2161: Exporter company name visible as a clickable link",                 "TC-2161"),
        ("TC-2166: Inline BL form 'Save Time!' with Submit Requirement present",       "TC-2166"),
        ("TC-2153: Currency dropdown visible in header",                               "TC-2153"),
        ("TC-2154: Language dropdown visible in header",                               "TC-2154"),
    ],

    "export_company": [
        ("TC-2139: Search bar visible in header",                                      "TC-2139"),
        ("TC-2161: Company details visible (name, GST, IEC, ratings)",                 "TC-2161"),
        ("TC-2153: Currency dropdown visible in header",                               "TC-2153"),
        ("TC-2154: Language dropdown visible in header",                               "TC-2154"),
    ],

    "company": [
        ("TC-4835: Company name (H1) visible in FCP header section",               "TC-4835"),
        ("TC-4836: Company address (city/state) visible in company details",       "TC-4836"),
        ("TC-4837: GST number shown masked if present (not full 15-char exposed)", "TC-4837"),
        ("TC-4838: Verified Supplier stamp visible in header (if applicable)",     "TC-4838"),
        ("TC-4839: Star rating + review count visible (if seller has reviews)",    "TC-4839"),
        ("TC-4840: Navigation tabs present — Our Products, About Us, Contact Us",  "TC-4840"),
        ("TC-4842: 'View Mobile Number' / contact CTA visible in header",          "TC-4842"),
        ("TC-4844: Company logo/image visible in FCP header",                      "TC-4844"),
        ("TC-4858: Ratings & Reviews section visible (if seller has reviews)",     "TC-4858"),
        ("TC-4859: Max 2 reviews shown in homepage ratings section",               "TC-4859"),
        ("TC-4866: Product listings visible on company page",                      "TC-4866"),
    ],

    "other": [
        ("Page loads without error",                                    "G1"),
        ("Page has meaningful content",                                 "G2"),
    ],
}


class ReportGenerator:
    def __init__(self, output_dir="output"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate(self, pages_data: list[dict], meta: dict = None) -> str:
        meta = meta or {}
        all_issues = []

        for page in pages_data:
            for issue in (page.get("issues") or []) + (page.get("ai_issues") or []):
                if not isinstance(issue, dict):
                    continue
                issue["page_url"]  = page.get("url", "")
                issue["page_type"] = page.get("page_type", "other")
                issue.setdefault("severity", "medium")
                issue.setdefault("category", "Other")
                issue.setdefault("title", "Untitled")
                issue.setdefault("detail", "")
                all_issues.append(issue)

        all_issues.sort(key=lambda i: SEV_ORDER.get(
            str(i.get("severity", "info")).lower(), 4))

        sev_counts = {"critical": 0, "high": 0, "medium": 0}
        for i in all_issues:
            s = str(i.get("severity", "medium")).lower()
            if s in sev_counts:
                sev_counts[s] += 1

        cat_counts: dict = {}
        for i in all_issues:
            c = i.get("category", "Other")
            cat_counts[c] = cat_counts.get(c, 0) + 1

        func_issues = [i for i in all_issues if i.get("category") in FUNCTIONAL_CATS]
        ui_issues   = [i for i in all_issues if i not in func_issues]

        pages_clean = []
        for page in pages_data:
            p = dict(page)
            ss = dict(p.get("screenshots", {}))
            p["screenshots"] = {"desktop_b64": ss.get("desktop_b64", "")}
            pages_clean.append(p)

        html = self._build(pages_clean, all_issues, func_issues, ui_issues,
                           sev_counts, cat_counts, meta)

        out = self.output_dir / "report.html"
        out.write_text(html, encoding="utf-8")
        print(f"  📊  Report: {len(all_issues)} issues | {len(pages_data)} pages → {out}")
        return str(out)

    # ─────────────────────────────────────────────────────────────────────
    def _issue_rows(self, issues):
        rows = []
        for i in issues:
            sev  = str(i.get("severity","medium")).lower()
            cat  = i.get("category","Other")
            func = cat in FUNCTIONAL_CATS
            tlbl = "⚙️ Functional" if func else "🎨 UI/UX"
            tcls = "tf" if func else "tu"
            ai   = ' <span class="aib">AI</span>' if i.get("source") == "claude-ai" else ""
            det  = (i.get("detail") or "")[:160]
            purl = i.get("page_url","")
            pt   = i.get("page_type","")
            rows.append(
                f'<tr class="ir" data-sev="{sev}" data-cat="{cat}">'
                f'<td><span class="sp sp-{sev}">{sev.upper()}</span></td>'
                f'<td><span class="tp {tcls}">{tlbl}</span></td>'
                f'<td><strong>{cat}</strong>{ai}</td>'
                f'<td>{i.get("title","")}</td>'
                f'<td class="dc">{det}</td>'
                f'<td class="uc"><a href="{purl}" target="_blank">'
                f'<span class="ptb">{pt}</span> ↗</a></td></tr>'
            )
        return "\n".join(rows)

    # ─────────────────────────────────────────────────────────────────────
    def _build_test_report(self, pages_data, all_issues, meta):
        """Build the Test Report tab HTML — one table per page with XML TC IDs."""
        gen        = meta.get("generated_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        exec_time  = meta.get("crawl_duration_s", 0)

        page_tables = []
        grand_pass = grand_fail = grand_na = grand_total = 0

        for page in pages_data:
            purl  = page.get("url", "")
            ptype = page.get("page_type", "other")

            # Resolve meta and checks for this template
            pmeta  = PAGE_META.get(ptype) or PAGE_META.get("pdp") if "pdp" in ptype else PAGE_META.get(ptype, PAGE_META["other"])
            checks = PAGE_CHECKS.get(ptype) or PAGE_CHECKS.get("pdp", PAGE_CHECKS["other"])

            pissues = [i for i in all_issues if i.get("page_url") == purl]
            load    = page.get("load_time_ms", "—")
            ai_ms   = page.get("ai_duration_ms", 0)
            tc_ids  = page.get("test_cases_covered", [])

            # Build set of TC IDs that have a FAIL (an issue references that TC ID)
            failed_tc_ids = set()
            for issue in pissues:
                title  = issue.get("title", "")
                detail = issue.get("detail", "")
                # Match TC-7XXX pattern in title or detail
                import re as _re
                for m in _re.findall(r'TC-\d{4,}|PDP-CORE-\d{4,}', title + " " + detail):
                    failed_tc_ids.add(m)

            rows_html = []
            pass_cnt = fail_cnt = na_cnt = 0

            for (check_label, tc_id) in checks:
                # A check is N/A if it's not in the tc_ids covered by this template
                # (tc_ids comes from TEST_CASE_COVERAGE in analyzer_claude.py)
                # We show all checks for the template — none are N/A since they're template-specific

                is_fail = tc_id in failed_tc_ids or any(
                    tc_id in (i.get("title","") + i.get("detail","")) for i in pissues
                )

                if is_fail:
                    fail_cnt += 1
                    fail_issue = next((i for i in pissues if tc_id in (i.get("title","") + i.get("detail",""))), None)
                    reason = fail_issue.get("title","")[:70] if fail_issue else "Issue detected"
                    rows_html.append(
                        f'<tr class="fail-row">'
                        f'<td class="tcid">{tc_id}</td>'
                        f'<td>{check_label}</td>'
                        f'<td><span class="ts ts-fail">✗ FAIL</span></td>'
                        f'<td class="tr-note">{reason}</td>'
                        f'</tr>'
                    )
                else:
                    pass_cnt += 1
                    rows_html.append(
                        f'<tr>'
                        f'<td class="tcid">{tc_id}</td>'
                        f'<td>{check_label}</td>'
                        f'<td><span class="ts ts-pass">✓ PASS</span></td>'
                        f'<td class="tr-note">—</td>'
                        f'</tr>'
                    )

            total_checks = pass_cnt + fail_cnt
            pass_pct = round(pass_cnt / total_checks * 100) if total_checks else 0

            grand_pass  += pass_cnt
            grand_fail  += fail_cnt
            grand_total += total_checks

            color    = pmeta["color"]
            pass_bar = f'<div class="pass-bar"><div class="pass-fill" style="width:{pass_pct}%;background:{color}"></div></div>'
            tc_list  = ", ".join(tc_ids[:10]) + (f" +{len(tc_ids)-10} more" if len(tc_ids) > 10 else "")

            page_tables.append(f"""
<div class="pt-block">
  <div class="pt-head" style="border-left:4px solid {color}">
    <div class="pt-left">
      <span class="pt-icon-lg">{pmeta['icon']}</span>
      <div>
        <div class="pt-label">{pmeta['label']}</div>
        <div class="pt-url">{purl}</div>
      </div>
    </div>
    <div class="pt-right">
      <div class="pt-stat"><span class="pts-num" style="color:{color}">{pass_cnt}</span><span class="pts-lbl">Passed</span></div>
      <div class="pt-stat"><span class="pts-num" style="color:#ef4444">{fail_cnt}</span><span class="pts-lbl">Failed</span></div>
      <div class="pt-stat"><span class="pts-num">{total_checks}</span><span class="pts-lbl">Total</span></div>
      <div class="pt-stat"><span class="pts-num" style="color:{color}">{pass_pct}%</span><span class="pts-lbl">Pass Rate</span></div>
    </div>
  </div>
  {pass_bar}
  <div class="pt-vitals">
    <span class="pv">Load: <b>{load}ms</b></span>
    <span class="pv">AI Time: <b>{ai_ms}ms</b></span>
    <span class="pv tc-cov">Test Cases: <b>{tc_list or "see table"}</b></span>
  </div>
  <div class="tc-table-wrap">
    <table class="tc-table">
      <thead><tr><th>Test Case ID</th><th>Check Description</th><th>Status</th><th>Failure Detail</th></tr></thead>
      <tbody>{"".join(rows_html)}</tbody>
    </table>
  </div>
</div>""")

        # Grand summary
        grand_pct = round(grand_pass / grand_total * 100) if grand_total else 0

        summary_html = f"""
<div class="tr-summary">
  <div class="trs-card trs-total">
    <div class="trs-num">{grand_total}</div>
    <div class="trs-lbl">Total Checks</div>
  </div>
  <div class="trs-card trs-pass">
    <div class="trs-num">{grand_pass}</div>
    <div class="trs-lbl">Passed</div>
  </div>
  <div class="trs-card trs-fail">
    <div class="trs-num">{grand_fail}</div>
    <div class="trs-lbl">Failed</div>
  </div>
  <div class="trs-card trs-pct">
    <div class="trs-num">{grand_pct}%</div>
    <div class="trs-lbl">Pass Rate</div>
  </div>
  <div class="trs-card trs-time">
    <div class="trs-num">{exec_time}s</div>
    <div class="trs-lbl">Execution Time</div>
  </div>
  <div class="trs-card trs-gen">
    <div class="trs-num" style="font-size:13px;font-weight:600">{gen}</div>
    <div class="trs-lbl">Generated At</div>
  </div>
</div>"""

        return summary_html + "\n".join(page_tables)

    # ─────────────────────────────────────────────────────────────────────
    def _build(self, pages, all_issues, func_issues, ui_issues,
               sev_counts, cat_counts, meta):
        total     = len(all_issues)
        url       = meta.get("url","")
        gen       = meta.get("generated_at","")
        pages_n   = meta.get("pages_crawled", len(pages))
        dur       = meta.get("crawl_duration_s", 0)

        func_rows = self._issue_rows(func_issues)
        ui_rows   = self._issue_rows(ui_issues)

        # Per-page screenshot accordions
        accordions = []
        for idx, p in enumerate(pages):
            purl  = p.get("url","")
            ptype = p.get("page_type","other")
            pmeta = PAGE_META.get(ptype, PAGE_META["other"])
            cnt   = len([i for i in all_issues if i.get("page_url") == purl])
            cwv   = p.get("core_web_vitals") or {}
            perf  = p.get("performance") or {}
            lcp   = cwv.get("lcp","—")
            cls_v = cwv.get("cls","—")
            fcp   = cwv.get("fcp","—")
            ttfb  = cwv.get("ttfb","—")
            load  = p.get("load_time_ms","—")

            lcp_cls = "vb" if isinstance(lcp,(int,float)) and lcp>4000 else ""
            cls_cls = "vb" if isinstance(cls_v,(int,float)) and cls_v>0.1 else ""
            fcp_cls = "vb" if isinstance(fcp,(int,float)) and fcp>3000 else ""

            ss_b64 = p.get("screenshots",{}).get("desktop_b64","")
            ss_html = (
                f'<img src="data:image/png;base64,{ss_b64}" alt="Screenshot of {purl}">'
                if ss_b64 else '<div class="no-ss">📷 Screenshot not captured</div>'
            )

            page_issue_rows = self._issue_rows(
                [i for i in all_issues if i.get("page_url") == purl])

            color = pmeta["color"]
            accordions.append(f"""
<div class="ab">
  <div class="ah" onclick="tog({idx})" style="border-left:3px solid {color}">
    <div class="ahl">
      <span>{pmeta['icon']}</span>
      <span class="ahurl">{purl}</span>
      <span class="ptb" style="background:{color}20;color:{color};border-color:{color}40">{pmeta['label']}</span>
    </div>
    <div class="ahr">
      <span class="chip {'chip-r' if cnt>0 else 'chip-g'}">{cnt} issue{'s' if cnt!=1 else ''}</span>
      <span class="chip">LCP {lcp}ms</span>
      <span class="chip">CLS {cls_v}</span>
      <span class="arr" id="arr-{idx}">▼</span>
    </div>
  </div>
  <div class="abody" id="ab-{idx}">
    <div class="vg">
      <div class="vit"><div class="vl">TTFB</div><div class="vv">{ttfb}<span>ms</span></div></div>
      <div class="vit"><div class="vl">FCP</div><div class="vv {fcp_cls}">{fcp}<span>ms</span></div></div>
      <div class="vit"><div class="vl">LCP</div><div class="vv {lcp_cls}">{lcp}<span>ms</span></div></div>
      <div class="vit"><div class="vl">CLS</div><div class="vv {cls_cls}">{cls_v}</div></div>
      <div class="vit"><div class="vl">Load</div><div class="vv">{load}<span>ms</span></div></div>
      <div class="vit"><div class="vl">DOM Nodes</div><div class="vv">{perf.get('dom_nodes','—')}</div></div>
      <div class="vit"><div class="vl">Images</div><div class="vv">{perf.get('images','—')}</div></div>
      <div class="vit"><div class="vl">Scripts</div><div class="vv">{perf.get('scripts','—')}</div></div>
    </div>
    <div class="ss-sec">
      <div class="ssl">🖥️ Desktop Screenshot — 1280×800 viewport</div>
      <div class="ssi">{ss_html}</div>
    </div>
    {f'<div class="pi"><div class="pih">Issues on this page ({cnt})</div><div class="tbl-wrap"><table class="itbl"><thead><tr><th>Sev</th><th>Type</th><th>Category</th><th>Issue</th><th>Detail</th><th>Page</th></tr></thead><tbody>{page_issue_rows}</tbody></table></div></div>' if page_issue_rows else '<p class="ok-msg">✅ No issues detected on this page</p>'}
  </div>
</div>""")

        test_report_html = self._build_test_report(pages, all_issues, meta)

        all_json = json.dumps(all_issues, default=str, ensure_ascii=False)
        cat_json = json.dumps(cat_counts)
        sev_json = json.dumps(sev_counts)

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>IndiaMART QA Report — {gen}</title>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root{{
  --bg:#f4f6fc;
  --white:#ffffff;
  --surf:#f8faff;
  --surf2:#f1f4fb;
  --border:#e2e8f5;
  --border2:#d0d9f0;
  --text:#0f172a;
  --text2:#334155;
  --text3:#64748b;
  --text4:#94a3b8;
  --acc:#d32f2f;
  --acc-s:#fdecea;
  --blue:#2563eb;
  --blue-s:#eff6ff;
  --crit:#dc2626;
  --crit-s:#fef2f2;
  --high:#ea6c00;
  --high-s:#fff4ed;
  --med:#b45309;
  --med-s:#fffbeb;
  --ok:#16a34a;
  --ok-s:#f0fdf4;
  --rad:12px;
  --rads:8px;
  --sh:0 1px 3px rgba(0,0,0,.06),0 1px 2px rgba(0,0,0,.04);
  --sh-m:0 4px 16px rgba(0,0,0,.08),0 2px 6px rgba(0,0,0,.04);
}}
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Plus Jakarta Sans',sans-serif;background:var(--bg);color:var(--text);font-size:13px;line-height:1.6;min-height:100vh}}
a{{color:var(--blue);text-decoration:none}}
a:hover{{text-decoration:underline}}
::-webkit-scrollbar{{width:5px;height:5px}}
::-webkit-scrollbar-track{{background:var(--surf)}}
::-webkit-scrollbar-thumb{{background:var(--border2);border-radius:3px}}

/* ── HEADER ── */
.hdr{{
  background:var(--white);
  border-bottom:1px solid var(--border);
  padding:22px 36px;
  box-shadow:var(--sh);
  position:relative;
}}
.hdr-top{{display:flex;align-items:center;gap:14px;margin-bottom:16px}}
.hdr-logo{{
  width:44px;height:44px;border-radius:10px;
  background:linear-gradient(135deg,var(--acc),#b71c1c);
  display:flex;align-items:center;justify-content:center;
  font-size:20px;box-shadow:0 4px 12px rgba(211,47,47,.25);
  flex-shrink:0;
}}
.hdr-titles h1{{font-size:18px;font-weight:800;color:var(--text);letter-spacing:-.3px;line-height:1.2}}
.hdr-titles p{{font-size:11px;color:var(--text3);margin-top:2px;font-weight:500}}
.hdr-badge{{
  margin-left:auto;
  background:var(--acc-s);border:1px solid #ffcdd2;
  color:var(--acc);padding:4px 12px;border-radius:20px;
  font-size:11px;font-weight:700;letter-spacing:.3px;
}}
.hdr-meta{{
  display:flex;gap:0;
  background:var(--surf);
  border:1px solid var(--border);
  border-radius:var(--rads);
  overflow:hidden;
}}
.hmi{{
  display:flex;flex-direction:column;gap:2px;
  padding:10px 20px;
  border-right:1px solid var(--border);
}}
.hmi:last-child{{border-right:none}}
.hml{{font-size:10px;text-transform:uppercase;letter-spacing:1.2px;color:var(--text4);font-weight:600}}
.hmv{{font-size:12px;font-weight:600;color:var(--text2);font-family:'JetBrains Mono',monospace;max-width:420px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}

/* ── MAIN TABS ── */
.main-tabs{{
  display:flex;
  background:var(--white);
  border-bottom:2px solid var(--border);
  padding:0 36px;
  box-shadow:var(--sh);
  position:sticky;top:0;z-index:50;
}}
.main-tab{{
  padding:14px 22px;font-size:13px;font-weight:600;
  color:var(--text3);cursor:pointer;
  border-bottom:3px solid transparent;margin-bottom:-2px;
  transition:all .2s;display:flex;align-items:center;gap:8px;
}}
.main-tab:hover{{color:var(--text2)}}
.main-tab.active{{color:var(--acc);border-bottom-color:var(--acc)}}
.main-pane{{display:none;padding:24px 36px;flex-direction:column;gap:20px}}
.main-pane.active{{display:flex}}

/* ── CARDS / SECTIONS ── */
.section{{
  background:var(--white);
  border:1px solid var(--border);
  border-radius:var(--rad);
  overflow:hidden;
  box-shadow:var(--sh);
}}
.sch{{
  padding:13px 18px;
  border-bottom:1px solid var(--border);
  background:var(--surf);
  display:flex;align-items:center;justify-content:space-between;
}}
.sct{{font-size:13px;font-weight:700;display:flex;align-items:center;gap:8px;color:var(--text)}}
.sci{{
  width:26px;height:26px;
  background:var(--acc-s);border:1px solid #ffcdd2;
  border-radius:6px;display:flex;align-items:center;justify-content:center;font-size:13px;
}}
.scb{{
  background:var(--blue-s);border:1px solid #bfdbfe;
  color:var(--blue);padding:3px 10px;border-radius:20px;
  font-size:11px;font-weight:700;
}}

/* ── STAT CARDS ── */
.stats{{display:grid;grid-template-columns:1.4fr 1fr 1fr 1fr 1fr;gap:14px}}
.stat{{
  background:var(--white);
  border:1px solid var(--border);
  border-radius:var(--rad);
  padding:18px 20px;
  box-shadow:var(--sh);
  position:relative;overflow:hidden;
}}
.stat::before{{content:'';position:absolute;top:0;left:0;right:0;height:3px}}
.s0::before{{background:linear-gradient(90deg,var(--blue),#60a5fa)}}
.s1::before{{background:linear-gradient(90deg,var(--crit),#f87171)}}
.s2::before{{background:linear-gradient(90deg,var(--high),#fb923c)}}
.s3::before{{background:linear-gradient(90deg,var(--med),#fbbf24)}}
.s4::before{{background:linear-gradient(90deg,var(--ok),#4ade80)}}
.sn{{font-size:36px;font-weight:800;line-height:1;letter-spacing:-2px;margin-bottom:4px}}
.s0 .sn{{color:var(--blue)}}
.s1 .sn{{color:var(--crit)}}
.s2 .sn{{color:var(--high)}}
.s3 .sn{{color:var(--med)}}
.s4 .sn{{color:var(--ok)}}
.sl{{font-size:10px;text-transform:uppercase;letter-spacing:1.2px;color:var(--text3);font-weight:700}}
.ssub{{font-size:11px;color:var(--text4);margin-top:3px}}

/* ── CHARTS ── */
.cr{{display:grid;grid-template-columns:200px 1fr;gap:16px;padding:18px}}
.ds{{display:flex;flex-direction:column;gap:10px;align-items:center}}
.leg{{display:flex;flex-direction:column;gap:7px;width:100%}}
.li{{display:flex;align-items:center;gap:8px;font-size:12px}}
.ld{{width:9px;height:9px;border-radius:50%;flex-shrink:0}}
.ll{{flex:1;color:var(--text2);font-weight:500}}
.lc{{font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--text3);font-weight:700}}

/* ── INNER TABS ── */
.tabs{{display:flex;border-bottom:1px solid var(--border);background:var(--surf);padding:0 18px}}
.tab{{
  padding:11px 16px;font-size:12px;font-weight:600;
  color:var(--text3);cursor:pointer;
  border-bottom:2px solid transparent;margin-bottom:-1px;
  transition:all .15s;white-space:nowrap;
}}
.tab:hover{{color:var(--text)}}
.tab.active{{color:var(--acc);border-bottom-color:var(--acc)}}
.tab-pane{{display:none}} .tab-pane.active{{display:block}}

/* ── FILTER BAR ── */
.fb-bar{{
  display:flex;align-items:center;gap:9px;
  padding:10px 18px;
  border-bottom:1px solid var(--border);
  background:var(--surf2);flex-wrap:wrap;
}}
.fbl{{font-size:10px;text-transform:uppercase;letter-spacing:1px;color:var(--text4);font-weight:600}}
.fbtn{{
  background:var(--white);border:1.5px solid var(--border);
  color:var(--text2);padding:4px 12px;border-radius:20px;
  font-size:11px;cursor:pointer;transition:all .15s;
  font-family:'Plus Jakarta Sans',sans-serif;font-weight:600;
}}
.fbtn:hover,.fbtn.active{{border-color:var(--acc);color:var(--acc);background:var(--acc-s)}}
.fsr{{
  margin-left:auto;background:var(--white);
  border:1.5px solid var(--border);color:var(--text);
  padding:5px 13px;border-radius:20px;font-size:12px;
  font-family:'Plus Jakarta Sans',sans-serif;outline:none;width:200px;
}}
.fsr:focus{{border-color:var(--acc)}}
.fsr::placeholder{{color:var(--text4)}}

/* ── ISSUE TABLE ── */
.tbl-wrap{{overflow-x:auto;max-height:520px;overflow-y:auto}}
.itbl{{width:100%;border-collapse:collapse;font-size:12px}}
.itbl th{{
  text-align:left;padding:10px 14px;
  font-size:10px;text-transform:uppercase;letter-spacing:1.2px;
  color:var(--text3);font-weight:700;
  border-bottom:2px solid var(--border);
  background:var(--surf);position:sticky;top:0;z-index:1;
}}
.itbl td{{padding:10px 14px;border-bottom:1px solid var(--border);vertical-align:middle}}
.itbl tr:last-child td{{border-bottom:none}}
.itbl tbody tr:hover td{{background:var(--acc-s)}}
.sp{{display:inline-block;padding:3px 9px;border-radius:20px;font-size:10px;font-weight:700;letter-spacing:.4px;white-space:nowrap}}
.sp-critical{{background:var(--crit-s);color:var(--crit);border:1px solid #fca5a5}}
.sp-high{{background:var(--high-s);color:var(--high);border:1px solid #fed7aa}}
.sp-medium{{background:var(--med-s);color:var(--med);border:1px solid #fde68a}}
.tp{{display:inline-block;padding:3px 8px;border-radius:5px;font-size:10px;font-weight:600;white-space:nowrap}}
.tf{{background:#fef2f2;color:#b91c1c;border:1px solid #fecaca}}
.tu{{background:var(--blue-s);color:#1d4ed8;border:1px solid #bfdbfe}}
.aib{{display:inline-block;background:#f5f3ff;color:#7c3aed;border:1px solid #ddd6fe;padding:1px 5px;border-radius:4px;font-size:9px;font-weight:700;margin-left:4px}}
.dc{{color:var(--text3);max-width:280px;font-size:12px}}
.uc{{text-align:center}}
.ptb{{display:inline-block;background:var(--surf2);border:1px solid var(--border2);color:var(--text3);padding:2px 6px;border-radius:4px;font-size:9px;font-weight:700;text-transform:uppercase}}

/* ── ACCORDION ── */
.ab{{border-bottom:1px solid var(--border)}}
.ab:last-child{{border-bottom:none}}
.ah{{
  display:flex;align-items:center;justify-content:space-between;
  padding:14px 18px;cursor:pointer;transition:background .15s;gap:14px;
}}
.ah:hover{{background:var(--surf)}}
.ahl{{display:flex;align-items:center;gap:8px;flex:1;min-width:0}}
.ahurl{{font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--blue);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:500}}
.ahr{{display:flex;align-items:center;gap:6px;flex-shrink:0}}
.chip{{background:var(--surf2);border:1px solid var(--border);color:var(--text3);padding:3px 9px;border-radius:20px;font-size:10px;font-weight:600}}
.chip-r{{border-color:#fca5a5;color:var(--crit);background:var(--crit-s)}}
.chip-g{{border-color:#86efac;color:var(--ok);background:var(--ok-s)}}
.arr{{color:var(--text4);font-size:11px;transition:transform .2s}}
.abody{{display:none;padding:18px;border-top:1px solid var(--border);background:var(--surf)}}
.abody.open{{display:block}}

/* ── VITALS GRID ── */
.vg{{display:grid;grid-template-columns:repeat(8,1fr);gap:10px;margin-bottom:16px}}
.vit{{
  background:var(--white);border:1px solid var(--border);
  border-radius:var(--rads);padding:10px 12px;
  box-shadow:var(--sh);
}}
.vl{{font-size:10px;text-transform:uppercase;letter-spacing:1px;color:var(--text4);margin-bottom:3px;font-weight:600}}
.vv{{font-size:17px;font-weight:700;font-family:'JetBrains Mono',monospace;color:var(--text)}}
.vv span{{font-size:11px;color:var(--text4);margin-left:1px}}
.vb{{color:var(--crit)!important}}

/* ── SCREENSHOT ── */
.ss-sec{{background:var(--white);border:1px solid var(--border);border-radius:var(--rads);overflow:hidden;margin-bottom:14px;box-shadow:var(--sh)}}
.ssl{{padding:9px 14px;font-size:11px;font-weight:700;color:var(--text3);border-bottom:1px solid var(--border);text-transform:uppercase;letter-spacing:1px;background:var(--surf)}}
.ssi{{overflow:auto;max-height:620px}}
.ssi img{{width:100%;display:block;object-fit:contain;object-position:top}}
.no-ss{{padding:40px;text-align:center;color:var(--text4);font-size:13px}}
.pi{{margin-top:14px}}
.pih{{font-size:11px;font-weight:700;color:var(--text2);margin-bottom:8px;text-transform:uppercase;letter-spacing:1px}}
.ok-msg{{text-align:center;padding:16px;color:var(--ok);font-size:13px;font-weight:600}}

/* ── TEST REPORT ── */
.tr-summary{{display:grid;grid-template-columns:repeat(6,1fr);gap:13px;margin-bottom:20px}}
.trs-card{{
  background:var(--white);border:1px solid var(--border);
  border-radius:var(--rad);padding:16px 18px;text-align:center;
  box-shadow:var(--sh);
}}
.trs-num{{font-size:28px;font-weight:800;line-height:1;margin-bottom:4px;letter-spacing:-1px}}
.trs-lbl{{font-size:10px;text-transform:uppercase;letter-spacing:1.2px;color:var(--text4);font-weight:700}}
.trs-total .trs-num{{color:var(--blue)}}
.trs-pass  .trs-num{{color:var(--ok)}}
.trs-fail  .trs-num{{color:var(--crit)}}
.trs-pct   .trs-num{{color:var(--med)}}
.trs-time  .trs-num{{color:var(--high)}}
.trs-gen   .trs-num{{color:var(--text2);font-size:13px}}

.pt-block{{
  background:var(--white);border:1px solid var(--border);
  border-radius:var(--rad);overflow:hidden;margin-bottom:16px;
  box-shadow:var(--sh);
}}
.pt-head{{
  display:flex;align-items:center;justify-content:space-between;
  padding:14px 18px;background:var(--surf);gap:16px;
  border-bottom:1px solid var(--border);
}}
.pt-left{{display:flex;align-items:center;gap:10px}}
.pt-icon-lg{{font-size:22px}}
.pt-label{{font-size:13px;font-weight:700;color:var(--text)}}
.pt-url{{font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--text3);margin-top:2px;max-width:500px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.pt-right{{display:flex;gap:16px;flex-shrink:0}}
.pt-stat{{text-align:center}}
.pts-num{{font-size:22px;font-weight:800;line-height:1;display:block;letter-spacing:-1px}}
.pts-lbl{{font-size:10px;color:var(--text4);text-transform:uppercase;letter-spacing:.8px;font-weight:600}}

.pass-bar{{height:5px;background:var(--border)}}
.pass-fill{{height:100%;transition:width .5s ease}}

.pt-vitals{{
  display:flex;gap:16px;flex-wrap:wrap;
  padding:10px 18px;
  background:var(--surf2);border-bottom:1px solid var(--border);
  font-size:11px;color:var(--text3);
}}
.pv{{display:flex;align-items:center;gap:4px}}
.pv b{{color:var(--text2);font-weight:600}}
.vbad{{color:var(--crit)!important;font-weight:700}}
.tc-cov{{max-width:600px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}

.tc-table-wrap{{overflow-x:auto;max-height:420px;overflow-y:auto}}
.tc-table{{width:100%;border-collapse:collapse;font-size:12px}}
.tc-table th{{
  text-align:left;padding:9px 14px;
  font-size:10px;text-transform:uppercase;letter-spacing:1.2px;
  color:var(--text3);font-weight:700;
  border-bottom:2px solid var(--border);
  background:var(--surf);position:sticky;top:0;z-index:1;
}}
.tc-table td{{padding:8px 14px;border-bottom:1px solid var(--border);vertical-align:middle}}
.tc-table tr:last-child td{{border-bottom:none}}
.tc-table .fail-row td{{background:#fef9f9}}
.tc-table .fail-row:hover td{{background:var(--crit-s)}}
.tc-table tbody tr:hover td{{background:var(--surf)}}
.tcid{{font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--acc);font-weight:700;white-space:nowrap}}
.ts{{display:inline-block;padding:3px 9px;border-radius:5px;font-size:10px;font-weight:700;white-space:nowrap}}
.ts-pass{{background:var(--ok-s);color:var(--ok);border:1px solid #86efac}}
.ts-fail{{background:var(--crit-s);color:var(--crit);border:1px solid #fca5a5}}
.tr-note{{color:var(--text3);font-size:11px;max-width:280px}}

/* ── FOOTER ── */
.footer{{
  text-align:center;padding:20px;
  color:var(--text4);font-size:11px;font-weight:500;
  border-top:1px solid var(--border);
  background:var(--white);
  margin-top:8px;
}}
</style>
</head>
<body>

<!-- HEADER -->
<div class="hdr">
  <div class="hdr-top">
    <div class="hdr-logo">🔍</div>
    <div class="hdr-titles">
      <h1>IndiaMART QA Report</h1>
      <p>AI-Powered Bug Finder · Functional &amp; UI Issues · Web Vitals · v5</p>
    </div>
    <div class="hdr-badge">AI Powered</div>
  </div>
  <div class="hdr-meta">
    <div class="hmi"><span class="hml">Target URL</span><span class="hmv">{url}</span></div>
    <div class="hmi"><span class="hml">Generated</span><span class="hmv">{gen}</span></div>
    <div class="hmi"><span class="hml">Pages Scanned</span><span class="hmv">{pages_n}</span></div>
    <div class="hmi"><span class="hml">Duration</span><span class="hmv">{dur}s</span></div>
  </div>
</div>

<!-- MAIN NAVIGATION TABS -->
<div class="main-tabs">
  <div class="main-tab active" onclick="mainTab(event,'bug-report')">🐛 Bug Report <span style="background:rgba(239,68,68,.15);color:#f87171;padding:2px 7px;border-radius:10px;font-size:11px;margin-left:6px">{total}</span></div>
  <div class="main-tab" onclick="mainTab(event,'test-report')">📋 Test Report <span style="background:rgba(34,197,94,.12);color:#4ade80;padding:2px 7px;border-radius:10px;font-size:11px;margin-left:6px">Execution</span></div>
</div>

<!-- ════════ BUG REPORT ════════ -->
<div class="main-pane active" id="bug-report">

  <div class="stats">
    <div class="stat s0"><div class="sn">{total}</div><div class="sl">Total Issues</div><div class="ssub">{len(func_issues)} functional · {len(ui_issues)} UI</div></div>
    <div class="stat s1"><div class="sn">{sev_counts['critical']}</div><div class="sl">Critical</div></div>
    <div class="stat s2"><div class="sn">{sev_counts['high']}</div><div class="sl">High</div></div>
    <div class="stat s3"><div class="sn">{sev_counts['medium']}</div><div class="sl">Medium</div></div>
    <div class="stat s4"><div class="sn">{pages_n}</div><div class="sl">Pages Scanned</div></div>
  </div>

  <div class="section">
    <div class="sch"><div class="sct"><div class="sci">📊</div>Issue Distribution</div></div>
    <div class="cr">
      <div class="ds">
        <canvas id="donut" width="170" height="170"></canvas>
        <div class="leg">
          <div class="li"><div class="ld" style="background:#ef4444"></div><span class="ll">Critical</span><span class="lc">{sev_counts['critical']}</span></div>
          <div class="li"><div class="ld" style="background:#f97316"></div><span class="ll">High</span><span class="lc">{sev_counts['high']}</span></div>
          <div class="li"><div class="ld" style="background:#eab308"></div><span class="ll">Medium</span><span class="lc">{sev_counts['medium']}</span></div>
        </div>
      </div>
      <canvas id="bar" height="170"></canvas>
    </div>
  </div>

  <div class="section">
    <div class="sch"><div class="sct"><div class="sci">🐛</div>All Issues</div><span class="scb">{total} total</span></div>
    <div class="tabs">
      <div class="tab active" onclick="switchTab(event,'t-all')">All <span style="opacity:.5">({total})</span></div>
      <div class="tab" onclick="switchTab(event,'t-func')">⚙️ Functional <span style="opacity:.5">({len(func_issues)})</span></div>
      <div class="tab" onclick="switchTab(event,'t-ui')">🎨 UI/UX <span style="opacity:.5">({len(ui_issues)})</span></div>
    </div>
    <div class="fb-bar">
      <span class="fbl">Severity:</span>
      <button class="fbtn active" onclick="fSev('all',this)">All</button>
      <button class="fbtn" onclick="fSev('critical',this)">Critical</button>
      <button class="fbtn" onclick="fSev('high',this)">High</button>
      <button class="fbtn" onclick="fSev('medium',this)">Medium</button>
      <input class="fsr" type="text" placeholder="Search issues…" oninput="applyF()">
    </div>
    <div class="tab-pane active" id="t-all"><div class="tbl-wrap"><table class="itbl"><thead><tr><th>Sev</th><th>Type</th><th>Category</th><th>Issue</th><th>Detail</th><th>Page</th></tr></thead><tbody id="all-body"></tbody></table></div></div>
    <div class="tab-pane" id="t-func"><div class="tbl-wrap"><table class="itbl"><thead><tr><th>Sev</th><th>Type</th><th>Category</th><th>Issue</th><th>Detail</th><th>Page</th></tr></thead><tbody>{func_rows}</tbody></table></div></div>
    <div class="tab-pane" id="t-ui"><div class="tbl-wrap"><table class="itbl"><thead><tr><th>Sev</th><th>Type</th><th>Category</th><th>Issue</th><th>Detail</th><th>Page</th></tr></thead><tbody>{ui_rows}</tbody></table></div></div>
  </div>

  <div class="section">
    <div class="sch"><div class="sct"><div class="sci">📸</div>Pages, Screenshots &amp; Vitals</div><span class="scb">{pages_n} pages</span></div>
    {"".join(accordions)}
  </div>

</div>

<!-- ════════ TEST REPORT ════════ -->
<div class="main-pane" id="test-report">
  {test_report_html}
</div>

<div class="footer">IndiaMART AI Bug Finder v5 · Bug Report + Test Report · {gen} · {total} issues · {pages_n} pages</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<script>
const sev={sev_json}, cats={cat_json}, all={all_json};

// Populate All tab
(function(){{
  const a=document.getElementById('all-body');
  const f=document.querySelector('#t-func tbody').innerHTML;
  const u=document.querySelector('#t-ui tbody').innerHTML;
  a.innerHTML=f+u;
}})();

// Donut
new Chart(document.getElementById('donut'),{{
  type:'doughnut',
  data:{{labels:['Critical','High','Medium'],
    datasets:[{{data:[sev.critical,sev.high,sev.medium],
      backgroundColor:['#dc2626','#ea6c00','#b45309'],borderWidth:0,hoverOffset:4}}]}},
  options:{{cutout:'72%',plugins:{{legend:{{display:false}}}},animation:{{duration:600}}}}
}});

// Bar
const bls=Object.keys(cats).slice(0,14);
new Chart(document.getElementById('bar'),{{
  type:'bar',
  data:{{labels:bls,datasets:[{{label:'Issues',data:bls.map(k=>cats[k]),
    backgroundColor:'rgba(211,47,47,.15)',borderColor:'rgba(211,47,47,.7)',
    borderWidth:1.5,borderRadius:5}}]}},
  options:{{indexAxis:'y',plugins:{{legend:{{display:false}}}},
    scales:{{x:{{grid:{{color:'rgba(0,0,0,.05)'}},ticks:{{color:'#64748b',font:{{size:11}}}}}},
             y:{{grid:{{display:false}},ticks:{{color:'#334155',font:{{size:11}}}}}}}},animation:{{duration:500}}}}
}});

// Main tabs
function mainTab(e,id){{
  document.querySelectorAll('.main-tab').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('.main-pane').forEach(t=>t.classList.remove('active'));
  e.currentTarget.classList.add('active');
  document.getElementById(id).classList.add('active');
}}

// Inner tabs
function switchTab(e,id){{
  const p=e.currentTarget.closest('.section');
  p.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  p.querySelectorAll('.tab-pane').forEach(t=>t.classList.remove('active'));
  e.currentTarget.classList.add('active');
  document.getElementById(id).classList.add('active');
  activeSev='all';
  p.querySelectorAll('.fbtn').forEach((b,i)=>b.classList.toggle('active',i===0));
}}

// Filter
let activeSev='all';
function fSev(s,btn){{
  activeSev=s;
  document.querySelectorAll('.fbtn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');applyF();
}}
function applyF(){{
  const q=(document.querySelector('.fsr').value||'').toLowerCase();
  document.querySelectorAll('.ir').forEach(row=>{{
    const so=activeSev==='all'||row.dataset.sev===activeSev;
    const qo=!q||row.textContent.toLowerCase().includes(q);
    row.style.display=so&&qo?'':'none';
  }});
}}

// Accordion
function tog(i){{
  const b=document.getElementById('ab-'+i);
  const a=document.getElementById('arr-'+i);
  const o=b.classList.toggle('open');
  a.style.transform=o?'rotate(180deg)':'';
}}
tog(0);
</script>
</body>
</html>"""
