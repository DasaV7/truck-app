# app.py
"""
A03 Streamlit app: centralized GitHub-backed data file (CSV) + uploads stored in repo.
- If GITHUB_TOKEN is set, app reads/writes CSV and uploads files to the repo.
- If not set, app uses local data/ and uploads/ folders.
"""

import os
import io
import base64
import uuid
import datetime
import logging
from typing import Optional, List

import streamlit as st
import pandas as pd
from werkzeug.security import generate_password_hash, check_password_hash

# Optional import for GitHub API (PyGithub)
try:
    from github import Github, InputGitTreeElement
except Exception:
    Github = None

# -------------------------
# Config
# -------------------------
st.set_page_config(page_title="Trucking Hub A03 (GitHub DB)", layout="wide")

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO = os.environ.get("GITHUB_REPO")  # owner/repo
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
DATA_PATH = os.environ.get("DATA_PATH", "data/receipts.csv")
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "uploads")

USE_GITHUB = bool(GITHUB_TOKEN and GITHUB_REPO and Github is not None)

# Local fallback paths
LOCAL_DATA_DIR = "data"
LOCAL_UPLOAD_DIR = "uploads"
LOCAL_DATA_PATH = os.path.join(LOCAL_DATA_DIR, "receipts.csv")

# Ensure local dirs exist for fallback
os.makedirs(LOCAL_DATA_DIR, exist_ok=True)
os.makedirs(LOCAL_UPLOAD_DIR, exist_ok=True)

# -------------------------
# GitHub helpers
# -------------------------
if USE_GITHUB:
    gh = Github(GITHUB_TOKEN)
    repo = gh.get_repo(GITHUB_REPO)

def _get_raw_url(path: str) -> str:
    """Return raw.githubusercontent URL for a file in the repo branch."""
    owner, repo_name = GITHUB_REPO.split("/")
    return f"https://raw.githubusercontent.com/{owner}/{repo_name}/{GITHUB_BRANCH}/{path}"

def github_get_file(path: str) -> Optional[dict]:
    """Return dict with 'content' (bytes) and 'sha' or None if not found."""
    try:
        contents = repo.get_contents(path, ref=GITHUB_BRANCH)
        content_bytes = base64.b64decode(contents.content)
        return {"content": content_bytes, "sha": contents.sha}
    except Exception as e:
        logging.debug("github_get_file not found: %s", e)
        return None

def github_create_or_update_file(path: str, content_bytes: bytes, message: str, sha: Optional[str] = None):
    """Create or update a file in the repo. If sha provided, update; else create."""
    try:
        if sha:
            repo.update_file(path, message, content_bytes, sha, branch=GITHUB_BRANCH)
        else:
            repo.create_file(path, message, content_bytes, branch=GITHUB_BRANCH)
    except Exception as e:
        logging.exception("github_create_or_update_file failed")
        raise

def github_delete_file(path: str, sha: str, message: str):
    try:
        repo.delete_file(path, message, sha, branch=GITHUB_BRANCH)
    except Exception:
        logging.exception("github_delete_file failed")
        raise

# -------------------------
# CSV data helpers
# -------------------------
CSV_COLUMNS = ["id","filename","url","uploaded_at","driver","truck","trailer","notes","lat","lon"]

def read_metadata_df() -> pd.DataFrame:
    """Read metadata CSV from GitHub or local fallback."""
    if USE_GITHUB:
        f = github_get_file(DATA_PATH)
        if f:
            return pd.read_csv(io.BytesIO(f["content"]), dtype=str).fillna("")
        else:
            # create empty df and commit initial file
            df = pd.DataFrame(columns=CSV_COLUMNS)
            commit_csv_to_github(df, "Create initial receipts CSV")
            return df
    else:
        if os.path.exists(LOCAL_DATA_PATH):
            return pd.read_csv(LOCAL_DATA_PATH, dtype=str).fillna("")
        else:
            df = pd.DataFrame(columns=CSV_COLUMNS)
            df.to_csv(LOCAL_DATA_PATH, index=False)
            return df

def commit_csv_to_github(df: pd.DataFrame, message: str):
    """Commit CSV to GitHub (create or update)."""
    csv_bytes = df.to_csv(index=False).encode("utf-8")
    existing = github_get_file(DATA_PATH)
    if existing:
        sha = existing["sha"]
        github_create_or_update_file(DATA_PATH, csv_bytes, message, sha=sha)
    else:
        github_create_or_update_file(DATA_PATH, csv_bytes, message, sha=None)

