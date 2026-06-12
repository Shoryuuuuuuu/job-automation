import re
import time
import logging
from datetime import datetime

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helper: ekstrak angka gaji dari string (mis. "Rp 5.000.000 – Rp 8.000.000")
# ---------------------------------------------------------------------------

def parse_gaji(teks_gaji: str | None) -> int | None:
    if not teks_gaji:
        return None
    angka_list = re.findall(r"\d[\d.]*", teks_gaji.replace(",", "."))
    if not angka_list:
        return None
    angka_bersih = [int(a.replace(".", "")) for a in angka_list]
    return min(angka_bersih)


# ---------------------------------------------------------------------------
# Helper: normalisasi nilai config jadi list
# ---------------------------------------------------------------------------

def _ke_list(nilai) -> list:
    if isinstance(nilai, list):
        return nilai
    if isinstance(nilai, str) and nilai.strip():
        return [nilai.strip()]
    return []


# ---------------------------------------------------------------------------
# Helper terpusat: ambil deskripsi dengan context independen
# ---------------------------------------------------------------------------

def _ambil_deskripsi(
    browser,
    url: str,
    selector: str,
    fallback_selectors: list[str] = None,
    nama_platform: str = "SCRAPER",
    timeout_goto: int = 20_000,
    timeout_selector: int = 10_000,
) -> str:
    """
    Buka halaman detail di context baru yang independen menggunakan
    browser.new_context() — mencegah error 'Please use browser.new_context()'.

    Strategi pengambilan deskripsi (berurutan):
    1. Coba selector utama dengan wait_for_selector.
    2. Jika timeout/tidak ditemukan, coba fallback_selectors tanpa wait.
    3. Last resort: ambil teks dari <main>, <article>, atau <body>.
    Tidak pernah crash — selalu return string (kosong jika semua gagal).
    """
    ctx = None
    detail_page = None
    try:
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        detail_page = ctx.new_page()
        detail_page.goto(url, timeout=timeout_goto)

        desc_el = None

        # 1. Coba selector utama dengan wait
        try:
            detail_page.wait_for_selector(selector, timeout=timeout_selector)
            desc_el = detail_page.query_selector(selector)
        except Exception:
            logger.debug(
                f"[{nama_platform}] Selector utama '{selector}' "
                f"tidak ditemukan, coba fallback."
            )

        # 2. Coba fallback selectors tanpa wait (halaman sudah dimuat)
        if desc_el is None and fallback_selectors:
            for fb in fallback_selectors:
                try:
                    desc_el = detail_page.query_selector(fb)
                    if desc_el:
                        logger.debug(f"[{nama_platform}] Fallback '{fb}' berhasil.")
                        break
                except Exception:
                    continue

        # 3. Last resort: main / article / body
        if desc_el is None:
            try:
                desc_el = (
                    detail_page.query_selector("main")
                    or detail_page.query_selector("article")
                    or detail_page.query_selector("body")
                )
                if desc_el:
                    logger.debug(f"[{nama_platform}] Menggunakan last-resort selector.")
            except Exception:
                pass

        return desc_el.inner_text().strip() if desc_el else ""

    except Exception as e:
        logger.warning(f"[{nama_platform}] Gagal ambil deskripsi dari {url}: {e}")
        return ""
    finally:
        if detail_page:
            try:
                detail_page.close()
            except Exception:
                pass
        if ctx:
            try:
                ctx.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Fungsi deskripsi per platform (semua pakai _ambil_deskripsi terpusat)
# ---------------------------------------------------------------------------

def _ambil_deskripsi_glints(browser, url: str) -> str:
    return _ambil_deskripsi(
        browser, url,
        selector="[data-testid='job-description']",
        nama_platform="GLINTS",
    )


def _ambil_deskripsi_jobstreet(browser, url: str) -> str:
    return _ambil_deskripsi(
        browser, url,
        selector="[data-automation='jobDescription']",
        fallback_selectors=[
            "div[data-automation='jobDescription']",
            "div[class*='job-description']",
            "div[class*='JobDescription']",
            "section[class*='job-details']",
            "div[id*='job-description']",
        ],
        nama_platform="JOBSTREET",
        timeout_selector=15_000,
    )


def _ambil_deskripsi_linkedin(browser, url: str) -> str:
    return _ambil_deskripsi(
        browser, url,
        selector="div.description__text",
        fallback_selectors=["div.show-more-less-html__markup"],
        nama_platform="LINKEDIN",
    )


def _ambil_deskripsi_internshala(browser, url: str) -> str:
    return _ambil_deskripsi(
        browser, url,
        selector="#about_internship",
        nama_platform="INTERNSHALA",
    )


def _ambil_deskripsi_weworkremotely(browser, url: str) -> str:
    return _ambil_deskripsi(
        browser, url,
        selector="div.listing-container",
        nama_platform="WWR",
    )


def _ambil_deskripsi_wellfound(browser, url: str) -> str:
    return _ambil_deskripsi(
        browser, url,
        selector="div[class*='jobDescription']",
        fallback_selectors=["section[class*='description']"],
        nama_platform="WELLFOUND",
    )


def _ambil_deskripsi_flexjobs(browser, url: str) -> str:
    return _ambil_deskripsi(
        browser, url,
        selector="div[class*='job-description']",
        fallback_selectors=["div[id*='job-description']"],
        nama_platform="FLEXJOBS",
    )


def _ambil_deskripsi_glassdoor(browser, url: str) -> str:
    return _ambil_deskripsi(
        browser, url,
        selector="div[class*='JobDetails']",
        fallback_selectors=["div[id*='JobDesc']"],
        nama_platform="GLASSDOOR",
    )


