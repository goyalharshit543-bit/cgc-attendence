"""Group-Photo Attendance & Student Self-Enrollment System.

Comprehensive Web Application:
- Student Self-Enrollment Portal (Webcam & Photo Upload with AI face validation into SQL)
- Teacher Class Photo AI Attendance (InsightFace SCRFD + ArcFace recognition)
- University ERP SaaS Two-Way Integration (Fetch roster via API Key + Push marked attendance)
- Enrolled Students Management & SQL Storage
- Sessions History, Analytics & CSV Export
"""
import os
import warnings
from datetime import date, datetime

import cv2
import numpy as np
import pandas as pd
import streamlit as st

# Suppress harmless deprecation warnings from libraries
warnings.filterwarnings("ignore", category=FutureWarning, module="insightface.*")
warnings.filterwarnings("ignore", message=".*use_container_width.*")

import db
import erp_connector
from engine import FaceEngine, draw_faces

# Page Configuration
st.set_page_config(
    page_title="FaceAttend — AI Attendance & ERP System",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom Styling
st.markdown(
    """
    <style>
        .main-header {
            font-size: 2.2rem;
            font-weight: 700;
            background: linear-gradient(90deg, #00e5ff, #8b5cf6, #ec4899);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 0.2rem;
        }
        .sub-header {
            color: #8b93a7;
            font-size: 1.05rem;
            margin-bottom: 1.5rem;
        }
        .stat-card {
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 10px;
            padding: 15px;
            text-align: center;
        }
        .badge-present {
            color: #10b981;
            font-weight: 600;
        }
        .badge-absent {
            color: #ef4444;
            font-weight: 600;
        }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_resource
def get_engine():
    return FaceEngine()


def bgr_from_upload(uploaded_file) -> np.ndarray | None:
    file_bytes = np.frombuffer(uploaded_file.getvalue(), np.uint8)
    return cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)


def crop_thumbnail(bgr: np.ndarray, bbox, size=120):
    x1, y1, x2, y2 = bbox
    h, w = bgr.shape[:2]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    crop = bgr[y1:y2, x1:x2]
    if crop.size == 0:
        return None
    return cv2.cvtColor(cv2.resize(crop, (size, size)), cv2.COLOR_BGR2RGB)


# Initialize Database
db.init_db()


def get_local_network_ip() -> str:
    """Get the local network IPv4 address for sharing the student link."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.2)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "localhost"


# Check if opened in dedicated student portal mode via URL query parameter (e.g. ?portal=student)
query_portal = st.query_params.get("portal", "").strip().lower()
is_student_portal = (query_portal in ("student", "register", "enroll"))

if is_student_portal:
    # Hide sidebar and Streamlit headers for a clean, dedicated student portal view
    st.markdown(
        """
        <style>
            [data-testid="stSidebar"] { display: none; }
            #MainMenu { visibility: hidden; }
            header { visibility: hidden; }
            footer { visibility: hidden; }
            .stApp { max-width: 680px; margin: 0 auto; }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.markdown('<div class="main-header" style="text-align:center;">🎓 Student Registration Portal</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-header" style="text-align:center;">Register your photo, GR number, and branch once to enable automatic classroom attendance.</div>', unsafe_allow_html=True)

    with st.container():
        st.markdown("#### 1. Your Information")
        c1, c2 = st.columns(2)
        with c1:
            full_name = st.text_input("Full Name *", placeholder="e.g. Harshit Verma").strip()
            roll_no = st.text_input("GR Number / Roll Number *", placeholder="e.g. 131 or GR-202401").strip()
        with c2:
            branch_options = [
                "-- Select Branch / Department --",
                "B.Tech Computer Science & Engineering (CSE)",
                "B.Tech Artificial Intelligence & Machine Learning (AI & ML)",
                "B.Tech Artificial Intelligence & Data Science (AI & DS)",
                "B.Tech Information Technology (IT)",
                "B.Tech Electronics & Communication Engineering (ECE)",
                "B.Tech Mechanical Engineering (ME)",
                "B.Tech Civil Engineering (CE)",
                "B.Tech Electrical Engineering (EE)",
                "OTHER",
            ]
            sel_dept = st.selectbox("Branch / Department *", branch_options)
            if sel_dept == "OTHER":
                dept = st.text_input("Write Your Department / Branch *", placeholder="e.g. B.Tech Chemical Engineering").strip()
            elif sel_dept != "-- Select Branch / Department --":
                dept = sel_dept
            else:
                dept = ""
            section_options = ["-- Select Section (A to Z) * --"] + [f"Section {chr(c)}" for c in range(ord('A'), ord('Z') + 1)]
            sel_sec = st.selectbox("Section *", section_options)
            section = sel_sec.replace("Section ", "").strip() if sel_sec != "-- Select Section (A to Z) * --" else ""
        email = st.text_input("University Email (Optional)", placeholder="e.g. student@college.edu").strip()

        st.markdown("---")
        st.markdown("#### 2. Face Photo / Selfie")
        capture_method = st.radio("Choose Input Method:", ["📸 Live Camera Selfie", "📁 Upload Face Photo"], horizontal=True)

        photos_to_process = []
        if capture_method == "📸 Live Camera Selfie":
            cam_pic = st.camera_input("Take a clear frontal selfie:")
            if cam_pic:
                photos_to_process.append(("Camera Snapshot", cam_pic))
        else:
            uploaded_pics = st.file_uploader(
                "Upload a clear face photo (JPG/PNG):",
                type=["jpg", "jpeg", "png", "webp"],
                accept_multiple_files=False,
            )
            if uploaded_pics:
                photos_to_process.append((uploaded_pics.name, uploaded_pics))

        st.markdown("---")
        if st.button("✨ Submit & Save to Database", type="primary", use_container_width=True):
            if not full_name or not roll_no or not dept or not section:
                st.error("⚠️ Full Name, GR Number, Branch, and Section are required.")
            elif not photos_to_process:
                st.error("⚠️ Please take a selfie or upload a photo.")
            else:
                engine = get_engine()
                with st.spinner("Analyzing face with AI and registering into database..."):
                    sid, is_new = db.upsert_student_from_erp(
                        student_code=roll_no,
                        name=full_name,
                        department=dept,
                        email=email,
                        gr_number=roll_no,
                        branch=dept,
                        section=section,
                    )
                    valid_encodings = 0
                    preview_crops = []
                    error_msgs = []

                    for label, p_file in photos_to_process:
                        img = bgr_from_upload(p_file)
                        if img is None:
                            error_msgs.append(f"{label}: Could not decode image.")
                            continue

                        faces = engine.analyze(img)
                        if len(faces) == 1:
                            db.add_encoding(sid, faces[0].embedding)
                            valid_encodings += 1
                            crop = crop_thumbnail(img, faces[0].bbox, size=150)
                            if crop is not None:
                                preview_crops.append(crop)
                            
                            # Automatically persist photo to student_photos disk folders & DB
                            try:
                                import re
                                clean_c = re.sub(r'[\\/*?:"<>|]', '_', (dept or "General").strip())
                                clean_s = f"Section_{re.sub(r'[\\/*?:\"<>|]', '_', (section or 'General').strip())}"
                                clean_gr = re.sub(r'[\\/*?:"<>|]', '_', str(roll_no).strip())
                                out_dir = Path("data") / "student_photos" / clean_c / clean_s
                                by_gr_dir = Path("data") / "student_photos" / "by_gr"
                                out_dir.mkdir(parents=True, exist_ok=True)
                                by_gr_dir.mkdir(parents=True, exist_ok=True)
                                target_f = out_dir / f"{clean_gr}.jpg"
                                target_gr = by_gr_dir / f"{clean_gr}.jpg"
                                cv2.imwrite(str(target_f), img, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
                                cv2.imwrite(str(target_gr), img, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
                                db.update_student_photo(sid, str(target_f).replace("\\", "/"))
                            except Exception as p_err:
                                print(f"[PhotoSave Error] {p_err}")

                        elif len(faces) == 0:
                            error_msgs.append("No face detected. Ensure good lighting and face camera directly.")
                        else:
                            error_msgs.append(f"Multiple faces detected ({len(faces)}). Only 1 person allowed.")

                    if valid_encodings > 0:
                        st.balloons()
                        sec_info = f", Section: {section}" if section else ""
                        st.success(f"🎉 **Registration Successful!** {full_name} (GR #{roll_no}, Branch: {dept}{sec_info}) saved in the database.")
                        if preview_crops:
                            st.image(preview_crops[0], caption="Verified Biometric Face", width=140)
                        st.info("You're all set! You will now be automatically recognized when class photos are taken.")
                    else:
                        st.error("❌ " + (" ".join(error_msgs) if error_msgs else "Could not detect a clear face."))

    st.stop()  # Stop execution here so teacher admin dashboard is completely inaccessible to students!

# Navigation Sidebar (Teacher / Admin Mode)
st.sidebar.markdown("## 🎓 FaceAttend")
st.sidebar.caption("AI-Powered Class Attendance & ERP System")

menu = st.sidebar.radio(
    "Navigation",
    [
        "📸 Teacher: Class Attendance",
        "🙋‍♂️ Student: Self-Enrollment",
        "🔗 University ERP SaaS Sync",
        "📋 Students & SQL Database",
        "📊 Sessions & Reports",
    ],
)

net_ip = get_local_network_ip()
st.sidebar.divider()
st.sidebar.markdown(
    f"""
    **📢 Student Portal Link:**
    - 🌐 Web: `http://{net_ip}:5000/student`
    - 📲 App: `http://{net_ip}:8501/?portal=student`

    **System Status:**
    - 🗄️ SQL Database: `Connected`
    - 🤖 AI Engine: `SCRFD + ArcFace (512-d)`
    - ⚡ University ERP: `Ready`
    """
)


# ===================================================================== #
# PAGE 1: TEACHER - CLASS ATTENDANCE
# ===================================================================== #
if menu == "📸 Teacher: Class Attendance":
    st.markdown('<div class="main-header">📸 Teacher Class Attendance</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-header">Upload a combined class group photo. AI automatically identifies all faces and marks attendance.</div>', unsafe_allow_html=True)

    sessions = db.list_sessions()

    col_title, col_thresh = st.columns([3, 1])
    with col_title:
        session_mode = st.radio("Session Choice", ["Create New Session", "Add to Existing Session"], horizontal=True)
        if session_mode == "Create New Session":
            session_title = st.text_input("Session Title / Subject", value=f"Lecture on {date.today().strftime('%b %d, %Y')}")
            active_session_id = None
        else:
            if sessions:
                choice = st.selectbox(
                    "Select Existing Session",
                    options=[f"#{s['id']} {s['title']} ({s['present_count'] or 0} present)" for s in sessions],
                )
                active_session_id = int(choice.split("#")[1].split(" ")[0])
            else:
                st.warning("No existing sessions found. Creating a new session.")
                session_title = st.text_input("Session Title", value=f"Class {date.today().isoformat()}")
                active_session_id = None

    with col_thresh:
        match_thresh = st.slider("Recognition Threshold", min_value=0.25, max_value=0.65, value=0.40, step=0.01,
                                help="Cosine similarity threshold. Higher = stricter; Lower = more permissive.")

    st.markdown("### 📤 Upload Classroom Photo(s)")
    photo_files = st.file_uploader(
        "Upload one or more group photos from the class (capturing front, left, or right angles):",
        type=["jpg", "jpeg", "png", "webp"],
        accept_multiple_files=True,
    )

    run_btn = st.button("🚀 Recognize Faces & Mark Attendance", type="primary", use_container_width=True)

    if run_btn and photo_files:
        if len(photo_files) > 5:
            st.warning("⚠️ Maximum 5 classroom photos allowed. Analyzing the first 5 photos.")
            photo_files = photo_files[:5]

        known = db.load_encodings()
        if not known:
            st.error("⚠️ No students enrolled yet in the SQL database! Enroll students first or sync from ERP.")
            st.stop()

        if active_session_id is None:
            active_session_id = db.create_session(session_title or "Untitled Session")

        engine = get_engine()
        present_ids: dict[int, float] = {}  # student_id -> best similarity
        student_appearances: dict[int, list[str]] = {}  # student_id -> list of filenames
        unique_unknown_faces: list[dict] = []  # list of {"crop": img, "embedding": np.ndarray}
        annotated_cards = []
        total_faces_all = 0
        max_faces_in_single_pic = 0

        # Pre-stack known embeddings for secondary cross-validation
        known_sids = []
        known_matrix = []
        for sid, entries in known.items():
            for item in entries:
                known_sids.append(sid)
                known_matrix.append(item[0])
        has_known = len(known_matrix) > 0
        known_mat = np.stack(known_matrix) if has_known else None

        prog_bar = st.progress(0.0)
        st.divider()

        for idx, pf in enumerate(photo_files):
            img = bgr_from_upload(pf)
            if img is None:
                st.error(f"Could not load image: {pf.name}")
                continue

            with st.spinner(f"Analyzing {pf.name} with AI..."):
                raw_faces = engine.analyze(img)
                # Filter out tiny noise artifacts (< 20px)
                faces = [
                    f for f in raw_faces
                    if (f.bbox[2] - f.bbox[0]) >= 20 and (f.bbox[3] - f.bbox[1]) >= 20
                ]
                engine.match_all(faces, known, threshold=match_thresh)

            total_faces_all += len(faces)
            max_faces_in_single_pic = max(max_faces_in_single_pic, len(faces))
            annotated = draw_faces(img, faces)

            matched_this_pic = 0
            for f in faces:
                if f.student_id is not None:
                    matched_this_pic += 1
                    best_sim = present_ids.get(f.student_id, -1.0)
                    present_ids[f.student_id] = max(best_sim, f.similarity or 0.0)
                    if pf.name not in student_appearances.setdefault(f.student_id, []):
                        student_appearances[f.student_id].append(pf.name)
                else:
                    # Check if this face matches an enrolled student at lower confidence (>= 0.32)
                    is_known_student = False
                    if has_known and f.embedding is not None:
                        sims = known_mat @ f.embedding
                        best_k_idx = int(np.argmax(sims))
                        if float(sims[best_k_idx]) >= 0.32:
                            is_known_student = True

                    if not is_known_student:
                        # De-duplicate unknown face with existing unknown faces across photos
                        is_dup = False
                        if f.embedding is not None and len(unique_unknown_faces) > 0:
                            for u in unique_unknown_faces:
                                if u.get("embedding") is not None:
                                    sim = float(np.dot(f.embedding, u["embedding"]) / (np.linalg.norm(f.embedding) * np.linalg.norm(u["embedding"])))
                                    if sim >= 0.35:
                                        is_dup = True
                                        break
                        if not is_dup:
                            crop = crop_thumbnail(img, f.bbox)
                            if crop is not None:
                                unique_unknown_faces.append({"crop": crop, "embedding": f.embedding})

            annotated_cards.append((pf.name, len(faces), matched_this_pic, cv2.cvtColor(annotated, cv2.COLOR_BGR2RGB)))
            prog_bar.progress((idx + 1) / len(photo_files))

        # Save to SQLite database
        for sid, sim in present_ids.items():
            db.mark_present(active_session_id, sid, sim)

        distinct_count = max(max_faces_in_single_pic, len(present_ids) + len(unique_unknown_faces))

        # Store in session state so subsequent actions (e.g. ERP push) don't lose results on rerun
        st.session_state["teacher_session"] = {
            "session_id": active_session_id,
            "total_faces": distinct_count,
            "total_raw_detections": total_faces_all,
            "distinct_people": distinct_count,
            "photos_count": len(photo_files),
            "present_count": len(present_ids),
            "student_appearances": student_appearances,
            "unknown_crops": [u["crop"] for u in unique_unknown_faces],
            "annotated_cards": annotated_cards,
        }

    elif run_btn and not photo_files:
        st.error("Please upload at least one class photo first.")

    # Render results whenever a processed session exists in session state
    if "teacher_session" in st.session_state:
        res = st.session_state["teacher_session"]
        curr_session_id = res["session_id"]
        p_count = res.get("photos_count", len(res.get("annotated_cards", [])))

        if p_count > 1:
            st.info(
                f"📸 **Multi-Photo Intelligent Angle Fusion Active:** Processed {p_count} classroom photos "
                f"({res.get('total_raw_detections', 0)} total face detections). Duplicate sightings across camera angles "
                f"were merged — each student is counted exactly once!"
            )

        for name, n_faces, n_matched, rgb_img in res.get("annotated_cards", []):
            st.markdown(f"#### 📷 {name} — {n_faces} faces detected ({n_matched} recognized)")
            st.image(rgb_img, use_container_width=True)

        st.success(f"🎉 Processed successfully! Marked {res['present_count']} unique student(s) Present for Session #{curr_session_id}.")

        # Metrics Display
        roster = db.session_attendance(curr_session_id)
        present_list = [r for r in roster if r["status"] == "present"]
        absent_list = [r for r in roster if r["status"] == "absent"]
        total_students = len(roster)
        att_rate = (len(present_list) / total_students * 100) if total_students > 0 else 0

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Students Present (Unique)", len(present_list))
        m2.metric("Students Absent", len(absent_list))
        m3.metric("Attendance Rate", f"{att_rate:.1f}%")
        m4.metric(
            "Total Class Strength",
            total_students,
            help=f"{res.get('total_raw_detections', 0)} face scans analyzed across {p_count} photo(s)",
        )

        # Unknown faces gallery
        unknown_crops = res.get("unknown_crops", [])
        if unknown_crops:
            with st.expander(f"⚠️ Unrecognized Faces Detected ({len(unknown_crops)})"):
                st.info("These faces did not match any enrolled student embeddings. They might be visitors or need photo re-enrollment.")
                u_cols = st.columns(6)
                for i, crop in enumerate(unknown_crops[:24]):
                    u_cols[i % 6].image(crop, use_container_width=True)

        # ERP Push Action Box
        st.divider()
        st.markdown("### ⚡ University ERP SaaS Integration")
        col_erp_info, col_erp_btn = st.columns([3, 1])
        with col_erp_info:
            st.write("Ready to mark these attendance records directly in the university ERP SaaS system using your API key?")
        with col_erp_btn:
            push_erp = st.button("📤 Push to University ERP", type="primary", key=f"push_erp_btn_{curr_session_id}")

        if push_erp:
            with st.spinner("Pushing attendance records to ERP SaaS API..."):
                ok, msg = erp_connector.push_session(curr_session_id)
                if ok:
                    st.success(f"✅ {msg}")
                    # refresh roster immediately after ERP push
                    roster = db.session_attendance(curr_session_id)
                else:
                    st.error(f"❌ {msg}")

        # Full Roster Table
        st.markdown("### 📋 Class Attendance Roster")
        appearances = res.get("student_appearances", {})
        roster_data = []
        for r in roster:
            srcs = appearances.get(r["id"], [])
            if r["status"] == "present":
                if len(srcs) > 1:
                    src_str = f"📸 In {len(srcs)} photos"
                elif len(srcs) == 1:
                    src_str = f"📸 {srcs[0]}"
                else:
                    src_str = "✍️ Verified"
            else:
                src_str = "—"

            roster_data.append(
                {
                    "Roll No": r["student_code"],
                    "Name": r["name"],
                    "Department": r.get("department", "—"),
                    "Status": "✅ Present" if r["status"] == "present" else "❌ Absent",
                    "AI Confidence": f"{r['similarity']:.2f}" if r.get("similarity") is not None else "—",
                    "Photo Source": src_str,
                    "ERP Synced": "Yes" if r.get("erp_synced") else "Pending",
                    "Marked Time": r["marked_at"] or "—",
                }
            )
        st.dataframe(pd.DataFrame(roster_data), use_container_width=True, hide_index=True)


# ===================================================================== #
# PAGE 2: STUDENT - SELF ENROLLMENT PORTAL
# ===================================================================== #
elif menu == "🙋‍♂️ Student: Self-Enrollment":
    st.markdown('<div class="main-header">🙋‍♂️ Student Self-Enrollment Portal</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-header">Upload your photo or take a selfie to register your face into the SQL attendance database.</div>', unsafe_allow_html=True)

    # Share link banner for teachers
    st.markdown(
        f"""
        <div style="background: linear-gradient(135deg, rgba(99,102,241,0.18), rgba(6,182,212,0.14)); border: 1px solid rgba(99,102,241,0.45); border-radius: 12px; padding: 18px 20px; margin-bottom: 22px;">
            <h4 style="margin: 0 0 6px 0; color: #fff; font-size: 1.1rem;">📢 Public Student Registration Link</h4>
            <p style="margin: 0 0 12px 0; color: #cbd5e1; font-size: 0.9rem;">
                Share this dedicated link with students via WhatsApp, email, or show on the projector. Students can open it on their phones, enter their Name, GR Number, and Branch, and take a selfie. All records are automatically saved to your database!
            </p>
            <div style="background: rgba(10,13,29,0.85); border: 1px solid rgba(0,229,255,0.3); padding: 10px 16px; border-radius: 8px; font-family: monospace; color: #00e5ff; font-size: 0.92rem; word-break: break-all;">
                🌐 <strong>Dedicated Student Link:</strong> http://{net_ip}:5000/student &nbsp;&nbsp;(or http://localhost:5000/student)
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.info("💡 **Why self-enroll?** By registering your face once, your attendance will be automatically recognized whenever your teacher uploads a class photo. Saves time for everyone!")

    with st.container():
        c1, c2 = st.columns([1, 1])

        with c1:
            st.markdown("#### 1. Student Information")
            full_name = st.text_input("Full Name *", placeholder="e.g. Harshit Verma").strip()
            roll_no = st.text_input("GR Number / Roll Number *", placeholder="e.g. 131 or GR-202401").strip()
            branch_options_teacher = [
                "-- Select Branch / Department --",
                "B.Tech Computer Science & Engineering (CSE)",
                "B.Tech Artificial Intelligence & Machine Learning (AI & ML)",
                "B.Tech Artificial Intelligence & Data Science (AI & DS)",
                "B.Tech Information Technology (IT)",
                "B.Tech Electronics & Communication Engineering (ECE)",
                "B.Tech Mechanical Engineering (ME)",
                "B.Tech Civil Engineering (CE)",
                "B.Tech Electrical Engineering (EE)",
                "OTHER",
            ]
            sel_dept = st.selectbox("Branch / Department *", branch_options_teacher, key="teacher_sel_dept")
            if sel_dept == "OTHER":
                dept = st.text_input("Write Your Department / Branch *", placeholder="e.g. B.Tech Chemical Engineering", key="teacher_other_dept").strip()
            elif sel_dept != "-- Select Branch / Department --":
                dept = sel_dept
            else:
                dept = ""
            teacher_sec_options = ["-- Select Section (A to Z) --"] + [f"Section {chr(c)}" for c in range(ord('A'), ord('Z') + 1)]
            sel_sec_teacher = st.selectbox("Section", teacher_sec_options, key="teacher_sel_sec")
            sec = sel_sec_teacher.replace("Section ", "").strip() if sel_sec_teacher != "-- Select Section (A to Z) --" else ""
            email = st.text_input("University Email", placeholder="e.g. alice@university.edu").strip()

        with c2:
            st.markdown("#### 2. Capture / Upload Face Photo")
            capture_method = st.radio("Choose Photo Input Method:", ["📸 Live Camera Selfie", "📁 Upload Image Files"], horizontal=True)

            photos_to_process = []
            if capture_method == "📸 Live Camera Selfie":
                cam_pic = st.camera_input("Take a clear frontal selfie:")
                if cam_pic:
                    photos_to_process.append(("Camera Snapshot", cam_pic))
            else:
                uploaded_pics = st.file_uploader(
                    "Upload 1 to 3 clear, frontal face photos (JPG/PNG):",
                    type=["jpg", "jpeg", "png", "webp"],
                    accept_multiple_files=True,
                )
                if uploaded_pics:
                    for up in uploaded_pics:
                        photos_to_process.append((up.name, up))

    st.markdown("---")
    enroll_btn = st.button("✨ Complete Enrollment & Save to SQL", type="primary", use_container_width=True)

    if enroll_btn:
        if not roll_no or not full_name or not dept:
            st.error("⚠️ Full Name, GR Number, and Branch are required.")
        elif not photos_to_process:
            st.error("⚠️ Please take a camera snapshot or upload at least one photo.")
        else:
            engine = get_engine()
            with st.spinner("Analyzing face with AI and computing 512-d embeddings..."):
                # Upsert student in SQLite
                sid, is_new = db.upsert_student_from_erp(
                    student_code=roll_no,
                    name=full_name,
                    department=dept,
                    email=email,
                    gr_number=roll_no,
                    branch=dept,
                    section=sec,
                )

                valid_encodings = 0
                error_msgs = []
                preview_crops = []

                for label, p_file in photos_to_process:
                    img = bgr_from_upload(p_file)
                    if img is None:
                        error_msgs.append(f"{label}: Could not decode image.")
                        continue

                    faces = engine.analyze(img)
                    if len(faces) == 1:
                        db.add_encoding(sid, faces[0].embedding)
                        valid_encodings += 1
                        crop = crop_thumbnail(img, faces[0].bbox, size=150)
                        if crop is not None:
                            preview_crops.append(crop)
                        
                        # Automatically persist photo to student_photos disk folders & DB
                        try:
                            import re
                            clean_c = re.sub(r'[\\/*?:"<>|]', '_', (dept or "General").strip())
                            clean_s = f"Section_{re.sub(r'[\\/*?:\"<>|]', '_', (sec or 'General').strip())}"
                            clean_gr = re.sub(r'[\\/*?:"<>|]', '_', str(roll_no).strip())
                            out_dir = Path("data") / "student_photos" / clean_c / clean_s
                            by_gr_dir = Path("data") / "student_photos" / "by_gr"
                            out_dir.mkdir(parents=True, exist_ok=True)
                            by_gr_dir.mkdir(parents=True, exist_ok=True)
                            target_f = out_dir / f"{clean_gr}.jpg"
                            target_gr = by_gr_dir / f"{clean_gr}.jpg"
                            cv2.imwrite(str(target_f), img, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
                            cv2.imwrite(str(target_gr), img, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
                            db.update_student_photo(sid, str(target_f).replace("\\", "/"))
                        except Exception as p_err:
                            print(f"[PhotoSave Error] {p_err}")

                    elif len(faces) == 0:
                        error_msgs.append(f"{label}: No face detected. Ensure good lighting and face camera.")
                    else:
                        error_msgs.append(f"{label}: Multiple faces detected ({len(faces)}). Only 1 person allowed.")

                if valid_encodings > 0:
                    action_word = "Enrolled" if is_new else "Updated"
                    st.success(f"🎉 **{action_word} successfully!** {full_name} ({roll_no}) saved in the database with {valid_encodings} face embedding(s).")

                    if preview_crops:
                        st.markdown("##### Registered Face Preview:")
                        p_cols = st.columns(len(preview_crops))
                        for i, pc in enumerate(preview_crops):
                            p_cols[i].image(pc, caption=f"Encoding #{i+1}", width=120)

                    if error_msgs:
                        with st.expander("Some photos were skipped:"):
                            for err in error_msgs:
                                st.warning(err)
                else:
                    if is_new:
                        db.delete_student(sid)
                    st.error("❌ Enrollment failed: No valid single-face photos detected.")
                    for err in error_msgs:
                        st.warning(err)


# ===================================================================== #
# PAGE 3: UNIVERSITY ERP SAAS INTEGRATION
# ===================================================================== #
elif menu == "🔗 University ERP SaaS Sync":
    st.markdown('<div class="main-header">🔗 University ERP SaaS Integration</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-header">Connect with the University ERP API to fetch student rosters and push attendance records automatically.</div>', unsafe_allow_html=True)

    current_cfg = db.get_erp_config()
    env_cfg = erp_connector.load_env()

    with st.expander("ℹ️ How ERP SaaS Integration Works", expanded=True):
        st.markdown(
            """
            1. **Inbound Sync**: Enter your University ERP API Key and Endpoint. Click **'Fetch Students from ERP'** to import student rosters directly into your local SQL database.
            2. **Outbound Sync**: When the teacher marks attendance, clicking **'Push to University ERP'** sends marked attendance (`present`/`absent`, timestamps, confidence scores) to the ERP API.
            3. **Simulator Mode**: If your university has not yet generated your live API key, toggle **Mock ERP Simulator** to test the entire flow with realistic data!
            """
        )

    st.markdown("### ⚙️ ERP SaaS API Settings")
    with st.form("erp_config_form"):
        col_url, col_key = st.columns(2)
        with col_url:
            erp_url = st.text_input(
                "ERP Base API URL",
                value=current_cfg.get("ERP_API_URL") or env_cfg.get("ERP_API_URL", "https://erp.university.edu/api"),
                help="Base URL of the university ERP API endpoint.",
            )
        with col_key:
            erp_key = st.text_input(
                "University ERP SaaS API Key",
                value=current_cfg.get("ERP_API_KEY") or env_cfg.get("ERP_API_KEY", ""),
                type="password",
                help="API Key provided by your University ERP administrator.",
            )

        col_h, col_s = st.columns(2)
        with col_h:
            erp_header = st.selectbox(
                "Authentication Header Name",
                ["Authorization", "X-API-Key", "X-Auth-Token"],
                index=0 if current_cfg.get("ERP_AUTH_HEADER") != "X-API-Key" else 1,
            )
        with col_s:
            erp_scheme = st.selectbox(
                "Token Prefix / Scheme (for Authorization header)",
                ["Bearer", "ApiKey", "Token", "None"],
                index=0,
            )

        mock_mode = st.checkbox(
            "🧪 Enable Built-in Mock ERP Simulator (test without live college server)",
            value=(current_cfg.get("ERP_MOCK_MODE") == "1" or env_cfg.get("ERP_MOCK_MODE") == "1"),
        )

        save_cfg = st.form_submit_button("💾 Save ERP Settings", type="primary")

    if save_cfg:
        db.save_erp_config(
            api_url=erp_url,
            api_key=erp_key,
            auth_header=erp_header,
            auth_scheme="" if erp_scheme == "None" else erp_scheme,
            mock_mode=mock_mode,
        )
        st.success("✅ ERP configuration saved to SQL database!")
        st.rerun()

    st.divider()

    st.markdown("### 🔄 Two-Way ERP Actions")
    col_t1, col_t2 = st.columns(2)

    with col_t1:
        st.markdown("#### 1. Test Connection")
        st.write("Verify that your API key and ERP endpoint are reachable.")
        if st.button("🔍 Test ERP Connection"):
            with st.spinner("Testing connection to ERP..."):
                cfg = erp_connector.load_env()
                is_mock = cfg.get("ERP_MOCK_MODE") in ("1", "true", "True", True)
                ok, msg = erp_connector.test_erp_connection(
                    api_url=cfg.get("ERP_API_URL", ""),
                    api_key=cfg.get("ERP_API_KEY", ""),
                    auth_header=cfg.get("ERP_AUTH_HEADER", "Authorization"),
                    auth_scheme=cfg.get("ERP_AUTH_SCHEME", "Bearer"),
                    mock_mode=is_mock,
                )
                if ok:
                    st.success(f"✅ {msg}")
                else:
                    st.error(f"❌ {msg}")

    with col_t2:
        st.markdown("#### 2. Fetch Students from ERP")
        st.write("Import / update student roster from university ERP into local SQL.")
        if st.button("📥 Fetch Students from ERP", type="primary"):
            with st.spinner("Fetching roster from University ERP..."):
                cfg = erp_connector.load_env()
                ok, msg, ins, upd = erp_connector.fetch_students_from_erp(cfg)
                if ok:
                    st.success(f"✅ {msg}")
                    st.info(f"Added {ins} new students, updated {upd} existing students in SQL.")
                else:
                    st.error(f"❌ {msg}")

    # Display Last Sync Info
    st.divider()
    students = db.list_students()
    erp_synced_count = sum(1 for s in students if s.get("erp_synced_at"))
    st.metric("Total Students in SQL", len(students))
    st.caption(f"{erp_synced_count} of {len(students)} students have synced with University ERP.")


# ===================================================================== #
# PAGE 4: STUDENTS & SQL DATABASE
# ===================================================================== #
elif menu == "📋 Students & SQL Database":
    st.markdown('<div class="main-header">📋 Enrolled Students & SQL Records</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-header">View and manage long-term student biometric records stored in SQLite.</div>', unsafe_allow_html=True)

    students = db.list_students()

    col_search, col_filter = st.columns([3, 1])
    with col_search:
        search_query = st.text_input("🔍 Search by Student Name or Roll Number", placeholder="Type name or roll number...")
    with col_filter:
        filter_status = st.selectbox("Filter", ["All Students", "With Face Photos", "Missing Face Photos"])

    filtered = students
    if search_query:
        q = search_query.lower()
        filtered = [s for s in filtered if q in s["name"].lower() or q in s["student_code"].lower()]

    if filter_status == "With Face Photos":
        filtered = [s for s in filtered if s["num_encodings"] > 0]
    elif filter_status == "Missing Face Photos":
        filtered = [s for s in filtered if s["num_encodings"] == 0]

    # Metrics
    m1, m2, m3 = st.columns(3)
    m1.metric("Total Enrolled Students", len(students))
    m2.metric("With Registered Face Data", sum(1 for s in students if s["num_encodings"] > 0))
    m3.metric("Awaiting Photos", sum(1 for s in students if s["num_encodings"] == 0))

    st.markdown("---")

    if filtered:
        table_data = []
        for s in filtered:
            table_data.append(
                {
                    "ID": s["id"],
                    "GR Number": s.get("gr_number") or s["student_code"],
                    "Full Name": s["name"],
                    "Branch": s.get("branch") or s.get("department") or "—",
                    "Section": s.get("section") or "—",
                    "Email": s.get("email") or "—",
                    "Face Encodings": f"✅ {s['num_encodings']} photo(s)" if s["num_encodings"] > 0 else "❌ None",
                    "ERP Synced": "Yes" if s.get("erp_synced_at") else "No",
                    "Enrolled Date": s["created_at"][:10],
                }
            )
        st.dataframe(pd.DataFrame(table_data), use_container_width=True, hide_index=True)
    else:
        st.info("No matching students found.")

    with st.expander("🗑️ Delete a Student Record"):
        if students:
            options = {f"#{s['id']} — {s['student_code']} ({s['name']})": s["id"] for s in students}
            sel = st.selectbox("Select student to remove:", list(options.keys()))
            if st.button("Delete Permanently from SQL", type="secondary"):
                sid = options[sel]
                db.delete_student(sid)
                st.success(f"Student #{sid} removed.")
                st.rerun()


# ===================================================================== #
# PAGE 5: SESSIONS & REPORTS
# ===================================================================== #
else:
    st.markdown('<div class="main-header">📊 Class Sessions & Attendance Reports</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-header">View past lecture attendance sessions, ERP sync status, and download CSV reports.</div>', unsafe_allow_html=True)

    sessions = db.list_sessions()
    if not sessions:
        st.info("No attendance sessions yet. Run a session from the 'Teacher: Class Attendance' page.")
    else:
        for s in sessions:
            synced_badge = "🟢 Synced to ERP" if s.get("erp_synced_count", 0) > 0 else "⚪ Not Synced"
            with st.expander(f"Session #{s['id']} — {s['title']} ({s['present_count'] or 0} present) [{synced_badge}]"):
                roster = db.session_attendance(s["id"])

                c_top1, c_top2 = st.columns([3, 1])
                with c_top1:
                    st.write(f"**Date:** {s['created_at'][:19]} | **Total Students:** {len(roster)} | **Present:** {s['present_count'] or 0}")
                with c_top2:
                    if st.button(f"⚡ Push Session #{s['id']} to ERP", key=f"repush_{s['id']}"):
                        with st.spinner("Pushing to ERP..."):
                            ok, msg = erp_connector.push_session(s["id"])
                            if ok:
                                st.success(msg)
                                st.rerun()
                            else:
                                st.error(msg)

                # Roster table
                r_data = [
                    {
                        "Roll No": r["student_code"],
                        "Name": r["name"],
                        "Department": r.get("department", "—"),
                        "Status": "✅ Present" if r["status"] == "present" else "❌ Absent",
                        "Confidence": f"{r['similarity']:.2f}" if r.get("similarity") is not None else "—",
                        "ERP Synced": "Yes" if r.get("erp_synced") else "No",
                    }
                    for r in roster
                ]
                st.dataframe(pd.DataFrame(r_data), use_container_width=True, hide_index=True)

                st.download_button(
                    label="⬇️ Download Attendance CSV",
                    data=db.attendance_csv(s["id"]),
                    file_name=f"attendance_session_{s['id']}_{s['title'].replace(' ', '_')}.csv",
                    mime="text/csv",
                    key=f"dl_csv_{s['id']}",
                )
