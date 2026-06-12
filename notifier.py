import asyncio
import json
import logging

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackContext

logger = logging.getLogger(__name__)

# Jeda antar pesan Telegram (detik) — mencegah rate limit
_TELEGRAM_DELAY = 0.5


class TelegramNotifier:
    def __init__(self, config, db):
        self.config = config
        self.db = db
        self.bot = Bot(token=self.config.TELEGRAM_TOKEN)

    # ------------------------------------------------------------------
    # Kirim laporan harian
    # ------------------------------------------------------------------

    def kirim_laporan_harian(self):
        asyncio.run(self._kirim_laporan_harian_async())

    async def _kirim_laporan_harian_async(self):
        try:
            stats = self.db.ambil_statistik()
            pesan = (
                "📊 *Laporan Harian*\n\n"
                f"📋 Total lowongan : {stats['total']}\n"
                f"📤 Terkirim       : {stats['terkirim']}\n"
                f"🎯 Interview      : {stats['interview']}\n"
                f"⏳ Pending review : {stats['pending']}\n"
                f"🏆 Win rate       : {stats['win_rate']}%"
            )
            await self.bot.send_message(
                chat_id=self.config.TELEGRAM_CHAT_ID,
                text=pesan,
                parse_mode="Markdown",
            )
            logger.info("Laporan harian berhasil dikirim.")
        except Exception as exc:
            logger.error("Gagal mengirim laporan harian: %s", exc)

    # ------------------------------------------------------------------
    # Kirim notifikasi hasil apply
    # ------------------------------------------------------------------

    def kirim_hasil_apply(self):
        """Kirim ringkasan hasil apply hari ini ke Telegram."""
        asyncio.run(self._kirim_hasil_apply_async())

    async def _kirim_hasil_apply_async(self):
        try:
            terkirim = self.db.ambil_by_status("terkirim")
            error    = self.db.ambil_by_status("error")

            if not terkirim and not error:
                return

            baris_terkirim = ""
            for l in terkirim[-10:]:  # maks 10 item agar tidak terlalu panjang
                baris_terkirim += f"  ✅ {l['judul']} — {l['perusahaan']}\n"

            baris_error = ""
            for l in error[-5:]:
                baris_error += f"  ❌ {l['judul']} — {l['perusahaan']}\n"

            pesan = "🚀 *Hasil Auto-Apply Hari Ini*\n\n"
            if baris_terkirim:
                pesan += f"*Berhasil dikirim ({len(terkirim)}):*\n{baris_terkirim}\n"
            if baris_error:
                pesan += f"*Gagal ({len(error)}):*\n{baris_error}"

            await self.bot.send_message(
                chat_id=self.config.TELEGRAM_CHAT_ID,
                text=pesan,
                parse_mode="Markdown",
            )
            logger.info("Notifikasi hasil apply berhasil dikirim.")
        except Exception as exc:
            logger.error("Gagal kirim notifikasi hasil apply: %s", exc)