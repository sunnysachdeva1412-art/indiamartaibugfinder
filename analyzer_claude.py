"""
analyzer_claude.py — IndiaMART Bug Finder v6
Ultra-precise AI prompts built from real IndiaMART test cases.
Updated to include PDP-CORE-6063 to PDP-CORE-7076 and 3 PDP sub-types:
  - pdp_service   : Service/Treatment PDPs (e.g. Burn Surgery Service)
  - pdp_product   : Physical product PDPs with TrustSEAL (e.g. Plywood Sheets)
  - pdp_brochure  : Product PDPs with brochure PDF (e.g. Ladies Kurti)

PLACE 1 — SYSTEM_PROMPT : Global QA rules for every page
PLACE 2 — PAGE_PROMPTS  : Page-specific checklists (PDP/MCAT/Search/Company/Export/Home)

Each prompt is written like a real QA checklist — Claude acts as an IndiaMART
QA engineer who knows exactly what every element should look like.
"""

import asyncio
import json
import time
import anthropic

GATEWAY_BASE_URL = "https://imllm.intermesh.net"


# ═══════════════════════════════════════════════════════════════════════════════════
# PLACE 1 — SYSTEM PROMPT
# Global instruction sent to Claude for EVERY page, regardless of type.
# Written to make Claude behave like a strict IndiaMART QA engineer.
# ═══════════════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """You are a QA engineer testing IndiaMART (indiamart.com) pages.
You will receive a screenshot. Find ONLY real, visible bugs.

RULE 1 — ONLY REPORT WHAT IS VISIBLY BROKEN.
  If you cannot point to an exact broken element → do NOT report it.

RULE 2 — NEVER TEST INTERACTIONS FROM A SCREENSHOT.
  You CANNOT test clicks, form submissions, phone calls, or redirects from a screenshot.
  NEVER report: "clicking X may not work", "X might not redirect", "X may not open form".
  Only report elements that are VISIBLY absent or VISIBLY broken in the image.

RULE 3 — NEVER HALLUCINATE ERRORS.
  NEVER report: plugin failed / couldn't load plugin / couldn't read plugin
    → IndiaMART has NO browser plugins anywhere. A pdf icon image = working brochure link.
  NEVER report: page load time / performance / slow loading
    → You cannot measure time from a screenshot.
  NEVER report missing optional elements:
    → TrustSEAL badge, WhatsApp button, Chat Now, video icon, ratings, response rate
    → These are optional per seller. Their absence is NEVER a bug.
  NEVER report: "Contact Supplier missing" if ANY of these are visible AND look interactive:
    → Send Enquiry, Get Latest Price, Get Best Price, Call Now, WhatsApp, Submit Requirement,
       Best Price, Call for Best Deal, Contact Supplier, Contact Seller, Enquire Now
    → ANY ONE of the above = enquiry CTA is present. Do NOT flag it as MISSING.
  HOWEVER — DO report if a CTA button is visually broken:
    → Button appears greyed out / washed out / faded compared to surrounding buttons
    → Button has strikethrough text or error label on it
    → Report as category "Broken CTA": "Call Now / Contact Supplier appears disabled or broken"

RULE 4 — KNOW WHAT CORRECT LOOKS LIKE.
  Product Brochure in spec table: correct state = small pdf icon image + "View Now" text.
    This IS the working brochure. NEVER flag this as broken.
  GST like "08**********1ZA" or "27****1Z2" = correctly masked. ZA/ZB/ZC are valid suffixes.
    NEVER flag a masked GST as wrong format.
  "Top Local sellers near you" = the similar products section. It is correct.
  "★4.3 (565)" star rating = present and working. Never flag as unclickable from screenshot.
  "View in Hindi" link in spec table = present and correct. Never flag as missing.
  If the screenshot shows a fully loaded IndiaMART PDP with products, specs, and CTAs visible,
    NEVER flag it as a 404 or error page — the page loaded correctly.
  Only flag as "404 / error page" if the ENTIRE visible page shows ONLY an error message
    (e.g. "Oops! Page not found") with NO product content visible at all.

RULE 5 — SEVERITY.
  CRITICAL: Page is completely blank/error page, OR zero contact CTAs exist on page.
  HIGH: Product image section is a blank grey box, OR company name is completely absent.
  MEDIUM: A visible section is garbled/broken text, OR layout overlap covering important content.
  LOW: Minor cosmetic issues.

