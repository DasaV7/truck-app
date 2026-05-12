# app.py
"""
A02 Streamlit app for Trucking Hub
- Uses Supabase (Auth + Storage + Postgres) when SUPABASE_URL and SUPABASE_KEY are set.
- Falls back to local SQLite and local auth for quick testing.
- Master and driver roles supported.
- Mobile-friendly upload page included.
"""

import os
import io
import uuid
import datetime
import logging
from typing import Optional

import streamlit as st

# Defensive imports with clear messages
try:
    import pandas as pd
except Exception:
    st.error("Missing dependency: pandas. Add pandas to requirements.txt and redeploy.")
    logging.exception("pandas import failed")
    st.stop()

try:
    from werkzeug.security import generate_password_hash, check_password_hash
except Exception:
    st.error("Missing dependency: werkzeug. Add werkzeug to requirements.txt and redeploy.")
    logging.exception("werkzeug import failed")
    st.stop()

# Try to import supabase client; optional
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
use_supabase = False
supabase = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        from supabase import create_client as create_supabase_client
        supabase = create_supabase_client(SUPABASE_URL, SUPABASE_KEY)
        use_supabase = True
    except Exception:
        st.warning("Supabase client import failed. Falling back to local mode.")
        logging.exception("supabase import failed")
        use_supabase = False

# Local DB fallback using SQLite and SQLAlchemy
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///data.db")
use_local_db = not use_supabase

if use_local_db:
    try:
        from sqlalchemy import create_engine, Column, Integer, String, DateTime, Float, Text
        from sqlalchemy.ext.declarative import declarative_base
        from sqlalchemy.orm import sessionmaker
    except Exception:
        st.error("Missing dependency: SQLAlchemy. Add SQLAlchemy to requirements.txt and redeploy.")
        logging.exception("SQLAlchemy import failed")
        st.stop()

# App config and CSS
st.set_page_config(page_title="Trucking Hub A02", layout="wide")
st.markdown(
    """
    <style>
    .stApp { background-color: #f7f7f8; color: #0b0b0b; }
    .card { background: white; border-radius: 14px; padding: 18px; box-shadow: 0 6px 18px rgba(0,0,0,0.06); margin-bottom:12px; }
    .title { font-weight:600; font-size:20px; }
    .muted { color: #6b6b72; font-size:13px; }
    .mobile { max-width:420px; margin:auto; }
    </style>
    """,
    unsafe_allow_html=True,
)

# -------------------------
# Local DB models and helpers
# -------------------------
if use_local_db:
    Base = declarative_base()
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {})
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    class User(Base):
        __tablename__ = "users"
        id = Column(Integer, primary_key=True)
        username = Column(String, unique=True, index=True)
        password_hash = Column(String)
        role = Column(String, default="driver")
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
STORAGE_BUCKET = os.environ.get("SUPABASE_BUCKET", "receipts")

def save_file_to_supabase(file_bytes: bytes, filename: str) -> str:
    """Upload to Supabase Storage and return public URL."""
    key = f"{uuid.uuid4().hex}_{filename}"
    res = supabase.storage.from_(STORAGE_BUCKET).upload(key, io.BytesIO(file_bytes), {"cacheControl":"3600", "upsert": False})
    if res.get("error"):
        logging.exception("Supabase storage upload error: %s", res)
        raise Exception(res["error"])
    public = supabase.storage.from_(STORAGE_BUCKET).get_public_url(key)
    # supabase returns dict with 'publicURL' or 'public_url' depending on client version
    public_url = public.get("publicURL") or public.get("public_url") or ""
    return public_url

UPLOAD_DIR = "uploads"
if not use_supabase:
    os.makedirs(UPLOAD_DIR, exist_ok=True)

def save_file_local(file_bytes: bytes, filename: str) -> str:
    path = os.path.join(UPLOAD_DIR, f"{uuid.uuid4().hex}_{filename}")
    with open(path, "wb") as f:
        f.write(file_bytes)
    return path

def save_file(file_bytes: bytes, filename: str) -> str:
    if use_supabase:
        return save_file_to_supabase(file_bytes, filename)
    else:
        return save_file_local(file_bytes, filename)

# -------------------------
# Auth helpers
# -------------------------
def local_get_user(username: str) -> Optional[object]:
    return db.query(User).filter(User.username == username).first()

def local_create_user(username: str, password: str, role: str = "driver", display_name: str = None):
    if local_get_user(username):
        return None
    u = User(username=username, password_hash=generate_password_hash(password), role=role, display_name=display_name)
    db.add(u)
    db.commit()
    return u

