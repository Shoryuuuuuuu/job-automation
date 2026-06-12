"""
main.py — Entry point Job Automation Pipeline

Alur kerja harian (dijalankan otomatis pukul 21:00):
  1. scrape_semua_platform       — ambil lowongan baru dari Jobstreet
  2. scoring_semua_lowongan_baru — scoring AI via Ollama, filter top-10
  3. generate_dokumen_pending    — buat cover letter untuk pending_review
  4. apply_otomatis              — auto-apply ke Jobstreet
  5. cek_waiting_edit_kadaluarsa — eskalasi lowongan yang terlalu lama
  6. kirim_laporan_harian        — ringkasan statistik harian ke Telegram

Monitor email (setiap 30 menit):
  - Cek inbox Gmail untuk notifikasi dari Jobstreet
  - Update status DB + kirim notifikasi Telegram
"""

import logging
import sys
import time
from datetime import datetime

import schedule

from config import config
from database import db
from scraper import Scraper
from scorer import Scorer
from generator import DocumentGenerator
from applier import Applier
from notifier import TelegramNotifier
from email_monitor import EmailMonitor

# ── Setup logging ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(str(config.LOG_PATH), encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


# ── Inisialisasi satu kali ────────────────────────────────────────────────────
scraper       = Scraper(config, db)
scorer        = Scorer(config, db)
generator     = DocumentGenerator(config, db)
applier       = Applier(config, db)
notifier      = TelegramNotifier(config, db)
email_monitor = EmailMonitor(config, db)


# ── Langkah-langkah pipeline harian ──────────────────────────────────────────

def scrape_semua_platform():
    logger.info("[PIPELINE] Langkah 1/6 — Scraping Jobstreet...")
    scraper.jalankan()


def scoring_semua_lowongan_baru():
    logger.info("[PIPELINE] Langkah 2/6 — Scoring & filter top-10...")
    scorer.jalankan()


def generate_dokumen_pending():
    logger.info("[PIPELINE] Langkah 3/6 — Generate cover letter (AI)...")
    generator.jalankan()


def apply_otomatis():
    logger.info("[PIPELINE] Langkah 4/6 — Auto-apply ke Jobstreet...")
    applier.jalankan()


def cek_waiting_edit_kadaluarsa():
    logger.info("[PIPELINE] Langkah 5/6 — Cek waiting_edit kadaluarsa...")
    kadaluarsa = db.ambil_waiting_edit_kadaluarsa(hari=3)
    if not kadaluarsa:
        logger.info("[PIPELINE] Tidak ada waiting_edit yang kadaluarsa.")
        return
    for lowongan in kadaluarsa:
        db.update_status(lowongan["id"], "skipped")
        logger.warning(
            "[PIPELINE] Kadaluarsa — '%s' (%s) di-skip karena >3 hari.",
            lowongan["judul"], lowongan["perusahaan"],
        )
    logger.info("[PIPELINE] %d lowongan waiting_edit di-skip.", len(kadaluarsa))


def kirim_laporan_harian():
    logger.info("[PIPELINE] Langkah 6/6 — Kirim laporan harian ke Telegram...")
    notifier.kirim_laporan_harian()


# ── Monitor email (setiap 30 menit) ──────────────────────────────────────────

def cek_email_jobstreet():
    logger.info("[SCHEDULE] Cek email Jobstreet...")
    try:
        email_monitor.jalankan()
    except Exception:
        logger.exception("[SCHEDULE] Email monitor gagal.")


# ── Inti pipeline harian ──────────────────────────────────────────────────────

def jalankan_pipeline():
    mulai = datetime.now()
    logger.info("=" * 55)
    logger.info("  PIPELINE DIMULAI  —  %s", mulai.strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("=" * 55)

    langkah = [
        scrape_semua_platform,
        scoring_semua_lowongan_baru,
        generate_dokumen_pending,
        apply_otomatis,
        cek_waiting_edit_kadaluarsa,
        kirim_laporan_harian,
    ]

    for fn in langkah:
        try:
            fn()
        except Exception:
            logger.exception(
                "[PIPELINE] Langkah '%s' gagal, lanjut ke berikutnya.", fn.__name__
            )

    durasi = (datetime.now() - mulai).seconds
    logger.info("=" * 55)
    logger.info("  PIPELINE SELESAI  —  durasi %ds", durasi)
    logger.info("=" * 55)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    config.tampilkan()
    if not config.validasi():
        logger.error("Konfigurasi tidak valid. Pipeline dihentikan.")
        sys.exit(1)

    db.reset_status_processing()

    # Pipeline harian — setiap hari pukul 10:15
    schedule.every().day.at("10:15").do(jalankan_pipeline)
    logger.info("Pipeline harian dijadwalkan pukul 10:15.")

    # Monitor email — setiap 30 menit
    schedule.every(30).minutes.do(cek_email_jobstreet)
    logger.info("Monitor email dijadwalkan setiap 30 menit.")

    # Jalankan cek email sekali langsung saat startup
    logger.info("Cek email pertama kali saat startup...")
    cek_email_jobstreet()

    logger.info("Menunggu jadwal berikutnya...")

    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    main()
