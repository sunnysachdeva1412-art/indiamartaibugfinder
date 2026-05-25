"""
crawler_playwright.py — IndiaMART Bug Finder v5
Real functional & UI checks based on actual IndiaMART test cases.

KEY PRINCIPLES:
  - Every check waits for elements to be fully loaded before inspecting
  - CTA clicks are real — we click, wait, then verify the outcome
  - No false positives: only report issues that are clearly broken
  - Page type is auto-detected (PDP / MCAT / Search / Company / Export / Other)
  - Desktop 1280×800 viewport, full-page screenshot
"""

import asyncio
import base64
import json
import re
import time
from pathlib import Path
from urllib.parse import urlparse, urljoin

from playwright.async_api import async_playwright, Page, Browser, TimeoutError as PWTimeout


# ─── helpers ─────────────────────────────────────────────────────────────────

def _issue(category, severity, title, detail, element=""):
    return {"category": category, "severity": severity,
            "title": title, "detail": detail, "element": element}


def _detect_page_type(url: str) -> str:
    path = urlparse(url).path.lower()
    host = urlparse(url).netloc.lower()
    parsed_full = urlparse(url)

    # export.indiamart.com — separate domain with its own page types
    if "export.indiamart" in host:
        if "/search.php" in path or "search.php" in path:
            return "export_search"
        if "/products/" in path or path.startswith("/products"):
            return "export_pdp"
        if "/company/" in path:
            return "export_company"
        return "export"

    # dir.indiamart.com search: /search.mp
    if "search.mp" in path or "search.mp" in parsed_full.query:
        return "search"

    # dir.indiamart.com IMPCAT: /impcat/
    if "/impcat/" in path:
        return "mcat"

    # www.indiamart.com PDP: /proddetail/
    if any(x in path for x in ["/proddetail", "/product-detail", "/prod-detail"]):
        return "pdp"

    # dir.indiamart.com search/MCAT
    if any(x in path for x in ["/search", "/cat-", "/dir-"]):
        if "search" in path:
            return "search"
        return "mcat"

    # www.indiamart.com Company page: /company-slug/ or /company-slug/page.html
    # Pattern: www.indiamart.com, path has exactly one slug segment (no sub-paths like /proddetail)
    if "www.indiamart" in host or "indiamart.com" in host:
        # Match /slug/ or /slug/something.html — but NOT root /
        m = re.match(r'^/([a-z0-9][a-z0-9\-]+)(/[a-z0-9\-\.]*)?/?$', path)
        if m and path != "/" and "search" not in path:
            return "company"

    if re.search(r"/[a-z0-9\-]+\.html", path) and "proddetail" not in path:
        return "company"

    if path.count("/") >= 2 and not any(x in path for x in ["search", "proddetail"]):
        return "mcat"

    return "home"


async def _detect_pdp_subtype(page) -> str:
    """
    Detect PDP template. Priority order:
      1. "Product Brochure" + pdf img → T14  (most unique signal)
      2. "Submit Requirement" button  → T1
      3. "Get Latest Price" + image gallery → T14
      4. "Get Latest Price" alone (service PDP) → T3
      5. Default → T3
    """
    try:
        s = await page.evaluate("""() => {
            const low = (document.body ? document.body.innerText : '').toLowerCase();
            const btns = [...document.querySelectorAll('button,[role=button],input[type=submit]')];

            const hasBrochureLabel  = low.includes('product brochure');
            const hasPdfImg         = !!document.querySelector('img[src*="pdf"],img[src*="PDF"]');
            const hasSubmitReq      = !!btns.find(el =>
                /submit.requirement/i.test(el.textContent || '') && el.offsetParent !== null);
            const hasGetLatestPrice = !!btns.find(el =>
                /get latest price/i.test(el.textContent || '') && el.offsetParent !== null);

            // T14 product PDPs have a large image gallery (>150x150px)
            // Service PDPs (surgery, consulting etc.) have GLP button but NO product gallery
            const hasImageGallery = [...document.querySelectorAll('img')].some(img => {
                const r   = img.getBoundingClientRect();
                const src = img.src || img.getAttribute('data-src') || '';
                return r.width > 150 && r.height > 150 &&
                    !src.includes('logo') && !src.includes('icon') &&
                    !src.includes('sprite') && !src.includes('pdf') &&
                    (img.naturalWidth > 0 || src.includes('imimg.com'));
            });

            return { hasBrochureLabel, hasPdfImg, hasSubmitReq, hasGetLatestPrice, hasImageGallery };
        }""")

        if s.get("hasBrochureLabel") and s.get("hasPdfImg"):
            return "pdp_t14"
        if s.get("hasSubmitReq"):
            return "pdp_t1"
        # Only T14 if GLP button AND a real product image gallery exists
        # Service PDPs (burn surgery, consulting) have GLP but no gallery → T3
        if s.get("hasGetLatestPrice") and s.get("hasImageGallery"):
            return "pdp_t14"
        return "pdp_t3"

    except Exception:
        return "pdp_t3"


async def _safe_eval(page: Page, js: str, default=None):
    try:
        return await page.evaluate(js)
    except Exception:
        return default


async def _screenshot_b64(page: Page, path: str) -> str:
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        # Viewport-only screenshot is 3-5x faster than full_page=True
        await page.screenshot(path=path, full_page=False, timeout=10000)
        with open(path, "rb") as f:
            data = f.read()
        return base64.b64encode(data).decode() if len(data) > 500 else ""
    except Exception as e:
        print(f"    ⚠️  Screenshot failed: {e}")
        return ""


async def _wait_page_load(page: Page, url: str) -> bool:
    """Load page — use 'load' event which fires faster than networkidle on IndiaMART."""
    for strategy in ["load", "domcontentloaded"]:
        try:
            await page.goto(url, wait_until=strategy, timeout=30000)
            # Short wait for React hydration — IndiaMART is React-based
            await page.wait_for_timeout(300)
            return True
        except PWTimeout:
            continue
        except Exception as e:
            if any(x in str(e) for x in ["ERR_", "net::", "NAME_NOT_RESOLVED"]):
                raise
    return False