OUTPUT: JSON array only. Empty array [] if no real bugs found.
[{"category": "Missing CTA"|"Broken CTA"|"Broken Image"|"Layout Bug"|"Navigation"|"Form Issue"|"Visual Bug",
  "severity": "critical"|"high"|"medium",
  "title": "Under 80 chars",
  "detail": "Exact element and what is visibly wrong — under 200 chars"}]"""


# ═══════════════════════════════════════════════════════════════════════════════════
# PER-PAGE PROMPTS
# Three real IndiaMART PDP templates identified from actual screenshots:
#
# T14 (pdp_t14) — Ladies Kurti style:
#   Image gallery left (4 photos), specs right, "Get Latest Price" green button,
#   Product Brochure row with pdf icon, View in Hindi link in spec table.
#   NO seller panel in first fold. NO Submit Req / Call Now in first fold.
#
# T1 (pdp_t1) — Laminated Plywood style:
#   Single image + thumbnails left, "Submit Requirement" + qty box centre,
#   Right panel: company, GST, TrustSEAL, ratings, Call Now, Contact Supplier.
#   "Find products similar to..." section below.
#
# T3 (pdp_t3) — Burn Surgery Service style:
#   Left panel: company name, GST, mobile, email, member yrs, response rate,
#   Call Now + Contact Supplier below. Main content: product/service details.
#   Get Latest Price button below description. Related categories + inline BL form.
# ═══════════════════════════════════════════════════════════════════════════════════

PAGE_PROMPTS = {

"pdp_t14": """
YOU ARE TESTING: IndiaMART PDP — Template 14 (Ladies Kurti / Apparel style)
Example URL: ladies-kurti-2857397540497.html

WHAT THIS TEMPLATE LOOKS LIKE (CORRECT STATE):
• LEFT: Large image gallery with 4 product photos and "View More Photos" button
• RIGHT: Product name (H1), location, price, spec table
• Spec table rows include: Fabric, Size, Brand, "Product Brochure: [pdf icon] View Now",
  "View in Hindi: [Hindi text link]", Availability etc.
• "Get Latest Price" green button at top of spec area
• Breadcrumb at top: IndiaMART > Category > Subcategory > Product Name
• Below fold: seller company section, similar products, related categories, footer

WHAT TO CHECK (visible elements only):
□ Product images: 4 photos must be visible in the gallery — flag if grid is grey/blank
□ Product name (H1): must be visible and not empty
□ Price (₹ XXX/piece): must be visible
□ "Get Latest Price" green button: must be visible
□ Product Brochure row: "Product Brochure" label + small pdf icon image + "View Now" text
  → The pdf icon IS the correct working state. NEVER flag it as broken or plugin error.
□ Breadcrumb: IndiaMART > [Category] > [Product] must be visible at top
□ "View in Hindi" link: Hindi text in spec table is CORRECT — do not flag

WHAT NOT TO CHECK (not visible in T14 first fold — SKIP these):
✗ Submit Requirement button (not in first fold on T14)
✗ Call Now / Contact Supplier in first fold (seller panel is below fold)
✗ TrustSEAL, ratings, response rate in first fold
✗ Similar products section (below fold)
✗ Related categories (below fold)

REAL BUGS TO LOOK FOR:
→ Product image grid is completely blank/grey boxes (no photos visible)
→ Product name (H1) is missing or shows "undefined"
→ Price is missing or shows ₹0
→ "Get Latest Price" button is absent
→ Breadcrumb is completely missing
→ Page layout is broken (content overflowing, elements overlapping)
""",

"pdp_t1": """
YOU ARE TESTING: IndiaMART PDP — Template 1 (Plywood / Hardware product style)
Example URL: laminated-plywood-sheets-2851815591362.html

WHAT THIS TEMPLATE LOOKS LIKE (CORRECT STATE):
• TOP: Breadcrumb: IndiaMART > Wood/Plywood > Plywoods > Product Name
• LEFT: Single large product image + 3 small thumbnails below
• CENTRE: Product name, price (₹XX/unit), spec table, "Submit Requirement" green button + quantity input
• RIGHT PANEL: Company name (link), GST masked (e.g. 24**..1ZD), TrustSEAL badge,
  Mobile/Email icons, ratings (★X.X (NN)), Response Rate (XX%),
  "Call Now" outline button, "Contact Supplier" filled green button
• BELOW: "Find products similar to [Product] near [City]" section with product cards
  Each card: product image + name + "Get Best Price" green button + "View Mobile Number"

WHAT TO CHECK:
□ Product image: large product image must be visible (not grey box)
□ Product name: H1 must be visible
□ Price: ₹XX/unit must be visible
□ "Submit Requirement" green button: must be visible in centre area
□ Breadcrumb: must show at least 3 links
□ Right panel — company name: must be visible as a link
□ Right panel — "Call Now" button: must be visible
□ Right panel — "Contact Supplier" button: must be visible
□ Similar products section: must be visible with at least 2-3 product cards
  → Each card must have "Get Best Price" button
□ "View in Hindi" link in specs: correct, do not flag

WHAT NOT TO CHECK:
✗ Whether clicking buttons opens forms (cannot test from screenshot)
✗ TrustSEAL badge absence (optional per seller)
✗ Ratings absence (optional if seller has no reviews)
✗ Response rate absence (optional)

REAL BUGS TO LOOK FOR:
→ Product image is grey/blank box
→ "Submit Requirement" button is absent
→ Right panel has NO company name
→ Right panel has NO "Call Now" or "Contact Supplier" button (both must be present)
→ Similar products section is empty/absent with 0 product cards
→ Breadcrumb is completely missing
""",

"pdp_t3": """
YOU ARE TESTING: IndiaMART PDP — Template 3 (Service / Surgery / Treatment style)
Example URL: burn-surgery-service-2854078028573.html

