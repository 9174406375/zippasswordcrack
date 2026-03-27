#!/usr/bin/env python3
"""
crackpro.py - ZipPasswordCrack.in v20
FIXES:
  - Cloud upload: Uses /tmp (works on ALL servers)
  - File size limit: 200MB 
  - Better error messages for upload failures
  - Google Ads support
  - Render/Fly.io/Railway all compatible
"""
import subprocess,sys,os
for _p in ["flask","pyzipper","pypdf","requests","werkzeug"]:
    try: __import__(_p)
    except: subprocess.run([sys.executable,"-m","pip","install","-q",_p],check=False)

import json,time,uuid,sqlite3,hashlib,logging,threading,traceback,shutil
from pathlib import Path
from datetime import datetime
from functools import wraps
from flask import Flask,request,jsonify,session,redirect,send_file,abort,make_response
from werkzeug.utils import secure_filename

try:
    from engine_ultra import gen_master,Cracker,CS,GITHUB_LISTS
except ImportError:
    print("ERROR: engine_ultra.py not found!"); sys.exit(1)

# ── CLOUD-COMPATIBLE PATHS ─────────────────────────────────────
# On Railway/Render/Fly: use /tmp (always writable)
# On Termux: use SD card
def get_data_dir():
    # Try environment variable first
    env_dir = os.environ.get("DATA_DIR","")
    if env_dir and Path(env_dir).parent.exists():
        return Path(env_dir)
    # Try Termux SD card
    termux = Path("/storage/emulated/0/ZipCracker")
    if termux.parent.exists():
        return termux
    # Cloud fallback: /tmp (always works)
    return Path("/tmp/zipcracker")

DATA_DIR = get_data_dir()
UPLOAD = DATA_DIR/"uploads"
DLDIR  = DATA_DIR/"downloads"
LOGDIR = DATA_DIR/"logs"
DICTS  = DATA_DIR/"dictionaries"
DB_F   = DATA_DIR/"crackpro.db"

for d in [DATA_DIR,UPLOAD,DLDIR,LOGDIR,DICTS]:
    d.mkdir(parents=True,exist_ok=True)

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOGDIR/f"app_{datetime.now():%Y%m%d}.log",encoding="utf-8"),
        logging.StreamHandler()
    ])
L=logging.getLogger("app")
L.info(f"Data dir: {DATA_DIR}")

app=Flask(__name__)
app.secret_key=os.environ.get("SECRET_KEY","zpc_v20_2025!")

# ── GOOGLE ADSENSE CONFIG ──────────────────────────────────────
GOOGLE_ADS_CLIENT = os.environ.get("GOOGLE_ADS_CLIENT","")  # ca-pub-XXXXXXXXXXXXXXXX
GOOGLE_ADS_SLOT   = os.environ.get("GOOGLE_ADS_SLOT","")    # Ad slot ID

# ── UPLOAD CONFIG - FIXED FOR CLOUD ───────────────────────────
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB","200"))
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

ALLOWED_EXTENSIONS = {".zip",".pdf"}
JOBS={}; JLOCK=threading.Lock()

# ── DB ─────────────────────────────────────────────────────────
def get_db():
    c=sqlite3.connect(str(DB_F),check_same_thread=False)
    c.row_factory=sqlite3.Row; c.execute("PRAGMA journal_mode=WAL"); return c

def init_db():
    c=get_db(); c.executescript("""
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL, password TEXT NOT NULL,
        email TEXT DEFAULT '', terms_accepted INTEGER DEFAULT 0,
        terms_at TEXT DEFAULT '',created_at TEXT DEFAULT(datetime('now')),last_login TEXT
    );
    CREATE TABLE IF NOT EXISTS jobs(
        id TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
        filename TEXT DEFAULT '', filetype TEXT DEFAULT '', filesize INTEGER DEFAULT 0,
        status TEXT DEFAULT 'queued', mode TEXT DEFAULT 'smart', cfg TEXT DEFAULT '{}',
        found_pw TEXT, attempts INTEGER DEFAULT 0, elapsed REAL DEFAULT 0,
        speed INTEGER DEFAULT 0, current_pw TEXT DEFAULT '',
        dl_ready INTEGER DEFAULT 0, dl_path TEXT DEFAULT '',
        est_eta TEXT DEFAULT '', use_aes INTEGER DEFAULT 0,
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
        total_jobs INTEGER DEFAULT 0
    );
    INSERT OR IGNORE INTO stats(id,total_cracked,total_attempts,total_jobs) VALUES(1,0,0,0);
    """); c.commit(); c.close()
init_db()

def hp(p): return hashlib.sha256(p.encode()).hexdigest()
def rlogin(f):
    @wraps(f)
    def inner(*a,**kw):
        if "uid" not in session: return redirect("/login")
        return f(*a,**kw)
    return inner
def me():
    uid=session.get("uid")
    if not uid: return None
    c=get_db(); u=c.execute("SELECT * FROM users WHERE id=?",(uid,)).fetchone(); c.close(); return u
def jset(jid,**kw):
    if not kw: return
    s=",".join(k+"=?" for k in kw); c=get_db()
    c.execute("UPDATE jobs SET "+s+" WHERE id=?",list(kw.values())+[jid]); c.commit(); c.close()
def jlog(jid,msg):
    try: c=get_db(); c.execute("INSERT INTO jlogs(job_id,msg) VALUES(?,?)",(jid,msg)); c.commit(); c.close()
    except: pass
