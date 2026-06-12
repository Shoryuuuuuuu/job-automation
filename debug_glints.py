"""
Script debug — jalankan langsung: python debug_glints.py
Akan buka browser, screenshot halaman, dan print semua selector yang ada.
"""
import time
from playwright.sync_api import sync_playwright

URL = "https://glints.com/id/opportunities/jobs/explore?keyword=Data+Analyst&locationName=Jakarta"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)  # headless=False biar kelihatan
    page = browser.new_page(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    )

    print(f"[DEBUG] Membuka: {URL}")
    page.goto(URL, timeout=30_000)
    time.sleep(3)

    # Screenshot
    page.screenshot(path="debug_glints_1_awal.png", full_page=False)
    print("[DEBUG] Screenshot 1 disimpan: debug_glints_1_awal.png")

    # Scroll
    for i in range(3):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(2)

    page.screenshot(path="debug_glints_2_scroll.png", full_page=False)
    print("[DEBUG] Screenshot 2 disimpan: debug_glints_2_scroll.png")

    # Cek URL akhir (mungkin redirect)
    print(f"[DEBUG] URL akhir: {page.url}")

    # Coba berbagai selector kartu
    selectors_to_test = [
        "[data-testid='job-card']",
        "[data-testid='JobCard']",
        "div[class*='JobCard']",
        "div[class*='job-card']",
        "div[class*='JobCardWrapper']",
        "article[class*='job']",
        "li[class*='job']",
        "a[href*='/opportunities/jobs/']",
        "[class*='CompactOpportunityCard']",
        "[class*='OpportunityCard']",
        "div[class*='DesktopJobCard']",
        "div[class*='job']",
    ]

    print("\n[DEBUG] Test selector kartu:")
    for sel in selectors_to_test:
        els = page.query_selector_all(sel)
        if els:
            print(f"  ✓ FOUND ({len(els)} item): {sel}")
            # Print teks dari elemen pertama
            try:
                teks = els[0].inner_text()[:100].replace('\n', ' ')
                print(f"    Preview: {teks}")
            except Exception:
                pass
        else:
            print(f"  ✗ not found: {sel}")

    # Print semua class dari div level atas untuk identifikasi struktur
    print("\n[DEBUG] Sample class names dari div/article di halaman:")
    class_sample = page.evaluate("""
        () => {
            const els = [...document.querySelectorAll('div[class], article[class], li[class]')];
            const classes = els
                .map(e => e.className)
                .filter(c => c && c.toLowerCase().includes('job') || c.toLowerCase().includes('card') || c.toLowerCase().includes('opportunit'))
                .slice(0, 30);
            return [...new Set(classes)];
        }
    """)
    for c in class_sample:
        print(f"  {c}")

    # Cek apakah ada prompt login
    login_prompt = page.query_selector(
        "button:has-text('Masuk'), a:has-text('Masuk'), "
        "div:has-text('Login to view'), div:has-text('Masuk untuk')"
    )
    if login_prompt:
        print("\n[DEBUG] ⚠️  ADA PROMPT LOGIN — Glints minta login sebelum bisa lihat lowongan!")
    else:
        print("\n[DEBUG] Tidak ada prompt login yang terdeteksi.")

    # Print h1/h2 di halaman
    headings = page.evaluate("""
        () => [...document.querySelectorAll('h1,h2,h3')]
              .map(e => e.innerText.trim())
              .filter(t => t.length > 0)
              .slice(0, 10)
    """)
    print(f"\n[DEBUG] Heading di halaman: {headings}")

    input("\n[DEBUG] Tekan Enter untuk tutup browser...")
    browser.close()

print("[DEBUG] Selesai.")