def _ambil_deskripsi_remotive(browser, url: str) -> str:
    return _ambil_deskripsi(
        browser, url,
        selector="div[class*='job-description']",
        fallback_selectors=["div.tw-prose"],
        nama_platform="REMOTIVE",
    )


def _ambil_deskripsi_dealls(browser, url: str) -> str:
    return _ambil_deskripsi(
        browser, url,
        selector="[data-testid='job-description']",
        fallback_selectors=[
            "div[class*='JobDescription']",
            "div[class*='description']",
        ],
        nama_platform="DEALLS",
    )


def _ambil_deskripsi_kalibrr(browser, url: str) -> str:
    return _ambil_deskripsi(
        browser, url,
        selector="[data-cy='job-description']",
        fallback_selectors=[
            "div[class*='JobDescription']",
            "div[class*='description']",
        ],
        nama_platform="KALIBRR",
    )


# ---------------------------------------------------------------------------
# Scraper platform: Glints
# ---------------------------------------------------------------------------

def scrape_glints(posisi: str, lokasi: str) -> list[dict]:
    hasil = []
    url_cari = (
        f"https://glints.com/id/opportunities/jobs/explore"
        f"?keyword={posisi.replace(' ', '%20')}"
        f"&locationName={lokasi.replace(' ', '%20')}"
    )
    logger.info(f"[GLINTS] Mulai scrape: posisi='{posisi}', lokasi='{lokasi}'")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(url_cari, timeout=30_000)
            page.wait_for_selector("[data-testid='job-card']", timeout=15_000)
            for _ in range(3):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(2)

            kartu_list = page.query_selector_all("[data-testid='job-card']")
            logger.info(f"[GLINTS] Ditemukan {len(kartu_list)} kartu lowongan")

            for kartu in kartu_list:
                try:
                    judul = kartu.query_selector("h2")
                    perusahaan = kartu.query_selector("[data-testid='company-name']")
                    lokasi_el = kartu.query_selector("[data-testid='job-location']")
                    gaji_el = kartu.query_selector("[data-testid='salary']")
                    link_el = kartu.query_selector("a")
                    if not judul or not link_el:
                        continue
                    url_lowongan = link_el.get_attribute("href") or ""
                    if url_lowongan.startswith("/"):
                        url_lowongan = "https://glints.com" + url_lowongan
                    teks_gaji = gaji_el.inner_text().strip() if gaji_el else None
                    deskripsi = _ambil_deskripsi_glints(browser, url_lowongan)
                    hasil.append({
                        "judul": judul.inner_text().strip(),
                        "perusahaan": perusahaan.inner_text().strip() if perusahaan else "",
                        "lokasi": lokasi_el.inner_text().strip() if lokasi_el else lokasi,
                        "gaji": teks_gaji,
                        "gaji_angka": parse_gaji(teks_gaji),
                        "url": url_lowongan,
                        "deskripsi": deskripsi,
                    })
                    time.sleep(1)
                except Exception as e:
                    logger.warning(f"[GLINTS] Gagal parse kartu: {e}")
                    continue

        except PlaywrightTimeoutError:
            logger.error("[GLINTS] Timeout saat memuat halaman")
        except Exception as e:
            logger.error(f"[GLINTS] Error tidak terduga: {e}")
        finally:
            browser.close()

    logger.info(f"[GLINTS] Selesai. Total: {len(hasil)} lowongan")
    return hasil


# ---------------------------------------------------------------------------
# Scraper platform: Jobstreet
# ---------------------------------------------------------------------------

def scrape_jobstreet(posisi: str, lokasi: str) -> list[dict]:
    hasil = []
    LOKASI_VALID_JOBSTREET = {"remote", "hybrid"}
    if lokasi.lower() in LOKASI_VALID_JOBSTREET:
        logger.info(
            f"[JOBSTREET] Skip query URL untuk lokasi '{lokasi}' "
            f"(bukan nama kota — akan difilter dari deskripsi)."
        )
        return []

    url_cari = (
        f"https://id.jobstreet.com/{posisi.lower().replace(' ', '-')}-jobs"
        f"?where={lokasi.replace(' ', '+')}"
    )
    logger.info(f"[JOBSTREET] Mulai scrape: posisi='{posisi}', lokasi='{lokasi}'")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        try:
            page.goto(url_cari, timeout=30_000)
            page.wait_for_selector("article[data-automation='normalJob']", timeout=15_000)
            for _ in range(3):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(2)

            kartu_list = page.query_selector_all("article[data-automation='normalJob']")
            logger.info(f"[JOBSTREET] Ditemukan {len(kartu_list)} kartu lowongan")

            for kartu in kartu_list:
                try:
                    judul_el = kartu.query_selector("a[data-automation='jobTitle']")
                    perusahaan_el = kartu.query_selector("a[data-automation='jobCompany']")
                    lokasi_el = kartu.query_selector("span[data-automation='jobLocation']")
                    gaji_el = kartu.query_selector("span[data-automation='jobSalary']")
                    if not judul_el:
                        continue
                    url_relative = judul_el.get_attribute("href") or ""
                    url_lowongan = (
                        "https://id.jobstreet.com" + url_relative
                        if url_relative.startswith("/")
                        else url_relative
                    )
                    teks_gaji = gaji_el.inner_text().strip() if gaji_el else None
                    deskripsi = _ambil_deskripsi_jobstreet(browser, url_lowongan)
                    hasil.append({
                        "judul": judul_el.inner_text().strip(),
                        "perusahaan": perusahaan_el.inner_text().strip() if perusahaan_el else "",
                        "lokasi": lokasi_el.inner_text().strip() if lokasi_el else lokasi,
                        "gaji": teks_gaji,
                        "gaji_angka": parse_gaji(teks_gaji),
                        "url": url_lowongan,
                        "deskripsi": deskripsi,
                    })
                    time.sleep(1)
                except Exception as e:
                    logger.warning(f"[JOBSTREET] Gagal parse kartu: {e}")
                    continue

        except PlaywrightTimeoutError:
            logger.error("[JOBSTREET] Timeout saat memuat halaman")
        except Exception as e:
            logger.error(f"[JOBSTREET] Error tidak terduga: {e}")
        finally:
            browser.close()

    logger.info(f"[JOBSTREET] Selesai. Total: {len(hasil)} lowongan")
    return hasil