def local_verify_login(username: str, password: str) -> Optional[object]:
    u = local_get_user(username)
    if not u:
        return None
    if check_password_hash(u.password_hash, password):
        return u
    return None

# Supabase auth helpers
def supabase_sign_up(email: str, password: str):
    """Sign up user via Supabase Auth. Returns user dict or raises."""
    res = supabase.auth.sign_up({"email": email, "password": password})
    if res.get("error"):
        raise Exception(res["error"])
    return res.get("user") or res

def supabase_sign_in(email: str, password: str):
    res = supabase.auth.sign_in_with_password({"email": email, "password": password})
    if res.get("error"):
        raise Exception(res["error"])
    return res.get("data") or res

def supabase_get_user_profile(email: str):
    """Look up user profile in 'users' table (Postgres) for role and display_name."""
    res = supabase.table("users").select("*").eq("email", email).limit(1).execute()
    if res.get("error"):
        logging.exception("Supabase table query error: %s", res)
        return None
    data = res.get("data") or []
    return data[0] if data else None

def supabase_create_profile(email: str, role: str = "driver", display_name: str = None):
    payload = {"email": email, "role": role, "display_name": display_name}
    res = supabase.table("users").insert(payload).execute()
    if res.get("error"):
        logging.exception("Supabase insert profile error: %s", res)
        raise Exception(res["error"])
    return res.get("data")[0]

# -------------------------
# Receipt helpers
# -------------------------
def supabase_insert_receipt(meta: dict):
    res = supabase.table("receipts").insert(meta).execute()
    if res.get("error"):
        logging.exception("Supabase insert receipt error: %s", res)
        raise Exception(res["error"])
    return res.get("data")[0]

def local_insert_receipt(meta: dict):
    r = Receipt(
        filename=meta.get("filename"),
        url=meta.get("url"),
        uploaded_at=meta.get("uploaded_at"),
        driver=meta.get("driver"),
        truck=meta.get("truck"),
        trailer=meta.get("trailer"),
        notes=meta.get("notes"),
        lat=meta.get("lat"),
        lon=meta.get("lon"),
    )
    db.add(r)
    db.commit()
    return r

# -------------------------
# Session and UI helpers
# -------------------------
if "user" not in st.session_state:
    st.session_state.user = None
if "supabase_session" not in st.session_state:
    st.session_state.supabase_session = None

def topbar():
    cols = st.columns([1, 3, 1])
    with cols[0]:
        st.image("https://upload.wikimedia.org/wikipedia/commons/3/3a/Apple_logo_black.svg", width=36)
    with cols[1]:
        st.markdown("<div style='font-weight:600; font-size:18px'>Trucking Hub</div>", unsafe_allow_html=True)
    with cols[2]:
        if st.session_state.user:
            st.markdown(f"**{st.session_state.user.get('display_name') or st.session_state.user.get('username') or st.session_state.user.get('email')}**")
            if st.button("Logout"):
                logout()

def logout():
    st.session_state.user = None
    st.session_state.supabase_session = None
    st.experimental_rerun()

# -------------------------
# Pages
# -------------------------
def signup_page():
    st.markdown("<div class='card'><div class='title'>Sign up</div></div>", unsafe_allow_html=True)
    if use_supabase:
        st.info("Sign up will create an account via Supabase Auth. You will receive a confirmation email if enabled.")
    with st.form("signup"):
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        display = st.text_input("Display name (optional)")
        role = st.selectbox("Role", ["driver", "master"])
        submit = st.form_submit_button("Create account")
        if submit:
            if not email or not password:
                st.error("Provide email and password")
            else:
                try:
                    if use_supabase:
                        supabase_sign_up(email, password)
                        # create profile row
                        try:
                            supabase_create_profile(email, role=role, display_name=display or None)
                        except Exception:
                            # profile may already exist
                            pass
                        st.success("Account created. Check your email for confirmation if enabled.")
                    else:
                        u = local_create_user(email.strip(), password.strip(), role=role, display_name=display.strip() or None)
                        if u:
                            st.success("Local account created. Sign in now.")
                        else:
                            st.error("User already exists")
                except Exception as e:
                    st.error(f"Sign up failed: {str(e)}")
                    logging.exception("signup error")

