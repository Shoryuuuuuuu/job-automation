# Job Automation Pipeline

Sistem otomatisasi pencarian dan pelamaran kerja. Setiap hari, program ini secara otomatis melakukan scraping lowongan dari **Jobstreet**, menilai kecocokan menggunakan AI lokal (Ollama), membuat cover letter, mengisi dan mengirimkan lamaran (Quick Apply), serta memantau email balasan dari perusahaan — semuanya tanpa interaksi manual setelah setup awal.

---

## Daftar Isi

- [Gambaran Umum](#gambaran-umum)
- [Arsitektur & Alur Kerja](#arsitektur--alur-kerja)
- [Struktur Project](#struktur-project)
- [Prasyarat](#prasyarat)
- [Instalasi](#instalasi)
- [Konfigurasi (.env)](#konfigurasi-env)
- [Setup CV](#setup-cv)
- [Setup Ollama](#setup-ollama)
- [Setup Telegram Bot](#setup-telegram-bot)
- [Setup Gmail (Email Monitor)](#setup-gmail-email-monitor)
- [Menjalankan Program](#menjalankan-program)
- [Login Pertama Kali (Jobstreet)](#login-pertama-kali-jobstreet)
- [Status Lowongan & Siklus Hidup](#status-lowongan--siklus-hidup)
- [Penjelasan Setiap Modul](#penjelasan-setiap-modul)
- [Debugging & Troubleshooting](#debugging--troubleshooting)
- [Batasan & Catatan Penting](#batasan--catatan-penting)

---

## Gambaran Umum

Program ini terdiri dari dua proses terjadwal yang berjalan terus-menerus selama program aktif:

| Proses | Frekuensi | Fungsi |
|---|---|---|
| **Pipeline harian** | Setiap hari pukul **10:15** | Scrape → Scoring → Cover letter → Apply → Laporan |
| **Monitor email** | Setiap **30 menit** (+ saat startup) | Cek balasan Jobstreet via Gmail, update status, notif Telegram |

Semua data lowongan disimpan di database SQLite lokal (`jobs.db`). Notifikasi dikirim ke Telegram.

---

## Arsitektur & Alur Kerja

```
10:15 setiap hari
   │
   ├─ 1. SCRAPER
   │     Scrape Jobstreet untuk setiap kombinasi POSISI_TARGET × LOKASI.
   │     Filter: blacklist kata, gaji minimum, anti-duplikat.
   │     Simpan semua yang lolos → status 'baru'
   │
   ├─ 2. SCORER
   │     Untuk setiap lowongan 'baru', kirim CV + deskripsi ke Ollama.
   │     AI menilai skor 0-100, skill match/gap, alasan, rekomendasi.
   │     Jika skor < SKOR_MINIMUM → 'auto_skip'
   │     Jika skor >= SKOR_MINIMUM → masuk kandidat
   │     Ambil 10 kandidat skor tertinggi per platform → 'pending_review'
   │     Sisanya → 'auto_skip'
   │
   ├─ 3. GENERATOR
   │     Untuk setiap 'pending_review', generate cover letter
   │     berbahasa Indonesia via Ollama. Simpan ke DB + file .txt
   │
   ├─ 4. APPLIER
   │     Buka browser Chromium (persistent session).
   │     Untuk setiap 'pending_review':
   │       - Klik "Quick apply"
   │       - Pilih CV dari profil Jobstreet
   │       - Tempel cover letter hasil AI
   │       - Jawab pertanyaan employer otomatis via AI
   │       - Submit aplikasi
   │     Berhasil → 'terkirim'   |   Gagal → 'error'
   │
   ├─ 5. EXPIRY CHECK
   │     'waiting_edit' lebih dari 3 hari → 'skipped'
   │
   └─ 6. LAPORAN HARIAN
         Kirim ringkasan statistik ke Telegram

setiap 30 menit (+ saat startup)
   │
   └─ EMAIL MONITOR
         Cek inbox Gmail untuk email dari Jobstreet/Seek.
         Deteksi tipe (viewed/message/interview/rejected/shortlisted)
         dari subject email, cocokkan nama perusahaan dengan DB,
         update status, kirim notifikasi Telegram.
```

---

## Struktur Project

```
job_automation/
├── main.py              # Entry point — scheduler & pipeline
├── config.py            # Konfigurasi dari .env
├── database.py          # SQLite — semua query & skema
├── scraper.py           # Scraping Jobstreet
├── scorer.py            # Scoring AI + filter top-10
├── generator.py         # Generate cover letter via AI
├── applier.py           # Auto-apply via Playwright
├── notifier.py          # Laporan ke Telegram
├── email_monitor.py     # Monitor Gmail untuk balasan
├── .env                 # Konfigurasi rahasia (JANGAN di-commit)
├── jobs.db              # Database SQLite (auto-dibuat)
├── app.log              # Log aplikasi (auto-dibuat)
└── output/
    ├── cv/
    │   └── cv.txt              # Teks CV kamu (WAJIB diisi manual)
    ├── cover_letters/          # Cover letter hasil generate (auto)
    ├── debug/                  # Screenshot debug applier (auto)
    └── browser_data/
        └── jobstreet/          # Session login Jobstreet (auto)
```

---

## Prasyarat

- **Python 3.10+**
- **Google Chrome / Chromium** (diinstal otomatis oleh Playwright)
- **Ollama** terinstal dan berjalan di lokal (atau server lain yang bisa diakses)
- Akun **Jobstreet** (login via Google)
- Akun **Telegram** + bot
- Akun **Gmail** dengan IMAP aktif

---

## Instalasi

1. Clone/download project ke folder `job_automation/`

2. Buat virtual environment (opsional tapi disarankan):
   ```bash
   python -m venv venv
   venv\Scripts\activate        # Windows
   source venv/bin/activate     # Linux/Mac
   ```

3. Install dependencies:
   ```bash
   pip install playwright requests python-dotenv schedule python-telegram-bot reportlab
   playwright install chromium
   ```

4. Buat file `.env` di root project (lihat contoh di bawah)

5. Buat file `output/cv/cv.txt` berisi teks CV kamu

---

## Konfigurasi (.env)

Buat file `.env` di root project:

```env
# ── Profil ──────────────────────────────────────────
NAMA=Nama Lengkap Kamu
EMAIL=emailkamu@gmail.com
NOMOR_HP=08123456789

# ── Target lowongan ───────────────────────────────────
POSISI_TARGET=Frontend Developer,React Developer,Web Developer,Machine Learning Engineer,AI Engineer
LOKASI=Jakarta,Remote,Hybrid
GAJI_MINIMUM=5000000
SKOR_MINIMUM=60
BLACKLIST=sales,target,MLM,commission,asuransi

# ── Platform (fokus Jobstreet) ────────────────────────
PLATFORM=jobstreet

# ── Telegram Bot ───────────────────────────────────────
TELEGRAM_TOKEN=isi_token_bot_telegram
TELEGRAM_CHAT_ID=isi_chat_id_kamu

# ── Ollama (LLM lokal) ──────────────────────────────────
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:7b

# ── Gmail (untuk email monitor) ─────────────────────────
SMTP_USER=emailkamu@gmail.com
SMTP_PASS=app_password_16_karakter
```

### Penjelasan Variabel

| Variabel | Wajib | Keterangan |
|---|---|---|
| `NAMA`, `EMAIL`, `NOMOR_HP` | Ya | Digunakan di cover letter |
| `POSISI_TARGET` | Tidak | Daftar posisi yang dicari, dipisah koma |
| `LOKASI` | Tidak | Daftar lokasi, dipisah koma |
| `GAJI_MINIMUM` | Tidak | Lowongan dengan gaji di bawah ini di-skip (default: 5.000.000) |
| `SKOR_MINIMUM` | Tidak | Skor AI minimum agar lolos ke pending_review (default: 60) |
| `BLACKLIST` | Tidak | Kata yang membuat lowongan otomatis di-skip |
| `PLATFORM` | Tidak | Default `jobstreet` |
| `TELEGRAM_TOKEN` / `TELEGRAM_CHAT_ID` | Ya | Untuk notifikasi |
| `OLLAMA_URL` / `OLLAMA_MODEL` | Tidak | Default localhost & qwen2.5:7b |
| `SMTP_USER` / `SMTP_PASS` | Ya (untuk email monitor) | Email & App Password Gmail |

---

## Setup CV

Buat file `output/cv/cv.txt` berisi **ringkasan CV dalam bentuk teks biasa**. Contoh:

```
RIVALDI ZULFAH
Frontend Developer

PENGALAMAN:
- Front End Developer di PT Macnesia Inti Teknologi (Okt 2021 - Okt 2022)
  Mengembangkan antarmuka web responsif menggunakan HTML, CSS, JavaScript,
  dan integrasi REST API berbasis Laravel.

- Web Developer (Magang) di SMK Pusaka 1 (Okt 2025 - Des 2025)
  Merancang dan membangun Sistem Informasi Sekolah dengan Laravel dan MySQL.

PENDIDIKAN:
- Bachelor of Information Technology, Bina Sarana Informatika University
  (Expected finish Jul 2026)

SKILL:
HTML, CSS, JavaScript, React.js, Node.js, Bootstrap, jQuery, PostgreSQL,
Python, Problem Solving, Team Work
```

Teks ini akan dikirim ke AI untuk: scoring kecocokan lowongan, generate cover letter, dan menjawab pertanyaan employer saat apply.

---

## Setup Ollama

1. Download dan install Ollama dari [ollama.com](https://ollama.com)
2. Tarik model yang akan digunakan:
   ```bash
   ollama pull qwen2.5:7b
   ```
3. Pastikan Ollama berjalan (biasanya otomatis di `http://localhost:11434`)
4. Test:
   ```bash
   curl http://localhost:11434/api/generate -d "{\"model\":\"qwen2.5:7b\",\"prompt\":\"halo\",\"stream\":false}"
   ```

---

## Setup Telegram Bot

1. Chat dengan [@BotFather](https://t.me/BotFather) di Telegram
2. Kirim `/newbot`, ikuti instruksi, simpan **token** yang diberikan
3. Untuk mendapatkan **chat ID**:
   - Kirim pesan apa saja ke bot kamu
   - Buka `https://api.telegram.org/bot<TOKEN>/getUpdates`
   - Cari nilai `"chat":{"id": ...}`
4. Isi `TELEGRAM_TOKEN` dan `TELEGRAM_CHAT_ID` di `.env`

---

## Setup Gmail (Email Monitor)

Email monitor membaca inbox Gmail untuk mendeteksi notifikasi dari Jobstreet (bukan email langsung dari perusahaan — Jobstreet selalu mengirim notifikasi perantara saat ada update lamaran).

1. Buka **Gmail → Settings → See all settings → Forwarding and POP/IMAP**
2. Aktifkan **IMAP**
3. Buat **App Password**:
   - Buka [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
   - Pilih app "Mail", buat password 16 karakter
   - Salin ke `SMTP_PASS` di `.env` (bukan password Gmail biasa)

### Tipe Event yang Dideteksi

| Event | Contoh Subject Email | Status DB |
|---|---|---|
| `interview` | "Interview invitation from PT ABC" | → `interview` |
| `rejected` | "Your application was unsuccessful" | → `ditolak` |
| `message` | "PT ABC has messaged you" | → `waiting_edit` |
| `shortlisted` | "You've been shortlisted" | → `waiting_edit` |
| `viewed` | "PT ABC has viewed your application" | tidak berubah, hanya notif |

---

## Menjalankan Program

```bash
python main.py
```

Saat dijalankan, program akan:
1. Menampilkan ringkasan konfigurasi
2. Validasi `.env` (gagal jika ada yang wajib belum diisi)
3. Reset status `processing`/`scored` yang menggantung (recovery dari crash)
4. Jadwalkan pipeline harian (10:15) dan email monitor (tiap 30 menit)
5. Langsung jalankan email monitor sekali (cek inbox saat startup)
6. Menunggu di loop — program harus tetap berjalan (gunakan `screen`/`tmux`/Task Scheduler agar tidak terhenti)

### Menjalankan Pipeline Manual (Testing)

Untuk testing tanpa menunggu jam 10:15, edit `main.py` bagian akhir:

```python
# Tambahkan baris ini sebelum loop while
jalankan_pipeline()
```

---

## Login Pertama Kali (Jobstreet)

Saat pipeline mencapai langkah **Apply Otomatis** untuk pertama kali, browser Chromium akan terbuka otomatis dan program menunggu kamu login secara manual:

1. Terminal menampilkan instruksi login
2. Di browser yang terbuka, klik **Sign in** → **Continue with Google** → pilih akun
3. **Penting**: setelah login di `id.jobstreet.com`, klik tombol **Apply** pada salah satu lowongan — ini akan memicu redirect ke `login.seek.com` (domain berbeda untuk proses apply)
4. Login juga di halaman `login.seek.com` dengan akun yang sama
5. Setelah kedua login selesai, ketik `lanjut` + Enter di terminal (atau program akan mendeteksi otomatis)

Session akan **tersimpan permanen** di `output/browser_data/jobstreet/` — login manual hanya diperlukan sekali, kecuali session expired (jarang, biasanya beberapa minggu/bulan).

> ⚠️ Jika muncul "Couldn't sign you in — this browser may not be insecure" dari Google, klik **Try again** — ini peringatan standar untuk automated browser, tetap bisa login.

---

## Status Lowongan & Siklus Hidup

```
baru ──────► processing ──────► scored ──┬──► pending_review ──► terkirim
                                          │           │              │
                                          │           │              ├──► viewed (notif saja)
                                          │           │              ├──► interview
                                          │           │              ├──► ditolak
                                          │           │              └──► waiting_edit ──► skipped (>3 hari)
                                          │           │
                                          └──► auto_skip (skor < minimum ATAU bukan top-10)

error / error_auth / email_invalid — status error di berbagai tahap
```

| Status | Arti |
|---|---|
| `baru` | Baru di-scrape, belum di-scoring |
| `processing` | Sedang diproses (scoring/apply) |
| `scored` | Sudah di-scoring AI, menunggu filter top-10 |
| `pending_review` | Top-10 skor tertinggi, siap dibuat cover letter & apply |
| `auto_skip` | Skor di bawah minimum atau bukan top-10 |
| `terkirim` | Lamaran berhasil dikirim |
| `viewed` | Perusahaan melihat lamaran (dari email monitor) |
| `interview` | Diundang interview |
| `ditolak` | Lamaran ditolak |
| `waiting_edit` | Perlu aksi manual (ada pesan/shortlist dari perusahaan) |
| `skipped` | Dilewati (waiting_edit kadaluarsa) |
| `error` | Gagal di salah satu tahap |

---

## Penjelasan Setiap Modul

### `config.py`
Memuat semua nilai dari `.env` ke instance `Config`. Validasi dilakukan saat startup — program berhenti jika ada variabel wajib yang kosong.

### `database.py`
Wrapper SQLite. Tabel utama `lowongan` menyimpan semua data lowongan + hasil scoring + cover letter + status. Tabel `log_error` mencatat error per modul. Tabel `konfigurasi` menyimpan state internal (UID email yang sudah diproses).

### `scraper.py`
Scrape Jobstreet untuk setiap kombinasi `POSISI_TARGET` × `LOKASI`. Mengambil judul, perusahaan, lokasi, gaji, URL, dan deskripsi lengkap. Filter anti-duplikat berbasis URL dan kesamaan judul+perusahaan dalam 7 hari terakhir.

### `scorer.py`
Mengirim CV + deskripsi lowongan ke Ollama, menerima skor 0-100 beserta skill match/gap dan rekomendasi. Setelah semua lowongan `baru` diproses, mengambil 10 skor tertinggi per platform sebagai `pending_review`.

### `generator.py`
Generate cover letter berbahasa Indonesia (maks 300 kata) via Ollama berdasarkan CV dan detail lowongan. Disimpan ke kolom `cover_letter` di DB dan file `.txt` di `output/cover_letters/`.

### `applier.py`
Modul paling kompleks — menggunakan Playwright dengan **persistent browser context** (session tersimpan di disk). Untuk setiap `pending_review`:
- Klik Quick Apply
- Pilih CV dari profil Jobstreet
- Tempel cover letter ke kolom "Write a cover letter"
- Jawab pertanyaan employer (dropdown/checkbox/radio) secara otomatis menggunakan Ollama, dicocokkan dengan isi CV
- Submit aplikasi

Screenshot debug otomatis disimpan ke `output/debug/` setiap kali ada langkah penting atau error, untuk memudahkan troubleshooting saat Jobstreet mengubah tampilan.

### `notifier.py`
Mengirim laporan statistik harian (total, terkirim, interview, win rate) ke Telegram.

### `email_monitor.py`
Cek inbox Gmail via IMAP setiap 30 menit. Mencari email dari domain Jobstreet/Seek, mendeteksi tipe event dari subject, mencocokkan nama perusahaan dengan lowongan di DB, update status, dan kirim notifikasi Telegram.

---

## Debugging & Troubleshooting

### Browser Quick Apply gagal / selector tidak ditemukan
Jobstreet sering mengubah struktur halaman. Cek folder `output/debug/` — setiap kegagalan menyimpan screenshot dengan nama `jobstreet_<tahap>_<id_lowongan>_<timestamp>.png`. Bandingkan dengan tampilan asli untuk update selector di `applier.py`.

### "Session expired — redirect ke login"
Folder `output/browser_data/jobstreet/` kemungkinan corrupt atau session benar-benar expired. Hapus folder tersebut dan jalankan ulang — program akan minta login manual lagi.

### Scraper timeout saat ambil deskripsi
Fungsi `_ambil_deskripsi` menggunakan `wait_until="domcontentloaded"` dan memblokir resource berat (gambar, font, analytics) untuk mempercepat load. Jika masih timeout, halaman akan tetap diproses dengan fallback selector atau diambil dari `<body>`.

### Tidak ada lowongan yang lolos ke `pending_review`
Cek log untuk skor yang diberikan AI — mungkin `SKOR_MINIMUM` di `.env` terlalu tinggi relatif terhadap CV dan lowongan yang tersedia.

### Email monitor tidak mendeteksi apa-apa
- Pastikan IMAP aktif di Gmail
- Pastikan `SMTP_PASS` adalah App Password (16 karakter), bukan password biasa
- Cek apakah email dari Jobstreet masuk folder Spam

### Program crash di tengah jalan
Saat restart, `db.reset_status_processing()` otomatis mengembalikan lowongan yang stuck di `processing`/`scored` ke `baru`, sehingga tidak ada data yang hilang.

---

## Batasan & Catatan Penting

- **Hanya mendukung Jobstreet.** Glints di-drop karena scraping membutuhkan login dan struktur halaman sering berubah; Google OAuth juga diblokir Playwright untuk Glints.
- **Browser harus tetap terbuka saat login pertama kali** — pastikan kamu standby saat pipeline mencapai tahap Apply untuk pertama kalinya.
- **Jawaban pertanyaan employer di Jobstreet dipilih oleh AI** berdasarkan CV — disarankan sesekali memeriksa hasil apply secara manual untuk memastikan jawaban relevan.
- **Rate limiting**: ada `time.sleep()` di berbagai titik (scraping, apply) untuk menghindari deteksi bot. Jangan menghapus delay ini.
- **Skor AI bersifat subjektif** — tergantung kualitas model Ollama yang digunakan dan kelengkapan `cv.txt`.
- File `.env` dan folder `output/browser_data/` berisi data sensitif — **jangan commit ke Git**.