# ---------------------------------------------------------------------------
# Scraper platform: LinkedIn Jobs
# ---------------------------------------------------------------------------

def scrape_linkedin(posisi: str, lokasi: str) -> list[dict]:
    hasil = []
    url_cari = (
        f"https://www.linkedin.com/jobs/search/"
        f"?keywords={posisi.replace(' ', '%20')}"
        f"&location={lokasi.replace(' ', '%20')}"
        f"&f_TPR=r86400"
    )
    logger.info(f"[LINKEDIN] Mulai scrape: posisi='{posisi}', lokasi='{lokasi}'")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        try:
            page.goto(url_cari, timeout=30_000)
            page.wait_for_selector("ul.jobs-search__results-list li", timeout=15_000)

            for _ in range(3):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(2)

            kartu_list = page.query_selector_all("ul.jobs-search__results-list li")
            logger.info(f"[LINKEDIN] Ditemukan {len(kartu_list)} kartu lowongan")

            for kartu in kartu_list:
                try:
                    judul_el = kartu.query_selector("h3.base-search-card__title")
                    perusahaan_el = kartu.query_selector("h4.base-search-card__subtitle a")
                    lokasi_el = kartu.query_selector("span.job-search-card__location")
                    link_el = kartu.query_selector("a.base-card__full-link")

                    if not judul_el or not link_el:
                        continue

                    url_lowongan = link_el.get_attribute("href") or ""
                    deskripsi = _ambil_deskripsi_linkedin(browser, url_lowongan)
                    hasil.append({
                        "judul": judul_el.inner_text().strip(),
                        "perusahaan": perusahaan_el.inner_text().strip() if perusahaan_el else "",
                        "lokasi": lokasi_el.inner_text().strip() if lokasi_el else lokasi,
                        "gaji": None,
                        "gaji_angka": None,
                        "url": url_lowongan,
                        "deskripsi": deskripsi,
                    })
                    time.sleep(1)
                except Exception as e:
                    logger.warning(f"[LINKEDIN] Gagal parse kartu: {e}")
                    continue

        except PlaywrightTimeoutError:
            logger.error("[LINKEDIN] Timeout — mungkin diminta login atau anti-bot aktif")
        except Exception as e:
            logger.error(f"[LINKEDIN] Error tidak terduga: {e}")
        finally:
            browser.close()

    logger.info(f"[LINKEDIN] Selesai. Total: {len(hasil)} lowongan")
    return hasil


# ---------------------------------------------------------------------------
# Scraper platform: Internshala
# ---------------------------------------------------------------------------

def scrape_internshala(posisi: str, lokasi: str) -> list[dict]:
    hasil = []
    posisi_slug = posisi.lower().replace(" ", "-")

    if lokasi.lower() in {"remote", "wfh", "work from home"}:
        url_cari = f"https://internshala.com/internships/{posisi_slug}-internship/work-from-home"
    else:
        url_cari = (
            f"https://internshala.com/internships/"
            f"{posisi_slug}-internship-in-{lokasi.lower().replace(' ', '-')}"
        )

    logger.info(f"[INTERNSHALA] Mulai scrape: posisi='{posisi}', lokasi='{lokasi}'")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        try:
            page.goto(url_cari, timeout=30_000)
            page.wait_for_selector(".internship_meta", timeout=15_000)

            for _ in range(3):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(2)

            kartu_list = page.query_selector_all(".individual_internship")
            logger.info(f"[INTERNSHALA] Ditemukan {len(kartu_list)} kartu lowongan")

            for kartu in kartu_list:
                try:
                    judul_el = kartu.query_selector(".job-internship-name")
                    perusahaan_el = kartu.query_selector(".company-name")
                    lokasi_el = kartu.query_selector(".locations span")
                    gaji_el = kartu.query_selector(".stipend")
                    link_el = kartu.query_selector("a.view_detail_button")

                    if not judul_el or not link_el:
                        continue

                    url_relative = link_el.get_attribute("href") or ""
                    url_lowongan = (
                        "https://internshala.com" + url_relative
                        if url_relative.startswith("/")
                        else url_relative
                    )
                    teks_gaji = gaji_el.inner_text().strip() if gaji_el else None
                    deskripsi = _ambil_deskripsi_internshala(browser, url_lowongan)
                    hasil.append({
                        "judul": judul_el.inner_text().strip(),
                        "perusahaan": perusahaan_el.inner_text().strip() if perusahaan_el else "",
                        "lokasi": lokasi_el.inner_text().strip() if lokasi_el else lokasi,
                        "gaji": teks_gaji,
                        "gaji_angka": parse_gaji(teks_gaji),
                        "url": url_lowongan,
                        "deskripsi": deskripsi,
                    })
                    time.sleep(1)
                except Exception as e:
                    logger.warning(f"[INTERNSHALA] Gagal parse kartu: {e}")
                    continue

        except PlaywrightTimeoutError:
            logger.error("[INTERNSHALA] Timeout saat memuat halaman")
        except Exception as e:
            logger.error(f"[INTERNSHALA] Error tidak terduga: {e}")
        finally:
            browser.close()

    logger.info(f"[INTERNSHALA] Selesai. Total: {len(hasil)} lowongan")
    return hasil


