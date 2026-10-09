/**
 * FaceAttend 3D Full-Stack Application Controller
 * Handles all business logic, camera feeds, file dropzones, REST API communication, and DOM rendering.
 */

// Global Application State
const state = {
  activeTab: 'showcase',
  sessions: [],
  students: [],
  erpConfig: {},
  networkInfo: null,
  studentPortalUrl: window.location.origin + '/student',
  teacherFiles: [],
  teacherCameraPhotos: [],
  studentFiles: [],
  studentCameraPhoto: null,
  activeCameraStream: null,
  currentTeacherResult: null,
};

// --------------------------------------------------------------------------
// Multi-Device Backend Bridge (Connects Render to Laptop Server & Database)
// --------------------------------------------------------------------------
export const DEFAULT_LAPTOP_BACKEND = 'https://harddisk-calcium-petite.ngrok-free.dev';

export function getApiBaseUrl() {
  const params = new URLSearchParams(window.location.search);
  const qBackend = params.get('backend') || params.get('server') || params.get('api');
  if (qBackend) {
    const clean = qBackend.trim().replace(/\/+$/, '');
    try { localStorage.setItem('cgc_teacher_backend', clean); } catch {}
    return clean;
  }
  try {
    const saved = localStorage.getItem('cgc_teacher_backend');
    if (saved) return saved.trim().replace(/\/+$/, '');
  } catch {}

  // If directly accessing local server or direct Cloudflare tunnel, use relative paths
  if (window.location.hostname === 'localhost' ||
      window.location.hostname === '127.0.0.1' ||
      window.location.hostname.endsWith('.trycloudflare.com')) {
    return '';
  }
  return DEFAULT_LAPTOP_BACKEND;
}

export function apiUrl(path) {
  const base = getApiBaseUrl();
  const clean = path.startsWith('/') ? path : '/' + path;
  return base ? `${base}${clean}` : clean;
}

// --------------------------------------------------------------------------
// Security & Formatting Utilities
// --------------------------------------------------------------------------
export function escapeHtml(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

export function formatLocalTime(isoString) {
  if (!isoString) return '—';
  try {
    const d = new Date(isoString);
    if (isNaN(d.getTime())) return isoString.slice(11, 19) || '—';
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  } catch {
    return '—';
  }
}

export function formatLocalDate(isoString) {
  if (!isoString) return '—';
  try {
    const d = new Date(isoString);
    if (isNaN(d.getTime())) return isoString.slice(0, 10);
    return d.toLocaleDateString([], { year: 'numeric', month: 'short', day: 'numeric' });
  } catch {
    return isoString.slice(0, 10);
  }
}

// --------------------------------------------------------------------------
// Initialization
// --------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', () => {
  initNavigation();
  initThresholdSlider();
  initDropzones();
  initModalListeners();
  loadInitialData();
});

async function loadInitialData() {
  await Promise.all([
    fetchSystemStatus(),
    fetchSessions(),
    fetchStudents(),
    fetchErpConfig(),
    fetchNetworkInfo(),
  ]);
}

// --------------------------------------------------------------------------
// Toast Notification Utility
// --------------------------------------------------------------------------
export function showToast(message, type = 'success') {
  const container = document.getElementById('toast-container');
  if (!container) return;

  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  const icon = type === 'success' ? '✅' : type === 'error' ? '❌' : type === 'warning' ? '⚠️' : 'ℹ️';
  toast.innerHTML = `<span>${icon}</span><span>${message}</span>`;

  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateX(100%)';
    toast.style.transition = 'all 0.3s ease';
    setTimeout(() => toast.remove(), 300);
  }, 4200);
}

// --------------------------------------------------------------------------
// Navigation & Tab Switching
// --------------------------------------------------------------------------
function initNavigation() {
  const tabButtons = document.querySelectorAll('.nav-tab-btn');
  tabButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      const targetTab = btn.getAttribute('data-tab');
      switchTab(targetTab);
    });
  });

  // Action links from hero
  document.querySelectorAll('[data-goto-tab]').forEach(el => {
    el.addEventListener('click', e => {
      e.preventDefault();
      switchTab(el.getAttribute('data-goto-tab'));
    });
  });
}

export function switchTab(tabId) {
  state.activeTab = tabId;

  // Update nav buttons
  document.querySelectorAll('.nav-tab-btn').forEach(b => {
    b.classList.toggle('active', b.getAttribute('data-tab') === tabId);
  });

  // Update tab panels
  document.querySelectorAll('.tab-pane').forEach(p => {
    p.classList.toggle('active', p.id === `tab-${tabId}`);
  });

  // Trigger contextual refreshes
  if (tabId === 'teacher') {
    populateSessionSelect();
  } else if (tabId === 'database') {
    fetchStudents();
  } else if (tabId === 'reports') {
    renderSessionsReport();
  } else if (tabId === 'erp') {
    fetchErpConfig();
  }

  window.scrollTo({ top: 0, behavior: 'smooth' });
}

// --------------------------------------------------------------------------
// Threshold Slider
// --------------------------------------------------------------------------
function initThresholdSlider() {
  const slider = document.getElementById('match-threshold-slider');
  const valDisplay = document.getElementById('match-threshold-val');
  if (slider && valDisplay) {
    slider.addEventListener('input', e => {
      valDisplay.textContent = parseFloat(e.target.value).toFixed(2);
    });
  }
}

// --------------------------------------------------------------------------
// Dropzone & File Handling
// --------------------------------------------------------------------------
function initDropzones() {
  // Teacher Classroom Photos Dropzone
  setupDropzone(
    document.getElementById('teacher-dropzone'),
    document.getElementById('teacher-file-input'),
    files => {
      state.teacherFiles.push(...files);
      renderTeacherFileChips();
    }
  );

  // Student Enrollment Photos Dropzone
  setupDropzone(
    document.getElementById('student-dropzone'),
    document.getElementById('student-file-input'),
    files => {
      state.studentFiles.push(...files);
      renderStudentFileChips();
    }
  );
}

function setupDropzone(zone, input, onFiles) {
  if (!zone || !input) return;

  zone.addEventListener('click', () => input.click());

  input.addEventListener('change', e => {
    if (e.target.files.length) {
      onFiles(Array.from(e.target.files));
      input.value = '';
    }
  });

  ['dragenter', 'dragover'].forEach(name => {
    zone.addEventListener(name, e => {
      e.preventDefault();
      zone.classList.add('dragover');
    });
  });

  ['dragleave', 'drop'].forEach(name => {
    zone.addEventListener(name, e => {
      e.preventDefault();
      zone.classList.remove('dragover');
    });
  });

  zone.addEventListener('drop', e => {
    if (e.dataTransfer.files.length) {
      onFiles(Array.from(e.dataTransfer.files));
    }
  });
}

function renderTeacherFileChips() {
  const container = document.getElementById('teacher-file-chips');
  if (!container) return;
  container.innerHTML = '';

  state.teacherFiles.forEach((file, index) => {
    const chip = document.createElement('div');
    chip.className = 'file-chip';
    chip.innerHTML = `
      <span>📄 ${file.name}</span>
      <span class="file-chip-remove" onclick="removeTeacherFile(${index})">✕</span>
    `;
    container.appendChild(chip);
  });

  state.teacherCameraPhotos.forEach((_, index) => {
    const chip = document.createElement('div');
    chip.className = 'file-chip';
    chip.style.borderColor = 'var(--cyan)';
    chip.innerHTML = `
      <span>📸 Snapshot #${index + 1}</span>
      <span class="file-chip-remove" onclick="removeTeacherCameraPhoto(${index})">✕</span>
    `;
    container.appendChild(chip);
  });
}