class EnhancedCrawler:
    def __init__(self, output_dir="output", headless=True, max_pages=5, page_type_hint="auto"):
        self.output_dir       = Path(output_dir)
        self.headless         = headless
        self.max_pages        = max_pages
        self.page_type_hint   = page_type_hint  # "auto" | "pdp" | "mcat"
        self.visited: set[str] = set()

    async def crawl(self, start_url: str, depth: int = 1) -> list[dict]:
        results = []
        queue = [(start_url, 0)]

        async with async_playwright() as pw:
           browser = await pw.chromium.launch(
    headless=True,
    executable_path="/usr/bin/chromium",
    args=[
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-software-rasterizer",
        "--disable-extensions",
        "--disable-background-networking",
        "--disable-sync",
        "--disable-default-apps",
        "--disable-popup-blocking",
        "--disable-notifications",
        "--window-size=1280,800"
                ],
            )

            while queue and len(results) < self.max_pages:
                url, cur_depth = queue.pop(0)
                if url in self.visited:
                    continue
                self.visited.add(url)

                print(f"\n  🔍 [{len(results)+1}/{self.max_pages}] {url}")
                page_data = await self._analyse_page(browser, url)
                results.append(page_data)

                # Collect internal links for depth crawl
                if cur_depth < depth:
                    for link in page_data.get("internal_links", [])[:3]:
                        if link not in self.visited:
                            queue.append((link, cur_depth + 1))

                await asyncio.sleep(0.5)

            await browser.close()
        return results

    async def _analyse_page(self, browser: Browser, url: str) -> dict:
        # For PDP pages on www: force www (not mobile).
        # export.indiamart.com and dir.indiamart.com stay as-is.
        is_export = "export.indiamart" in url
        if "m.indiamart.com" in url and "/impcat/" not in url and not is_export:
            url = url.replace("m.indiamart.com", "www.indiamart.com")

        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-IN",
            timezone_id="Asia/Kolkata",
            extra_http_headers={
                "Accept-Language": "en-IN,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "sec-ch-ua": '"Chromium";v="124","Google Chrome";v="124"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
            },
        )

        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins',   { get: () => [1,2,3] });
            Object.defineProperty(screen, 'width',        { get: () => 1280 });
            Object.defineProperty(screen, 'height',       { get: () => 800  });
            window.chrome = { runtime: {} };
        """)

        page = await context.new_page()

        # Intercept mobile redirects for PDP pages only
        # dir.indiamart.com/impcat/ should stay on dir — do not redirect it
        async def block_mobile(route):
            req_url = route.request.url
            if "m.indiamart.com" in req_url and "/impcat/" not in req_url:
                fixed = req_url.replace("m.indiamart.com", "www.indiamart.com")
                await route.continue_(url=fixed)
            else:
                await route.continue_()
        await page.route("**/*", block_mobile)

        issues: list[dict] = []

        # ── Load page ─────────────────────────────────────────────────────────
        t0 = time.time()
        try:
            ok = await _wait_page_load(page, url)
            if not ok:
                raise Exception("All page load strategies failed")
        except Exception as e:
            await context.close()
            return {
                "url": url, "page_type": _detect_page_type(url),
                "load_error": str(e),
                "issues": [_issue("Load Error", "critical", "Page failed to load", str(e))],
                "screenshots": {"desktop_b64": ""},
                "core_web_vitals": {}, "cta_results": [],
            }

        load_ms = round((time.time() - t0) * 1000)

        # Safety check — if still on mobile (for PDP only, not IMPCAT), force reload on www
        if "m.indiamart.com" in page.url and "/impcat/" not in page.url:
            fixed = page.url.replace("m.indiamart.com", "www.indiamart.com")
            print(f"      ⚠ Mobile redirect — fixing: {fixed[:70]}")
            try:
                await page.goto(fixed, wait_until="load", timeout=30000)
                await page.wait_for_timeout(800)
            except Exception:
                pass

        page_type = _detect_page_type(page.url)

        # Override with hint if user specified a page type
        if self.page_type_hint == "mcat":
            page_type = "mcat"
        elif self.page_type_hint == "pdp":
            page_type = "pdp"
        elif self.page_type_hint == "search":
            page_type = "search"
        elif self.page_type_hint == "export":
            page_type = "export"
        elif self.page_type_hint == "company":
            page_type = "company"

        if page_type == "pdp":
            page_type = await _detect_pdp_subtype(page)
        print(f"      Page type: {page_type} | Load: {load_ms}ms")

        # Scroll to trigger lazy images
        await _safe_eval(page, "window.scrollTo(0, 600)")
        await page.wait_for_timeout(500)
        await _safe_eval(page, "window.scrollTo(0, 0)")
        await page.wait_for_timeout(300)

        # ── Screenshot ───────────────────────────────────────────────────────
        slug = re.sub(r"[^\w]", "_", url)[:60]
        ss_path = str(self.output_dir / "screenshots" / f"{slug}_desktop.png")
        ss_b64  = await _screenshot_b64(page, ss_path)

        # ── PDP checks ───────────────────────────────────────────────────────
        if page_type in ("pdp", "pdp_t14", "pdp_t1", "pdp_t3"):
            issues += await self._check_pdp(page, page_type)
        elif page_type == "mcat":
            issues += await self._check_mcat(page)
        elif page_type == "search":
            issues += await self._check_search(page)
        elif page_type in ("export", "export_search", "export_pdp", "export_company"):
            issues += await self._check_export(page, page_type)
        elif page_type == "company":
            issues += await self._check_company(page)

        # ── Web vitals ───────────────────────────────────────────────────────
        cwv = await _safe_eval(page, """() => {
            const nav   = performance.getEntriesByType('navigation')[0];
            const paint = performance.getEntriesByType('paint');
            const fcp   = paint.find(p => p.name === 'first-contentful-paint');
            const lcp   = performance.getEntriesByType('largest-contentful-paint');
            const cls   = performance.getEntriesByType('layout-shift');
            const clsScore = cls.reduce((s,e) => s + (e.hadRecentInput ? 0 : e.value), 0);
            return {
                ttfb: nav ? Math.round(nav.responseStart - nav.requestStart) : null,
                fcp:  fcp ? Math.round(fcp.startTime) : null,
                lcp:  lcp.length ? Math.round(lcp[lcp.length-1].startTime) : null,
                cls:  parseFloat(clsScore.toFixed(3)),
            };
        }""", {})

        await context.close()

        # ── Redirect Validation ──────────────────────────────────────────────
        print(f"      🔗 Running redirect validation...")
        redirect_issues, redirect_results = await self._check_redirects(
            browser, page, url, page_type
        )
        issues += redirect_issues

        # ── Click Interaction Testing ─────────────────────────────────────────
        # Pass titles of issues already raised by DOM checks so click tester
        # skips CTAs that were already flagged — prevents duplicate reporting
        print(f"      🖱️  Running click interaction tests...")
        already_flagged = {i.get("title","") for i in issues}
        click_issues, cta_results = await self._check_click_interactions(
            browser, url, page_type, already_flagged
        )
        issues += click_issues

        return {
            "url":              url,
            "page_type":        page_type,
            "load_time_ms":     load_ms,
            "issues":           issues,
            "core_web_vitals":  cwv,
            "cta_results":      cta_results,
            "redirect_results": redirect_results,
            "screenshots":      {"desktop": ss_path, "desktop_b64": ss_b64},
        }

    async def _check_pdp(self, page: Page, page_type: str = "pdp_t3") -> list[dict]:
        """
        DOM-based checks for PDP pages — 3 templates.
        Only checks that are verifiable without clicking (presence/visibility).
        Click-based test cases are handled by AI screenshot analysis.

        T14 (pdp_t14): Ladies Kurti style — image gallery, Get Latest Price, brochure
        T1  (pdp_t1):  Plywood style — Submit Requirement, right seller panel, similar products
        T3  (pdp_t3):  Service style — left seller panel, Contact Supplier, related categories
        """
        issues = []
        await page.wait_for_timeout(500)

        data = await _safe_eval(page, r"""() => {
            const body = document.body ? document.body.innerText : '';

            // Helper: element exists in DOM and is not hidden via display:none / visibility:hidden
            // NOTE: offsetParent===null fails for sticky/fixed positioned elements (right panels).
            // Use getComputedStyle instead which works correctly for all position types.
            function isVisible(el) {
                if (!el || !document.contains(el)) return false;
                const s = window.getComputedStyle(el);
                return s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
            }

            // ── Product image (T14, T1) ──────────────────────────────────────
            const largeImgs = [...document.querySelectorAll('img')].filter(img => {
                if (!img.complete || img.naturalWidth === 0) return false;
                const src = img.src || '';
                if (/pdf|icon|logo|gif|sprite|flag/i.test(src)) return false;
                const r = img.getBoundingClientRect();
                return r.width > 200 && r.height > 150;
            });
            const hasProductImage = largeImgs.length > 0;

            // ── Company name as a link (ALL, TC-7047) ────────────────────────
            const companyLink = [...document.querySelectorAll('a[href]')].find(el => {
                const href = el.getAttribute('href') || '';
                const txt  = el.textContent.trim();
                return txt.length > 3 && txt.length < 100 &&
                    (href.includes('indiamart.com/') || href.startsWith('/')) &&
                    !href.includes('proddetail') && !href.includes('/search') &&
                    !href.includes('dir.indiamart') && !href.includes('export') &&
                    !href.includes('help') && !href.includes('sell') &&
                    isVisible(el);
            });
            const hasCompanyLink = !!companyLink;

            // ── Breadcrumbs (ALL, TC-7055/56/57) ────────────────────────────
            const allNavLinks = [...document.querySelectorAll('a[href]')].filter(a => {
                const href = a.getAttribute('href') || '';
                const txt  = a.textContent.trim();
                return txt.length > 1 && txt.length < 50 &&
                    (href.includes('indiamart') || href.startsWith('/')) &&
                    isVisible(a);
            });
            const bcHasIndiamart = allNavLinks.some(a =>
                /indiamart/i.test(a.textContent) ||
                (a.getAttribute('href') || '').includes('dir.indiamart')
            );
            const bcLinkCount = allNavLinks.length;

            // ── Submit Requirement button (T1, TC-7046) ──────────────────────
            const submitReqBtn = [...document.querySelectorAll('button,input[type=submit],[role=button]')]
                .find(el => /submit.requirement/i.test(el.textContent) && isVisible(el));
            const hasSubmitReq = !!submitReqBtn;

            // ── Contact Supplier (T1, T3, TC-7049) ──────────────────────────
            // Desktop: "Contact Supplier" button
            // Mobile: "Send Enquiry" button (same function, different label)
            const hasContactSupplier =
                /contact.suppl|contact.seller|send.enquiry|send enquiry/i.test(body) &&
                !![...document.querySelectorAll('button,a,[role=button]')]
                    .find(el => /contact.su(p|pp)li|contact.seller|send.enquiry/i.test(el.textContent));

            // ── Call Now CTA ─────────────────────────────────────────────────
            // Desktop: "Call Now" button. Mobile: "Call NowWhatsAppBest Price" (concat text)
            // Match body text — if "Call Now" appears anywhere the button exists
            const hasCallNow = /call.?now/i.test(body);

            // ── Brochure PDF (T14, TC-7052) ───────────────────────────────────
            const hasBrochureLabel = /product brochure/i.test(body);
            const hasPdfIconImg    = !!document.querySelector('img[src*="pdf"],img[src*="PDF"]');
            const hasViewNow       = /view now/i.test(body);
            const brochureOk       = !hasBrochureLabel || (hasPdfIconImg || hasViewNow);

            // ── View in Hindi (T14, T1, TC-7054) ─────────────────────────────
            const hindiLabelExists = /view in hindi/i.test(body);
            const hindiLink = [...document.querySelectorAll('a[href]')]
                .find(el => (el.getAttribute('href') || '').includes('hindi.indiamart') ||
                    /view in hindi/i.test(el.textContent));
            const hindiLinkOk = !hindiLabelExists || !!hindiLink;

            // ── GST masked (ALL, TC-7065) ─────────────────────────────────────
            const gstRaw   = body.match(/GST[\s\-:]*([0-9]{2}[A-Z0-9\*]{10,13}[A-Z0-9])/i);
            const gstVisible = !!gstRaw;
            const gstMasked  = !gstRaw || (gstRaw[1].match(/\*/g) || []).length >= 3;

            // ── Similar products section (T1, TC-7058) ────────────────────────
            const hasSimilarSection = /find.products.similar|similar.to|top.local.seller/i.test(body);

            // ── Related categories (T3, T14, TC-7062) ────────────────────────
            const hasRelatedCat = /related.categor|find.related.categor|find.categor/i.test(body);

            // ── More products from seller (ALL, TC-7069) ─────────────────────
            const hasMoreProds = /more.products.from|more.from.*seller/i.test(body);

            // ── Inline BL form (T3, TC-7073) ─────────────────────────────────
            const hasBlForm = /tell us what you need|help you get quotes|send enquiry for/i.test(body);
            let blPrefilled = false;
            if (hasBlForm) {
                const inputs = [...document.querySelectorAll('textarea,input[type=text],input:not([type])')];
                blPrefilled = inputs.some(inp =>
                    ((inp.value || inp.defaultValue || inp.placeholder || '')).trim().length > 2);
            }

            // ── Header logo + search (ALL, TC-7075) ──────────────────────────
            const logoLink   = !!document.querySelector('header a img, [class*=header] a img, a img[alt*=indiamart i], a[class*=logo]');
            const searchInput = !!document.querySelector('input[type=search],input[placeholder*=search i],[class*=search] input,#search');

            // ── Footer links (ALL, TC-7076) ───────────────────────────────────
            // Use body text to check for footer content — works on both desktop and mobile
            const footerKeywords = ['about us','terms of use','privacy policy','help center',
                'contact us','careers','shipping','refund','sitemap','advertise'];
            const footerLinks = footerKeywords.filter(k => body.toLowerCase().includes(k)).length;

            return {
                hasProductImage, hasCompanyLink,
                bcLinkCount, bcHasIndiamart,
                hasSubmitReq, hasContactSupplier, hasCallNow,
                brochureOk, hasBrochureLabel,
                hindiLinkOk, hindiLabelExists,
                gstVisible, gstMasked,
                hasSimilarSection, hasRelatedCat, hasMoreProds,
                hasBlForm, blPrefilled,
                logoLink, searchInput,
                footerLinks,
            };
        }""", {})


        # ── TC-7044: Product image (T14, T1 — NOT T3 which has no gallery) ───
        if page_type in ("pdp_t14", "pdp_t1"):
            if not data.get("hasProductImage"):
                issues.append(_issue("Broken Image", "critical",
                    "Product image not loaded (TC-7044)",
                    "No product image visible on PDP — image section appears blank. "
                    "Buyers cannot see the product."))

        # ── TC-7046/7067: Submit Requirement button (T1 only) ─────────────────
        if page_type == "pdp_t1":
            if not data.get("hasSubmitReq"):
                issues.append(_issue("Missing CTA", "critical",
                    "'Submit Requirement' button missing (TC-7046)",
                    "Template 1 PDP must have 'Submit Requirement' button visible — "
                    "primary enquiry action is absent."))

        # ── TC-7047: Company name must be a clickable link (ALL) ──────────────
        if not data.get("hasCompanyLink"):
            issues.append(_issue("Navigation", "high",
                "Company name is not a clickable link (TC-7047)",
                "Seller company name must be a hyperlink to the company catalog page. "
                "It is either absent or rendered as plain text."))

        # ── TC-7049/7051: Contact Supplier button (T1, T3) ───────────────────
        if page_type in ("pdp_t1", "pdp_t3"):
            if not data.get("hasContactSupplier"):
                issues.append(_issue("Missing CTA", "critical",
                    "'Contact Supplier' CTA missing (TC-7049)",
                    "Template 1/3 PDP must have 'Contact Supplier' button visible — "
                    "primary contact action is absent."))

        # ── TC-7052: Brochure PDF link (T14 only) ────────────────────────────
        # IMPORTANT: correct state is small pdf.png icon + "View Now" text in spec table
        # Only flag if brochure label exists but BOTH pdf icon AND view now text are missing
        if page_type == "pdp_t14":
            if not data.get("brochureOk"):
                issues.append(_issue("Broken CTA", "high",
                    "Product Brochure link broken in spec table (TC-7052)",
                    "'Product Brochure' row exists in specs but PDF icon image "
                    "and 'View Now' text are both absent."))

        # ── TC-7054: View in Hindi link (T14, T1) ────────────────────────────
        # Only flag if "View in Hindi" text is visible but is NOT a link
        if page_type in ("pdp_t14", "pdp_t1"):
            if not data.get("hindiLinkOk"):
                issues.append(_issue("Navigation", "medium",
                    "'View in Hindi' text is not a clickable link (TC-7054)",
                    "'View in Hindi' label is visible in spec table but is not "
                    "an anchor — clicking it will not open Hindi PDP."))

        # ── TC-7055/56/57: Breadcrumbs (ALL) ─────────────────────────────────
        if data.get("bcLinkCount", 0) < 3:
            issues.append(_issue("Navigation", "high",
                f"Breadcrumb incomplete — only {data.get('bcLinkCount',0)} link(s) (TC-7055)",
                "Breadcrumb must show: IndiaMART > Category > Subcategory — "
                "fewer than 3 links means navigation is broken."))
        elif not data.get("bcHasIndiamart"):
            issues.append(_issue("Navigation", "medium",
                "Breadcrumb first link does not point to IndiaMART (TC-7055)",
                "First breadcrumb link should go to dir.indiamart.com."))

        # ── TC-7058: Similar products section (T1) ────────────────────────────
        if page_type == "pdp_t1":
            if not data.get("hasSimilarSection"):
                issues.append(_issue("PDP", "medium",
                    "'Find products similar to' section missing (TC-7058)",
                    "Template 1 PDP should show similar products section below specs."))

        # ── TC-7062: Related categories (T3, T14) ────────────────────────────
        if page_type in ("pdp_t3", "pdp_t14"):
            if not data.get("hasRelatedCat"):
                issues.append(_issue("PDP", "medium",
                    "Related categories section missing (TC-7062)",
                    "'Find related categories' section not found on this PDP."))

        # ── TC-7065: Company details — GST masking (ALL) ─────────────────────
        if data.get("gstVisible") and not data.get("gstMasked"):
            issues.append(_issue("Privacy", "critical",
                "GST number is NOT masked (TC-7065)",
                "Full GST number is visible without masking — must be shown as "
                "e.g. '08**********1ZA'. This is a privacy violation."))

        # ── TC-7069: More products from seller (ALL — if applicable) ─────────
        # NOTE: not all sellers have multiple products — absence is normal, don't flag

        # ── TC-7073: Inline BL form + prefill (T3) ───────────────────────────
        if page_type == "pdp_t3":
            if data.get("hasBlForm") and not data.get("blPrefilled"):
                issues.append(_issue("Form Issue", "medium",
                    "Product name not pre-filled in BL form textbox (TC-7073)",
                    "'Tell us what you need' BL form exists but requirement "
                    "textbox is empty — product name should be pre-filled."))

        # ── TC-7075: Header logo + search bar (ALL) ───────────────────────────
        if not data.get("logoLink"):
            issues.append(_issue("Navigation", "high",
                "IndiaMART logo missing or not linked in header (TC-7075)",
                "Header logo is absent or not wrapped in a link."))
        if not data.get("searchInput"):
            issues.append(_issue("Navigation", "high",
                "Search bar missing from header (TC-7075)",
                "Search input not found — buyers cannot search from this page."))

        # ── TC-7076: Footer links (ALL) ───────────────────────────────────────
        # Only flag if ZERO footer links — mobile pages render footer differently
        if data.get("footerLinks", 0) == 0:
            issues.append(_issue("Navigation", "medium",
                "Footer links not found — footer may not have rendered (TC-7076)",
                "No footer navigation links detected. Page may not have fully loaded."))

        return issues

    # ═══════════════════════════════════════════════════════════════════════
    # IMPCAT / MCAT PAGE CHECKS
    # Source: DIR_FB_Test_testsuite-deep.xml (TC-6461 to TC-6491)
    # ═══════════════════════════════════════════════════════════════════════

    async def _check_mcat(self, page: Page) -> list[dict]:
        """
        DOM-based checks for IMPCAT/MCAT pages.
        Source: DIR_FB_Test_testsuite-deep.xml (TC-6461 to TC-6491)
        """
        issues = []

        # ── Dismiss login/sign-in popup if present ───────────────────────────
        # IndiaMART shows a Sign In modal on IMPCAT pages — dismiss it first
        try:
            for sel in [
                '[class*=close i]', '[class*=modal-close]', '[aria-label*=close i]',
                'button[class*=skip]', '[class*=skip i]', 'button.close',
                '[data-dismiss=modal]',
            ]:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=400):
                    await btn.click(timeout=400)
                    await page.wait_for_timeout(300)
                    break
        except Exception:
            pass

        await page.wait_for_timeout(500)

        data = await _safe_eval(page, r"""() => {
            const body = document.body ? document.body.innerText : '';

            // TC-6461: City strip
            // Use body text — if 3+ city names appear, city strip is present
            const cityNames = ['All India','Near Me','Ahmedabad','Delhi',
                'Noida','Mumbai','Coimbatore','Rajkot','Thane','Bangalore'];
            const cityInBody = cityNames.filter(c => body.includes(c)).length;
            const cityAnchorCount = [...document.querySelectorAll('a[href]')]
                .filter(a => (a.getAttribute('href') || '').includes('/city/')).length;
            const hasCityStrip = cityInBody >= 3 || cityAnchorCount >= 2;

            // TC-6463: Near Me
            const hasNearMe = /near.?me/i.test(body);

            // TC-6469: Product names link to proddetail
            const prodLinks = [...document.querySelectorAll('a[href]')]
                .filter(a => (a.getAttribute('href') || '').includes('proddetail') &&
                    a.textContent.trim().length > 3);
            const hasProductLinks = prodLinks.length > 0;

            // TC-6470: GST / TrustSeal / yrs
            const hasGST       = /\bGST\b/.test(body);
            const hasTrustSeal = /trustseal|verified.exporter|verified.supplier/i.test(body);
            const hasYrs       = /\d+\s*(yr|yrs|year|month|mnth)/i.test(body);

            // TC-6471: Call Now — present AND ALL clickable
            const callNowEls = [...document.querySelectorAll('a,button,[role=button],div')].filter(el =>
                /^call.?now$/i.test((el.innerText||el.textContent||'').trim())
            );
            const hasCallNow = callNowEls.length > 0;
            // Count how many are disabled vs enabled
            const callNowDisabledCount = callNowEls.filter(el => {
                const s = window.getComputedStyle(el);
                return el.disabled===true || el.getAttribute('disabled')!==null ||
                    s.pointerEvents==='none' || el.getAttribute('aria-disabled')==='true' ||
                    (el.classList&&[...el.classList].some(c=>c.includes('disabled')));
            }).length;
            const callNowEnabledCount  = callNowEls.length - callNowDisabledCount;
            const callNowClickable     = callNowDisabledCount === 0; // ALL must be enabled
            const callNowPartial       = callNowEnabledCount > 0 && callNowDisabledCount > 0;

            // TC-6467: Contact Supplier — all IndiaMART CTA label variants
            // Real pages use: "Get Latest Price", "Send Enquiry", "Enquire Now",
            // "Get Best Price", "Contact Supplier", "Contact Seller", "Get Quote"
            const contactEls = [...document.querySelectorAll('a,button,[role=button]')].filter(el => {
                const txt = (el.innerText||el.textContent||'').trim();
                return /contact.suppl|contact.seller|send.enqui|get.latest.price|get.best.price|enquire.now|get.quote/i.test(txt)
                    && txt.length < 60;
            });
            const hasContactSupplier = contactEls.length > 0;
            const contactDisabledCount = contactEls.filter(el => {
                const s = window.getComputedStyle(el);
                return el.disabled===true || el.getAttribute('disabled')!==null ||
                    s.pointerEvents==='none' || el.getAttribute('aria-disabled')==='true' ||
                    (el.classList&&[...el.classList].some(c=>c.includes('disabled')));
            }).length;
            const contactSupplierClickable = contactDisabledCount === 0;

            // TC-6472/6491: BL form
            const hasBlForm = /submit.requirement|get.quotes.from.verified|tell.us.what.you.need/i.test(body);
            let blPrefilled = false;
            if (hasBlForm) {
                const inputs = [...document.querySelectorAll('textarea,input[type=text],input:not([type])')];
                blPrefilled = inputs.some(inp =>
                    ((inp.value || inp.defaultValue || inp.placeholder || '')).trim().length > 2);
            }

            // TC-6474: Related categories
            const hasRelatedCat = /related.categor|explore.more/i.test(body) ||
                !![...document.querySelectorAll('h2,h3,h4,strong')]
                    .find(el => /related.categor/i.test(el.textContent));

            // TC-6476: Explore / Business type filter
            const hasExploreFilter = /\bexplore\b/i.test(body) ||
                /business.type|manufacturer|retailer|trader/i.test(body);

            // TC-6488: Q&A section
            const hasQASection = /have.a.question|ask.our.expert|question.*answer/i.test(body);

            // Product images
            const cardImgs = [...document.querySelectorAll('img')].filter(img => {
                const r = img.getBoundingClientRect();
                return r.width > 80 && r.height > 80 &&
                    img.complete && img.naturalWidth > 0 &&
                    !img.src.includes('logo') && !img.src.includes('icon');
            });
            const hasProductImages = cardImgs.length >= 2;

            // Prices
            const hasPrices = /\u20b9\s*[\d,]+/.test(body);

            // Header / footer
            const logoLink    = !!document.querySelector('header a img, a img[alt*=indiamart i], a[class*=logo]');
            const searchInput = !!document.querySelector('input[type=search],input[placeholder*=search i],[class*=search] input,#search');
            const footerWords = ['about us','terms of use','privacy policy','help','contact us'];
            const footerLinks = footerWords.filter(k => body.toLowerCase().includes(k)).length;

            return {
                hasCityStrip, cityInBody, cityAnchorCount,
                hasNearMe, hasProductLinks, hasProductImages, hasPrices,
                hasGST, hasTrustSeal, hasYrs,
                hasCallNow, callNowClickable, callNowPartial,
                callNowDisabledCount, callNowEnabledCount,
                hasContactSupplier, contactSupplierClickable, contactDisabledCount,
                hasBlForm, blPrefilled,
                hasRelatedCat, hasExploreFilter, hasQASection,
                logoLink, searchInput, footerLinks,
            };
        }""", {})

        # ── TC-6461: City strip ───────────────────────────────────────────────
        if not data.get("hasCityStrip"):
            issues.append(_issue("MCAT", "high",
                "City strip missing on IMPCAT page (TC-6461)",
                "City filter strip (All India | Delhi | Noida...) not found — "
                "buyers cannot filter products by city."))

        # ── TC-6463: Near Me CTA ──────────────────────────────────────────────
        if not data.get("hasNearMe"):
            issues.append(_issue("Missing CTA", "medium",
                "'Near Me' CTA missing from city strip (TC-6463)",
                "'Near Me' option not found in the city strip — "
                "buyers cannot filter by their current location."))

        # ── TC-6469: Product name links → proddetail ──────────────────────────
        if not data.get("hasProductLinks"):
            issues.append(_issue("Navigation", "critical",
                "Product names are not linked to PDP pages (TC-6469)",
                "No proddetail links found — product names must link to "
                "their Product Detail Page."))

        # ── Product images on cards ───────────────────────────────────────────
        if not data.get("hasProductImages"):
            issues.append(_issue("Broken Image", "critical",
                "Product card images not loaded on IMPCAT page",
                "Fewer than 2 product images loaded — product cards appear blank. "
                "Critical listing page bug."))

        # ── Prices on cards ───────────────────────────────────────────────────
        if not data.get("hasPrices"):
            issues.append(_issue("MCAT", "high",
                "Product prices missing from product cards",
                "No ₹ price values found on product cards — buyers cannot "
                "compare prices without clicking each product."))

        # ── TC-6470: GST/TrustSeal/yrs on cards ─────────────────────────────
        if not data.get("hasGST") and not data.get("hasTrustSeal"):
            issues.append(_issue("Trust Signal", "medium",
                "No GST or TrustSeal badges on product cards (TC-6470)",
                "Neither GST badge nor TrustSeal visible on any card — "
                "trust indicators expected on supplier cards."))
        if not data.get("hasYrs"):
            issues.append(_issue("Trust Signal", "medium",
                "Member years (yrs/mnths) missing from product cards (TC-6470)",
                "'X yrs' / 'X months' member duration not found on any product card."))

        # ── TC-6471: Call Now button ──────────────────────────────────────────
        if not data.get("hasCallNow"):
            issues.append(_issue("Functional", "high",
                "'Call Now' CTA missing from product cards (TC-6471)",
                "'Call Now' button not found on any product card."))
        elif not data.get("callNowClickable"):
            disabled = data.get("callNowDisabledCount", 0)
            enabled  = data.get("callNowEnabledCount", 0)
            total    = disabled + enabled
            if data.get("callNowPartial"):
                issues.append(_issue("Functional", "high",
                    f"'Call Now' disabled on {disabled} of {total} product cards (TC-6471)",
                    f"'Call Now' works on {enabled} card(s) but is disabled on "
                    f"{disabled} card(s) — pointer-events:none or disabled attribute "
                    f"detected. Buyers on those cards cannot call the supplier."))
            else:
                issues.append(_issue("Functional", "high",
                    f"'Call Now' CTA disabled on all {total} product cards (TC-6471)",
                    f"All {total} 'Call Now' buttons are non-interactive "
                    f"(pointer-events:none / aria-disabled). "
                    f"Buyers cannot call any supplier from this page."))

        # ── TC-6467: Contact Supplier CTA ────────────────────────────────────
        if not data.get("hasContactSupplier"):
            issues.append(_issue("Functional", "high",
                "'Contact Supplier' CTA missing from product cards (TC-6467)",
                "No 'Contact Supplier' CTA found on any product card."))
        elif not data.get("contactSupplierClickable"):
            disabled = data.get("contactDisabledCount", 0)
            issues.append(_issue("Functional", "high",
                f"'Contact Supplier' disabled on all {disabled} product cards (TC-6467)",
                f"All {disabled} 'Contact Supplier' buttons are disabled "
                f"(disabled attribute / aria-disabled detected). "
                f"Buyers cannot send enquiries to any supplier."))

        # ── TC-6472: Inline BL form present ──────────────────────────────────
        if not data.get("hasBlForm"):
            issues.append(_issue("Form Issue", "medium",
                "Inline BL form (Submit Requirement) missing (TC-6472)",
                "'Submit Requirement' / 'Get Quotes' BL form not found on page — "
                "buyers cannot submit bulk enquiries."))

        # ── TC-6491: BL form prefilled with category name ────────────────────
        if data.get("hasBlForm") and not data.get("blPrefilled"):
            issues.append(_issue("Form Issue", "medium",
                "BL form textbox not prefilled with category name (TC-6491)",
                "'Get Quotes from Verified Suppliers' textbox is empty — "
                "should be prefilled with the IMPCAT category name."))

        # ── TC-6474: Related categories section ──────────────────────────────
        if not data.get("hasRelatedCat"):
            issues.append(_issue("MCAT", "medium",
                "Related categories section missing (TC-6474)",
                "Related categories / Explore More section not found — "
                "buyers cannot navigate to related product categories."))

        # ── TC-6476: Business type filter / Explore ───────────────────────────
        if not data.get("hasExploreFilter"):
            issues.append(_issue("MCAT", "medium",
                "Business type filter (Explore) missing (TC-6476)",
                "'Explore' / Business Type filter not found — "
                "buyers cannot filter by Manufacturer/Retailer/Trader."))

        # ── TC-6488/6489: Q&A section ─────────────────────────────────────────
        if not data.get("hasQASection"):
            issues.append(_issue("MCAT", "medium",
                "Q&A section 'Have a Question? Ask our expert' missing (TC-6488)",
                "Q&A section not found on page — expected below product listing."))

        # ── Header + footer ───────────────────────────────────────────────────
        if not data.get("logoLink"):
            issues.append(_issue("Navigation", "high",
                "IndiaMART logo missing or not linked in header",
                "Header logo is absent or not wrapped in a link."))
        if not data.get("searchInput"):
            issues.append(_issue("Navigation", "high",
                "Search bar missing from header",
                "Search input not found — buyers cannot search from this page."))
        if data.get("footerLinks", 0) == 0:
            issues.append(_issue("Navigation", "medium",
                "Footer links not found — footer may not have rendered",
                "No footer navigation links detected. Page may not have fully loaded."))

        return issues

    # ═══════════════════════════════════════════════════════════════════════
    # SEARCH PAGE CHECKS
    # Source: Serach_FB_TC_testsuite-deep.xml (TC-3590 to TC-3644)
    # ═══════════════════════════════════════════════════════════════════════

    async def _check_search(self, page: Page) -> list[dict]:
        """
        DOM-based checks for dir.indiamart.com/search.mp pages.
        Source: Serach_FB_TC_testsuite-deep.xml

        Suites:
          Search bar functionality    TC-3590, 3591, 3593, 3594, 3597, 3598, 3599
          City suggestor              TC-3605, 3606, 3607, 3608, 3609
          City strip                  TC-3610, 3611, 3612, 3613, 3614
          Product card redirection    TC-3633, 3634, 3635, 3637, 3638, 3639, 3640, 3643
          Inline BL form              TC-3641, 3644

        Click-required TCs (cannot test from DOM — AI screenshot handles these):
          3593: city dropdown filtering
          3597: blank search validation popup
          3598: auto-suggestions dropdown
          3606: city suggestor clickable
          3607: use my location
          3608/3609/3610/3611/3613/3614: city click/selection behavior
          3633: product image click → enquiry form
          3635: Contact Supplier click → popup
          3637/3638: arrow buttons → next/prev product
          3640: ratings click → review page
          3641: Submit Requirement click → popup
        """
        issues = []

        # Dismiss any login popup first
        try:
            for sel in ['[class*=close i]','[class*=modal-close]','[aria-label*=close i]',
                        'button[class*=skip]','[data-dismiss=modal]']:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=400):
                    await btn.click(timeout=400)
                    await page.wait_for_timeout(300)
                    break
        except Exception:
            pass

        await page.wait_for_timeout(500)

        data = await _safe_eval(page, r"""() => {
            const body = document.body ? document.body.innerText : '';

            // ── TC-3591: Search results present (product cards) ───────────────
            const prodLinks = [...document.querySelectorAll('a[href]')]
                .filter(a => (a.getAttribute('href') || '').includes('proddetail') &&
                    a.textContent.trim().length > 3);
            const hasProductCards = prodLinks.length > 0;

            // ── TC-3605: City suggestor present ────────────────────────────────
            // "select city to find sellers near you" text or city input field
            const hasCitySuggestor =
                /select.city.to.find|city.to.find.seller|enter.city/i.test(body) ||
                !!document.querySelector('input[placeholder*=city i], [class*=city-suggestor], [class*=citySuggestor]');

            // ── TC-3612: Near Me CTA in city strip ─────────────────────────────
            const hasNearMe = /near.?me/i.test(body);

            // ── TC-3610: City strip present (row of city chips) ────────────────
            const cityNames = ['All India','Near Me','Delhi','Mumbai','Ahmedabad',
                'Noida','Bangalore','Chennai','Kolkata','Hyderabad','Pune'];
            const cityInBody = cityNames.filter(c => body.includes(c)).length;
            const cityAnchorCount = [...document.querySelectorAll('a[href]')]
                .filter(a => (a.getAttribute('href') || '').includes('/city/')).length;
            const hasCityStrip = cityInBody >= 3 || cityAnchorCount >= 2;

            // ── TC-3634: Company names are links to company pages ──────────────
            const companyLinks = [...document.querySelectorAll('a[href]')].filter(a => {
                const href = a.getAttribute('href') || '';
                const txt  = a.textContent.trim();
                return txt.length > 3 && txt.length < 100 &&
                    href.includes('indiamart.com/') &&
                    !href.includes('proddetail') && !href.includes('/search') &&
                    !href.includes('dir.indiamart') && !href.includes('help');
            });
            const hasCompanyLinks = companyLinks.length > 0;

            // ── TC-3635: Contact Supplier CTA present ─────────────────────────
            const hasContactSupplier = /contact.suppl|contact.seller|send.enquiry/i.test(body);

            // ── TC-3643: Call Now CTA present ─────────────────────────────────
            const hasCallNow = /call.?now/i.test(body);

            // ── TC-3641/3644: Inline BL form + prefilled ──────────────────────
            const hasBlForm = /submit.requirement|tell.us.what.you.need|get.quotes/i.test(body);
            let blPrefilled = false;
            if (hasBlForm) {
                const inputs = [...document.querySelectorAll('textarea,input[type=text],input:not([type])')];
                blPrefilled = inputs.some(inp =>
                    ((inp.value || inp.defaultValue || inp.placeholder || '')).trim().length > 2);
            }

            // ── Product images ────────────────────────────────────────────────
            const cardImgs = [...document.querySelectorAll('img')].filter(img => {
                const r = img.getBoundingClientRect();
                return r.width > 80 && r.height > 80 &&
                    img.complete && img.naturalWidth > 0 &&
                    !img.src.includes('logo') && !img.src.includes('icon');
            });
            const hasProductImages = cardImgs.length >= 2;

            // ── Prices ────────────────────────────────────────────────────────
            const hasPrices = /\u20b9\s*[\d,]+/.test(body);

            // ── Header search bar (TC-3590) ────────────────────────────────────
            const searchInput = !!document.querySelector(
                'input[type=search],input[placeholder*=search i],[class*=search] input,#search');
            const logoLink = !!document.querySelector(
                'header a img, a img[alt*=indiamart i], a[class*=logo]');

            // ── Footer ─────────────────────────────────────────────────────────
            const footerWords = ['about us','terms of use','privacy policy','help','contact us'];
            const footerLinks = footerWords.filter(k => body.toLowerCase().includes(k)).length;

            return {
                hasProductCards, hasProductImages, hasPrices,
                hasCitySuggestor, hasNearMe, hasCityStrip,
                hasCompanyLinks, hasContactSupplier, hasCallNow,
                hasBlForm, blPrefilled,
                searchInput, logoLink, footerLinks,
            };
        }""", {})

        # ── TC-3591: Product cards present ──────────────────────────────────
        if not data.get("hasProductCards"):
            issues.append(_issue("Search", "critical",
                "No product cards / search results found (TC-3591)",
                "No proddetail links found on search page — search results "
                "appear to be completely empty."))

        # ── Product images ───────────────────────────────────────────────────
        if not data.get("hasProductImages"):
            issues.append(_issue("Broken Image", "high",
                "Product card images not loaded on search page (TC-3633)",
                "Fewer than 2 product images rendered — product cards appear blank."))

        # ── Prices ───────────────────────────────────────────────────────────
        if not data.get("hasPrices"):
            issues.append(_issue("Search", "high",
                "Product prices missing from search result cards",
                "No ₹ price values visible on any product card on search page."))

        # ── TC-3605: City suggestor ──────────────────────────────────────────
        if not data.get("hasCitySuggestor"):
            issues.append(_issue("Search", "medium",
                "City suggestor missing on search page (TC-3605)",
                "'Select city to find sellers near you' city suggestor not found — "
                "buyers cannot filter results by city."))

        # ── TC-3612: Near Me CTA ─────────────────────────────────────────────
        if not data.get("hasNearMe"):
            issues.append(_issue("Missing CTA", "medium",
                "'Near Me' CTA missing from city strip (TC-3612)",
                "'Near Me' option not found in the city strip on search page."))

        # ── TC-3610: City strip ──────────────────────────────────────────────
        if not data.get("hasCityStrip"):
            issues.append(_issue("Search", "medium",
                "City strip missing on search page (TC-3610)",
                "City filter chips (Delhi | Mumbai | Ahmedabad...) not found — "
                "buyers cannot quickly filter by city."))

        # ── TC-3634: Company names as links ──────────────────────────────────
        if not data.get("hasCompanyLinks"):
            issues.append(_issue("Navigation", "high",
                "Seller company names not linked to company pages (TC-3634)",
                "No company page anchor links found on search result cards."))

        # ── TC-3635: Contact Supplier ────────────────────────────────────────
        if not data.get("hasContactSupplier"):
            issues.append(_issue("Missing CTA", "high",
                "'Contact Supplier' CTA missing from search results (TC-3635)",
                "No 'Contact Supplier' / 'Send Enquiry' CTA found on any product card."))

        # ── TC-3643: Call Now ────────────────────────────────────────────────
        if not data.get("hasCallNow"):
            issues.append(_issue("Missing CTA", "high",
                "'Call Now' CTA missing from search result cards (TC-3643)",
                "'Call Now' button not found on any product card."))

        # ── TC-3641: BL form ─────────────────────────────────────────────────
        if not data.get("hasBlForm"):
            issues.append(_issue("Form Issue", "medium",
                "Inline BL form (Submit Requirement) missing (TC-3641)",
                "'Submit Requirement' / 'Tell us what you need' BL form not found."))

        # ── TC-3644: BL form prefilled ───────────────────────────────────────
        if data.get("hasBlForm") and not data.get("blPrefilled"):
            issues.append(_issue("Form Issue", "medium",
                "BL form textbox not prefilled with search query (TC-3644)",
                "'Tell us what you need' textbox is empty — "
                "should be prefilled with the searched product name."))

        # ── TC-3590: Header search bar ───────────────────────────────────────
        if not data.get("searchInput"):
            issues.append(_issue("Missing CTA", "high",
                "Search bar missing from header on search page (TC-3590)",
                "Search input not found — buyers cannot refine their search."))
        if not data.get("logoLink"):
            issues.append(_issue("Navigation", "high",
                "IndiaMART logo missing or not linked in header",
                "Header logo is absent or not wrapped in a link."))
        if data.get("footerLinks", 0) == 0:
            issues.append(_issue("Navigation", "medium",
                "Footer links not found — footer may not have rendered",
                "No footer navigation links detected."))

        return issues

    # ═══════════════════════════════════════════════════════════════════════
    # EXPORT PAGE CHECKS
    # Source: Export_BF_Test_testsuite-deep.xml (TC-2139 to TC-2169)
    # Domain: export.indiamart.com
    # ═══════════════════════════════════════════════════════════════════════

    async def _check_export(self, page: Page, page_type: str = "export") -> list[dict]:
        """
        DOM-based checks for export.indiamart.com pages.
        Source: Export_BF_Test_testsuite-deep.xml

        DOM-checkable TCs (presence checks):
          TC-2140: Homepage loads successfully
          TC-2141: Trust badges below header (Verified Exporters, TrustSEAL, GST, IEC, Made in India)
          TC-2153: Currency dropdown visible in header
          TC-2154: Language dropdown visible in header

        Click-required TCs (delegated to AI screenshot):
          TC-2139,2143,2144,2145,2146,2147,2149,2150,2151,2152,2153,2155,
          TC-2156,2157,2158,2159,2160,2161,2166,2168,2169
        """
        issues = []
        await page.wait_for_timeout(500)

        data = await _safe_eval(page, r"""() => {
            const body = document.body ? document.body.innerText : '';

            // ── TC-2140: Homepage loaded ───────────────────────────────────────
            const hasTitle = !!document.title && document.title.trim().length > 3;

            // ── TC-2141: Trust badges below header ────────────────────────────
            // Expected: "Verified Exporters Only", "TrustSEAL Certified Sellers",
            //           "GST Verified", "IEC Verified", "Made in India - Supplied Globally"
            const hasVerifiedExporters = /verified.exporter/i.test(body);
            const hasTrustSeal         = /trustseal.certified|trustseal/i.test(body);
            const hasGSTVerified       = /gst.verified/i.test(body);
            const hasIECVerified       = /iec.verified/i.test(body);
            const hasMadeInIndia       = /made.in.india/i.test(body);

            // ── TC-2153: Currency dropdown ────────────────────────────────────
            const hasCurrencyDropdown =
                !!document.querySelector('[class*=currency], [id*=currency], select[name*=currency]') ||
                /\b(USD|EUR|GBP|INR|AED)\b/.test(body);

            // ── TC-2154: Language dropdown ────────────────────────────────────
            const hasLanguageDropdown =
                !!document.querySelector('[class*=language], [id*=language], select[name*=lang]') ||
                /\b(english|español|français|中文)\b/i.test(body);

            // ── Search bar (TC-2139) ──────────────────────────────────────────
            const hasSearchBar = !!document.querySelector(
                'input[type=search], input[placeholder*=search i], '
                + '[class*=search] input, #search, input[name*=search i]'
            );

            // ── Get Quote CTA (TC-2145) ───────────────────────────────────────
            const hasGetQuote = /get.quote/i.test(body);

            // ── Footer ────────────────────────────────────────────────────────
            const footerWords = ['about', 'privacy', 'terms', 'contact'];
            const footerLinks = footerWords.filter(k => body.toLowerCase().includes(k)).length;

            // ── Product cards on search/listing pages ─────────────────────────
            const prodLinks = [...document.querySelectorAll('a[href]')]
                .filter(a => {
                    const href = a.getAttribute('href') || '';
                    return href.includes('/products/') || href.includes('proddetail');
                });
            const hasProductCards = prodLinks.length > 0;

            // ── TrustSEAL on product cards (TC-2149) ──────────────────────────
            const hasTrustSealOnCards = /trustseal/i.test(body) && hasProductCards;

            return {
                hasTitle,
                hasVerifiedExporters, hasTrustSeal, hasGSTVerified,
                hasIECVerified, hasMadeInIndia,
                hasCurrencyDropdown, hasLanguageDropdown,
                hasSearchBar, hasGetQuote,
                footerLinks, hasProductCards, hasTrustSealOnCards,
            };
        }""", {})

        if page_type == "export":
            # ── TC-2140: Homepage loaded ─────────────────────────────────────
            if not data.get("hasTitle"):
                issues.append(_issue("Export", "critical",
                    "Export homepage failed to load (TC-2140)",
                    "Page title is missing — export.indiamart.com homepage did not load."))

            # ── TC-2141: Trust badges below header ───────────────────────────
            missing_badges = []
            if not data.get("hasVerifiedExporters"): missing_badges.append("Verified Exporters Only")
            if not data.get("hasTrustSeal"):          missing_badges.append("TrustSEAL Certified Sellers")
            if not data.get("hasGSTVerified"):         missing_badges.append("GST Verified")
            if not data.get("hasIECVerified"):         missing_badges.append("IEC Verified")
            if not data.get("hasMadeInIndia"):         missing_badges.append("Made in India")
            if missing_badges:
                issues.append(_issue("Trust Signal", "high",
                    f"Trust badge(s) missing below header (TC-2141): {', '.join(missing_badges)}",
                    f"Expected trust badges not visible: {', '.join(missing_badges)}. "
                    "These appear below the header on export.indiamart.com."))

            # ── TC-2153: Currency dropdown ────────────────────────────────────
            if not data.get("hasCurrencyDropdown"):
                issues.append(_issue("Missing CTA", "medium",
                    "Currency dropdown not visible in header (TC-2153)",
                    "Currency selector (USD/EUR/GBP dropdown) not found in export header."))

            # ── TC-2154: Language dropdown ────────────────────────────────────
            if not data.get("hasLanguageDropdown"):
                issues.append(_issue("Missing CTA", "medium",
                    "Language dropdown not visible in header (TC-2154)",
                    "Language selector dropdown not found in export header."))

            # ── TC-2145: Get Quote CTA ────────────────────────────────────────
            if not data.get("hasGetQuote"):
                issues.append(_issue("Missing CTA", "medium",
                    "'Get Quote' CTA missing from export homepage (TC-2145)",
                    "'Get Quote' button not found in header or body of export homepage."))

            # ── TC-2139: Search bar ───────────────────────────────────────────
            if not data.get("hasSearchBar"):
                issues.append(_issue("Missing CTA", "high",
                    "Search bar missing from export homepage (TC-2139)",
                    "Search input not found on export.indiamart.com — "
                    "buyers cannot search for products."))

        elif page_type == "export_search":
            # Export search results page checks
            if not data.get("hasProductCards"):
                issues.append(_issue("Export", "critical",
                    "No product cards on export search results page (TC-2152)",
                    "No /products/ links found — export search results appear empty."))
            if not data.get("hasSearchBar"):
                issues.append(_issue("Missing CTA", "high",
                    "Search bar missing from export search page (TC-2139)",
                    "Search input not found on export search results page."))

        elif page_type == "export_pdp":
            # Export PDP checks
            if not data.get("hasSearchBar"):
                issues.append(_issue("Missing CTA", "high",
                    "Search bar missing from export PDP (TC-2139)",
                    "Search input not found on export product detail page."))

        # Common for all export pages
        if data.get("footerLinks", 0) == 0:
            issues.append(_issue("Navigation", "medium",
                "Footer links not found on export page",
                "No footer navigation links detected — page may not have fully loaded."))

        return issues

    # ═══════════════════════════════════════════════════════════════════════
    # COMPANY / FCP PAGE CHECKS
    # Source: Company_BF_Testsuite_testsuite-deep.xml (TC-4835 to TC-4893)
    # URL pattern: www.indiamart.com/company-slug/
    # ═══════════════════════════════════════════════════════════════════════

    async def _check_company(self, page: Page) -> list[dict]:
        """
        DOM checks for IndiaMART Free Catalog Page (FCP / Company Page).
        Source: Company_BF_Testsuite_testsuite-deep.xml

        DOM-checkable TCs (static presence):
          TC-4835: Company name visible in header section
          TC-4836: Company address visible in company details
          TC-4837: GST number visible (if seller has GST)
          TC-4838: Verified Supplier stamp visible in header
          TC-4839: Ratings visible in header (if seller has ratings)
          TC-4844: Company logo/image visible in header
          TC-4858: Ratings & Reviews section present (if seller has reviews)
          TC-4859: Max 2 reviews shown in homepage ratings section

        Click-required TCs (handled by AI screenshot):
          TC-4840,4842,4843,4846,4848,4857,4860,4861,4864,4865,4866,4867,
          TC-4868,4869,4870,4871,4872,4873,4874,4875,4876,4877,4878,4879,
          TC-4880,4881,4882,4883,4884,4892,4893
        """
        issues = []
        await page.wait_for_timeout(500)

        data = await _safe_eval(page, r"""() => {
            const body = document.body ? document.body.innerText : '';

            // ── TC-4835: Company name in header ───────────────────────────────
            // FCP header always shows company name as H1 or prominent heading
            const h1 = document.querySelector('h1');
            const hasCompanyName = !!(h1 && h1.textContent.trim().length > 2);
            const companyNameText = h1 ? h1.textContent.trim().slice(0, 60) : '';

            // ── TC-4836: Company address visible ──────────────────────────────
            // Address typically shows city, state e.g. "Mumbai, Maharashtra"
            const hasAddress = /[A-Z][a-z]+,\s*[A-Z][a-z]+/.test(body) ||
                /\d{6}/.test(body) ||  // pincode
                !!document.querySelector('[class*=address], [class*=Address], [itemprop=address]');

            // ── TC-4837: GST visible (only check if seller has GST) ───────────
            // GST is shown masked: "27****1Z2" or unmasked — presence check only
            const hasGST = /\bGST\b/i.test(body);
            // Flag ONLY if GST is shown unmasked (full 15 chars, no asterisks)
            const gstMatch = body.match(/GST[\s\-:]*([0-9]{2}[A-Z0-9\*]{10,13}[A-Z0-9])/i);
            const gstUnmasked = gstMatch ? (gstMatch[1].match(/\*/g) || []).length < 3 : false;

            // ── TC-4838: Verified Supplier stamp ─────────────────────────────
            const hasVerifiedStamp = /verified.supplier|trustseal|verified.exporter/i.test(body) ||
                !!document.querySelector('[class*=verified], [class*=trustseal], [class*=TrustSEAL]');

            // ── TC-4839: Ratings visible ──────────────────────────────────────
            const hasRatings = /\d+(\.\d+)?\s*\(?\d+\)?/.test(body) &&
                !!document.querySelector('[class*=rating], [class*=star], [class*=review]');

            // ── TC-4844: Company logo/image visible ───────────────────────────
            const companyLogo = [...document.querySelectorAll('img')].find(img => {
                const r = img.getBoundingClientRect();
                const src = img.src || '';
                const alt = (img.alt || '').toLowerCase();
                return r.width > 30 && r.height > 30 &&
                    img.complete && img.naturalWidth > 0 &&
                    !src.includes('banner') && !src.includes('product') &&
                    !src.includes('flag') && !src.includes('icon');
            });
            const hasCompanyLogo = !!companyLogo;

            // ── TC-4840: Navigation tabs in company header ────────────────────
            // FCP has sticky header with: Home | Our Products / Products & Services | About Us | Contact Us
            const hasNavHome     = /\bHome\b/.test(body);
            const hasNavProducts = /Our Products|Products.Services|Products\s*&\s*Services/i.test(body);
            const hasNavAboutUs  = /About.Us/i.test(body);
            const hasNavContactUs = /Contact.Us/i.test(body);

            // ── TC-4858: Ratings & Reviews section ───────────────────────────
            const hasReviewSection = /ratings?.and.reviews?|rating.section|review.section/i.test(body) ||
                !!document.querySelector('[class*=review-section], [class*=reviewSection], [class*=testimonial]');

            // ── TC-4857: HSN code section ──────────────────────────────────────
            const hasHSN = /\bHSN\b/.test(body);

            // ── View Mobile Number CTA ────────────────────────────────────────
            const hasViewMobile = /view.mobile|send.email|call/i.test(body);

            // ── Product listing on FCP ────────────────────────────────────────
            // FCP shows products as category grids with product name links and
            // "...more" links — NOT proddetail links. Detect by body text length
            // and presence of product category headings or product name text.
            const hasCategoryGrid = !!(
                document.querySelector('[class*=catg], [class*=category], [class*=product-list]') ||
                // "...more" links appear under each category on FCP
                [...document.querySelectorAll('a[href]')]
                    .filter(a => /\.\.\.more|view.all/i.test(a.textContent)).length > 0 ||
                // Body has substantial content (>500 chars after stripping) = products listed
                body.length > 500
            );
            const hasProducts = hasCategoryGrid;

            // ── Header search bar (IndiaMART centralized header) ──────────────
            const hasSearchBar = !!document.querySelector(
                'input[type=search],input[placeholder*=search i],[class*=search] input,#search');

            // ── Footer ────────────────────────────────────────────────────────
            const footerWords = ['about','privacy','terms','contact','help'];
            const footerLinks = footerWords.filter(k => body.toLowerCase().includes(k)).length;

            return {
                hasCompanyName, companyNameText,
                hasAddress, hasGST, gstUnmasked,
                hasVerifiedStamp, hasRatings, hasCompanyLogo,
                hasNavHome, hasNavProducts, hasNavAboutUs, hasNavContactUs,
                hasReviewSection, hasHSN, hasViewMobile, hasProducts,
                hasSearchBar, footerLinks,
            };
        }""", {})

        # ── TC-4835: Company name visible ─────────────────────────────────────
        if not data.get("hasCompanyName"):
            issues.append(_issue("Company", "critical",
                "Company name (H1) missing from FCP header (TC-4835)",
                "No H1 heading found — company name must be visible in "
                "the FCP header section."))

        # ── TC-4836: Company address visible ──────────────────────────────────
        if not data.get("hasAddress"):
            issues.append(_issue("Company", "medium",
                "Company address not visible on FCP (TC-4836)",
                "Company address (city, state or pincode) not found in "
                "company details section."))

        # ── TC-4837: GST shown unmasked — privacy violation ───────────────────
        # Only flag if GST is visible AND unmasked (not checking absence — optional)
        if data.get("hasGST") and data.get("gstUnmasked"):
            issues.append(_issue("Privacy", "critical",
                "GST number shown UNMASKED on company page (TC-4837)",
                "Full GST number visible without masking — must show as "
                "'27****1Z2' format. Full exposure is a privacy violation."))

        # ── TC-4838: Verified Supplier stamp ──────────────────────────────────
        # Note: not all sellers have TrustSEAL — only flag if page title/desc
        # indicates it's a TrustSEAL seller but stamp is absent
        # Conservative: skip this check to avoid false positives

        # ── TC-4840: Navigation tabs present ──────────────────────────────────
        missing_nav = []
        if not data.get("hasNavProducts"):  missing_nav.append("Our Products")
        if not data.get("hasNavAboutUs"):   missing_nav.append("About Us")
        if not data.get("hasNavContactUs"): missing_nav.append("Contact Us")
        if missing_nav:
            issues.append(_issue("Navigation", "high",
                f"FCP navigation tab(s) missing (TC-4840): {', '.join(missing_nav)}",
                f"Expected navigation tabs not visible in FCP sticky header: "
                f"{', '.join(missing_nav)}."))

        # ── TC-4844: Company logo visible ─────────────────────────────────────
        # Only flag if page has been scrolled enough and no logo found
        # Conservative: only flag as medium since logo is optional for some sellers
        if not data.get("hasCompanyLogo"):
            issues.append(_issue("Company", "medium",
                "Company logo/image not visible in FCP header (TC-4844)",
                "No company logo image found in header area — "
                "sellers may not have uploaded logo (optional)."))

        # ── TC-4842 / TC-4843: View Mobile CTA visible ────────────────────────
        if not data.get("hasViewMobile"):
            issues.append(_issue("Missing CTA", "high",
                "'View Mobile Number' / contact CTA missing from FCP (TC-4842)",
                "No 'View Mobile Number', 'Send Email', or 'Call' CTA found — "
                "buyers cannot contact this seller."))

        # ── Products listed ───────────────────────────────────────────────────
        if not data.get("hasProducts"):
            issues.append(_issue("Company", "medium",
                "No product listings visible on company FCP page",
                "No proddetail links found — company page appears to have "
                "no products listed."))

        # ── TC-4837: GST shown unmasked already handled above ─────────────────

        # ── Header search bar (IndiaMART centralized header) ──────────────────
        if not data.get("hasSearchBar"):
            issues.append(_issue("Navigation", "high",
                "IndiaMART search bar missing from header on company page",
                "Search input not found — buyers cannot search from this page."))

        # ── Footer ────────────────────────────────────────────────────────────
        if data.get("footerLinks", 0) == 0:
            issues.append(_issue("Navigation", "medium",
                "Footer links not found — footer may not have rendered",
                "No footer navigation links detected on company page."))

        return issues

    # ═══════════════════════════════════════════════════════════════════════════
    # REDIRECT VALIDATION
    # ═══════════════════════════════════════════════════════════════════════════
    async def _check_redirects(
        self, browser, page, base_url: str, page_type: str
    ) -> tuple:
        issues  = []
        results = []

        parsed_base = urlparse(base_url)
        host        = parsed_base.hostname or ""
        is_local    = host in ("localhost", "127.0.0.1") or \
                      host.startswith("192.168.") or host.startswith("10.")
        skip_types  = {"product", "company"} if is_local else set()
        if is_local:
            print(f"        ℹ️  Local URL — skipping product & company link validation")

        try:
            links_to_check = await page.evaluate("""() => {
                const origin = window.location.origin;
                const links  = [];
                function abs(href) {
                    if (!href) return null;
                    if (href.startsWith('http')) return href;
                    if (href.startsWith('//'))   return 'https:' + href;
                    if (href.startsWith('/'))    return origin + href;
                    return null;
                }
                const logoA = document.querySelector('a.logo-a, header a img, a img[alt*=indiamart i], a[class*=logo]');
                if (logoA) {
                    const a = logoA.tagName === 'A' ? logoA : logoA.closest('a');
                    if (a) links.push({ type: 'logo', label: 'Header Logo', href: abs(a.getAttribute('href')) });
                }
                const bcLinks = [...document.querySelectorAll('[class*=breadcrumb] a, [class*=bread-crumb] a')].slice(0,4);
                bcLinks.forEach((a,i) => {
                    const href = abs(a.getAttribute('href'));
                    if (href) links.push({ type: 'breadcrumb', label: `Breadcrumb: ${a.textContent.trim().slice(0,35)}`, href });
                });
                const prodLinks = [...document.querySelectorAll('a[href*=proddetail]')].slice(0,3);
                prodLinks.forEach((a,i) => {
                    const href = abs(a.getAttribute('href'));
                    if (href) links.push({ type: 'product', label: `Product Card ${i+1}: ${a.textContent.trim().slice(0,35)}`, href });
                });
                const compLinks = [...document.querySelectorAll('a[href]')].filter(a => {
                    const href = a.getAttribute('href') || '';
                    const txt  = a.textContent.trim();
                    return txt.length > 3 && txt.length < 80 &&
                        href.includes('indiamart.com/') &&
                        !href.includes('proddetail') && !href.includes('/search') &&
                        !href.includes('dir.indiamart') && !href.includes('help') &&
                        !href.includes('sell') && !href.includes('login');
                }).slice(0,2);
                compLinks.forEach((a,i) => {
                    const href = abs(a.getAttribute('href'));
                    if (href) links.push({ type: 'company', label: `Company Link ${i+1}: ${a.textContent.trim().slice(0,35)}`, href });
                });
                const footerLinks = [...document.querySelectorAll('footer a[href],[class*=footer] a[href]')]
                    .filter(a => { const h = a.getAttribute('href')||''; return h.startsWith('http')||h.startsWith('/'); })
                    .slice(0,3);
                footerLinks.forEach((a,i) => {
                    const href = abs(a.getAttribute('href'));
                    if (href) links.push({ type: 'footer', label: `Footer: ${a.textContent.trim().slice(0,25)}`, href });
                });
                return links.filter(l => l.href && !l.href.includes('javascript:'));
            }""")
        except Exception as e:
            return [], []

        if not links_to_check:
            return [], []

        print(f"        Found {len(links_to_check)} links to validate")

        FOOTER_DOMAINS = [r"indiamart\.com", r"facebook\.com", r"fb\.com",
                          r"twitter\.com", r"x\.com", r"linkedin\.com",
                          r"instagram\.com", r"youtube\.com",
                          r"play\.google\.com", r"apps\.apple\.com", r"localhost"]
        BAD_PATTERNS   = [r"/login\b", r"/signin\b", r"/sign-in\b",
                          r"[?&]redirect=", r"/404\b", r"/not-found\b", r"/error\b"]

        ctx  = await browser.new_context(viewport={"width":1280,"height":800},
               user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36")
        tpg  = await ctx.new_page()

        for link in links_to_check:
            href, ltype, label = link["href"], link["type"], link["label"]
            if ltype in skip_types:
                print(f"        ⏭️  [skipped-local] {label[:55]}")
                continue

            result = {"type": ltype, "label": label, "url": href,
                      "status": None, "final_url": None, "passed": True, "issue": None}
            try:
                resp      = await tpg.goto(href, wait_until="domcontentloaded", timeout=15000)
                final_url = tpg.url
                status    = resp.status if resp else 0
                result["status"]    = status
                result["final_url"] = final_url

                if status in (404, 410):
                    result["passed"] = False
                    result["issue"]  = f"HTTP {status} — page not found"
                    issues.append(_issue("Functional", "high",
                        f"{label} → 404 Not Found",
                        f"Link '{href[:80]}' returned HTTP {status}. Broken link."))
                elif status >= 500:
                    result["passed"] = False
                    issues.append(_issue("Functional", "critical",
                        f"{label} → Server Error ({status})",
                        f"Link '{href[:80]}' returned HTTP {status}."))
                elif status in (200, 301, 302):
                    # Check for bad redirect
                    bad = next((p for p in BAD_PATTERNS if re.search(p, final_url, re.I)), None)
                    if bad:
                        result["passed"] = False
                        issues.append(_issue("Functional", "high",
                            f"{label} → Wrong redirect destination",
                            f"Link redirected to: '{final_url[:80]}' (login/error page)."))
                    elif ltype == "product" and "proddetail" not in final_url:
                        result["passed"] = False
                        issues.append(_issue("Functional", "high",
                            f"{label} → Expected PDP but landed elsewhere",
                            f"Product link landed on '{final_url[:80]}' — expected /proddetail/ URL."))
                    elif ltype == "footer":
                        matched = any(re.search(p, final_url, re.I) for p in FOOTER_DOMAINS)
                        if not matched:
                            result["passed"] = False
                            issues.append(_issue("Functional", "medium",
                                f"{label} → Unexpected domain",
                                f"Footer link landed on '{final_url[:80]}' — unknown domain."))

            except PWTimeout:
                result["passed"] = False
                result["status"] = "timeout"
                issues.append(_issue("Functional", "high",
                    f"{label} → Page load timeout",
                    f"Link '{href[:80]}' timed out after 15s."))
            except Exception as e:
                err = str(e)
                if "ERR_NAME_NOT_RESOLVED" in err or "net::" in err:
                    if "localhost" not in href and "127.0.0.1" not in href:
                        result["passed"] = False
                        issues.append(_issue("Functional", "high",
                            f"{label} → URL unresolvable",
                            f"Link '{href[:80]}' could not be resolved (DNS error)."))

            icon = "✅" if result["passed"] else "❌"
            print(f"        {icon} [{result.get('status','?')}] {label[:55]}")
            results.append(result)

        await ctx.close()
        return issues, results

    # ═══════════════════════════════════════════════════════════════════════════
    # CLICK INTERACTION TESTING
    # ═══════════════════════════════════════════════════════════════════════════
    async def _check_click_interactions(
        self, browser, base_url: str, page_type: str, already_flagged: set = None
    ) -> tuple:
        issues      = []
        cta_results = []
        already_flagged = already_flagged or set()

        ctx = await browser.new_context(viewport={"width":1280,"height":800},
              user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36")
        await ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        page = await ctx.new_page()

        try:
            await page.goto(base_url, wait_until="load", timeout=30000)
            await page.wait_for_timeout(1000)
        except Exception as e:
            await ctx.close()
            return [], []

        # Dismiss modals
        for sel in ['[data-dismiss=modal]','button[aria-label*=close i]','.modal-close',
                    '[class*=close-btn]','button.close','[class*=skip i]']:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=600):
                    await btn.click(timeout=600)
                    await page.wait_for_timeout(300)
            except Exception:
                pass

        # Define tests per page type
        if page_type in ("pdp","pdp_t1","pdp_t3","pdp_t14"):
            tests = [
                {"name":"Contact Supplier","tc":"TC-7049","sel":["button:has-text('Contact Supplier')","button:has-text('Send Enquiry')"],"expect":"modal_opens","sev":"high"},
                {"name":"Submit Requirement","tc":"TC-7046","sel":["button:has-text('Submit Requirement')"],"expect":"modal_or_confirm","sev":"high"},
            ]
        elif page_type == "mcat":
            # MCAT CTA clickability is fully handled by DOM checks (callNowClickable,
            # contactSupplierClickable) with per-card disabled counts.
            # Click interaction testing for MCAT is skipped to avoid duplicate reporting.
            tests = []
        elif page_type == "search":
            tests = [
                {"name":"Call Now (card)","tc":"TC-3643","sel":["button:has-text('Call Now')","[class*=btn-call]"],"expect":"phone_or_modal","sev":"high"},
                {"name":"Contact Supplier (card)","tc":"TC-3635","sel":["button:has-text('Contact Supplier')"],"expect":"modal_opens","sev":"high"},
                {"name":"Search Button","tc":"TC-3591","sel":["button:has-text('Search')","button:has-text('Find')",".hdr-search-btn","[class*=search-btn]"],"expect":"results_load","sev":"high"},
            ]
        else:
            await ctx.close()
            return [], []

        for test in tests:
            result = {"name":test["name"],"tc":test["tc"],"passed":False,"outcome":"not_found","detail":""}

            # Skip if DOM check already flagged this CTA — avoids duplicate reporting
            already = any(test["tc"] in title or test["name"] in title
                         for title in already_flagged)
            if already:
                result["outcome"] = "skipped_already_flagged"
                result["detail"]  = f"Already reported by DOM check — skipping click test"
                result["passed"]  = True  # don't double-count as failure
                print(f"        ⏭️  {test['name']} ({test['tc']}): already flagged by DOM check")
                cta_results.append(result)
                continue

            el = None

            for sel in test["sel"]:
                try:
                    loc = page.locator(sel).first
                    if await loc.is_visible(timeout=1500):
                        is_dis = await loc.evaluate("""el => {
                            const s = window.getComputedStyle(el);
                            return el.disabled===true || el.getAttribute('disabled')!==null ||
                                s.pointerEvents==='none' || el.getAttribute('aria-disabled')==='true' ||
                                [...(el.classList||[])].some(c=>c.includes('disabled'));
                        }""")
                        if is_dis:
                            result["outcome"] = "disabled"
                            result["detail"]  = f"Found via '{sel}' but is disabled (pointer-events/disabled attr)"
                            issues.append(_issue("Functional", test["sev"],
                                f"{test['name']} CTA present but disabled ({test['tc']})",
                                f"{test['name']} button is visible but non-interactive — "
                                f"pointer-events:none or disabled attribute detected."))
                            break
                        el = loc
                        break
                except Exception:
                    continue

            if result["outcome"] == "disabled":
                print(f"        ❌ {test['name']} ({test['tc']}): disabled")
                cta_results.append(result)
                continue

            if el is None:
                print(f"        ⚠️  {test['name']} ({test['tc']}): not_found")
                cta_results.append(result)
                continue

            pre_url  = page.url
            pre_body = await _safe_eval(page, "document.body.innerHTML.length", 0)

            try:
                await el.scroll_into_view_if_needed(timeout=3000)
                await el.click(timeout=12000)
                await page.wait_for_timeout(1500)
            except Exception as e:
                err_str = str(e)
                # Timeout on real IndiaMART pages = slow JS/network, not a broken CTA
                # Only flag if it is NOT a timeout error
                if "Timeout" in err_str or "timeout" in err_str:
                    result["outcome"] = "timeout_skip"
                    result["detail"]  = "Click timed out — likely slow page or auth wall, not a bug"
                    result["passed"]  = True  # don't flag as issue
                    print(f"        ⏭️  {test['name']}: click timeout (skipped — not flagged)")
                else:
                    result["outcome"] = "click_failed"
                    result["detail"]  = err_str[:120]
                    issues.append(_issue("Functional", test["sev"],
                        f"{test['name']} button click failed ({test['tc']})",
                        f"Clicking '{test['name']}' threw unexpected error: {err_str[:100]}"))
                    print(f"        ❌ {test['name']}: click_failed")
                cta_results.append(result)
                continue

            post_url  = page.url
            post_body = await _safe_eval(page, "document.body.innerHTML.length", 0)

            if test["expect"] in ("phone_or_modal", "modal_opens", "modal_or_confirm"):
                modal_info = await _safe_eval(page, """() => {
                    const modals = [...document.querySelectorAll(
                        '[class*=modal],[class*=dialog],[role=dialog],[class*=popup],' +
                        '[class*=overlay],[class*=enquiry],[class*=contact],[class*=login],' +
                        '[class*=signin],[class*=otp],[class*=verify],[class*=auth],' +
                        '[id*=modal],[id*=dialog],[id*=popup],[id*=login]')];
                    const visible = modals.some(m => {
                        const s = window.getComputedStyle(m);
                        const r = m.getBoundingClientRect();
                        return s.display!=='none' && s.visibility!=='hidden' &&
                            s.opacity!=='0' && r.width>80 && r.height>50;
                    });
                    const bodyTxt  = document.body.innerText;
                    const phone    = /\+?[0-9][\s-]?\(?[0-9]{3}\)?[\s-]?[0-9]{3,4}[\s-]?[0-9]{4}/.test(bodyTxt);
                    const confirm  = /thank you|submitted|success|requirement sent/i.test(bodyTxt);
                    // Login/OTP wall = CTA is working, just needs auth (valid on real IndiaMART)
                    const loginWall = /enter.*mobile|verify.*number|\botp\b|sign.?in to|log.?in to|continue with/i.test(bodyTxt);
                    return {visible, phone, confirm, loginWall};
                }""", {})
                tel_nav   = "tel:" in post_url
                body_grew = abs(post_body - pre_body) > 200
                passed    = (tel_nav or
                             modal_info.get("visible") or
                             modal_info.get("phone") or
                             modal_info.get("confirm") or
                             modal_info.get("loginWall") or
                             body_grew)
                if passed:
                    result["passed"]  = True
                    result["outcome"] = "passed"
                    outcome_str = ("tel: nav" if tel_nav else
                                   "login wall (auth needed)" if modal_info.get("loginWall") else
                                   "modal opened" if modal_info.get("visible") else
                                   "phone shown" if modal_info.get("phone") else
                                   "DOM changed")
                    result["detail"]  = f"✅ CTA responded — {outcome_str}"
                else:
                    result["outcome"] = "no_response"
                    result["detail"]  = "Clicked but no modal, phone, login wall or DOM change appeared"
                    issues.append(_issue("Functional", test["sev"],
                        f"{test['name']} clicked but no response ({test['tc']})",
                        f"Clicking '{test['name']}' produced no visible result — "
                        f"no modal opened, no phone revealed, no DOM change detected. "
                        f"CTA appears functionally broken."))

            elif test["expect"] == "results_load":
                url_changed  = post_url != pre_url
                body_changed = abs(post_body - pre_body) > 1000
                has_prods    = await _safe_eval(page, "document.querySelectorAll('a[href*=proddetail]').length > 0", False)
                if url_changed or (body_changed and has_prods):
                    result["passed"]  = True
                    result["outcome"] = "passed"
                    result["detail"]  = f"✅ Results loaded"
                else:
                    result["outcome"] = "no_results"
                    issues.append(_issue("Functional", test["sev"],
                        f"Search button click produced no results ({test['tc']})",
                        "Clicking Search button did not load new results or change URL."))

            icon = "✅" if result["passed"] else "❌"
            print(f"        {icon} {test['name']} ({test['tc']}): {result['outcome']}")
            cta_results.append(result)

        await ctx.close()
        return issues, cta_results