# ---------------------------------------------------------------------------
# Scraper platform: We Work Remotely
# ---------------------------------------------------------------------------

def scrape_weworkremotely(posisi: str, lokasi: str) -> list[dict]:
    hasil = []
    url_cari = f"https://weworkremotely.com/remote-jobs/search?term={posisi.replace(' ', '+')}"
    logger.info(f"[WWR] Mulai scrape: posisi='{posisi}' (semua listing remote)")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(url_cari, timeout=30_000)
            page.wait_for_selector("ul.jobs li.feature", timeout=15_000)

            kartu_list = page.query_selector_all("ul.jobs li.feature")
            logger.info(f"[WWR] Ditemukan {len(kartu_list)} kartu lowongan")

            for kartu in kartu_list:
                try:
                    judul_el = kartu.query_selector("span.title")
                    perusahaan_el = kartu.query_selector("span.company")
                    link_el = kartu.query_selector("a")

                    if not judul_el or not link_el:
                        continue

                    url_relative = link_el.get_attribute("href") or ""
                    url_lowongan = (
                        "https://weworkremotely.com" + url_relative
                        if url_relative.startswith("/")
                        else url_relative
                    )
                    deskripsi = _ambil_deskripsi_weworkremotely(browser, url_lowongan)
                    hasil.append({
                        "judul": judul_el.inner_text().strip(),
                        "perusahaan": perusahaan_el.inner_text().strip() if perusahaan_el else "",
                        "lokasi": "Remote",
                        "gaji": None,
                        "gaji_angka": None,
                        "url": url_lowongan,
                        "deskripsi": deskripsi,
                    })
                    time.sleep(1)
                except Exception as e:
                    logger.warning(f"[WWR] Gagal parse kartu: {e}")
                    continue

        except PlaywrightTimeoutError:
            logger.error("[WWR] Timeout saat memuat halaman")
        except Exception as e:
            logger.error(f"[WWR] Error tidak terduga: {e}")
        finally:
            browser.close()

    logger.info(f"[WWR] Selesai. Total: {len(hasil)} lowongan")
    return hasil


# ---------------------------------------------------------------------------
# Scraper platform: Wellfound (ex AngelList Talent)
# ---------------------------------------------------------------------------

def scrape_wellfound(posisi: str, lokasi: str) -> list[dict]:
    hasil = []
    url_cari = (
        f"https://wellfound.com/jobs"
        f"?q={posisi.replace(' ', '%20')}"
        f"&l={lokasi.replace(' ', '%20')}"
    )
    logger.info(f"[WELLFOUND] Mulai scrape: posisi='{posisi}', lokasi='{lokasi}'")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        try:
            page.goto(url_cari, timeout=30_000)
            page.wait_for_selector("div[class*='JobListing']", timeout=15_000)

            for _ in range(3):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(2)

            kartu_list = page.query_selector_all("div[class*='JobListing']")
            logger.info(f"[WELLFOUND] Ditemukan {len(kartu_list)} kartu lowongan")

            for kartu in kartu_list:
                try:
                    judul_el = kartu.query_selector("a[class*='jobTitle'], h2")
                    perusahaan_el = kartu.query_selector("a[class*='companyName'], span[class*='company']")
                    lokasi_el = kartu.query_selector("span[class*='location']")
                    gaji_el = kartu.query_selector("span[class*='compensation'], span[class*='salary']")
                    link_el = kartu.query_selector("a[href*='/jobs/']")

                    if not judul_el or not link_el:
                        continue

                    url_relative = link_el.get_attribute("href") or ""
                    url_lowongan = (
                        "https://wellfound.com" + url_relative
                        if url_relative.startswith("/")
                        else url_relative
                    )
                    teks_gaji = gaji_el.inner_text().strip() if gaji_el else None
                    deskripsi = _ambil_deskripsi_wellfound(browser, url_lowongan)
                    hasil.append({
                        "judul": judul_el.inner_text().strip(),
                        "perusahaan": perusahaan_el.inner_text().strip() if perusahaan_el else "",
                        "lokasi": lokasi_el.inner_text().strip() if lokasi_el else lokasi,
                        "gaji": teks_gaji,
                        "gaji_angka": parse_gaji(teks_gaji),
                        "url": url_lowongan,
                        "deskripsi": deskripsi,
                    })
                    time.sleep(1)
                except Exception as e:
                    logger.warning(f"[WELLFOUND] Gagal parse kartu: {e}")
                    continue

        except PlaywrightTimeoutError:
            logger.error("[WELLFOUND] Timeout saat memuat halaman")
        except Exception as e:
            logger.error(f"[WELLFOUND] Error tidak terduga: {e}")
        finally:
            browser.close()

    logger.info(f"[WELLFOUND] Selesai. Total: {len(hasil)} lowongan")
    return hasil


# ---------------------------------------------------------------------------
# Scraper platform: FlexJobs
# ---------------------------------------------------------------------------