window.removeTeacherFile = function(index) {
  state.teacherFiles.splice(index, 1);
  renderTeacherFileChips();
};

window.removeTeacherCameraPhoto = function(index) {
  state.teacherCameraPhotos.splice(index, 1);
  renderTeacherFileChips();
};

function renderStudentFileChips() {
  const container = document.getElementById('student-file-chips');
  if (!container) return;
  container.innerHTML = '';

  state.studentFiles.forEach((file, index) => {
    const chip = document.createElement('div');
    chip.className = 'file-chip';
    chip.innerHTML = `
      <span>📄 ${file.name}</span>
      <span class="file-chip-remove" onclick="removeStudentFile(${index})">✕</span>
    `;
    container.appendChild(chip);
  });
}

window.removeStudentFile = function(index) {
  state.studentFiles.splice(index, 1);
  renderStudentFileChips();
};

// --------------------------------------------------------------------------
// API Calls: System Status
// --------------------------------------------------------------------------
async function fetchSystemStatus() {
  try {
    const res = await fetch(apiUrl('/api/status'));
    const data = await res.json();
    if (data.ok) {
      const totalStudEl = document.getElementById('stat-total-students');
      if (totalStudEl) totalStudEl.textContent = data.students_count;

      const regFacesEl = document.getElementById('stat-registered-faces');
      if (regFacesEl) regFacesEl.textContent = data.students_with_photos;

      const totalSessEl = document.getElementById('stat-total-sessions');
      if (totalSessEl) totalSessEl.textContent = data.sessions_count;

      const erpStatusEl = document.getElementById('status-erp-pill');
      if (erpStatusEl) {
        erpStatusEl.textContent = data.erp_connected ? `⚡ ERP: ${data.erp_mode}` : '⚡ ERP: Ready';
      }
    }
  } catch (err) {
    console.error('Status fetch error:', err);
  }
}

// --------------------------------------------------------------------------
// API Calls: Sessions
// --------------------------------------------------------------------------
async function fetchSessions() {
  try {
    const res = await fetch(apiUrl('/api/sessions'));
    const data = await res.json();
    if (data.ok) {
      state.sessions = data.sessions;
      populateSessionSelect();
    }
  } catch (err) {
    console.error('Sessions fetch error:', err);
  }
}

function populateSessionSelect() {
  const select = document.getElementById('teacher-existing-session-select');
  if (!select) return;

  select.innerHTML = '';
  if (state.sessions.length === 0) {
    const opt = document.createElement('option');
    opt.value = '';
    opt.textContent = 'No previous sessions found';
    select.appendChild(opt);
    return;
  }

  state.sessions.forEach(s => {
    const opt = document.createElement('option');
    opt.value = s.id;
    opt.textContent = `#${s.id} ${s.title} (${s.present_count || 0} present) — ${s.created_at.slice(0, 10)}`;
    select.appendChild(opt);
  });
}

// --------------------------------------------------------------------------
// Feature 1: Teacher Class Attendance Recognition
// --------------------------------------------------------------------------
export async function runAttendanceRecognition() {
  const mode = document.querySelector('input[name="teacher_session_mode"]:checked').value;
  const newTitle = document.getElementById('teacher-session-title').value.trim();
  const existingId = document.getElementById('teacher-existing-session-select').value;
  const threshold = document.getElementById('match-threshold-slider').value;
  const dept = document.getElementById('teacher-dept-filter')?.value || '';
  const section = document.getElementById('teacher-section-filter')?.value || '';

  if (state.teacherFiles.length === 0 && state.teacherCameraPhotos.length === 0) {
    showToast('Please upload or snap at least one classroom photo.', 'error');
    return;
  }

  const formData = new FormData();
  formData.append('threshold', threshold);
  formData.append('session_mode', mode);
  if (dept) {
    formData.append('department', dept);
  }
  if (section) {
    formData.append('section', section);
  }

  if (mode === 'new') {
    formData.append('session_title', newTitle || `Lecture on ${new Date().toLocaleDateString()}`);
  } else {
    if (!existingId) {
      showToast('Please select an existing session or switch to New Session.', 'error');
      return;
    }
    formData.append('session_id', existingId);
  }

  state.teacherFiles.forEach(file => {
    formData.append('photos', file);
  });

  state.teacherCameraPhotos.forEach(b64 => {
    formData.append('camera_photos', b64);
  });

  const btn = document.getElementById('btn-run-attendance');
  const originalHtml = btn.innerHTML;
  btn.innerHTML = `<span class="spinner"></span> Analyzing with InsightFace AI...`;
  btn.disabled = true;

  try {
    const res = await fetch(apiUrl('/api/attendance/recognize'), {
      method: 'POST',
      body: formData,
    });

    if (!res.ok) {
      if (res.status === 413) {
        showToast('Uploaded photos exceed 64 MB size limit. Please upload fewer or compressed images.', 'error');
        return;
      }
      try {
        const errJson = await res.json();
        showToast(errJson.error || `Server error (${res.status})`, 'error');
      } catch {
        showToast(`Server returned error HTTP ${res.status}`, 'error');
      }
      return;
    }

    const result = await res.json();
    state.currentTeacherResult = result;
    renderTeacherResults(result);
    showToast(`Marked ${result.present_count} student(s) Present for Session #${result.session_id}!`, 'success');
    await fetchSessions();
    await fetchSystemStatus();

  } catch (err) {
    showToast(`Error processing photos: ${err.message}`, 'error');
  } finally {
    btn.innerHTML = originalHtml;
    btn.disabled = false;
  }
}

