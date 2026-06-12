import json
import logging
import time
from datetime import datetime

import requests

logger = logging.getLogger(__name__)


class Scorer:
    def __init__(self, config, db):
        self.config = config
        self.db = db

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def jalankan(self):
        """
        Entry point:
        1. Ambil semua lowongan berstatus 'baru', scoring seluruhnya via Ollama AI.
        2. Dari yang lolos SKOR_MINIMUM, ambil top-10 per platform (skor tertinggi)
           → set 'pending_review'.
        3. Sisanya → set 'auto_skip'.
        """
        lowongan_baru = self.db.ambil_by_status("baru")

        if not lowongan_baru:
            logger.info("[SCORER] Tidak ada lowongan baru untuk di-scoring.")
            print("[SCORER] Tidak ada lowongan baru untuk di-scoring.")
            return

        total = len(lowongan_baru)
        logger.info(
            f"[SCORER] Memulai scoring {total} lowongan "
            f"pada {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )
        print(f"[SCORER] Scoring {total} lowongan...")

        berhasil = 0
        gagal = 0

        # { platform: [ {"id": ..., "skor": ..., "judul": ...}, ... ] }
        kandidat_per_platform: dict[str, list[dict]] = {}

        for i, lowongan in enumerate(lowongan_baru, start=1):
            lowongan_id = lowongan["id"]
            judul       = lowongan.get("judul", "-")
            perusahaan  = lowongan.get("perusahaan", "-")
            platform    = lowongan.get("platform", "unknown")

            print(f"[SCORER] ({i}/{total}) '{judul}' — {perusahaan}")

            self.db.update_status(lowongan_id, "processing")

            try:
                hasil = self._scoring_ai(lowongan)
                self._validasi_hasil(hasil)

                self.db.update_scoring(
                    id=lowongan_id,
                    skor=hasil["skor"],
                    skill_match=hasil["skill_match"],
                    skill_gap=hasil["skill_gap"],
                    alasan=hasil["alasan"],
                    rekomendasi=hasil["rekomendasi"],
                )

                if hasil["skor"] < self.config.SKOR_MINIMUM:
                    self.db.update_status(lowongan_id, "auto_skip")
                    logger.info(
                        f"[SCORER] AUTO_SKIP — '{judul}' | skor={hasil['skor']} "
                        f"(min={self.config.SKOR_MINIMUM})"
                    )
                else:
                    # Sementara set 'scored', akan diputuskan setelah semua selesai
                    self.db.update_status(lowongan_id, "scored")
                    kandidat_per_platform.setdefault(platform, []).append({
                        "id"   : lowongan_id,
                        "skor" : hasil["skor"],
                        "judul": judul,
                    })
                    logger.info(
                        f"[SCORER] SCORED — '{judul}' | skor={hasil['skor']} "
                        f"| rekomendasi={hasil['rekomendasi']}"
                    )

                berhasil += 1

            except (requests.RequestException, ConnectionError) as e:
                logger.error(f"[SCORER] Gagal koneksi ke Ollama untuk '{judul}': {e}")
                self.db.update_status(lowongan_id, "error")
                gagal += 1

            except (json.JSONDecodeError, ValueError) as e:
                logger.error(f"[SCORER] Respons AI tidak valid untuk '{judul}': {e}")
                self.db.update_status(lowongan_id, "error")
                gagal += 1

            except Exception as e:
                logger.error(f"[SCORER] Error tidak terduga untuk '{judul}': {e}")
                self.db.update_status(lowongan_id, "error")
                gagal += 1

        # ── Filter top-10 per platform setelah semua scoring selesai ──
        total_pending   = 0
        total_auto_skip = 0

        for platform, kandidat in kandidat_per_platform.items():
            kandidat.sort(key=lambda x: x["skor"], reverse=True)
            top10   = kandidat[:10]
            sisanya = kandidat[10:]

            for item in top10:
                self.db.update_status(item["id"], "pending_review")
                logger.info(
                    f"[SCORER] PENDING_REVIEW [{platform}] "
                    f"'{item['judul']}' | skor={item['skor']}"
                )
                total_pending += 1

            for item in sisanya:
                self.db.update_status(item["id"], "auto_skip")
                logger.info(
                    f"[SCORER] AUTO_SKIP (melebihi top-10) [{platform}] "
                    f"'{item['judul']}' | skor={item['skor']}"
                )
                total_auto_skip += 1

        print(
            f"[SCORER] Selesai. Scoring: berhasil={berhasil}, gagal={gagal}, total={total}\n"
            f"[SCORER] Hasil akhir: pending_review={total_pending}, "
            f"auto_skip={total_auto_skip}"
        )
        logger.info(
            f"[SCORER] Selesai. berhasil={berhasil}, gagal={gagal}, total={total} | "
            f"pending_review={total_pending}, auto_skip_top10={total_auto_skip}"
        )

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _scoring_ai(self, lowongan: dict) -> dict:
        prompt = self._build_prompt(lowongan)
        payload = {
            "model": self.config.OLLAMA_MODEL,
            "prompt": prompt,
            "format": "json",
            "stream": False,
        }

        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                logger.debug(f"[SCORER] Request ke Ollama (percobaan {attempt})...")
                response = requests.post(
                    f"{self.config.OLLAMA_URL}/api/generate",
                    json=payload,
                    timeout=120,
                )
                response.raise_for_status()

                raw = response.json().get("response", "")
                if not raw:
                    raise ValueError("Respons Ollama kosong.")

                return json.loads(raw)

            except (requests.ConnectionError, requests.Timeout) as e:
                logger.warning(f"[SCORER] Koneksi gagal (percobaan {attempt}/{max_retries}): {e}")
                if attempt < max_retries:
                    time.sleep(2 ** attempt)
                else:
                    raise

    def _build_prompt(self, lowongan: dict) -> str:
        return f"""Kamu adalah career advisor profesional yang membantu kandidat menilai kecocokan dengan lowongan kerja.

Tugasmu: nilai seberapa cocok kandidat berikut dengan lowongan yang tersedia.

=== CV KANDIDAT ===
{self.config.CV_TEXT}

=== DATA LOWONGAN ===
Posisi     : {lowongan.get('judul', '-')}
Perusahaan : {lowongan.get('perusahaan', '-')}
Lokasi     : {lowongan.get('lokasi', '-')}
Gaji       : {lowongan.get('gaji', 'Tidak disebutkan')}
Deskripsi  :
{lowongan.get('deskripsi', '-')}

=== INSTRUKSI ===
Berikan penilaian dalam format JSON berikut (tanpa teks tambahan di luar JSON):
{{
  "skor": <integer 0-100, ukuran kecocokan keseluruhan>,
  "skill_match": [<list skill kandidat yang relevan dengan lowongan>],
  "skill_gap": [<list skill yang dibutuhkan lowongan tapi tidak dimiliki kandidat>],
  "alasan": "<penjelasan singkat 2-3 kalimat mengapa skor tersebut diberikan>",
  "rekomendasi": "<salah satu dari: apply / pertimbangkan / skip>"
}}

Panduan skor:
- 80-100 : Sangat cocok, hampir semua persyaratan terpenuhi
- 60-79  : Cukup cocok, beberapa gap kecil
- 40-59  : Kurang cocok, gap signifikan
- 0-39   : Tidak cocok
"""

    @staticmethod
    def _validasi_hasil(hasil: dict):
        field_wajib = ["skor", "skill_match", "skill_gap", "alasan", "rekomendasi"]
        for field in field_wajib:
            if field not in hasil:
                raise ValueError(f"Field '{field}' tidak ada dalam respons AI.")

        if not isinstance(hasil["skor"], (int, float)):
            raise ValueError(f"Field 'skor' harus berupa angka, dapat: {type(hasil['skor'])}")

        hasil["skor"] = max(0, min(100, int(hasil["skor"])))

        if not isinstance(hasil["skill_match"], list):
            hasil["skill_match"] = []

        if not isinstance(hasil["skill_gap"], list):
            hasil["skill_gap"] = []

        rekomendasi_valid = {"apply", "pertimbangkan", "skip"}
        if hasil["rekomendasi"].lower() not in rekomendasi_valid:
            logger.warning(
                f"[SCORER] Nilai rekomendasi tidak dikenal: '{hasil['rekomendasi']}', "
                f"di-set ke 'pertimbangkan'"
            )
            hasil["rekomendasi"] = "pertimbangkan"

        return hasil
