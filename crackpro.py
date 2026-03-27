#!/usr/bin/env python3
"""
crackpro.py — ZipPasswordCrack.in v24 ULTRA PRODUCTION
FIXES v24:
  - sqlite3.Row → dict() conversion (Dashboard Error FIXED)
  - Service Unavailable: robust error handling everywhere
  - Personal dashboard: user sirf apna data dekhega
  - Email notifications: working perfectly
  - Google OAuth: fully working
  - Speed: 10k-50k/s standard ZIP, ~500/s AES (hardware limit)
  - All tools working: image compress, zip, pdf merge, pdf2jpg
  - SEO optimized
  - Mobile + Desktop responsive
  - File auto-delete after crack
"""
import subprocess, sys, os, smtplib, secrets, json, time, uuid, sqlite3
import hashlib, logging, threading, traceback, io, zipfile as zf_mod
from pathlib import Path
from datetime import datetime, timedelta
from functools import wraps
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ── Auto-install ──────────────────────────────────────────────────────────────
for _p in ["flask","pyzipper","pypdf","requests","werkzeug","Pillow","pikepdf"]:
    try: __import__(_p.lower().replace("-","_"))
    except: subprocess.run([sys.executable,"-m","pip","install","-q",_p],check=False)

from flask import (Flask, request, jsonify, session, redirect,
                   send_file, abort, Response)
from werkzeug.utils import secure_filename

try:
    from engine_ultra import gen_master, Cracker, CS, GITHUB_LISTS
except ImportError:
    print("ERROR: engine_ultra.py not found!"); sys.exit(1)

# ── Helper: sqlite3.Row → dict (MAIN BUG FIX) ────────────────────────────────
def row2dict(row):
    """Convert sqlite3.Row to dict safely — fixes 'sqlite3.Row has no attr get'"""
    if row is None: return None
    if isinstance(row, dict): return row
    try: return dict(row)
    except: return {k: row[k] for k in row.keys()}

# ── Directories ───────────────────────────────────────────────────────────────
def _data_dir():
    for d in [os.environ.get("DATA_DIR",""),"/tmp/zipcracker"]:
        if d:
            p=Path(d)
            try: p.mkdir(parents=True,exist_ok=True); return p
            except: continue
    return Path("/tmp/zipcracker")

DATA_DIR=_data_dir()
UPLOAD=DATA_DIR/"uploads"; DLDIR=DATA_DIR/"downloads"
LOGDIR=DATA_DIR/"logs";    DICTS=DATA_DIR/"dictionaries"
DB_F=DATA_DIR/"crackpro.db"
for d in [UPLOAD,DLDIR,LOGDIR,DICTS]: d.mkdir(parents=True,exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOGDIR/f"app_{datetime.now():%Y%m%d}.log",encoding="utf-8"),
        logging.StreamHandler(),
    ]
)
L=logging.getLogger("app")
L.info(f"ZipPasswordCrack.in v24 ULTRA | DATA={DATA_DIR}")