function renderTeacherResults(result) {
  const container = document.getElementById('teacher-results-container');
  if (!container) return;
  container.style.display = 'block';

  // Multi-Photo Fusion Notice Banner
  const mergeBanner = document.getElementById('multi-photo-merge-banner');
  const mergeDesc = document.getElementById('multi-photo-merge-desc');
  if (mergeBanner) {
    if (result.photos_count && result.photos_count > 1) {
      mergeBanner.style.display = 'flex';
      const namesList = (result.present_names && result.present_names.length > 0)
        ? ` (${result.present_names.map(escapeHtml).join(', ')})`
        : '';
      if (mergeDesc) {
        mergeDesc.textContent = `Combined ${result.photos_count} classroom photos (${result.total_raw_detections} total face scans). Duplicate student sightings across camera angles were merged. Exactly ${result.present_count} different student(s) marked present${namesList} with zero double-counting!`;
      }
    } else {
      mergeBanner.style.display = 'none';
    }
  }

  // Metrics
  const totalRoster = result.total_students !== undefined ? result.total_students : (result.present_count + result.absent_count);
  const totalAttended = result.total_attended_students !== undefined ? result.total_attended_students : (result.present_count || 0);

  if (document.getElementById('res-total-faces')) {
    document.getElementById('res-total-faces').textContent = totalRoster;
  }
  if (document.getElementById('res-present-count')) {
    document.getElementById('res-present-count').textContent = totalAttended;
  }
  if (document.getElementById('res-absent-count')) {
    document.getElementById('res-absent-count').textContent = result.absent_count;
  }
  if (document.getElementById('res-attendance-rate')) {
    document.getElementById('res-attendance-rate').textContent = `${result.attendance_rate}%`;
  }

  const scansSummary = document.getElementById('res-scans-summary');
  if (scansSummary) {
    if (result.photos_count && result.photos_count > 1) {
      scansSummary.textContent = `${totalAttended} attended of ${totalRoster} enrolled in class (${result.total_raw_detections} scans across ${result.photos_count} photos)`;
    } else {
      scansSummary.textContent = `${totalAttended} attended of ${totalRoster} enrolled in class`;
    }
  }

  // Annotated Images Gallery
  const imgGallery = document.getElementById('annotated-images-gallery');
  imgGallery.innerHTML = '';
  result.annotated_cards.forEach(card => {
    const cardDiv = document.createElement('div');
    cardDiv.className = 'annotated-card';
    cardDiv.innerHTML = `
      <div class="annotated-header">
        <strong>📷 ${escapeHtml(card.filename)}</strong>
        <span class="badge badge-synced">${card.faces_detected} detected (${card.faces_matched} recognized)</span>
      </div>
      <div class="annotated-img-wrap">
        <img src="${card.image_data}" alt="AI Annotated Class Photo" />
      </div>
    `;
    imgGallery.appendChild(cardDiv);
  });

  // Unknown Faces Crops
  const unknownSection = document.getElementById('unknown-faces-section');
  const unknownGrid = document.getElementById('unknown-faces-grid');
  unknownGrid.innerHTML = '';

  if (result.unknown_crops && result.unknown_crops.length > 0) {
    unknownSection.style.display = 'block';
    document.getElementById('unknown-count-label').textContent = result.unknown_crops.length;
    result.unknown_crops.forEach((b64, idx) => {
      const chip = document.createElement('div');
      chip.className = 'unknown-chip';
      chip.innerHTML = `
        <img src="${b64}" alt="Unrecognized Face" />
        <span>Face #${idx + 1}</span>
      `;
      unknownGrid.appendChild(chip);
    });
  } else {
    unknownSection.style.display = 'none';
  }

  // Roster Table with Override Action
  renderRosterTable(result.roster, 'teacher-roster-tbody', result.session_id);

  // Push to ERP Button listener
  const erpBtn = document.getElementById('btn-push-session-erp');
  erpBtn.onclick = () => pushCurrentSessionToErp(result.session_id);

  // Download CSV Button listener
  const csvBtn = document.getElementById('btn-download-session-csv');
  csvBtn.onclick = () => downloadSessionCsv(result.session_id);

  // Scroll to results
  container.scrollIntoView({ behavior: 'smooth' });
}

