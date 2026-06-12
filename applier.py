"""
applier.py — Auto-apply ke Glints dan Jobstreet menggunakan Playwright.

Strategi login:
  Menggunakan persistent browser context (launch_persistent_context).
  Data session disimpan di folder output/browser_data/glints/ dan
  output/browser_data/jobstreet/. Sekali login manual via Google,
  tidak perlu login lagi sampai session expired.
  Login manual tidak ada batas waktu — tunggu sampai kamu ketik
  'lanjut' di terminal atau login terdeteksi otomatis.

Alur Glints (3 step modal):
  1. Resume sudah ada di profil → klik Selanjutnya
  2. Skill assessment → pilih "Dasar" untuk semua skill
  3. Years of experience → pilih "<1 thn"
  → Klik Kirim

Alur Jobstreet (4 step halaman penuh):
  1. Choose documents → pilih CV dari profil + paste cover letter AI
  2. Answer employer questions → dijawab otomatis via Ollama AI
  3. Update Jobstreet Profile → klik Continue
  4. Review and submit → klik Submit application
"""

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger(__name__)

GLINTS_SKILL_LEVEL      = "Dasar"
GLINTS_EXPERIENCE_LABEL = "<1 thn"

TIMEOUT_GOTO    = 30_000
TIMEOUT_ELEMENT = 15_000

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# Helper: jawab pertanyaan employer Jobstreet via Ollama
# ---------------------------------------------------------------------------

def _jawab_pertanyaan_ai(
    pertanyaan: str,
    opsi: list[str],
    tipe: str,
    cv_text: str,
    ollama_url: str,
    ollama_model: str,
) -> list[str]:
    prompt = f"""Kamu adalah asisten yang membantu mengisi form lamaran kerja.

CV kandidat:
{cv_text[:2000]}

Pertanyaan dari employer: "{pertanyaan}"
Tipe jawaban: {tipe}
Pilihan yang tersedia: {json.dumps(opsi, ensure_ascii=False)}

Instruksi:
- Pilih jawaban yang paling sesuai dengan CV kandidat
- Untuk 'radio' dan 'dropdown': pilih TEPAT 1 jawaban
- Untuk 'checkbox': boleh pilih lebih dari 1 jika kandidat memiliki skill tersebut
- Jika tidak ada yang cocok, pilih opsi paling umum/netral
- Jawab HANYA dengan JSON array berisi teks pilihan yang dipilih (verbatim dari daftar opsi)
- Contoh: ["React.js", "Node.js"]
"""
    try:
        response = requests.post(
            f"{ollama_url}/api/generate",
            json={"model": ollama_model, "prompt": prompt, "stream": False},
            timeout=60,
        )
        response.raise_for_status()
        raw = response.json().get("response", "").strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        jawaban = json.loads(raw)

        if isinstance(jawaban, list):
            valid = [j for j in jawaban if j in opsi]
            if valid:
                return valid

        return [opsi[0]] if opsi else []

    except Exception as e:
        logger.warning(f"[AI] Gagal jawab pertanyaan '{pertanyaan[:50]}': {e}")
        return [opsi[0]] if opsi else []


# ---------------------------------------------------------------------------
# Kelas utama Applier
# ---------------------------------------------------------------------------

