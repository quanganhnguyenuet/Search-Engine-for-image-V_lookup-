"""Capture real UI results and bounded evidence; requires playwright + local API/web."""
import asyncio
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from playwright.async_api import async_playwright

OUT = Path(__file__).resolve().parent
QUERIES = [
    ("01-banh-chung", "bánh chưng"),
    ("02-ruong-bac-thang", "ruộng bậc thang xanh ở vùng núi"),
    ("03-ao-dai-khong-non", "người mặc áo dài đỏ, không đội nón"),
]


async def main():
    (OUT / "evidence").mkdir(exist_ok=True)
    (OUT / "assets").mkdir(exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=["--no-sandbox"])
        context = await browser.new_context(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
        request = context.request
        status = await request.get("http://127.0.0.1:8000/api/v1/search/status")
        status_json = await status.json()
        (OUT / "evidence/status.json").write_text(json.dumps(status_json, ensure_ascii=False, indent=2))
        summaries = []
        for slug, query in QUERIES:
            page = await context.new_page()
            async with page.expect_response(lambda r: "/api/culture/ai" in r.url and r.request.method == "POST", timeout=240000) as pending:
                await page.goto("http://localhost:3000/?" + urlencode({"q": query}), wait_until="domcontentloaded")
            response = await pending.value
            body = await response.json()
            fallback = False
            # A failed provider call is recorded. A separately labelled local run
            # still illustrates the retrieval limit without inventing LLM output.
            if response.status != 200:
                (OUT / f"evidence/{slug}-auto-error.json").write_text(json.dumps({"status": response.status, "body": body}, ensure_ascii=False, indent=2))
                fallback = True
                async def force_local(route):
                    data = json.loads(route.request.post_data)
                    data["use_llm"] = False
                    await route.continue_(post_data=json.dumps(data))
                await page.route("**/api/culture/ai", force_local)
                async with page.expect_response(lambda r: "/api/culture/ai" in r.url, timeout=120000) as pending:
                    await page.reload(wait_until="domcontentloaded")
                response = await pending.value
                body = await response.json()
            if response.status != 200:
                raise RuntimeError(f"Query {slug} failed: HTTP {response.status}")
            await page.locator(".skeleton-card").first.wait_for(state="hidden", timeout=30000)
            await page.locator("#collection").scroll_into_view_if_needed()
            await page.evaluate("""async () => { await document.fonts.ready;
                await Promise.all([...document.querySelectorAll('.photo-grid img')].slice(0,6).map(i => i.decode().catch(()=>{}))); }""")
            await page.screenshot(path=str(OUT / f"assets/{slug}-full.png"), full_page=True)
            await page.locator(".photo-grid, .empty-state").first.screenshot(path=str(OUT / f"assets/{slug}-results.png"))
            grid = page.locator(".photo-card").first
            if await grid.count():
                await grid.screenshot(path=str(OUT / f"assets/{slug}-first.png"))
            evidence = {"captured_at": datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).isoformat(),
                        "request": {"query": query, "mode": "contextual", "use_llm": False if fallback else None},
                        "local_fallback": fallback, "status": response.status, "response": body}
            (OUT / f"evidence/{slug}.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
            summary = {"slug": slug, "query": query, "route": body.get("search_route"),
                       "llm_calls": body.get("llm_call_count"), "local_fallback": fallback,
                       "total": body.get("total"), "top_titles": [i["title"] for i in body.get("items", [])[:3]]}
            summaries.append(summary)
            print(json.dumps(summary, ensure_ascii=False), flush=True)
            await page.close()
        (OUT / "evidence/capture-summary.json").write_text(json.dumps(summaries, ensure_ascii=False, indent=2))
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