function renderRosterTable(roster, tbodyId, sessionId) {
  const tbody = document.getElementById(tbodyId);
  if (!tbody) return;
  tbody.innerHTML = '';

  if (!roster || roster.length === 0) {
    tbody.innerHTML = `<tr><td colspan="10" style="text-align:center; color:var(--text-muted);">No student records found</td></tr>`;
    return;
  }

  roster.forEach(r => {
    const isPresent = r.status === 'present';
    let photoBadgeHtml = '<span style="color:var(--text-muted);">—</span>';
    if (isPresent) {
      if (r.photo_sources && r.photo_sources.length > 0) {
        if (r.photo_sources.length > 1) {
          photoBadgeHtml = `<span class="badge" style="background:rgba(16,185,129,0.18); color:#34d399; border:1px solid rgba(16,185,129,0.35);" title="Seen in: ${r.photo_sources.map(escapeHtml).join(', ')}">📸 In ${r.photo_sources.length} photos</span>`;
        } else {
          const pName = r.photo_sources[0];
          const shortName = pName.length > 14 ? pName.substring(0, 11) + '...' : pName;
          photoBadgeHtml = `<span class="badge" style="background:rgba(59,130,246,0.15); color:#93c5fd; border:1px solid rgba(59,130,246,0.3);" title="${escapeHtml(pName)}">📸 ${escapeHtml(shortName)}</span>`;
        }
      } else {
        photoBadgeHtml = `<span class="badge" style="background:rgba(139,92,246,0.15); color:#c4b5fd; border:1px solid rgba(139,92,246,0.3);">✍️ Verified</span>`;
      }
    }

    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><strong>${escapeHtml(r.student_code)}</strong></td>
      <td>${escapeHtml(r.name)}</td>
      <td>${escapeHtml(r.department || '—')}</td>
      <td>
        ${r.section ? `<span class="badge" style="background:rgba(99,102,241,0.18); color:#a5b4fc; border:1px solid rgba(99,102,241,0.35); font-weight:700;">Section ${escapeHtml(r.section)}</span>` : '<span style="color:var(--text-muted);">—</span>'}
      </td>
      <td>
        <span class="badge ${isPresent ? 'badge-present' : 'badge-absent'}">
          ${isPresent ? '✅ Present' : '❌ Absent'}
        </span>
      </td>
      <td>${r.similarity !== null && r.similarity !== undefined ? Number(r.similarity).toFixed(2) : '—'}</td>
      <td>${photoBadgeHtml}</td>
      <td>
        <span class="badge ${r.erp_synced ? 'badge-synced' : 'badge-pending'}">
          ${r.erp_synced ? 'Synced' : 'Pending'}
        </span>
      </td>
      <td>${formatLocalTime(r.marked_at)}</td>
      <td>
        ${sessionId ? `
          <button class="btn ${isPresent ? 'btn-ruby' : 'btn-emerald'} btn-sm"
                  onclick="toggleStudentAttendance(${sessionId}, ${r.id}, '${isPresent ? 'absent' : 'present'}')">
            ${isPresent ? 'Mark Absent' : 'Mark Present'}
          </button>
        ` : '—'}
      </td>
    `;
    tbody.appendChild(tr);
  });
}

window.toggleStudentAttendance = async function(sessionId, studentId, targetStatus) {
  const dept = document.getElementById('teacher-dept-filter')?.value || '';
  try {
    const res = await fetch(apiUrl(`/api/sessions/${sessionId}/override`), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ student_id: studentId, status: targetStatus, department: dept }),
    });
    const data = await res.json();
    if (data.ok) {
      showToast(data.message, 'success');
      const presentCount = data.total_attended_students !== undefined ? data.total_attended_students : data.present_count;
      const totalCount = data.total_students !== undefined ? data.total_students : (presentCount + data.absent_count);

      if (document.getElementById('res-present-count')) {
        document.getElementById('res-present-count').textContent = presentCount;
      }
      if (document.getElementById('res-absent-count')) {
        document.getElementById('res-absent-count').textContent = data.absent_count;
      }
      if (document.getElementById('res-attendance-rate')) {
        document.getElementById('res-attendance-rate').textContent = `${data.attendance_rate}%`;
      }
      if (document.getElementById('res-total-faces')) {
        document.getElementById('res-total-faces').textContent = totalCount;
      }
      const scansSummary = document.getElementById('res-scans-summary');
      if (scansSummary) {
        scansSummary.textContent = `${presentCount} attended of ${totalCount} enrolled in class`;
      }
      renderRosterTable(data.roster, 'teacher-roster-tbody', sessionId);
      await fetchSessions();
    } else {
      showToast(data.error || 'Failed to update attendance.', 'error');
    }
  } catch (err) {
    showToast(`Override error: ${err.message}`, 'error');
  }
};

export async function pushCurrentSessionToErp(sessionId) {
  const btn = document.getElementById('btn-push-session-erp');
  if (btn) btn.disabled = true;

  try {
    const res = await fetch(apiUrl(`/api/sessions/${sessionId}/push_erp`), { method: 'POST' });
    const data = await res.json();
    if (data.ok) {
      showToast(data.message || 'Pushed attendance to University ERP!', 'success');
      // Refresh roster
      const rosterRes = await fetch(apiUrl(`/api/sessions/${sessionId}/roster`));
      const rosterData = await rosterRes.json();
      if (rosterData.ok) {
        renderRosterTable(rosterData.roster, 'teacher-roster-tbody');
      }
    } else {
      showToast(`ERP Push Failed: ${data.message}`, 'error');
    }
  } catch (err) {
    showToast(`Error communicating with ERP: ${err.message}`, 'error');
  } finally {
    if (btn) btn.disabled = false;
  }
}

export function downloadSessionCsv(sessionId) {
  window.location.href = apiUrl(`/api/sessions/${sessionId}/csv`);
}

// --------------------------------------------------------------------------
// Handle Teacher Branch selection
window.handleTeacherBranchChange = function() {
  const sel = document.getElementById('student-dept');
  const otherGrp = document.getElementById('teacher-other-branch-group');
  const otherInp = document.getElementById('student-other-dept');
  if (sel && sel.value === 'OTHER') {
    if (otherGrp) otherGrp.style.display = 'block';
    if (otherInp) otherInp.focus();
  } else {
    if (otherGrp) otherGrp.style.display = 'none';
    if (otherInp) otherInp.value = '';
  }
};

// Feature 2: Student Self-Enrollment
// --------------------------------------------------------------------------
export async function submitStudentEnrollment() {
  const code = document.getElementById('student-roll').value.trim();
  const name = document.getElementById('student-name').value.trim();
  const branchSel = document.getElementById('student-dept') ? document.getElementById('student-dept').value : '';
  let dept = branchSel;
  if (branchSel === 'OTHER') {
    dept = document.getElementById('student-other-dept') ? document.getElementById('student-other-dept').value.trim() : '';
    if (!dept) {
      showToast('Please enter the department / branch name.', 'error');
      document.getElementById('student-other-dept')?.focus();
      return;
    }
  }
  const section = document.getElementById('student-section') ? document.getElementById('student-section').value.trim() : '';
  const email = document.getElementById('student-email').value.trim();

  if (!code || !name) {
    showToast('GR Number and Full Name are required.', 'error');
    return;
  }

  if (!dept) {
    showToast('Please select a Branch / Department.', 'error');
    document.getElementById('student-dept')?.focus();
    return;
  }

  if (!section) {
    showToast('Please select a Section (from Section A to Z).', 'error');
    document.getElementById('student-section')?.focus();
    return;
  }

  if (state.studentFiles.length === 0 && !state.studentCameraPhoto) {
    showToast('Please take a camera snapshot or upload at least one photo.', 'error');
    return;
  }

  const formData = new FormData();
  formData.append('student_code', code);
  formData.append('gr_number', code);
  formData.append('name', name);
  formData.append('department', dept);
  formData.append('branch', dept);
  formData.append('section', section);
  formData.append('email', email);

  state.studentFiles.forEach(file => {
    formData.append('photos', file);
  });

  if (state.studentCameraPhoto) {
    formData.append('camera_photos', state.studentCameraPhoto);
  }

  const btn = document.getElementById('btn-submit-enroll');
  const originalHtml = btn.innerHTML;
  btn.innerHTML = `<span class="spinner"></span> Extracting 512-d Face Embeddings...`;
  btn.disabled = true;

  try {
    const res = await fetch(apiUrl('/api/students/enroll'), {
      method: 'POST',
      body: formData,
    });
    const data = await res.json();

    if (!data.ok) {
      showToast(data.error || 'Enrollment failed.', 'error');
      if (data.details && data.details.length) {
        showToast(data.details.join(' | '), 'warning');
      }
      return;
    }

    showToast(data.message, 'success');

    // Show preview crops
    const previewContainer = document.getElementById('enrollment-crop-preview');
    const previewGrid = document.getElementById('enrollment-crop-grid');
    previewGrid.innerHTML = '';

    if (data.preview_crops && data.preview_crops.length > 0) {
      previewContainer.style.display = 'block';
      data.preview_crops.forEach((crop, idx) => {
        const item = document.createElement('div');
        item.className = 'unknown-chip';
        item.style.borderColor = 'var(--emerald)';
        item.innerHTML = `
          <img src="${crop}" alt="Registered Face" />
          <span style="color:var(--emerald);">Vector #${idx + 1} (512-d)</span>
        `;
        previewGrid.appendChild(item);
      });
    }

    // Reset inputs
    state.studentFiles = [];
    state.studentCameraPhoto = null;
    renderStudentFileChips();
    document.getElementById('student-roll').value = '';
    document.getElementById('student-name').value = '';
    if (document.getElementById('student-dept')) document.getElementById('student-dept').value = '';
    if (document.getElementById('teacher-other-branch-group')) document.getElementById('teacher-other-branch-group').style.display = 'none';
    if (document.getElementById('student-other-dept')) document.getElementById('student-other-dept').value = '';
    if (document.getElementById('student-section')) document.getElementById('student-section').value = '';
    document.getElementById('student-email').value = '';
    document.getElementById('selfie-preview-chip').style.display = 'none';

    await fetchStudents();
    await fetchSystemStatus();

  } catch (err) {
    showToast(`Enrollment error: ${err.message}`, 'error');
  } finally {
    btn.innerHTML = originalHtml;
    btn.disabled = false;
  }
}

// --------------------------------------------------------------------------
// Feature 2B: Public Student Portal Link & QR Modal
// --------------------------------------------------------------------------
export async function fetchNetworkInfo(retries = 6) {
  try {
    const res = await fetch(apiUrl('/api/network-info'));
    const data = await res.json();
    if (data.ok) {
      state.networkInfo = data;

      // Permanent global static URL
      const globalUrl = data.global_url || 'https://harddisk-calcium-petite.ngrok-free.dev';
      const shareUrl = `${globalUrl}/student`;

      state.studentPortalUrl = shareUrl;

      const liveUrlElem = document.getElementById('live-share-url');
      if (liveUrlElem) liveUrlElem.textContent = shareUrl;

      const modalUrlElem = document.getElementById('qr-modal-url');
      if (modalUrlElem) modalUrlElem.textContent = shareUrl;

      const qrImg = document.getElementById('qr-modal-img');
      if (qrImg) {
        qrImg.src = `https://api.qrserver.com/v1/create-qr-code/?size=260x260&data=${encodeURIComponent(shareUrl)}`;
      }

      // Update badge if global
      const globalBadge = document.getElementById('global-link-indicator');
      if (globalBadge) {
        globalBadge.innerHTML = '🌐 Permanent Link Active';
        globalBadge.style.color = '#34d399';
        globalBadge.style.background = 'rgba(16, 185, 129, 0.2)';
        globalBadge.style.borderColor = 'rgba(16, 185, 129, 0.4)';
      }
    }
  } catch (err) {
    console.warn('Network info error:', err);
    state.studentPortalUrl = 'https://harddisk-calcium-petite.ngrok-free.dev/student';
    const liveUrlElem = document.getElementById('live-share-url');
    if (liveUrlElem) liveUrlElem.textContent = state.studentPortalUrl;
    const modalUrlElem = document.getElementById('qr-modal-url');
    if (modalUrlElem) modalUrlElem.textContent = state.studentPortalUrl;
  }
}

