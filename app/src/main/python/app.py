import os, re, secrets, string, ipaddress
from functools import wraps
from flask import Flask, request, session, redirect, url_for, render_template, flash
import routeros_api

ROUTER_HOST = os.getenv("ROUTER_HOST", "10.10.10.254")
ROUTER_PORT = int(os.getenv("ROUTER_PORT", "8728"))
ROUTER_USER = os.getenv("ROUTER_USER", "manager")
ROUTER_PASSWORD = os.getenv("ROUTER_PASSWORD", "")
ROUTER_USE_TLS = os.getenv("ROUTER_USE_TLS", "false").lower() == "true"
APP_PASSWORD = os.getenv("APP_PASSWORD", "")
APP_HOST = os.getenv("APP_HOST", "127.0.0.1")
APP_PORT = int(os.getenv("APP_PORT", "8080"))

if not APP_PASSWORD or not ROUTER_PASSWORD:
    raise RuntimeError("Set APP_PASSWORD and ROUTER_PASSWORD in .env before starting.")

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", secrets.token_hex(32))
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict")

def connect():
    pool = routeros_api.RouterOsApiPool(
        ROUTER_HOST, username=ROUTER_USER, password=ROUTER_PASSWORD,
        port=ROUTER_PORT, plaintext_login=True,
        use_ssl=ROUTER_USE_TLS, ssl_verify=ROUTER_USE_TLS
    )
    return pool, pool.get_api()

def csrf():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(24)
    return session["csrf"]

def valid_csrf():
    return bool(session.get("csrf")) and secrets.compare_digest(
        session.get("csrf", ""), request.form.get("csrf", "")
    )

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("index"))
        return fn(*args, **kwargs)
    return wrapper

def fmt_bytes(value):
    try:
        n = float(value or 0)
    except (TypeError, ValueError):
        n = 0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} PB"

def safe_id(value):
    return isinstance(value, str) and value.startswith("*") and len(value) < 32

def get_dashboard_data():
    data = dict(
        logged_in=True, csrf=csrf(), router_host=ROUTER_HOST,
        router_error=None, identity="—", version="—", cpu="—", uptime="—",
        active=[], queues=[], vouchers=[], profiles=[], session_vouchers=session.get("last_vouchers", [])
    )
    pool = None
    try:
        pool, api = connect()
        ident = api.get_resource("/system/identity").get()
        res = api.get_resource("/system/resource").get()
        data["identity"] = ident[0].get("name", "—") if ident else "—"
        if res:
            data["version"] = res[0].get("version", "—")
            data["cpu"] = res[0].get("cpu-load", "—")
            data["uptime"] = res[0].get("uptime", "—")
        data["active"] = api.get_resource("/ip/hotspot/active").get()
        data["queues"] = api.get_resource("/queue/simple").get()
        data["vouchers"] = api.get_resource("/ip/hotspot/user").get()
        data["profiles"] = api.get_resource("/ip/hotspot/user/profile").get()
    except Exception as exc:
        data["router_error"] = str(exc)
    finally:
        if pool:
            pool.disconnect()
    return data

@app.get("/")
def index():
    if not session.get("logged_in"):
        return render_template("index.html", logged_in=False, csrf=csrf(),
                               router_host=ROUTER_HOST, router_error=None)
    data = get_dashboard_data()
    data["logged_in"] = True
    data["fmt_bytes"] = fmt_bytes
    return render_template("index.html", **data)

@app.post("/login")
def login():
    if not valid_csrf():
        flash("Form token invalid. Refresh and try again.", "error")
    elif secrets.compare_digest(request.form.get("password", ""), APP_PASSWORD):
        session.clear()
        session["logged_in"] = True
        session["csrf"] = secrets.token_urlsafe(24)
        flash("Dashboard login successful.")
    else:
        flash("Incorrect dashboard password.", "error")
    return redirect(url_for("index"))

@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

@app.post("/queue/add")
@login_required
def queue_add():
    if not valid_csrf():
        flash("Invalid form token.", "error"); return redirect(url_for("index"))
    name = request.form.get("name", "").strip()
    target = request.form.get("target", "").strip()
    limit = request.form.get("limit", "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,32}", name):
        flash("Queue name: use 1–32 letters, numbers, dot, dash or underscore.", "error")
        return redirect(url_for("index"))
    try:
        ipaddress.ip_network(target, strict=False)
    except ValueError:
        flash("Enter a valid IP or subnet, e.g. 10.10.11.11/32.", "error")
        return redirect(url_for("index"))
    if not re.fullmatch(r"[0-9]+[KMGkmg]?/[0-9]+[KMGkmg]?", limit):
        flash("Invalid speed. Example: 20M/20M.", "error"); return redirect(url_for("index"))
    pool = None
    try:
        pool, api = connect()
        existing = api.get_resource("/queue/simple").get(name=name)
        if existing:
            flash("A queue with that name already exists.", "error")
        else:
            api.get_resource("/queue/simple").add(
                name=name, target=target, **{"max-limit": limit},
                comment="Created by MikroTik Ultimate Manager"
            )
            flash(f"Queue {name} created with limit {limit}.")
    except Exception as exc:
        flash(f"Queue creation failed: {exc}", "error")
    finally:
        if pool: pool.disconnect()
    return redirect(url_for("index"))