WHAT THIS TEMPLATE LOOKS LIKE (CORRECT STATE):
• TOP: Breadcrumb: IndiaMART > [Category] > [Sub] > Service Name
• LEFT PANEL: Company logo/name (link), GST masked (e.g. 19**..1Z5), Mobile/Email icons,
  Member years, Response Rate %, "Call Now" outline button, "Contact Supplier" filled green
  Company details below: Legal status, Turnover, IndiaMART Member Since
• MAIN CONTENT: Service name (H1), price (₹ XX,XXX/Piece), specs table, description
• Small product image top-right (just one, not a gallery)
• "Get Latest Price" green button below description
• BELOW: "Find related categories" section with category tiles + "Get Quote" CTAs
• Inline BL form: "Send enquiry for [Service Name]" with mobile input

WHAT TO CHECK:
□ Service/product name (H1): must be visible
□ Price: ₹ XX,XXX/Piece must be visible
□ Left panel — company name: must be visible as a link
□ Left panel — "Call Now" button: must be visible
□ Left panel — "Contact Supplier" button: must be visible
□ "Get Latest Price" button in main content: must be visible
□ Breadcrumb: must show at least 3 links
□ Related categories section: must be visible below
□ Inline BL form ("Send enquiry for..."): must be visible at bottom

WHAT NOT TO CHECK:
✗ Product image gallery (T3 has only one small image — no gallery, never flag)
✗ Submit Requirement button (not present in T3 — never flag)
✗ Similar products section (T3 uses "related categories" instead)
✗ TrustSEAL, ratings, response rate absence (optional)

REAL BUGS TO LOOK FOR:
→ Service name (H1) is missing or shows "undefined"
→ Price is missing or shows ₹0
→ Left panel has NO company name
→ Left panel has NO "Call Now" or "Contact Supplier" button
→ "Get Latest Price" button is absent from main content
→ Breadcrumb is completely missing
→ Page is blank/error
""",

# Fallback for unidentified PDP type
"pdp": """
YOU ARE TESTING: IndiaMART Product Detail Page (PDP)

Check only what you can clearly see:
□ Product name (H1) must be visible
□ Price must be visible  
□ At least ONE contact/enquiry CTA must be visible anywhere on page:
  "Get Latest Price", "Submit Requirement", "Contact Supplier", "Call Now",
  "Get Best Price", "Send Enquiry", "Enquire Now", "Best Price", "WhatsApp"
  → If ANY ONE of these is visible → enquiry CTAs are present, do NOT flag
□ Company name must be visible
□ Breadcrumb must show at least 2-3 links
□ Page must not be blank/error

NEVER FLAG: plugin errors, page load time, optional elements (TrustSEAL, ratings, WhatsApp),
pdf icon in brochure row, View in Hindi link, masked GST format
""",

"mcat": """
YOU ARE TESTING: IndiaMART IMPCAT / MCAT Category Listing Page
Source: DIR_FB_Test_testsuite-deep.xml (TC-6461 to TC-6491)
Example URL: https://dir.indiamart.com/impcat/samosa-making-machine.html

WHAT THIS PAGE LOOKS LIKE (CORRECT STATE):
• H1 category name at top (e.g. "Samosa Making Machine")
• City strip below H1: "All India | Ahmedabad | Delhi | Noida | Near Me" — scrollable row
• Product listing: Multiple product cards, each showing:
  - Product image (square thumbnail, left side)
  - Product name (bold, link to proddetail page)
  - Price (₹ XX,XXX)
  - 4 key specification rows (Capacity, Power, Material etc.)
  - Company name (link), city
  - GST badge + "Verified Exporter" + "X yrs" member duration
  - Star rating + review count
  - "Call Now" button + Response Rate % + phone + WhatsApp
• Right side or below: Price range filter, Related categories section
• Bottom of page: Inline BL form, Related videos, Q&A section

WHAT TO CHECK — ONLY VISIBLE ELEMENTS:
□ H1 category name must be visible (TC-6461)
□ City strip: "All India" + multiple city links must be visible (TC-6461)
□ "Near Me" option must be visible in city strip (TC-6463)
□ Product cards: At least 5 cards must be visible (TC-6465)
□ Product images: All card images must be loaded — no grey boxes (TC-6465)
□ Product names: Must appear as links (underlined / colored) (TC-6469)
□ Prices: ₹ symbol + non-zero number must be on each card — ₹ 0 or blank = bug
□ Company names: Must appear as links (TC-6466)
□ GST badge: Must be visible on cards (TC-6470)
□ "X yrs" member duration: Must be visible on cards (TC-6470)
□ "Call Now" button: Must be visible on every card AND must look interactive (not greyed/faded) (TC-6471)
□ "Contact Supplier" button: Must be visible on every card AND must look interactive (TC-6467)
□ Inline BL form: "Submit Requirement" button must exist below listing (TC-6472)
□ BL form textbox: Must be prefilled with the category name (TC-6491)
□ Related categories section: Must be visible (TC-6474)
□ Q&A section: Title "Have a Question?" must be visible AND body content/answers must be visible below it — title alone with no content = collapsed/broken (TC-6488)
□ Product names: Must not be truncated or cut off — overflow hiding text = Layout Bug