export function openStudentShareModal() {
  const overlay = document.getElementById('qr-modal-overlay');
  if (!overlay) return;
  const url = state.studentPortalUrl || 'https://harddisk-calcium-petite.ngrok-free.dev/student';

  const modalUrlElem = document.getElementById('qr-modal-url');
  if (modalUrlElem) modalUrlElem.textContent = url;

  const qrImg = document.getElementById('qr-modal-img');
  if (qrImg) {
    qrImg.src = `https://api.qrserver.com/v1/create-qr-code/?size=260x260&data=${encodeURIComponent(url)}`;
  }

  overlay.style.display = 'flex';
}

export function openStudentPortalTab() {
  const url = state.studentPortalUrl || 'https://harddisk-calcium-petite.ngrok-free.dev/student';
  window.open(url, '_blank');
}

export function closeStudentShareModal(e) {
  const overlay = document.getElementById('qr-modal-overlay');
  if (overlay) overlay.style.display = 'none';
}

export function copyStudentLink() {
  const url = state.studentPortalUrl || document.getElementById('live-share-url')?.textContent || `${window.location.origin}/student`;
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(url).then(() => {
      showToast('Student Portal Link copied to clipboard! Share it with your students.', 'success');
    }).catch(() => {
      fallbackCopy(url);
    });
  } else {
    fallbackCopy(url);
  }
}

export function shareStudentLinkWhatsApp() {
  const url = state.studentPortalUrl || document.getElementById('live-share-url')?.textContent || `${window.location.origin}/student`;
  const text = `🎓 *FaceAttend Student Registration Portal*\nPlease open this link on your smartphone to submit your photo and attendance details:\n👉 ${url}`;
  window.open(`https://api.whatsapp.com/send?text=${encodeURIComponent(text)}`, '_blank');
}

export function copyTeacherDashboardLink() {
  const teacherUrl = (state.networkInfo && state.networkInfo.global_url) ? state.networkInfo.global_url : window.location.origin;
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(teacherUrl).then(() => {
      showToast('Teacher Dashboard Link copied! Send this link to other teachers.', 'success');
    }).catch(() => {
      fallbackCopy(teacherUrl);
    });
  } else {
    fallbackCopy(teacherUrl);
  }
}

export function downloadQrCode() {
  const qrImg = document.getElementById('qr-modal-img');
  if (!qrImg || !qrImg.src) return;
  const a = document.createElement('a');
  a.href = qrImg.src;
  a.download = 'faceattend-student-portal-qr.png';
  a.target = '_blank';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  showToast('QR Code download started!', 'success');
}

function fallbackCopy(text) {
  const textArea = document.createElement('textarea');
  textArea.value = text;
  document.body.appendChild(textArea);
  textArea.select();
  document.execCommand('copy');
  document.body.removeChild(textArea);
  showToast('Link copied to clipboard!', 'success');
}

// --------------------------------------------------------------------------
// Feature 3: University ERP SaaS Integration
// --------------------------------------------------------------------------
async function fetchErpConfig() {
  try {
    const res = await fetch(apiUrl('/api/erp/config'));
    const data = await res.json();
    if (data.ok) {
      state.erpConfig = data;
      document.getElementById('erp-api-url').value = data.ERP_API_URL || '';
      document.getElementById('erp-api-key').value = data.ERP_API_KEY || '';
      document.getElementById('erp-auth-header').value = data.ERP_AUTH_HEADER || 'Authorization';
      document.getElementById('erp-auth-scheme').value = data.ERP_AUTH_SCHEME || 'Bearer';
      document.getElementById('erp-mock-mode').checked = !!data.ERP_MOCK_MODE;
    }
  } catch (err) {
    console.error('ERP config fetch error:', err);
  }
}

export async function saveErpConfig() {
  const url = document.getElementById('erp-api-url').value.trim();
  const key = document.getElementById('erp-api-key').value.trim();
  const header = document.getElementById('erp-auth-header').value;
  const scheme = document.getElementById('erp-auth-scheme').value;
  const mock = document.getElementById('erp-mock-mode').checked;

  try {
    const res = await fetch(apiUrl('/api/erp/config'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        api_url: url,
        api_key: key,
        auth_header: header,
        auth_scheme: scheme,
        mock_mode: mock,
      }),
    });
    const data = await res.json();
    if (data.ok) {
      showToast('ERP Configuration saved to SQL database!', 'success');
      await fetchSystemStatus();
    } else {
      showToast(data.error || 'Failed to save configuration.', 'error');
    }
  } catch (err) {
    showToast(`Error saving ERP config: ${err.message}`, 'error');
  }
}

