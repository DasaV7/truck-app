# app.py
import os
import io
import uuid
import datetime
from typing import Optional

import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Float, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from werkzeug.security import generate_password_hash, check_password_hash

# Optional Supabase client
try:
    from supabase import create_client as create_supabase_client
except Exception:
    create_supabase_client = None

# -------------------------
# Configuration and Setup
# -------------------------
st.set_page_config(page_title="Trucking Hub", layout="wide")

# Minimal iOS-like CSS
st.markdown(
    """
    <style>
    .stApp { background-color: #f7f7f8; color: #0b0b0b; }
    .card { background: white; border-radius: 14px; padding: 18px; box-shadow: 0 6px 18px rgba(0,0,0,0.06); }
    .title { font-weight:600; font-size:20px; }
    .muted { color: #6b6b72; font-size:13px; }
    </style>
    """,
    unsafe_allow_html=True,
)

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///data.db")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
STORAGE_TYPE = "supabase" if SUPABASE_URL and SUPABASE_KEY and create_supabase_client else "local"

# SQLAlchemy
Base = declarative_base()
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {})
SessionLocal = sessionmaker(bind=engine)
db = SessionLocal()

# -------------------------
# Models
# -------------------------
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, index=True)
    password_hash = Column(String)
    role = Column(String, default="driver")  # 'master' or 'driver'
    display_name = Column(String, nullable=True)

class Receipt(Base):
    __tablename__ = "receipts"
    id = Column(Integer, primary_key=True)
    filename = Column(String)
    url = Column(Text)
    uploaded_at = Column(DateTime, default=datetime.datetime.utcnow)
    driver = Column(String, index=True)
    truck = Column(String, index=True)
    trailer = Column(String, index=True)
    notes = Column(Text, nullable=True)
    lat = Column(Float, nullable=True)
    lon = Column(Float, nullable=True)

Base.metadata.create_all(bind=engine)

# -------------------------
# Storage helpers
# -------------------------
if STORAGE_TYPE == "supabase":
    supabase = create_supabase_client(SUPABASE_URL, SUPABASE_KEY)
    BUCKET = os.environ.get("SUPABASE_BUCKET", "receipts")
else:
    UPLOAD_DIR = "uploads"
    os.makedirs(UPLOAD_DIR, exist_ok=True)

def save_file(file_bytes: bytes, filename: str) -> str:
    """Save file to storage and return public URL or path."""
    if STORAGE_TYPE == "supabase":
        key = f"{uuid.uuid4().hex}_{filename}"
        res = supabase.storage.from_(BUCKET).upload(key, io.BytesIO(file_bytes))
        if res.get("error"):
            raise Exception(res["error"])
        public_url = supabase.storage.from_(BUCKET).get_public_url(key).get("publicURL")
        return public_url
    else:
        path = os.path.join(UPLOAD_DIR, f"{uuid.uuid4().hex}_{filename}")
        with open(path, "wb") as f:
            f.write(file_bytes)
        return path

# -------------------------
# Auth helpers
# -------------------------
def get_user_by_username(username: str) -> Optional[User]:
    return db.query(User).filter(User.username == username).first()

def create_user(username: str, password: str, role: str = "driver", display_name: str = None):
    if get_user_by_username(username):
        return None
    u = User(username=username, password_hash=generate_password_hash(password), role=role, display_name=display_name)
    db.add(u)
    db.commit()
    return u

def verify_login(username: str, password: str) -> Optional[User]:
    u = get_user_by_username(username)
    if not u:
        return None
    if check_password_hash(u.password_hash, password):
        return u
    return None

# -------------------------
# Session and UI
# -------------------------
if "user" not in st.session_state:
    st.session_state.user = None

def login_ui():
    st.markdown("<div class='card'><div class='title'>Sign in</div></div>", unsafe_allow_html=True)
    with st.form("login_form", clear_on_submit=False):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in")
        if submitted:
            user = verify_login(username.strip(), password.strip())
            if user:
                st.session_state.user = {"id": user.id, "username": user.username, "role": user.role, "display_name": user.display_name}
                st.experimental_rerun()
            else:
                st.error("Invalid credentials")

def logout():
    st.session_state.user = None
    st.experimental_rerun()

def topbar():
    cols = st.columns([1, 3, 1])
    with cols[0]:
        st.image("https://upload.wikimedia.org/wikipedia/commons/3/3a/Apple_logo_black.svg", width=36)
    with cols[1]:
        st.markdown("<div style='font-weight:600; font-size:18px'>Trucking Hub</div>", unsafe_allow_html=True)
    with cols[2]:
        if st.session_state.user:
            st.markdown(f"**{st.session_state.user.get('display_name') or st.session_state.user['username']}**")
            if st.button("Logout"):
                logout()

