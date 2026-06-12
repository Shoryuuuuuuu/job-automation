import os
from pathlib import Path
from dotenv import load_dotenv


# ── Muat file .env ────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")


def _env(key: str, default=None, required: bool = False):
    value = os.getenv(key, default)
    if required and not value:
        raise ValueError(
            f"[CONFIG] Variable '{key}' wajib diisi di file .env\n"
            f"Contoh: lihat file .env.example"
        )
    return value


def _env_int(key: str, default: int = 0) -> int:
    try:
        return int(os.getenv(key, default))
    except (ValueError, TypeError):
        return default


def _env_list(key: str, default: list = None) -> list:
    value = os.getenv(key)
    if not value:
        return default or []
    return [item.strip() for item in value.split(",") if item.strip()]


def _baca_cv() -> str:
    cv_path = BASE_DIR / "output" / "cv" / "cv.txt"
    if not cv_path.exists():
        print(
            f"[CONFIG] WARNING: File CV tidak ditemukan di {cv_path}\n"
            f"         Buat file tersebut dan isi dengan teks CV kamu."
        )
        return ""
    return cv_path.read_text(encoding="utf-8")


# ──────────────────────────────────────────────────────────────────────────────
class Config:
    def __init__(self):
        # ── Profil ────────────────────────────────────────────────────
        self.NAMA     = _env("NAMA",     required=True)
        self.EMAIL    = _env("EMAIL",    required=True)
        self.NOMOR_HP = _env("NOMOR_HP", default="")
        self.CV_TEXT  = _baca_cv()

        # ── Kriteria job ──────────────────────────────────────────────
        self.POSISI_TARGET = _env_list("POSISI_TARGET", default=[
            "Frontend Developer",
            "React Developer",
            "Web Developer",
            "Machine Learning Engineer",
            "AI Engineer",
        ])
        self.LOKASI = _env_list("LOKASI", default=[
            "Jakarta",
            "Remote",
            "Hybrid",
        ])
        self.GAJI_MINIMUM = _env_int("GAJI_MINIMUM", default=5_000_000)
        self.BLACKLIST    = _env_list("BLACKLIST", default=[
            "sales", "target", "MLM", "commission", "asuransi",
        ])

        # ── Platform: fokus Glints & Jobstreet ───────────────────────
        self.PLATFORM = _env_list("PLATFORM", default=[
            "jobstreet",
        ])

        # ── Threshold ─────────────────────────────────────────────────
        self.SKOR_MINIMUM = _env_int("SKOR_MINIMUM", default=60)

        # ── Ollama ────────────────────────────────────────────────────
        self.OLLAMA_URL   = _env("OLLAMA_URL",   default="http://localhost:11434")
        self.OLLAMA_MODEL = _env("OLLAMA_MODEL", default="qwen2.5:7b")

        # ── Telegram ──────────────────────────────────────────────────
        self.TELEGRAM_TOKEN   = _env("TELEGRAM_TOKEN",   required=True)
        self.TELEGRAM_CHAT_ID = _env("TELEGRAM_CHAT_ID", required=True)

        # ── Kredensial platform ────────────────────────────────────────
        # Tidak wajib diisi di .env karena login dilakukan manual sekali
        # via browser (Google OAuth). Cookies disimpan di output/cookies/.
        self.GLINTS_EMAIL       = _env("GLINTS_EMAIL",       default="")
        self.GLINTS_PASSWORD    = _env("GLINTS_PASSWORD",    default="")
        self.JOBSTREET_EMAIL    = _env("JOBSTREET_EMAIL",    default="")
        self.JOBSTREET_PASSWORD = _env("JOBSTREET_PASSWORD", default="")

        # ── Email SMTP (tetap ada untuk keperluan lain) ───────────────
        self.SMTP_HOST = "smtp.gmail.com"
        self.SMTP_PORT = 587
        self.SMTP_USER = _env("SMTP_USER", default="")
        self.SMTP_PASS = _env("SMTP_PASS", default="")

        # ── Path folder ───────────────────────────────────────────────
        self.BASE_DIR         = BASE_DIR
        self.DIR_OUTPUT       = BASE_DIR / "output"
        self.DIR_CV           = BASE_DIR / "output" / "cv"
        self.DIR_COVER_LETTER = BASE_DIR / "output" / "cover_letters"
        self.DIR_LAMARAN      = BASE_DIR / "output" / "lamaran"
        self.DIR_COOKIES      = BASE_DIR / "output" / "cookies"
        self.DB_PATH          = BASE_DIR / "jobs.db"
        self.LOG_PATH         = BASE_DIR / "app.log"

    def tampilkan(self):
        print("=" * 50)
        print("  JOB AUTOMATION — KONFIGURASI")
        print("=" * 50)
        print(f"  Nama            : {self.NAMA}")
        print(f"  Email           : {self.EMAIL}")
        print(f"  Posisi target   : {', '.join(self.POSISI_TARGET)}")
        print(f"  Lokasi          : {', '.join(self.LOKASI)}")
        print(f"  Gaji minimum    : Rp {self.GAJI_MINIMUM:,}")
        print(f"  Skor minimum    : {self.SKOR_MINIMUM}/100")
        print(f"  Platform aktif  : {', '.join(self.PLATFORM)}")
        print(f"  Ollama model    : {self.OLLAMA_MODEL}")
        print(f"  Glints email    : {self.GLINTS_EMAIL}")
        print(f"  Jobstreet email : {self.JOBSTREET_EMAIL}")
        print(f"  CV dimuat       : {'Ya' if self.CV_TEXT else 'BELUM — isi output/cv/cv.txt'}")
        print("=" * 50)

    def validasi(self) -> bool:
        errors = []

        if not self.NAMA:
            errors.append("NAMA belum diisi di .env")
        if not self.EMAIL:
            errors.append("EMAIL belum diisi di .env")
        if not self.TELEGRAM_TOKEN or self.TELEGRAM_TOKEN.startswith("123456"):
            errors.append("TELEGRAM_TOKEN belum diisi di .env")
        if not self.TELEGRAM_CHAT_ID or self.TELEGRAM_CHAT_ID == "987654321":
            errors.append("TELEGRAM_CHAT_ID belum diisi di .env")
        if not self.CV_TEXT:
            errors.append("CV belum ada — buat file output/cv/cv.txt")

        if errors:
            print("\n[CONFIG] ERROR — hal berikut belum diisi:")
            for e in errors:
                print(f"  ✗ {e}")
            print("\nLihat .env.example untuk panduan pengisian.\n")
            return False

        print("[CONFIG] ✓ Semua konfigurasi valid.")
        return True


# ── Instance tunggal ──────────────────────────────────────────────────────────
config = Config()
