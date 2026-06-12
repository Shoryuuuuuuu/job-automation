import logging
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

logger = logging.getLogger(__name__)


class EmailSender:
    def __init__(self, config, db):
        """
        Args:
            config: objek konfigurasi dengan atribut:
                    EMAIL, NAMA, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, DIR_CV
            db:     objek database dengan method:
                    cari_by_id, catat_waktu_kirim, catat_error, update_status
        """
        self.config = config
        self.db = db

    # ------------------------------------------------------------------
    # Entry point utama
    # ------------------------------------------------------------------
    def kirim(self, job_id: str) -> bool:
        """
        Kirim email lamaran untuk lowongan dengan job_id tertentu.

        Returns:
            True  jika email berhasil terkirim.
            False jika lowongan tidak ditemukan atau terjadi error pengiriman.
        """
        lowongan = self.db.cari_by_id(job_id)
        if not lowongan:
            logger.warning("Lowongan id=%s tidak ditemukan di database.", job_id)
            return False

        msg = self._buat_pesan(lowongan)

        cv_path = Path(self.config.DIR_CV) / "cv.pdf"
        self._lampirkan_cv(msg, cv_path)

        return self._kirim_smtp(msg, job_id)

    # ------------------------------------------------------------------
    # Susun objek MIMEMultipart
    # ------------------------------------------------------------------
    def _buat_pesan(self, lowongan: dict) -> MIMEMultipart:
        msg = MIMEMultipart()
        msg["From"] = self.config.EMAIL
        msg["To"] = self._resolve_email_penerima(lowongan)
        msg["Subject"] = (
            f"Lamaran: {lowongan['judul']} \u2014 {self.config.NAMA}"
        )

        cover_letter = lowongan.get("cover_letter", "").strip()
        if not cover_letter:
            logger.warning(
                "Cover letter kosong untuk lowongan id=%s.", lowongan["id"]
            )
        msg.attach(MIMEText(cover_letter, "plain", "utf-8"))

        return msg

    # ------------------------------------------------------------------
    # Tentukan alamat email penerima
    # ------------------------------------------------------------------
    def _resolve_email_penerima(self, lowongan: dict) -> str:
        # Gunakan email eksplisit jika sudah tersimpan di DB
        if lowongan.get("email_hr"):
            return lowongan["email_hr"]

        # Fallback: tebak dari nama perusahaan (perlu validasi manual)
        domain = lowongan["perusahaan"].lower().replace(" ", "")
        fallback = f"hr@{domain}.com"
        logger.warning(
            "Email HR tidak tersedia untuk '%s', fallback ke %s",
            lowongan["perusahaan"],
            fallback,
        )
        return fallback

    # ------------------------------------------------------------------
    # Lampirkan CV PDF jika file tersedia
    # ------------------------------------------------------------------
    def _lampirkan_cv(self, msg: MIMEMultipart, cv_path: Path) -> None:
        if not cv_path.exists():
            logger.warning("File CV tidak ditemukan di path: %s", cv_path)
            return

        try:
            with open(cv_path, "rb") as f:
                attachment = MIMEApplication(f.read(), _subtype="pdf")
            attachment.add_header(
                "Content-Disposition", "attachment", filename="CV.pdf"
            )
            msg.attach(attachment)
            logger.debug("CV berhasil dilampirkan dari %s.", cv_path)
        except OSError as exc:
            logger.error("Gagal membaca file CV: %s", exc)

    # ------------------------------------------------------------------
    # Kirim via SMTP dan catat hasilnya ke DB
    # ------------------------------------------------------------------
    def _kirim_smtp(self, msg: MIMEMultipart, job_id: str) -> bool:
        host = self.config.SMTP_HOST
        port = int(self.config.SMTP_PORT)

        try:
            with smtplib.SMTP(host, port, timeout=30) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.ehlo()
                smtp.login(self.config.SMTP_USER, self.config.SMTP_PASS)
                smtp.send_message(msg)

            self.db.catat_waktu_kirim(job_id)  # update status → "terkirim"
            logger.info(
                "Email berhasil dikirim ke %s untuk lowongan id=%s.",
                msg["To"],
                job_id,
            )
            return True

        except smtplib.SMTPAuthenticationError as exc:
            error_msg = f"Autentikasi SMTP gagal: {exc}"
            logger.error(error_msg)
            self.db.catat_error("sender", error_msg)
            self.db.update_status(job_id, "error_auth")

        except smtplib.SMTPRecipientsRefused as exc:
            error_msg = f"Penerima ditolak server: {exc}"
            logger.error(error_msg)
            self.db.catat_error("sender", error_msg)
            self.db.update_status(job_id, "email_invalid")

        except smtplib.SMTPException as exc:
            error_msg = f"SMTP error: {exc}"
            logger.error(error_msg)
            self.db.catat_error("sender", error_msg)
            self.db.update_status(job_id, "perlu_dokumen")

        except OSError as exc:
            # Koneksi gagal (timeout, DNS, dsb.)
            error_msg = f"Koneksi SMTP gagal: {exc}"
            logger.error(error_msg)
            self.db.catat_error("sender", error_msg)
            self.db.update_status(job_id, "perlu_dokumen")

        return False