def scrape_flexjobs(posisi: str, lokasi: str) -> list[dict]:
    hasil = []
    url_cari = (
        f"https://www.flexjobs.com/search"
        f"?search={posisi.replace(' ', '+')}"
        f"&location={lokasi.replace(' ', '+')}"
    )
    logger.info(f"[FLEXJOBS] Mulai scrape: posisi='{posisi}', lokasi='{lokasi}'")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        try:
            page.goto(url_cari, timeout=30_000)
            page.wait_for_selector("li[data-job-id]", timeout=15_000)

            for _ in range(3):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(2)

            kartu_list = page.query_selector_all("li[data-job-id]")
            logger.info(f"[FLEXJOBS] Ditemukan {len(kartu_list)} kartu lowongan")

            for kartu in kartu_list:
                try:
                    judul_el = kartu.query_selector("h2 a, h3 a")
                    perusahaan_el = kartu.query_selector("span[class*='company']")
                    lokasi_el = kartu.query_selector("span[class*='location']")

                    if not judul_el:
                        continue

                    url_relative = judul_el.get_attribute("href") or ""
                    url_lowongan = (
                        "https://www.flexjobs.com" + url_relative
                        if url_relative.startswith("/")
                        else url_relative
                    )

                    # FlexJobs berbayar — coba ambil preview deskripsi dari kartu
                    preview_el = kartu.query_selector("p[class*='description'], div[class*='description']")
                    deskripsi = preview_el.inner_text().strip() if preview_el else _ambil_deskripsi_flexjobs(browser, url_lowongan)

                    hasil.append({
                        "judul": judul_el.inner_text().strip(),
                        "perusahaan": perusahaan_el.inner_text().strip() if perusahaan_el else "",
                        "lokasi": lokasi_el.inner_text().strip() if lokasi_el else lokasi,
                        "gaji": None,
                        "gaji_angka": None,
                        "url": url_lowongan,
                        "deskripsi": deskripsi,
                    })
                    time.sleep(1)
                except Exception as e:
                    logger.warning(f"[FLEXJOBS] Gagal parse kartu: {e}")
                    continue

        except PlaywrightTimeoutError:
            logger.error("[FLEXJOBS] Timeout saat memuat halaman")
        except Exception as e:
            logger.error(f"[FLEXJOBS] Error tidak terduga: {e}")
        finally:
            browser.close()

    logger.info(f"[FLEXJOBS] Selesai. Total: {len(hasil)} lowongan")
    return hasil


# ---------------------------------------------------------------------------
# Scraper platform: Glassdoor
# ---------------------------------------------------------------------------

def scrape_glassdoor(posisi: str, lokasi: str) -> list[dict]:
    hasil = []
    url_cari = (
        f"https://www.glassdoor.com/Job/jobs.htm"
        f"?sc.keyword={posisi.replace(' ', '%20')}"
        f"&locKeyword={lokasi.replace(' ', '%20')}"
    )
    logger.info(f"[GLASSDOOR] Mulai scrape: posisi='{posisi}', lokasi='{lokasi}'")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        try:
            page.goto(url_cari, timeout=30_000)
            page.wait_for_selector(
                "li[data-test='jobListing'], article.JobCard_jobCard__wjTHv",
                timeout=15_000
            )
            time.sleep(3)

            kartu_list = (
                page.query_selector_all("li[data-test='jobListing']")
                or page.query_selector_all("article[class*='JobCard']")
            )
            logger.info(f"[GLASSDOOR] Ditemukan {len(kartu_list)} kartu lowongan")

            for kartu in kartu_list:
                try:
                    judul_el = (
                        kartu.query_selector("a[data-test='job-title']")
                        or kartu.query_selector("a[class*='jobTitle']")
                    )
                    perusahaan_el = (
                        kartu.query_selector("span[data-test='emp-name']")
                        or kartu.query_selector("div[class*='EmployerProfile']")
                    )
                    lokasi_el = (
                        kartu.query_selector("div[data-test='emp-location']")
                        or kartu.query_selector("div[class*='location']")
                    )
                    gaji_el = (
                        kartu.query_selector("div[data-test='detailSalary']")
                        or kartu.query_selector("span[class*='salary']")
                    )

                    if not judul_el:
                        continue

                    url_relative = judul_el.get_attribute("href") or ""
                    url_lowongan = (
                        "https://www.glassdoor.com" + url_relative
                        if url_relative.startswith("/")
                        else url_relative
                    )
                    teks_gaji = gaji_el.inner_text().strip() if gaji_el else None
                    deskripsi = _ambil_deskripsi_glassdoor(browser, url_lowongan)
                    hasil.append({
                        "judul": judul_el.inner_text().strip(),
                        "perusahaan": perusahaan_el.inner_text().strip() if perusahaan_el else "",
                        "lokasi": lokasi_el.inner_text().strip() if lokasi_el else lokasi,
                        "gaji": teks_gaji,
                        "gaji_angka": parse_gaji(teks_gaji),
                        "url": url_lowongan,
                        "deskripsi": deskripsi,
                    })
                    time.sleep(1)
                except Exception as e:
                    logger.warning(f"[GLASSDOOR] Gagal parse kartu: {e}")
                    continue

        except PlaywrightTimeoutError:
            logger.error("[GLASSDOOR] Timeout — mungkin anti-bot aktif atau CAPTCHA muncul")
        except Exception as e:
            logger.error(f"[GLASSDOOR] Error tidak terduga: {e}")
        finally:
            browser.close()

    logger.info(f"[GLASSDOOR] Selesai. Total: {len(hasil)} lowongan")
    return hasil