export async function testErpConnection() {
  const url = document.getElementById('erp-api-url').value.trim();
  const key = document.getElementById('erp-api-key').value.trim();
  const header = document.getElementById('erp-auth-header').value;
  const scheme = document.getElementById('erp-auth-scheme').value;
  const mock = document.getElementById('erp-mock-mode').checked;

  const btn = document.getElementById('btn-test-erp');
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span> Testing Connection...`;

  try {
    const res = await fetch(apiUrl('/api/erp/test'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        api_url: url,
        api_key: key,
        auth_header: header,
        auth_scheme: scheme,
        mock_mode: mock,
      }),
    });
    const data = await res.json();
    if (data.ok) {
      showToast(`Connection Successful: ${data.message}`, 'success');
    } else {
      showToast(`ERP Test Failed: ${data.message}`, 'error');
    }
  } catch (err) {
    showToast(`Connection error: ${err.message}`, 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = `🔍 Test ERP Connection`;
  }
}

export async function fetchStudentsFromErp() {
  const btn = document.getElementById('btn-fetch-erp');
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span> Syncing Student Roster...`;

  try {
    const res = await fetch(apiUrl('/api/erp/fetch'), { method: 'POST' });
    const data = await res.json();
    if (data.ok) {
      showToast(`Roster Synced: ${data.message}`, 'success');
      await fetchStudents();
      await fetchSystemStatus();
    } else {
      showToast(`Sync Failed: ${data.message}`, 'error');
    }
  } catch (err) {
    showToast(`Sync error: ${err.message}`, 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = `📥 Fetch Students from ERP`;
  }
}

// --------------------------------------------------------------------------
// Feature 4: Students & SQL Database Management
// --------------------------------------------------------------------------
async function fetchStudents() {
  const query = document.getElementById('db-search-input')?.value.trim() || '';
  const filter = document.getElementById('db-filter-select')?.value || 'all';
  const section = document.getElementById('db-section-filter')?.value || '';
  const dept = document.getElementById('db-dept-filter')?.value || '';

  try {
    const url = `/api/students?q=${encodeURIComponent(query)}&filter=${filter}&section=${encodeURIComponent(section)}&department=${encodeURIComponent(dept)}`;
    const res = await fetch(apiUrl(url));
    const data = await res.json();
    if (data.ok) {
      state.students = data.students;
      populateDepartmentFilters();
      if (state.viewMode === 'grouped') {
        fetchGroupedStudents();
      } else {
        renderStudentsTable(data.students);
      }
      if (data.metrics) {
        document.getElementById('db-total-count').textContent = data.metrics.total_students;
        document.getElementById('db-with-faces-count').textContent = data.metrics.with_photos;
        document.getElementById('db-missing-faces-count').textContent = data.metrics.missing_photos;
      }
    }
  } catch (err) {
    console.error('Students fetch error:', err);
  }
}

function populateDepartmentFilters() {
  const depts = new Set();
  state.students.forEach(s => {
    const d = (s.department || s.branch || '').trim();
    if (d) depts.add(d);
  });
  const sortedDepts = Array.from(depts).sort();

  // 1. Teacher dept filter dropdown
  const teacherSel = document.getElementById('teacher-dept-filter');
  if (teacherSel) {
    const curVal = teacherSel.value;
    teacherSel.innerHTML = '<option value="">All Departments (Entire University)</option>';
    sortedDepts.forEach(d => {
      const opt = document.createElement('option');
      opt.value = d;
      opt.textContent = d;
      if (d === curVal) opt.selected = true;
      teacherSel.appendChild(opt);
    });
  }

  // 2. Database tab course/dept filter dropdown
  const dbDeptSel = document.getElementById('db-dept-filter');
  if (dbDeptSel) {
    const curDbVal = dbDeptSel.value;
    dbDeptSel.innerHTML = '<option value="">All Courses / Departments</option>';
    sortedDepts.forEach(d => {
      const opt = document.createElement('option');
      opt.value = d;
      opt.textContent = d;
      if (d === curDbVal) opt.selected = true;
      dbDeptSel.appendChild(opt);
    });
  }
}

function renderStudentsTable(students) {
  const tbody = document.getElementById('db-students-tbody');
  if (!tbody) return;
  tbody.innerHTML = '';

  if (!students || students.length === 0) {
    tbody.innerHTML = `<tr><td colspan="9" style="text-align:center; color:var(--text-muted); padding:24px;">No student records found matching the criteria</td></tr>`;
    return;
  }

  students.forEach(s => {
    const hasPhotos = s.num_encodings > 0;
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>#${s.id}</td>
      <td><strong style="color:var(--cyan); font-family:var(--font-mono);">${escapeHtml(s.gr_number || s.student_code)}</strong></td>
      <td><strong>${escapeHtml(s.name)}</strong></td>
      <td>${escapeHtml(s.department || s.branch || '—')}</td>
      <td>
        ${s.section ? `<span class="badge" style="background:rgba(99,102,241,0.18); color:#a5b4fc; border:1px solid rgba(99,102,241,0.35); font-weight:700;">Section ${escapeHtml(s.section)}</span>` : '<span style="color:var(--text-muted);">—</span>'}
      </td>
      <td>${escapeHtml(s.email || '—')}</td>
      <td>
        <span class="badge ${hasPhotos ? 'badge-present' : 'badge-absent'}" style="${hasPhotos ? 'cursor:pointer;' : ''}" title="${hasPhotos ? 'Click to view photo' : 'No photo'}" onclick="${hasPhotos ? `openPhotoModal(${s.id})` : ''}">
          ${hasPhotos ? `✅ ${s.num_encodings} photo(s)` : '❌ None'}
        </span>
      </td>
      <td>
        <span class="badge ${s.erp_synced_at ? 'badge-synced' : 'badge-pending'}">
          ${s.erp_synced_at ? 'Yes' : 'No'}
        </span>
      </td>
      <td>
        <button class="btn btn-ruby btn-sm" onclick="deleteStudent(${s.id})">Delete</button>
      </td>
    `;
    tbody.appendChild(tr);
  });
}

async function fetchGroupedStudents() {
  const query = document.getElementById('db-search-input')?.value.trim() || '';
  const filter = document.getElementById('db-filter-select')?.value || 'all';
  const section = document.getElementById('db-section-filter')?.value || '';
  const dept = document.getElementById('db-dept-filter')?.value || '';

  const container = document.getElementById('db-grouped-view');
  if (!container) return;
  container.innerHTML = '<div style="text-align:center; padding:30px; color:var(--text-muted);"><span class="spinner"></span> Loading Course &amp; Section hierarchy...</div>';

  try {
    const url = `/api/students/grouped?q=${encodeURIComponent(query)}&filter=${filter}&section=${encodeURIComponent(section)}&department=${encodeURIComponent(dept)}`;
    const res = await fetch(apiUrl(url));
    const data = await res.json();
    if (data.ok) {
      renderGroupedStudentsView(data);
    } else {
      container.innerHTML = `<p style="text-align:center; color:var(--ruby); padding:20px;">Failed to load grouped view: ${escapeHtml(data.error)}</p>`;
    }
  } catch (err) {
    container.innerHTML = `<p style="text-align:center; color:var(--ruby); padding:20px;">Error: ${escapeHtml(err.message)}</p>`;
  }
}

function renderGroupedStudentsView(data) {
  const container = document.getElementById('db-grouped-view');
  if (!container) return;
  container.innerHTML = '';

  if (!data.courses || data.courses.length === 0) {
    container.innerHTML = `
      <div class="glass-panel" style="text-align:center; padding:40px; color:var(--text-muted);">
        <div style="font-size:2.2rem; margin-bottom:10px;">📭</div>
        <p>No student records found in the database matching this search or filter.</p>
      </div>`;
    return;
  }

  data.courses.forEach(c => {
    const courseCard = document.createElement('div');
    courseCard.className = 'course-group-card';

    let sectionsHtml = '';
    c.sections.forEach(sec => {
      let studentsHtml = '';
      sec.students.forEach(st => {
        const hasPhotos = st.num_encodings > 0;
        studentsHtml += `
          <div class="student-profile-card">
            <img src="${apiUrl(`/api/students/${st.id}/photo`)}"
                 alt="${escapeHtml(st.name)}"
                 class="student-card-photo"
                 loading="lazy"
                 title="Click to view photo of ${escapeHtml(st.name)}"
                 onclick="openPhotoModal(${st.id})" />
            <div class="student-card-info">
              <div class="student-card-name" title="${escapeHtml(st.name)}">${escapeHtml(st.name)}</div>
              <div class="student-card-gr">GR: ${escapeHtml(st.gr_number || st.student_code)}</div>
              <div style="display:flex; gap:6px; align-items:center; flex-wrap:wrap; margin-top:3px;">
                <span class="badge ${hasPhotos ? 'badge-present' : 'badge-absent'}" style="font-size:0.7rem; padding:2px 6px;">
                  ${hasPhotos ? `✅ Enrolled (${st.num_encodings})` : '❌ Missing Photo'}
                </span>
              </div>
            </div>
          </div>
        `;
      });

      sectionsHtml += `
        <div class="section-group-panel">
          <div class="section-header-bar">
            <div style="display:flex; align-items:center; gap:10px;">
              <span class="section-pill-tag">Section ${escapeHtml(sec.section)}</span>
              <span style="font-size:0.85rem; color:var(--text-muted);">${sec.student_count} student${sec.student_count === 1 ? '' : 's'}</span>
            </div>
          </div>
          <div class="student-cards-grid">
            ${studentsHtml}
          </div>
        </div>
      `;
    });

    courseCard.innerHTML = `
      <div class="course-group-header">
        <div class="course-title">
          <span style="color:var(--cyan);">🎓</span>
          <span>${escapeHtml(c.course)}</span>
        </div>
        <div style="display:flex; gap:8px;">
          <span class="badge" style="background:rgba(0, 229, 255, 0.12); color:var(--cyan); border:1px solid rgba(0, 229, 255, 0.3);">
            ${c.sections_count} Section${c.sections_count === 1 ? '' : 's'}
          </span>
          <span class="badge" style="background:rgba(139, 92, 246, 0.12); color:#c4b5fd; border:1px solid rgba(139, 92, 246, 0.3);">
            ${c.student_count} Student${c.student_count === 1 ? '' : 's'}
          </span>
        </div>
      </div>
      ${sectionsHtml}
    `;

    container.appendChild(courseCard);
  });
}

export function switchStudentView(mode) {
  state.viewMode = mode;
  const btnTable = document.getElementById('btn-view-table');
  const btnGrouped = document.getElementById('btn-view-grouped');
  const tableView = document.getElementById('db-table-view');
  const groupedView = document.getElementById('db-grouped-view');

  if (mode === 'grouped') {
    btnGrouped?.classList.add('active');
    btnTable?.classList.remove('active');
    if (tableView) tableView.style.display = 'none';
    if (groupedView) groupedView.style.display = 'block';
    fetchGroupedStudents();
  } else {
    btnTable?.classList.add('active');
    btnGrouped?.classList.remove('active');
    if (tableView) tableView.style.display = 'block';
    if (groupedView) groupedView.style.display = 'none';
    renderStudentsTable(state.students);
  }
}
window.switchStudentView = switchStudentView;

export function openPhotoModal(studentId) {
  const s = state.students.find(st => st.id === studentId);
  if (!s) return;
  const modal = document.getElementById('student-photo-modal');
  if (!modal) return;

  document.getElementById('photo-modal-name').textContent = s.name;
  document.getElementById('photo-modal-gr').textContent = s.gr_number || s.student_code;
  document.getElementById('photo-modal-dept').textContent = s.department || s.branch || 'General';
  document.getElementById('photo-modal-section').textContent = s.section ? `Section ${s.section}` : 'Unassigned';
  document.getElementById('photo-modal-path').textContent = s.photo_path || `data/student_photos/by_gr/${s.gr_number || s.student_code}.jpg`;

  const photoUrl = apiUrl(`/api/students/${s.id}/photo`);
  const img = document.getElementById('photo-modal-img');
  if (img) img.src = photoUrl;

  const dlLink = document.getElementById('photo-modal-download-link');
  if (dlLink) {
    dlLink.href = photoUrl;
    dlLink.download = `${s.gr_number || s.student_code}_${s.name.replace(/\s+/g, '_')}.jpg`;
  }

  modal.classList.add('active');
}
window.openPhotoModal = openPhotoModal;

export function closePhotoModal(e) {
  if (e && e.target && e.target.id !== 'student-photo-modal' && !e.target.classList.contains('modal-close') && e.target.tagName !== 'BUTTON') {
    return;
  }
  const modal = document.getElementById('student-photo-modal');
  if (modal) modal.classList.remove('active');
}
window.closePhotoModal = closePhotoModal;

window.deleteStudent = async function(id) {
  const student = state.students.find(s => s.id === id);
  const displayName = student ? `${student.name} (${student.student_code})` : `#${id}`;
  if (!confirm(`Are you sure you want to permanently delete student ${displayName} and their biometric data?`)) {
    return;
  }

  try {
    const res = await fetch(apiUrl(`/api/students/${id}`), { method: 'DELETE' });
    const data = await res.json();
    if (data.ok) {
      showToast(`Student #${id} removed.`, 'success');
      await fetchStudents();
      await fetchSystemStatus();
    } else {
      showToast(data.error || 'Failed to delete student.', 'error');
    }
  } catch (err) {
    showToast(`Delete error: ${err.message}`, 'error');
  }
};

// Search & Filter event bindings
document.getElementById('db-search-input')?.addEventListener('input', () => fetchStudents());
document.getElementById('db-dept-filter')?.addEventListener('change', () => fetchStudents());
document.getElementById('db-section-filter')?.addEventListener('change', () => fetchStudents());
document.getElementById('db-filter-select')?.addEventListener('change', () => fetchStudents());

// --------------------------------------------------------------------------
// Feature 5: Sessions & Reports
// --------------------------------------------------------------------------
function renderSessionsReport() {
  const container = document.getElementById('reports-sessions-list');
  if (!container) return;
  container.innerHTML = '';

  if (state.sessions.length === 0) {
    container.innerHTML = `<p style="text-align:center; color:var(--text-muted); padding:30px;">No attendance sessions marked yet. Run one from the Teacher Attendance tab!</p>`;
    return;
  }

  state.sessions.forEach(s => {
    const isSynced = (s.erp_synced_count || 0) > 0;
    const item = document.createElement('div');
    item.className = 'glass-panel';
    item.style.marginBottom = '20px';
    item.innerHTML = `
      <div class="panel-header" style="margin-bottom:14px; padding-bottom:12px;">
        <div>
          <h4 style="font-size:1.15rem; margin-bottom:4px;">Session #${s.id} — ${escapeHtml(s.title)}</h4>
          <span style="font-size:0.82rem; color:var(--text-muted);">
            Created: ${formatLocalDate(s.created_at)} ${formatLocalTime(s.created_at)} · Total Attended Students: ${s.present_count || 0}
          </span>
        </div>
        <div style="display:flex; gap:10px; align-items:center;">
          <span class="badge ${isSynced ? 'badge-synced' : 'badge-pending'}">
            ${isSynced ? '🟢 Synced to ERP' : '⚪ Not Synced'}
          </span>
          <button class="btn btn-secondary btn-sm" onclick="pushPastSession(${s.id})">⚡ Push to ERP</button>
          <a class="btn btn-emerald btn-sm" href="${apiUrl(`/api/sessions/${s.id}/csv`)}" download>⬇️ Download CSV</a>
        </div>
      </div>
      <div id="roster-view-${s.id}">
        <button class="btn btn-secondary btn-sm" onclick="toggleSessionRoster(${s.id})">👁️ View Class Roster</button>
        <div id="roster-table-wrap-${s.id}" style="display:none; margin-top:14px;"></div>
      </div>
    `;
    container.appendChild(item);
  });
}

window.toggleSessionRoster = async function(sessionId) {
  const wrap = document.getElementById(`roster-table-wrap-${sessionId}`);
  if (!wrap) return;

  if (wrap.style.display === 'block') {
    wrap.style.display = 'none';
    return;
  }

  wrap.style.display = 'block';
  wrap.innerHTML = `<span class="spinner"></span> Loading session roster...`;

  try {
    const res = await fetch(apiUrl(`/api/sessions/${sessionId}/roster`));
    const data = await res.json();
    if (data.ok) {
      let tableHtml = `
        <div style="display:flex; gap:12px; margin-bottom:14px; flex-wrap:wrap; font-size:0.85rem;">
          <span class="badge badge-present">✅ Total Attended Students: ${data.present_count}</span>
          <span class="badge badge-absent">❌ Students Absent: ${data.absent_count}</span>
          <span class="badge" style="background:rgba(0,229,255,0.12); color:var(--cyan); border:1px solid rgba(0,229,255,0.3);">👥 Total Students in Class: ${data.total_students}</span>
          <span class="badge" style="background:rgba(139,92,246,0.12); color:#c4b5fd; border:1px solid rgba(139,92,246,0.3);">📈 Attendance Rate: ${data.attendance_rate}%</span>
        </div>
        <div class="table-container">
          <table class="cyber-table">
            <thead>
              <tr>
                <th>Roll No</th>
                <th>Name</th>
                <th>Department</th>
                <th>Section</th>
                <th>Status</th>
                <th>Confidence</th>
                <th>ERP Synced</th>
                <th>Marked Time</th>
                <th>Manual Override</th>
              </tr>
            </thead>
            <tbody>
      `;
      data.roster.forEach(r => {
        const isPresent = r.status === 'present';
        tableHtml += `
          <tr>
            <td><strong>${escapeHtml(r.student_code)}</strong></td>
            <td>${escapeHtml(r.name)}</td>
            <td>${escapeHtml(r.department || '—')}</td>
            <td>${r.section ? `<span class="badge" style="background:rgba(99,102,241,0.18); color:#a5b4fc; border:1px solid rgba(99,102,241,0.35); font-weight:700;">Section ${escapeHtml(r.section)}</span>` : '<span style="color:var(--text-muted);">—</span>'}</td>
            <td><span class="badge ${isPresent ? 'badge-present' : 'badge-absent'}">${isPresent ? '✅ Present' : '❌ Absent'}</span></td>
            <td>${r.similarity !== null ? Number(r.similarity).toFixed(2) : '—'}</td>
            <td><span class="badge ${r.erp_synced ? 'badge-synced' : 'badge-pending'}">${r.erp_synced ? 'Yes' : 'No'}</span></td>
            <td>${formatLocalTime(r.marked_at)}</td>
            <td>
              <button class="btn ${isPresent ? 'btn-ruby' : 'btn-emerald'} btn-sm"
                      onclick="toggleReportStudentAttendance(${sessionId}, ${r.id}, '${isPresent ? 'absent' : 'present'}')">
                ${isPresent ? 'Mark Absent' : 'Mark Present'}
              </button>
            </td>
          </tr>
        `;
      });
      tableHtml += `</tbody></table></div>`;
      wrap.innerHTML = tableHtml;
    } else {
      wrap.innerHTML = `<span style="color:var(--ruby);">${escapeHtml(data.error)}</span>`;
    }
  } catch (err) {
    wrap.innerHTML = `<span style="color:var(--ruby);">${escapeHtml(err.message)}</span>`;
  }
};

window.toggleReportStudentAttendance = async function(sessionId, studentId, targetStatus) {
  try {
    const res = await fetch(apiUrl(`/api/sessions/${sessionId}/override`), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ student_id: studentId, status: targetStatus }),
    });
    const data = await res.json();
    if (data.ok) {
      showToast(data.message, 'success');
      await toggleSessionRoster(sessionId); // close
      await toggleSessionRoster(sessionId); // reopen with fresh data
      await fetchSessions();
    } else {
      showToast(data.error || 'Failed to update attendance.', 'error');
    }
  } catch (err) {
    showToast(`Override error: ${err.message}`, 'error');
  }
};