# -------------------------
# Pages
# -------------------------
def driver_portal(user):
    st.markdown("<div class='card'><div class='title'>Driver Portal</div></div>", unsafe_allow_html=True)
    st.write("Upload receipts and optionally include GPS coordinates.")
    with st.form("upload"):
        uploaded_file = st.file_uploader("Receipt photo or PDF", type=["png","jpg","jpeg","pdf"])
        truck = st.text_input("Truck number")
        trailer = st.text_input("Trailer number")
        lat = st.text_input("Latitude (optional)")
        lon = st.text_input("Longitude (optional)")
        notes = st.text_area("Notes")
        submit = st.form_submit_button("Upload")
        if submit:
            if not uploaded_file:
                st.error("Please choose a file")
            else:
                raw = uploaded_file.read()
                url = save_file(raw, uploaded_file.name)
                r = Receipt(
                    filename=uploaded_file.name,
                    url=url,
                    driver=user["username"],
                    truck=truck.strip(),
                    trailer=trailer.strip(),
                    notes=notes.strip() or None,
                    lat=float(lat) if lat else None,
                    lon=float(lon) if lon else None
                )
                db.add(r)
                db.commit()
                st.success("Uploaded")
                st.experimental_rerun()

    st.markdown("### Your recent uploads")
    rows = db.query(Receipt).filter(Receipt.driver == user["username"]).order_by(Receipt.uploaded_at.desc()).limit(50).all()
    if rows:
        df = pd.DataFrame([{
            "id": r.id, "filename": r.filename, "truck": r.truck, "trailer": r.trailer,
            "uploaded_at": r.uploaded_at, "notes": r.notes, "url": r.url
        } for r in rows])
        st.dataframe(df)
    else:
        st.info("No uploads yet")

def master_dashboard():
    st.markdown("<div class='card'><div class='title'>Master Dashboard</div></div>", unsafe_allow_html=True)
    st.write("Search receipts and export CSVs.")
    with st.form("search"):
        driver = st.text_input("Driver username")
        truck = st.text_input("Truck number")
        trailer = st.text_input("Trailer number")
        date_from = st.date_input("From", value=None)
        date_to = st.date_input("To", value=None)
        submitted = st.form_submit_button("Search")
        if submitted:
            q = db.query(Receipt)
            if driver: q = q.filter(Receipt.driver == driver.strip())
            if truck: q = q.filter(Receipt.truck == truck.strip())
            if trailer: q = q.filter(Receipt.trailer == trailer.strip())
            if date_from:
                q = q.filter(Receipt.uploaded_at >= datetime.datetime.combine(date_from, datetime.time.min))
            if date_to:
                q = q.filter(Receipt.uploaded_at <= datetime.datetime.combine(date_to, datetime.time.max))
            results = q.order_by(Receipt.uploaded_at.desc()).all()
            if not results:
                st.info("No results")
            else:
                df = pd.DataFrame([{
                    "id": r.id, "filename": r.filename, "driver": r.driver, "truck": r.truck,
                    "trailer": r.trailer, "uploaded_at": r.uploaded_at, "notes": r.notes, "url": r.url
                } for r in results])
                st.dataframe(df)
                csv = df.to_csv(index=False).encode("utf-8")
                st.download_button("Export CSV", data=csv, file_name="receipts_export.csv", mime="text/csv")

    st.markdown("---")
    st.markdown("### Live Map (last known points)")
    pts = db.query(Receipt).filter(Receipt.lat != None, Receipt.lon != None).order_by(Receipt.uploaded_at.desc()).all()
    if pts:
        map_df = pd.DataFrame([{"lat": p.lat, "lon": p.lon, "driver": p.driver, "time": p.uploaded_at} for p in pts])
        st.map(map_df.rename(columns={"lon":"lon","lat":"lat"}))
    else:
        st.info("No GPS points available")

def admin_user_management():
    st.markdown("<div class='card'><div class='title'>User Management</div></div>", unsafe_allow_html=True)
    st.write("Create driver accounts")
    with st.form("create_user"):
        username = st.text_input("Username")
        display = st.text_input("Display name")
        password = st.text_input("Password", type="password")
        role = st.selectbox("Role", ["driver", "master"])
        submit = st.form_submit_button("Create")
        if submit:
            if not username or not password:
                st.error("Provide username and password")
            else:
                u = create_user(username.strip(), password.strip(), role=role, display_name=display.strip() or None)
                if u:
                    st.success(f"Created {username}")
                else:
                    st.error("User exists")

    st.markdown("### Existing users")
    users = db.query(User).all()
    df = pd.DataFrame([{"id": u.id, "username": u.username, "role": u.role, "display_name": u.display_name} for u in users])
    st.dataframe(df)

# -------------------------
# App routing
# -------------------------
st.sidebar.title("Navigation")
if not st.session_state.user:
    st.sidebar.info("Please sign in")
    login_ui()
else:
    topbar()
    role = st.session_state.user["role"]
    if role == "master":
        page = st.sidebar.radio("Page", ["Dashboard", "Users"])
        if page == "Dashboard":
            master_dashboard()
        else:
            admin_user_management()
    else:
        page = st.sidebar.radio("Page", ["Portal", "My Uploads"])
        if page == "Portal":
            driver_portal(st.session_state.user)
        else:
            driver_portal(st.session_state.user)