# ---------------------------------------------------------------------------
# Scraper platform: Remotive
# ---------------------------------------------------------------------------

def scrape_remotive(posisi: str, lokasi: str) -> list[dict]:
    hasil = []
    url_cari = f"https://remotive.com/remote-jobs?search={posisi.replace(' ', '+')}"
    logger.info(f"[REMOTIVE] Mulai scrape: posisi='{posisi}' (semua listing remote)")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(url_cari, timeout=30_000)
            page.wait_for_selector("li[class*='job-list-item']", timeout=15_000)

            for _ in range(3):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(2)

            kartu_list = page.query_selector_all("li[class*='job-list-item']")
            logger.info(f"[REMOTIVE] Ditemukan {len(kartu_list)} kartu lowongan")

            for kartu in kartu_list:
                try:
                    judul_el = kartu.query_selector("h2[itemprop='title'], h3[itemprop='title']")
                    perusahaan_el = kartu.query_selector("span[itemprop='name']")
                    gaji_el = kartu.query_selector("span[class*='salary']")
                    link_el = kartu.query_selector("a[href*='/remote-jobs/']")

                    if not judul_el or not link_el:
                        continue

                    url_relative = link_el.get_attribute("href") or ""
                    url_lowongan = (
                        "https://remotive.com" + url_relative
                        if url_relative.startswith("/")
                        else url_relative
                    )
                    teks_gaji = gaji_el.inner_text().strip() if gaji_el else None
                    deskripsi = _ambil_deskripsi_remotive(browser, url_lowongan)
                    hasil.append({
                        "judul": judul_el.inner_text().strip(),
                        "perusahaan": perusahaan_el.inner_text().strip() if perusahaan_el else "",
                        "lokasi": "Remote",
                        "gaji": teks_gaji,
                        "gaji_angka": parse_gaji(teks_gaji),
                        "url": url_lowongan,
                        "deskripsi": deskripsi,
                    })
                    time.sleep(1)
                except Exception as e:
                    logger.warning(f"[REMOTIVE] Gagal parse kartu: {e}")
                    continue

        except PlaywrightTimeoutError:
            logger.error("[REMOTIVE] Timeout saat memuat halaman")
        except Exception as e:
            logger.error(f"[REMOTIVE] Error tidak terduga: {e}")
        finally:
            browser.close()

    logger.info(f"[REMOTIVE] Selesai. Total: {len(hasil)} lowongan")
    return hasil


# ---------------------------------------------------------------------------
# Scraper platform: Dealls
# ---------------------------------------------------------------------------

def scrape_dealls(posisi: str, lokasi: str) -> list[dict]:
    hasil = []
    url_cari = (
        f"https://jobs.dealls.com/search"
        f"?query={posisi.replace(' ', '%20')}"
        f"&location={lokasi.replace(' ', '%20')}"
    )
    logger.info(f"[DEALLS] Mulai scrape: posisi='{posisi}', lokasi='{lokasi}'")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        try:
            page.goto(url_cari, timeout=30_000)
            page.wait_for_selector(
                "div[data-testid='job-card'], div[class*='JobCard'], div[class*='job-card']",
                timeout=15_000
            )

            for _ in range(3):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(2)

            kartu_list = (
                page.query_selector_all("div[data-testid='job-card']")
                or page.query_selector_all("div[class*='JobCard']")
                or page.query_selector_all("div[class*='job-card']")
            )
            logger.info(f"[DEALLS] Ditemukan {len(kartu_list)} kartu lowongan")

            for kartu in kartu_list:
                try:
                    judul_el = kartu.query_selector("h2, h3, [data-testid='job-title']")
                    perusahaan_el = kartu.query_selector(
                        "[data-testid='company-name'], "
                        "span[class*='company'], p[class*='company']"
                    )
                    lokasi_el = kartu.query_selector(
                        "[data-testid='job-location'], "
                        "span[class*='location'], p[class*='location']"
                    )
                    gaji_el = kartu.query_selector(
                        "[data-testid='salary'], "
                        "span[class*='salary'], p[class*='salary']"
                    )
                    link_el = kartu.query_selector("a[href*='/job/'], a[href*='/jobs/']")

                    if not judul_el or not link_el:
                        continue

                    url_relative = link_el.get_attribute("href") or ""
                    url_lowongan = (
                        "https://jobs.dealls.com" + url_relative
                        if url_relative.startswith("/")
                        else url_relative
                    )
                    teks_gaji = gaji_el.inner_text().strip() if gaji_el else None
                    deskripsi = _ambil_deskripsi_dealls(browser, url_lowongan)
                    hasil.append({
                        "judul": judul_el.inner_text().strip(),
                        "perusahaan": perusahaan_el.inner_text().strip() if perusahaan_el else "",
                        "lokasi": lokasi_el.inner_text().strip() if lokasi_el else lokasi,
                        "gaji": teks_gaji,
                        "gaji_angka": parse_gaji(teks_gaji),
                        "url": url_lowongan,
                        "deskripsi": deskripsi,
                    })
                    time.sleep(1)
                except Exception as e:
                    logger.warning(f"[DEALLS] Gagal parse kartu: {e}")
                    continue

        except PlaywrightTimeoutError:
            logger.error("[DEALLS] Timeout saat memuat halaman")
        except Exception as e:
            logger.error(f"[DEALLS] Error tidak terduga: {e}")
        finally:
            browser.close()

    logger.info(f"[DEALLS] Selesai. Total: {len(hasil)} lowongan")
    return hasil