def fmt_t(s):
    s=int(s or 0)
    if s<60: return str(s)+"s"
    if s<3600: return str(s//60)+"m "+str(s%60)+"s"
    if s<86400: return str(s//3600)+"h "+str((s%3600)//60)+"m"
    return str(s//86400)+"d "+str((s%86400)//3600)+"h"
def get_stats():
    try:
        c=get_db(); s=c.execute("SELECT * FROM stats WHERE id=1").fetchone(); c.close()
        return dict(s) if s else {"total_cracked":0,"total_attempts":0,"total_jobs":0}
    except: return {"total_cracked":0,"total_attempts":0,"total_jobs":0}
def upd_stats(cracked=0,attempts=0,jobs=0):
    try:
        c=get_db()
        c.execute("UPDATE stats SET total_cracked=total_cracked+?,total_attempts=total_attempts+?,total_jobs=total_jobs+? WHERE id=1",(cracked,attempts,jobs))
        c.commit(); c.close()
    except: pass

# ── JOB RUNNER ─────────────────────────────────────────────────
def run_job(jid,fpath,cfg):
    cancel=threading.Event()
    with JLOCK: JOBS[jid]={"cancel":cancel}
    jset(jid,status="running"); jlog(jid,"Started v20 — Common first → 18k/sec")
    t0=time.time()
    try:
        gen=gen_master(cfg); freq=int(cfg.get("progress_every",500))
        mode=cfg.get("mode","smart")
        est_map={"smart":5_000_000,"calendar":50_000_000,"mobile":10_000_000,
                 "dictionary":15_000_000,"keyboard":500_000,"brute":2_900_000_000,"hybrid":100_000_000_000}
        est_total=est_map.get(mode,5_000_000)
        upd_stats(jobs=1)

        def cb(n,sp,pw):
            if cancel.is_set(): return False
            el=time.time()-t0
            if sp>0 and est_total>0:
                rem=max(0,est_total-n)
                secs=int(rem/sp)
                if secs<3600: eta=f"~{secs//60}m mein"
                elif secs<86400: eta=f"~{secs//3600}h mein"
                else: eta=f"~{secs//86400}d mein"
            else: eta="Calculating..."
            jset(jid,attempts=n,speed=sp,elapsed=round(el,1),
                 current_pw=(pw or "")[:120],est_eta=eta)
            if n%(freq*10)==0:
                jlog(jid,f"Trying: {n:,} | {sp:,}/s | ETA: {eta} | {(pw or '')[:35]}")
                upd_stats(attempts=freq*10)
            return True

        res=Cracker.crack(fpath,gen,cb,freq)
        use_aes=res.get("use_aes",False)
        if use_aes: jset(jid,use_aes=1)

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
            if cfg.get("file_type","")=="zip":
                dl_zip=str(DLDIR/(jid+"_extracted.zip"))
                er=Cracker.extract_and_zip(fpath,pw,dl_zip)
                if er["ok"]:
                    jset(jid,dl_ready=1,dl_path=dl_zip)
                    jlog(jid,f"Files extracted: {len(er['files'])} ready for download")
                else:
                    jlog(jid,f"Extract: {er.get('error','')}")
        else:
            jset(jid,status="failed",attempts=res.get("attempts",0),
                 elapsed=res.get("elapsed",0),speed=res.get("speed",0),
                 est_eta="Not found",finished_at=datetime.now().isoformat())
            jlog(jid,f"Not found. Tried {res.get('attempts',0):,} in {fmt_t(res.get('elapsed',0))}")
            upd_stats(attempts=res.get("attempts",0))
        if res.get("error"): jlog(jid,f"Error: {res['error']}")
    except Exception as e:
        L.error(f"Job {jid}: {e}\n{traceback.format_exc()}")
        jset(jid,status="error",finished_at=datetime.now().isoformat()); jlog(jid,f"Fatal: {e}")
    finally:
        try: Path(fpath).unlink(missing_ok=True)
        except: pass
        with JLOCK: JOBS.pop(jid,None)

# ── ADS SNIPPET ────────────────────────────────────────────────
def ads_head():
    if not GOOGLE_ADS_CLIENT: return ""
    return f'<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client={GOOGLE_ADS_CLIENT}" crossorigin="anonymous"></script>'

def ads_banner(pos="top"):
    if not GOOGLE_ADS_CLIENT or not GOOGLE_ADS_SLOT: return ""
    return (f'<div style="text-align:center;margin:.8rem 0;background:#05090f;padding:.5rem;border-radius:6px;border:1px solid #111e30">'
            f'<ins class="adsbygoogle" style="display:block" data-ad-client="{GOOGLE_ADS_CLIENT}" '
            f'data-ad-slot="{GOOGLE_ADS_SLOT}" data-ad-format="auto" data-full-width-responsive="true"></ins>'
            f'<script>(adsbygoogle=window.adsbygoogle||[]).push({{}});</script></div>')

# ── CSS ────────────────────────────────────────────────────────
CSS="""<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#030609;color:#bdd0e8;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;min-height:100vh}
a{color:#00c6ff;text-decoration:none}a:hover{color:#00ff88}
nav{background:rgba(5,9,16,.98);border-bottom:1px solid #111e30;height:52px;display:flex;align-items:center;justify-content:space-between;padding:0 1.2rem;position:sticky;top:0;z-index:100}
.logo{color:#00c6ff;font-weight:800;font-family:monospace;font-size:.9rem}
.navr{display:flex;gap:.9rem;align-items:center}.navr a{color:#4a6070;font-size:.82rem;font-weight:600}
.navr a:hover{color:#00c6ff}.nbtn{background:#00c6ff!important;color:#000!important;padding:.3rem .85rem;border-radius:5px;font-weight:700!important}
.wrap{max-width:960px;margin:0 auto;padding:1.8rem 1rem 5rem}
h1{font-size:1.6rem;font-weight:800;color:#fff;margin-bottom:.18rem}
.sub{color:#3d5268;font-size:.83rem;margin-bottom:1.3rem}
.card{background:#090f18;border:1px solid #111e30;border-radius:10px;padding:1.25rem;margin-bottom:.9rem}
.ct{font-size:.87rem;font-weight:700;color:#fff;margin-bottom:.8rem;display:flex;align-items:center;gap:.35rem}
.fg{margin-bottom:.88rem}
label{display:block;font-size:.71rem;font-weight:600;color:#3d5268;text-transform:uppercase;letter-spacing:.06em;margin-bottom:.27rem}
input,select,textarea{width:100%;background:#05090f;border:1px solid #111e30;color:#bdd0e8;padding:.56rem .82rem;border-radius:6px;font-family:inherit;font-size:.87rem;outline:none;transition:.2s}
input:focus,select:focus,textarea:focus{border-color:#00c6ff}input[type=checkbox]{width:auto;accent-color:#00c6ff}
.btn{display:inline-flex;align-items:center;gap:.3rem;padding:.56rem 1.12rem;border-radius:6px;border:none;cursor:pointer;font-size:.85rem;font-weight:700;font-family:inherit;transition:.2s;text-decoration:none}
.bp{background:#00c6ff;color:#000}.bp:hover{opacity:.88}.bg_{background:#00e676;color:#000}
.bd{background:#e0304a;color:#fff}.bo{background:transparent;border:1px solid #111e30;color:#bdd0e8}
.bo:hover{border-color:#00c6ff;color:#00c6ff}.bsm{padding:.27rem .7rem;font-size:.75rem}.bw{width:100%;justify-content:center}
.badge{display:inline-block;padding:.11rem .5rem;border-radius:4px;font-size:.67rem;font-weight:700}
.sr{background:rgba(0,198,255,.13);color:#00c6ff;animation:pulse 1.5s infinite}
.sf{background:rgba(0,230,118,.13);color:#00e676}.se,.sx{background:rgba(224,48,74,.13);color:#e0304a}
.sq{background:rgba(255,140,0,.13);color:#ff8c00}.sc{background:rgba(61,82,104,.13);color:#3d5268}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
.pgw{background:#040810;border-radius:99px;height:6px;overflow:hidden;margin:.3rem 0}
.pgf{height:100%;border-radius:99px;background:linear-gradient(90deg,#00c6ff,#00e676);transition:width .5s;min-width:2px}
.g2{display:grid;grid-template-columns:1fr 1fr;gap:.82rem}
.g3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:.82rem}
.g4{display:grid;grid-template-columns:repeat(4,1fr);gap:.72rem;margin-bottom:.9rem}
@media(max-width:540px){.g2,.g3,.g4{grid-template-columns:1fr 1fr}}
.stat{background:#090f18;border:1px solid #111e30;border-radius:8px;padding:.82rem;text-align:center}
.sv{font-size:1.3rem;font-weight:900;color:#00c6ff;font-family:monospace}
.sl{font-size:.63rem;color:#3d5268;text-transform:uppercase;letter-spacing:.07em;margin-top:.17rem}
.ae{background:rgba(224,48,74,.08);border:1px solid rgba(224,48,74,.25);color:#f07080;border-radius:6px;padding:.62rem .82rem;margin:.48rem 0;font-size:.83rem}
.ao{background:rgba(0,230,118,.08);border:1px solid rgba(0,230,118,.25);color:#70f090;border-radius:6px;padding:.62rem .82rem;margin:.48rem 0;font-size:.83rem}
.ai{background:rgba(0,198,255,.08);border:1px solid rgba(0,198,255,.25);color:#70d8ff;border-radius:6px;padding:.62rem .82rem;margin:.48rem 0;font-size:.83rem}
.aw{background:rgba(255,200,0,.08);border:1px solid rgba(255,200,0,.25);color:#ffc800;border-radius:6px;padding:.82rem;margin:.48rem 0;font-size:.83rem}
table{width:100%;border-collapse:collapse}
th{text-align:left;font-size:.67rem;color:#3d5268;text-transform:uppercase;letter-spacing:.07em;padding:.5rem .82rem;border-bottom:1px solid #111e30}
td{padding:.7rem .82rem;border-bottom:1px solid rgba(17,30,48,.5);font-size:.83rem;vertical-align:middle}
tr:hover td{background:rgba(0,198,255,.02)}.mono{font-family:monospace}
.fbox{background:rgba(0,230,118,.05);border:2px solid #00e676;border-radius:12px;padding:1.5rem;text-align:center;margin:.8rem 0;box-shadow:0 0 40px rgba(0,230,118,.15)}
.fpw{font-family:monospace;font-size:1.8rem;font-weight:700;color:#00e676;word-break:break-all;text-shadow:0 0 25px rgba(0,230,118,.5)}
.term{background:#000;border:1px solid #0a1520;border-radius:8px;padding:.82rem;font-family:monospace;font-size:.73rem;color:#00c6ff;max-height:230px;overflow-y:auto;line-height:1.65}
.tok{color:#00e676}.terr{color:#e0304a}
.mg{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:.52rem}
.mc{background:#05090f;border:2px solid #111e30;border-radius:8px;padding:.8rem;cursor:pointer;transition:.2s;text-align:center;user-select:none}
.mc:hover,.mc.sel{border-color:#00c6ff;background:rgba(0,198,255,.05)}
.mi{font-size:1.4rem;margin-bottom:.2rem}.mn_{font-size:.75rem;font-weight:700;color:#fff}.md_{font-size:.62rem;color:#3d5268;margin-top:.1rem}
.ckg{display:flex;flex-wrap:wrap;gap:.3rem}
.ckl{display:flex;align-items:center;background:#05090f;border:1px solid #111e30;padding:.27rem .66rem;border-radius:5px;cursor:pointer;font-size:.77rem;transition:.2s;gap:.28rem}
.ckl:hover{border-color:#00c6ff;color:#00c6ff}
.dz{border:2px dashed #111e30;border-radius:8px;padding:2rem;text-align:center;cursor:pointer;transition:.2s}.dz:hover{border-color:#00c6ff}
.dz.dragover{border-color:#00c6ff;background:rgba(0,198,255,.05)}
.hidden{display:none!important}
code{background:#05090f;padding:.1rem .32rem;border-radius:3px;color:#00c6ff;font-family:monospace;font-size:.85em}
.rtbox{background:#000;border:2px solid #ffdd00;border-radius:10px;padding:1rem;margin:.6rem 0;font-family:monospace}
.rt-label{color:#3d5268;font-size:.65rem;text-transform:uppercase;letter-spacing:.08em;margin-bottom:.3rem}
.rt-pw{color:#ffdd00;font-size:1.2rem;font-weight:700;word-break:break-all}
.rt-info{color:#3d5268;font-size:.69rem;margin-top:.3rem}
.dlbox{background:rgba(0,230,118,.05);border:1px solid rgba(0,230,118,.3);border-radius:8px;padding:1rem;margin:.6rem 0;text-align:center}
.auth-wrap{min-height:calc(100vh - 52px);display:flex;align-items:center;justify-content:center;padding:2rem}
.auth-card{background:#090f18;border:1px solid #111e30;border-radius:10px;padding:1.8rem;width:100%;max-width:420px}
.eta-box{background:rgba(255,200,0,.07);border:1px solid rgba(255,200,0,.2);border-radius:8px;padding:.8rem 1rem;margin:.5rem 0;display:flex;align-items:center;gap:.7rem}
.legal-warning{background:rgba(255,100,0,.08);border:2px solid rgba(255,100,0,.35);border-radius:10px;padding:1.2rem;margin:.7rem 0}
.legal-warning h3{color:#ff6b35;font-size:.9rem;margin-bottom:.5rem}
.legal-warning p{font-size:.8rem;color:#cc8060;line-height:1.7}
.upload-progress{display:none;margin:.5rem 0}
.upload-bar{background:#040810;border-radius:4px;height:8px;overflow:hidden}
.upload-fill{height:100%;background:linear-gradient(90deg,#00c6ff,#00e676);width:0%;transition:width .3s;border-radius:4px}
</style>"""

JSCMN="<script>function q(id){return document.getElementById(id)}function chk(sel){return [...document.querySelectorAll(sel+':checked')].map(c=>c.value)}</script>"

def nav(li=False,un=""):
    if li: return f'<nav><a href="/" class="logo">&#9889; ZipPasswordCrack.in</a><div class="navr"><a href="/dashboard">Dashboard</a><a href="/crack">+ Job</a><span style="color:#3d5268;font-size:.78rem">Hi <b style="color:#00c6ff">{un}</b></span><a href="/logout" class="nbtn">Logout</a></div></nav>'
    return '<nav><a href="/" class="logo">&#9889; ZipPasswordCrack.in</a><div class="navr"><a href="/login">Login</a><a href="/register" class="nbtn">Register Free</a></div></nav>'

def page(body,title="ZipPasswordCrack.in",li=False,un="",js=""):
    ah=ads_head()
    return "".join(['<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f'<title>{title}</title>',ah,CSS,'</head><body>',nav(li,un),body,
        JSCMN,f'<script>{js}</script>' if js else '','</body></html>'])

@app.route("/",methods=["GET","HEAD"])
def home():
    u=me(); gs=get_stats()
    ad_top=ads_banner("top")
    body=(f'<div style="min-height:calc(100vh - 52px);display:flex;align-items:center;background:radial-gradient(ellipse at 20% 50%,rgba(0,198,255,.05),transparent 55%)">'
          f'<div class="wrap" style="text-align:center;padding-top:2rem">'
          +ad_top+
          f'<h1 style="font-size:clamp(1.8rem,5vw,2.8rem);line-height:1.12;margin-bottom:.6rem">'
          f'Apni File ka Password<br><span style="color:#00c6ff">Recover Karo — 24/7</span></h1>'
          f'<p style="color:#3d5268;max-width:560px;margin:0 auto 1rem;font-size:.9rem;line-height:1.7">'
          f'Sirf <b style="color:#fff">apni khud ki file</b>. Upload karo, background mein kaam hota rahega. '
          f'Password milne pe notification + dashboard update.</p>'
          f'<div class="legal-warning" style="max-width:560px;margin:0 auto .8rem;text-align:left">'
          f'<h3>&#9888;&#65039; Sirf Legal Use — Apni Khud Ki File</h3>'
          f'<p>Kisi doosre ki file pe use karna India IT Act 2000 ke tehat illegal hai.</p></div>'
          f'<div style="display:flex;gap:.65rem;justify-content:center;flex-wrap:wrap;margin-bottom:1.2rem">'
          f'<a href="/register" class="btn bp" style="padding:.75rem 1.8rem">&#128640; Register (Free)</a>'
          f'<a href="/login" class="btn bo" style="padding:.75rem 1.8rem">Login</a>'
          f'<a href="/terms" class="btn bo" style="padding:.75rem 1.2rem;font-size:.78rem">Terms</a></div>'
          f'<div style="background:#090f18;border:1px solid #111e30;border-radius:10px;padding:1rem;max-width:500px;margin:0 auto 1rem">'
          f'<div style="font-size:.68rem;color:#3d5268;text-transform:uppercase;letter-spacing:.08em;margin-bottom:.5rem">&#127760; Live Counter</div>'
          f'<div style="display:flex;gap:1.5rem;justify-content:center;font-family:monospace">'
          f'<div><div style="font-size:1.4rem;font-weight:900;color:#00e676" id="gs-c">{gs["total_cracked"]:,}</div><div style="font-size:.62rem;color:#3d5268">Cracked</div></div>'
          f'<div><div style="font-size:1.4rem;font-weight:900;color:#00c6ff" id="gs-a">{gs["total_attempts"]:,}</div><div style="font-size:.62rem;color:#3d5268">Attempts</div></div>'
          f'<div><div style="font-size:1.4rem;font-weight:900;color:#ff8c00" id="gs-j">{gs["total_jobs"]:,}</div><div style="font-size:.62rem;color:#3d5268">Jobs</div></div>'
          f'</div></div>'
          f'<div class="g4" style="max-width:580px;margin:0 auto">'
          f'<div class="stat"><div class="sv" style="color:#00e676;font-size:.9rem">18k/s</div><div class="sl">ZIP Speed</div></div>'
          f'<div class="stat"><div class="sv" style="color:#ff8c00;font-size:.85rem">100T+</div><div class="sl">Combos</div></div>'
          f'<div class="stat"><div class="sv" style="font-size:.9rem">15</div><div class="sl">GitHub Lists</div></div>'
          f'<div class="stat"><div class="sv" style="color:#ffdd00;font-size:.85rem">24/7</div><div class="sl">Background</div></div>'
          f'</div></div></div>'
          +ads_banner("bottom"))
    js=("setInterval(()=>fetch('/api/stats').then(r=>r.json()).then(d=>{"
        "if(q('gs-c'))q('gs-c').textContent=d.total_cracked.toLocaleString();"
        "if(q('gs-a'))q('gs-a').textContent=d.total_attempts.toLocaleString();"
        "if(q('gs-j'))q('gs-j').textContent=d.total_jobs.toLocaleString();"
        "}),10000);")
    return page(body,li=bool(u),un=u["username"] if u else "",js=js)

@app.route("/terms")
def terms():
    body=('<div class="wrap"><h1>Terms of Service</h1><p class="sub">ZipPasswordCrack.in</p>'
          '<div class="legal-warning"><h3>&#9888;&#65039; LEGAL WARNING</h3>'
          '<p><b>Yeh tool SIRF apni khud ki files ke liye.</b> Kisi doosre ki file = '
          'IT Act 2000 Section 43/66 = 3 saal jail + fine.</p></div>'
          '<div style="background:#040c14;border:1px solid #111e30;border-radius:8px;padding:1.2rem;font-size:.8rem;line-height:1.8;color:#8099b8;max-height:400px;overflow-y:auto">'
          '<p><b style="color:#00c6ff">1. Legal Use Only</b><br>'
          'Sirf apni files. Unauthorized use strictly prohibited.</p>'
          '<p><b style="color:#00c6ff">2. Privacy</b><br>'
          'Files crack hone ke baad delete. Data third parties ko nahi dete.</p>'
          '<p><b style="color:#00c6ff">3. Limitation</b><br>'
          'Service as-is. Kisi damage ke liye zimmedaari nahi.</p>'
          '<p><b style="color:#00c6ff">4. Governing Law</b><br>'
          'India laws apply. Last updated: 2025</p>'
          '</div>'
          '<div style="margin-top:1rem"><a href="/register" class="btn bp">Register (Accept karke)</a>'
          ' &nbsp;<a href="/" class="btn bo">Back</a></div></div>')
    return page(body,"Terms")

@app.route("/register",methods=["GET","POST"])
def register():
    if me(): return redirect("/dashboard")
    err=""; suc=""
    if request.method=="POST":
        un=(request.form.get("username") or "").strip()
        pw=(request.form.get("password") or "").strip()
        em=(request.form.get("email") or "").strip()
        if not request.form.get("terms"): err="Terms accept karna zaroori hai."
        elif not request.form.get("legal"): err="Legal confirmation zaroori hai."
        elif len(un)<3: err="Username 3+ chars."
        elif len(pw)<4: err="Password 4+ chars."
        else:
            try:
                c=get_db()
                c.execute("INSERT INTO users(username,password,email,terms_accepted,terms_at) VALUES(?,?,?,1,datetime('now'))",(un,hp(pw),em))
                c.commit(); c.close(); suc="Account ready! Login karo."
            except sqlite3.IntegrityError: err="Username taken."
            except Exception as e: err=str(e)
    body='<div class="auth-wrap"><div class="auth-card"><h2 style="color:#fff;text-align:center;margin-bottom:.5rem">Register Free</h2>'
    if err: body+=f'<div class="ae">&#10060; {err}</div>'
    if suc: body+=f'<div class="ao">&#9989; {suc} <a href="/login">Login</a></div>'
    if not suc:
        body+=('<form method="POST"><div class="fg"><label>Username</label><input name="username" required placeholder="username"></div>'
               '<div class="fg"><label>Password</label><input name="password" type="password" required placeholder="password"></div>'
               '<div class="fg"><label>Email (optional)</label><input name="email" type="email" placeholder="you@example.com"></div>'
               '<div class="legal-warning" style="margin:.6rem 0;padding:.8rem"><h3>&#9888;&#65039; Confirm</h3>'
               '<p>Sirf apni khud ki files ke liye use karunga.</p></div>'
               '<label class="ckl" style="background:transparent;border:none;gap:.5rem;padding:.3rem 0">'
               '<input type="checkbox" name="legal" value="1" required>'
               '<span style="font-size:.8rem">Sirf <b style="color:#00e676">apni khud ki files</b> ke liye</span></label><br>'
               '<label class="ckl" style="background:transparent;border:none;gap:.5rem;padding:.3rem 0">'
               '<input type="checkbox" name="terms" value="1" required>'
               '<span style="font-size:.8rem"><a href="/terms" target="_blank">Terms</a> accept karta hoon</span></label>'
               '<button type="submit" class="btn bp bw" style="margin-top:.7rem">Register &#8594;</button>'
               '</form>')
    body+='<p style="text-align:center;margin-top:.9rem;color:#3d5268;font-size:.82rem">Pehle se? <a href="/login">Login</a></p></div></div>'
    return page(body,"Register")

@app.route("/login",methods=["GET","POST"])
def login():
    if "uid" in session: return redirect("/dashboard")
    err=""
    if request.method=="POST":
        un=(request.form.get("username") or "").strip(); pw=(request.form.get("password") or "").strip()
        try:
            c=get_db(); u=c.execute("SELECT * FROM users WHERE username=? AND password=?",(un,hp(pw))).fetchone()
            if u:
                c.execute("UPDATE users SET last_login=datetime('now') WHERE id=?",(u["id"],)); c.commit(); c.close()
                session.clear(); session["uid"]=int(u["id"]); session["uname"]=str(u["username"]); return redirect("/dashboard")
            c.close(); err="Galat username ya password."
        except Exception as e: err=str(e)
    body='<div class="auth-wrap"><div class="auth-card"><h2 style="color:#fff;text-align:center;margin-bottom:.5rem">Login</h2>'
    if err: body+=f'<div class="ae">&#10060; {err}</div>'
    body+=('<form method="POST"><div class="fg"><label>Username</label><input name="username" required placeholder="username" autocomplete="username"></div>'
           '<div class="fg"><label>Password</label><input name="password" type="password" required placeholder="password" autocomplete="current-password"></div>'
           '<button type="submit" class="btn bp bw">Login &#8594;</button></form>'
           '<p style="text-align:center;margin-top:.9rem;color:#3d5268;font-size:.82rem">Naya? <a href="/register">Register (free)</a></p>'
           '</div></div>')
    return page(body,"Login")

@app.route("/logout")
def logout(): session.clear(); return redirect("/")

@app.route("/dashboard")
@rlogin
def dashboard():
    try:
        u=me()
        if not u: session.clear(); return redirect("/login")
        c=get_db(); jobs=c.execute("SELECT * FROM jobs WHERE user_id=? ORDER BY created_at DESC LIMIT 50",(u["id"],)).fetchall(); c.close()
        total=len(jobs); fc=sum(1 for j in jobs if j["status"]=="found")
        fl=sum(1 for j in jobs if j["status"]=="failed"); rn=sum(1 for j in jobs if j["status"] in ("running","queued"))
        gs=get_stats()
        rows=""
        for j in jobs:
            pc=f'<span style="color:#00e676;font-family:monospace;cursor:pointer;font-size:.83rem" onclick="navigator.clipboard.writeText(\'{j["found_pw"]}\');this.textContent=\'Copied!\'">{j["found_pw"]}</span>' if j["found_pw"] else "&mdash;"
            stop=f'<button onclick="stopJob(\'{j["id"]}\')" class="btn bd bsm" style="margin-left:.2rem">Stop</button>' if j["status"] in ("running","queued") else ""
            dl=f'<a href="/dl/{j["id"]}" class="btn bg_ bsm" style="margin-left:.2rem">&#8595; Files</a>' if j["status"]=="found" and j["dl_ready"] else ""
            bc="sr" if j["status"]=="running" else("sf" if j["status"]=="found" else("se" if j["status"] in ("failed","error") else("sq" if j["status"]=="queued" else "sc")))
            eta_cell=f'<br><span style="color:#ffc800;font-size:.67rem">ETA: {j["est_eta"]}</span>' if j["status"] in ("running","queued") and j.get("est_eta") else ""
            rows+=(f'<tr><td class="mono" style="max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{(j["filename"] or "")[:24]}</td>'
                   f'<td><span class="badge {bc}">{j["status"]}</span>{eta_cell}</td>'
                   f'<td>{j["mode"] or ""}</td><td class="mono">{j["attempts"] or 0:,}</td>'
                   f'<td class="mono">{j["speed"] or 0:,}/s</td><td>{pc}</td>'
                   f'<td class="mono">{fmt_t(j["elapsed"])}</td>'
                   f'<td><a href="/job/{j["id"]}" class="btn bo bsm">View</a>{stop}{dl}</td></tr>')
        table=(f'<div style="overflow-x:auto"><table><thead><tr>'
               f'<th>File</th><th>Status</th><th>Mode</th><th>Attempts</th>'
               f'<th>Speed</th><th>Password</th><th>Time</th><th></th>'
               f'</tr></thead><tbody>{rows}</tbody></table></div>') if jobs else (
               '<p style="color:#3d5268;text-align:center;padding:1.5rem">Koi job nahi. <a href="/crack">Start karo</a></p>')
        js="function stopJob(id){if(!confirm('Cancel?'))return;fetch('/api/cancel/'+id,{method:'POST'}).then(r=>r.json()).then(d=>{if(d.ok)location.reload();else alert(d.error||'err')})}"
        if rn: js+="setTimeout(()=>location.reload(),5000);"
        js+=("setInterval(()=>fetch('/api/stats').then(r=>r.json()).then(d=>{"
             "if(q('gs-c'))q('gs-c').textContent=d.total_cracked.toLocaleString();"
             "if(q('gs-a'))q('gs-a').textContent=d.total_attempts.toLocaleString();"
             "}),7000);")
        body=(f'<div class="wrap">'
              +ads_banner("top")+
              f'<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:1rem">'
              f'<div><h1>Dashboard</h1><p class="sub">Hi <b style="color:#00c6ff">{u["username"]}</b> &#128075; — Background mein 24/7 kaam hota rahega!</p></div>'
              f'<a href="/crack" class="btn bp">+ New Job</a></div>'
              f'<div style="background:#090f18;border:1px solid #111e30;border-radius:8px;padding:.8rem 1rem;margin-bottom:.9rem">'
              f'<div style="display:flex;gap:2rem;font-family:monospace;font-size:.82rem">'
              f'<div>Cracked: <b style="color:#00e676" id="gs-c">{gs["total_cracked"]:,}</b></div>'
              f'<div>Attempts: <b style="color:#00c6ff" id="gs-a">{gs["total_attempts"]:,}</b></div>'
              f'<div>Jobs: <b style="color:#ff8c00">{gs["total_jobs"]:,}</b></div>'
              f'</div></div>'
              f'<div class="g4"><div class="stat"><div class="sv">{total}</div><div class="sl">Total</div></div>'
              f'<div class="stat"><div class="sv" style="color:#00e676">{fc}</div><div class="sl">Found</div></div>'
              f'<div class="stat"><div class="sv" style="color:#e0304a">{fl}</div><div class="sl">Failed</div></div>'
              f'<div class="stat"><div class="sv" style="color:#ff8c00">{rn}</div><div class="sl">Running</div></div></div>'
              f'<div class="card"><div class="ct">&#128203; Your Jobs</div>{table}</div>'
              +ads_banner("bottom")+
              f'</div>')
        return page(body,"Dashboard",True,u["username"],js)
    except Exception as e:
        L.error(f"Dashboard: {e}\n{traceback.format_exc()}"); return f'<h2 style="color:red;padding:2rem">{e}</h2>',500

@app.route("/crack")
@rlogin
def crack_page():
    u=me()
    def ck(items):
        h='<div class="ckg">'
        for v,l,c in items: h+=f'<label class="ckl"><input type="checkbox" value="{v}"{" checked" if c else ""}>{l}</label>'
        return h+'</div>'
    cs=[("lower","a-z",True),("upper","A-Z",False),("digits","0-9",True),("sym_india","Indian Sym",False),("sym","All Sym",False),("alnum","AlphaNum",False),("full","Full ASCII",False)]
    df=[("%d%m%Y","DDMMYYYY",True),("%Y%m%d","YYYYMMDD",True),("%d%m%y","DDMMYY",True),("%d/%m/%Y","DD/MM/YYYY",True),("%d%m","DDMM",True),("%Y","YYYY",True)]
    cc=[("+91","&#127470;&#127475; India",True),("+92","&#127477;&#127472; Pakistan",False),("+880","&#127463;&#127465; Bangladesh",False),("+1","&#127482;&#127480; USA",False),("+44","&#127468;&#127463; UK",False),("+971","&#127462;&#127466; UAE",False),("+966","&#127480;&#127462; Saudi",False),("+86","&#127464;&#127475; China",False),("+62","&#127470;&#127465; Indonesia",False),("+7","&#127479;&#127482; Russia",False)]
    seps=[("","None",True),("_","_",True),("-","-",True),(".",".",True),("@","@",True),("#","#",False)]
    gh_html='<div class="ckg">'+''.join(f'<label class="ckl"><input type="checkbox" value="{v}">{l}</label>' for v,l in [("top1m","SecLists 1M"),("top100k","100K"),("top10k","10K"),("best1050","Best 1050"),("xato1m","Xato 1M"),("probable","Probable 12K"),("weakpass","WeakPass"),("leaked","Leaked Gmail"),("rockyou","RockYou"),("common_3k","Common 3K"),("bt4","BT4"),("darkweb","DarkWeb 10K"),("top500","Top 500"),("hak5","Hak5 10K"),("kaonashi","Ashley Madison")])+'</div>'

    body=('<div class="wrap"><h1>New Crack Job</h1>'
          '<p class="sub">Upload karo — 24/7 background mein kaam hoga. ETA bhi dikhega!</p>'
          +ads_banner("top")+
          '<div class="aw">&#9888;&#65039; Sirf apni file ka use karein.</div>'
          '<div class="card" style="border-color:#1a3a1a"><div class="ct" style="color:#00e676">&#9889; Priority + Speed</div>'
          '<div style="font-family:monospace;font-size:.77rem;line-height:1.85;color:#bdd0e8">'
          '<div>1&#65039;&#8419; Common(200+) → 2&#65039;&#8419; Google-style → 3&#65039;&#8419; Smart Personal → 4&#65039;&#8419; Calendar</div>'
          '<div>5&#65039;&#8419; Keyboard → 6&#65039;&#8419; 15 GitHub Lists → 7&#65039;&#8419; Brute Force (100T+)</div>'
          '<div>Standard ZIP: <b style="color:#00e676">18k/sec</b> | AES-256: <b style="color:#ff8c00">200/sec</b> (hardware limit)</div>'
          '</div></div>'
          '<div class="card"><div class="ct">&#128193; File Upload (Max 200MB)</div>'
          '<div class="dz" id="dz" onclick="q(\'fi\').click()" '
          'ondragover="event.preventDefault();this.classList.add(\'dragover\')" '
          'ondragleave="this.classList.remove(\'dragover\')" ondrop="handleDrop(event)">'
          '<div style="font-size:2rem;margin-bottom:.3rem">&#128194;</div>'
          '<div style="color:#fff;font-weight:700">ZIP ya PDF — Drop here / Click</div>'
          '<div id="fn" style="color:#00c6ff;margin-top:.3rem;font-size:.82rem">Max 200MB</div>'
          '</div>'
          '<div class="upload-progress" id="uprog"><div style="font-size:.78rem;color:#bdd0e8;margin-bottom:.3rem" id="uprog-txt">Uploading...</div><div class="upload-bar"><div class="upload-fill" id="uprog-bar"></div></div></div>'
          '<input type="file" id="fi" accept=".zip,.pdf" class="hidden" onchange="showFile(this)"></div>'
          '<div class="card"><div class="ct">&#9876;&#65039; Attack Mode</div><div class="mg">'
          '<div class="mc sel" onclick="selMode(\'smart\',this)"><div class="mi">&#129504;</div><div class="mn_">Smart</div><div class="md_">Best choice</div></div>'
          '<div class="mc" onclick="selMode(\'calendar\',this)"><div class="mi">&#128197;</div><div class="mn_">Calendar</div><div class="md_">Dates</div></div>'
          '<div class="mc" onclick="selMode(\'mobile\',this)"><div class="mi">&#128241;</div><div class="mn_">Mobile</div><div class="md_">Phone</div></div>'
          '<div class="mc" onclick="selMode(\'dictionary\',this)"><div class="mi">&#128214;</div><div class="mn_">Dictionary</div><div class="md_">15 lists</div></div>'
          '<div class="mc" onclick="selMode(\'keyboard\',this)"><div class="mi">&#9000;&#65039;</div><div class="mn_">Keyboard</div><div class="md_">Walks</div></div>'
          '<div class="mc" onclick="selMode(\'brute\',this)"><div class="mi">&#128170;</div><div class="mn_">Brute</div><div class="md_">18k/sec</div></div>'
          '<div class="mc" onclick="selMode(\'hybrid\',this)"><div class="mi">&#128293;</div><div class="mn_">Hybrid ALL</div><div class="md_">Everything</div></div>'
          '</div><input type="hidden" id="mode" value="smart"></div>'
          '<div id="p-smart" class="card"><div class="ct">&#128100; Personal Info</div>'
          '<div class="ai">Auto: r4hul (leet), a1v2n3 (interleaved), Rahul@786. 50M+ combos!</div>'
          '<div class="g2">'
          '<div class="fg"><label>Naam / Name</label><input id="s-name" placeholder="Rahul, Mohammed, Priya"></div>'
          '<div class="fg"><label>Nickname</label><input id="s-nick" placeholder="rocky, lucky, rinku"></div>'
          '<div class="fg"><label>Date of Birth</label><input id="s-dob" placeholder="25/12/1990"></div>'
          '<div class="fg"><label>Mobile</label><input id="s-mob" placeholder="+919876543210"></div>'
          '<div class="fg"><label>City</label><input id="s-city" placeholder="Mumbai, Lahore"></div>'
          '<div class="fg"><label>Pet / Vehicle</label><input id="s-pet" placeholder="Tommy, Hero"></div>'
          '<div class="fg"><label>Favourite</label><input id="s-fav" placeholder="Cricket, Allah"></div>'
          '<div class="fg"><label>Lucky Number</label><input id="s-lucky" placeholder="786, 108, 420"></div>'
          '</div><div class="fg"><label>Other Keywords</label><input id="s-other" placeholder="company, school"></div></div>'
          f'<div id="p-calendar" class="card hidden"><div class="ct">&#128197; Calendar</div>'
          f'<div class="g3"><div class="fg"><label>Start Year</label><input id="c-sy" type="number" value="1940"></div>'
          f'<div class="fg"><label>End Year</label><input id="c-ey" type="number" value="2025"></div></div>'
          f'<div class="fg"><label>Prefix Words</label><textarea id="c-pre" rows="3" placeholder="rahul&#10;786&#10;maa"></textarea></div>'
          f'<div class="fg"><label>Suffix Words</label><textarea id="c-suf" rows="3" placeholder="@123&#10;786&#10;!"></textarea></div>'
          f'<div class="fg"><label>Date Formats</label>{ck(df)}</div>'
          f'<div class="fg"><label>Separators</label>{ck(seps)}</div></div>'
          f'<div id="p-mobile" class="card hidden"><div class="ct">&#128241; Mobile</div>'
          f'<div class="fg"><label>Specific Numbers</label><textarea id="m-nums" rows="3" placeholder="+919876543210"></textarea></div>'
          f'<div class="fg"><label>Countries</label>{ck(cc)}</div>'
          f'<div class="fg"><label>Density</label><input id="m-den" type="number" value="100" min="1" max="100"></div></div>'
          f'<div id="p-dictionary" class="card hidden"><div class="ct">&#128214; Dictionary + GitHub</div>'
          f'<div class="fg"><label>GitHub Lists</label>{gh_html}</div>'
          f'<div class="fg"><label>Extra URLs</label><textarea id="d-extra" rows="2" placeholder="https://raw.github..."></textarea></div></div>'
          f'<div id="p-brute" class="card hidden"><div class="ct">&#128170; Brute Force</div>'
          f'<div class="ai">Standard ZIP: 18k/sec | AES-256: 200/sec (hardware limit)</div>'
          f'<div class="fg"><label>Charset</label>{ck(cs)}</div>'
          f'<div class="fg"><label>Custom Chars</label><input id="b-cc" placeholder="@#786"></div>'
          f'<div class="g3"><div class="fg"><label>Min Length</label><input id="b-min" type="number" value="1" min="1" max="30"></div>'
          f'<div class="fg"><label>Max Length</label><input id="b-max" type="number" value="8" min="1" max="30"></div></div>'
          f'<div class="g2"><div class="fg"><label>Prefix</label><input id="b-pre" placeholder="rahul_"></div>'
          f'<div class="fg"><label>Suffix</label><input id="b-suf" placeholder="_786"></div></div>'
          f'<div class="ai mono" id="bf-est">Calculating...</div></div>'
          '<div class="card"><div class="fg"><label>Progress Frequency</label>'
          '<select id="freq"><option value="500" selected>Har 500</option><option value="1000">Har 1,000</option>'
          '<option value="2000">Har 2,000</option><option value="5000">Har 5,000</option></select></div>'
          '<div style="background:rgba(0,198,255,.07);border:1px solid rgba(0,198,255,.2);border-radius:6px;padding:.6rem;margin-bottom:.7rem;font-size:.78rem;color:#70d8ff">'
          '&#128276; Password milne pe browser notification milegi (allow karo)</div>'
          '<button onclick="submitJob()" class="btn bp bw" style="padding:.82rem;font-size:.9rem" id="sbtn">'
          '&#128640; Crack Job Start Karo — 24/7 Background!</button></div>'
          +ads_banner("bottom")+
          '</div>')

    js=("var mode='smart';var panels=['smart','calendar','mobile','dictionary','brute'];"
        "function selMode(m,el){document.querySelectorAll('.mc').forEach(c=>c.classList.remove('sel'));el.classList.add('sel');mode=m;"
        "panels.forEach(p=>{var e=q('p-'+p);if(e)e.classList.toggle('hidden',p!==m&&m!=='hybrid')});"
        "if(m==='hybrid')panels.forEach(p=>{var e=q('p-'+p);if(e)e.classList.remove('hidden')});calcBF();}"
        "function showFile(inp){var f=inp.files[0];if(f){var mb=(f.size/1024/1024).toFixed(1);"
        "if(f.size>200*1024*1024){alert('File too large! Max 200MB.Please compress first.');return;}"
        "q('fn').textContent='✅ '+f.name+' ('+mb+' MB)';}}"
        "function handleDrop(e){e.preventDefault();q('dz').classList.remove('dragover');"
        "if(e.dataTransfer.files.length){q('fi').files=e.dataTransfer.files;showFile(q('fi'));}}"
        "function calcBF(){var cs=new Set((chk('#p-brute .ckg .ckl input').join('')+(q('b-cc')?q('b-cc').value:'')).split(''));"
        "var n=Math.max(cs.size,2);var mn=parseInt(q('b-min')?q('b-min').value:1);"
        "var mx=parseInt(q('b-max')?q('b-max').value:8);var t=0;"
        "for(var i=mn;i<=mx;i++)t+=Math.pow(n,i);"
        "var s=Math.round(t/18000);var ts=s>86400?Math.round(s/86400)+'d':s>3600?Math.round(s/3600)+'h':s>60?Math.round(s/60)+'m':s+'s';"
        "var tstr=t>1e18?(t/1e18).toFixed(1)+' Quintillion':t>1e15?(t/1e15).toFixed(1)+' Quadrillion':t>1e12?(t/1e12).toFixed(1)+' Trillion':t>1e9?(t/1e9).toFixed(1)+' Billion':t>1e6?(t/1e6).toFixed(1)+' M':String(t);"
        "var el=q('bf-est');if(el)el.textContent='Estimated: '+tstr+' combos | ~'+ts+' @ 18k/s';}"
        "if('Notification' in window)Notification.requestPermission();"
        "function submitJob(){"
        "var fi=q('fi');if(!fi.files.length){alert('Pehle file select karo!');return;}"
        "var f=fi.files[0];if(f.size>200*1024*1024){alert('File too large! Max 200MB');return;}"
        "var cfg={mode:mode,progress_every:parseInt(q('freq').value),"
        "github_lists:chk('#p-dictionary .ckg .ckl input'),"
        "extra_wordlists:(q('d-extra')?q('d-extra').value:'').split('\\n').map(s=>s.trim()).filter(Boolean),"
        "user_info:{name:q('s-name')?q('s-name').value:'',nick:q('s-nick')?q('s-nick').value:'',"
        "dob:q('s-dob')?q('s-dob').value:'',mobile:q('s-mob')?q('s-mob').value:'',"
        "city:q('s-city')?q('s-city').value:'',pet:q('s-pet')?q('s-pet').value:'',"
        "fav:q('s-fav')?q('s-fav').value:'',lucky:q('s-lucky')?q('s-lucky').value:'',"
        "other:q('s-other')?q('s-other').value:''},"
        "calendar:{start_year:parseInt(q('c-sy')?q('c-sy').value:1940),"
        "end_year:parseInt(q('c-ey')?q('c-ey').value:2025),"
        "prefix_words:(q('c-pre')?q('c-pre').value:'').split('\\n').map(s=>s.trim()).filter(Boolean),"
        "suffix_words:(q('c-suf')?q('c-suf').value:'').split('\\n').map(s=>s.trim()).filter(Boolean),"
        "date_formats:chk('#p-calendar .ckg .ckl input'),"
        "separators:chk('#p-calendar .fg:last-child .ckl input')},"
        "mobile:{numbers:(q('m-nums')?q('m-nums').value:'').split('\\n').map(s=>s.trim()).filter(Boolean),"
        "country_codes:chk('#p-mobile .ckg .ckl input'),"
        "density:parseInt(q('m-den')?q('m-den').value:100)},"
        "brute:{charsets:chk('#p-brute .ckg .ckl input[type=checkbox]'),"
        "custom_chars:q('b-cc')?q('b-cc').value:'',"
        "min_len:parseInt(q('b-min')?q('b-min').value:1),"
        "max_len:parseInt(q('b-max')?q('b-max').value:8),"
        "prefix:q('b-pre')?q('b-pre').value:'',"
        "suffix:q('b-suf')?q('b-suf').value:''}};"
        "var fd=new FormData();fd.append('file',f);fd.append('config',JSON.stringify(cfg));"
        "var btn=q('sbtn');btn.disabled=true;btn.textContent='Uploading...';"
        "var prog=q('uprog');prog.style.display='block';"
        "var xhr=new XMLHttpRequest();"
        "xhr.upload.onprogress=function(e){if(e.lengthComputable){"
        "var pct=Math.round(e.loaded/e.total*100);"
        "q('uprog-bar').style.width=pct+'%';"
        "q('uprog-txt').textContent='Uploading: '+pct+'%';}};"
        "xhr.onload=function(){"
        "prog.style.display='none';"
        "if(xhr.status===200){var d=JSON.parse(xhr.responseText);"
        "if(d.job_id)window.location.href='/job/'+d.job_id;"
        "else{alert('Error: '+(d.error||'?'));btn.disabled=false;btn.textContent='Retry';}}"
        "else{try{var e=JSON.parse(xhr.responseText);alert('Upload error: '+e.error);}catch(x){alert('Upload failed. File too large or network error.');}"
        "btn.disabled=false;btn.textContent='Retry';}};"
        "xhr.onerror=function(){prog.style.display='none';alert('Network error. Check connection.');btn.disabled=false;btn.textContent='Retry';};"
        "xhr.open('POST','/api/submit');xhr.send(fd);}"
        "calcBF();"
        "[q('b-min'),q('b-max'),q('b-cc')].forEach(el=>{if(el)el.addEventListener('input',calcBF)});"
        "document.querySelectorAll('#p-brute .ckg .ckl input').forEach(el=>el.addEventListener('change',calcBF));")
    return page(body,"New Job",True,u["username"],js)

@app.route("/job/<jid>")
@rlogin
def job_page(jid):
    try:
        u=me(); c=get_db()
        j=c.execute("SELECT * FROM jobs WHERE id=? AND user_id=?",(jid,u["id"])).fetchone()
        if not j: c.close(); abort(404)
        logs=list(reversed(c.execute("SELECT ts,msg FROM jlogs WHERE job_id=? ORDER BY id DESC LIMIT 100",(jid,)).fetchall()))
        c.close()
        found_html=""
        if j["found_pw"]:
            pw=str(j["found_pw"])
            dl_html=(f'<div class="dlbox"><div style="color:#00e676;font-size:.8rem;margin-bottom:.5rem">&#9989; Files ready!</div>'
                     f'<a href="/dl/{jid}" class="btn bg_ bw">&#8595; Download Extracted Files</a>'
                     f'<div style="color:#3d5268;font-size:.72rem;margin-top:.4rem">3 sec mein auto-delete</div></div>') if j["dl_ready"] else ""
            found_html=(f'<div class="fbox"><div style="font-size:.66rem;color:#3d5268;text-transform:uppercase;letter-spacing:.12em;margin-bottom:.4rem">&#127881; PASSWORD MIL GAYA!</div>'
                        f'<div class="fpw">{pw}</div>'
                        f'<p style="color:#3d5268;font-size:.78rem;margin-top:.5rem">Click: <span onclick="navigator.clipboard.writeText(\'{pw}\');this.textContent=\'Copied!\'" style="color:#00c6ff;cursor:pointer;font-family:monospace">{pw}</span></p>'
                        f'</div>{dl_html}')
        is_run=j["status"] in ("running","queued")
        cur=j["current_pw"] or "..."
        eta=j.get("est_eta","") or ""
        rtbox=""
        if is_run:
            rtbox=(f'<div class="rtbox"><div class="rt-label">&#128269; Live — Currently Trying:</div>'
                   f'<div class="rt-pw" id="rt-pw">{cur}</div>'
                   f'<div class="rt-info">Attempt #<span id="rt-cnt">{j["attempts"] or 0:,}</span>'
                   f' | <span id="rt-spd">{j["speed"] or 0:,}</span>/s'
                   f' | <span id="rt-el">{fmt_t(j["elapsed"])}</span></div></div>')
        eta_html=""
        if eta and eta not in ("Mil gaya!","Not found","Calculating...","") and is_run:
            eta_html=(f'<div class="eta-box"><span style="font-size:1.4rem">&#9200;</span>'
                      f'<div>ETA: <b style="color:#ffc800" id="eta-val">{eta}</b>'
                      f'<br><span style="color:#3d5268;font-size:.68rem">Wapas aao tab tak password mil sakta hai</span></div></div>')
        log_lines="".join(
            f'<div class="{"tok" if ("PASSWORD FOUND" in (l["msg"] or "").upper() or "CRACKED" in (l["msg"] or "").upper()) else "terr" if "error" in (l["msg"] or "").lower() else ""}">'
            f'<span style="color:#3d5268">{(l["ts"] or "")[-8:]}</span> {l["msg"] or ""}</div>\n'
            for l in logs)
        bc="sr" if j["status"]=="running" else("sf" if j["status"]=="found" else("se" if j["status"] in ("failed","error") else "sc"))
        cancel_btn='<button onclick="stopJob()" class="btn bd bw">&#128721; Cancel</button>' if is_run else ""
        copy_btn=(f'<button onclick="navigator.clipboard.writeText(\'{j["found_pw"]}\')" class="btn bg_ bw" style="margin-top:.5rem">&#128203; Copy Password</button>') if j["found_pw"] else ""
        aes_warn=('<div class="aw" style="font-size:.79rem">&#128274; AES-256 ZIP: ~200/sec — hardware crypto limit. Normal hai.</div>') if j.get("use_aes") else ""
        body=(f'<div class="wrap">'
              +ads_banner("top")+
              f'<div style="display:flex;align-items:center;gap:.6rem;margin-bottom:1.2rem">'
              f'<a href="/dashboard" class="btn bo bsm">&#8592; Dashboard</a>'
              f'<h1 style="font-size:1.3rem">Job Details</h1>'
              f'<span class="badge {bc}">{j["status"]}</span></div>'
              +aes_warn+found_html+rtbox+eta_html+
              f'<div class="g4"><div class="stat"><div class="sv mono" id="sv-a">{j["attempts"] or 0:,}</div><div class="sl">Attempts</div></div>'
              f'<div class="stat"><div class="sv mono" id="sv-s">{j["speed"] or 0:,}</div><div class="sl">Speed/s</div></div>'
              f'<div class="stat"><div class="sv mono" id="sv-t">{fmt_t(j["elapsed"])}</div><div class="sl">Time</div></div>'
              f'<div class="stat"><div class="sv mono" style="color:#ff8c00" id="sv-st">{j["status"]}</div><div class="sl">Status</div></div></div>'
              f'<div class="card"><div style="display:flex;justify-content:space-between;margin-bottom:.28rem">'
              f'<span style="font-size:.76rem;color:#3d5268">Progress</span>'
              f'<span id="pg-l" style="font-size:.76rem;color:#00c6ff">{j["attempts"] or 0:,} tried</span>'
              f'</div><div class="pgw"><div class="pgf" id="pgb" style="width:3%"></div></div></div>'
              f'<div class="g2"><div class="card"><div class="ct">&#128196; Info</div>'
              f'<table><tr><td style="color:#3d5268;font-size:.78rem;padding:.26rem 0">File</td><td class="mono" style="font-size:.78rem">{j["filename"] or "&mdash;"}</td></tr>'
              f'<tr><td style="color:#3d5268;font-size:.78rem;padding:.26rem 0">Type</td><td style="font-size:.78rem">{j["filetype"] or "&mdash;"} {"(AES-256)" if j.get("use_aes") else ""}</td></tr>'
              f'<tr><td style="color:#3d5268;font-size:.78rem;padding:.26rem 0">Mode</td><td style="font-size:.78rem">{j["mode"] or "&mdash;"}</td></tr>'
              f'<tr><td style="color:#3d5268;font-size:.78rem;padding:.26rem 0">Size</td><td style="font-size:.78rem">{(j["filesize"] or 0)//1024} KB</td></tr></table></div>'
              f'<div class="card"><div class="ct">&#9881;&#65039; Actions</div>'
              +cancel_btn+copy_btn+
              f'<a href="/crack" class="btn bo bw" style="margin-top:.5rem">+ Naya Job</a></div></div>'
              f'<div class="card"><div class="ct">&#128187; Log (Live)</div><div class="term" id="lb">{log_lines}</div></div>'
              +ads_banner("bottom")+
              f'</div>')
        js=(f"var JID='{jid}',JST='{j['status']}',pgA=3;"
            "function stopJob(){if(!confirm('Cancel?'))return;"
            "fetch('/api/cancel/'+JID,{method:'POST'}).then(r=>r.json()).then(d=>{if(d.ok)location.reload();else alert(d.error||'err');});}"
            "function poll(){fetch('/api/progress/'+JID).then(r=>r.json()).then(d=>{"
            "if(!d||!d.status)return;"
            "if(q('sv-a'))q('sv-a').textContent=(d.attempts||0).toLocaleString();"
            "if(q('sv-s'))q('sv-s').textContent=(d.speed||0).toLocaleString();"
            "if(q('sv-t'))q('sv-t').textContent=d.ef||'0s';"
            "if(q('sv-st'))q('sv-st').textContent=d.status;"
            "pgA=Math.min(pgA+0.2,95);if(q('pgb'))q('pgb').style.width=pgA+'%';"
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
            "return '<div class=\"'+c+'\"><span style=\"color:#3d5268\">'+(l.ts||'').slice(-8)+'</span> '+l.msg+'</div>';"
            "}).join('');lb.scrollTop=lb.scrollHeight;}"
            "if(d.status==='found'){"
            "if('Notification' in window&&Notification.permission==='granted'){"
            "new Notification('Password Mil Gaya! 🎉',{body:'Password: '+d.found_pw,icon:'/health'});}"
            "if(q('pgb'))q('pgb').style.width='100%';"
            "setTimeout(()=>location.reload(),800);}"
            "else if(d.status==='running'||d.status==='queued')setTimeout(poll,2000);"
            "}).catch(()=>setTimeout(poll,5000));}"
            "if(JST==='running'||JST==='queued')setTimeout(poll,1500);"
            "var lb=document.getElementById('lb');if(lb)lb.scrollTop=lb.scrollHeight;"
            "if('Notification' in window)Notification.requestPermission();")
        return page(body,f"Job — {j['filename'] or 'Unknown'}",True,u["username"],js)
    except Exception as e:
        L.error(f"Job {jid}: {e}\n{traceback.format_exc()}")
        return f'<h2 style="color:red;padding:2rem">{e}</h2>',500

@app.route("/dl/<jid>")
@rlogin
def dl(jid):
    u=me(); c=get_db()
    j=c.execute("SELECT * FROM jobs WHERE id=? AND user_id=?",(jid,u["id"])).fetchone(); c.close()
    if not j or not j["dl_ready"]: abort(404)
    dl_path=j["dl_path"]
    if not dl_path or not Path(dl_path).exists(): abort(404)
    fname=(j["filename"] or "file").replace(".zip","")+"_extracted.zip"
    def after():
        try: Path(dl_path).unlink(missing_ok=True); jset(jid,dl_ready=0,dl_path="")
        except: pass
    resp=send_file(dl_path,as_attachment=True,download_name=fname)
    threading.Timer(3.0,after).start()
    return resp

@app.route("/api/submit",methods=["POST"])
@rlogin
def api_submit():
    try:
        u=me()
        if "file" not in request.files:
            return jsonify({"error":"No file received. Please try again."}),400
        f=request.files["file"]
        if not f or not f.filename:
            return jsonify({"error":"Empty file. Please select a file."}),400
        name=secure_filename(f.filename); ext=Path(name).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            return jsonify({"error":"Only .zip and .pdf files allowed"}),400
        # Save to temp first
        tmp_path=str(UPLOAD/f"{uuid.uuid4()}{ext}")
        try:
            f.save(tmp_path)
            size=Path(tmp_path).stat().st_size
            if size==0:
                Path(tmp_path).unlink(missing_ok=True)
                return jsonify({"error":"Empty file uploaded"}),400
            if size>MAX_UPLOAD_MB*1024*1024:
                Path(tmp_path).unlink(missing_ok=True)
                return jsonify({"error":f"File too large. Max {MAX_UPLOAD_MB}MB"}),400
        except Exception as e:
            return jsonify({"error":f"Upload failed: {str(e)}"}),500
        try: cfg=json.loads(request.form.get("config","{}"))
        except: cfg={}
        jid=str(uuid.uuid4())
        cfg["file_type"]=ext.lstrip(".")
        c=get_db()
        c.execute("INSERT INTO jobs(id,user_id,filename,filetype,filesize,status,mode,cfg) VALUES(?,?,?,?,?,?,?,?)",
                  (jid,u["id"],name,ext.lstrip("."),size,"queued",cfg.get("mode","smart"),json.dumps(cfg)))
        c.commit(); c.close()
        with JLOCK: JOBS[jid]={"cancel":threading.Event()}
        threading.Thread(target=run_job,args=(jid,tmp_path,cfg),daemon=True,name=f"J{jid[:6]}").start()
        L.info(f"Job {jid}: {name} ({size//1024}KB) by {u['username']}")
        return jsonify({"job_id":jid,"message":"Job started!"})
    except Exception as e:
        L.error(f"Submit: {e}\n{traceback.format_exc()}")
        return jsonify({"error":f"Server error: {str(e)}"}),500

@app.route("/api/progress/<jid>")
@rlogin
def api_progress(jid):
    try:
        u=me(); c=get_db()
        j=c.execute("SELECT * FROM jobs WHERE id=? AND user_id=?",(jid,u["id"])).fetchone()
        if not j: c.close(); return jsonify({"error":"Not found"}),404
        logs=list(reversed(c.execute("SELECT ts,msg FROM jlogs WHERE job_id=? ORDER BY id DESC LIMIT 60",(jid,)).fetchall()))
        c.close()
        return jsonify({"status":j["status"],"attempts":j["attempts"] or 0,"speed":j["speed"] or 0,
                        "ef":fmt_t(j["elapsed"]),"cpw":j["current_pw"] or "",
                        "found_pw":j["found_pw"],"dl_ready":j["dl_ready"],"eta":j.get("est_eta",""),
                        "use_aes":j.get("use_aes",0),
                        "logs":[{"ts":l["ts"],"msg":l["msg"]} for l in logs]})
    except Exception as e: return jsonify({"error":str(e)}),500

@app.route("/api/cancel/<jid>",methods=["POST"])
@rlogin
def api_cancel(jid):
    try:
        u=me(); c=get_db()
        j=c.execute("SELECT id FROM jobs WHERE id=? AND user_id=?",(jid,u["id"])).fetchone(); c.close()
        if not j: return jsonify({"error":"Not found"}),404
        with JLOCK:
            info=JOBS.get(jid)
            if info: info["cancel"].set()
        jset(jid,status="cancelled"); return jsonify({"ok":True})
    except Exception as e: return jsonify({"error":str(e)}),500

@app.route("/api/stats")
def api_stats():
    gs=get_stats()
    return jsonify({"total_cracked":gs["total_cracked"],"total_attempts":gs["total_attempts"],
                    "total_jobs":gs["total_jobs"],"active_jobs":len(JOBS)})

@app.route("/health",methods=["GET","HEAD"])
def health():
    return jsonify({"ok":True,"time":datetime.now().isoformat(),"jobs":len(JOBS),
                    "v":"20","data_dir":str(DATA_DIR)})

@app.errorhandler(413)
def e413(e):
    return jsonify({"error":f"File too large! Maximum {MAX_UPLOAD_MB}MB allowed. Please compress the file first."}),413

@app.errorhandler(404)
def e404(e): return '<p style="color:#e0304a;padding:2rem">404 Not Found</p><a href="/" style="color:#00c6ff;padding-left:2rem">Home</a>',404

@app.errorhandler(500)
def e500(e): L.error(f"500: {e}"); return '<p style="color:#e0304a;padding:2rem">Server Error</p><a href="/" style="color:#00c6ff;padding-left:2rem">Home</a>',500

if __name__=="__main__":
    PORT=int(os.environ.get("PORT",5000))
    print(f"\n{'='*58}\n  ZipPasswordCrack.in v20\n"
          f"  Data: {DATA_DIR}\n"
          f"  Upload: {UPLOAD}\n"
          f"  Google Ads: {'YES' if GOOGLE_ADS_CLIENT else 'Not configured'}\n"
          f"  http://0.0.0.0:{PORT}\n{'='*58}\n")
    app.run(host="0.0.0.0",port=PORT,debug=False,threaded=True,use_reloader=False)
