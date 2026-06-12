import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path

import requests
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_JUSTIFY

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helper: panggil Ollama
# ---------------------------------------------------------------------------

def panggil_ollama(
    prompt: str,
    ollama_url: str,
    model: str,
    timeout: int = 120,
    max_retries: int = 3,
) -> str:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
    }

    for attempt in range(1, max_retries + 1):
        try:
            logger.debug(f"[OLLAMA] Request ke {ollama_url} (percobaan {attempt})...")
            response = requests.post(
                f"{ollama_url}/api/generate",
                json=payload,
                timeout=timeout,
            )
            response.raise_for_status()

            teks = response.json().get("response", "").strip()
            if not teks:
                raise ValueError("Respons Ollama kosong.")
            return teks

        except (requests.ConnectionError, requests.Timeout) as e:
            logger.warning(f"[OLLAMA] Koneksi gagal (percobaan {attempt}/{max_retries}): {e}")
            if attempt < max_retries:
                time.sleep(2 ** attempt)
            else:
                raise

        except requests.HTTPError as e:
            logger.error(f"[OLLAMA] HTTP error: {e}")
            raise


# ---------------------------------------------------------------------------
# Helper: sanitasi nama file
# ---------------------------------------------------------------------------

def _sanitasi_nama(teks: str) -> str:
    bersih = re.sub(r'[\\/*?:"<>|]', "", teks)
    bersih = bersih.strip().replace(" ", "_")
    return bersih[:60]


# ---------------------------------------------------------------------------
# Kelas utama DocumentGenerator
# ---------------------------------------------------------------------------