window.pushPastSession = async function(sessionId) {
  try {
    const res = await fetch(apiUrl(`/api/sessions/${sessionId}/push_erp`), { method: 'POST' });
    const data = await res.json();
    if (data.ok) {
      showToast(data.message || 'Pushed to ERP!', 'success');
      await fetchSessions();
      renderSessionsReport();
    } else {
      showToast(data.message || 'Push failed.', 'error');
    }
  } catch (err) {
    showToast(`Error: ${err.message}`, 'error');
  }
};

// --------------------------------------------------------------------------
// Live Webcam Modal & Capture
// --------------------------------------------------------------------------
let cameraTargetMode = 'teacher'; // 'teacher' or 'student'

export function openCameraModal(mode = 'teacher') {
  cameraTargetMode = mode;
  const modal = document.getElementById('camera-modal');
  const video = document.getElementById('camera-video');
  const canvas = document.getElementById('camera-canvas');
  const retakeBtn = document.getElementById('camera-btn-retake');
  const snapBtn = document.getElementById('camera-btn-snap');
  const confirmBtn = document.getElementById('camera-btn-confirm');

  video.style.display = 'block';
  canvas.style.display = 'none';
  snapBtn.style.display = 'inline-flex';
  retakeBtn.style.display = 'none';
  confirmBtn.style.display = 'none';

  modal.classList.add('active');

  navigator.mediaDevices.getUserMedia({ video: { width: 1280, height: 720 } })
    .then(stream => {
      state.activeCameraStream = stream;
      video.srcObject = stream;
      video.play();
    })
    .catch(err => {
      showToast(`Cannot access webcam: ${err.message}`, 'error');
      closeCameraModal();
    });
}