def write_metadata_df(df: pd.DataFrame, message: str = "Update receipts CSV"):
    """Write metadata to GitHub or local file."""
    if USE_GITHUB:
        commit_csv_to_github(df, message)
    else:
        df.to_csv(LOCAL_DATA_PATH, index=False)

# -------------------------
# File upload helpers
# -------------------------
def save_upload(file_bytes: bytes, filename: str) -> str:
    """
    Save uploaded file to GitHub uploads/ directory (or local uploads/).
    Returns a URL (raw.githubusercontent or local path).
    """
    unique_name = f"{uuid.uuid4().hex}_{filename}"
    path = f"{UPLOAD_DIR}/{unique_name}"
    if USE_GITHUB:
        # commit file to repo
        try:
            github_create_or_update_file(path, file_bytes, f"Add upload {unique_name}", sha=None)
            return _get_raw_url(path)
        except Exception as e:
            logging.exception("Failed to upload to GitHub")
            raise
    else:
        local_path = os.path.join(LOCAL_UPLOAD_DIR, unique_name)
        with open(local_path, "wb") as f:
            f.write(file_bytes)
        return local_path

# -------------------------
# Simple local auth (for demo)
# -------------------------
# For GitHub mode you can manage users via the repo or use the app's local user table.
# This demo uses a simple local user store saved in the CSV metadata (driver field).
# For production, replace with proper auth (OIDC, Supabase, etc.)

def ensure_master_exists():
    """Create a default master user in local file if none exists (only for local mode)."""
    if USE_GITHUB:
        return
    # store users in a small local file
    users_path = os.path.join(LOCAL_DATA_DIR, "users.csv")
    if not os.path.exists(users_path):
        df = pd.DataFrame([{"username":"master","password_hash":generate_password_hash("masterpass"),"role":"master","display_name":"Owner"}])
        df.to_csv(users_path, index=False)

def local_get_user(username: str):
    users_path = os.path.join(LOCAL_DATA_DIR, "users.csv")
    if not os.path.exists(users_path):
        return None
    df = pd.read_csv(users_path, dtype=str).fillna("")
    row = df[df["username"] == username]
    if row.empty:
        return None
    r = row.iloc[0].to_dict()
    return r

def local_create_user(username: str, password: str, role: str = "driver", display_name: str = None):
    users_path = os.path.join(LOCAL_DATA_DIR, "users.csv")
    if os.path.exists(users_path):
        df = pd.read_csv(users_path, dtype=str).fillna("")
    else:
        df = pd.DataFrame(columns=["username","password_hash","role","display_name"])
    if username in df["username"].values:
        return None
    df = df.append({
        "username": username,
        "password_hash": generate_password_hash(password),
        "role": role,
        "display_name": display_name or ""
    }, ignore_index=True)
    df.to_csv(users_path, index=False)
    return True

def local_verify_login(username: str, password: str):
    u = local_get_user(username)
    if not u:
        return False
    return check_password_hash(u["password_hash"], password)

# -------------------------
# UI and pages
# -------------------------
if "user" not in st.session_state:
    st.session_state.user = None

st.title("Trucking Hub A03 — GitHub-backed DB")

if not USE_GITHUB:
    st.warning("Running in local fallback mode. To enable GitHub-backed storage set GITHUB_TOKEN and GITHUB_REPO in app secrets.")
    ensure_master_exists()

# Simple auth UI
if not st.session_state.user:
    st.sidebar.header("Sign in")
    mode = st.sidebar.selectbox("Mode", ["Sign in", "Sign up"])
    if mode == "Sign up":
        with st.form("signup"):
            username = st.text_input("Email or username")
            password = st.text_input("Password", type="password")
            display = st.text_input("Display name (optional)")
            role = st.selectbox("Role", ["driver", "master"])
            submitted = st.form_submit_button("Create account")
            if submitted:
                if USE_GITHUB:
                    st.error("Sign up via GitHub mode is not implemented in this demo. Create users via your repo or use local mode.")
                else:
                    ok = local_create_user(username.strip(), password.strip(), role=role, display_name=display.strip())
                    if ok:
                        st.success("Account created. Sign in now.")
                    else:
                        st.error("User exists")
    else:
        with st.form("login"):
            username = st.text_input("Email or username")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Sign in")
            if submitted:
                if USE_GITHUB:
                    # In GitHub mode we do not implement GitHub Auth; treat any username as driver for demo
                    st.session_state.user = {"username": username.strip(), "role": "driver", "display_name": username.strip()}
                    st.experimental_rerun()
                else:
                    if local_verify_login(username.strip(), password.strip()):
                        u = local_get_user(username.strip())
                        st.session_state.user = {"username": username.strip(), "role": u["role"], "display_name": u.get("display_name") or username.strip()}
                        st.experimental_rerun()
                    else:
                        st.error("Invalid credentials")