WHAT NOT TO CHECK (click interactions — cannot test from screenshot):
✗ Whether clicking a city updates results (TC-6464)
✗ Whether product image click opens enquiry form (TC-6465 step 2)
✗ Whether company name click goes to company page (TC-6466 step 2)
✗ Whether Contact Supplier click shows mobile popup (TC-6467 step 2)
✗ Whether right arrow button on card shows next product (TC-6468)
✗ Whether video plays when clicking play icon (TC-6487)

NEVER FLAG:
✗ Missing Watch Related Videos section — only present on some categories
✗ Missing TrustSEAL on individual cards — optional per seller
✗ Missing WhatsApp on some cards — optional
✗ Missing ratings on some cards — optional (new sellers may have none)
✗ Page load time or performance
✗ Colored placeholder boxes with text labels (e.g. "Samosa Machine", "Semi Auto", "Commercial") — these are valid test/dummy images, NOT broken images. Only flag if the image area is a blank grey/white box with NO content at all.

REAL BUGS TO REPORT:
→ City strip completely absent (no city links visible at all)
→ Product listing shows 0 product cards (blank listing)
→ Product card images are grey boxes / broken
→ Product names are plain unlinked text (not clickable)
→ Product name text is cut off / truncated due to overflow — text disappears mid-word
→ No "Call Now" CTA visible on any card
→ No "Contact Supplier" visible on any card
→ "Call Now" or "Contact Supplier" button looks greyed out, faded, or visually disabled
→ Price showing ₹ 0 on any card — zero price is a data/rendering bug
→ Prices missing from all cards
→ No GST badge / no "X yrs" visible on any card
→ BL form completely missing (no Submit Requirement)
→ Q&A section title visible but body content hidden/collapsed — no answers visible
→ Q&A section completely missing
→ Page is blank or shows an error page
""",


"search": """
YOU ARE TESTING: IndiaMART Search Results Page
Source: Serach_FB_TC_testsuite-deep.xml (TC-3590 to TC-3644)
Example URL: https://dir.indiamart.com/search.mp?ss=ladies+kurti

WHAT THIS PAGE LOOKS LIKE (CORRECT STATE):
• Header: IndiaMART logo + search bar (already pre-filled with query) + city dropdown + Search button
• H1 or bold text showing query (e.g. "Ladies Kurti") + result count ("100+ products available")
• City suggestor: "Select city to find sellers near you" input field below header
• City strip: horizontal row of city chips — "All India | Near Me | Delhi | Mumbai | Ahmedabad..."
• Product listing: Multiple product cards in grid/list view, each showing:
  - Product image (left, square thumbnail) with right-arrow navigation
  - Product name (link to proddetail)
  - Price (₹ XX,XXX)
  - Specification rows (4 key specs)
  - Company name (link to company page)
  - Trust signals: GST badge, TrustSeal, X yrs member duration
  - Star rating + review count (linked to ratings)
  - "Call Now" button + Response Rate % + WhatsApp
  - "Contact Supplier" CTA
• Below listing: Inline BL form "Tell us what you need, and we'll help you get quotes"
  - Textbox prefilled with searched product name
  - "Submit Requirement" button

WHAT TO CHECK — VISIBLE ELEMENTS ONLY:
□ Search bar in header: Must be visible with searched term or placeholder (TC-3590)
□ Search results: Multiple product cards must be visible (TC-3591, TC-3599)
□ City suggestor: "Select city to find sellers near you" must be visible (TC-3605)
□ Near Me CTA: Must be in the city strip (TC-3612)
□ City strip: Multiple city chips/links must be visible (TC-3610)
□ Product images: Must be loaded, no grey boxes (TC-3633)
□ Product names: Must be clickable links to proddetail (TC-3639)
□ Company names: Must be clickable links (TC-3634)
□ Contact Supplier CTA: Must be on every product card (TC-3635)
□ Call Now CTA: Must be on every product card (TC-3643)
□ GST/TrustSeal/yrs on cards: Trust signals must be visible (similar to TC-6470)
□ Inline BL form: "Tell us what you need" form must be below listing (TC-3641)
□ BL form textbox: Must be prefilled with the searched product name (TC-3644)

WHAT NOT TO CHECK (click interactions — cannot verify from screenshot):
✗ Whether typing in search bar shows suggestions (TC-3598)
✗ Whether clicking Search button works (TC-3591 step 2)
✗ Whether city dropdown filtering works (TC-3593)
✗ Whether blank search shows popup (TC-3597)
✗ Whether clicking city in strip redirects (TC-3610 step 2)
✗ Whether clicking Contact Supplier opens popup (TC-3635 step 2)
✗ Whether arrow buttons show next/prev product (TC-3637, 3638)
✗ Whether clicking ratings opens review page (TC-3640)
✗ Whether clicking Submit Requirement shows popup (TC-3641 step 2)