export function snapCameraPhoto() {
  const video = document.getElementById('camera-video');
  const canvas = document.getElementById('camera-canvas');
  const retakeBtn = document.getElementById('camera-btn-retake');
  const snapBtn = document.getElementById('camera-btn-snap');
  const confirmBtn = document.getElementById('camera-btn-confirm');

  canvas.width = video.videoWidth || 1280;
  canvas.height = video.videoHeight || 720;
  const ctx = canvas.getContext('2d');
  ctx.drawImage(video, 0, 0, canvas.width, canvas.height);

  video.style.display = 'none';
  canvas.style.display = 'block';
  snapBtn.style.display = 'none';
  retakeBtn.style.display = 'inline-flex';
  confirmBtn.style.display = 'inline-flex';
}

export function retakeCameraPhoto() {
  const video = document.getElementById('camera-video');
  const canvas = document.getElementById('camera-canvas');
  const retakeBtn = document.getElementById('camera-btn-retake');
  const snapBtn = document.getElementById('camera-btn-snap');
  const confirmBtn = document.getElementById('camera-btn-confirm');

  video.style.display = 'block';
  canvas.style.display = 'none';
  snapBtn.style.display = 'inline-flex';
  retakeBtn.style.display = 'none';
  confirmBtn.style.display = 'none';
}

export function confirmCameraPhoto() {
  const canvas = document.getElementById('camera-canvas');
  const dataUrl = canvas.toDataURL('image/jpeg', 0.9);

  if (cameraTargetMode === 'teacher') {
    state.teacherCameraPhotos.push(dataUrl);
    renderTeacherFileChips();
    showToast('Classroom snapshot captured and queued!', 'success');
  } else {
    state.studentCameraPhoto = dataUrl;
    const previewChip = document.getElementById('selfie-preview-chip');
    const previewImg = document.getElementById('selfie-preview-img');
    if (previewChip && previewImg) {
      previewImg.src = dataUrl;
      previewChip.style.display = 'inline-flex';
    }
    showToast('Student selfie captured!', 'success');
  }

  closeCameraModal();
}

export function closeCameraModal() {
  const modal = document.getElementById('camera-modal');
  modal.classList.remove('active');

  if (state.activeCameraStream) {
    state.activeCameraStream.getTracks().forEach(track => track.stop());
    state.activeCameraStream = null;
  }
}

function initModalListeners() {
  document.getElementById('camera-btn-snap')?.addEventListener('click', snapCameraPhoto);
  document.getElementById('camera-btn-retake')?.addEventListener('click', retakeCameraPhoto);
  document.getElementById('camera-btn-confirm')?.addEventListener('click', confirmCameraPhoto);
  document.getElementById('camera-modal-close')?.addEventListener('click', closeCameraModal);

  // Close camera modal on backdrop click
  document.getElementById('camera-modal')?.addEventListener('click', (e) => {
    if (e.target.id === 'camera-modal') {
      closeCameraModal();
    }
  });

  // Close camera modal on Escape key
  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && document.getElementById('camera-modal')?.classList.contains('active')) {
      closeCameraModal();
    }
  });
}