# ---------------------------------------------------------------------------
# Scraper platform: Kalibrr
# ---------------------------------------------------------------------------

def scrape_kalibrr(posisi: str, lokasi: str) -> list[dict]:
    hasil = []
    url_cari = (
        f"https://www.kalibrr.id/job-board"
        f"?query={posisi.replace(' ', '%20')}"
        f"&location={lokasi.replace(' ', '%20')}"
    )
    logger.info(f"[KALIBRR] Mulai scrape: posisi='{posisi}', lokasi='{lokasi}'")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        try:
            page.goto(url_cari, timeout=30_000)
            page.wait_for_selector(
                "div[class*='JobCard'], li[class*='job-listing'], div[data-cy='job-card']",
                timeout=15_000
            )

            for _ in range(3):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(2)

            kartu_list = (
                page.query_selector_all("div[data-cy='job-card']")
                or page.query_selector_all("div[class*='JobCard']")
                or page.query_selector_all("li[class*='job-listing']")
            )
            logger.info(f"[KALIBRR] Ditemukan {len(kartu_list)} kartu lowongan")

            for kartu in kartu_list:
                try:
                    judul_el = (
                        kartu.query_selector("h2[data-cy='job-title'], h3[data-cy='job-title']")
                        or kartu.query_selector("h2, h3")
                    )
                    perusahaan_el = kartu.query_selector(
                        "[data-cy='company-name'], "
                        "span[class*='company'], div[class*='company']"
                    )
                    lokasi_el = kartu.query_selector(
                        "[data-cy='job-location'], "
                        "span[class*='location'], div[class*='location']"
                    )
                    gaji_el = kartu.query_selector(
                        "[data-cy='salary'], "
                        "span[class*='salary'], div[class*='salary']"
                    )
                    link_el = (
                        kartu.query_selector("a[href*='/jobs/'], a[href*='/job/']")
                        or kartu.query_selector("a")
                    )

                    if not judul_el or not link_el:
                        continue

                    url_relative = link_el.get_attribute("href") or ""
                    url_lowongan = (
                        "https://www.kalibrr.id" + url_relative
                        if url_relative.startswith("/")
                        else url_relative
                    )
                    teks_gaji = gaji_el.inner_text().strip() if gaji_el else None
                    deskripsi = _ambil_deskripsi_kalibrr(browser, url_lowongan)
                    hasil.append({
                        "judul": judul_el.inner_text().strip(),
                        "perusahaan": perusahaan_el.inner_text().strip() if perusahaan_el else "",
                        "lokasi": lokasi_el.inner_text().strip() if lokasi_el else lokasi,
                        "gaji": teks_gaji,
                        "gaji_angka": parse_gaji(teks_gaji),
                        "url": url_lowongan,
                        "deskripsi": deskripsi,
                    })
                    time.sleep(1)
                except Exception as e:
                    logger.warning(f"[KALIBRR] Gagal parse kartu: {e}")
                    continue

        except PlaywrightTimeoutError:
            logger.error("[KALIBRR] Timeout saat memuat halaman")
        except Exception as e:
            logger.error(f"[KALIBRR] Error tidak terduga: {e}")
        finally:
            browser.close()

    logger.info(f"[KALIBRR] Selesai. Total: {len(hasil)} lowongan")
    return hasil


# ---------------------------------------------------------------------------
# Dispatcher: mapping nama platform -> fungsi scraper
# ---------------------------------------------------------------------------

PLATFORM_SCRAPER: dict[str, callable] = {
    "glints": scrape_glints,
    "jobstreet": scrape_jobstreet,
    "linkedin": scrape_linkedin,
    "internshala": scrape_internshala,
    "weworkremotely": scrape_weworkremotely,
    "wellfound": scrape_wellfound,
    "flexjobs": scrape_flexjobs,
    "glassdoor": scrape_glassdoor,
    "remotive": scrape_remotive,
    "dealls": scrape_dealls,
    "kalibrr": scrape_kalibrr,
}

# Platform yang hanya menampilkan remote — lokasi diabaikan saat query URL
PLATFORM_REMOTE_ONLY = {"weworkremotely", "remotive"}


# ---------------------------------------------------------------------------
# Kelas utama Scraper
# ---------------------------------------------------------------------------