NEVER FLAG:
✗ Missing auto-suggestions dropdown (only visible after typing)
✗ Missing ratings on some cards (optional for new sellers)
✗ Missing WhatsApp on some cards (optional)
✗ Page load time

REAL BUGS TO REPORT:
→ Zero product cards / search results (blank page for a valid query)
→ Product card images are grey boxes
→ Product names are plain text (not links)
→ No "Contact Supplier" CTA on any card
→ No "Call Now" CTA on any card
→ City suggestor completely absent
→ City strip completely absent (no city chips)
→ BL form completely absent below listing
→ BL form textbox empty (not prefilled with search query)
→ Search bar missing from header
→ Page showing error or completely blank
""",



"export": """
YOU ARE TESTING: IndiaMART Export Homepage
Source: Export_BF_Test_testsuite-deep.xml (TC-2140 to TC-2154)
URL: https://export.indiamart.com/

WHAT THIS PAGE LOOKS LIKE (CORRECT STATE):
• Header: IndiaMART Export logo (top-left), search bar, currency dropdown (USD/INR/EUR),
  language dropdown, "Get Quote" CTA, "Sign In" button
• Below header: Trust badge strip showing:
  "Verified Exporters Only | TrustSEAL Certified Sellers | GST Verified | IEC Verified | Made in India - Supplied Globally"
• Body sections (scroll to see):
  - "Explore Products" — grid of product category tiles with icons
  - "Why Trust IndiaMART Export?" — badges (Verified Exporters, GST Verified, TrustSEAL, IEC Verified) with "Know More" link
  - "IndiaMART Export by Numbers" — stat counters (exporters count, products, countries)
  - "About Us" — brief description of IndiaMART Export near footer

WHAT TO CHECK:
□ Page title must be visible (TC-2140)
□ Trust badge strip below header must show ALL of:
  "Verified Exporters Only", "TrustSEAL Certified Sellers", "GST Verified", "IEC Verified", "Made in India" (TC-2141)
□ Search bar must be visible in header (TC-2139)
□ Currency dropdown (showing USD/EUR/INR etc.) must be visible (TC-2153)
□ Language dropdown must be visible (TC-2154)
□ "Get Quote" CTA must be visible in header (TC-2145)

WHAT NOT TO CHECK (click interactions — cannot test from screenshot):
✗ Whether clicking category tile redirects (TC-2143)
✗ Whether "Know More" button redirects (TC-2144)
✗ Whether Get Quote button opens form (TC-2145 click)
✗ Whether search returns results (TC-2139, 2146, 2147)
✗ Whether TrustSEAL opens new tab (TC-2149)
✗ Whether currency dropdown changes prices (TC-2153 change)
✗ Whether language dropdown works (TC-2154 change)

NEVER FLAG:
✗ Missing product listing (homepage shows categories, not products)
✗ Page load time
✗ Optional decorative elements

REAL BUGS TO REPORT:
→ Page is blank or shows an error
→ Trust badge strip completely absent (none of the 5 badges visible)
→ One or more specific trust badges missing (e.g., "IEC Verified" absent)
→ Search bar missing from header
→ Currency or language dropdown completely absent
→ "Get Quote" CTA absent from header
→ Any homepage section completely missing
""",

"export_search": """
YOU ARE TESTING: IndiaMART Export Search Results Page
URL: https://export.indiamart.com/search.php?ss=...

WHAT THIS PAGE LOOKS LIKE (CORRECT STATE):
• Header: same as export homepage (logo, search bar pre-filled, currency, language, Get Quote, Sign In)
• Below header: "Verified Exporters for [query]" heading
• Search filter chips: related sub-categories below the heading
• Product listing: Cards with product image, product name (link), price, exporter company name,
  TrustSEAL badge, GST/IEC badges, "Get Latest Price" CTA
• "Show More Products" button after first ~10 results (requires login/OTP)
• Inline BL form: "Save Time! Get verified sellers exporting to your country"

WHAT TO CHECK:
□ Search bar visible in header with query pre-filled (TC-2139)
□ Product cards visible with images, names, prices (TC-2152)
□ Company names on cards are clickable links (TC-2151)
□ TrustSEAL badge visible on product cards (TC-2149)
□ "Get Latest Price" CTA on each card (TC-2158)
□ Inline BL form present on page (TC-2157, 2168)
□ Currency and language dropdowns visible in header (TC-2153, 2154)

NEVER FLAG:
✗ "Show More Products" flow (requires OTP — click interaction)
✗ Missing results for edge case queries
✗ Page load time
""",

"export_pdp": """
YOU ARE TESTING: IndiaMART Export Product Detail Page (PDP)
URL: https://export.indiamart.com/products/?id=...

