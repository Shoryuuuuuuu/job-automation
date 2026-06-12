import logging
import sqlite3
import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from config import config


# ──────────────────────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)

class Database:

    def __init__(self, db_path: Path = None):
        self.db_path = str(db_path or config.DB_PATH)
        self._inisialisasi()

    # ── Koneksi ───────────────────────────────────────────────────────────────

    def _koneksi(self) -> sqlite3.Connection:
        """Buka koneksi baru. Selalu tutup setelah pakai (pakai with statement)."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    # ── Inisialisasi tabel ────────────────────────────────────────────────────

    def _inisialisasi(self):
        """Buat tabel jika belum ada. Dipanggil otomatis saat startup."""
        with self._koneksi() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS lowongan (
                    id              TEXT PRIMARY KEY,
                    judul           TEXT NOT NULL,
                    perusahaan      TEXT NOT NULL,
                    lokasi          TEXT,
                    gaji            TEXT,
                    url             TEXT UNIQUE NOT NULL,
                    platform        TEXT,
                    deskripsi       TEXT,

                    -- Hasil scoring AI
                    skor            INTEGER DEFAULT 0,
                    skill_match     TEXT DEFAULT '[]',
                    skill_gap       TEXT DEFAULT '[]',
                    alasan          TEXT,
                    rekomendasi     TEXT,

                    -- Dokumen yang digenerate
                    cover_letter    TEXT,

                    -- Status alur kerja
                    -- baru | processing | scored | pending_review | waiting_edit |
                    -- terkirim | interview | perlu_dokumen |
                    -- ditolak | skipped | auto_skip | error | error_auth | email_invalid
                    status          TEXT DEFAULT 'baru',

                    -- Timestamp
                    tanggal_scrape  TEXT,
                    tanggal_kirim   TEXT,
                    tanggal_update  TEXT
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS log_error (
                    id      INTEGER PRIMARY KEY AUTOINCREMENT,
                    modul   TEXT NOT NULL,
                    pesan   TEXT NOT NULL,
                    waktu   TEXT NOT NULL
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_status
                ON lowongan(status)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_platform_tanggal
                ON lowongan(platform, tanggal_scrape)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS konfigurasi (
                    kunci  TEXT PRIMARY KEY,
                    nilai  TEXT NOT NULL
                )
            """)
            conn.commit()

        print(f"[DB] Database siap: {self.db_path}")

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _sekarang() -> str:
        return datetime.now().isoformat(timespec="seconds")

    @staticmethod
    def _generate_id() -> str:
        """Gunakan UUID penuh untuk menghindari collision."""
        return str(uuid.uuid4())

    @staticmethod
    def _baris_ke_dict(baris) -> Optional[dict]:
        if baris is None:
            return None
        return dict(baris)

    # ── SIMPAN ────────────────────────────────────────────────────────────────

    def simpan_lowongan(self, data: dict) -> bool:
        """
        Simpan lowongan baru ke database.
        INSERT OR IGNORE — otomatis skip jika URL sudah ada.
        """
        if "id" not in data or not data["id"]:
            data["id"] = self._generate_id()

        if "tanggal_scrape" not in data:
            data["tanggal_scrape"] = self._sekarang()

        try:
            with self._koneksi() as conn:
                conn.execute("""
                    INSERT OR IGNORE INTO lowongan
                    (id, judul, perusahaan, lokasi, gaji,
                     url, platform, deskripsi, tanggal_scrape)
                    VALUES (?,?,?,?,?,?,?,?,?)
                """, [
                    data.get("id"),
                    data.get("judul", ""),
                    data.get("perusahaan", ""),
                    data.get("lokasi", ""),
                    data.get("gaji", ""),
                    data.get("url", ""),
                    data.get("platform", ""),
                    data.get("deskripsi", ""),
                    data.get("tanggal_scrape"),
                ])
                conn.commit()
            return True
        except sqlite3.Error as e:
            self.catat_error("simpan_lowongan", str(e))
            return False

    # ── UPDATE ────────────────────────────────────────────────────────────────

    def update_status(self, id: str, status_baru: str) -> bool:
        """
        Update status lowongan.
        Status valid diperluas — termasuk 'scored', 'error', 'error_auth', 'email_invalid'.
        """
        status_valid = {
            "baru", "pending_review", "waiting_edit",
            "terkirim", "interview", "perlu_dokumen",
            "ditolak", "skipped", "auto_skip", "processing",
            "scored", "error", "error_auth", "email_invalid",
        }
        if status_baru not in status_valid:
            logger_msg = f"[DB] WARNING: Status '{status_baru}' tidak dikenal."
            print(logger_msg)

        try:
            with self._koneksi() as conn:
                conn.execute("""
                    UPDATE lowongan
                    SET status=?, tanggal_update=?
                    WHERE id=?
                """, [status_baru, self._sekarang(), id])
                conn.commit()
            return True
        except sqlite3.Error as e:
            self.catat_error("update_status", str(e))
            return False

    def update_scoring(
        self,
        id: str,
        skor: int,
        skill_match: list,
        skill_gap: list,
        alasan: str,
        rekomendasi: str,
    ) -> bool:
        """Simpan hasil scoring AI ke lowongan."""
        try:
            with self._koneksi() as conn:
                conn.execute("""
                    UPDATE lowongan
                    SET skor=?, skill_match=?, skill_gap=?,
                        alasan=?, rekomendasi=?, tanggal_update=?
                    WHERE id=?
                """, [
                    skor,
                    json.dumps(skill_match, ensure_ascii=False),
                    json.dumps(skill_gap,   ensure_ascii=False),
                    alasan,
                    rekomendasi,
                    self._sekarang(),
                    id,
                ])
                conn.commit()
            return True
        except sqlite3.Error as e:
            self.catat_error("update_scoring", str(e))
            return False

    def update_cover_letter(self, id: str, teks: str) -> bool:
        """Simpan cover letter yang sudah digenerate."""
        try:
            with self._koneksi() as conn:
                conn.execute("""
                    UPDATE lowongan
                    SET cover_letter=?, tanggal_update=?
                    WHERE id=?
                """, [teks, self._sekarang(), id])
                conn.commit()
            return True
        except sqlite3.Error as e:
            self.catat_error("update_cover_letter", str(e))
            return False

    def catat_waktu_kirim(self, id: str) -> bool:
        """Catat waktu kirim dan set status ke 'terkirim'."""
        try:
            with self._koneksi() as conn:
                conn.execute("""
                    UPDATE lowongan
                    SET tanggal_kirim=?, status='terkirim', tanggal_update=?
                    WHERE id=?
                """, [self._sekarang(), self._sekarang(), id])
                conn.commit()
            return True
        except sqlite3.Error as e:
            self.catat_error("catat_waktu_kirim", str(e))
            return False

    def catat_error(self, modul: str, pesan: str):
        """Simpan log error dari modul manapun."""
        try:
            with self._koneksi() as conn:
                conn.execute("""
                    INSERT INTO log_error (modul, pesan, waktu)
                    VALUES (?,?,?)
                """, [modul, pesan, self._sekarang()])
                conn.commit()
        except Exception:
            pass

    # ── QUERY / BACA ──────────────────────────────────────────────────────────

    def cari_by_id(self, id: str) -> Optional[dict]:
        """Cari satu lowongan berdasarkan ID."""
        with self._koneksi() as conn:
            baris = conn.execute(
                "SELECT * FROM lowongan WHERE id=?", [id]
            ).fetchone()
        return self._baris_ke_dict(baris)

    def url_sudah_ada(self, url: str) -> bool:
        """Cek apakah URL lowongan sudah pernah di-scrape."""
        with self._koneksi() as conn:
            baris = conn.execute(
                "SELECT id FROM lowongan WHERE url=?", [url]
            ).fetchone()
        return baris is not None

    def ada_yang_mirip(self, judul: str, perusahaan: str) -> bool:
        """
        Cek apakah ada lowongan dengan judul mirip dari perusahaan yang sama
        dalam 7 hari terakhir. Mencegah duplikat dengan URL berbeda.
        """
        tujuh_hari_lalu = (
            datetime.now() - timedelta(days=7)
        ).isoformat(timespec="seconds")

        with self._koneksi() as conn:
            rows = conn.execute("""
                SELECT judul FROM lowongan
                WHERE perusahaan=?
                AND tanggal_scrape >= ?
            """, [perusahaan, tujuh_hari_lalu]).fetchall()

        if not rows:
            return False

        kata_judul = set(judul.lower().split())
        for row in rows:
            kata_existing = set(row["judul"].lower().split())
            if not kata_judul or not kata_existing:
                continue
            irisan = kata_judul & kata_existing
            similarity = len(irisan) / max(len(kata_judul), len(kata_existing))
            if similarity >= 0.6:
                return True

        return False

    def sudah_scrape_hari_ini(self, platform: str) -> bool:
        """Cek apakah platform sudah di-scrape hari ini."""
        awal_hari = datetime.now().replace(
            hour=0, minute=0, second=0, microsecond=0
        ).isoformat(timespec="seconds")

        with self._koneksi() as conn:
            baris = conn.execute("""
                SELECT COUNT(*) as n FROM lowongan
                WHERE platform=? AND tanggal_scrape >= ?
            """, [platform, awal_hari]).fetchone()

        return baris["n"] > 0

    def ambil_by_status(self, status: str) -> list[dict]:
        """Ambil semua lowongan dengan status tertentu, terbaru dulu."""
        with self._koneksi() as conn:
            rows = conn.execute("""
                SELECT * FROM lowongan
                WHERE status=?
                ORDER BY tanggal_scrape DESC
            """, [status]).fetchall()
        return [self._baris_ke_dict(r) for r in rows]

    def ambil_waiting_edit_kadaluarsa(self, hari: int = 3) -> list[dict]:
        """Ambil lowongan waiting_edit yang sudah lebih dari X hari."""
        batas = (
            datetime.now() - timedelta(days=hari)
        ).isoformat(timespec="seconds")

        with self._koneksi() as conn:
            rows = conn.execute("""
                SELECT * FROM lowongan
                WHERE status='waiting_edit'
                AND tanggal_update < ?
            """, [batas]).fetchall()
        return [self._baris_ke_dict(r) for r in rows]

    def ambil_terkirim_belum_dibalas(self, hari: int = 14) -> list[dict]:
        """Ambil lamaran yang sudah terkirim lebih dari X hari tanpa update."""
        batas = (
            datetime.now() - timedelta(days=hari)
        ).isoformat(timespec="seconds")

        with self._koneksi() as conn:
            rows = conn.execute("""
                SELECT * FROM lowongan
                WHERE status='terkirim'
                AND tanggal_kirim < ?
            """, [batas]).fetchall()
        return [self._baris_ke_dict(r) for r in rows]

    # ── STATISTIK ─────────────────────────────────────────────────────────────

    def ambil_statistik(self) -> dict:
        """
        Hitung statistik keseluruhan untuk laporan harian.
        Satu query tunggal dengan COUNT(CASE WHEN ...) — lebih efisien.
        """
        with self._koneksi() as conn:
            row = conn.execute("""
                SELECT
                    COUNT(*)                                             AS total,
                    COUNT(CASE WHEN status='terkirim'       THEN 1 END) AS terkirim,
                    COUNT(CASE WHEN status='interview'      THEN 1 END) AS interview,
                    COUNT(CASE WHEN status='pending_review' THEN 1 END) AS pending,
                    COUNT(CASE WHEN status='waiting_edit'   THEN 1 END) AS waiting_edit,
                    COUNT(CASE WHEN status='perlu_dokumen'  THEN 1 END) AS perlu_dokumen,
                    COUNT(CASE WHEN status='ditolak'        THEN 1 END) AS ditolak,
                    COUNT(CASE WHEN status='skipped'        THEN 1 END) AS skipped,
                    COUNT(CASE WHEN status='auto_skip'      THEN 1 END) AS auto_skip
                FROM lowongan
            """).fetchone()

        terkirim  = row["terkirim"]
        interview = row["interview"]

        return {
            "total"        : row["total"],
            "terkirim"     : terkirim,
            "pending"      : row["pending"],
            "waiting_edit" : row["waiting_edit"],
            "interview"    : interview,
            "perlu_dokumen": row["perlu_dokumen"],
            "ditolak"      : row["ditolak"],
            "skipped"      : row["skipped"],
            "auto_skip"    : row["auto_skip"],
            "win_rate"     : round(interview / terkirim * 100, 1) if terkirim > 0 else 0.0,
        }

    def ambil_log_error(self, limit: int = 20) -> list[dict]:
        """Ambil log error terbaru."""
        with self._koneksi() as conn:
            rows = conn.execute("""
                SELECT * FROM log_error
                ORDER BY waktu DESC
                LIMIT ?
            """, [limit]).fetchall()
        return [self._baris_ke_dict(r) for r in rows]


    def ambil_semua_terkirim(self) -> list[dict]:
        """Ambil semua lowongan yang sudah terkirim — dipakai email monitor."""
        with self._koneksi() as conn:
            rows = conn.execute("""
                SELECT * FROM lowongan
                WHERE status IN ('terkirim', 'interview', 'waiting_edit')
                ORDER BY tanggal_kirim DESC
            """).fetchall()
        return [self._baris_ke_dict(r) for r in rows]

    def ambil_email_processed_uids(self) -> set[str]:
        """Ambil set UID email yang sudah diproses."""
        try:
            with self._koneksi() as conn:
                row = conn.execute("""
                    SELECT nilai FROM konfigurasi WHERE kunci='email_processed_uids'
                """).fetchone()
            if row:
                return set(json.loads(row["nilai"]))
            return set()
        except Exception:
            return set()

    def simpan_email_processed_uids(self, uids: set[str]):
        """Simpan set UID email yang sudah diproses."""
        try:
            with self._koneksi() as conn:
                conn.execute("""
                    INSERT INTO konfigurasi (kunci, nilai)
                    VALUES ('email_processed_uids', ?)
                    ON CONFLICT(kunci) DO UPDATE SET nilai=excluded.nilai
                """, [json.dumps(list(uids))])
                conn.commit()
        except Exception as e:
            logger.warning(f"[DB] Gagal simpan email UIDs: {e}")

    # ── RESET / MAINTENANCE ───────────────────────────────────────────────────

    def reset_status_processing(self):
        """
        Reset lowongan yang stuck di status 'processing' atau 'scored'.
        Dipanggil saat startup untuk pulihkan dari crash sebelumnya.
        """
        with self._koneksi() as conn:
            conn.execute("""
                UPDATE lowongan SET status='baru', tanggal_update=?
                WHERE status IN ('processing', 'scored')
            """, [self._sekarang()])
            jumlah = conn.execute("SELECT changes()").fetchone()[0]
            conn.commit()

        if jumlah > 0:
            print(f"[DB] Resume: {jumlah} lowongan direset dari 'processing/scored' → 'baru'")


# ── Instance tunggal yang dipakai semua modul ─────────────────────────────────
db = Database()