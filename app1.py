from flask import Flask, render_template, request, session, redirect, url_for, flash
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
import os
import firebase_admin
from firebase_admin import credentials, firestore
import pandas as pd
import random
import traceback
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, 
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    handlers=[
                        logging.FileHandler('app.log'),
                        logging.StreamHandler()
                    ])

# Initialize Flask app
app = Flask(__name__, 
    template_folder='templates', 
    static_folder='static'
)
app.secret_key = os.getenv('SECRET_KEY', 'your-secret-key-here_45678')

# Firebase credentials path
FIREBASE_CREDENTIALS_PATH = r"D:\web\webathon_timetable\firebase_credentials.json"

def initialize_firebase():
    """Initialize Firebase Admin SDK"""
    try:
        if not os.path.exists(FIREBASE_CREDENTIALS_PATH):
            raise FileNotFoundError(f"Firebase credentials file not found at {FIREBASE_CREDENTIALS_PATH}")
        if not firebase_admin._apps:
            cred = credentials.Certificate(FIREBASE_CREDENTIALS_PATH)
            firebase_admin.initialize_app(cred)
        db = firestore.client()
        logging.info("Successfully connected to Firebase")
        return db
    except Exception as e:
        logging.error(f"Firebase Connection Error: {e}")
        logging.error(traceback.format_exc())
        raise

# Initialize database connection
try:
    db = initialize_firebase()
    users_collection = db.collection('users')
except Exception as e:
    logging.critical("Could not establish Firebase connection. Application cannot start.")
    raise

# Constants for timetable generation
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
TIMINGS = ["10-11", "11-12", "12-1", "LUNCH", "2-3", "3-4"]
MAX_HOURS_PER_WEEK = 20
REQUIRED_COLUMNS = ['Department', 'Semester', 'Section', 'Subject', 
                   'Theory Classes', 'Practical Classes', 'Tutorial Classes', 'Faculty']

# Home route (Signup/Login page)
@app.route('/')
def index():
    if 'email' in session:
        return redirect(url_for('dashboard'))
    return render_template('index.html')

# Signup route
@app.route('/signup', methods=['POST'])
def signup():
    if users_collection is None:
        flash('Firebase is not properly configured.', 'danger')
        return render_template('index.html')

    name = request.form.get('name')
    email = request.form.get('email')
    password = request.form.get('password')
    confirm_password = request.form.get('confirm_password')
    role = request.form.get('role')
    department = request.form.get('department', '')
    semester = request.form.get('semester', '')
    section = request.form.get('section', '')
    subject = request.form.get('subject', '')

    if not all([name, email, password, confirm_password, role]):
        flash('All required fields must be filled.', 'danger')
        return render_template('index.html')

    if password != confirm_password:
        flash('Passwords do not match.', 'danger')
        return render_template('index.html')

    user_ref = users_collection.document(email).get()
    if user_ref.exists:
        flash('Email already exists.', 'danger')
        return render_template('index.html')

    hashed_password = generate_password_hash(password)
    user_doc = {
        'name': name,
        'email': email,
        'password': hashed_password,
        'role': role
    }

    role_fields = {
        'Program Chair': ['department'],
        'CR': ['department', 'semester', 'section'],
        'SR': ['department', 'semester', 'section', 'subject'],
        'Faculty': ['department', 'semester', 'section', 'subject'],
        'Coordinator': ['department', 'semester', 'section']
    }

    if role not in role_fields:
        flash('Invalid role selected.', 'danger')
        return render_template('index.html')

    for field in role_fields[role]:
        if locals()[field]:
            user_doc[field] = locals()[field]

    users_collection.document(email).set(user_doc)
    flash('Signup successful! Please login.', 'success')
    return render_template('index.html', show_login=True)

# Login route
@app.route('/login', methods=['POST'])
def login():
    if users_collection is None:
        flash('Firebase is not properly configured.', 'danger')
        return render_template('index.html')

    email = request.form.get('email')
    password = request.form.get('password')

    if not all([email, password]):
        flash('Email and password are required.', 'danger')
        return render_template('index.html', show_login=True)

    user_ref = users_collection.document(email).get()
    if user_ref.exists:
        user = user_ref.to_dict()
        if check_password_hash(user['password'], password):
            session['email'] = email
            session['role'] = user['role']
            session['name'] = user['name']
            flash('Login successful!', 'success')
            return redirect(url_for('dashboard'))  # Redirects to timetable dashboard
    
    flash('Invalid email or password.', 'danger')
    return render_template('index.html', show_login=True)