# ── Flask ─────────────────────────────────────────────────────────────────────
app=Flask(__name__,static_folder=None)
app.secret_key=os.environ.get("SECRET_KEY",secrets.token_hex(32))
app.config.update(
    MAX_CONTENT_LENGTH=200*1024*1024,
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    SESSION_COOKIE_SECURE=os.environ.get("RAILWAY_ENVIRONMENT","")!="",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

# ── Config ────────────────────────────────────────────────────────────────────
SMTP_HOST            =os.environ.get("SMTP_HOST","smtp.gmail.com")
SMTP_PORT            =int(os.environ.get("SMTP_PORT","587"))
SMTP_USER            =os.environ.get("SMTP_USER","")
SMTP_PASS            =os.environ.get("SMTP_PASS","").replace(" ","")
SITE_URL             =os.environ.get("SITE_URL","https://zippasswordcrack.in")
SITE_NAME            ="ZipPasswordCrack.in"
GOOGLE_CLIENT_ID     =os.environ.get("GOOGLE_CLIENT_ID","").strip("()")
GOOGLE_CLIENT_SECRET =os.environ.get("GOOGLE_CLIENT_SECRET","").strip("()")
ADS_CLIENT           =os.environ.get("GOOGLE_ADS_CLIENT","")
ADS_SLOT             =os.environ.get("GOOGLE_ADS_SLOT","")

JOBS={}; JLOCK=threading.Lock()

# ── DB ────────────────────────────────────────────────────────────────────────
def get_db():
    c=sqlite3.connect(str(DB_F),check_same_thread=False,timeout=30)
    c.row_factory=sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=15000")
    c.execute("PRAGMA synchronous=NORMAL")
    c.execute("PRAGMA cache_size=10000")
    return c

def init_db():
    try:
        c=get_db(); c.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT, email TEXT UNIQUE,
            display_name TEXT DEFAULT '',
            terms_accepted INTEGER DEFAULT 0,
            email_verified INTEGER DEFAULT 0,
            email_token TEXT DEFAULT '',
            reset_token TEXT DEFAULT '',
            reset_expires TEXT DEFAULT '',
            notif_email INTEGER DEFAULT 1,
            created_at TEXT DEFAULT(datetime('now')),
            last_login TEXT,
            login_type TEXT DEFAULT 'password',
            google_id TEXT, avatar TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS jobs(
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            filename TEXT DEFAULT '', filetype TEXT DEFAULT '',
            filesize INTEGER DEFAULT 0,
            status TEXT DEFAULT 'queued', mode TEXT DEFAULT 'smart',
            cfg TEXT DEFAULT '{}', found_pw TEXT,
            attempts INTEGER DEFAULT 0, elapsed REAL DEFAULT 0,
            speed INTEGER DEFAULT 0, current_pw TEXT DEFAULT '',
            dl_ready INTEGER DEFAULT 0, dl_path TEXT DEFAULT '',
            est_eta TEXT DEFAULT '', use_aes INTEGER DEFAULT 0,
            notified INTEGER DEFAULT 0,
            created_at TEXT DEFAULT(datetime('now')), finished_at TEXT
        );
        CREATE TABLE IF NOT EXISTS jlogs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL, msg TEXT NOT NULL,
            ts TEXT DEFAULT(datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS stats(
            id INTEGER PRIMARY KEY,
            total_cracked INTEGER DEFAULT 0,
            total_attempts INTEGER DEFAULT 0,
            total_jobs INTEGER DEFAULT 0,
            updated_at TEXT DEFAULT(datetime('now'))
        );
        INSERT OR IGNORE INTO stats(id,total_cracked,total_attempts,total_jobs) VALUES(1,0,0,0);
        CREATE INDEX IF NOT EXISTS idx_jobs_user ON jobs(user_id);
        CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
        CREATE INDEX IF NOT EXISTS idx_jlogs_job ON jlogs(job_id);
        """); c.commit(); c.close()
        L.info("DB OK")
    except Exception as e: L.error(f"DB init: {e}")

init_db()

def hp(p): return hashlib.sha256(p.encode()).hexdigest() if p else ""

def me():
    uid=session.get("uid")
    if not uid: return None
    try:
        c=get_db()
        row=c.execute("SELECT * FROM users WHERE id=?",(uid,)).fetchone()
        c.close()
        return row2dict(row)  # FIX: return dict not Row
    except Exception as e: L.error(f"me(): {e}"); return None

def rlogin(f):
    @wraps(f)
    def inner(*a,**kw):
        if "uid" not in session: return redirect("/login")
        return f(*a,**kw)
    return inner

def jset(jid,**kw):
    if not kw: return
    s=",".join(k+"=?" for k in kw)
    try:
        c=get_db(); c.execute("UPDATE jobs SET "+s+" WHERE id=?",list(kw.values())+[jid])
        c.commit(); c.close()
    except Exception as e: L.error(f"jset: {e}")

def jlog(jid,msg):
    try:
        c=get_db(); c.execute("INSERT INTO jlogs(job_id,msg) VALUES(?,?)",(jid,msg[:500]))
        c.commit(); c.close()
    except: pass

def fmt_t(s):
    s=int(s or 0)
    if s<60: return f"{s}s"
    if s<3600: return f"{s//60}m {s%60}s"
    if s<86400: return f"{s//3600}h {(s%3600)//60}m"
    return f"{s//86400}d {(s%86400)//3600}h"

def get_stats():
    try:
        c=get_db(); row=c.execute("SELECT * FROM stats WHERE id=1").fetchone(); c.close()
        d=row2dict(row)
        return d if d else {"total_cracked":0,"total_attempts":0,"total_jobs":0}
    except: return {"total_cracked":0,"total_attempts":0,"total_jobs":0}

def upd_stats(cracked=0,attempts=0,jobs=0):
    try:
        c=get_db()
        c.execute("UPDATE stats SET total_cracked=total_cracked+?,total_attempts=total_attempts+?,"
                  "total_jobs=total_jobs+?,updated_at=datetime('now') WHERE id=1",(cracked,attempts,jobs))
        c.commit(); c.close()
    except: pass

def get_job_dict(jid, user_id=None):
    """Get job as dict safely"""
    try:
        c=get_db()
        if user_id:
            row=c.execute("SELECT * FROM jobs WHERE id=? AND user_id=?",(jid,user_id)).fetchone()
        else:
            row=c.execute("SELECT * FROM jobs WHERE id=?",(jid,)).fetchone()
        c.close()
        return row2dict(row)
    except Exception as e: L.error(f"get_job_dict: {e}"); return None

# ── Email ─────────────────────────────────────────────────────────────────────
def send_email(to_email,subject,html_body,text_body=None):
    if not SMTP_USER or not SMTP_PASS or not to_email:
        L.info(f"Email not configured, skipping: {subject}"); return False
    try:
        msg=MIMEMultipart("alternative")
        msg["Subject"]=subject
        msg["From"]=f"{SITE_NAME} <{SMTP_USER}>"
        msg["To"]=to_email
        if text_body: msg.attach(MIMEText(text_body,"plain","utf-8"))
        msg.attach(MIMEText(html_body,"html","utf-8"))
        s=smtplib.SMTP(SMTP_HOST,SMTP_PORT,timeout=30)
        s.ehlo(); s.starttls(); s.ehlo()
        s.login(SMTP_USER,SMTP_PASS)
        s.sendmail(SMTP_USER,to_email,msg.as_string())
        s.quit()
        L.info(f"Email sent to {to_email}: {subject}"); return True
    except Exception as e: L.error(f"Email ({type(e).__name__}): {e}"); return False

def email_password_found(user_email,username,filename,password,job_id):
    if not user_email: return
    if len(password)>5: masked=password[:3]+"*"*(len(password)-5)+password[-2:]
    else: masked=password[:1]+"*"*max(len(password)-1,1)
    dashboard_url=f"{SITE_URL}/job/{job_id}"
    subject=f"Password Mil Gaya! — {filename} — {SITE_NAME}"
    now_str=datetime.now().strftime("%d %b %Y, %I:%M %p")
    html=f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><style>
body{{font-family:Arial,sans-serif;background:#030609;color:#bdd0e8;margin:0;padding:0}}
.w{{max-width:600px;margin:0 auto;padding:30px 20px}}
.c{{background:#090f18;border:1px solid #111e30;border-radius:14px;padding:32px}}
.logo{{color:#00c6ff;font-size:22px;font-weight:900;font-family:monospace;text-align:center;margin-bottom:20px}}
.title{{color:#00e676;font-size:26px;font-weight:800;text-align:center}}
.pw-box{{background:#000;border:2px solid #00e676;border-radius:10px;padding:22px;text-align:center;margin:20px 0}}
.pw{{color:#00e676;font-size:30px;font-weight:900;font-family:monospace;letter-spacing:4px}}
.btn{{display:block;background:linear-gradient(135deg,#00c6ff,#00e676);color:#000;text-decoration:none;text-align:center;padding:15px;border-radius:9px;font-weight:900;font-size:16px;margin:22px 0}}
.row{{display:flex;justify-content:space-between;padding:9px 0;border-bottom:1px solid #111e30;font-size:14px}}
.warn{{background:rgba(255,100,0,.08);border:1px solid rgba(255,100,0,.3);border-radius:7px;padding:13px;margin-top:16px;color:#cc8060;font-size:12px}}
.foot{{text-align:center;margin-top:22px;color:#3d5268;font-size:12px}}
</style></head><body><div class="w"><div class="c">
<div class="logo">&#9889; ZipPasswordCrack.in</div>
<div class="title">&#127881; Password Mil Gaya!</div>
<p style="color:#3d5268;font-size:14px;text-align:center">Aapki file ka password recover ho gaya</p>
<div class="pw-box">
<div style="color:#3d5268;font-size:11px;margin-bottom:12px">PARTIAL PASSWORD — DASHBOARD PE FULL DEKHEIN</div>
<div class="pw">{masked}</div>
<div style="color:#3d5268;font-size:12px;margin-top:10px">Security ke liye partially hidden</div>
</div>
<div style="margin:16px 0">
<div class="row"><span style="color:#3d5268">&#128193; File</span><span style="font-weight:700">{filename}</span></div>
<div class="row"><span style="color:#3d5268">&#128100; Account</span><span style="font-weight:700">{username}</span></div>
<div class="row"><span style="color:#3d5268">&#128336; Time</span><span style="font-weight:700">{now_str}</span></div>
</div>
<a href="{dashboard_url}" class="btn">&#128272; Complete Password &amp; Download &#8594; Dashboard</a>
<div class="warn">&#9888; Yeh service sirf apni khud ki files ke liye. Unauthorized use illegal hai (IT Act 2000).</div>
</div><div class="foot">
<a href="{SITE_URL}" style="color:#00c6ff">{SITE_NAME}</a> — India's #1 Password Recovery
</div></div></body></html>"""
    threading.Thread(target=send_email,args=(user_email,subject,html),daemon=True).start()

def email_welcome(to_email,username,verify_token=None):
    subject=f"Welcome to {SITE_NAME}!"
    vbtn=(f'<a href="{SITE_URL}/verify-email?token={verify_token}" style="display:inline-block;background:linear-gradient(135deg,#00c6ff,#00e676);color:#000;text-decoration:none;padding:14px 30px;border-radius:9px;font-weight:900;margin:10px 5px">&#9989; Email Verify Karo</a>'
          if verify_token else "")
    html=f"""<!DOCTYPE html><html><head><meta charset="UTF-8"></head><body style="font-family:Arial,sans-serif;background:#030609;color:#bdd0e8;margin:0;padding:20px">
<div style="max-width:550px;margin:0 auto;background:#090f18;border:1px solid #111e30;border-radius:14px;padding:32px;text-align:center">
<div style="color:#00c6ff;font-size:22px;font-weight:900;font-family:monospace;margin-bottom:16px">&#9889; ZipPasswordCrack.in</div>
<h2 style="color:#00e676">Welcome, {username}!</h2>
<p style="font-size:14px;line-height:1.8">India ka #1 ZIP/PDF Password Recovery tool mein aapka swagat hai!<br>Ab aap apni encrypted files ke passwords recover kar sakte hain — 24/7 background mein!</p>
{vbtn}
<a href="{SITE_URL}/crack" style="display:inline-block;background:linear-gradient(135deg,#00e676,#00c6ff);color:#000;text-decoration:none;padding:14px 30px;border-radius:9px;font-weight:900;margin:10px 5px">&#128640; Pehla Job Start Karo</a>
<p style="color:#3d5268;font-size:12px;margin-top:22px">{SITE_NAME} — Sirf legal use — apni khud ki files ke liye</p>
</div></body></html>"""
    threading.Thread(target=send_email,args=(to_email,subject,html),daemon=True).start()

def email_reset_password(to_email,username,reset_token):
    subject=f"Password Reset — {SITE_NAME}"
    reset_url=f"{SITE_URL}/reset-password?token={reset_token}"
    html=f"""<!DOCTYPE html><html><head><meta charset="UTF-8"></head><body style="font-family:Arial,sans-serif;background:#030609;color:#bdd0e8;margin:0;padding:20px">
<div style="max-width:550px;margin:0 auto;background:#090f18;border:1px solid #111e30;border-radius:14px;padding:32px;text-align:center">
<div style="color:#00c6ff;font-size:22px;font-weight:900;font-family:monospace;margin-bottom:16px">&#9889; ZipPasswordCrack.in</div>
<h2 style="color:#ffc800">&#128273; Password Reset Request</h2>
<p style="font-size:14px;line-height:1.8">Hi <b style="color:#00c6ff">{username}</b>, aapne password reset request ki hai.<br>Neeche click karein naya password set karne ke liye:</p>
<a href="{reset_url}" style="display:inline-block;background:#e0304a;color:#fff;text-decoration:none;padding:14px 30px;border-radius:9px;font-weight:900;margin:14px 0">Reset Password &#8594;</a>
<p style="color:#3d5268;font-size:12px;margin-top:14px">Link 1 ghante mein expire ho jayega.<br>Agar request nahi ki to ignore karein.</p>
</div></body></html>"""
    threading.Thread(target=send_email,args=(to_email,subject,html),daemon=True).start()

# ── Job Runner ────────────────────────────────────────────────────────────────
def run_job(jid,fpath,cfg,user_id):
    cancel=threading.Event()
    with JLOCK: JOBS[jid]={"cancel":cancel}
    jset(jid,status="running")
    jlog(jid,"Started v24 ULTRA — Parallel batch engine — Smart > Dict > Brute")
    t0=time.time()
    try:
        gen=gen_master(cfg); freq=int(cfg.get("progress_every",1000))
        mode=cfg.get("mode","smart")
        est_map={"smart":5_000_000,"calendar":50_000_000,"mobile":10_000_000,
                 "dictionary":15_000_000,"keyboard":500_000,"brute":2_900_000_000,"hybrid":100_000_000_000}
        est_total=est_map.get(mode,5_000_000)
        upd_stats(jobs=1)

        def cb(n,sp,pw):
            if cancel.is_set(): return False
            el=time.time()-t0; eta="Calculating..."
            if sp>0 and est_total>0:
                rem=max(0,est_total-n); secs=int(rem/sp)
                if secs<3600: eta=f"~{secs//60}m mein"
                elif secs<86400: eta=f"~{secs//3600}h mein"
                else: eta=f"~{secs//86400}d mein"
            jset(jid,attempts=n,speed=sp,elapsed=round(el,1),
                 current_pw=(pw or "")[:120],est_eta=eta)
            if n%(freq*10)==0:
                jlog(jid,f"Trying: {n:,} | {sp:,}/s | ETA: {eta} | {(pw or '')[:35]}")
                upd_stats(attempts=freq*10)
            return True

        res=Cracker.crack(fpath,gen,cb,freq)
        if res.get("use_aes"): jset(jid,use_aes=1)

        if res.get("cancelled"):
            jset(jid,status="cancelled",elapsed=res["elapsed"],speed=res["speed"],
                 finished_at=datetime.now().isoformat())
            jlog(jid,f"Cancelled after {res['attempts']:,} attempts")
            upd_stats(attempts=res["attempts"])
        elif res.get("found"):
            pw=res["password"]
            jset(jid,status="found",found_pw=pw,attempts=res["attempts"],
                 elapsed=res["elapsed"],speed=res["speed"],est_eta="Mil gaya!",
                 finished_at=datetime.now().isoformat())
            jlog(jid,f"PASSWORD FOUND: {pw}")
            jlog(jid,f"Speed: {res['speed']:,}/s | Attempts: {res['attempts']:,} | Time: {fmt_t(res['elapsed'])}")
            upd_stats(cracked=1,attempts=res["attempts"])
            # Email notification
            try:
                c2=get_db()
                u_row=row2dict(c2.execute("SELECT username,email,notif_email FROM users WHERE id=?",(user_id,)).fetchone())
                j_row=row2dict(c2.execute("SELECT filename FROM jobs WHERE id=?",(jid,)).fetchone())
                c2.close()
                if u_row and u_row.get("notif_email") and u_row.get("email"):
                    email_password_found(u_row["email"],u_row["username"],
                                         j_row["filename"] if j_row else "file",pw,jid)
                    jset(jid,notified=1)
                    jlog(jid,f"Email notification sent to {u_row['email']}")
            except Exception as e: L.error(f"Email notify: {e}")
            # Extract ZIP
            if cfg.get("file_type","")=="zip":
                dl_zip=str(DLDIR/(jid+"_extracted.zip"))
                er=Cracker.extract_and_zip(fpath,pw,dl_zip)
                if er["ok"]:
                    jset(jid,dl_ready=1,dl_path=dl_zip)
                    jlog(jid,f"Files extracted: {len(er['files'])} files ready")
                else: jlog(jid,f"Extract error: {er.get('error','')}")
        else:
            jset(jid,status="failed",attempts=res.get("attempts",0),
                 elapsed=res.get("elapsed",0),speed=res.get("speed",0),
                 est_eta="Not found",finished_at=datetime.now().isoformat())
            jlog(jid,f"Not found after {res.get('attempts',0):,} attempts in {fmt_t(res.get('elapsed',0))}")
            upd_stats(attempts=res.get("attempts",0))
        if res.get("error"): jlog(jid,f"Engine error: {res['error']}")
    except Exception as e:
        L.error(f"Job {jid}: {e}\n{traceback.format_exc()}")
        jset(jid,status="error",finished_at=datetime.now().isoformat())
        jlog(jid,f"Fatal: {e}")
    finally:
        try:
            if Path(fpath).exists(): Path(fpath).unlink(); L.info(f"Deleted: {fpath}")
        except: pass
        with JLOCK: JOBS.pop(jid,None)

# ── HTML Helpers ──────────────────────────────────────────────────────────────
def ads_head():
    if not ADS_CLIENT: return ""
    return f'<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client={ADS_CLIENT}" crossorigin="anonymous"></script>'

def ads_block():
    if not ADS_CLIENT or not ADS_SLOT: return ""
    return (f'<div style="text-align:center;margin:.8rem 0">'
            f'<ins class="adsbygoogle" style="display:block" data-ad-client="{ADS_CLIENT}" '
            f'data-ad-slot="{ADS_SLOT}" data-ad-format="auto" data-full-width-responsive="true"></ins>'
            f'<script>(adsbygoogle=window.adsbygoogle||[]).push({{}});</script></div>')

GOOGLE_SVG='<svg width="18" height="18" viewBox="0 0 24 24"><path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/><path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/><path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/><path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/></svg>'

def google_btn_html():
    if not GOOGLE_CLIENT_ID: return ""
    return (f'<a href="/auth/google" style="display:flex;align-items:center;justify-content:center;'
            f'gap:.6rem;background:#fff;color:#333;border:1px solid #ddd;border-radius:8px;'
            f'padding:.6rem 1rem;font-weight:600;font-size:.85rem;text-decoration:none;'
            f'width:100%;margin:.5rem 0;box-sizing:border-box">'
            f'{GOOGLE_SVG} Continue with Google</a>'
            f'<div style="display:flex;align-items:center;gap:.5rem;margin:.7rem 0;color:#3d5268;font-size:.75rem">'
            f'<span style="flex:1;height:1px;background:#111e30"></span>ya<span style="flex:1;height:1px;background:#111e30"></span></div>')

CSS="""<style>
:root{--bg:#030609;--bg2:#090f18;--bg3:#05090f;--c1:#00c6ff;--c2:#00e676;--c3:#ffc800;
--c4:#ff6b35;--c5:#e0304a;--dim:#3d5268;--body:#bdd0e8;--ln:#111e30;--ln2:#0a1520;--wh:#fff}
*{margin:0;padding:0;box-sizing:border-box}html{scroll-behavior:smooth}
body{background:var(--bg);color:var(--body);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;min-height:100vh}
a{color:var(--c1);text-decoration:none}a:hover{color:var(--c2)}
nav{background:rgba(3,6,9,.97);border-bottom:1px solid var(--ln);height:56px;display:flex;align-items:center;justify-content:space-between;padding:0 1.5rem;position:sticky;top:0;z-index:200;backdrop-filter:blur(12px)}
.logo{color:var(--c1);font-weight:900;font-family:monospace;font-size:.95rem}
.logo span{color:var(--wh)}
.navr{display:flex;gap:.8rem;align-items:center;flex-wrap:wrap}
.navr a{color:var(--dim);font-size:.82rem;font-weight:600;transition:.2s}.navr a:hover{color:var(--c1)}
.nbtn{background:var(--c1)!important;color:#000!important;padding:.32rem .9rem;border-radius:6px;font-weight:700!important}
.wrap{max-width:1000px;margin:0 auto;padding:1.8rem 1.2rem 5rem}
h1{font-size:1.55rem;font-weight:800;color:var(--wh);margin-bottom:.2rem}
.sub{color:var(--dim);font-size:.82rem;margin-bottom:1.3rem}
.card{background:var(--bg2);border:1px solid var(--ln);border-radius:12px;padding:1.3rem;margin-bottom:.9rem;transition:.2s}
.card:hover{border-color:rgba(0,198,255,.2)}
.ct{font-size:.87rem;font-weight:700;color:var(--wh);margin-bottom:.85rem;display:flex;align-items:center;gap:.35rem}
.fg{margin-bottom:.85rem}
label{display:block;font-size:.7rem;font-weight:600;color:var(--dim);text-transform:uppercase;letter-spacing:.06em;margin-bottom:.27rem}
input,select,textarea{width:100%;background:var(--bg3);border:1px solid var(--ln);color:var(--body);padding:.58rem .85rem;border-radius:7px;font-family:inherit;font-size:.87rem;outline:none;transition:.2s}
input:focus,select:focus,textarea:focus{border-color:var(--c1);box-shadow:0 0 0 3px rgba(0,198,255,.1)}
input[type=checkbox]{width:auto;accent-color:var(--c1)}
.btn{display:inline-flex;align-items:center;gap:.3rem;padding:.58rem 1.15rem;border-radius:7px;border:none;cursor:pointer;font-size:.85rem;font-weight:700;font-family:inherit;transition:all .2s;text-decoration:none}
.bp{background:var(--c1);color:#000}.bp:hover{opacity:.88;transform:translateY(-1px)}
.bg_{background:var(--c2);color:#000}.bd{background:var(--c5);color:var(--wh)}
.bo{background:transparent;border:1px solid var(--ln);color:var(--body)}.bo:hover{border-color:var(--c1);color:var(--c1)}
.bsm{padding:.27rem .72rem;font-size:.75rem}.bw{width:100%;justify-content:center}
.badge{display:inline-block;padding:.12rem .52rem;border-radius:4px;font-size:.67rem;font-weight:700}
.sr{background:rgba(0,198,255,.13);color:var(--c1);animation:pulse 1.5s infinite}
.sf{background:rgba(0,230,118,.13);color:var(--c2)}
.se,.sx{background:rgba(224,48,74,.13);color:var(--c5)}
.sq{background:rgba(255,200,0,.13);color:var(--c3)}.sc{background:rgba(61,82,104,.13);color:var(--dim)}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.45}}
.pgw{background:var(--bg3);border-radius:99px;height:6px;overflow:hidden;margin:.35rem 0}
.pgf{height:100%;border-radius:99px;background:linear-gradient(90deg,var(--c1),var(--c2));transition:width .5s;min-width:3px}
.g2{display:grid;grid-template-columns:1fr 1fr;gap:.85rem}
.g3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:.85rem}
.g4{display:grid;grid-template-columns:repeat(4,1fr);gap:.75rem;margin-bottom:.9rem}
@media(max-width:640px){.g2,.g4{grid-template-columns:1fr 1fr}.g3{grid-template-columns:1fr}}
.stat{background:var(--bg2);border:1px solid var(--ln);border-radius:10px;padding:.85rem;text-align:center;transition:.2s}
.stat:hover{border-color:rgba(0,198,255,.25);transform:translateY(-2px)}
.sv{font-size:1.3rem;font-weight:900;color:var(--c1);font-family:monospace}
.sl{font-size:.62rem;color:var(--dim);text-transform:uppercase;letter-spacing:.07em;margin-top:.18rem}
.ae{background:rgba(224,48,74,.08);border:1px solid rgba(224,48,74,.25);color:#f07080;border-radius:7px;padding:.65rem .85rem;margin:.5rem 0;font-size:.82rem}
.ao{background:rgba(0,230,118,.08);border:1px solid rgba(0,230,118,.25);color:#70f090;border-radius:7px;padding:.65rem .85rem;margin:.5rem 0;font-size:.82rem}
.ai{background:rgba(0,198,255,.08);border:1px solid rgba(0,198,255,.25);color:#70d8ff;border-radius:7px;padding:.65rem .85rem;margin:.5rem 0;font-size:.82rem}
.aw{background:rgba(255,200,0,.08);border:1px solid rgba(255,200,0,.25);color:var(--c3);border-radius:7px;padding:.85rem;margin:.5rem 0;font-size:.82rem}
table{width:100%;border-collapse:collapse}
th{text-align:left;font-size:.67rem;color:var(--dim);text-transform:uppercase;letter-spacing:.07em;padding:.52rem .85rem;border-bottom:1px solid var(--ln)}
td{padding:.72rem .85rem;border-bottom:1px solid rgba(17,30,48,.5);font-size:.82rem;vertical-align:middle}
tr:hover td{background:rgba(0,198,255,.02)}.mono{font-family:monospace}
.fbox{background:rgba(0,230,118,.05);border:2px solid var(--c2);border-radius:14px;padding:1.6rem;text-align:center;margin:.85rem 0;box-shadow:0 0 40px rgba(0,230,118,.15)}
.fpw{font-family:monospace;font-size:2rem;font-weight:700;color:var(--c2);word-break:break-all;text-shadow:0 0 25px rgba(0,230,118,.5)}
.term{background:#000;border:1px solid var(--ln2);border-radius:8px;padding:.85rem;font-family:monospace;font-size:.72rem;color:var(--c1);max-height:260px;overflow-y:auto;line-height:1.65}
.tok{color:var(--c2)}.terr{color:var(--c5)}
.mg{display:grid;grid-template-columns:repeat(auto-fit,minmax(108px,1fr));gap:.52rem}
.mc{background:var(--bg3);border:2px solid var(--ln);border-radius:9px;padding:.82rem;cursor:pointer;transition:all .2s;text-align:center;user-select:none}
.mc:hover,.mc.sel{border-color:var(--c1);background:rgba(0,198,255,.06);transform:translateY(-1px)}
.mi{font-size:1.45rem;margin-bottom:.2rem}.mn_{font-size:.74rem;font-weight:700;color:var(--wh)}.md_{font-size:.61rem;color:var(--dim);margin-top:.1rem}
.ckg{display:flex;flex-wrap:wrap;gap:.3rem}
.ckl{display:flex;align-items:center;background:var(--bg3);border:1px solid var(--ln);padding:.28rem .68rem;border-radius:5px;cursor:pointer;font-size:.76rem;transition:.2s;gap:.28rem}
.ckl:hover{border-color:var(--c1);color:var(--c1)}
.dz{border:2px dashed var(--ln);border-radius:10px;padding:2.2rem;text-align:center;cursor:pointer;transition:all .2s;background:rgba(5,9,15,.5)}
.dz:hover,.dz.drag{border-color:var(--c1);background:rgba(0,198,255,.04)}
.hidden{display:none!important}
.rtbox{background:#000;border:2px solid var(--c3);border-radius:10px;padding:1rem;margin:.6rem 0;font-family:monospace}
.rt-label{color:var(--dim);font-size:.63rem;text-transform:uppercase;letter-spacing:.08em;margin-bottom:.3rem}
.rt-pw{color:var(--c3);font-size:1.2rem;font-weight:700;word-break:break-all}
.rt-info{color:var(--dim);font-size:.68rem;margin-top:.3rem}
.dlbox{background:rgba(0,230,118,.05);border:1px solid rgba(0,230,118,.3);border-radius:9px;padding:1rem;margin:.6rem 0;text-align:center}
.auth-wrap{min-height:calc(100vh - 56px);display:flex;align-items:center;justify-content:center;padding:2rem}
.auth-card{background:var(--bg2);border:1px solid var(--ln);border-radius:14px;padding:2rem;width:100%;max-width:440px}
.legal-box{background:rgba(255,100,0,.07);border:1.5px solid rgba(255,100,0,.3);border-radius:9px;padding:1.1rem;margin:.7rem 0}
.legal-box h3{color:var(--c4);font-size:.88rem;margin-bottom:.45rem}
.legal-box p{font-size:.78rem;color:#cc8060;line-height:1.7}
.eta-box{background:rgba(255,200,0,.07);border:1px solid rgba(255,200,0,.2);border-radius:8px;padding:.85rem 1.1rem;margin:.5rem 0;display:flex;align-items:center;gap:.8rem}
.prog-wrap{display:none;margin:.5rem 0}
.prog-bar{background:var(--bg3);border-radius:5px;height:8px;overflow:hidden}
.prog-fill{height:100%;background:linear-gradient(90deg,var(--c1),var(--c2));width:0%;transition:width .3s;border-radius:5px}
.tool-card{background:var(--bg2);border:1px solid var(--ln);border-radius:10px;padding:1.2rem;text-align:center;cursor:pointer;transition:all .2s}
.tool-card:hover{border-color:var(--c1);transform:translateY(-2px);box-shadow:0 4px 20px rgba(0,198,255,.1)}
.tool-icon{font-size:2rem;margin-bottom:.4rem}.tool-name{font-size:.85rem;font-weight:700;color:var(--wh)}.tool-desc{font-size:.68rem;color:var(--dim);margin-top:.2rem}
.seo-hidden{display:none}
@media(max-width:480px){nav{padding:0 .8rem}.logo{font-size:.82rem}.nbtn{padding:.28rem .6rem;font-size:.75rem}.navr{gap:.5rem}}
</style>"""

SEO_META="""<meta name="description" content="India ka #1 ZIP aur PDF Password Recovery Tool — ZipPasswordCrack.in. Bhula hua ZIP password recover karo. 10,000+ passwords/sec. Free online.">
<meta name="keywords" content="zip password recovery,pdf password remove,zip file password unlock,encrypted zip open,zip password finder india,pdf password recovery online free,zip password crack online,forget zip password,recover zip password,unlock pdf file,zip password breaker,zip unlock tool india,zip file ka password kaise tode,pdf password bhul gaye,encrypted pdf unlock,best zip password recovery india,zip password cracker,zip unlocker,pdf unlocker,password recovery tool india">
<meta name="robots" content="index,follow,max-snippet:-1,max-image-preview:large">
<meta name="author" content="ZipPasswordCrack.in">
<meta name="language" content="Hindi,English">
<meta name="geo.region" content="IN">
<meta property="og:title" content="ZipPasswordCrack.in — India's #1 ZIP PDF Password Recovery">
<meta property="og:description" content="Apni encrypted ZIP ya PDF file ka password recover karo. Free, 10k+/sec.">
<meta property="og:type" content="website">
<meta property="og:url" content="https://zippasswordcrack.in">
<link rel="canonical" href="https://zippasswordcrack.in">
<script type="application/ld+json">{"@context":"https://schema.org","@type":"WebApplication","name":"ZipPasswordCrack.in","url":"https://zippasswordcrack.in","description":"India ka best ZIP aur PDF password recovery tool","applicationCategory":"UtilitiesApplication","operatingSystem":"Web","offers":{"@type":"Offer","price":"0","priceCurrency":"INR"}}</script>"""

JSCMN="<script>function q(id){return document.getElementById(id)}function chk(sel){return [...document.querySelectorAll(sel+':checked')].map(c=>c.value)}</script>"

def nav_html(li=False,un="",avatar=""):
    avt=(f'<img src="{avatar}" style="width:26px;height:26px;border-radius:50%;border:2px solid var(--c1)" onerror="this.style.display=\'none\'">'
         if avatar else "")
    if li:
        return (f'<nav><a href="/" class="logo">&#9889; Zip<span>Password</span>Crack.in</a>'
                f'<div class="navr"><a href="/dashboard">Dashboard</a><a href="/crack">+ Job</a>'
                f'<a href="/tools">Tools</a><a href="/profile">Profile</a>'
                f'{avt}<span style="color:var(--dim);font-size:.78rem">Hi <b style="color:var(--c1)">{un}</b></span>'
                f'<a href="/logout" class="nbtn">Logout</a></div></nav>')
    return ('<nav><a href="/" class="logo">&#9889; Zip<span>Password</span>Crack.in</a>'
            '<div class="navr"><a href="/login">Login</a><a href="/tools">Tools</a>'
            '<a href="/register" class="nbtn">Register Free</a></div></nav>')

def page(body,title="ZipPasswordCrack.in — ZIP PDF Password Recovery",
         li=False,un="",js="",extra_head="",avatar=""):
    return ("<!DOCTYPE html><html lang='hi'><head>"
            "<meta charset='UTF-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{title}</title>"+SEO_META+ads_head()+extra_head+CSS+
            "</head><body>"+nav_html(li,un,avatar)+body+JSCMN+
            (f"<script>{js}</script>" if js else "")+"</body></html>")

# ══ ROUTES ════════════════════════════════════════════════════════════════════

@app.route("/",methods=["GET","HEAD"])
def home():
    try:
        u=me(); gs=get_stats()
        tc=gs.get("total_cracked",0); ta=gs.get("total_attempts",0); tj=gs.get("total_jobs",0)
        hero=(
            '<section style="min-height:calc(100vh - 56px);display:flex;align-items:center;'
            'background:radial-gradient(ellipse at 20% 50%,rgba(0,198,255,.06) 0%,transparent 60%),'
            'radial-gradient(ellipse at 80% 20%,rgba(0,230,118,.05) 0%,transparent 50%)">'
            '<div class="wrap" style="text-align:center;width:100%">'
            +ads_block()+
            '<div style="display:inline-block;background:rgba(0,198,255,.1);border:1px solid rgba(0,198,255,.3);'
            'border-radius:20px;padding:.3rem .85rem;font-size:.72rem;color:var(--c1);font-weight:700;'
            'margin-bottom:1rem">&#127470;&#127475; INDIA\'S #1 PASSWORD RECOVERY TOOL</div>'
            '<h1 style="font-size:clamp(1.8rem,5vw,3rem);line-height:1.1;margin-bottom:.7rem;color:var(--wh)">'
            'Apni ZIP/PDF File ka<br><span style="color:var(--c1)">Password Recover Karo</span></h1>'
            '<p style="color:var(--dim);max-width:580px;margin:0 auto 1rem;font-size:.95rem;line-height:1.8">'
            '<b style="color:var(--wh)">10,000+</b> passwords/second ki speed. '
            'Background mein 24/7 kaam. Password milte hi <b style="color:var(--c2)">email notification</b>.</p>'
            '<div class="legal-box" style="max-width:560px;margin:0 auto .9rem;text-align:left">'
            '<h3>&#9888;&#65039; Sirf Legal Use — Apni Khud Ki File</h3>'
            '<p>Kisi doosre ki file pe use karna India IT Act 2000 ke tehat illegal hai.</p></div>'
            '<div style="display:flex;gap:.65rem;justify-content:center;flex-wrap:wrap;margin-bottom:1.3rem">'
            '<a href="/register" class="btn bp" style="padding:.85rem 2.2rem;font-size:.95rem">&#128640; Register (Free)</a>'
            '<a href="/login" class="btn bo" style="padding:.85rem 2rem;font-size:.95rem">Login</a>'
            '<a href="/tools" class="btn bo" style="padding:.85rem 1.5rem;font-size:.9rem">&#128295; Free Tools</a>'
            '</div>'
            '<div style="background:var(--bg2);border:1px solid var(--ln);border-radius:14px;padding:1.2rem;max-width:540px;margin:0 auto 1.2rem">'
            '<div style="font-size:.67rem;color:var(--dim);text-transform:uppercase;letter-spacing:.08em;margin-bottom:.55rem">&#127758; Live Global Counter</div>'
            '<div style="display:flex;gap:1.5rem;justify-content:center;font-family:monospace;flex-wrap:wrap">'
            f'<div><div style="font-size:1.6rem;font-weight:900;color:var(--c2)" id="gs-c">{tc:,}</div><div style="font-size:.6rem;color:var(--dim)">Passwords Found</div></div>'
            f'<div><div style="font-size:1.6rem;font-weight:900;color:var(--c1)" id="gs-a">{ta:,}</div><div style="font-size:.6rem;color:var(--dim)">Attempts</div></div>'
            f'<div><div style="font-size:1.6rem;font-weight:900;color:var(--c3)" id="gs-j">{tj:,}</div><div style="font-size:.6rem;color:var(--dim)">Jobs</div></div>'
            '</div></div>'
            '<div class="g4" style="max-width:600px;margin:0 auto 1.2rem">'
            '<div class="stat"><div class="sv" style="color:var(--c2);font-size:.85rem">10k-50k/s</div><div class="sl">ZIP Speed</div></div>'
            '<div class="stat"><div class="sv" style="color:var(--c3);font-size:.82rem">100T+</div><div class="sl">Combos</div></div>'
            '<div class="stat"><div class="sv" style="font-size:.85rem">15+</div><div class="sl">Word Lists</div></div>'
            '<div class="stat"><div class="sv" style="color:var(--c3);font-size:.82rem">24/7</div><div class="sl">Background</div></div>'
            '</div>'
            '<div class="seo-hidden"><h2>ZIP Password Recovery Online Free India</h2>'
            '<h2>PDF Password Remove Online Free</h2><h2>ZIP File Ka Password Bhul Gaye? Recover Karo</h2></div>'
            '</div></section>'
        )
        js=("setInterval(()=>fetch('/api/stats').then(r=>r.json()).then(d=>{"
            "if(q('gs-c'))q('gs-c').textContent=d.total_cracked.toLocaleString();"
            "if(q('gs-a'))q('gs-a').textContent=d.total_attempts.toLocaleString();"
            "if(q('gs-j'))q('gs-j').textContent=d.total_jobs.toLocaleString();"
            "}),10000);")
        return page(hero,li=bool(u),un=u.get("username","") if u else "",avatar=u.get("avatar","") if u else "",js=js)
    except Exception as e:
        L.error(f"Home: {e}\n{traceback.format_exc()}")
        return page('<div class="wrap" style="text-align:center;padding-top:3rem"><h1>ZipPasswordCrack.in</h1>'
                    '<p class="sub">Loading...</p><a href="/login" class="btn bp" style="margin-top:1rem">Login</a>'
                    ' <a href="/register" class="btn bo" style="margin-top:1rem;margin-left:.5rem">Register</a></div>')

@app.route("/terms")
def terms():
    body=('<div class="wrap"><h1>Terms of Service &amp; Privacy Policy</h1>'
          '<div class="legal-box"><h3>&#9888; LEGAL WARNING</h3>'
          '<p>Sirf apni khud ki files. Unauthorized = IT Act 2000 = jail + fine.</p></div>'
          '<div style="background:var(--bg3);border:1px solid var(--ln);border-radius:8px;padding:1.2rem;font-size:.8rem;line-height:1.85;color:#8099b8">'
          '<p><b style="color:var(--c1)">1. Legal Use Only</b> — Sirf apni files.</p>'
          '<p><b style="color:var(--c1)">2. Privacy</b> — Files crack ke baad delete. No third-party sharing.</p>'
          '<p><b style="color:var(--c1)">3. Email</b> — Sirf notifications. No spam.</p>'
          '<p><b style="color:var(--c1)">4. Google Login</b> — OAuth sirf login ke liye.</p>'
          '<p><b style="color:var(--c1)">5. Governing Law</b> — India. Updated: 2025.</p>'
          '</div><div style="margin-top:1rem"><a href="/register" class="btn bp">Register</a> '
          '<a href="/" class="btn bo" style="margin-left:.5rem">Back</a></div></div>')
    return page(body,"Terms — ZipPasswordCrack.in")

@app.route("/register",methods=["GET","POST"])
def register():
    if me(): return redirect("/dashboard")
    err=""; suc=""
    if request.method=="POST":
        un=(request.form.get("username") or "").strip()
        pw=(request.form.get("password") or "").strip()
        em=(request.form.get("email") or "").strip().lower()
        dn=(request.form.get("display_name") or un).strip()
        if   not request.form.get("terms"):  err="Terms accept karna zaroori."
        elif not request.form.get("legal"):  err="Legal confirmation zaroori."
        elif len(un)<3:                      err="Username 3+ chars chahiye."
        elif not un.replace("_","").replace("-","").isalnum(): err="Username: letters/numbers/_/- only."
        elif len(pw)<6:                      err="Password 6+ chars chahiye."
        elif em and "@" not in em:           err="Valid email daalo."
        else:
            try:
                vt=secrets.token_urlsafe(32); c=get_db()
                c.execute("INSERT INTO users(username,password,email,display_name,terms_accepted,email_token,login_type) VALUES(?,?,?,?,1,?,'password')",
                          (un,hp(pw),em or None,dn,vt))
                c.commit(); c.close()
                if em: email_welcome(em,un,vt)
                suc="Account ban gaya! Login karo."
            except sqlite3.IntegrityError: err="Username ya email already exists."
            except Exception as e: err=str(e)

    body=('<div class="auth-wrap"><div class="auth-card">'
          '<h2 style="color:var(--wh);text-align:center;margin-bottom:.3rem">Register Free</h2>'
          '<p style="color:var(--dim);text-align:center;font-size:.8rem;margin-bottom:1rem">ZipPasswordCrack.in — India\'s #1</p>')
    if err: body+=f'<div class="ae">&#10060; {err}</div>'
    if suc: body+=f'<div class="ao">&#9989; {suc} <a href="/login">Login here</a></div>'
    if not suc:
        body+=(google_btn_html()+
               '<form method="POST">'
               '<div class="fg"><label>Username *</label><input name="username" required placeholder="username (3+ chars)"></div>'
               '<div class="fg"><label>Password *</label><input name="password" type="password" required placeholder="6+ chars"></div>'
               '<div class="fg"><label>Email (notifications ke liye)</label><input name="email" type="email" placeholder="aap@gmail.com"></div>'
               '<div class="fg"><label>Display Name (optional)</label><input name="display_name" placeholder="Apna naam"></div>'
               '<div class="legal-box" style="padding:.8rem"><h3>&#9888; Confirm</h3><p>Sirf apni khud ki files.</p></div>'
               '<label class="ckl" style="background:transparent;border:none;gap:.5rem;padding:.3rem 0">'
               '<input type="checkbox" name="legal" value="1" required>'
               '<span style="font-size:.79rem">Sirf <b style="color:var(--c2)">apni files</b> ke liye</span></label><br>'
               '<label class="ckl" style="background:transparent;border:none;gap:.5rem;padding:.3rem 0">'
               '<input type="checkbox" name="terms" value="1" required>'
               '<span style="font-size:.79rem"><a href="/terms" target="_blank">Terms</a> accept karta hoon</span></label>'
               '<button type="submit" class="btn bp bw" style="margin-top:.8rem">Register &#8594;</button>'
               '</form>')
    body+='<p style="text-align:center;margin-top:1rem;color:var(--dim);font-size:.82rem">Pehle se? <a href="/login">Login</a></p></div></div>'
    return page(body,"Register — ZipPasswordCrack.in")

@app.route("/login",methods=["GET","POST"])
def login():
    if "uid" in session: return redirect("/dashboard")
    err=""; info=""
    if request.args.get("verified"): info="Email verified! Login karo."
    if request.method=="POST":
        identifier=(request.form.get("identifier") or "").strip()
        pw=(request.form.get("password") or "").strip()
        try:
            c=get_db()
            u=row2dict(c.execute("SELECT * FROM users WHERE (username=? OR email=?) AND password=? AND login_type='password'",
                        (identifier,identifier.lower(),hp(pw))).fetchone())
            if u:
                c.execute("UPDATE users SET last_login=datetime('now') WHERE id=?",(u["id"],))
                c.commit(); c.close()
                session.clear(); session.permanent=True
                session["uid"]=int(u["id"]); session["uname"]=str(u["username"])
                return redirect("/dashboard")
            c.close(); err="Username/email ya password galat hai."
        except Exception as e: err=str(e); L.error(f"Login: {e}")

    body=('<div class="auth-wrap"><div class="auth-card">'
          '<h2 style="color:var(--wh);text-align:center;margin-bottom:.3rem">Login</h2>'
          '<p style="color:var(--dim);text-align:center;font-size:.8rem;margin-bottom:1rem">ZipPasswordCrack.in</p>')
    if err: body+=f'<div class="ae">&#10060; {err}</div>'
    if info: body+=f'<div class="ao">&#9989; {info}</div>'
    body+=(google_btn_html()+
           '<form method="POST">'
           '<div class="fg"><label>Username ya Email</label><input name="identifier" required placeholder="username ya email"></div>'
           '<div class="fg"><label>Password</label><input name="password" type="password" required placeholder="password"></div>'
           '<button type="submit" class="btn bp bw">Login &#8594;</button></form>'
           '<div style="text-align:center;margin-top:.8rem;font-size:.8rem;color:var(--dim)">'
           '<a href="/forgot-password">Password bhul gaye?</a></div>'
           '<p style="text-align:center;margin-top:.7rem;color:var(--dim);font-size:.82rem">Naya? <a href="/register">Register (free)</a></p>'
           '</div></div>')
    return page(body,"Login — ZipPasswordCrack.in")

@app.route("/auth/google")
def auth_google():
    if not GOOGLE_CLIENT_ID: return redirect("/login")
    state=secrets.token_urlsafe(16); session["oauth_state"]=state
    callback=f"{SITE_URL}/auth/google/callback"
    url=(f"https://accounts.google.com/o/oauth2/v2/auth?"
         f"client_id={GOOGLE_CLIENT_ID}&redirect_uri={callback}&response_type=code"
         f"&scope=openid%20email%20profile&state={state}&access_type=offline")
    return redirect(url)

@app.route("/auth/google/callback")
def auth_google_callback():
    try:
        import requests as req_lib
        code=request.args.get("code",""); state=request.args.get("state","")
        if not code or state!=session.get("oauth_state",""): return redirect("/login")
        callback=f"{SITE_URL}/auth/google/callback"
        tok_resp=req_lib.post("https://oauth2.googleapis.com/token",
            data={"code":code,"client_id":GOOGLE_CLIENT_ID,"client_secret":GOOGLE_CLIENT_SECRET,
                  "redirect_uri":callback,"grant_type":"authorization_code"},timeout=15)
        tok=tok_resp.json()
        if "error" in tok: L.error(f"Google token error: {tok}"); return redirect("/login")
        guser=req_lib.get("https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization":f"Bearer {tok['access_token']}"},timeout=15).json()
        email=guser.get("email","").lower(); name=guser.get("name","")
        google_id=guser.get("sub",""); avatar=guser.get("picture","")
        if not email: return redirect("/login")
        c=get_db()
        u=row2dict(c.execute("SELECT * FROM users WHERE email=? OR google_id=?",(email,google_id)).fetchone())
        if u:
            c.execute("UPDATE users SET last_login=datetime('now'),google_id=?,avatar=?,login_type='google' WHERE id=?",
                      (google_id,avatar,u["id"]))
            c.commit(); uid=u["id"]; uname=u["username"]
        else:
            uname=email.split("@")[0].replace(".","_")[:20]; orig=uname; i=1
            while c.execute("SELECT id FROM users WHERE username=?",(uname,)).fetchone():
                uname=f"{orig}{i}"; i+=1
            c.execute("INSERT INTO users(username,email,display_name,terms_accepted,email_verified,login_type,google_id,avatar,notif_email) VALUES(?,?,?,1,1,'google',?,?,1)",
                      (uname,email,name,google_id,avatar))
            c.commit()
            uid=row2dict(c.execute("SELECT id FROM users WHERE email=?",(email,)).fetchone())["id"]
            email_welcome(email,uname)
        c.close()
        session.clear(); session.permanent=True
        session["uid"]=int(uid); session["uname"]=str(uname)
        return redirect("/dashboard")
    except Exception as e:
        L.error(f"Google OAuth: {e}\n{traceback.format_exc()}")
        return redirect("/login")

@app.route("/forgot-password",methods=["GET","POST"])
def forgot_password():
    msg=""; err=""
    if request.method=="POST":
        em=(request.form.get("email") or "").strip().lower()
        if not em or "@" not in em: err="Valid email daalo."
        else:
            c=get_db(); u=row2dict(c.execute("SELECT * FROM users WHERE email=?",(em,)).fetchone()); c.close()
            if u:
                token=secrets.token_urlsafe(32); expires=(datetime.now()+timedelta(hours=1)).isoformat()
                c=get_db(); c.execute("UPDATE users SET reset_token=?,reset_expires=? WHERE id=?",
                                      (token,expires,u["id"])); c.commit(); c.close()
                email_reset_password(em,u["username"],token)
            msg="Agar email registered hai to reset link bheja gaya hai. (Spam bhi check karo)"
    body=('<div class="auth-wrap"><div class="auth-card">'
          '<h2 style="color:var(--wh);text-align:center;margin-bottom:.3rem">&#128273; Password Reset</h2>')
    if err: body+=f'<div class="ae">&#10060; {err}</div>'
    if msg: body+=f'<div class="ao">&#128231; {msg}</div>'
    if not msg:
        body+=('<form method="POST"><div class="fg"><label>Registered Email</label>'
               '<input name="email" type="email" required placeholder="aap@gmail.com"></div>'
               '<button type="submit" class="btn bp bw">Reset Link Bhejo</button></form>')
    body+='<p style="text-align:center;margin-top:1rem;color:var(--dim);font-size:.82rem"><a href="/login">&#8592; Back to Login</a></p></div></div>'
    return page(body,"Forgot Password — ZipPasswordCrack.in")

@app.route("/reset-password",methods=["GET","POST"])
def reset_password():
    token=request.args.get("token",""); err=""; suc=""
    try:
        c=get_db(); u=row2dict(c.execute("SELECT * FROM users WHERE reset_token=?",(token,)).fetchone()); c.close()
    except: u=None
    if not u or not token: return redirect("/login")
    if datetime.now()>datetime.fromisoformat(u.get("reset_expires") or "2000-01-01"): return redirect("/forgot-password")
    if request.method=="POST":
        pw=(request.form.get("password") or "").strip(); pw2=(request.form.get("password2") or "").strip()
        if len(pw)<6: err="Password 6+ chars chahiye."
        elif pw!=pw2: err="Passwords match nahi karte."
        else:
            c=get_db(); c.execute("UPDATE users SET password=?,reset_token='',reset_expires='' WHERE id=?",
                                  (hp(pw),u["id"])); c.commit(); c.close()
            suc="Password reset ho gaya! Login karo."
    body='<div class="auth-wrap"><div class="auth-card"><h2 style="color:var(--wh);text-align:center">Naya Password Set Karo</h2>'
    if err: body+=f'<div class="ae">&#10060; {err}</div>'
    if suc: body+=f'<div class="ao">&#9989; {suc} <a href="/login">Login</a></div>'
    if not suc:
        body+=(f'<form method="POST"><input type="hidden" name="token" value="{token}">'
               '<div class="fg"><label>Naya Password</label><input name="password" type="password" required placeholder="6+ chars"></div>'
               '<div class="fg"><label>Confirm Password</label><input name="password2" type="password" required></div>'
               '<button type="submit" class="btn bp bw">Password Reset Karo</button></form>')
    body+='</div></div>'
    return page(body,"Reset Password — ZipPasswordCrack.in")

@app.route("/verify-email")
def verify_email():
    token=request.args.get("token","")
    try:
        c=get_db(); u=row2dict(c.execute("SELECT * FROM users WHERE email_token=?",(token,)).fetchone())
        if u: c.execute("UPDATE users SET email_verified=1,email_token='' WHERE id=?",(u["id"],)); c.commit()
        c.close()
    except: pass
    return redirect("/login?verified=1")

@app.route("/logout")
def logout(): session.clear(); return redirect("/")

@app.route("/dashboard")
@rlogin
def dashboard():
    try:
        u=me()
        if not u: session.clear(); return redirect("/login")
        c=get_db()
        rows=c.execute("SELECT * FROM jobs WHERE user_id=? ORDER BY created_at DESC LIMIT 50",(u["id"],)).fetchall()
        jobs=[row2dict(r) for r in rows]  # FIX: convert all rows to dict
        c.close()
        total=len(jobs)
        fc=sum(1 for j in jobs if j.get("status")=="found")
        fl=sum(1 for j in jobs if j.get("status")=="failed")
        rn=sum(1 for j in jobs if j.get("status") in ("running","queued"))
        gs=get_stats(); tbl=""
        for j in jobs:
            pw_val=j.get("found_pw") or ""
            if pw_val:
                pc=(f'<span style="color:var(--c2);font-family:monospace;cursor:pointer;font-size:.82rem" '
                    f'onclick="navigator.clipboard.writeText(this.dataset.pw);this.textContent=\'Copied!\'" '
                    f'data-pw="{pw_val}">{pw_val}</span>')
            else: pc="&mdash;"
            jid=j.get("id","")
            stop=(f'<button onclick="stopJ(\'{jid}\')" class="btn bd bsm" style="margin-left:.2rem">Stop</button>'
                  if j.get("status") in ("running","queued") else "")
            dl=(f'<a href="/dl/{jid}" class="btn bg_ bsm" style="margin-left:.2rem">&#8595; Files</a>'
                if j.get("status")=="found" and j.get("dl_ready") else "")
            view_link=f'<a href="/job/{jid}" class="btn bo bsm">View</a>'
            bc=("sr" if j.get("status")=="running" else "sf" if j.get("status")=="found" else
                "se" if j.get("status") in ("failed","error") else "sq" if j.get("status")=="queued" else "sc")
            eta_cell=(f'<br><span style="color:var(--c3);font-size:.65rem">ETA: {j.get("est_eta","")}</span>'
                      if j.get("status") in ("running","queued") and j.get("est_eta") else "")
            fname=(j.get("filename") or "")[:22]
            tbl+=(f'<tr><td class="mono" style="max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{fname}</td>'
                   f'<td><span class="badge {bc}">{j.get("status","")}</span>{eta_cell}</td>'
                   f'<td>{j.get("mode") or ""}</td><td class="mono">{(j.get("attempts") or 0):,}</td>'
                   f'<td class="mono">{(j.get("speed") or 0):,}/s</td><td>{pc}</td>'
                   f'<td class="mono">{fmt_t(j.get("elapsed",0))}</td><td>{view_link}{stop}{dl}</td></tr>')
        table=(f'<div style="overflow-x:auto"><table><thead><tr>'
               f'<th>File</th><th>Status</th><th>Mode</th><th>Attempts</th>'
               f'<th>Speed</th><th>Password</th><th>Time</th><th>Actions</th>'
               f'</tr></thead><tbody>{tbl}</tbody></table></div>'
               if jobs else '<p style="color:var(--dim);text-align:center;padding:1.5rem">Koi job nahi. <a href="/crack">Pehla job start karo</a></p>')
        js=("function stopJ(id){if(!confirm('Cancel?'))return;"
            "fetch('/api/cancel/'+id,{method:'POST'}).then(r=>r.json()).then(d=>{if(d.ok)location.reload();else alert(d.error||'err')})}"
            +("setTimeout(()=>location.reload(),5000);" if rn else "")
            +";setInterval(()=>fetch('/api/stats').then(r=>r.json()).then(d=>{"
            "if(q('gs-c'))q('gs-c').textContent=d.total_cracked.toLocaleString();"
            "if(q('gs-a'))q('gs-a').textContent=d.total_attempts.toLocaleString();"
            "}),8000);")
        avt=(f'<img src="{u.get("avatar","")}" style="width:40px;height:40px;border-radius:50%;border:2px solid var(--c1);margin-right:.5rem" onerror="this.style.display=\'none\'">'
             if u.get("avatar") else "")
        body=(f'<div class="wrap">'
              +ads_block()+
              f'<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:1rem;flex-wrap:wrap;gap:.5rem">'
              f'<div style="display:flex;align-items:center">{avt}<div>'
              f'<h1>My Dashboard</h1>'
              f'<p class="sub">Hi <b style="color:var(--c1)">{u.get("display_name") or u.get("username","")}</b> &#128075; — Background mein 24/7!</p>'
              f'</div></div><a href="/crack" class="btn bp">+ New Job</a></div>'
              f'<div style="background:var(--bg2);border:1px solid var(--ln);border-radius:8px;padding:.8rem 1rem;margin-bottom:.9rem">'
              f'<div style="display:flex;gap:2rem;font-family:monospace;font-size:.82rem;flex-wrap:wrap">'
              f'<div>Global Cracked: <b style="color:var(--c2)" id="gs-c">{gs.get("total_cracked",0):,}</b></div>'
              f'<div>Attempts: <b style="color:var(--c1)" id="gs-a">{gs.get("total_attempts",0):,}</b></div>'
              f'<div>Total Jobs: <b style="color:var(--c3)">{gs.get("total_jobs",0):,}</b></div></div></div>'
              f'<div class="g4">'
              f'<div class="stat"><div class="sv">{total}</div><div class="sl">My Jobs</div></div>'
              f'<div class="stat"><div class="sv" style="color:var(--c2)">{fc}</div><div class="sl">Found</div></div>'
              f'<div class="stat"><div class="sv" style="color:var(--c5)">{fl}</div><div class="sl">Failed</div></div>'
              f'<div class="stat"><div class="sv" style="color:var(--c3)">{rn}</div><div class="sl">Running</div></div>'
              f'</div>'
              f'<div class="card"><div class="ct">&#128203; My Jobs ({total})</div>{table}</div>'
              +ads_block()+f'</div>')
        return page(body,f"Dashboard — {u.get('username','')} — ZipPasswordCrack.in",
                    True,u.get("username",""),js,avatar=u.get("avatar",""))
    except Exception as e:
        L.error(f"Dashboard: {e}\n{traceback.format_exc()}")
        # Clean error page — no raw error shown to user
        body=('<div class="wrap" style="text-align:center;padding-top:3rem">'
              '<div style="font-size:3rem;margin-bottom:.8rem">&#9889;</div>'
              '<h1 style="color:var(--wh)">Dashboard</h1>'
              '<p class="sub">Ek second mein reload hoga...</p>'
              '<a href="/dashboard" class="btn bp" style="margin-top:1rem">Reload Dashboard</a>'
              ' <a href="/logout" class="btn bo" style="margin-top:1rem;margin-left:.5rem">Logout &amp; Retry</a></div>')
        return page(body,"Dashboard — ZipPasswordCrack.in",True,session.get("uname",""))

@app.route("/crack")
@rlogin
def crack_page():
    try:
        u=me()
        def ck(items):
            h='<div class="ckg">'
            for v,l,checked in items:
                ch=" checked" if checked else ""
                h+=f'<label class="ckl"><input type="checkbox" value="{v}"{ch}>{l}</label>'
            return h+'</div>'
        cs=[("lower","a-z",True),("upper","A-Z",False),("digits","0-9",True),("sym_india","Indian Sym",False),("sym","All Sym",False),("alnum","AlphaNum",False),("full","Full ASCII",False)]
        df=[("%d%m%Y","DDMMYYYY",True),("%Y%m%d","YYYYMMDD",True),("%d%m%y","DDMMYY",True),("%d/%m/%Y","DD/MM/YYYY",True),("%d%m","DDMM",True),("%Y","YYYY",True)]
        cc=[("+91","&#127470;&#127475; India",True),("+92","&#127477;&#127472; Pakistan",False),("+880","&#127463;&#127465; Bangladesh",False),("+1","&#127482;&#127480; USA",False),("+44","&#127468;&#127463; UK",False),("+971","&#127462;&#127466; UAE",False),("+966","&#127480;&#127462; Saudi",False),("+86","&#127464;&#127475; China",False),("+62","&#127470;&#127465; Indonesia",False),("+7","&#127479;&#127482; Russia",False)]
        seps=[("","None",True),("_","_",True),("-","-",True),(".",".",True),("@","@",True),("#","#",False)]
        gh='<div class="ckg">'+",".join(f'<label class="ckl"><input type="checkbox" value="{v}">{l}</label>' for v,l in [("top1m","SecLists 1M"),("top100k","100K"),("top10k","10K"),("best1050","Best 1050"),("xato1m","Xato 1M"),("probable","Probable 12K"),("weakpass","WeakPass"),("leaked","Leaked Gmail"),("rockyou","RockYou"),("common_3k","Common 3K"),("bt4","BT4"),("darkweb","DarkWeb 10K"),("top500","Top 500"),("hak5","Hak5 10K"),("kaonashi","Ashley Madison")])+'</div>'
        body=('<div class="wrap"><h1>New Crack Job</h1>'
              '<p class="sub">Upload karo — 24/7 background mein kaam hoga. Password milne pe email aayegi!</p>'
              +ads_block()+
              '<div class="aw">&#9888; Sirf apni khud ki file. Unauthorized use illegal hai.</div>'
              '<div class="card" style="border-color:rgba(0,198,255,.3)"><div class="ct" style="color:var(--c2)">&#9889; Attack Priority + Speed</div>'
              '<div style="font-family:monospace;font-size:.76rem;line-height:2">'
              '<div>1&#65039;&#8419; Common (300+) &#8594; 2&#65039;&#8419; Google combos &#8594; 3&#65039;&#8419; Smart Personal</div>'
              '<div>4&#65039;&#8419; Calendar &#8594; 5&#65039;&#8419; Keyboard &#8594; 6&#65039;&#8419; 15 GitHub Lists &#8594; 7&#65039;&#8419; Brute</div>'
              '<div>Standard ZIP: <b style="color:var(--c2)">10k-50k/sec</b> (parallel threads) | AES-256: <b style="color:var(--c3)">~500/sec</b> (hardware limit)</div>'
              '<div style="color:var(--dim);font-size:.7rem">&#9989; File auto-delete after crack | Email notification on password found</div>'
              '</div></div>'
              '<div class="card"><div class="ct">&#128193; File Upload (Max 200MB)</div>'
              '<div class="dz" id="dz" onclick="q(\'fi\').click()" ondragover="event.preventDefault();this.classList.add(\'drag\')" ondragleave="this.classList.remove(\'drag\')" ondrop="handleDrop(event)">'
              '<div style="font-size:2.5rem;margin-bottom:.4rem">&#128194;</div>'
              '<div style="color:var(--wh);font-weight:700;font-size:1rem">ZIP ya PDF — Drop here ya Click karo</div>'
              '<div id="fn" style="color:var(--c1);margin-top:.35rem;font-size:.82rem">Max 200MB — Upload ke baad auto-delete</div>'
              '</div>'
              '<div class="prog-wrap" id="uprog">'
              '<div style="font-size:.78rem;color:var(--body);margin-bottom:.35rem" id="uprog-txt">Uploading...</div>'
              '<div class="prog-bar"><div class="prog-fill" id="uprog-fill"></div></div>'
              '</div>'
              '<input type="file" id="fi" accept=".zip,.pdf" class="hidden" onchange="showFile(this)"></div>'
              '<div class="card"><div class="ct">&#127919; Attack Mode</div><div class="mg">'
              '<div class="mc sel" onclick="selMode(\'smart\',this)"><div class="mi">&#129504;</div><div class="mn_">Smart</div><div class="md_">Best choice</div></div>'
              '<div class="mc" onclick="selMode(\'calendar\',this)"><div class="mi">&#128197;</div><div class="mn_">Calendar</div><div class="md_">Date patterns</div></div>'
              '<div class="mc" onclick="selMode(\'mobile\',this)"><div class="mi">&#128241;</div><div class="mn_">Mobile</div><div class="md_">18 countries</div></div>'
              '<div class="mc" onclick="selMode(\'dictionary\',this)"><div class="mi">&#128218;</div><div class="mn_">Dictionary</div><div class="md_">15 lists</div></div>'
              '<div class="mc" onclick="selMode(\'keyboard\',this)"><div class="mi">&#9000;</div><div class="mn_">Keyboard</div><div class="md_">Walks</div></div>'
              '<div class="mc" onclick="selMode(\'brute\',this)"><div class="mi">&#128170;</div><div class="mn_">Brute Force</div><div class="md_">100T+</div></div>'
              '<div class="mc" onclick="selMode(\'hybrid\',this)"><div class="mi">&#128293;</div><div class="mn_">Hybrid ALL</div><div class="md_">Everything</div></div>'
              '</div><input type="hidden" id="mode" value="smart"></div>'
              '<div id="p-smart" class="card"><div class="ct">&#128100; Personal Info (most effective!)</div>'
              '<div class="ai">Auto: r4hul (leet), a1v2n3 (interleaved), Rahul@786 + DOB combos. 50M+ personalised!</div>'
              '<div class="g2">'
              '<div class="fg"><label>Naam / Name</label><input id="s-name" placeholder="Rahul, Mohammed, Priya"></div>'
              '<div class="fg"><label>Nickname</label><input id="s-nick" placeholder="rocky, lucky, rinku"></div>'
              '<div class="fg"><label>Date of Birth (DD/MM/YYYY)</label><input id="s-dob" placeholder="25/12/1990"></div>'
              '<div class="fg"><label>Mobile Number</label><input id="s-mob" placeholder="+919876543210"></div>'
              '<div class="fg"><label>City</label><input id="s-city" placeholder="Mumbai, Lahore, Dubai"></div>'
              '<div class="fg"><label>Pet / Vehicle</label><input id="s-pet" placeholder="Tommy, Hero, Bullet"></div>'
              '<div class="fg"><label>Favourite</label><input id="s-fav" placeholder="Cricket, Allah, Dhoni"></div>'
              '<div class="fg"><label>Lucky Number</label><input id="s-lucky" placeholder="786, 108, 420, 999"></div>'
              '</div><div class="fg"><label>Other Keywords (comma sep)</label><input id="s-other" placeholder="company, school, koi bhi"></div></div>'
              f'<div id="p-calendar" class="card hidden"><div class="ct">&#128197; Calendar Attack</div>'
              f'<div class="ai">All date formats x prefix x suffix. Very powerful for Indian passwords.</div>'
              f'<div class="g3"><div class="fg"><label>Start Year</label><input id="c-sy" type="number" value="1940"></div>'
              f'<div class="fg"><label>End Year</label><input id="c-ey" type="number" value="2025"></div></div>'
              f'<div class="fg"><label>Prefix Words</label><textarea id="c-pre" rows="3" placeholder="rahul&#10;786&#10;maa"></textarea></div>'
              f'<div class="fg"><label>Suffix Words</label><textarea id="c-suf" rows="3" placeholder="@123&#10;786&#10;!&#10;@2024"></textarea></div>'
              f'<div class="fg"><label>Date Formats</label>{ck(df)}</div>'
              f'<div class="fg"><label>Separators</label>{ck(seps)}</div></div>'
              f'<div id="p-mobile" class="card hidden"><div class="ct">&#128241; Mobile Attack (18 Countries)</div>'
              f'<div class="ai">Specific numbers first. Density=100 = ALL numbers.</div>'
              f'<div class="fg"><label>Specific Numbers</label><textarea id="m-nums" rows="3" placeholder="+919876543210&#10;9876543210"></textarea></div>'
              f'<div class="fg"><label>Countries</label>{ck(cc)}</div>'
              f'<div class="fg"><label>Density (1-100)</label><input id="m-den" type="number" value="100" min="1" max="100"></div></div>'
              f'<div id="p-dictionary" class="card hidden"><div class="ct">&#128218; Dictionary + 15 GitHub Lists</div>'
              f'<div class="ai">Real leaked passwords + mutations. SecLists, RockYou, DarkWeb etc.</div>'
              f'<div class="fg"><label>GitHub Lists</label>{gh}</div>'
              f'<div class="fg"><label>Extra URLs/Paths</label><textarea id="d-extra" rows="2" placeholder="https://raw.github..."></textarea></div></div>'
              f'<div id="p-brute" class="card hidden"><div class="ct">&#128170; Brute Force — 100 Trillion+</div>'
              f'<div class="ai">Standard: 10k-50k/sec parallel | AES-256: ~500/sec (PBKDF2 hardware limit)<br>'
              f'a-z+0-9 len=8: 2.9T combos | Full ASCII: 6.6T</div>'
              f'<div class="fg"><label>Charset</label>{ck(cs)}</div>'
              f'<div class="fg"><label>Custom Characters</label><input id="b-cc" placeholder="@#786 extra chars"></div>'
              f'<div class="g3"><div class="fg"><label>Min Length</label><input id="b-min" type="number" value="1" min="1" max="30"></div>'
              f'<div class="fg"><label>Max Length</label><input id="b-max" type="number" value="8" min="1" max="30"></div></div>'
              f'<div class="g2"><div class="fg"><label>Prefix</label><input id="b-pre" placeholder="rahul_"></div>'
              f'<div class="fg"><label>Suffix</label><input id="b-suf" placeholder="_786"></div></div>'
              f'<div class="ai mono" id="bf-est">Calculating...</div></div>'
              '<div class="card"><div class="fg"><label>Progress Update Frequency</label>'
              '<select id="freq"><option value="500">Har 500</option><option value="1000" selected>Har 1,000</option>'
              '<option value="2000">Har 2,000</option><option value="5000">Har 5,000</option></select></div>'
              '<div class="ai" style="margin-bottom:.7rem">&#128276; Password milne pe browser notification + email aayegi</div>'
              '<button onclick="submitJob()" class="btn bp bw" style="padding:.85rem;font-size:.95rem" id="sbtn">'
              '&#128640; Crack Job Start Karo — Auto-delete + Email Alert!</button></div>'
              +ads_block()+'</div>')
        js=("var mode='smart';var panels=['smart','calendar','mobile','dictionary','brute'];"
            "function selMode(m,el){document.querySelectorAll('.mc').forEach(c=>c.classList.remove('sel'));el.classList.add('sel');mode=m;"
            "panels.forEach(p=>{var e=q('p-'+p);if(e)e.classList.toggle('hidden',p!==m&&m!=='hybrid')});"
            "if(m==='hybrid')panels.forEach(p=>{var e=q('p-'+p);if(e)e.classList.remove('hidden')});calcBF();}"
            "function showFile(inp){var f=inp.files[0];if(!f)return;"
            "if(f.size>200*1024*1024){alert('Max 200MB!');inp.value='';return;}"
            "q('fn').textContent='OK: '+f.name+' ('+(f.size/1024/1024).toFixed(1)+' MB)';}"
            "function handleDrop(e){e.preventDefault();q('dz').classList.remove('drag');"
            "if(e.dataTransfer.files[0]){q('fi').files=e.dataTransfer.files;showFile(q('fi'));}}"
            "function calcBF(){"
            "var cs=new Set((chk('#p-brute .ckg .ckl input').join('')+(q('b-cc')?q('b-cc').value:'')).split(''));"
            "var n=Math.max(cs.size,2),mn=parseInt((q('b-min')||{value:1}).value),mx=parseInt((q('b-max')||{value:8}).value);"
            "var t=0;for(var i=mn;i<=mx;i++)t+=Math.pow(n,i);"
            "var ts=Math.round(t/10000),tss=ts>86400?Math.round(ts/86400)+'d':ts>3600?Math.round(ts/3600)+'h':ts>60?Math.round(ts/60)+'m':ts+'s';"
            "var ts2=t>1e18?(t/1e18).toFixed(1)+' Quintillion':t>1e15?(t/1e15).toFixed(1)+' Quadrillion':t>1e12?(t/1e12).toFixed(1)+' Trillion':t>1e9?(t/1e9).toFixed(1)+' Billion':t>1e6?(t/1e6).toFixed(1)+' M':String(t);"
            "var el=q('bf-est');if(el)el.textContent='Estimated: '+ts2+' combinations | ~'+tss+' @ 10k/s';}"
            "if('Notification' in window)Notification.requestPermission();"
            "function submitJob(){"
            "var fi=q('fi');if(!fi.files.length){alert('Pehle file select karo!');return;}"
            "var f=fi.files[0];if(f.size>200*1024*1024){alert('Max 200MB!');return;}"
            "var cfg={mode:mode,progress_every:parseInt(q('freq').value),"
            "github_lists:chk('#p-dictionary .ckg .ckl input'),"
            "extra_wordlists:(q('d-extra')?q('d-extra').value:'').split('\\n').map(s=>s.trim()).filter(Boolean),"
            "user_info:{name:q('s-name')?q('s-name').value:'',nick:q('s-nick')?q('s-nick').value:'',"
            "dob:q('s-dob')?q('s-dob').value:'',mobile:q('s-mob')?q('s-mob').value:'',"
            "city:q('s-city')?q('s-city').value:'',pet:q('s-pet')?q('s-pet').value:'',"
            "fav:q('s-fav')?q('s-fav').value:'',lucky:q('s-lucky')?q('s-lucky').value:'',"
            "other:q('s-other')?q('s-other').value:''},"
            "calendar:{start_year:parseInt((q('c-sy')||{value:1940}).value),"
            "end_year:parseInt((q('c-ey')||{value:2025}).value),"
            "prefix_words:(q('c-pre')?q('c-pre').value:'').split('\\n').map(s=>s.trim()).filter(Boolean),"
            "suffix_words:(q('c-suf')?q('c-suf').value:'').split('\\n').map(s=>s.trim()).filter(Boolean),"
            "date_formats:chk('#p-calendar .ckg .ckl input'),"
            "separators:chk('#p-calendar .fg:last-child .ckl input')},"
            "mobile:{numbers:(q('m-nums')?q('m-nums').value:'').split('\\n').map(s=>s.trim()).filter(Boolean),"
            "country_codes:chk('#p-mobile .ckg .ckl input'),"
            "density:parseInt((q('m-den')||{value:100}).value)},"
            "brute:{charsets:chk('#p-brute .ckg .ckl input[type=checkbox]'),"
            "custom_chars:q('b-cc')?q('b-cc').value:'',"
            "min_len:parseInt((q('b-min')||{value:1}).value),"
            "max_len:parseInt((q('b-max')||{value:8}).value),"
            "prefix:q('b-pre')?q('b-pre').value:'',"
            "suffix:q('b-suf')?q('b-suf').value:''}};"
            "var fd=new FormData();fd.append('file',f);fd.append('config',JSON.stringify(cfg));"
            "var btn=q('sbtn');btn.disabled=true;btn.textContent='Uploading...';"
            "var pr=q('uprog');pr.style.display='block';"
            "var xhr=new XMLHttpRequest();"
            "xhr.upload.onprogress=function(e){if(e.lengthComputable){"
            "var pct=Math.round(e.loaded/e.total*100);"
            "q('uprog-fill').style.width=pct+'%';"
            "q('uprog-txt').textContent='Uploading: '+pct+'% — please wait...';"
            "btn.textContent='Uploading '+pct+'%...';}};"
            "xhr.onload=function(){pr.style.display='none';"
            "try{var d=JSON.parse(xhr.responseText);"
            "if(d.job_id){window.location.href='/job/'+d.job_id;}"
            "else{alert('Error: '+(d.error||'Unknown'));btn.disabled=false;btn.textContent='Retry';}}"
            "catch(e){alert('Server error. Try again.');btn.disabled=false;btn.textContent='Retry';}};"
            "xhr.onerror=function(){pr.style.display='none';alert('Network error.');btn.disabled=false;btn.textContent='Retry';};"
            "xhr.open('POST','/api/submit');xhr.send(fd);}"
            "calcBF();"
            "[q('b-min'),q('b-max'),q('b-cc')].forEach(el=>{if(el)el.addEventListener('input',calcBF)});"
            "document.querySelectorAll('#p-brute .ckg .ckl input').forEach(el=>el.addEventListener('change',calcBF));")
        return page(body,"New Crack Job — ZipPasswordCrack.in",True,u.get("username",""),js,avatar=u.get("avatar",""))
    except Exception as e:
        L.error(f"Crack page: {e}")
        return redirect("/dashboard")

@app.route("/job/<jid>")
@rlogin
def job_page(jid):
    try:
        u=me(); c=get_db()
        j=row2dict(c.execute("SELECT * FROM jobs WHERE id=? AND user_id=?",(jid,u["id"])).fetchone())
        if not j: c.close(); abort(404)
        logs=[row2dict(r) for r in c.execute("SELECT ts,msg FROM jlogs WHERE job_id=? ORDER BY id DESC LIMIT 100",(jid,)).fetchall()]
        logs=list(reversed(logs))
        c.close()
        found_html=""; pw_val=j.get("found_pw") or ""
        if pw_val:
            dl_html=""
            if j.get("dl_ready"):
                dl_html=(f'<div class="dlbox">'
                         f'<div style="color:var(--c2);font-size:.82rem;margin-bottom:.6rem">&#9989; Extracted files ready!</div>'
                         f'<a href="/dl/{jid}" class="btn bg_ bw" style="font-size:.95rem">&#8595; Download Extracted Files</a>'
                         f'<div style="color:var(--dim);font-size:.7rem;margin-top:.4rem">Auto-delete after download</div></div>')
            found_html=(f'<div class="fbox">'
                        f'<div style="font-size:.64rem;color:var(--dim);text-transform:uppercase;letter-spacing:.12em;margin-bottom:.45rem">PASSWORD MIL GAYA!</div>'
                        f'<div class="fpw" id="fpw">{pw_val}</div>'
                        f'<div style="margin-top:.7rem;display:flex;gap:.5rem;justify-content:center;flex-wrap:wrap">'
                        f'<button onclick="navigator.clipboard.writeText(document.getElementById(\'fpw\').textContent);this.textContent=\'Copied!\'" class="btn bp bsm">&#128203; Copy</button>'
                        f'</div></div>{dl_html}')
        is_run=j.get("status") in ("running","queued"); cur=j.get("current_pw") or "..."
        eta=j.get("est_eta","") or ""
        rtbox=""
        if is_run:
            rtbox=(f'<div class="rtbox"><div class="rt-label">&#128269; Currently Trying:</div>'
                   f'<div class="rt-pw" id="rt-pw">{cur}</div>'
                   f'<div class="rt-info">Attempt #<span id="rt-cnt">{(j.get("attempts") or 0):,}</span>'
                   f' | Speed: <span id="rt-spd">{(j.get("speed") or 0):,}</span>/s'
                   f' | Time: <span id="rt-el">{fmt_t(j.get("elapsed",0))}</span></div></div>')
        eta_html=""
        if eta and eta not in ("Mil gaya!","Not found","Calculating...","") and is_run:
            eta_html=(f'<div class="eta-box"><span style="font-size:1.6rem">&#9201;</span>'
                      f'<div><div style="font-size:.78rem;color:var(--body)">Estimated Time Remaining</div>'
                      f'<b style="color:var(--c3);font-size:1.1rem;font-family:monospace" id="eta-val">{eta}</b></div></div>')
        log_lines="".join(
            f'<div class="{"tok" if ("PASSWORD FOUND" in (l.get("msg") or "").upper() or "CRACKED" in (l.get("msg") or "").upper()) else "terr" if "error" in (l.get("msg") or "").lower() else ""}">'
            f'<span style="color:var(--dim)">{(l.get("ts") or "")[-8:]}</span> {l.get("msg") or ""}</div>\n'
            for l in logs)
        bc=("sr" if j.get("status")=="running" else "sf" if j.get("status")=="found" else
            "se" if j.get("status") in ("failed","error") else "sc")
        cancel_btn=('<button onclick="stopJob()" class="btn bd bw">&#9209; Cancel Job</button>' if is_run else "")
        copy_btn=(f'<button onclick="navigator.clipboard.writeText(document.getElementById(\'fpw\').textContent)" class="btn bg_ bw" style="margin-top:.5rem">&#128203; Copy Password</button>' if pw_val else "")
        aes_warn=('<div class="aw" style="font-size:.78rem">&#128274; AES-256 ZIP detected: ~500/sec (PBKDF2 hardware limit — this is normal)</div>' if j.get("use_aes") else "")
        notif_badge=(f'<span style="font-size:.72rem;color:var(--c2);margin-left:.5rem">&#128231; Email sent</span>' if j.get("notified") else "")
        body=(f'<div class="wrap">'
              +ads_block()+
              f'<div style="display:flex;align-items:center;gap:.65rem;margin-bottom:1.2rem;flex-wrap:wrap">'
              f'<a href="/dashboard" class="btn bo bsm">&#8592; Dashboard</a>'
              f'<h1 style="font-size:1.3rem">Job Details</h1>'
              f'<span class="badge {bc}">{j.get("status","")}</span>{notif_badge}</div>'
              +aes_warn+found_html+rtbox+eta_html+
              f'<div class="g4">'
              f'<div class="stat"><div class="sv mono" id="sv-a">{(j.get("attempts") or 0):,}</div><div class="sl">Attempts</div></div>'
              f'<div class="stat"><div class="sv mono" id="sv-s">{(j.get("speed") or 0):,}</div><div class="sl">Speed/s</div></div>'
              f'<div class="stat"><div class="sv mono" id="sv-t">{fmt_t(j.get("elapsed",0))}</div><div class="sl">Elapsed</div></div>'
              f'<div class="stat"><div class="sv mono" style="color:var(--c3)" id="sv-st">{j.get("status","")}</div><div class="sl">Status</div></div>'
              f'</div>'
              f'<div class="card"><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:.3rem">'
              f'<span style="font-size:.75rem;color:var(--dim)">Progress</span>'
              f'<span id="pg-l" style="font-size:.75rem;color:var(--c1)">{(j.get("attempts") or 0):,} tried</span>'
              f'</div><div class="pgw"><div class="pgf" id="pgb" style="width:2%"></div></div></div>'
              f'<div class="g2">'
              f'<div class="card"><div class="ct">&#128196; File Info</div><table>'
              f'<tr><td style="color:var(--dim);font-size:.77rem;padding:.25rem 0">File</td><td class="mono" style="font-size:.77rem">{j.get("filename") or "&mdash;"}</td></tr>'
              f'<tr><td style="color:var(--dim);font-size:.77rem;padding:.25rem 0">Type</td><td style="font-size:.77rem">{j.get("filetype") or "&mdash;"} {"(AES-256)" if j.get("use_aes") else ""}</td></tr>'
              f'<tr><td style="color:var(--dim);font-size:.77rem;padding:.25rem 0">Mode</td><td style="font-size:.77rem">{j.get("mode") or "&mdash;"}</td></tr>'
              f'<tr><td style="color:var(--dim);font-size:.77rem;padding:.25rem 0">Size</td><td style="font-size:.77rem">{(j.get("filesize") or 0)//1024} KB</td></tr>'
              f'</table></div>'
              f'<div class="card"><div class="ct">&#9881; Actions</div>'
              +cancel_btn+copy_btn+
              f'<a href="/crack" class="btn bo bw" style="margin-top:.5rem">+ Naya Job</a></div></div>'
              f'<div class="card"><div class="ct">&#128187; Activity Log (Live)</div>'
              f'<div class="term" id="lb">{log_lines}</div></div>'
              +ads_block()+'</div>')
        js=(f"var JID='{jid}',JST='{j.get('status','')}',pgA=2;"
            "function stopJob(){if(!confirm('Cancel?'))return;"
            "fetch('/api/cancel/'+JID,{method:'POST'}).then(r=>r.json()).then(d=>{if(d.ok)location.reload();else alert(d.error||'err');});}"
            "function poll(){fetch('/api/progress/'+JID).then(r=>r.json()).then(d=>{"
            "if(!d||!d.status)return;"
            "if(q('sv-a'))q('sv-a').textContent=(d.attempts||0).toLocaleString();"
            "if(q('sv-s'))q('sv-s').textContent=(d.speed||0).toLocaleString();"
            "if(q('sv-t'))q('sv-t').textContent=d.ef||'0s';"
            "if(q('sv-st'))q('sv-st').textContent=d.status;"
            "pgA=Math.min(pgA+0.18,95);if(q('pgb'))q('pgb').style.width=pgA+'%';"
            "if(q('pg-l'))q('pg-l').textContent=(d.attempts||0).toLocaleString()+' tried | '+(d.speed||0).toLocaleString()+'/s';"
            "if(q('rt-pw')&&d.cpw)q('rt-pw').textContent=d.cpw;"
            "if(q('rt-cnt'))q('rt-cnt').textContent=(d.attempts||0).toLocaleString();"
            "if(q('rt-spd'))q('rt-spd').textContent=(d.speed||0).toLocaleString();"
            "if(q('rt-el'))q('rt-el').textContent=d.ef||'0s';"
            "if(q('eta-val')&&d.eta)q('eta-val').textContent=d.eta;"
            "var lb=q('lb');"
            "if(lb&&d.logs&&d.logs.length){"
            "lb.innerHTML=d.logs.map(l=>{"
            "var c=(l.msg&&(l.msg.toUpperCase().includes('FOUND')||l.msg.toUpperCase().includes('CRACKED')))?'tok':'';"
            "return '<div class=\"'+c+'\"><span style=\"color:var(--dim)\">'+(l.ts||'').slice(-8)+'</span> '+l.msg+'</div>';"
            "}).join('');lb.scrollTop=lb.scrollHeight;}"
            "if(d.status==='found'){"
            "if('Notification' in window&&Notification.permission==='granted')"
            "new Notification('Password Mil Gaya!',{body:'Dashboard pe dekhein'});"
            "if(q('pgb'))q('pgb').style.width='100%';"
            "setTimeout(()=>location.reload(),800);}"
            "else if(d.status==='running'||d.status==='queued')setTimeout(poll,2000);"
            "}).catch(()=>setTimeout(poll,6000));}"
            "if(JST==='running'||JST==='queued')setTimeout(poll,1500);"
            "var lb=document.getElementById('lb');if(lb)lb.scrollTop=lb.scrollHeight;"
            "if('Notification' in window)Notification.requestPermission();")
        return page(body,f"Job — {j.get('filename') or 'Unknown'} — ZipPasswordCrack.in",
                    True,u.get("username",""),js,avatar=u.get("avatar",""))
    except Exception as e:
        L.error(f"Job page {jid}: {e}\n{traceback.format_exc()}")
        return page(f'<div class="wrap"><h1>Job Error</h1><p class="sub">{str(e)[:100]}</p>'
                    '<a href="/dashboard" class="btn bp" style="margin-top:1rem">Dashboard</a></div>',
                    "Job Error"),200

@app.route("/tools")
def tools():
    try:
        u=me(); li=bool(u); un=u.get("username","") if u else ""; avt=u.get("avatar","") if u else ""
        body=('<div class="wrap"><h1>&#128295; Free Online Tools</h1>'
              '<p class="sub">ZIP, PDF, Image compress + office tools — sab free!</p>'
              +ads_block()+
              '<div class="g3">'
              '<div class="tool-card" onclick="location.href=\'/tools/zip-compress\'"><div class="tool-icon">&#128230;</div><div class="tool-name">ZIP Compress</div><div class="tool-desc">Files ko ZIP mein compress karo</div></div>'
              '<div class="tool-card" onclick="location.href=\'/tools/zip-extract\'"><div class="tool-icon">&#128228;</div><div class="tool-name">ZIP Extract</div><div class="tool-desc">ZIP file extract karo</div></div>'
              '<div class="tool-card" onclick="location.href=\'/tools/image-compress\'"><div class="tool-icon">&#128247;</div><div class="tool-name">Image Compress</div><div class="tool-desc">Photo size reduce karo</div></div>'
              '<div class="tool-card" onclick="location.href=\'/tools/pdf-merge\'"><div class="tool-icon">&#128196;</div><div class="tool-name">PDF Merge</div><div class="tool-desc">Multiple PDFs combine karo</div></div>'
              '<div class="tool-card" onclick="location.href=\'/tools/pdf-to-jpg\'"><div class="tool-icon">&#128248;</div><div class="tool-name">PDF to JPG</div><div class="tool-desc">PDF pages ko images mein</div></div>'
              '<div class="tool-card" onclick="location.href=\'/crack\'"><div class="tool-icon">&#128272;</div><div class="tool-name">Password Recovery</div><div class="tool-desc">Encrypted ZIP/PDF unlock karo</div></div>'
              '</div>'+ads_block()+'</div>')
        return page(body,"Free Online Tools — ZipPasswordCrack.in",li,un,avatar=avt)
    except Exception as e:
        L.error(f"Tools: {e}")
        return redirect("/")

@app.route("/tools/image-compress",methods=["GET","POST"])
def tool_image_compress():
    try:
        u=me(); li=bool(u); un=u.get("username","") if u else ""; avt=u.get("avatar","") if u else ""
        result=""
        if request.method=="POST" and "img" in request.files:
            try:
                from PIL import Image
                f=request.files["img"]; quality=int(request.form.get("quality","75"))
                quality=max(10,min(95,quality)); fname=secure_filename(f.filename or "img.jpg")
                ext=Path(fname).suffix.lower()
                if ext not in {".jpg",".jpeg",".png",".webp"}: result='<div class="ae">Only JPG/PNG/WebP supported</div>'
                else:
                    img=Image.open(f)
                    if img.mode in ("RGBA","P"): img=img.convert("RGB")
                    buf=io.BytesIO(); img.save(buf,format="JPEG",quality=quality,optimize=True); buf.seek(0)
                    out_path=DLDIR/f"img_{uuid.uuid4()}.jpg"; out_path.write_bytes(buf.getvalue())
                    result=(f'<div class="ao">&#9989; Compressed! Size: {out_path.stat().st_size//1024}KB | Q: {quality}%<br>'
                            f'<a href="/dl-file/{out_path.name}" class="btn bg_ bsm" style="margin-top:.5rem">&#8595; Download</a></div>')
            except Exception as e: result=f'<div class="ae">Error: {e}</div>'
        body=('<div class="wrap"><h1>&#128247; Image Compress</h1><p class="sub">Photo size reduce karo</p>'
              +ads_block()+result+
              '<div class="card"><form method="POST" enctype="multipart/form-data">'
              '<div class="fg"><label>Image (JPG/PNG/WebP, Max 50MB)</label><input type="file" name="img" accept=".jpg,.jpeg,.png,.webp" required></div>'
              '<div class="fg"><label>Quality (10-95)</label><input type="number" name="quality" value="75" min="10" max="95"></div>'
              '<button type="submit" class="btn bp bw">&#128248; Compress &#8594;</button>'
              '</form></div></div>')
        return page(body,"Image Compress — ZipPasswordCrack.in",li,un,avatar=avt)
    except Exception as e: L.error(f"ImageCompress: {e}"); return redirect("/tools")

@app.route("/tools/zip-compress",methods=["GET","POST"])
def tool_zip_compress():
    try:
        u=me(); li=bool(u); un=u.get("username","") if u else ""; avt=u.get("avatar","") if u else ""
        result=""
        if request.method=="POST":
            try:
                files=request.files.getlist("files")
                if not files or not files[0].filename: result='<div class="ae">Koi file select nahi ki.</div>'
                else:
                    out_path=DLDIR/f"zip_{uuid.uuid4()}.zip"
                    with zf_mod.ZipFile(str(out_path),"w",zf_mod.ZIP_DEFLATED) as zf:
                        for f in files:
                            if f and f.filename: zf.writestr(secure_filename(f.filename),f.read())
                    result=(f'<div class="ao">&#9989; ZIP created! {len(files)} files | {out_path.stat().st_size//1024}KB<br>'
                            f'<a href="/dl-file/{out_path.name}" class="btn bg_ bsm" style="margin-top:.5rem">&#8595; Download ZIP</a></div>')
            except Exception as e: result=f'<div class="ae">Error: {e}</div>'
        body=('<div class="wrap"><h1>&#128230; ZIP Compress</h1><p class="sub">Files ko ZIP archive mein</p>'
              +ads_block()+result+
              '<div class="card"><form method="POST" enctype="multipart/form-data">'
              '<div class="fg"><label>Files Select Karo (Multiple)</label><input type="file" name="files" multiple required></div>'
              '<button type="submit" class="btn bp bw">&#128230; Create ZIP &#8594;</button></form></div></div>')
        return page(body,"ZIP Compress — ZipPasswordCrack.in",li,un,avatar=avt)
    except Exception as e: L.error(f"ZipCompress: {e}"); return redirect("/tools")

@app.route("/tools/zip-extract",methods=["GET","POST"])
def tool_zip_extract():
    try:
        u=me(); li=bool(u); un=u.get("username","") if u else ""; avt=u.get("avatar","") if u else ""
        result=""
        if request.method=="POST" and "zipf" in request.files:
            try:
                import shutil; f=request.files["zipf"]
                tmp_in=DLDIR/f"tmp_{uuid.uuid4()}.zip"; f.save(str(tmp_in))
                with zf_mod.ZipFile(str(tmp_in)) as zin:
                    names=zin.namelist(); tmp_dir=DATA_DIR/f"ext_{uuid.uuid4()}"
                    tmp_dir.mkdir(parents=True); zin.extractall(str(tmp_dir))
                out_path=DLDIR/f"extracted_{uuid.uuid4()}.zip"
                shutil.make_archive(str(out_path).replace(".zip",""),"zip",str(tmp_dir))
                shutil.rmtree(str(tmp_dir),ignore_errors=True); tmp_in.unlink(missing_ok=True)
                result=(f'<div class="ao">&#9989; Extracted! {len(names)} files<br>'
                        f'<a href="/dl-file/{out_path.name}" class="btn bg_ bsm" style="margin-top:.5rem">&#8595; Download</a></div>')
            except Exception as e: result=f'<div class="ae">Error: {e} — Password-protected? <a href="/crack">Use Password Recovery</a></div>'
        body=('<div class="wrap"><h1>&#128228; ZIP Extract</h1><p class="sub">ZIP file extract karo</p>'
              +ads_block()+result+
              '<div class="card"><form method="POST" enctype="multipart/form-data">'
              '<div class="fg"><label>ZIP File (Max 200MB)</label><input type="file" name="zipf" accept=".zip" required></div>'
              '<button type="submit" class="btn bp bw">&#128228; Extract &#8594;</button></form></div>'
              '<div class="ai">&#128272; Password-protected ZIP? <a href="/crack">Password Recovery</a></div></div>')
        return page(body,"ZIP Extract — ZipPasswordCrack.in",li,un,avatar=avt)
    except Exception as e: L.error(f"ZipExtract: {e}"); return redirect("/tools")

@app.route("/tools/pdf-merge",methods=["GET","POST"])
def tool_pdf_merge():
    try:
        u=me(); li=bool(u); un=u.get("username","") if u else ""; avt=u.get("avatar","") if u else ""
        result=""
        if request.method=="POST":
            try:
                from pypdf import PdfWriter,PdfReader
                files=request.files.getlist("pdfs")
                if not files or not files[0].filename: result='<div class="ae">Koi PDF select nahi ki.</div>'
                else:
                    writer=PdfWriter(); count=0
                    for f in files:
                        if f and f.filename:
                            reader=PdfReader(f)
                            for pg in reader.pages: writer.add_page(pg); count+=1
                    out_path=DLDIR/f"merged_{uuid.uuid4()}.pdf"
                    with open(str(out_path),"wb") as fout: writer.write(fout)
                    result=(f'<div class="ao">&#9989; Merged! {len(files)} PDFs | {count} pages<br>'
                            f'<a href="/dl-file/{out_path.name}" class="btn bg_ bsm" style="margin-top:.5rem">&#8595; Download Merged PDF</a></div>')
            except Exception as e: result=f'<div class="ae">Error: {e}</div>'
        body=('<div class="wrap"><h1>&#128196; PDF Merge</h1><p class="sub">Multiple PDFs ek mein combine karo</p>'
              +ads_block()+result+
              '<div class="card"><form method="POST" enctype="multipart/form-data">'
              '<div class="fg"><label>PDF Files (Multiple — order maintain hoga)</label>'
              '<input type="file" name="pdfs" accept=".pdf" multiple required></div>'
              '<button type="submit" class="btn bp bw">&#128279; Merge PDFs &#8594;</button></form></div></div>')
        return page(body,"PDF Merge — ZipPasswordCrack.in",li,un,avatar=avt)
    except Exception as e: L.error(f"PdfMerge: {e}"); return redirect("/tools")

@app.route("/tools/pdf-to-jpg",methods=["GET","POST"])
def tool_pdf_to_jpg():
    try:
        u=me(); li=bool(u); un=u.get("username","") if u else ""; avt=u.get("avatar","") if u else ""
        result=""
        if request.method=="POST" and "pdf" in request.files:
            try:
                try: import fitz; HAS_FITZ=True
                except: HAS_FITZ=False
                if not HAS_FITZ:
                    subprocess.run([sys.executable,"-m","pip","install","-q","pymupdf"],check=False)
                    try: import fitz; HAS_FITZ=True
                    except: HAS_FITZ=False
                if not HAS_FITZ: result='<div class="ae">pymupdf install karo: pip install pymupdf</div>'
                else:
                    f=request.files["pdf"]; tmp_pdf=DLDIR/f"tmp_{uuid.uuid4()}.pdf"; f.save(str(tmp_pdf))
                    doc=fitz.open(str(tmp_pdf)); pages=min(len(doc),int(request.form.get("pages","5")))
                    out_zip=DLDIR/f"pdf_imgs_{uuid.uuid4()}.zip"
                    with zf_mod.ZipFile(str(out_zip),"w",zf_mod.ZIP_DEFLATED) as zf:
                        for i in range(pages):
                            pg=doc[i]; mat=fitz.Matrix(1.5,1.5); pix=pg.get_pixmap(matrix=mat)
                            zf.writestr(f"page_{i+1:03d}.jpg",pix.tobytes("jpeg"))
                    doc.close(); tmp_pdf.unlink(missing_ok=True)
                    result=(f'<div class="ao">&#9989; Converted! {pages} pages to JPG<br>'
                            f'<a href="/dl-file/{out_zip.name}" class="btn bg_ bsm" style="margin-top:.5rem">&#8595; Download ZIP</a></div>')
            except Exception as e: result=f'<div class="ae">Error: {e}</div>'
        body=('<div class="wrap"><h1>&#128248; PDF to JPG</h1><p class="sub">PDF pages ko high quality JPG mein</p>'
              +ads_block()+result+
              '<div class="card"><form method="POST" enctype="multipart/form-data">'
              '<div class="fg"><label>PDF File (Max 50MB)</label><input type="file" name="pdf" accept=".pdf" required></div>'
              '<div class="fg"><label>Kitne Pages (max 20)</label><input type="number" name="pages" value="5" min="1" max="20"></div>'
              '<button type="submit" class="btn bp bw">&#128247; Convert &#8594;</button></form></div></div>')
        return page(body,"PDF to JPG — ZipPasswordCrack.in",li,un,avatar=avt)
    except Exception as e: L.error(f"PdfToJpg: {e}"); return redirect("/tools")

@app.route("/profile",methods=["GET","POST"])
@rlogin
def profile():
    try:
        u=me(); msg=""; err=""
        if request.method=="POST":
            dn=(request.form.get("display_name") or "").strip()
            notif=1 if request.form.get("notif_email") else 0
            try:
                c=get_db(); c.execute("UPDATE users SET display_name=?,notif_email=? WHERE id=?",(dn,notif,u["id"]))
                c.commit(); c.close(); msg="Profile updated!"; u=me()
            except Exception as e: err=str(e)
        avt=u.get("avatar","") if u else ""
        avt_html=(f'<img src="{avt}" style="width:60px;height:60px;border-radius:50%;border:3px solid var(--c1);display:block;margin:0 auto .8rem">') if avt else ""
        body=(f'<div class="wrap"><h1>&#128100; My Profile</h1>'
              f'<div class="card" style="max-width:500px">'
              +avt_html+
              f'<div style="text-align:center;margin-bottom:1rem">'
              f'<div style="font-size:1.1rem;font-weight:700;color:var(--wh)">{u.get("display_name") or u.get("username","")}</div>'
              f'<div style="font-size:.78rem;color:var(--dim)">@{u.get("username","")} | {u.get("login_type") or "password"} login</div>'
              f'<div style="font-size:.78rem;color:var(--dim)">{u.get("email") or "No email"}</div>'
              f'</div>'
              +(f'<div class="ao">&#9989; {msg}</div>' if msg else "")
              +(f'<div class="ae">&#10060; {err}</div>' if err else "")+
              f'<form method="POST">'
              f'<div class="fg"><label>Display Name</label><input name="display_name" value="{u.get("display_name") or ""}"></div>'
              f'<label class="ckl" style="background:transparent;border:none;padding:.3rem 0">'
              f'<input type="checkbox" name="notif_email" value="1" {"checked" if u.get("notif_email") else ""}>'
              f'<span>Password milne pe email notification</span></label>'
              f'<button type="submit" class="btn bp bw" style="margin-top:.8rem">Save Changes</button>'
              f'</form></div></div>')
        return page(body,"Profile — ZipPasswordCrack.in",True,u.get("username",""),avatar=avt)
    except Exception as e:
        L.error(f"Profile: {e}")
        return redirect("/dashboard")

@app.route("/dl-file/<fname>")
def dl_file(fname):
    try:
        fname=secure_filename(fname); fpath=DLDIR/fname
        if not fpath.exists(): abort(404)
        resp=send_file(str(fpath),as_attachment=True,download_name=fname)
        threading.Timer(120.0,lambda: fpath.unlink(missing_ok=True)).start()
        return resp
    except Exception as e: L.error(f"dl_file: {e}"); abort(404)

@app.route("/dl/<jid>")
@rlogin
def dl(jid):
    try:
        u=me(); c=get_db()
        j=row2dict(c.execute("SELECT * FROM jobs WHERE id=? AND user_id=?",(jid,u["id"])).fetchone()); c.close()
        if not j or not j.get("dl_ready"): abort(404)
        dl_path=j.get("dl_path")
        if not dl_path or not Path(dl_path).exists(): abort(404)
        fname=(j.get("filename") or "file").replace(".zip","")+"_extracted.zip"
        def after():
            try: Path(dl_path).unlink(missing_ok=True); jset(jid,dl_ready=0,dl_path="")
            except: pass
        resp=send_file(dl_path,as_attachment=True,download_name=fname)
        threading.Timer(10.0,after).start()
        return resp
    except Exception as e: L.error(f"dl: {e}"); abort(404)

# ── API Routes ────────────────────────────────────────────────────────────────
@app.route("/api/submit",methods=["POST"])
@rlogin
def api_submit():
    try:
        u=me()
        if "file" not in request.files: return jsonify({"error":"No file received."}),400
        f=request.files["file"]
        if not f or not f.filename: return jsonify({"error":"Empty file."}),400
        name=secure_filename(f.filename); ext=Path(name).suffix.lower()
        if ext not in {".zip",".pdf"}: return jsonify({"error":"Only .zip and .pdf files allowed"}),400
        tmp_path=UPLOAD/f"{uuid.uuid4()}{ext}"
        try:
            f.save(str(tmp_path)); size=tmp_path.stat().st_size
            if size==0: tmp_path.unlink(missing_ok=True); return jsonify({"error":"Empty file"}),400
            if size>200*1024*1024: tmp_path.unlink(missing_ok=True); return jsonify({"error":"Max 200MB"}),400
        except Exception as e: return jsonify({"error":f"Upload failed: {e}"}),500
        try: cfg=json.loads(request.form.get("config","{}"))
        except: cfg={}
        jid=str(uuid.uuid4()); cfg["file_type"]=ext.lstrip(".")
        c=get_db()
        c.execute("INSERT INTO jobs(id,user_id,filename,filetype,filesize,status,mode,cfg) VALUES(?,?,?,?,?,?,?,?)",
                  (jid,u["id"],name,ext.lstrip("."),size,"queued",cfg.get("mode","smart"),json.dumps(cfg)))
        c.commit(); c.close()
        with JLOCK: JOBS[jid]={"cancel":threading.Event()}
        threading.Thread(target=run_job,args=(jid,str(tmp_path),cfg,u["id"]),daemon=True,name=f"J{jid[:8]}").start()
        L.info(f"Job {jid}: {name} ({size//1024}KB) by {u.get('username','')}")
        return jsonify({"job_id":jid,"status":"queued","message":"Job started! Email aayegi."})
    except Exception as e:
        L.error(f"Submit: {e}\n{traceback.format_exc()}")
        return jsonify({"error":f"Server error: {str(e)}"}),500

@app.route("/api/progress/<jid>")
@rlogin
def api_progress(jid):
    try:
        u=me(); c=get_db()
        j=row2dict(c.execute("SELECT * FROM jobs WHERE id=? AND user_id=?",(jid,u["id"])).fetchone())
        if not j: c.close(); return jsonify({"error":"Not found"}),404
        logs=[row2dict(r) for r in c.execute("SELECT ts,msg FROM jlogs WHERE job_id=? ORDER BY id DESC LIMIT 60",(jid,)).fetchall()]
        c.close()
        return jsonify({"status":j.get("status",""),"attempts":j.get("attempts") or 0,"speed":j.get("speed") or 0,
                        "ef":fmt_t(j.get("elapsed",0)),"cpw":j.get("current_pw") or "",
                        "found_pw":j.get("found_pw"),"dl_ready":j.get("dl_ready"),"eta":j.get("est_eta",""),
                        "use_aes":j.get("use_aes",0),"notified":j.get("notified",0),
                        "logs":[{"ts":l.get("ts"),"msg":l.get("msg")} for l in reversed(logs)]})
    except Exception as e: return jsonify({"error":str(e)}),500

@app.route("/api/cancel/<jid>",methods=["POST"])
@rlogin
def api_cancel(jid):
    try:
        u=me(); c=get_db()
        j=row2dict(c.execute("SELECT id FROM jobs WHERE id=? AND user_id=?",(jid,u["id"])).fetchone()); c.close()
        if not j: return jsonify({"error":"Not found"}),404
        with JLOCK:
            info=JOBS.get(jid)
            if info: info["cancel"].set()
        jset(jid,status="cancelled"); return jsonify({"ok":True})
    except Exception as e: return jsonify({"error":str(e)}),500

@app.route("/api/stats")
def api_stats():
    try:
        gs=get_stats()
        return jsonify({"total_cracked":gs.get("total_cracked",0),"total_attempts":gs.get("total_attempts",0),
                        "total_jobs":gs.get("total_jobs",0),"active_jobs":len(JOBS)})
    except: return jsonify({"total_cracked":0,"total_attempts":0,"total_jobs":0,"active_jobs":0})

@app.route("/health",methods=["GET","HEAD"])
def health():
    try: c=get_db(); c.execute("SELECT 1"); c.close(); db_ok=True
    except: db_ok=False
    return jsonify({"ok":True,"time":datetime.now().isoformat(),"jobs":len(JOBS),
                    "v":"24","data_dir":str(DATA_DIR),"db_ok":db_ok,
                    "email_configured":bool(SMTP_USER and SMTP_PASS),
                    "google_oauth":bool(GOOGLE_CLIENT_ID)})

@app.route("/robots.txt")
def robots():
    return Response("User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /dl/\nDisallow: /dl-file/\nSitemap: https://zippasswordcrack.in/sitemap.xml",
                    mimetype="text/plain")

@app.route("/sitemap.xml")
def sitemap():
    xml=('<?xml version="1.0" encoding="UTF-8"?>'
         '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
         '<url><loc>https://zippasswordcrack.in/</loc><priority>1.0</priority><changefreq>daily</changefreq></url>'
         '<url><loc>https://zippasswordcrack.in/register</loc><priority>0.9</priority></url>'
         '<url><loc>https://zippasswordcrack.in/tools</loc><priority>0.8</priority><changefreq>weekly</changefreq></url>'
         '<url><loc>https://zippasswordcrack.in/tools/image-compress</loc><priority>0.7</priority></url>'
         '<url><loc>https://zippasswordcrack.in/tools/zip-compress</loc><priority>0.7</priority></url>'
         '<url><loc>https://zippasswordcrack.in/tools/zip-extract</loc><priority>0.7</priority></url>'
         '<url><loc>https://zippasswordcrack.in/tools/pdf-merge</loc><priority>0.7</priority></url>'
         '<url><loc>https://zippasswordcrack.in/tools/pdf-to-jpg</loc><priority>0.7</priority></url>'
         '<url><loc>https://zippasswordcrack.in/terms</loc><priority>0.5</priority></url>'
         '</urlset>')
    return Response(xml,mimetype="application/xml")

@app.errorhandler(413)
def e413(e): return jsonify({"error":"File too large! Max 200MB."}),413

@app.errorhandler(404)
def e404(e):
    return page('<div class="wrap" style="text-align:center;padding-top:4rem">'
                '<div style="font-size:4rem;margin-bottom:1rem">&#128269;</div>'
                '<h1 style="color:var(--wh)">404 — Page Not Found</h1>'
                '<p style="color:var(--dim);margin-top:.5rem">Yeh page exist nahi karta.</p>'
                '<a href="/" class="btn bp" style="margin-top:1.5rem;display:inline-flex">&#127968; Home</a>'
                '</div>',"404 — ZipPasswordCrack.in"),404

@app.errorhandler(500)
def e500(e):
    L.error(f"500: {e}")
    return page('<div class="wrap" style="text-align:center;padding-top:4rem">'
                '<div style="font-size:4rem;margin-bottom:1rem">&#9889;</div>'
                '<h1 style="color:var(--wh)">500 — Server Error</h1>'
                '<p style="color:var(--dim);margin-top:.5rem">Retry karo.</p>'
                '<a href="/" class="btn bp" style="margin-top:1.5rem;display:inline-flex">&#127968; Home</a>'
                '</div>',"Error — ZipPasswordCrack.in"),500

if __name__=="__main__":
    PORT=int(os.environ.get("PORT",5000))
    print(f"\n{'='*65}")
    print(f"  ZipPasswordCrack.in v24 ULTRA PRODUCTION")
    print(f"  Data:    {DATA_DIR}")
    print(f"  Email:   {'OK - '+SMTP_USER if SMTP_USER and SMTP_PASS else 'NOT configured'}")
    print(f"  Google:  {'OK - OAuth ready' if GOOGLE_CLIENT_ID else 'NOT configured'}")
    print(f"  URL:     http://0.0.0.0:{PORT}")
    print(f"{'='*65}\n")
    app.run(host="0.0.0.0",port=PORT,debug=False,threaded=True,use_reloader=False)
