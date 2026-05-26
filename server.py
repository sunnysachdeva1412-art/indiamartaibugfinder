"""
server.py — IndiaMART Bug Finder v5
Flask web server. Run: python server.py → http://localhost:5000
"""

import asyncio, base64, os, sys, threading, time, uuid
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, request, send_file

sys.path.insert(0, str(Path(__file__).parent))
from crawler_playwright import EnhancedCrawler
from analyzer_claude import ClaudeAnalyzer
from reporter import ReportGenerator

app = Flask(__name__)
jobs: dict = {}
lock = threading.Lock()
OUT_BASE = Path("web_output")
OUT_BASE.mkdir(exist_ok=True)

# ── Load IndiaMART logo ──────────────────────────────────────────────────────
_LOGO_B64  = ""
_LOGO_MIME = "image/jpeg"
try:
    for _logo_name in ["download__3_.jpeg", "download.png"]:
        _lp = Path(__file__).parent / _logo_name
        if _lp.exists():
            _LOGO_B64  = base64.b64encode(_lp.read_bytes()).decode()
            _LOGO_MIME = "image/jpeg" if _logo_name.endswith(".jpeg") else "image/png"
            break
except Exception:
    pass

_LOGO_SRC = f"data:{_LOGO_MIME};base64,{_LOGO_B64}" if _LOGO_B64 else ""

# ── Agent runner ─────────────────────────────────────────────────────────────