class Applier:
    def __init__(self, config, db):
        self.config = config
        self.db = db

        self._browser_dir_glints = (
            Path(config.BASE_DIR) / "output" / "browser_data" / "glints"
        )
        self._browser_dir_jobstreet = (
            Path(config.BASE_DIR) / "output" / "browser_data" / "jobstreet"
        )
        self._debug_dir = Path(config.BASE_DIR) / "output" / "debug"

        self._browser_dir_glints.mkdir(parents=True, exist_ok=True)
        self._browser_dir_jobstreet.mkdir(parents=True, exist_ok=True)
        self._debug_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Helper: screenshot untuk debug
    # ------------------------------------------------------------------

    def _screenshot(self, page, nama: str):
        try:
            path = self._debug_dir / f"{nama}_{int(time.time())}.png"
            page.screenshot(path=str(path), full_page=False)
            logger.debug(f"Screenshot disimpan: {path}")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Helper: tunggu input user tanpa batas waktu
    # ------------------------------------------------------------------

    def _tunggu_konfirmasi_user(self, label: str) -> None:
        """Blokir sampai user ketik 'lanjut' / 'l' / 'y' di terminal."""
        selesai = threading.Event()

        def _baca_input():
            print(f"[{label}] Ketik 'lanjut' + Enter setelah kamu selesai login: ", end="", flush=True)
            while True:
                try:
                    inp = input("").strip().lower()
                    if inp in ("lanjut", "l", "y", "yes", "continue", "c", "ok"):
                        selesai.set()
                        break
                except Exception:
                    selesai.set()
                    break

        t = threading.Thread(target=_baca_input, daemon=True)
        t.start()
        selesai.wait()  # tunggu tanpa batas waktu

    # ------------------------------------------------------------------
    # Public: entry point
    # ------------------------------------------------------------------

    def jalankan(self):
        lowongan_list = self.db.ambil_by_status("pending_review")

        if not lowongan_list:
            logger.info("[APPLIER] Tidak ada lowongan pending_review.")
            print("[APPLIER] Tidak ada lowongan pending_review.")
            return

        siap_apply = [l for l in lowongan_list if l.get("cover_letter", "").strip()]
        belum_cl   = len(lowongan_list) - len(siap_apply)

        if belum_cl > 0:
            logger.warning(f"[APPLIER] {belum_cl} lowongan belum punya cover letter, dilewati.")

        if not siap_apply:
            print("[APPLIER] Tidak ada lowongan siap apply.")
            return

        glints_list    = [l for l in siap_apply if l.get("platform") == "glints"]
        jobstreet_list = [l for l in siap_apply if l.get("platform") == "jobstreet"]

        total = len(siap_apply)
        print(f"[APPLIER] Mulai apply {total} lowongan "
              f"(Glints: {len(glints_list)}, Jobstreet: {len(jobstreet_list)})")
        logger.info(f"[APPLIER] Mulai apply {total} lowongan pada "
                    f"{datetime.now().strftime('%Y-%m-%d %H:%M')}")

        berhasil = gagal = 0

        if glints_list:
            ok, fail = self._apply_batch_glints(glints_list)
            berhasil += ok
            gagal    += fail

        if jobstreet_list:
            ok, fail = self._apply_batch_jobstreet(jobstreet_list)
            berhasil += ok
            gagal    += fail

        print(f"[APPLIER] Selesai. Berhasil: {berhasil}, Gagal: {gagal}, Total: {total}")
        logger.info(f"[APPLIER] Selesai. berhasil={berhasil}, gagal={gagal}, total={total}")

    # ==================================================================
    # GLINTS
    # ==================================================================

    def _apply_batch_glints(self, lowongan_list: list[dict]) -> tuple[int, int]:
        berhasil = gagal = 0

        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(self._browser_dir_glints),
                headless=False,
                user_agent=USER_AGENT,
                viewport={"width": 1280, "height": 800},
                args=["--disable-blink-features=AutomationControlled"],
            )
            page = context.new_page()
            page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )

            if not self._cek_login_glints(page):
                if not self._login_manual_glints(page):
                    logger.error("[GLINTS] Login gagal, batch dibatalkan.")
                    context.close()
                    return 0, len(lowongan_list)

            for i, lowongan in enumerate(lowongan_list, 1):
                judul = lowongan.get("judul", "-")
                print(f"[GLINTS] ({i}/{len(lowongan_list)}) Apply: '{judul}'")
                try:
                    ok = self._apply_satu_glints(page, lowongan)
                    if ok:
                        self.db.catat_waktu_kirim(lowongan["id"])
                        berhasil += 1
                        logger.info(f"[GLINTS] Berhasil apply: '{judul}'")
                    else:
                        self.db.update_status(lowongan["id"], "error")
                        gagal += 1
                        logger.warning(f"[GLINTS] Gagal apply: '{judul}'")
                    time.sleep(2)
                except Exception as e:
                    logger.error(f"[GLINTS] Error apply '{judul}': {e}")
                    self.db.update_status(lowongan["id"], "error")
                    gagal += 1

            context.close()

        return berhasil, gagal

    def _cek_login_glints(self, page) -> bool:
        try:
            logger.info("[GLINTS] Mengecek status login...")
            page.goto("https://glints.com/id", timeout=TIMEOUT_GOTO)
            page.wait_for_timeout(3000)
            self._screenshot(page, "glints_cek_login")
            logger.info(f"[GLINTS] URL setelah buka homepage: {page.url}")

            # Cek ada tombol login/masuk = belum login
            login_btn = (
                page.query_selector("a:has-text('Masuk')")
                or page.query_selector("button:has-text('Masuk')")
                or page.query_selector("a:has-text('Login')")
                or page.query_selector("a[href*='/login']")
            )
            if login_btn:
                logger.info("[GLINTS] Belum login — tombol masuk ditemukan.")
                return False

            # Cek ada elemen user yang hanya muncul saat login
            avatar = (
                page.query_selector("[data-testid='user-avatar']")
                or page.query_selector("[class*='UserAvatar']")
                or page.query_selector("[class*='ProfileAvatar']")
            )
            if avatar:
                logger.info("[GLINTS] Sudah login — avatar ditemukan.")
                return True

            # Tidak yakin — anggap belum login supaya prompt muncul
            logger.info("[GLINTS] Status login tidak pasti, minta login ulang.")
            return False

        except Exception as e:
            logger.warning(f"[GLINTS] Gagal cek login: {e}")
            return False

    def _login_manual_jobstreet(self, page) -> bool:
        try:
            logger.info("[JOBSTREET] Memulai sesi login manual...")
            print("\n" + "=" * 60)
            print("  [JOBSTREET] LOGIN DIPERLUKAN")
            print("=" * 60)
            print("  Browser Chromium sudah terbuka. Silakan:")
            print("  1. Login ke Jobstreet via Google")
            print("  2. Setelah halaman Jobstreet terbuka, tekan Enter di sini")
            print("  Tidak ada batas waktu — program menunggu kamu.")
            print("=" * 60 + "\n")

            page.goto("https://id.jobstreet.com/id/login", timeout=TIMEOUT_GOTO)

            # Polling otomatis dulu — kalau terdeteksi langsung lanjut
            # tanpa perlu ketik apapun
            print("[JOBSTREET] Menunggu login... (atau tekan Enter untuk lanjut manual)")

            detected = False
            for _ in range(5):  # cek 5x dulu selama 15 detik
                page.wait_for_timeout(3000)
                profil = page.query_selector(
                    "[data-automation='profile-menu'], a[href*='/profile']"
                )
                sign_in = page.query_selector("a:has-text('Sign in')")
                if profil and not sign_in:
                    print("[JOBSTREET] Login terdeteksi otomatis!")
                    detected = True
                    break

            if not detected:
                # Tidak terdeteksi otomatis — minta konfirmasi manual (blocking, tanpa thread)
                input("[JOBSTREET] Tekan Enter setelah kamu selesai login: ")

            page.wait_for_timeout(1000)
            self._screenshot(page, "jobstreet_setelah_login")
            logger.info("[JOBSTREET] Login selesai.")
            print("[JOBSTREET] Melanjutkan proses apply...\n")
            return True

        except Exception as e:
            logger.error(f"[JOBSTREET] Error saat login: {e}")
            return False


    def _apply_satu_glints(self, page, lowongan: dict) -> bool:
        try:
            page.goto(lowongan["url"], timeout=TIMEOUT_GOTO)
            page.wait_for_timeout(2000)

            # Cek sudah pernah apply
            sudah_apply = (
                page.query_selector("[data-testid='applied-badge']")
                or page.query_selector("button:has-text('Sudah Dilamar')")
                or page.query_selector("span:has-text('Sudah Dilamar')")
            )
            if sudah_apply:
                logger.info(f"[GLINTS] Sudah pernah apply: '{lowongan['judul']}'")
                self.db.update_status(lowongan["id"], "terkirim")
                return True

            # Klik tombol LAMAR
            tombol_lamar = (
                page.query_selector("button:has-text('LAMAR'):not(:has-text('CHAT'))")
                or page.query_selector("[data-testid='apply-button']")
                or page.query_selector("button:has-text('Lamar')")
            )
            if not tombol_lamar:
                logger.warning(f"[GLINTS] Tombol LAMAR tidak ditemukan: '{lowongan['judul']}'")
                self._screenshot(page, f"glints_notfound_{lowongan['id']}")
                return False

            tombol_lamar.click()
            page.wait_for_timeout(2000)

            # STEP 1: Resume — langsung klik Selanjutnya
            selanjutnya = page.wait_for_selector(
                "button:has-text('Selanjutnya')", timeout=TIMEOUT_ELEMENT
            )
            selanjutnya.click()
            page.wait_for_timeout(1500)

            # STEP 2: Skill Assessment
            self._isi_skill_assessment_glints(page)
            selanjutnya = page.wait_for_selector(
                "button:has-text('Selanjutnya')", timeout=TIMEOUT_ELEMENT
            )
            selanjutnya.click()
            page.wait_for_timeout(1500)

            # STEP 3: Years of Experience
            self._pilih_experience_glints(page)

            # Klik Kirim
            kirim = page.wait_for_selector(
                "button:has-text('Kirim')", timeout=TIMEOUT_ELEMENT
            )
            kirim.click()
            page.wait_for_timeout(3000)

            # Verifikasi
            sukses = page.query_selector(
                "[class*='success'], [class*='Success'], "
                "div:has-text('Lamaran berhasil'), "
                "div:has-text('berhasil dikirim')"
            )
            if sukses:
                return True

            modal = page.query_selector("[role='dialog'], [class*='Modal']")
            if not modal:
                return True

            return True

        except PlaywrightTimeoutError as e:
            logger.error(f"[GLINTS] Timeout saat apply '{lowongan['judul']}': {e}")
            self._screenshot(page, f"glints_timeout_{lowongan['id']}")
            return False
        except Exception as e:
            logger.error(f"[GLINTS] Error saat apply '{lowongan['judul']}': {e}")
            self._screenshot(page, f"glints_error_{lowongan['id']}")
            return False

    def _isi_skill_assessment_glints(self, page):
        try:
            page.wait_for_selector(
                f"label:has-text('{GLINTS_SKILL_LEVEL}'), input[type='radio']",
                timeout=TIMEOUT_ELEMENT
            )
            skill_groups = page.query_selector_all(
                "div[class*='SkillGroup'], fieldset, "
                "div[class*='skill-group'], div[class*='question-group']"
            )
            if skill_groups:
                for group in skill_groups:
                    dasar = group.query_selector(f"label:has-text('{GLINTS_SKILL_LEVEL}')")
                    if dasar:
                        dasar.click()
                        time.sleep(0.3)
            else:
                for label in page.query_selector_all(f"label:has-text('{GLINTS_SKILL_LEVEL}')"):
                    try:
                        label.click()
                        time.sleep(0.3)
                    except Exception:
                        continue
        except PlaywrightTimeoutError:
            logger.warning("[GLINTS] Step 2 skill assessment tidak muncul, dilanjutkan.")
        except Exception as e:
            logger.warning(f"[GLINTS] Error isi skill assessment: {e}")

    def _pilih_experience_glints(self, page):
        try:
            page.wait_for_selector("input[type='radio']", timeout=TIMEOUT_ELEMENT)
            exp_label = page.query_selector(f"label:has-text('{GLINTS_EXPERIENCE_LABEL}')")
            if exp_label:
                exp_label.click()
                time.sleep(0.3)
            else:
                radio_list = page.query_selector_all("input[type='radio']")
                if len(radio_list) > 1:
                    radio_list[1].click()
        except Exception as e:
            logger.warning(f"[GLINTS] Error pilih experience: {e}")

    # ==================================================================
    # JOBSTREET
    # ==================================================================

    def _apply_batch_jobstreet(self, lowongan_list: list[dict]) -> tuple[int, int]:
        berhasil = gagal = 0

        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(self._browser_dir_jobstreet),
                headless=False,
                user_agent=USER_AGENT,
                viewport={"width": 1280, "height": 800},
                args=["--disable-blink-features=AutomationControlled"],
            )
            page = context.new_page()
            page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )

            if not self._cek_login_jobstreet(page):
                if not self._login_manual_jobstreet(page):
                    logger.error("[JOBSTREET] Login gagal, batch dibatalkan.")
                    context.close()
                    return 0, len(lowongan_list)

            for i, lowongan in enumerate(lowongan_list, 1):
                judul = lowongan.get("judul", "-")
                print(f"[JOBSTREET] ({i}/{len(lowongan_list)}) Apply: '{judul}'")
                try:
                    ok = self._apply_satu_jobstreet(page, lowongan)
                    if ok:
                        self.db.catat_waktu_kirim(lowongan["id"])
                        berhasil += 1
                        logger.info(f"[JOBSTREET] Berhasil apply: '{judul}'")
                    else:
                        self.db.update_status(lowongan["id"], "error")
                        gagal += 1
                        logger.warning(f"[JOBSTREET] Gagal apply: '{judul}'")
                    time.sleep(3)
                except Exception as e:
                    logger.error(f"[JOBSTREET] Error apply '{judul}': {e}")
                    self.db.update_status(lowongan["id"], "error")
                    gagal += 1

            context.close()

        return berhasil, gagal

    def _cek_login_jobstreet(self, page) -> bool:
        try:
            logger.info("[JOBSTREET] Mengecek status login...")
            page.goto("https://id.jobstreet.com", timeout=TIMEOUT_GOTO)
            page.wait_for_timeout(4000)
            self._screenshot(page, "jobstreet_cek_login")
            logger.info(f"[JOBSTREET] URL homepage: {page.url}")

            # Kalau ada tombol Sign in = belum login
            sign_in = (
                page.query_selector("a:has-text('Sign in')")
                or page.query_selector("button:has-text('Sign in')")
                or page.query_selector("a:has-text('Log in')")
                or page.query_selector("a:has-text('Masuk')")
            )
            if sign_in:
                logger.info("[JOBSTREET] Belum login — tombol Sign in ditemukan.")
                return False

            # Verifikasi dengan buka halaman /profile
            page.goto("https://id.jobstreet.com/profile", timeout=TIMEOUT_GOTO)
            page.wait_for_timeout(3000)
            self._screenshot(page, "jobstreet_cek_profil")
            logger.info(f"[JOBSTREET] URL setelah buka /profile: {page.url}")

            # Diredirect ke login = belum login
            if any(k in page.url.lower() for k in ("login", "signin", "auth", "oauth")):
                logger.info("[JOBSTREET] Belum login — redirect ke halaman auth.")
                return False

            # Tetap di /profile = sudah login
            if "profile" in page.url.lower():
                logger.info("[JOBSTREET] Sudah login — berhasil akses halaman profil.")
                return True

            # Tidak yakin — anggap belum login
            logger.info("[JOBSTREET] Status login tidak pasti, minta login ulang.")
            return False

        except Exception as e:
            logger.warning(f"[JOBSTREET] Gagal cek login: {e}")
            return False

    def _login_manual_jobstreet(self, page) -> bool:
        try:
            logger.info("[JOBSTREET] Memulai sesi login manual...")
            print("\n" + "=" * 60)
            print("  [JOBSTREET] LOGIN DIPERLUKAN")
            print("=" * 60)
            print("  Browser Chromium sudah terbuka. Silakan:")
            print("  1. Klik 'Sign in' atau 'Masuk'")
            print("  2. Pilih 'Continue with Google'")
            print("  3. Pilih akun Google kamu")
            print("  4. Tunggu sampai halaman Jobstreet terbuka penuh")
            print("  Tidak ada batas waktu — program menunggu kamu.")
            print("=" * 60 + "\n")

            page.goto("https://id.jobstreet.com/id/login", timeout=TIMEOUT_GOTO)

            user_lanjut = threading.Event()

            def baca_input():
                print("[JOBSTREET] Ketik 'lanjut' + Enter setelah login selesai: ", end="", flush=True)
                while True:
                    try:
                        inp = input("").strip().lower()
                        if inp in ("lanjut", "l", "y", "yes", "ok", "continue", "c"):
                            user_lanjut.set()
                            break
                    except Exception:
                        user_lanjut.set()
                        break

            t = threading.Thread(target=baca_input, daemon=True)
            t.start()

            # Polling tiap 3 detik tanpa batas waktu
            while not user_lanjut.is_set():
                page.wait_for_timeout(3000)

                sign_in = (
                    page.query_selector("a:has-text('Sign in')")
                    or page.query_selector("button:has-text('Sign in')")
                )
                profil = page.query_selector(
                    "[data-automation='profile-menu'], "
                    "a[href*='/profile']"
                )

                if profil and not sign_in:
                    user_lanjut.set()
                    print("\n[JOBSTREET] Login terdeteksi otomatis!")
                    break

                # Cek URL sudah keluar dari auth
                if not any(k in page.url.lower() for k in ("login", "signin", "auth", "oauth")):
                    if "jobstreet.com" in page.url:
                        page.wait_for_timeout(2000)
                        profil2 = page.query_selector(
                            "[data-automation='profile-menu'], "
                            "a[href*='/profile']"
                        )
                        if profil2:
                            user_lanjut.set()
                            print("\n[JOBSTREET] Login terdeteksi otomatis!")
                            break

            page.wait_for_timeout(1000)
            self._screenshot(page, "jobstreet_setelah_login")
            logger.info("[JOBSTREET] Login selesai. Session tersimpan di browser_data/jobstreet/")
            print("[JOBSTREET] Melanjutkan proses apply...\n")
            return True

        except Exception as e:
            logger.error(f"[JOBSTREET] Error saat login: {e}")
            return False

    def _apply_satu_jobstreet(self, page, lowongan: dict) -> bool:
        try:
            page.goto(lowongan["url"], timeout=TIMEOUT_GOTO)
            page.wait_for_timeout(2000)

            # Cek sudah apply
            sudah = (
                page.query_selector("span:has-text('Applied')")
                or page.query_selector("button:has-text('Applied')")
                or page.query_selector("[data-automation='applied-label']")
            )
            if sudah:
                logger.info(f"[JOBSTREET] Sudah pernah apply: '{lowongan['judul']}'")
                self.db.update_status(lowongan["id"], "terkirim")
                return True

            # Klik Quick apply / Apply
            apply_btn = (
                page.query_selector("a:has-text('Quick apply')")
                or page.query_selector("button:has-text('Quick apply')")
                or page.query_selector("a:has-text('Apply')")
                or page.query_selector("button:has-text('Apply')")
            )
            if not apply_btn:
                logger.warning(f"[JOBSTREET] Tombol apply tidak ditemukan: '{lowongan['judul']}'")
                self._screenshot(page, f"jobstreet_notfound_{lowongan['id']}")
                return False

            apply_btn.click()
            page.wait_for_timeout(3000)

            # Cek redirect ke seek.com setelah klik apply
            if "login.seek.com" in page.url or "seek.com/login" in page.url:
                logger.warning(f"[JOBSTREET] Redirect ke seek.com login.")
                print("\n" + "=" * 60)
                print("  [JOBSTREET] LOGIN seek.com DIPERLUKAN")
                print("=" * 60)
                print("  Silakan login di browser yang terbuka.")
                print("=" * 60)

                # Tunggu sampai keluar dari halaman seek login — tanpa thread
                print("[JOBSTREET] Menunggu login seek.com", end="", flush=True)
                for _ in range(60):  # tunggu maks 3 menit
                    page.wait_for_timeout(3000)
                    print(".", end="", flush=True)
                    if "login.seek.com" not in page.url and "seek.com/login" not in page.url:
                        print("\n[JOBSTREET] Login seek.com terdeteksi!")
                        break
                else:
                    # Setelah 3 menit tidak terdeteksi — minta konfirmasi manual
                    print()
                    input("[JOBSTREET] Tekan Enter setelah selesai login seek.com: ")

                page.wait_for_timeout(2000)
                logger.info("[JOBSTREET] Session seek.com tersimpan. Ulangi apply...")

                # Kembali ke halaman lowongan dan retry apply
                page.goto(lowongan["url"], timeout=TIMEOUT_GOTO)
                page.wait_for_timeout(2000)

                apply_btn2 = (
                    page.query_selector("a:has-text('Quick apply')")
                    or page.query_selector("button:has-text('Quick apply')")
                    or page.query_selector("a:has-text('Apply')")
                    or page.query_selector("button:has-text('Apply')")
                )
                if apply_btn2:
                    apply_btn2.click()
                    page.wait_for_timeout(3000)

            # Masih di login page setelah retry
            if "login" in page.url.lower():
                logger.error(f"[JOBSTREET] Masih di halaman login setelah retry.")
                return False

            # STEP 1
            if not self._step1_dokumen_jobstreet(page, lowongan):
                return False

            # STEP 2
            if "role-requirements" in page.url:
                self._step2_pertanyaan_jobstreet(page, lowongan)

            # STEP 3
            if "profile" in page.url:
                self._step3_profil_jobstreet(page)

            # STEP 4
            if "review" in page.url:
                return self._step4_submit_jobstreet(page, lowongan)

            # Fallback submit
            submit = (
                page.query_selector("button:has-text('Submit application')")
                or page.query_selector("button:has-text('Submit')")
            )
            if submit:
                submit.click()
                page.wait_for_timeout(3000)
                return True

            logger.warning(f"[JOBSTREET] Flow tidak sesuai ekspektasi: '{lowongan['judul']}'")
            self._screenshot(page, f"jobstreet_flow_{lowongan['id']}")
            return False

        except PlaywrightTimeoutError as e:
            logger.error(f"[JOBSTREET] Timeout saat apply '{lowongan['judul']}': {e}")
            self._screenshot(page, f"jobstreet_timeout_{lowongan['id']}")
            return False
        except Exception as e:
            logger.error(f"[JOBSTREET] Error saat apply '{lowongan['judul']}': {e}")
            self._screenshot(page, f"jobstreet_error_{lowongan['id']}")
            return False

    def _step1_dokumen_jobstreet(self, page, lowongan: dict) -> bool:
        try:
            page.wait_for_timeout(3000)
            self._screenshot(page, f"jobstreet_step1_{lowongan['id']}")
            logger.info(f"[JOBSTREET] URL step 1: {page.url}")

            # Cek diredirect ke login
            if any(k in page.url.lower() for k in ("login", "signin", "auth")):
                logger.error("[JOBSTREET] Session expired — redirect ke login di step 1.")
                print("[JOBSTREET] Session expired! Hapus folder output/browser_data/jobstreet/ dan jalankan ulang.")
                return False

            # Tunggu elemen step 1
            try:
                page.wait_for_selector(
                    "label:has-text('Select a resumé'), "
                    "label:has-text('Select a resume'), "
                    "label:has-text('Pilih resume'), "
                    "input[type='radio'], "
                    "h1:has-text('Choose'), "
                    "h1:has-text('Pilih dokumen')",
                    timeout=TIMEOUT_ELEMENT
                )
            except PlaywrightTimeoutError:
                # Log heading untuk debug
                h_texts = page.evaluate("""
                    () => [...document.querySelectorAll('h1,h2,h3,button')]
                          .map(e => e.innerText.trim())
                          .filter(t => t.length > 0)
                          .slice(0, 15)
                """)
                logger.error(f"[JOBSTREET] Elemen step 1 tidak ditemukan. Elemen di halaman: {h_texts}")
                return False

            # Pilih "Select a resumé"
            select_resume = (
                page.query_selector("label:has-text('Select a resumé')")
                or page.query_selector("label:has-text('Select a resume')")
                or page.query_selector("label:has-text('Pilih resume')")
                or page.query_selector("input[type='radio'][value*='select']")
            )
            if select_resume:
                select_resume.click()
                time.sleep(0.5)

            # Pilih "Write a cover letter"
            write_cl = (
                page.query_selector("label:has-text('Write a cover letter')")
                or page.query_selector("label:has-text('Tulis cover letter')")
                or page.query_selector("input[value*='write']")
            )
            if write_cl:
                write_cl.click()
                time.sleep(0.5)

            # Isi cover letter
            cover_letter_text = lowongan.get("cover_letter", "").strip()
            if cover_letter_text:
                textarea = (
                    page.query_selector("textarea[placeholder*='cover letter']")
                    or page.query_selector("textarea[placeholder*='Introduce']")
                    or page.query_selector("div[contenteditable='true']")
                    or page.query_selector("textarea")
                )
                if textarea:
                    textarea.click()
                    textarea.fill(cover_letter_text)
                    time.sleep(0.5)

            # Klik Continue
            continue_btn = page.wait_for_selector(
                "button:has-text('Continue')", timeout=TIMEOUT_ELEMENT
            )
            continue_btn.click()
            page.wait_for_timeout(2000)
            return True

        except Exception as e:
            logger.error(f"[JOBSTREET] Step 1 error: {e}")
            self._screenshot(page, f"jobstreet_step1_error_{lowongan['id']}")
            return False

    def _step2_pertanyaan_jobstreet(self, page, lowongan: dict):
        try:
            page.wait_for_selector(
                "select, input[type='checkbox'], input[type='radio']",
                timeout=TIMEOUT_ELEMENT
            )
            page.wait_for_timeout(1000)

            # Dropdown
            for sel in page.query_selector_all("select"):
                try:
                    lbl_el = page.query_selector(f"label[for='{sel.get_attribute('id')}']")
                    pertanyaan = lbl_el.inner_text().strip() if lbl_el else "Pilih opsi"
                    opsi = [
                        o.inner_text().strip()
                        for o in sel.query_selector_all("option")
                        if o.get_attribute("value") and o.inner_text().strip()
                    ]
                    if not opsi:
                        continue
                    jawaban = _jawab_pertanyaan_ai(
                        pertanyaan, opsi, "dropdown",
                        self.config.CV_TEXT, self.config.OLLAMA_URL, self.config.OLLAMA_MODEL
                    )
                    if jawaban:
                        sel.select_option(label=jawaban[0])
                        time.sleep(0.3)
                except Exception as e:
                    logger.debug(f"[JOBSTREET] Error dropdown: {e}")

            # Checkbox (fieldset)
            for fs in page.query_selector_all("fieldset"):
                try:
                    legend = fs.query_selector("legend, h3, h4, p[class*='label']")
                    if not legend:
                        continue
                    pertanyaan = legend.inner_text().strip()
                    checkboxes = fs.query_selector_all("input[type='checkbox']")
                    if not checkboxes:
                        continue
                    opsi = []
                    for cb in checkboxes:
                        lbl = page.query_selector(f"label[for='{cb.get_attribute('id')}']")
                        if lbl:
                            opsi.append(lbl.inner_text().strip())
                    if not opsi:
                        continue
                    jawaban = _jawab_pertanyaan_ai(
                        pertanyaan, opsi, "checkbox",
                        self.config.CV_TEXT, self.config.OLLAMA_URL, self.config.OLLAMA_MODEL
                    )
                    for cb in checkboxes:
                        lbl = page.query_selector(f"label[for='{cb.get_attribute('id')}']")
                        if lbl and lbl.inner_text().strip() in jawaban:
                            if not cb.is_checked():
                                cb.click()
                                time.sleep(0.2)
                except Exception as e:
                    logger.debug(f"[JOBSTREET] Error checkbox: {e}")

            # Radio groups
            radio_groups: dict[str, list] = {}
            for rb in page.query_selector_all("input[type='radio']"):
                name = rb.get_attribute("name") or "default"
                radio_groups.setdefault(name, []).append(rb)

            for name, radios in radio_groups.items():
                try:
                    container = page.query_selector(
                        f"fieldset:has(input[name='{name}']), "
                        f"div:has(input[name='{name}'])"
                    )
                    if not container:
                        continue
                    legend = container.query_selector("legend, p, h3, h4")
                    pertanyaan = legend.inner_text().strip() if legend else name
                    opsi = []
                    for rb in radios:
                        lbl = page.query_selector(f"label[for='{rb.get_attribute('id')}']")
                        if lbl:
                            opsi.append(lbl.inner_text().strip())
                    if not opsi:
                        continue
                    jawaban = _jawab_pertanyaan_ai(
                        pertanyaan, opsi, "radio",
                        self.config.CV_TEXT, self.config.OLLAMA_URL, self.config.OLLAMA_MODEL
                    )
                    for rb in radios:
                        lbl = page.query_selector(f"label[for='{rb.get_attribute('id')}']")
                        if lbl and lbl.inner_text().strip() in jawaban:
                            if not rb.is_checked():
                                rb.click()
                                time.sleep(0.2)
                            break
                except Exception as e:
                    logger.debug(f"[JOBSTREET] Error radio: {e}")

            continue_btn = page.wait_for_selector(
                "button:has-text('Continue')", timeout=TIMEOUT_ELEMENT
            )
            continue_btn.click()
            page.wait_for_timeout(2000)

        except PlaywrightTimeoutError:
            logger.warning("[JOBSTREET] Step 2 tidak ada pertanyaan employer, lanjut.")
        except Exception as e:
            logger.error(f"[JOBSTREET] Step 2 error: {e}")

    def _step3_profil_jobstreet(self, page):
        try:
            page.wait_for_selector("button:has-text('Continue')", timeout=TIMEOUT_ELEMENT)
            page.wait_for_timeout(1000)
            page.click("button:has-text('Continue')")
            page.wait_for_timeout(2000)
        except Exception as e:
            logger.warning(f"[JOBSTREET] Step 3 error: {e}")

    def _step4_submit_jobstreet(self, page, lowongan: dict) -> bool:
        try:
            submit_btn = page.wait_for_selector(
                "button:has-text('Submit application')",
                timeout=TIMEOUT_ELEMENT
            )
            page.wait_for_timeout(1000)
            submit_btn.click()
            page.wait_for_timeout(4000)
            self._screenshot(page, f"jobstreet_submit_{lowongan['id']}")

            sukses = (
                page.query_selector("h1:has-text('Application submitted')")
                or page.query_selector("h2:has-text('Application submitted')")
                or page.query_selector("div:has-text('successfully submitted')")
                or page.query_selector("[data-automation='application-submitted']")
            )
            if sukses:
                return True

            if "submitted" in page.url or "confirmation" in page.url:
                return True

            return True  # Optimistis

        except Exception as e:
            logger.error(f"[JOBSTREET] Step 4 error: {e}")
            return False