def login_ui():
    st.markdown("<div class='card'><div class='title'>Sign in</div></div>", unsafe_allow_html=True)
    with st.form("login_form", clear_on_submit=False):
        username = st.text_input("Email or username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in")
        if submitted:
            if use_supabase:
                try:
                    data = supabase_sign_in(username.strip(), password.strip())
                    # supabase returns session and user in different client versions
                    # fetch profile from users table
                    profile = supabase_get_user_profile(username.strip())
                    st.session_state.user = {"email": username.strip(), "role": profile.get("role") if profile else "driver", "display_name": profile.get("display_name") if profile else username.strip()}
                    st.experimental_rerun()
                except Exception as e:
                    st.error("Sign in failed. Check credentials.")
                    logging.exception("supabase sign in failed")
            else:
                user = local_verify_login(username.strip(), password.strip())
                if user:
                    st.session_state.user = {"username": user.username, "role": user.role, "display_name": user.display_name}
                    st.experimental_rerun()
                else:
                    st.error("Invalid credentials")

def driver_upload_view(user, mobile=False):
    # Mobile-friendly single-column layout if mobile True
    container = st.container()
    if mobile:
        st.markdown("<div class='mobile'>", unsafe_allow_html=True)
    st.markdown("<div class='card'><div class='title'>Upload Receipt</div></div>", unsafe_allow_html=True)
    with st.form("upload"):
        uploaded_file = st.file_uploader("Receipt photo or PDF", type=["png","jpg","jpeg","pdf"])
        if mobile:
            truck = st.text_input("Truck number", key="truck_m")
            trailer = st.text_input("Trailer number", key="trailer_m")
        else:
            cols = st.columns(2)
            with cols[0]:
                truck = st.text_input("Truck number")
            with cols[1]:
                trailer = st.text_input("Trailer number")
        lat = st.text_input("Latitude (optional)")
        lon = st.text_input("Longitude (optional)")
        notes = st.text_area("Notes")
        submit = st.form_submit_button("Upload")
        if submit:
            if not uploaded_file:
                st.error("Please choose a file")
            else:
                try:
                    raw = uploaded_file.read()
                    url = save_file(raw, uploaded_file.name)
                    meta = {
                        "filename": uploaded_file.name,
                        "url": url,
                        "uploaded_at": datetime.datetime.utcnow().isoformat(),
                        "driver": user.get("email") or user.get("username"),
                        "truck": truck.strip(),
                        "trailer": trailer.strip(),
                        "notes": notes.strip() or None,
                        "lat": float(lat) if lat else None,
                        "lon": float(lon) if lon else None,
                    }
                    if use_supabase:
                        supabase_insert_receipt(meta)
                    else:
                        # convert uploaded_at back to datetime for local insert
                        meta["uploaded_at"] = datetime.datetime.fromisoformat(meta["uploaded_at"])
                        local_insert_receipt(meta)
                    st.success("Uploaded")
                    st.experimental_rerun()
                except Exception as e:
                    st.error(f"Upload failed: {str(e)}")
                    logging.exception("upload failed")
    if mobile:
        st.markdown("</div>", unsafe_allow_html=True)

def driver_portal(user):
    st.markdown("<div class='card'><div class='title'>Driver Portal</div></div>", unsafe_allow_html=True)
    st.write("Upload receipts and view your recent uploads.")
    driver_upload_view(user, mobile=False)
    st.markdown("### Your recent uploads")
    if use_supabase:
        res = supabase.table("receipts").select("*").eq("driver", user.get("email")).order("uploaded_at", desc=True).limit(100).execute()
        rows = res.get("data") or []
        if rows:
            df = pd.DataFrame(rows)
            st.dataframe(df[["id","filename","truck","trailer","uploaded_at","notes","url"]])
        else:
            st.info("No uploads yet")
    else:
        rows = db.query(Receipt).filter(Receipt.driver == user.get("username")).order_by(Receipt.uploaded_at.desc()).limit(100).all()
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
        driver = st.text_input("Driver email or username")
        truck = st.text_input("Truck number")
        trailer = st.text_input("Trailer number")
        date_from = st.date_input("From", value=None)
        date_to = st.date_input("To", value=None)
        submitted = st.form_submit_button("Search")
        if submitted:
            try:
                if use_supabase:
                    q = supabase.table("receipts").select("*")
                    if driver: q = q.eq("driver", driver.strip())
                    if truck: q = q.eq("truck", truck.strip())
                    if trailer: q = q.eq("trailer", trailer.strip())
                    if date_from:
                        q = q.gte("uploaded_at", datetime.datetime.combine(date_from, datetime.time.min).isoformat())
                    if date_to:
                        q = q.lte("uploaded_at", datetime.datetime.combine(date_to, datetime.time.max).isoformat())
                    res = q.order("uploaded_at", desc=True).execute()
                    rows = res.get("data") or []
                    if not rows:
                        st.info("No results")
                    else:
                        df = pd.DataFrame(rows)
                        st.dataframe(df[["id","filename","driver","truck","trailer","uploaded_at","notes","url"]])
                        csv = df.to_csv(index=False).encode("utf-8")
                        st.download_button("Export CSV", data=csv, file_name="receipts_export.csv", mime="text/csv")
                else:
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
            except Exception as e:
                st.error("Search failed")
                logging.exception("search failed")

    st.markdown("---")
    st.markdown("### Live Map (last known points)")
    try:
        if use_supabase:
            res = supabase.table("receipts").select("lat,lon,driver,uploaded_at").not_("lat", "is", None).not_("lon", "is", None).order("uploaded_at", desc=True).limit(200).execute()
            pts = res.get("data") or []
            if pts:
                map_df = pd.DataFrame([{"lat": p.get("lat"), "lon": p.get("lon"), "driver": p.get("driver"), "time": p.get("uploaded_at")} for p in pts])
                st.map(map_df.rename(columns={"lon":"lon","lat":"lat"}))
            else:
                st.info("No GPS points available")
        else:
            pts = db.query(Receipt).filter(Receipt.lat != None, Receipt.lon != None).order_by(Receipt.uploaded_at.desc()).limit(200).all()
            if pts:
                map_df = pd.DataFrame([{"lat": p.lat, "lon": p.lon, "driver": p.driver, "time": p.uploaded_at} for p in pts])
                st.map(map_df)
            else:
                st.info("No GPS points available")
    except Exception:
        st.error("Failed to load map data")
        logging.exception("map load failed")