def run_agent(job_id, url, depth, max_pages, skip_ai, api_key, page_type_hint="auto"):
    def log(msg):
        with lock:
            jobs[job_id]["logs"].append({
                "t": datetime.now().strftime("%H:%M:%S"), "msg": msg
            })
    def status(s):
        with lock:
            jobs[job_id]["status"] = s

    try:
        out_dir = OUT_BASE / job_id
        (out_dir / "screenshots").mkdir(parents=True, exist_ok=True)
        t0 = time.time()

        status("crawling")
        log(f"🌐 Starting crawl: {url}")
        log(f"   Depth: {depth} | Max pages: {max_pages}")
        if page_type_hint and page_type_hint != "auto":
            log(f"   Page type hint: {page_type_hint.upper()}")

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        crawler = EnhancedCrawler(str(out_dir), headless=True, max_pages=max_pages,
                                  page_type_hint=page_type_hint)
        pages = loop.run_until_complete(crawler.crawl(url, depth=depth))
        log(f"✅ Crawled {len(pages)} page(s)")

        for p in pages:
            cnt = len(p.get("issues", []))
            ptype = p.get("page_type", "?")
            log(f"   [{ptype}] {p.get('url','')[:65]} — {cnt} issues")

        if not skip_ai and api_key:
            status("analyzing")
            log("🤖 Claude AI visual analysis started...")
            analyzer = ClaudeAnalyzer(api_key)
            pages = loop.run_until_complete(analyzer.analyze_pages(pages))
            ai_cnt = sum(len(p.get("ai_issues", [])) for p in pages)
            log(f"✅ AI done — {ai_cnt} visual issue(s) found")
        else:
            log("⏭️  AI analysis skipped")
            for p in pages:
                p.setdefault("ai_issues", [])

        status("reporting")
        log("📄 Generating report...")
        elapsed = round(time.time() - t0, 1)
        meta = {
            "url": url, "depth": depth,
            "pages_crawled": len(pages),
            "crawl_duration_s": elapsed,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        reporter = ReportGenerator(str(out_dir))
        report_path = reporter.generate(pages, meta)

        total = sum(len(p.get("issues", [])) + len(p.get("ai_issues", [])) for p in pages)
        crit = sum(1 for p in pages
                   for i in (p.get("issues",[]) + p.get("ai_issues",[]))
                   if str(i.get("severity","")).lower() == "critical")

        log(f"✅ Report ready — {total} issues ({crit} critical) in {elapsed}s")

        with lock:
            jobs[job_id].update({
                "status": "done",
                "report_path": str(report_path),
                "total_issues": total,
                "critical_issues": crit,
                "elapsed": elapsed,
                "pages_crawled": len(pages),
            })
        loop.close()

    except Exception as e:
        with lock:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["error"] = str(e)
        log(f"❌ Error: {e}")


# ── API routes ───────────────────────────────────────────────────────────────

@app.route("/api/run", methods=["POST"])
def api_run():
    d = request.json or {}
    url = (d.get("url") or "").strip()
    if not url: return jsonify({"error": "URL required"}), 400
    if not url.startswith("http"): url = "https://" + url
    depth     = min(int(d.get("depth", 1)), 3)
    max_pages = min(int(d.get("max_pages", 5)), 20)
    skip_ai        = bool(d.get("skip_ai", False))
    api_key        = d.get("api_key") or os.environ.get("ANTHROPIC_API_KEY", "")
    page_type_hint = d.get("page_type_hint", "auto")
    job_id    = str(uuid.uuid4())[:8]
    with lock:
        jobs[job_id] = {
            "status": "queued", "url": url, "logs": [],
            "report_path": None, "created_at": datetime.now().isoformat(),
        }
    threading.Thread(
        target=run_agent,
        args=(job_id, url, depth, max_pages, skip_ai, api_key, page_type_hint),
        daemon=True
    ).start()
    return jsonify({"job_id": job_id})

@app.route("/api/status/<job_id>")
def api_status(job_id):
    with lock: job = jobs.get(job_id)
    return jsonify(job) if job else (jsonify({"error": "Not found"}), 404)

@app.route("/api/report/<job_id>")
def api_report(job_id):
    with lock: job = jobs.get(job_id)
    if not job or job.get("status") != "done":
        return jsonify({"error": "Not ready"}), 404
    p = Path(job["report_path"])
    return send_file(str(p), mimetype="text/html") if p.exists() \
        else (jsonify({"error": "File missing"}), 404)

@app.route("/api/jobs")
def api_jobs():
    with lock:
        return jsonify([
            {"job_id": jid, "url": j.get("url", ""), "status": j.get("status", ""),
             "created_at": j.get("created_at", ""), "total_issues": j.get("total_issues"),
             "critical_issues": j.get("critical_issues"), "elapsed": j.get("elapsed"),
             "pages_crawled": j.get("pages_crawled")}
            for jid, j in sorted(jobs.items(), key=lambda x: x[1].get("created_at",""), reverse=True)
        ])

@app.route("/")
def index(): return FRONTEND_HTML

# ── Frontend HTML ─────────────────────────────────────────────────────────────

_LOGO_HTML = (
    f'<img class="s-logo" src="{_LOGO_SRC}" alt="IndiaMART">'
    if _LOGO_SRC else '<span class="s-logo-text">IndiaMART</span>'
)

FRONTEND_HTML = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>IndiaMART Bug Finder AI Agent</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
:root{{
  --red:#c0392b;--red-d:#a93226;--red-bg:#fdf3f2;--red-lt:#e8d5d3;
  --white:#fff;--gray-50:#f8f9fa;--gray-100:#f1f3f5;--gray-200:#e9ecef;
  --gray-300:#dee2e6;--gray-400:#ced4da;--gray-500:#adb5bd;
  --gray-600:#6c757d;--gray-700:#495057;--gray-800:#343a40;--gray-900:#212529;
  --green:#1a7f4b;--green-bg:#eaf5ef;--amber:#92600a;--amber-bg:#fef5e7;
  --blue:#1a56a0;--blue-bg:#ebf2fb;
  --radius:6px;--radius-lg:10px;
}}
body{{font-family:'Inter',sans-serif;background:var(--gray-100);color:var(--gray-800);font-size:13px;line-height:1.5;min-height:100vh}}
::-webkit-scrollbar{{width:6px;height:6px}}
::-webkit-scrollbar-track{{background:var(--gray-100)}}
::-webkit-scrollbar-thumb{{background:var(--gray-300);border-radius:3px}}

/* LAYOUT */
.app{{display:flex;min-height:100vh}}
.sidebar{{width:300px;flex-shrink:0;background:var(--white);border-right:1px solid var(--gray-200);display:flex;flex-direction:column;position:fixed;top:0;left:0;bottom:0;overflow-y:auto;z-index:100}}
.main{{margin-left:300px;flex:1;display:flex;flex-direction:column;min-height:100vh}}

/* SIDEBAR HEADER */
.s-header{{border-bottom:1px solid var(--gray-200)}}
.s-logo-bar{{display:flex;align-items:center;gap:12px;padding:14px 16px;border-bottom:3px solid var(--red);background:var(--white)}}
.s-logo{{height:72px;width:auto;object-fit:contain;flex-shrink:0}}
.s-logo-text{{font-size:18px;font-weight:800;color:var(--red);letter-spacing:-.5px}}
.s-divider{{width:1px;height:36px;background:var(--gray-200);flex-shrink:0}}
.s-title{{font-size:12px;font-weight:700;color:var(--gray-800)}}
.s-subtitle{{font-size:10px;color:var(--gray-500);margin-top:1px}}
.s-badges{{display:flex;gap:5px;flex-wrap:wrap;padding:8px 16px 10px}}
.badge{{font-size:10px;font-weight:600;padding:2px 8px;border-radius:3px;letter-spacing:.3px}}
.b-red{{background:var(--red-bg);color:var(--red);border:1px solid var(--red-lt)}}
.b-blue{{background:var(--blue-bg);color:var(--blue);border:1px solid #c3d9f4}}
.b-green{{background:var(--green-bg);color:var(--green);border:1px solid #b9deca}}

/* FORM */
.s-section{{padding:14px 16px;border-bottom:1px solid var(--gray-200)}}
.s-section-title{{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--gray-500);margin-bottom:12px}}
.form-group{{margin-bottom:11px}}
.form-label{{display:block;font-size:11px;font-weight:600;color:var(--gray-700);margin-bottom:4px}}
.form-input,.form-select{{width:100%;padding:7px 10px;background:var(--white);border:1px solid var(--gray-300);border-radius:var(--radius);color:var(--gray-800);font-size:12px;font-family:'Inter',sans-serif;outline:none;transition:border-color .15s,box-shadow .15s}}
.form-input:focus,.form-select:focus{{border-color:var(--red);box-shadow:0 0 0 3px rgba(192,57,43,.1)}}
.form-input::placeholder{{color:var(--gray-400)}}
.mono{{font-family:'JetBrains Mono',monospace;font-size:11px}}

/* INFO BOX */
.info-box{{background:var(--green-bg);border:1px solid #b9deca;border-radius:var(--radius);padding:8px 10px;margin-bottom:12px;font-size:11px;color:var(--green)}}

/* TOGGLE */
.toggle-row{{display:flex;align-items:center;justify-content:space-between;margin-bottom:13px;font-size:12px;color:var(--gray-600)}}
.toggle{{position:relative;width:36px;height:20px}}
.toggle input{{opacity:0;width:0;height:0;position:absolute}}
.toggle-track{{position:absolute;inset:0;background:var(--gray-300);border-radius:20px;cursor:pointer;transition:.2s}}
.toggle-track::before{{content:'';position:absolute;width:14px;height:14px;left:3px;top:3px;background:var(--white);border-radius:50%;transition:.2s;box-shadow:0 1px 3px rgba(0,0,0,.2)}}
input:checked+.toggle-track{{background:var(--red)}}
input:checked+.toggle-track::before{{transform:translateX(16px)}}

/* RUN BUTTON */
.run-btn{{width:100%;padding:10px 16px;background:var(--red);color:var(--white);border:none;border-radius:var(--radius);font-size:13px;font-weight:600;font-family:'Inter',sans-serif;cursor:pointer;transition:background .15s;display:flex;align-items:center;justify-content:center;gap:7px;box-shadow:0 2px 6px rgba(192,57,43,.3)}}
.run-btn:hover:not(:disabled){{background:var(--red-d)}}
.run-btn:disabled{{opacity:.55;cursor:not-allowed}}

/* HISTORY */
.hist-empty{{text-align:center;padding:20px 0;color:var(--gray-400);font-size:12px;line-height:1.8}}
.hist-item{{padding:9px 11px;border-radius:var(--radius);border:1px solid var(--gray-200);cursor:pointer;transition:all .15s;margin-bottom:5px;background:var(--white)}}
.hist-item:hover,.hist-item.active{{border-color:var(--red);background:var(--red-bg)}}
.hist-url{{font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--gray-600);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-bottom:4px}}
.hist-meta{{display:flex;align-items:center;gap:6px}}
.sp{{font-size:10px;font-weight:600;padding:1px 7px;border-radius:3px}}
.sp-running{{background:var(--amber-bg);color:var(--amber)}}
.sp-done{{background:var(--green-bg);color:var(--green)}}
.sp-error{{background:var(--red-bg);color:var(--red)}}
.hist-info{{font-size:10px;color:var(--gray-400);margin-left:auto}}

/* TOPBAR */
.topbar{{background:var(--white);border-bottom:1px solid var(--gray-200);padding:14px 28px;display:flex;align-items:center;justify-content:space-between;gap:16px;position:sticky;top:0;z-index:50;box-shadow:0 1px 3px rgba(0,0,0,.06)}}
.topbar-label{{font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:1px;color:var(--gray-400);margin-bottom:2px}}
.topbar-title{{font-size:16px;font-weight:700;color:var(--gray-900)}}
.topbar-url{{font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--gray-500);margin-top:2px}}
.topbar-right{{display:flex;gap:8px;flex-shrink:0}}
.top-btn{{padding:7px 14px;border-radius:var(--radius);font-size:12px;font-weight:600;font-family:'Inter',sans-serif;cursor:pointer;transition:all .15s;display:flex;align-items:center;gap:5px}}
.tb-outline{{background:var(--white);border:1px solid var(--gray-300);color:var(--gray-700)}}
.tb-outline:hover{{border-color:var(--red);color:var(--red)}}
.tb-solid{{background:var(--red);border:1px solid var(--red);color:var(--white)}}
.tb-solid:hover{{background:var(--red-d)}}

/* CONTENT */
.content{{flex:1;padding:24px 28px;display:flex;flex-direction:column;gap:20px}}

/* WELCOME */
.welcome{{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:40px 24px;text-align:center;gap:18px;min-height:60vh}}
.welcome-logo{{height:80px;width:auto;margin-bottom:4px}}
.welcome-logo-text{{font-size:36px;font-weight:800;color:var(--red)}}
.welcome h2{{font-size:21px;font-weight:700;color:var(--gray-900);letter-spacing:-.3px}}
.welcome-sub{{font-size:13px;color:var(--gray-600);max-width:500px;line-height:1.75}}
.feature-list{{background:var(--white);border:1px solid var(--gray-200);border-radius:var(--radius-lg);padding:16px 20px;max-width:500px;text-align:left;width:100%}}
.feature-list h4{{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--gray-500);margin-bottom:10px}}
.fl-item{{display:flex;align-items:flex-start;gap:8px;font-size:12px;color:var(--gray-700);margin-bottom:6px;line-height:1.4}}
.fl-item:last-child{{margin-bottom:0}}
.welcome-steps{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;width:100%;max-width:680px;margin-top:8px}}
.step-card{{background:var(--white);border:1px solid var(--gray-200);border-radius:var(--radius-lg);padding:16px 12px;text-align:center;transition:border-color .2s}}
.step-card:hover{{border-color:var(--red)}}
.step-num{{width:26px;height:26px;border-radius:50%;background:var(--red);color:var(--white);font-size:11px;font-weight:700;display:flex;align-items:center;justify-content:center;margin:0 auto 8px}}
.step-icon{{font-size:18px;margin-bottom:6px}}
.step-label{{font-size:11px;color:var(--gray-600);line-height:1.5}}

/* PROGRESS */
.progress-card{{background:var(--white);border:1px solid var(--gray-200);border-radius:var(--radius-lg);overflow:hidden}}
.progress-header{{padding:12px 18px;background:var(--gray-50);border-bottom:1px solid var(--gray-200);display:flex;align-items:center;gap:12px;flex-wrap:wrap}}
.progress-tag{{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--gray-500);flex-shrink:0}}
.progress-url-text{{font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--gray-600);flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.phase-row{{display:flex;gap:5px}}
.phase{{font-size:10px;font-weight:600;padding:3px 10px;border-radius:3px;background:var(--gray-100);color:var(--gray-400);transition:all .25s;border:1px solid var(--gray-200)}}
.phase.active{{background:#ebf2fb;color:var(--blue);border-color:#c3d9f4}}
.phase.done{{background:var(--green-bg);color:var(--green);border-color:#b9deca}}
.progress-track{{height:3px;background:var(--gray-200)}}
.progress-fill{{height:100%;width:0%;background:var(--red);transition:width .5s ease}}
.log-area{{padding:14px 18px;max-height:260px;overflow-y:auto;background:var(--gray-900);font-family:'JetBrains Mono',monospace;font-size:11px;color:#a8b8c8;line-height:1.9}}
.log-line{{display:flex;gap:12px}}
.log-time{{color:#4a5568;flex-shrink:0}}

/* STAT CARDS */
.stat-row{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}}
.stat-card{{background:var(--white);border:1px solid var(--gray-200);border-radius:var(--radius-lg);padding:18px 16px;position:relative;overflow:hidden}}
.stat-card::after{{content:'';position:absolute;left:0;top:0;bottom:0;width:3px}}
.sc-red::after{{background:var(--red)}}
.sc-blue::after{{background:var(--blue)}}
.sc-amber::after{{background:#d97706}}
.sc-green::after{{background:var(--green)}}
.stat-label{{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.8px;color:var(--gray-500);margin-bottom:8px}}
.stat-value{{font-size:32px;font-weight:700;line-height:1;letter-spacing:-1px}}
.sc-red .stat-value{{color:var(--red)}}
.sc-blue .stat-value{{color:var(--blue)}}
.sc-amber .stat-value{{color:#d97706}}
.sc-green .stat-value{{color:var(--green)}}
.stat-desc{{font-size:11px;color:var(--gray-400);margin-top:6px}}

/* REPORT */
.report-card{{background:var(--white);border:1px solid var(--gray-200);border-radius:var(--radius-lg);overflow:hidden}}
.report-header{{padding:12px 18px;background:var(--gray-50);border-bottom:1px solid var(--gray-200);display:flex;align-items:center;justify-content:space-between}}
.report-title{{font-size:13px;font-weight:600;color:var(--gray-800);display:flex;align-items:center;gap:8px}}
.live-dot{{width:7px;height:7px;border-radius:50%;background:var(--green);box-shadow:0 0 6px var(--green)}}
.report-actions{{display:flex;gap:7px}}
.r-btn{{padding:6px 13px;border-radius:var(--radius);font-size:11px;font-weight:600;font-family:'Inter',sans-serif;cursor:pointer;transition:all .15s;text-decoration:none;display:inline-flex;align-items:center;gap:4px}}
.r-ghost{{background:var(--white);border:1px solid var(--gray-300);color:var(--gray-600)}}
.r-ghost:hover{{border-color:var(--red);color:var(--red)}}
.r-solid{{background:var(--red);border:1px solid var(--red);color:var(--white)}}
.r-solid:hover{{background:var(--red-d)}}
iframe#rif{{width:100%;border:none;height:calc(100vh - 300px);min-height:500px}}

/* UTILS */
.hidden{{display:none!important}}
@keyframes spin{{to{{transform:rotate(360deg)}}}}
.spinner{{width:14px;height:14px;border:2px solid rgba(255,255,255,.3);border-top-color:#fff;border-radius:50%;animation:spin .6s linear infinite}}
@keyframes fadeIn{{from{{opacity:0;transform:translateY(6px)}}to{{opacity:1;transform:translateY(0)}}}}
.fade-in{{animation:fadeIn .3s ease both}}
</style>
</head>
<body>
<div class="app">

<aside class="sidebar">
  <div class="s-header">
    <div class="s-logo-bar">
      {_LOGO_HTML}
      <div class="s-divider"></div>
      <div>
        <div class="s-title">Bug Finder AI Agent</div>
        <div class="s-subtitle">Real QA · Functional &amp; UI</div>
      </div>
    </div>
    <div class="s-badges">
      <span class="badge b-red">AI Vision</span>
      <span class="badge b-blue">Web Vitals</span>
      <span class="badge b-green">Screenshots</span>
    </div>
  </div>

  <div class="s-section">
    <div class="s-section-title">New Scan</div>
    <div class="info-box">🎯 Finds real bugs only — no SEO noise, no false positives</div>

    <div class="form-group">
      <label class="form-label">Target URL</label>
      <input id="urlInput" class="form-input mono" placeholder="https://www.indiamart.com/…" type="url">
    </div>
    <div class="form-group">
      <label class="form-label">Page Type</label>
      <select id="pageTypeSel" class="form-select">
        <option value="auto" selected>Auto-detect</option>
        <option value="pdp">PDP — Product Detail Page</option>
        <option value="mcat">IMPCAT / MCAT — Category Page</option>
        <option value="search">Search — Search Results Page</option>
        <option value="export">Export — export.indiamart.com</option>
        <option value="company">Company — Seller FCP Page</option>
      </select>
    </div>
    <div class="form-group">
      <label class="form-label">Crawl Depth</label>
      <select id="depthSel" class="form-select">
        <option value="1" selected>1 — This URL only</option>
        <option value="2">2 — URL + linked pages</option>
        <option value="3">3 — Deep crawl</option>
      </select>
    </div>
    <div class="form-group">
      <label class="form-label">Max Pages</label>
      <select id="pagesSel" class="form-select">
        <option value="1">1 page</option>
        <option value="3">3 pages</option>
        <option value="5" selected>5 pages</option>
        <option value="10">10 pages</option>
      </select>
    </div>
    <div class="form-group">
      <label class="form-label">Anthropic API Key <span style="color:var(--gray-400);font-weight:400">(for AI vision)</span></label>
      <input id="apiKey" class="form-input mono" placeholder="sk-ant-…" type="password">
    </div>
    <div class="toggle-row">
      <span>Skip AI Vision</span>
      <label class="toggle"><input type="checkbox" id="skipAi"><span class="toggle-track"></span></label>
    </div>
    <button class="run-btn" id="runBtn" onclick="startScan()">
      <span id="runIcon">&#9654;</span>
      <span id="runTxt">Run Scan</span>
    </button>
  </div>

  <div class="s-section" style="flex:1;overflow-y:auto">
    <div class="s-section-title">Scan History</div>
    <div id="histList"><div class="hist-empty">No scans yet.<br>Enter a URL and click Run Scan.</div></div>
  </div>
</aside>

<main class="main">
  <div class="topbar">
    <div>
      <div class="topbar-label">IndiaMART Bug Finder AI Agent v5</div>
      <div class="topbar-title" id="tbTitle">Ready</div>
      <div class="topbar-url" id="tbSub">Enter a URL in the sidebar to begin</div>
    </div>
    <div class="topbar-right">
      <button class="top-btn tb-outline hidden" id="openBtn" onclick="openTab()">&#8599; Open Report</button>
      <button class="top-btn tb-solid hidden" id="dlBtn" onclick="dlReport()">&#8595; Download</button>
    </div>
  </div>

  <div class="content" id="mainContent">

    <div class="welcome" id="welcomeEl">
      {f'<img class="welcome-logo" src="{_LOGO_SRC}" alt="IndiaMART">' if _LOGO_SRC else '<div class="welcome-logo-text">IndiaMART</div>'}
      <h2>Bug Finder AI Agent</h2>
      <p class="welcome-sub">Enter any IndiaMART URL — the agent auto-detects the page type (PDP, MCAT, Search, Company, Export), runs targeted checks from 1,470+ real test cases, and generates a professional QA report with screenshots and Web Vitals.</p>
      <div class="feature-list">
        <h4>What gets checked</h4>
        <div class="fl-item">📦 <span><strong>PDP:</strong> Product name, images, price, Enquiry CTA, View Mobile, trust signals, breadcrumbs</span></div>
        <div class="fl-item">📂 <span><strong>MCAT:</strong> Product listing cards, filters panel, BL form, city filter, breadcrumbs</span></div>
        <div class="fl-item">🔍 <span><strong>Search:</strong> Results count, Contact Supplier CTA, city filter, clickable product names</span></div>
        <div class="fl-item">🏢 <span><strong>Company:</strong> Company name, nav tabs, products, Contact CTA, trust seal, address</span></div>
        <div class="fl-item">🌍 <span><strong>Export:</strong> Results, Contact CTA, currency/language selector</span></div>
        <div class="fl-item">🤖 <span><strong>AI Vision:</strong> Claude analyses screenshots for visual bugs not detectable by code</span></div>
      </div>
      <div class="welcome-steps">
        <div class="step-card"><div class="step-icon">&#128279;</div><div class="step-num">1</div><div class="step-label">Paste IndiaMART URL</div></div>
        <div class="step-card"><div class="step-icon">&#128375;</div><div class="step-num">2</div><div class="step-label">Agent crawls &amp; checks</div></div>
        <div class="step-card"><div class="step-icon">&#129302;</div><div class="step-num">3</div><div class="step-label">Claude AI analyses screenshot</div></div>
        <div class="step-card"><div class="step-icon">&#128202;</div><div class="step-num">4</div><div class="step-label">Professional QA report</div></div>
      </div>
    </div>

    <div class="hidden" id="progEl">
      <div class="progress-card fade-in">
        <div class="progress-header">
          <span class="progress-tag">Scanning</span>
          <span class="progress-url-text" id="progUrl"></span>
          <div class="phase-row">
            <span class="phase" id="ph-crawling">Crawl</span>
            <span class="phase" id="ph-analyzing">AI</span>
            <span class="phase" id="ph-reporting">Report</span>
          </div>
        </div>
        <div class="progress-track"><div class="progress-fill" id="progBar"></div></div>
        <div class="log-area" id="logBox"></div>
      </div>
    </div>

    <div class="stat-row hidden fade-in" id="statsEl">
      <div class="stat-card sc-red"><div class="stat-label">Issues Found</div><div class="stat-value" id="s-issues">—</div><div class="stat-desc">Functional &amp; UI bugs</div></div>
      <div class="stat-card sc-blue"><div class="stat-label">Pages Scanned</div><div class="stat-value" id="s-pages">—</div><div class="stat-desc">Full page analysis</div></div>
      <div class="stat-card sc-amber"><div class="stat-label">Critical Issues</div><div class="stat-value" id="s-crit">—</div><div class="stat-desc">Fix immediately</div></div>
      <div class="stat-card sc-green"><div class="stat-label">Scan Time</div><div class="stat-value" id="s-time">—</div><div class="stat-desc">Seconds elapsed</div></div>
    </div>

    <div class="report-card hidden fade-in" id="rfEl">
      <div class="report-header">
        <div class="report-title"><span class="live-dot"></span>QA Report — IndiaMART Bug Finder v5</div>
        <div class="report-actions">
          <button class="r-btn r-ghost" onclick="openTab()">&#8599; New Tab</button>
          <a class="r-btn r-solid" id="rfLink" href="#" target="_blank">&#128203; Full Report</a>
        </div>
      </div>
      <iframe id="rif" src="about:blank"></iframe>
    </div>

  </div>
</main>
</div>

<script>
let curJob=null, poll=null, jobs={{}};

async function startScan(){{
  const url=document.getElementById('urlInput').value.trim();
  if(!url){{shakeUrl();return;}}
  setBusy(true);
  hide('welcomeEl');show('progEl');hide('statsEl');hide('rfEl');hide('openBtn');hide('dlBtn');
  document.getElementById('progUrl').textContent=url;
  document.getElementById('logBox').innerHTML='';
  document.getElementById('progBar').style.width='3%';
  setPhase('queued'); setTB('Scanning…',url);
  try{{
    const r=await fetch('/api/run',{{method:'POST',headers:{{'Content-Type':'application/json'}},
      body:JSON.stringify({{url,depth:+document.getElementById('depthSel').value,
        max_pages:+document.getElementById('pagesSel').value,
        skip_ai:document.getElementById('skipAi').checked,
        api_key:document.getElementById('apiKey').value.trim(),
        page_type_hint:document.getElementById('pageTypeSel').value}})}});
    const d=await r.json();
    if(d.error){{alert(d.error);setBusy(false);return;}}
    curJob=d.job_id; jobs[curJob]={{url,status:'queued'}};
    renderHist();
    poll=setInterval(()=>pollJob(curJob),1500);
  }}catch(e){{alert('Server error: '+e.message);setBusy(false);}}
}}

async function pollJob(id){{
  try{{
    const r=await fetch('/api/status/'+id);
    const j=await r.json();
    jobs[id]=j;
    const logEl=document.getElementById('logBox');
    logEl.innerHTML=(j.logs||[]).map(l=>`<div class="log-line"><span class="log-time">${{l.t}}</span><span>${{l.msg}}</span></div>`).join('');
    logEl.scrollTop=logEl.scrollHeight;
    setPhase(j.status);
    const pct={{queued:3,crawling:25,analyzing:65,reporting:88,done:100,error:100}};
    document.getElementById('progBar').style.width=(pct[j.status]||3)+'%';
    renderHist();
    if(j.status==='done'){{clearInterval(poll);setBusy(false);showResult(j,id);}}
    else if(j.status==='error'){{clearInterval(poll);setBusy(false);setTB('Scan Failed',j.url||'');}}
  }}catch(e){{console.error(e);}}
}}

function showResult(j,id){{
  setTB('Scan Complete ✓',j.url||'');
  document.getElementById('s-issues').textContent=j.total_issues??'—';
  document.getElementById('s-pages').textContent=j.pages_crawled??'—';
  document.getElementById('s-crit').textContent=j.critical_issues??'—';
  document.getElementById('s-time').textContent=j.elapsed??'—';
  show('statsEl');show('rfEl');
  const u='/api/report/'+id;
  document.getElementById('rif').src=u;
  document.getElementById('rfLink').href=u;
  show('openBtn');show('dlBtn');
  document.getElementById('openBtn').dataset.jid=id;
  document.getElementById('dlBtn').dataset.jid=id;
}}

function loadJob(id){{
  const j=jobs[id];if(!j)return;
  curJob=id;
  document.querySelectorAll('.hist-item').forEach(e=>e.classList.remove('active'));
  document.getElementById('hi-'+id)?.classList.add('active');
  setTB(j.status==='done'?'Scan Complete ✓':'Scan: '+j.status,j.url||'');
  show('progEl');hide('welcomeEl');
  document.getElementById('progUrl').textContent=j.url||'';
  const logEl=document.getElementById('logBox');
  logEl.innerHTML=(j.logs||[]).map(l=>`<div class="log-line"><span class="log-time">${{l.t}}</span><span>${{l.msg}}</span></div>`).join('');
  setPhase(j.status);
  const pct={{queued:3,crawling:25,analyzing:65,reporting:88,done:100}};
  document.getElementById('progBar').style.width=(pct[j.status]||3)+'%';
  if(j.status==='done'){{showResult(j,id);}}
  else{{hide('statsEl');hide('rfEl');hide('openBtn');hide('dlBtn');}}
}}

function renderHist(){{
  const el=document.getElementById('histList');
  const list=Object.entries(jobs).reverse();
  if(!list.length){{el.innerHTML='<div class="hist-empty">No scans yet.<br>Enter a URL and click Run Scan.</div>';return;}}
  el.innerHTML=list.map(([id,j])=>{{
    const short=(j.url||'').replace(/https?:[/][/]/,'').slice(0,34);
    const st=j.status||'queued';
    const cls=st==='done'?'sp-done':st==='error'?'sp-error':'sp-running';
    const lbl=st.charAt(0).toUpperCase()+st.slice(1);
    const info=st==='done'?`${{j.total_issues??'?'}} issues · ${{j.elapsed??'?'}}s`:'';
    return `<div class="hist-item" id="hi-${{id}}" onclick="loadJob('${{id}}')">
      <div class="hist-url">${{short}}</div>
      <div class="hist-meta"><span class="sp ${{cls}}">${{lbl}}</span><span class="hist-info">${{info}}</span></div>
    </div>`;
  }}).join('');
  document.getElementById('hi-'+curJob)?.classList.add('active');
}}

function setPhase(s){{
  const order=['crawling','analyzing','reporting'];
  const map={{crawling:0,analyzing:1,reporting:2,done:2}};
  const cur=map[s]??-1;
  order.forEach((p,i)=>{{
    const el=document.getElementById('ph-'+p);if(!el)return;
    el.className='phase';
    if(i<cur)el.classList.add('done');
    else if(i===cur)el.classList.add('active');
  }});
}}
function setTB(t,s){{document.getElementById('tbTitle').textContent=t;document.getElementById('tbSub').textContent=s;}}
function setBusy(b){{
  document.getElementById('runBtn').disabled=b;
  document.getElementById('runIcon').innerHTML=b?'<div class="spinner"></div>':'&#9654;';
  document.getElementById('runTxt').textContent=b?'Scanning…':'Run Scan';
}}
function show(id){{document.getElementById(id)?.classList.remove('hidden');}}
function hide(id){{document.getElementById(id)?.classList.add('hidden');}}
function openTab(){{const id=document.getElementById('openBtn').dataset.jid;if(id)window.open('/api/report/'+id,'_blank');}}
function dlReport(){{
  const id=document.getElementById('dlBtn').dataset.jid;if(!id)return;
  const a=document.createElement('a');a.href='/api/report/'+id;
  a.download='indiamart_report_'+id+'.html';a.click();
}}
function shakeUrl(){{
  const el=document.getElementById('urlInput');
  el.style.borderColor='#c0392b';el.style.boxShadow='0 0 0 3px rgba(192,57,43,.15)';
  setTimeout(()=>{{el.style.borderColor='';el.style.boxShadow='';}},1200);
}}
document.addEventListener('keydown',e=>{{
  if(e.key==='Enter'&&document.activeElement.id==='urlInput')startScan();
}});
(async()=>{{
  try{{
    const r=await fetch('/api/jobs');
    const list=await r.json();
    list.forEach(j=>{{jobs[j.job_id]=j;}});
    renderHist();
  }}catch(e){{}}
}})();
</script>
</body>
</html>'''

if __name__ == "__main__":
    print("""
╔══════════════════════════════════════════════════════╗
║  IndiaMART Bug Finder AI Agent — v5                 ║
║  Real QA · Functional & UI · Web Vitals             ║
║  Railway Deployment Mode                             ║
╚══════════════════════════════════════════════════════╝
""")
    app.run(host="0.0.0.0", port=8080, debug=False, threaded=True)