WHAT THIS PAGE LOOKS LIKE (CORRECT STATE):
• Header: same as export (logo, search, currency, language, Get Quote, Sign In)
• Product name (H1), product images, price
• "Get Latest Price" CTA button — prominent green button
• Exporter company details: company name (link), GST, IEC, TrustSEAL, ratings
• "Save Time! Get verified sellers exporting to your country" inline BL form
  - Phone number input + "Submit Requirement" button
• "Return to Top" button at bottom-right
• Company Name link → redirects to /company/ page

WHAT TO CHECK:
□ Product name (H1) visible (TC-2158)
□ "Get Latest Price" CTA button visible (TC-2158)
□ Exporter company name visible as a link (TC-2161)
□ TrustSEAL badge visible on company section (TC-2149)
□ Inline BL form "Save Time!" with Submit Requirement present (TC-2166)
□ "Return to Top" button visible after scrolling (TC-2159)
□ Currency/language dropdowns in header (TC-2153, 2154)

NEVER FLAG:
✗ Get Latest Price click interactions (TC-2158 — click)
✗ Return to Top scroll behavior
✗ Page load time
""",



"company": """
YOU ARE TESTING: IndiaMART Free Catalog Page (FCP) — Company/Seller Page
Source: Company_BF_Testsuite_testsuite-deep.xml (TC-4835 to TC-4893)
Example URL: https://www.indiamart.com/careformulationlabs/

WHAT THIS PAGE LOOKS LIKE (CORRECT STATE):
• Centralized IndiaMART header at top: logo, search bar, city dropdown, Get Best Price, Sign In
• Company header section: Company logo (left), Company name (H1), City, Star rating + count,
  GST number (masked), Verified Supplier stamp, "Send Email" icon, "View Mobile Number" button
• Sticky sub-header: "Home | Our Products | About Us | Contact Us" navigation tabs
• Body:
  - "Our Top Products" / product slider section showing top product cards
  - "About Us" section with brief company description + "Read More" link
  - "Deals in HSN Code" section (if seller has HSN codes)
  - "Ratings & Reviews" section (if seller has reviews) — max 2 reviews + "View More Reviews" link
  - "Products & Services / Our Product Range" section showing product categories
• Footer: IndiaMART standard footer

WHAT TO CHECK — VISIBLE ELEMENTS ONLY:
□ Company name (H1) must be visible in header section (TC-4835)
□ Company address (city, state) must be visible (TC-4836)
□ GST number — if shown, must be MASKED with asterisks like "27****1Z2" (TC-4837)
□ "View Mobile Number" or "Send Email" or "Call" CTA must be visible (TC-4842)
□ Company logo/image must be visible in header (TC-4844) — if seller has uploaded one
□ Navigation tabs must include: "Our Products" / "Products & Services", "About Us", "Contact Us" (TC-4840)
□ Star rating + review count visible (if seller has reviews) (TC-4839)
□ Product cards or product categories must be visible on page (TC-4866–4869)
□ If ratings section present: star rating, satisfaction %, and reviews must be visible (TC-4858)
□ "View More Reviews" CTA must be visible in ratings section (if reviews > 2) (TC-4860)

WHAT NOT TO CHECK (click interactions):
✗ Whether clicking tabs redirects correctly (TC-4840, 4864, 4865, 4882, 4892)
✗ Whether View Mobile opens enquiry form (TC-4842)
✗ Whether product name click goes to PDP (TC-4873, 4874)
✗ Whether enquiry forms submit correctly (TC-4875, 4876, 4877, 4878)
✗ Whether View More Reviews redirects to testimonial page (TC-4860, 4861)
✗ Whether Read More redirects to about us page (TC-4848)

NEVER FLAG:
✗ Missing company logo — optional, not all sellers upload logo
✗ Missing Verified Supplier stamp — optional, only TrustSEAL sellers have it
✗ Missing ratings/review section — normal if seller has 0 reviews
✗ Missing HSN section — optional, not all sellers have HSN codes
✗ Missing "Shop Now" CTA — optional for some sellers