@app.post("/queue/update")
@login_required
def queue_update():
    if not valid_csrf():
        flash("Invalid form token.", "error"); return redirect(url_for("index"))
    rid = request.form.get("id", "")
    limit = request.form.get("limit", "").strip()
    if not safe_id(rid) or not re.fullmatch(r"[0-9]+[KMGkmg]?/[0-9]+[KMGkmg]?", limit):
        flash("Invalid queue ID or speed. Example: 20M/20M.", "error")
        return redirect(url_for("index"))
    pool = None
    try:
        pool, api = connect()
        api.get_resource("/queue/simple").set(id=rid, **{"max-limit": limit})
        flash(f"Queue speed updated to {limit}.")
    except Exception as exc:
        flash(f"Queue update failed: {exc}", "error")
    finally:
        if pool: pool.disconnect()
    return redirect(url_for("index"))

@app.post("/queue/delete")
@login_required
def queue_delete():
    if not valid_csrf():
        flash("Invalid form token.", "error"); return redirect(url_for("index"))
    rid = request.form.get("id", "")
    if not safe_id(rid):
        flash("Invalid queue ID.", "error"); return redirect(url_for("index"))
    pool = None
    try:
        pool, api = connect()
        api.get_resource("/queue/simple").remove(id=rid)
        flash("Selected simple queue removed.")
    except Exception as exc:
        flash(f"Queue delete failed: {exc}", "error")
    finally:
        if pool: pool.disconnect()
    return redirect(url_for("index"))

@app.post("/voucher/create")
@login_required
def voucher_create():
    if not valid_csrf():
        flash("Invalid form token.", "error"); return redirect(url_for("index"))
    try:
        count = int(request.form.get("count", "1"))
    except ValueError:
        count = 0
    profile = request.form.get("profile", "").strip()
    prefix = request.form.get("prefix", "V").strip().upper()
    uptime = request.form.get("uptime", "").strip()
    data_limit = request.form.get("data_limit", "").strip()
    if not 1 <= count <= 50:
        flash("Generate between 1 and 50 vouchers per batch.", "error")
        return redirect(url_for("index"))
    if not re.fullmatch(r"[A-Z0-9]{0,5}", prefix):
        flash("Prefix can contain up to 5 letters or numbers.", "error")
        return redirect(url_for("index"))
    if uptime and not re.fullmatch(r"[0-9]+[smhdw]([0-9]+[smhdw])*", uptime):
        flash("Uptime format example: 1h, 12h, 1d.", "error")
        return redirect(url_for("index"))
    if data_limit and not re.fullmatch(r"[0-9]+[KMGkmg]?", data_limit):
        flash("Data limit format example: 500M or 2G.", "error")
        return redirect(url_for("index"))
    pool = None
    made = []
    try:
        pool, api = connect()
        profs = api.get_resource("/ip/hotspot/user/profile").get()
        if profile and not any(p.get("name") == profile for p in profs):
            flash("Selected Hotspot profile was not found.", "error")
            return redirect(url_for("index"))
        users = api.get_resource("/ip/hotspot/user")
        alphabet = string.ascii_uppercase + string.digits
        for _ in range(count):
            code = prefix + "".join(secrets.choice(alphabet) for _ in range(8))
            attrs = {"name": code, "password": code, "comment": "Voucher created by Ultimate Manager"}
            if profile: attrs["profile"] = profile
            if uptime: attrs["limit-uptime"] = uptime
            if data_limit: attrs["limit-bytes-total"] = str(int(float(data_limit[:-1]) * {"K":1024,"M":1048576,"G":1073741824}[data_limit[-1].upper()])) if data_limit[-1].isalpha() else data_limit
            users.add(**attrs)
            made.append(code)
        session["last_vouchers"] = made
        flash(f"{len(made)} voucher(s) created. Username and password are the same code.")
    except Exception as exc:
        flash(f"Voucher creation failed: {exc}", "error")
    finally:
        if pool: pool.disconnect()
    return redirect(url_for("index"))

@app.post("/voucher/delete")
@login_required
def voucher_delete():
    if not valid_csrf():
        flash("Invalid form token.", "error"); return redirect(url_for("index"))
    rid = request.form.get("id", "")
    if not safe_id(rid):
        flash("Invalid voucher ID.", "error"); return redirect(url_for("index"))
    pool = None
    try:
        pool, api = connect()
        api.get_resource("/ip/hotspot/user").remove(id=rid)
        flash("Selected Hotspot voucher/user removed.")
    except Exception as exc:
        flash(f"Voucher deletion failed: {exc}", "error")
    finally:
        if pool: pool.disconnect()
    return redirect(url_for("index"))

@app.get("/vouchers.txt")
@login_required
def vouchers_download():
    from flask import Response
    codes = session.get("last_vouchers", [])
    if not codes:
        flash("No newly generated voucher batch to download.", "error")
        return redirect(url_for("index"))
    body = "username,password\n" + "".join(f"{c},{c}\n" for c in codes)
    return Response(body, mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=vouchers.csv"})

if __name__ == "__main__":
    print(f"Dashboard: http://{APP_HOST}:{APP_PORT}")
    app.run(host=APP_HOST, port=APP_PORT, debug=False)