# Logout route
@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'success')
    return redirect(url_for('index'))

# Timetable-related routes
def validate_excel_data(df):
    missing_cols = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing_cols:
        raise ValueError(f"Missing required columns: {', '.join(missing_cols)}")
    if df.empty:
        raise ValueError("The Excel file is empty")
    if df[['Department', 'Semester', 'Section', 'Subject']].isnull().values.any():
        raise ValueError("Department, Semester, Section, and Subject cannot be empty")

def generate_timetables(df):
    try:
        df["Semester"] = df["Semester"].astype(str)
        df["Theory Classes"] = pd.to_numeric(df["Theory Classes"], errors='coerce').fillna(0).astype(int)
        df["Practical Classes"] = pd.to_numeric(df["Practical Classes"], errors='coerce').fillna(0).astype(int)
        df["Tutorial Classes"] = pd.to_numeric(df["Tutorial Classes"], errors='coerce').fillna(0).astype(int)
        
        faculty_data = {str(faculty): {"assigned_slots": 0} for faculty in df["Faculty"].unique() if pd.notna(faculty)}
        grouped = df.groupby(['Department', 'Semester', 'Section'])
        timetables = []
        
        for (dept, sem, sec), group in grouped:
            timetable = {
                "department": str(dept),
                "semester": str(sem),
                "section": str(sec),
                "days": {day: {time: None for time in TIMINGS} for day in DAYS}
            }
            
            for _, row in group.iterrows():
                subject = str(row["Subject"])
                theory_count = row["Theory Classes"]
                lab_count = row["Practical Classes"]
                tutorial_count = row["Tutorial Classes"]
                faculty = str(row["Faculty"]) if pd.notna(row["Faculty"]) else None
                
                if not faculty:
                    logging.warning(f"No faculty assigned for {subject} in {dept}, Semester {sem}, Section {sec}")
                    continue
                
                for _ in range(theory_count):
                    for _ in range(50):
                        day = random.choice(DAYS)
                        time = random.choice([t for t in TIMINGS if t != "LUNCH"])
                        if timetable["days"][day][time] is None and faculty_data[faculty]["assigned_slots"] < MAX_HOURS_PER_WEEK:
                            timetable["days"][day][time] = f"{subject} ({faculty})"
                            faculty_data[faculty]["assigned_slots"] += 1
                            break
                
                for _ in range(lab_count):
                    for _ in range(50):
                        day = random.choice(DAYS)
                        time_idx = random.randint(0, len(TIMINGS) - 3)
                        time1, time2 = TIMINGS[time_idx], TIMINGS[time_idx + 1]
                        if (time1 != "LUNCH" and time2 != "LUNCH" and
                            timetable["days"][day][time1] is None and 
                            timetable["days"][day][time2] is None and
                            faculty_data[faculty]["assigned_slots"] < MAX_HOURS_PER_WEEK - 1):
                            timetable["days"][day][time1] = f"{subject} Lab ({faculty})"
                            timetable["days"][day][time2] = f"{subject} Lab ({faculty})"
                            faculty_data[faculty]["assigned_slots"] += 2
                            break
                
                for _ in range(tutorial_count):
                    for _ in range(50):
                        day = random.choice(DAYS)
                        time = random.choice([t for t in TIMINGS if t != "LUNCH"])
                        if timetable["days"][day][time] is None and faculty_data[faculty]["assigned_slots"] < MAX_HOURS_PER_WEEK:
                            timetable["days"][day][time] = f"{subject} Tutorial ({faculty})"
                            faculty_data[faculty]["assigned_slots"] += 1
                            break
            
            timetables.append(timetable)
        
        return timetables
    except Exception as e:
        logging.error(f"Error in timetable generation: {str(e)}")
        raise

def store_timetables(timetables):
    try:
        for timetable in timetables:
            dept = timetable["department"]
            sem = timetable["semester"]
            sec = timetable["section"]
            section_ref = db.collection(f"{dept}_{sem}_sections").document(sec)
            section_ref.set({
                "department": dept,
                "semester": sem,
                "section": sec,
                "timetable": timetable["days"]
            })
        return True
    except Exception as e:
        logging.error(f"Error storing timetables: {e}")
        return False