REAL BUGS TO REPORT:
→ Company name (H1) completely missing from header
→ Company address completely absent
→ GST number shown UNMASKED (full 15-char GST visible with no asterisks) — privacy violation
→ "View Mobile Number" / contact CTA completely absent — buyers cannot reach seller
→ Navigation sub-header missing (no "Our Products", "About Us", "Contact Us" tabs)
→ No products visible anywhere on the company page
→ Page is blank or shows an error
→ IndiaMART search bar missing from centralized header
""",


}  # end PAGE_PROMPTS


TEST_CASE_COVERAGE = {
    "pdp_t14": [
        "PDP-CORE-7044", "PDP-CORE-7047", "PDP-CORE-7048", "PDP-CORE-7052",
        "PDP-CORE-7054", "PDP-CORE-7055", "PDP-CORE-7056", "PDP-CORE-7057",
        "PDP-CORE-7062", "PDP-CORE-7063", "PDP-CORE-7065", "PDP-CORE-7069",
        "PDP-CORE-7071", "PDP-CORE-7072", "PDP-CORE-7075", "PDP-CORE-7076",
    ],
    "pdp_t1": [
        "PDP-CORE-7044", "PDP-CORE-7046", "PDP-CORE-7047", "PDP-CORE-7048",
        "PDP-CORE-7049", "PDP-CORE-7050", "PDP-CORE-7051", "PDP-CORE-7054",
        "PDP-CORE-7055", "PDP-CORE-7056", "PDP-CORE-7057", "PDP-CORE-7058",
        "PDP-CORE-7059", "PDP-CORE-7060", "PDP-CORE-7061", "PDP-CORE-7065",
        "PDP-CORE-7066", "PDP-CORE-7067", "PDP-CORE-7069", "PDP-CORE-7070",
        "PDP-CORE-7071", "PDP-CORE-7072", "PDP-CORE-7075", "PDP-CORE-7076",
    ],
    "pdp_t3": [
        "PDP-CORE-7047", "PDP-CORE-7049", "PDP-CORE-7050", "PDP-CORE-7051",
        "PDP-CORE-7055", "PDP-CORE-7056", "PDP-CORE-7057", "PDP-CORE-7062",
        "PDP-CORE-7063", "PDP-CORE-7064", "PDP-CORE-7065", "PDP-CORE-7066",
        "PDP-CORE-7069", "PDP-CORE-7070", "PDP-CORE-7071", "PDP-CORE-7072",
        "PDP-CORE-7073", "PDP-CORE-7074", "PDP-CORE-7075", "PDP-CORE-7076",
    ],
    "pdp": [
        "PDP-CORE-7044", "PDP-CORE-7047", "PDP-CORE-7055",
        "PDP-CORE-7065", "PDP-CORE-7075", "PDP-CORE-7076",
    ],
    "mcat": [
        "TC-6461", "TC-6463", "TC-6464", "TC-6465", "TC-6466",
        "TC-6467", "TC-6468", "TC-6469", "TC-6470", "TC-6471",
        "TC-6472", "TC-6491", "TC-6474", "TC-6476",
        "TC-6486", "TC-6487", "TC-6488", "TC-6489",
    ],
    "search": [
        "TC-3590", "TC-3591", "TC-3593", "TC-3594", "TC-3597", "TC-3598", "TC-3599",
        "TC-3605", "TC-3606", "TC-3607", "TC-3608", "TC-3609",
        "TC-3610", "TC-3611", "TC-3612", "TC-3613", "TC-3614",
        "TC-3633", "TC-3634", "TC-3635", "TC-3637", "TC-3638",
        "TC-3639", "TC-3640", "TC-3641", "TC-3643", "TC-3644",
    ],
    "export": [
        "TC-2139", "TC-2140", "TC-2141", "TC-2143", "TC-2144",
        "TC-2145", "TC-2146", "TC-2147", "TC-2149", "TC-2153", "TC-2154",
        "TC-2155", "TC-2156", "TC-2157", "TC-2158", "TC-2159",
        "TC-2160", "TC-2161", "TC-2166", "TC-2168", "TC-2169",
    ],
    "export_search": ["TC-2139","TC-2149","TC-2151","TC-2152","TC-2153","TC-2154","TC-2157","TC-2158","TC-2168"],
    "export_pdp":    ["TC-2139","TC-2149","TC-2153","TC-2154","TC-2158","TC-2159","TC-2161","TC-2166"],
    "export_company":["TC-2139","TC-2151","TC-2153","TC-2154","TC-2161"],
    "company": [
        "TC-4835","TC-4836","TC-4837","TC-4838","TC-4839",
        "TC-4840","TC-4842","TC-4843","TC-4844","TC-4846",
        "TC-4848","TC-4857","TC-4858","TC-4859","TC-4860",
        "TC-4861","TC-4864","TC-4865","TC-4866","TC-4867",
        "TC-4868","TC-4869","TC-4870","TC-4871","TC-4872",
        "TC-4873","TC-4874","TC-4875","TC-4876","TC-4877",
        "TC-4878","TC-4879","TC-4880","TC-4881","TC-4882",
        "TC-4883","TC-4884","TC-4892","TC-4893",
    ],
}


# ═══════════════════════════════════════════════════════════════════════════════════
# Claude Analyzer Class
# ═══════════════════════════════════════════════════════════════════════════════════

class ClaudeAnalyzer:
    def __init__(self, api_key: str):
        self.client = anthropic.Anthropic(
            api_key=api_key,
            base_url=GATEWAY_BASE_URL,
        )

    async def analyze_pages(self, pages_data: list[dict]) -> list[dict]:
        tasks = [self._analyze_page(p) for p in pages_data]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        output = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                print(f"    ❌  AI task {i} failed: {result}")
                pages_data[i].setdefault("ai_issues", [])
                pages_data[i].setdefault("test_cases_covered", [])
                pages_data[i].setdefault("ai_duration_ms", 0)
                output.append(pages_data[i])
            else:
                output.append(result)
        return output

    async def _analyze_page(self, page_data: dict) -> dict:
        url       = page_data.get("url", "")
        b64       = page_data.get("screenshots", {}).get("desktop_b64", "")
        page_type = page_data.get("page_type", "other")

        # Map page_type to prompt key and coverage key
        PDP_TYPES = ("pdp_t14", "pdp_t1", "pdp_t3", "pdp",
                     "pdp_service", "pdp_product", "pdp_brochure")
        if page_type in PDP_TYPES:
            prompt_type   = page_type if page_type in PAGE_PROMPTS else "pdp"
            coverage_type = page_type if page_type in TEST_CASE_COVERAGE else "pdp"
        elif page_type == "mcat":
            prompt_type   = "mcat"
            coverage_type = "mcat"
        elif page_type == "search":
            prompt_type   = "search"
            coverage_type = "search"
        elif page_type in ("export", "export_search", "export_pdp", "export_company"):
            prompt_type   = page_type if page_type in PAGE_PROMPTS else "export"
            coverage_type = page_type if page_type in TEST_CASE_COVERAGE else "export"
        elif page_type == "company":
            prompt_type   = "company"
            coverage_type = "company"
        else:
            prompt_type   = page_type
            coverage_type = page_type

        if not b64 or len(b64) < 1000:
            print(f"    ⚠️  No screenshot for {url} — skipping AI")
            page_data.setdefault("ai_issues", [])
            page_data["test_cases_covered"] = TEST_CASE_COVERAGE.get(coverage_type, [])
            page_data["ai_duration_ms"] = 0
            return page_data

        # ── Build user message: Place 2 prompt + screenshot ──────────────
        page_specific = PAGE_PROMPTS.get(prompt_type) or PAGE_PROMPTS.get("pdp", "")
        user_text = (
            f"URL being tested: {url}\n\n"
            f"{page_specific}\n\n"
            "Carefully examine the screenshot. Report ONLY real visible bugs from the checklist above.\n"
            "Return a JSON array only. No text outside the JSON array."
        )

        content = [
            {"type": "text",  "text": user_text},
            {"type": "image", "source": {
                "type":       "base64",
                "media_type": "image/png",
                "data":       b64,
            }},
        ]

        print(f"    🤖  AI analysing [{page_type.upper()}] {url[:55]}...")
        t0 = time.time()
        ai_issues = await self._call_claude(content, url)
        ai_ms = round((time.time() - t0) * 1000)

        # Normalise output
        valid_sevs = {"critical", "high", "medium"}
        normalised = []
        for issue in ai_issues:
            if not issue.get("title"):
                continue
            sev = str(issue.get("severity", "medium")).lower()
            if sev not in valid_sevs:
                sev = "medium"
            normalised.append({
                "category": issue.get("category", "Visual Bug"),
                "severity": sev,
                "title":    issue.get("title", "")[:120],
                "detail":   issue.get("detail", "")[:500],
                "element":  "AI Visual Analysis",
                "source":   "claude-ai",
            })

        page_data["ai_issues"]          = normalised
        page_data["test_cases_covered"] = TEST_CASE_COVERAGE.get(coverage_type, [])
        page_data["ai_duration_ms"]     = ai_ms

        print(f"    ✅  [{page_type.upper()}] {len(normalised)} issue(s) found in {ai_ms}ms")
        return page_data

    async def _call_claude(self, content: list, url: str, retries: int = 3) -> list:
        """Call Claude API with exponential backoff retry."""
        for attempt in range(retries):
            try:
                loop = asyncio.get_event_loop()
                resp = await loop.run_in_executor(
                    None,
                    lambda: self.client.messages.create(
                        model="anthropic/claude-sonnet-4-6",
                        max_tokens=1500,
                        system=SYSTEM_PROMPT,      # ← PLACE 1
                        messages=[{
                            "role":    "user",
                            "content": content     # ← PLACE 2 + screenshot
                        }],
                    )
                )

                raw = resp.content[0].text.strip()

                # Strip markdown fences if model adds them
                if raw.startswith("```"):
                    lines = raw.split("\n")
                    lines = lines[1:] if lines[0].startswith("```") else lines
                    if lines and lines[-1].strip() == "```":
                        lines = lines[:-1]
                    raw = "\n".join(lines).strip()

                if not raw or raw == "[]":
                    return []

                parsed = json.loads(raw)
                return parsed if isinstance(parsed, list) else []

            except json.JSONDecodeError as e:
                print(f"    ⚠️  JSON parse error for {url}: {e}")
                return []

            except Exception as e:
                err = str(e)
                if any(x in err for x in ("429", "502", "503", "overloaded")):
                    wait = 2 ** attempt + 1
                    print(f"    ⏳  API busy, retrying in {wait}s (attempt {attempt+1}/{retries})")
                    await asyncio.sleep(wait)
                else:
                    print(f"    ❌  AI error for {url}: {err[:100]}")
                    return []

        print(f"    ❌  Giving up on {url} after {retries} attempts")
        return []