class DocumentGenerator:
    def __init__(self, config, db):
        self.config = config
        self.db = db

        dir_output = Path(self.config.DIR_COVER_LETTER)
        dir_output.mkdir(parents=True, exist_ok=True)
        self._dir_output = dir_output

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def jalankan(self):
        """Generate cover letter untuk semua lowongan pending_review."""
        pending = self.db.ambil_by_status("pending_review")

        if not pending:
            logger.info("[GENERATOR] Tidak ada lowongan pending_review.")
            print("[GENERATOR] Tidak ada lowongan pending_review.")
            return

        total = len(pending)
        print(f"[GENERATOR] Membuat cover letter untuk {total} lowongan...")
        logger.info(f"[GENERATOR] Mulai generate {total} cover letter pada {datetime.now().strftime('%Y-%m-%d %H:%M')}")

        berhasil = 0
        gagal = 0

        for i, lowongan in enumerate(pending, start=1):
            judul = lowongan.get("judul", "-")
            perusahaan = lowongan.get("perusahaan", "-")
            print(f"[GENERATOR] ({i}/{total}) '{judul}' — {perusahaan}")

            try:
                cover_letter = self._generate_cover_letter(lowongan)
                self.db.update_cover_letter(lowongan["id"], cover_letter)
                path_txt, path_pdf = self._simpan_file(lowongan, cover_letter)
                logger.info(
                    f"[GENERATOR] Berhasil: '{judul}' | txt={path_txt}"
                    + (f" | pdf={path_pdf}" if path_pdf else "")
                )
                berhasil += 1

            except (requests.RequestException, ConnectionError) as e:
                logger.error(f"[GENERATOR] Gagal koneksi Ollama untuk '{judul}': {e}")
                self.db.update_status(lowongan["id"], "error")
                gagal += 1

            except Exception as e:
                logger.error(f"[GENERATOR] Error tidak terduga untuk '{judul}': {e}")
                self.db.update_status(lowongan["id"], "error")
                gagal += 1

        print(f"[GENERATOR] Selesai. Berhasil: {berhasil}, Gagal: {gagal}, Total: {total}")
        logger.info(f"[GENERATOR] Selesai. Berhasil={berhasil}, Gagal={gagal}, Total={total}")

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _generate_cover_letter(self, lowongan: dict) -> str:
        skill_match = lowongan.get("skill_match", [])
        if isinstance(skill_match, str):
            try:
                skill_match = json.loads(skill_match)
            except json.JSONDecodeError:
                skill_match = [skill_match]

        skill_str = ", ".join(skill_match) if skill_match else "sesuai deskripsi"
        prompt = self._build_prompt(lowongan, skill_str)

        return panggil_ollama(
            prompt=prompt,
            ollama_url=self.config.OLLAMA_URL,
            model=self.config.OLLAMA_MODEL,
        )

    def _build_prompt(self, lowongan: dict, skill_str: str) -> str:
        tanggal = datetime.now().strftime("%d %B %Y")
        nomor_hp = getattr(self.config, "NOMOR_HP", "")

        return f"""Kamu adalah penulis profesional yang ahli membuat cover letter pekerjaan.

Tulis cover letter profesional dalam Bahasa Indonesia berdasarkan data berikut.

=== DATA KANDIDAT ===
Nama    : {self.config.NAMA}
Email   : {self.config.EMAIL}
{f"No. HP  : {nomor_hp}" if nomor_hp else ""}
Tanggal : {tanggal}

=== TARGET POSISI ===
Posisi     : {lowongan.get("judul", "-")}
Perusahaan : {lowongan.get("perusahaan", "-")}
Lokasi     : {lowongan.get("lokasi", "-")}

=== RINGKASAN CV ===
{self.config.CV_TEXT}

=== SKILL YANG RELEVAN ===
{skill_str}

=== INSTRUKSI ===
- Maksimal 300 kata
- Gunakan bahasa formal dan profesional
- Sertakan paragraf pembuka (minat dan posisi yang dilamar)
- Sertakan paragraf isi (keahlian relevan dan pengalaman yang cocok)
- Sertakan paragraf penutup (harapan dan ajakan interview)
- Jangan sertakan subject email, hanya isi surat saja
- Mulai langsung dengan salam pembuka (mis. "Kepada Yth.")
- Akhiri dengan tanda tangan: nama dan kontak
- Jangan tambahkan teks di luar isi cover letter
"""

    def _simpan_file(self, lowongan: dict, cover_letter: str) -> tuple:
        nama_file = f"{lowongan['id']}_{_sanitasi_nama(lowongan.get('perusahaan', 'unknown'))}"

        path_txt = self._dir_output / f"{nama_file}.txt"
        path_txt.write_text(cover_letter, encoding="utf-8")
        logger.debug(f"[GENERATOR] Disimpan: {path_txt}")

        path_pdf = None
        if getattr(self.config, "BUAT_PDF", False):
            path_pdf = self._dir_output / f"{nama_file}.pdf"
            self._buat_pdf(path_pdf=path_pdf, cover_letter=cover_letter, lowongan=lowongan)
            logger.debug(f"[GENERATOR] PDF disimpan: {path_pdf}")

        return path_txt, path_pdf

    def _buat_pdf(self, path_pdf: Path, cover_letter: str, lowongan: dict):
        doc = SimpleDocTemplate(
            str(path_pdf),
            pagesize=A4,
            rightMargin=2.5 * cm,
            leftMargin=2.5 * cm,
            topMargin=2.5 * cm,
            bottomMargin=2.5 * cm,
        )

        styles = getSampleStyleSheet()

        style_header = ParagraphStyle(
            "header",
            parent=styles["Normal"],
            fontSize=10,
            textColor=colors.HexColor("#555555"),
            alignment=TA_RIGHT,
        )
        style_judul = ParagraphStyle(
            "judul",
            parent=styles["Heading1"],
            fontSize=14,
            textColor=colors.HexColor("#1a1a2e"),
            spaceAfter=4,
        )
        style_subjudul = ParagraphStyle(
            "subjudul",
            parent=styles["Normal"],
            fontSize=10,
            textColor=colors.HexColor("#444444"),
            spaceAfter=12,
        )
        style_body = ParagraphStyle(
            "body",
            parent=styles["Normal"],
            fontSize=11,
            leading=18,
            alignment=TA_JUSTIFY,
            spaceAfter=10,
        )

        tanggal = datetime.now().strftime("%d %B %Y")
        story = []

        nomor_hp = getattr(self.config, "NOMOR_HP", "")
        kontak = f"{self.config.NAMA} | {self.config.EMAIL}"
        if nomor_hp:
            kontak += f" | {nomor_hp}"
        story.append(Paragraph(kontak, style_header))
        story.append(Paragraph(tanggal, style_header))
        story.append(Spacer(1, 0.4 * cm))
        story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#cccccc")))
        story.append(Spacer(1, 0.4 * cm))
        story.append(Paragraph(f"Lamaran: {lowongan.get('judul', '-')}", style_judul))
        story.append(Paragraph(lowongan.get("perusahaan", "-"), style_subjudul))

        for paragraf in cover_letter.split("\n"):
            teks = paragraf.strip()
            if teks:
                story.append(Paragraph(teks, style_body))

        story.append(Spacer(1, 0.5 * cm))
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#eeeeee")))
        doc.build(story)