@app.route("/upload", methods=["GET", "POST"])
def upload_subjects():
    if 'email' not in session:
        flash('Please login first.', 'danger')
        return redirect(url_for('index'))
    
    if request.method == "POST":
        try:
            if "file" not in request.files:
                return render_template("upload.html", message="No file uploaded", error=True)
            file = request.files["file"]
            if file.filename == "":
                return render_template("upload.html", message="No file selected", error=True)
            if not file.filename.lower().endswith(('.xlsx', '.xls')):
                return render_template("upload.html", message="Invalid file type. Please upload an Excel file.", error=True)

            os.makedirs("uploads", exist_ok=True)
            file_path = os.path.join("uploads", file.filename)
            file.save(file_path)

            df = pd.read_excel(file_path)
            validate_excel_data(df)

            subjects_data = df.to_dict('records')
            subjects_collection = db.collection('subjects')
            for subject in subjects_data:
                subjects_collection.add(subject)

            timetables = generate_timetables(df)
            if not timetables:
                return render_template("upload.html", message="No timetables were generated", error=True)

            if not store_timetables(timetables):
                return render_template("upload.html", message="Error storing timetables in database", error=True)

            return render_template("upload.html", message=f"Successfully generated {len(timetables)} timetables!", success=True)
        except Exception as e:
            logging.error(f"Unexpected error in upload: {e}")
            return render_template("upload.html", message=f"An unexpected error occurred: {str(e)}", error=True)

    return render_template("upload.html", message=None)

@app.route("/view_timetables")
def view_timetables():
    if 'email' not in session:
        flash('Please login first.', 'danger')
        return redirect(url_for('index'))
    
    try:
        timetables = []
        departments = set()
        semesters = set()
        sections = set()
        
        collections = [col for col in db.collections() if '_sections' in col.id]
        for collection in collections:
            collection_timetables = collection.stream()
            for doc in collection_timetables:
                timetable_data = doc.to_dict()
                timetables.append(timetable_data)
                departments.add(timetable_data['department'])
                semesters.add(timetable_data['semester'])
                sections.add(timetable_data['section'])
        
        selected_department = request.args.get("department", "")
        selected_semester = request.args.get("semester", "")
        selected_section = request.args.get("section", "")
        
        filtered_timetables = [t for t in timetables if
                               (not selected_department or t['department'] == selected_department) and
                               (not selected_semester or t['semester'] == selected_semester) and
                               (not selected_section or t['section'] == selected_section)]
        
        return render_template("view_timetables.html", 
                             timetables=filtered_timetables,
                             departments=sorted(departments),
                             semesters=sorted(semesters),
                             sections=sorted(sections),
                             selected_department=selected_department,
                             selected_semester=selected_semester,
                             selected_section=selected_section)
    except Exception as e:
        logging.error(f"Error loading timetables: {e}")
        return render_template("view_timetables.html", timetables=[], error_message=f"Error loading timetables: {str(e)}")

@app.route("/dashboard")
def dashboard():
    if 'email' not in session:
        flash('Please login first.', 'danger')
        return redirect(url_for('index'))
    
    try:
        department = request.args.get("department", "")
        semester = request.args.get("semester", "")
        section = request.args.get("section", "")
        
        # Static lists for now; could be dynamically fetched from Firestore
        departments = ["CSE", "CS_DS"]
        semesters = ["3", "4"]
        sections = ["a", "b"]
        
        timetables = []
        if department or semester or section:
            collections = [col for col in db.collections() if '_sections' in col.id]
            for collection in collections:
                docs = collection.stream()
                for doc in docs:
                    t = doc.to_dict()
                    if ((not department or t['department'] == department) and
                        (not semester or t['semester'] == semester) and
                        (not section or t['section'] == section)):
                        timetables.append(t)
        else:
            collections = [col for col in db.collections() if '_sections' in col.id]
            for collection in collections:
                docs = collection.stream()
                timetables.extend([doc.to_dict() for doc in docs])
        
        return render_template("dashboard.html",
                            timetables=timetables,
                            departments=departments,
                            semesters=semesters,
                            sections=sections,
                            selected_department=department,
                            selected_semester=semester,
                            selected_section=section,
                            name=session['name'],
                            role=session['role'])
    except Exception as e:
        logging.error(f"Error loading dashboard: {e}")
        return render_template("dashboard.html", error_message=f"Error loading dashboard: {str(e)}")

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=True)