def admin_user_management():
    st.markdown("<div class='card'><div class='title'>User Management</div></div>", unsafe_allow_html=True)
    st.write("Create driver accounts and manage roles.")
    with st.form("create_user"):
        username = st.text_input("Email or username")
        display = st.text_input("Display name")
        password = st.text_input("Password", type="password")
        role = st.selectbox("Role", ["driver", "master"])
        submit = st.form_submit_button("Create")
        if submit:
            if not username or not password:
                st.error("Provide username and password")
            else:
                try:
                    if use_supabase:
                        # create auth user and profile row
                        supabase_sign_up(username.strip(), password.strip())
                        try:
                            supabase_create_profile(username.strip(), role=role, display_name=display.strip() or None)
                        except Exception:
                            pass
                        st.success(f"Created {username} (Supabase)")
                    else:
                        u = local_create_user(username.strip(), password.strip(), role=role, display_name=display.strip() or None)
                        if u:
                            st.success(f"Created {username}")
                        else:
                            st.error("User exists")
                except Exception as e:
                    st.error(f"Create user failed: {str(e)}")
                    logging.exception("create user failed")

    st.markdown("### Existing users")
    try:
        if use_supabase:
            res = supabase.table("users").select("*").order("email", desc=False).execute()
            users = res.get("data") or []
            df = pd.DataFrame(users)
            if not df.empty:
                st.dataframe(df[["id","email","role","display_name"]])
            else:
                st.info("No users found in Supabase users table")
        else:
            users = db.query(User).all()
            df = pd.DataFrame([{"id": u.id, "username": u.username, "role": u.role, "display_name": u.display_name} for u in users])
            st.dataframe(df)
    except Exception:
        st.error("Failed to load users")
        logging.exception("load users failed")

# -------------------------
# App routing
# -------------------------
st.sidebar.title("Navigation")
if not st.session_state.user:
    st.sidebar.info("Please sign in or sign up")
    login_ui()
    st.sidebar.markdown("---")
    st.sidebar.button("Sign up", on_click=lambda: st.session_state.update({"show_signup": True}))
    if st.session_state.get("show_signup"):
        signup_page()
else:
    topbar()
    role = st.session_state.user.get("role", "driver")
    if role == "master":
        page = st.sidebar.radio("Page", ["Dashboard", "Users"])
        if page == "Dashboard":
            master_dashboard()
        else:
            admin_user_management()
    else:
        page = st.sidebar.radio("Page", ["Portal", "Mobile Upload"])
        if page == "Portal":
            driver_portal(st.session_state.user)
        else:
            # Mobile upload view optimized for phones
            st.markdown("<div class='mobile'>", unsafe_allow_html=True)
            st.markdown("<div class='card'><div class='title'>Mobile Upload</div></div>", unsafe_allow_html=True)
            driver_upload_view(st.session_state.user, mobile=True)
            st.markdown("</div>", unsafe_allow_html=True)
