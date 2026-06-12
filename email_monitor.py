"""
email_monitor.py — Monitor inbox Gmail untuk notifikasi dari Jobstreet.

Cara kerja:
  1. Konek ke Gmail via IMAP (SSL)
  2. Cari email dari domain Jobstreet/Seek yang belum diproses
  3. Parse subject untuk ekstrak perusahaan + tipe event
  4. Cocokkan dengan lowongan di DB
  5. Update status DB + kirim notifikasi Telegram

Tipe event yang dideteksi:
  - viewed      → perusahaan melihat lamaranmu
  - message     → ada pesan baru dari perusahaan
  - interview   → undangan interview
  - rejected    → lamaran ditolak
  - update      → update lain dari Jobstreet

Setup Gmail:
  - Aktifkan IMAP di Gmail Settings → Forwarding and POP/IMAP
  - Gunakan App Password (bukan password biasa) di .env:
    SMTP_USER=emailkamu@gmail.com
    SMTP_PASS=xxxx-xxxx-xxxx-xxxx  (App Password 16 karakter)
"""

import asyncio
import email
import imaplib
import json
import logging
import re
import time
from datetime import datetime, timedelta
from email.header import decode_header

from telegram import Bot

logger = logging.getLogger(__name__)

# Pengirim email Jobstreet yang dikenal
JOBSTREET_SENDERS = {
    "no-reply@jobstreet.co.id",
    "noreply@jobstreet.co.id",
    "no-reply@seek.com.au",
    "noreply@seek.com.au",
    "notifications@seek.com.au",
    "jobstreet@e.jobstreet.com",
    "no-reply@jobstreet.com",
    "alerts@jobstreet.co.id",
}

# Keyword di subject → tipe event
# Urutan penting: cek yang lebih spesifik dulu
EVENT_PATTERNS = [
    # Interview
    (r"interview",                              "interview"),
    (r"undangan.*wawancara",                    "interview"),
    (r"wawancara",                              "interview"),
    (r"invited.*interview",                     "interview"),

    # Ditolak
    (r"not.*progress",                          "rejected"),
    (r"unsuccessful",                           "rejected"),
    (r"tidak.*lanjut",                          "rejected"),
    (r"ditolak",                                "rejected"),
    (r"we.*regret",                             "rejected"),

    # Pesan dari perusahaan
    (r"new.*message",                           "message"),
    (r"pesan.*baru",                            "message"),
    (r"has.*messaged",                          "message"),
    (r"replied",                                "message"),

    # Dilihat
    (r"viewed.*application",                    "viewed"),
    (r"melihat.*lamaran",                       "viewed"),
    (r"has.*viewed",                            "viewed"),
    (r"profile.*viewed",                        "viewed"),

    # Shortlisted
    (r"shortlist",                              "shortlisted"),
    (r"shortlisted",                            "shortlisted"),

    # Fallback
    (r"application",                            "update"),
    (r"lamaran",                                "update"),
]

# Emoji per tipe event untuk notifikasi Telegram
EVENT_EMOJI = {
    "interview"  : "🎯",
    "rejected"   : "❌",
    "message"    : "💬",
    "viewed"     : "👀",
    "shortlisted": "⭐",
    "update"     : "📬",
}

# Status DB yang akan di-set per event
EVENT_TO_STATUS = {
    "interview"  : "interview",
    "rejected"   : "ditolak",
    "message"    : "waiting_edit",   # perlu aksi — buka dan balas
    "viewed"     : None,             # tidak ubah status, cukup notif
    "shortlisted": "waiting_edit",
    "update"     : None,
}


# ---------------------------------------------------------------------------
# Helper: decode header email (subject bisa encoded)
# ---------------------------------------------------------------------------

