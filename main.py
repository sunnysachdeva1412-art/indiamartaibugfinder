"""
main.py — IndiaMART Bug Finder v5
CLI entry point.

Usage:
  python main.py --url https://www.indiamart.com/proddetail/xyz.html
  python main.py --url https://www.indiamart.com/ --depth 2 --max-pages 5
  python main.py --url https://www.indiamart.com/ --skip-ai
"""

import argparse
import asyncio
import json
import os
import shutil
import time
from datetime import datetime
from pathlib import Path

from crawler_playwright import EnhancedCrawler
from analyzer_claude import ClaudeAnalyzer
from reporter import ReportGenerator

BANNER = """
╔══════════════════════════════════════════════════════════════╗
║    IndiaMART AI Bug Finder  ·  v6                           ║
║    3 PDP Types · PDP-CORE-6063–7076 · Desktop · Web Vitals  ║
╚══════════════════════════════════════════════════════════════╝
"""


def parse_args():
    p = argparse.ArgumentParser(description="IndiaMART AI Bug Finder v6")
    p.add_argument("--url",        required=True,  help="URL to crawl")
    p.add_argument("--depth",      type=int, default=1, help="Crawl depth (default: 1)")
    p.add_argument("--max-pages",  type=int, default=5, help="Max pages (default: 5)")
    p.add_argument("--output",     default="output", help="Output directory")
    p.add_argument("--headless",   action="store_true", default=True)
    p.add_argument("--no-headless", dest="headless", action="store_false")
    p.add_argument("--skip-ai",    action="store_true", help="Skip Claude AI analysis")
    return p.parse_args()


async def main():
    print(BANNER)
    args = parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key and not args.skip_ai:
        print("⚠️  ANTHROPIC_API_KEY not set — running with --skip-ai\n")
        args.skip_ai = True

    out = Path(args.output)
    (out / "screenshots").mkdir(parents=True, exist_ok=True)
    start = time.time()

    print(f"🌐  Target   : {args.url}")
    print(f"📐  Depth    : {args.depth}   Max pages: {args.max_pages}")
    print(f"🤖  AI Vision: {'disabled' if args.skip_ai else 'enabled'}")
    print()

    # Phase 1: Crawl
    print("═" * 60)
    print("  PHASE 1 — Crawling & Automated Checks")
    print("═" * 60)
    crawler = EnhancedCrawler(str(out), args.headless, args.max_pages)
    pages = await crawler.crawl(args.url, args.depth)
    print(f"\n✅  Crawled {len(pages)} page(s)")

    for p in pages:
        cnt = len(p.get("issues", []))
        ss = "✅" if p.get("screenshots", {}).get("desktop_b64") else "❌"
        print(f"    {p['url'][:70]:70s}  issues:{cnt:3d}  ss:{ss}")

    # Phase 2: AI Analysis
    if not args.skip_ai:
        print("\n" + "═" * 60)
        print("  PHASE 2 — Claude AI Visual Analysis")
        print("═" * 60)
        analyzer = ClaudeAnalyzer(api_key)
        pages = await analyzer.analyze_pages(pages)
        ai_total = sum(len(p.get("ai_issues", [])) for p in pages)
        print(f"\n✅  AI analysis complete — {ai_total} visual issue(s) found")
    else:
        for p in pages:
            p.setdefault("ai_issues", [])

    # Phase 3: Report
    print("\n" + "═" * 60)
    print("  PHASE 3 — Generating Report")
    print("═" * 60)
    elapsed = round(time.time() - start, 1)
    meta = {
        "url": args.url, "depth": args.depth,
        "pages_crawled": len(pages),
        "crawl_duration_s": elapsed,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    reporter = ReportGenerator(str(out))
    report_path = reporter.generate(pages, meta)

    # Timestamped backup
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy(report_path, out / f"report_{ts}.html")

    # Save JSON (without base64)
    json_pages = []
    for page in pages:
        p = dict(page)
        ss = dict(p.get("screenshots", {}))
        ss.pop("desktop_b64", None)
        p["screenshots"] = ss
        json_pages.append(p)
    (out / "results.json").write_text(
        json.dumps({"meta": meta, "pages": json_pages}, indent=2, default=str),
        encoding="utf-8"
    )

    total = sum(len(p.get("issues", [])) + len(p.get("ai_issues", [])) for p in pages)
    print(f"\n✅  Report  → {report_path}")
    print(f"📊  Total   : {total} real issues found")
    print(f"⏱️   Time    : {elapsed}s")
    print("\n  Open report.html in your browser.\n")


if __name__ == "__main__":
    asyncio.run(main())