class Scraper:
    def __init__(self, config, db):
        self.config = config
        self.db = db

        self._posisi_list = _ke_list(self.config.POSISI_TARGET)
        self._lokasi_list = _ke_list(self.config.LOKASI)

        if not self._posisi_list:
            logger.warning("[SCRAPER] POSISI_TARGET kosong — tidak ada yang akan di-scrape.")
        if not self._lokasi_list:
            logger.warning("[SCRAPER] LOKASI kosong — tidak ada yang akan di-scrape.")

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def jalankan(self):
        """Entry point: iterasi semua platform, simpan semua lowongan yang lolos filter bisnis."""
        logger.info(f"[SCRAPER] Memulai scraping pada {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        logger.info(f"[SCRAPER] Posisi target : {self._posisi_list}")
        logger.info(f"[SCRAPER] Lokasi        : {self._lokasi_list}")
        logger.info(f"[SCRAPER] Platform aktif: {self.config.PLATFORM}")

        platform_tidak_dikenal = [
            p for p in self.config.PLATFORM if p not in PLATFORM_SCRAPER
        ]
        if platform_tidak_dikenal:
            logger.warning(
                f"[SCRAPER] Platform tidak dikenal dan akan dilewati: {platform_tidak_dikenal}. "
                f"Platform yang tersedia: {list(PLATFORM_SCRAPER.keys())}"
            )

        for platform in self.config.PLATFORM:
            if platform not in PLATFORM_SCRAPER:
                continue

            if self.db.sudah_scrape_hari_ini(platform):
                logger.info(f"[SCRAPER] {platform} sudah di-scrape hari ini, skip.")
                print(f"[SCRAPER] {platform} sudah di-scrape hari ini, skip.")
                continue

            logger.info(f"[SCRAPER] Memulai platform: {platform}")
            print(f"[SCRAPER] Scraping {platform}...")

            try:
                lowongan_list = self._scrape(platform)
                disimpan = self._filter_dan_simpan(lowongan_list, platform)
                print(f"[SCRAPER] {platform}: {len(lowongan_list)} ditemukan, {disimpan} disimpan.")
            except Exception as e:
                logger.error(f"[SCRAPER] Error pada platform {platform}: {e}")
                print(f"[SCRAPER] Gagal scrape {platform}: {e}")

        logger.info("[SCRAPER] Selesai.")

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _scrape(self, platform: str) -> list[dict]:
        """
        Iterasi semua kombinasi posisi × lokasi, kumpulkan semua hasil mentah.
        Tidak ada filter skor di sini — scoring dilakukan di scorer.py.
        """
        semua_hasil = []
        scraper_fn = PLATFORM_SCRAPER[platform]

        # Platform remote-only: scrape sekali per posisi
        if platform in PLATFORM_REMOTE_ONLY:
            for posisi in self._posisi_list:
                logger.info(f"[SCRAPER] [{platform}] Remote-only: posisi='{posisi}'")
                try:
                    hasil = scraper_fn(posisi=posisi, lokasi="Remote")
                    semua_hasil += hasil
                    logger.info(
                        f"[SCRAPER] [{platform}] '{posisi}': {len(hasil)} lowongan ditemukan"
                    )
                    time.sleep(1)
                except Exception as e:
                    logger.error(f"[SCRAPER] [{platform}] Gagal scrape '{posisi}': {e}")
            return semua_hasil

        # Platform biasa: iterasi semua kombinasi posisi × lokasi
        total_kombinasi = len(self._posisi_list) * len(self._lokasi_list)
        logger.info(
            f"[SCRAPER] {platform}: akan scrape "
            f"{len(self._posisi_list)} posisi × {len(self._lokasi_list)} lokasi "
            f"= {total_kombinasi} kombinasi"
        )

        for posisi in self._posisi_list:
            for lokasi in self._lokasi_list:
                logger.info(
                    f"[SCRAPER] [{platform}] Kombinasi: posisi='{posisi}', lokasi='{lokasi}'"
                )
                try:
                    hasil = scraper_fn(posisi=posisi, lokasi=lokasi)
                    semua_hasil += hasil
                    logger.info(
                        f"[SCRAPER] [{platform}] '{posisi}' di '{lokasi}': "
                        f"{len(hasil)} lowongan ditemukan"
                    )
                    time.sleep(1)
                except Exception as e:
                    logger.error(
                        f"[SCRAPER] [{platform}] Gagal scrape "
                        f"'{posisi}' di '{lokasi}': {e}"
                    )
                    continue

        logger.info(
            f"[SCRAPER] {platform}: total {len(semua_hasil)} lowongan "
            f"dari {total_kombinasi} kombinasi"
        )
        return semua_hasil

    def _filter_dan_simpan(self, lowongan_list: list[dict], platform: str) -> int:
        """
        Filter hanya berdasarkan aturan bisnis (duplikat, blacklist, gaji minimum).
        Semua yang lolos disimpan ke DB dengan status 'baru'.
        Filter skor dilakukan sepenuhnya di scorer.py setelah scoring AI selesai.
        """
        jumlah_disimpan = 0

        for item in lowongan_list:
            # 1. Skip jika URL sudah pernah disimpan
            if self.db.url_sudah_ada(item["url"]):
                logger.debug(f"[FILTER] URL duplikat, skip: {item['url']}")
                continue

            # 2. Skip jika judul mirip dari perusahaan yang sama (dalam 7 hari)
            if self.db.ada_yang_mirip(item["judul"], item["perusahaan"]):
                logger.debug(
                    f"[FILTER] Duplikat mirip: '{item['judul']}' "
                    f"dari '{item['perusahaan']}', skip."
                )
                continue

            # 3. Filter kata blacklist (judul + deskripsi)
            teks_gabung = f"{item['judul']} {item.get('deskripsi', '')}".lower()
            if any(kata.lower() in teks_gabung for kata in self.config.BLACKLIST):
                logger.debug(f"[FILTER] Blacklist match, skip: '{item['judul']}'")
                continue

            # 4. Filter gaji minimum (hanya jika info gaji tersedia)
            gaji_angka = item.get("gaji_angka")
            if gaji_angka is not None and gaji_angka < self.config.GAJI_MINIMUM:
                logger.debug(
                    f"[FILTER] Gaji di bawah minimum "
                    f"(Rp{gaji_angka:,} < Rp{self.config.GAJI_MINIMUM:,}), "
                    f"skip: '{item['judul']}'"
                )
                continue

            # Lolos semua filter — simpan ke DB dengan status 'baru'
            item["platform"] = platform
            item["status"] = "baru"
            item.setdefault("tanggal_scrape", datetime.now().isoformat())

            self.db.simpan_lowongan(item)
            jumlah_disimpan += 1
            logger.info(f"[SIMPAN] '{item['judul']}' dari '{item['perusahaan']}' ({platform})")

        return jumlah_disimpan
