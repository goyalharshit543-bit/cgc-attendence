# 🎓 FaceAttend — AI Attendance & University ERP SaaS System

AI-powered attendance system designed for universities and colleges:
- 🙋‍♂️ **Student Self-Enrollment**: Students register their face photos or selfies in the browser; 512-d ArcFace vectors are saved into long-term SQL storage.
- 📸 **Teacher Class Attendance**: Teachers upload combined class photos; InsightFace (SCRFD + ArcFace) detects and recognizes every student, marking Present/Absent.
- 🔗 **University ERP SaaS Two-Way Sync**: Fetch student rosters using your University ERP SaaS API Key, and push marked attendance directly to the ERP with one click.
- 🌐 **Live in Google Chrome**: Single-click desktop launcher to run the server and open Google Chrome automatically.

---

## 🚀 Quick Start (Zero Setup — Automatic Installation)

### 1. Launch with One Click
Double-click `start_attendance.bat`.

**Everything is automated on any Windows laptop**:
- 🐍 **Auto-Python**: If Python is not installed, it automatically downloads and installs official Python silently.
- 📦 **Auto-Dependencies**: Automatically downloads all required libraries (`numpy`, `opencv-python`, `insightface`, `onnxruntime`, `streamlit`, `Pillow`, `pandas`, `requests`).
- 🤖 **Auto-AI Models**: Automatically downloads and prepares the InsightFace ArcFace neural networks (`buffalo_l`).
- 🌐 **Auto-Browser**: Boots the local web server and opens Google Chrome at **`http://localhost:8501`**.

---

## 🛠️ System Architecture & Workflow

### 1. Student Self-Enrollment Portal
- Students open the web app on their phone or laptop.
- Enter their **Roll Number**, **Full Name**, **Department**, and **Email**.
- Upload 1–3 photos OR snap a selfie with their device camera (`st.camera_input`).
- **AI Validation**: Confirms a single clear face is present, computes a 512-dimensional ArcFace embedding, and stores it in SQLite (`attendance.db`).
- Raw photos do not clutter the database — only compact normalized embeddings are saved for long-term recognition.

### 2. Teacher Combined Class Photo Attendance
- At the end of class, the teacher takes 1 or more photos of the classroom.
- Uploads the photo(s) in the **Teacher: Class Attendance** tab.
- **AI Face Detection & Recognition**:
  - SCRFD detects all faces in the photo (handles small faces in back rows).
  - ArcFace extracts embeddings and compares them with registered students using cosine similarity.
  - Generates annotated classroom photos (Green = Recognized student + confidence, Red = Unrecognized).
  - Displays instant metrics (Faces detected, Present count, Absent count, Attendance percentage).
  - Lists full class roster with editable attendance and unknown face thumbnails.

### 3. University ERP SaaS Integration (API Key Two-Way Sync)
- **Inbound Roster Sync**:
  - Enter your University ERP SaaS API Key and Endpoint in the **University ERP SaaS Sync** tab.
  - Click **'Fetch Students from ERP'**: calls the ERP API (e.g. `/api/students`) to populate/sync student records in SQL.
- **Outbound Attendance Push**:
  - After taking attendance, click **'Push to University ERP'**: sends `present`/`absent` status, confidence scores, and timestamps to the university ERP attendance marking endpoint.
- **Built-in Mock ERP Simulator**:
  - If your university IT team has not yet issued production API keys, check **'Enable Built-in Mock ERP Simulator'** to test the entire roster fetch and attendance push workflow immediately!

---

## 📁 Key Files

| File | Purpose |
|---|---|
| `app.py` | Complete web dashboard (Student Portal, Teacher Attendance, ERP Sync, Roster, Reports) |
| `launch_chrome.py` | Starts the app and opens Google Chrome directly |
| `start_attendance.bat` | One-click Windows launcher |
| `db.py` | SQLite schema and operations (students, encodings, sessions, attendance, ERP config) |
| `engine.py` | InsightFace SCRFD detector + ArcFace 512-d feature extractor + matching |
| `erp_connector.py` | ERP SaaS two-way connector (student fetch + attendance push + simulator) |
| `bulk_enroll.py` | CLI bulk enrollment for folders of student photos |
| `enroll.py` | CLI single student enrollment |
| `data/attendance.db` | Long-term SQLite database file |

---

## ⚙️ Configuration & Environment Variables

You can configure ERP credentials directly inside the web UI, or define them in a `.env` file:

```ini
# University ERP SaaS Configuration
ERP_API_URL=https://erp.youruniversity.edu/api
ERP_API_KEY=your_university_api_key_here
ERP_AUTH_HEADER=Authorization     # or: X-API-Key
ERP_AUTH_SCHEME=Bearer            # scheme prefix used with Authorization
ERP_MOCK_MODE=0                   # set to 1 for offline testing simulator
```