def _decode_subject(raw_subject: str) -> str:
    parts = decode_header(raw_subject)
    decoded = []
    for part, enc in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(enc or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return " ".join(decoded)


def _decode_sender(raw_from: str) -> str:
    """Ekstrak alamat email dari header From."""
    match = re.search(r"<([^>]+)>", raw_from)
    if match:
        return match.group(1).lower().strip()
    return raw_from.lower().strip()


# ---------------------------------------------------------------------------
# Helper: deteksi tipe event dari subject
# ---------------------------------------------------------------------------

def _deteksi_event(subject: str) -> str:
    subject_lower = subject.lower()
    for pattern, event in EVENT_PATTERNS:
        if re.search(pattern, subject_lower):
            return event
    return "update"


# ---------------------------------------------------------------------------
# Helper: ekstrak nama perusahaan dari subject
# ---------------------------------------------------------------------------

def _ekstrak_perusahaan(subject: str) -> str | None:
    """
    Coba ekstrak nama perusahaan dari subject email Jobstreet.
    Contoh subject:
    - "Your application to Data Analyst at PT ABC has been viewed"
    - "PT XYZ has messaged you about your application"
    - "Interview invitation from PT ABC for Data Scientist"
    """
    subject_lower = subject.lower()

    # Pola "at [Perusahaan]"
    m = re.search(r"\bat\s+([A-Z][^,\n]+?)(?:\s+has|\s+is|\s+for|$)", subject, re.IGNORECASE)
    if m:
        return m.group(1).strip()

    # Pola "[Perusahaan] has ..."
    m = re.search(r"^([A-Z][^,\n]+?)\s+has\s+", subject, re.IGNORECASE)
    if m:
        nama = m.group(1).strip()
        # Hindari false positive seperti "Your application has..."
        if not any(w in nama.lower() for w in ("your", "the", "a ", "an ")):
            return nama

    # Pola "from [Perusahaan]"
    m = re.search(r"\bfrom\s+([A-Z][^,\n]+?)(?:\s+for|\s+about|$)", subject, re.IGNORECASE)
    if m:
        return m.group(1).strip()

    return None


# ---------------------------------------------------------------------------
# Helper: cocokkan nama perusahaan dengan DB
# ---------------------------------------------------------------------------

def _cari_lowongan_by_perusahaan(db, nama_perusahaan: str) -> list[dict]:
    """
    Cari lowongan di DB yang nama perusahaannya mirip.
    Pakai similarity sederhana berbasis kata.
    """
    if not nama_perusahaan:
        return []

    kata_target = set(nama_perusahaan.lower().split())
    semua = db.ambil_semua_terkirim()  # ambil semua yang sudah dikirim

    cocok = []
    for lowongan in semua:
        kata_db = set(lowongan.get("perusahaan", "").lower().split())
        if not kata_db:
            continue
        irisan = kata_target & kata_db
        similarity = len(irisan) / max(len(kata_target), len(kata_db))
        if similarity >= 0.5:  # 50% kata sama = match
            cocok.append(lowongan)

    return cocok


# ---------------------------------------------------------------------------
# Kelas utama EmailMonitor
# ---------------------------------------------------------------------------

class EmailMonitor:
    def __init__(self, config, db):
        self.config = config
        self.db = db
        self.bot = Bot(token=self.config.TELEGRAM_TOKEN)

        # Set untuk track UID email yang sudah diproses (hindari duplikat)
        # Disimpan di DB agar persist antar restart
        self._processed_uids: set[str] = self._muat_processed_uids()

    # ------------------------------------------------------------------
    # Public: entry point
    # ------------------------------------------------------------------

    def jalankan(self):
        """Sync wrapper — dipanggil dari scheduler."""
        asyncio.run(self._jalankan_async())

    async def _jalankan_async(self):
        logger.info("[EMAIL] Mulai cek inbox Gmail...")
        print("[EMAIL] Cek inbox Gmail...")

        try:
            email_list = self._ambil_email_jobstreet()
        except Exception as e:
            logger.error(f"[EMAIL] Gagal konek ke Gmail: {e}")
            print(f"[EMAIL] Gagal konek Gmail: {e}")
            return

        if not email_list:
            logger.info("[EMAIL] Tidak ada email Jobstreet baru.")
            print("[EMAIL] Tidak ada email Jobstreet baru.")
            return

        print(f"[EMAIL] Ditemukan {len(email_list)} email Jobstreet baru.")
        logger.info(f"[EMAIL] Ditemukan {len(email_list)} email Jobstreet baru.")

        for item in email_list:
            await self._proses_satu_email(item)

        # Simpan UID yang sudah diproses
        self._simpan_processed_uids()

    # ------------------------------------------------------------------
    # Koneksi Gmail via IMAP
    # ------------------------------------------------------------------

    def _ambil_email_jobstreet(self) -> list[dict]:
        """
        Konek ke Gmail via IMAP, cari email dari Jobstreet
        dalam 30 hari terakhir yang belum diproses.
        """
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        mail.login(self.config.SMTP_USER, self.config.SMTP_PASS)
        mail.select("INBOX")

        # Cari email 30 hari terakhir
        tanggal_batas = (datetime.now() - timedelta(days=30)).strftime("%d-%b-%Y")
        _, data = mail.search(None, f'(SINCE "{tanggal_batas}")')

        uid_list = data[0].split()
        logger.info(f"[EMAIL] Total email 30 hari terakhir: {len(uid_list)}")

        hasil = []
        for uid in uid_list:
            uid_str = uid.decode()

            # Skip yang sudah diproses
            if uid_str in self._processed_uids:
                continue

            try:
                _, msg_data = mail.fetch(uid, "(RFC822)")
                raw = msg_data[0][1]
                msg = email.message_from_bytes(raw)

                sender = _decode_sender(msg.get("From", ""))

                # Filter hanya dari Jobstreet
                domain_sender = sender.split("@")[-1] if "@" in sender else ""
                is_jobstreet = (
                    sender in JOBSTREET_SENDERS
                    or "jobstreet" in domain_sender
                    or "seek.com" in domain_sender
                )

                if not is_jobstreet:
                    continue

                subject = _decode_subject(msg.get("Subject", ""))
                tanggal = msg.get("Date", "")

                hasil.append({
                    "uid"    : uid_str,
                    "sender" : sender,
                    "subject": subject,
                    "tanggal": tanggal,
                })

            except Exception as e:
                logger.warning(f"[EMAIL] Gagal baca email UID {uid_str}: {e}")
                continue

        mail.logout()
        return hasil

    # ------------------------------------------------------------------
    # Proses satu email
    # ------------------------------------------------------------------

    async def _proses_satu_email(self, item: dict):
        subject     = item["subject"]
        uid         = item["uid"]
        tanggal     = item["tanggal"]

        logger.info(f"[EMAIL] Proses: '{subject}'")

        event       = _deteksi_event(subject)
        perusahaan  = _ekstrak_perusahaan(subject)
        emoji       = EVENT_EMOJI.get(event, "📬")
        status_baru = EVENT_TO_STATUS.get(event)

        # Cari lowongan yang cocok di DB
        lowongan_cocok = _cari_lowongan_by_perusahaan(self.db, perusahaan) if perusahaan else []

        # Update status DB
        if status_baru and lowongan_cocok:
            for lw in lowongan_cocok:
                self.db.update_status(lw["id"], status_baru)
                logger.info(
                    f"[EMAIL] Update status '{lw['judul']}' @ '{lw['perusahaan']}' "
                    f"→ '{status_baru}'"
                )

        # Susun pesan Telegram
        pesan = self._format_pesan_telegram(
            event=event,
            emoji=emoji,
            subject=subject,
            perusahaan=perusahaan,
            tanggal=tanggal,
            lowongan_cocok=lowongan_cocok,
            status_baru=status_baru,
        )

        # Kirim ke Telegram
        try:
            await self.bot.send_message(
                chat_id=self.config.TELEGRAM_CHAT_ID,
                text=pesan,
                parse_mode="Markdown",
            )
            logger.info(f"[EMAIL] Notifikasi Telegram terkirim untuk: '{subject}'")
        except Exception as e:
            logger.error(f"[EMAIL] Gagal kirim Telegram: {e}")

        # Tandai sudah diproses
        self._processed_uids.add(uid)

    # ------------------------------------------------------------------
    # Format pesan Telegram
    # ------------------------------------------------------------------

    def _format_pesan_telegram(
        self,
        event: str,
        emoji: str,
        subject: str,
        perusahaan: str | None,
        tanggal: str,
        lowongan_cocok: list[dict],
        status_baru: str | None,
    ) -> str:

        label_event = {
            "interview"  : "Undangan Interview!",
            "rejected"   : "Lamaran Ditolak",
            "message"    : "Pesan Baru dari Perusahaan",
            "viewed"     : "Lamaran Dilihat",
            "shortlisted": "Kamu Masuk Shortlist!",
            "update"     : "Update Lamaran",
        }.get(event, "Update Lamaran")

        baris = [
            f"{emoji} *{label_event}*",
            f"",
            f"📧 Subject: `{subject}`",
        ]

        if perusahaan:
            baris.append(f"🏢 Perusahaan: *{perusahaan}*")

        if tanggal:
            baris.append(f"🕐 Diterima: {tanggal[:25]}")

        if lowongan_cocok:
            baris.append(f"")
            baris.append(f"📋 *Lowongan yang cocok di DB:*")
            for lw in lowongan_cocok[:3]:  # maks 3
                baris.append(f"  • {lw['judul']} — {lw['perusahaan']}")
            if status_baru:
                baris.append(f"  ✅ Status diupdate → `{status_baru}`")
        else:
            baris.append(f"")
            baris.append(f"⚠️ Tidak ada lowongan yang cocok di database.")

        return "\n".join(baris)

    # ------------------------------------------------------------------
    # Persist processed UIDs ke DB
    # ------------------------------------------------------------------

    def _muat_processed_uids(self) -> set[str]:
        """Muat daftar UID yang sudah diproses dari DB."""
        try:
            return self.db.ambil_email_processed_uids()
        except Exception:
            return set()

    def _simpan_processed_uids(self):
        """Simpan UID yang sudah diproses ke DB."""
        try:
            self.db.simpan_email_processed_uids(self._processed_uids)
        except Exception as e:
            logger.warning(f"[EMAIL] Gagal simpan processed UIDs: {e}")