else:
    # Topbar
    cols = st.columns([1, 4, 1])
    with cols[0]:
        st.image("https://upload.wikimedia.org/wikipedia/commons/3/3a/Apple_logo_black.svg", width=36)
    with cols[1]:
        st.markdown(f"**Trucking Hub** — {st.session_state.user.get('display_name') or st.session_state.user.get('username')}")
    with cols[2]:
        if st.button("Logout"):
            st.session_state.user = None
            st.experimental_rerun()

    role = st.session_state.user.get("role", "driver")
    if role == "master":
        page = st.sidebar.radio("Page", ["Dashboard", "Users"])
    else:
        page = st.sidebar.radio("Page", ["Upload", "My Uploads"])

    if page == "Upload":
        st.header("Upload receipt")
        with st.form("upload_form"):
            uploaded_file = st.file_uploader("Receipt photo or PDF", type=["png","jpg","jpeg","pdf"])
            truck = st.text_input("Truck number")
            trailer = st.text_input("Trailer number")
            lat = st.text_input("Latitude (optional)")
            lon = st.text_input("Longitude (optional)")
            notes = st.text_area("Notes")
            submit = st.form_submit_button("Upload")
            if submit:
                if not uploaded_file:
                    st.error("Choose a file")
                else:
                    try:
                        raw = uploaded_file.read()
                        url = save_upload(raw, uploaded_file.name)
                        df = read_metadata_df()
                        new = {
                            "id": str(uuid.uuid4().hex),
                            "filename": uploaded_file.name,
                            "url": url,
                            "uploaded_at": datetime.datetime.utcnow().isoformat(),
                            "driver": st.session_state.user.get("username"),
                            "truck": truck.strip(),
                            "trailer": trailer.strip(),
                            "notes": notes.strip(),
                            "lat": lat.strip(),
                            "lon": lon.strip()
                        }
                        df = df.append(new, ignore_index=True)
                        write_metadata_df(df, message=f"Add receipt {new['id']}")
                        st.success("Uploaded and metadata saved to central GitHub file")
                    except Exception as e:
                        st.error(f"Upload failed: {e}")
                        logging.exception("upload failed")

    elif page == "My Uploads":
        st.header("My recent uploads")
        df = read_metadata_df()
        my = df[df["driver"] == st.session_state.user.get("username")]
        if my.empty:
            st.info("No uploads yet")
        else:
            st.dataframe(my.sort_values("uploaded_at", ascending=False).reset_index(drop=True))

    elif page == "Dashboard":
        st.header("Master Dashboard")
        st.write("Search receipts and export CSV")
        with st.form("search"):
            driver = st.text_input("Driver username/email")
            truck = st.text_input("Truck number")
            trailer = st.text_input("Trailer number")
            date_from = st.date_input("From", value=None)
            date_to = st.date_input("To", value=None)
            submitted = st.form_submit_button("Search")
            if submitted:
                df = read_metadata_df()
                q = df
                if driver:
                    q = q[q["driver"] == driver.strip()]
                if truck:
                    q = q[q["truck"] == truck.strip()]
                if trailer:
                    q = q[q["trailer"] == trailer.strip()]
                if date_from:
                    q = q[pd.to_datetime(q["uploaded_at"]) >= pd.to_datetime(datetime.datetime.combine(date_from, datetime.time.min))]
                if date_to:
                    q = q[pd.to_datetime(q["uploaded_at"]) <= pd.to_datetime(datetime.datetime.combine(date_to, datetime.time.max))]
                if q.empty:
                    st.info("No results")
                else:
                    st.dataframe(q.sort_values("uploaded_at", ascending=False).reset_index(drop=True))
                    csv = q.to_csv(index=False).encode("utf-8")
                    st.download_button("Export CSV", data=csv, file_name="receipts_export.csv", mime="text/csv")

    elif page == "Users":
        st.header("User management (local only)")
        if USE_GITHUB:
            st.info("User management UI is disabled in GitHub mode. Manage users via your repo or external auth.")
        else:
            with st.form("create_user"):
                username = st.text_input("Username")
                password = st.text_input("Password", type="password")
                role = st.selectbox("Role", ["driver", "master"])
                submit = st.form_submit_button("Create")
                if submit:
                    ok = local_create_user(username.strip(), password.strip(), role=role)
                    if ok:
                        st.success("User created")
                    else:
                        st.error("User exists")
            # list users
            users_path = os.path.join(LOCAL_DATA_DIR, "users.csv")
            if os.path.exists(users_path):
                st.dataframe(pd.read_csv(users_path).fillna(""))

