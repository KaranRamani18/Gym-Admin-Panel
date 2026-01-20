from flask import Flask, render_template, request, redirect, url_for, session, flash, g
import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import date, timedelta, datetime
import os
from PIL import Image, ImageDraw, ImageFont
import random
import sys
import requests
import webbrowser
import threading
import base64  # NEW: for image upload to Wasender
import time 

# ========================
# RESOURCE PATH (EXE SAFE)
# ========================
def resource_path(relative):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative)
    return os.path.join(os.path.abspath("."), relative)

# ========================
# FLASK INIT
# ========================
app = Flask(__name__)
app.secret_key = "mysecretkey123"

DATABASE = resource_path("gym.db")

# ========================
# DB CONNECTION HELPERS
# ========================
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(error):
    db = g.pop("db", None)
    if db:
        db.close()

# ========================
# WASENDER API CONFIG
# ========================
API_KEY = "ca73a268707ec80894108cffcc8133b988873ad39ce373a72cd5a32dc0ad594a"
SEND_URL = "https://wasenderapi.com/api/send-message"
UPLOAD_URL = "https://wasenderapi.com/api/upload"

def format_whatsapp_number(number: str) -> str:
    """
    Convert stored number to E.164 format.
    Your DB stores only 10-digit Indian numbers like 9876543210.
    This will convert them to +919876543210.
    """
    s = str(number).strip().replace(" ", "")
    # Already in +E.164
    if s.startswith("+"):
        return s
    # 12 digits starting with 91 -> add +
    if len(s) == 12 and s.startswith("91"):
        return "+" + s
    # 10-digit Indian mobile -> prefix +91
    if len(s) == 10 and s.isdigit():
        return "+91" + s
    # Fallback: return as-is
    return s

# ========================
# WASENDER TEXT MESSAGE API
# ========================
def send_whatsapp_text(number, message):
    phone = format_whatsapp_number(number)

    payload = {
        "to": phone,
        "text": message
    }

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }

    try:
        r = requests.post(SEND_URL, json=payload, headers=headers, timeout=30)
        print("Wasender TEXT Response:", r.status_code, r.text)
        return r.status_code == 200
    except Exception as e:
        print("Error sending text message:", e)
        return False


# ========================
# WASENDER IMAGE HELPERS
# ========================
def wasender_upload_image(image_path: str) -> str:
    """
    Upload a local image file to WasenderAPI and return the temporary public URL.
    Uses POST /api/upload with Base64 JSON body.
    """
    try:
        with open(image_path, "rb") as f:
            img_bytes = f.read()
    except Exception as e:
        print("Error reading image file:", image_path, e)
        raise

    b64_str = base64.b64encode(img_bytes).decode("utf-8")

    payload = {
        # include full data URL so API can detect mimetype
        "base64": f"data:image/jpeg;base64,{b64_str}"
    }

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }

    r = requests.post(UPLOAD_URL, json=payload, headers=headers, timeout=60)
    print("Wasender UPLOAD Response:", r.status_code, r.text)

    r.raise_for_status()
    data = r.json()

    if not data.get("success"):
        raise RuntimeError(f"Wasender upload failed: {data}")

    # API returns publicUrl which is valid for a limited time
    return data["publicUrl"]


def send_whatsapp_image(number, image_url: str, caption: str = "") -> bool:
    """
    Send an image message with optional caption using WasenderAPI.
    Uses POST /api/send-message with imageUrl.
    """
    phone = format_whatsapp_number(number)

    payload = {
        "to": phone,
        "imageUrl": image_url
    }
    if caption:
        payload["text"] = caption

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }

    try:
        r = requests.post(SEND_URL, json=payload, headers=headers, timeout=30)
        print("Wasender IMAGE Response:", r.status_code, r.text)
        return r.status_code == 200
    except Exception as e:
        print("Error sending image message:", e)
        return False


# ========================
# LOGIN ROUTES
# ========================
@app.route("/", methods=["GET", "POST"])
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        db = get_db()
        user = db.execute("SELECT * FROM admins WHERE username=?", (username,)).fetchone()

        if user and check_password_hash(user["password_hash"], password):
            session["admin_id"] = user["id"]
            session["username"] = user["username"]
            flash("Login successful!", "success")
            return redirect(url_for("dashboard"))

        flash("Invalid username or password", "danger")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out successfully.", "info")
    return redirect(url_for("login"))


# ========================
# ADD MEMBER
# ========================
@app.route("/add", methods=["GET", "POST"])
def add_member():
    if "admin_id" not in session:
        return redirect(url_for("login"))

    if request.method == "POST":
        name = request.form["name"]
        mobile = request.form["mobile"]
        plan = request.form["plan"]
        package = request.form["package"]
        joined_date = request.form["joined_date"]
        end_date = request.form["end_date"]

        if not all([name, mobile, plan, package, joined_date, end_date]):
            flash("Please fill all fields.", "warning")
        else:
            db = get_db()
            db.execute("""
                INSERT INTO members (name, mobile, plan, package, joined_date, end_date)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (name, mobile, plan, package, joined_date, end_date))
            db.commit()

            flash("Member added!", "success")
            return redirect(url_for("view_members"))

    return render_template("add_member.html")


# ========================
# EDIT MEMBER
# ========================
@app.route("/edit/<int:member_id>", methods=["GET", "POST"])
def edit_member(member_id):
    if "admin_id" not in session:
        return redirect(url_for("login"))

    db = get_db()
    member = db.execute("SELECT * FROM members WHERE id=?", (member_id,)).fetchone()
    if not member:
        flash("Member not found!", "danger")
        return redirect(url_for("view_members"))

    if request.method == "POST":
        name = request.form["name"]
        mobile = request.form["mobile"]
        plan = request.form["plan"]
        package = request.form["package"]
        joined_date = request.form["joined_date"]
        end_date = request.form["end_date"]

        if not mobile.isdigit() or len(mobile) != 10:
            flash("Mobile must be 10 digits!", "warning")
            return redirect(url_for("edit_member", member_id=member_id))

        if not all([name, mobile, plan, package, joined_date, end_date]):
            flash("Fill all fields!", "warning")
            return redirect(url_for("edit_member", member_id=member_id))

        db.execute("""
            UPDATE members
            SET name=?, mobile=?, plan=?, package=?, joined_date=?, end_date=?
            WHERE id=?
        """, (name, mobile, plan, package, joined_date, end_date, member_id))
        db.commit()

        flash("Updated successfully!", "success")
        return redirect(url_for("view_members"))

    return render_template("edit_member.html", member=member)


# ========================
# DELETE MEMBER
# ========================
@app.route("/delete/<int:member_id>")
def delete_member(member_id):
    if "admin_id" not in session:
        return redirect(url_for("login"))

    db = get_db()
    db.execute("DELETE FROM members WHERE id=?", (member_id,))
    db.commit()

    flash("Member deleted!", "info")
    return redirect(url_for("view_members"))


# ========================
# VIEW MEMBERS
# ========================
@app.route("/members")
def view_members():
    if "admin_id" not in session:
        return redirect(url_for("login"))

    search = request.args.get("search", "").strip()
    db = get_db()

    if search:
        members = db.execute("""
            SELECT * FROM members
            WHERE name LIKE ? OR mobile LIKE ?
            ORDER BY date(end_date)
        """, (f"%{search}%", f"%{search}%")).fetchall()
    else:
        members = db.execute("SELECT * FROM members ORDER BY date(end_date)").fetchall()

    today = date.today()
    out = []

    for m in members:
        try:
            ed = datetime.strptime(m["end_date"], "%Y-%m-%d").date()
            days_left = (ed - today).days
        except:
            days_left = 0

        row = dict(m)
        row["days_left"] = days_left
        out.append(row)

    return render_template("view_members.html", members=out, search_query=search)


# ========================
# DASHBOARD
# ========================
@app.route("/dashboard")
def dashboard():
    if "admin_id" not in session:
        return redirect(url_for("login"))

    db = get_db()

    total = db.execute("SELECT COUNT(*) FROM members").fetchone()[0]
    today = date.today().isoformat()

    active = db.execute("SELECT COUNT(*) FROM members WHERE date(end_date) >= date(?)", (today,)).fetchone()[0]
    expired = db.execute("SELECT COUNT(*) FROM members WHERE date(end_date) < date(?)", (today,)).fetchone()[0]

    cutoff = (date.today() + timedelta(days=4)).isoformat()
    soon = db.execute("""
        SELECT COUNT(*) FROM members
        WHERE date(end_date) >= date(?) AND date(end_date) <= date(?)
    """, (today, cutoff)).fetchone()[0]

    return render_template("dashboard.html",
                           total_members=total,
                           active_members=active,
                           expired_members=expired,
                           expiring_soon=soon)


# =====================================================
# GENERATE INVOICE IMAGE
# =====================================================
GENERATED_DIR = resource_path(os.path.join("static", "generated"))
if not os.path.exists(GENERATED_DIR):
    os.makedirs(GENERATED_DIR, exist_ok=True)

def generate_invoice(member):
    TEMPLATE_PATH = resource_path(os.path.join("static", "template_invoice.jpg.jpg"))

    base = Image.open(TEMPLATE_PATH).convert("RGBA")
    W, H = base.size

    img = Image.new("RGBA", base.size)
    img.paste(base, (0, 0))
    draw = ImageDraw.Draw(img)

    font_path = resource_path("fonts/NotoSansGujarati-Regular.ttf")
    try:
        font_bill = ImageFont.truetype(font_path, 36)
        font_value = ImageFont.truetype(font_path, 40)
    except:
        font_bill = ImageFont.load_default()
        font_value = ImageFont.load_default()

    name = member["name"]
    mobile = member["mobile"]
    plan = member["plan"]
    package = member["package"]
    joined = member["joined_date"]
    endd = member["end_date"]

    joined_fmt = datetime.strptime(joined, "%Y-%m-%d").strftime("%d/%m/%Y")
    end_fmt = datetime.strptime(endd, "%Y-%m-%d").strftime("%d/%m/%Y")

    draw.text((150, 360), name, font=font_value, fill="black")
    draw.text((150, 420), f"Mobile: {mobile}", font=font_bill, fill="black")

    y = 980
    draw.text((200, y), plan, font=font_value, fill="black")
    draw.text((600, y), package, font=font_value, fill="black")
    draw.text((1150, y), joined_fmt, font=font_value, fill="black")
    draw.text((1600, y), end_fmt, font=font_value, fill="black")

    # STATUS BADGE
    try:
        ed = datetime.strptime(endd, "%Y-%m-%d").date()
        diff = (ed - date.today()).days

        if diff < 0:
            status = "Expired"
        elif diff <= 4:
            status = "Expiring Soon"
        else:
            status = "Active"
    except:
        status = "Unknown"

    draw.rectangle([2000, y, 2350, y + 70], fill=(255, 0, 0))
    draw.text((2020, y + 10), status, font=font_value, fill="white")

    # =====================================================
    #   📌 ADD INSTAGRAM LOGO + 2 USERNAMES (BOTTOM-RIGHT)
    # =====================================================

    try:
        insta_logo_path = resource_path(os.path.join("static", "icons", "instagram.png"))
        insta_logo = Image.open(insta_logo_path).convert("RGBA")

        # Resize logo
        logo_w, logo_h = 70, 70
        insta_logo = insta_logo.resize((logo_w, logo_h))

        padding = 60  # distance from bottom-right corner

        # Logo position (bottom-right)
        logo_x = W - logo_w - padding
        logo_y = H - logo_h - padding

        # Paste logo
        img.paste(insta_logo, (logo_x, logo_y), insta_logo)

        # 2 Instagram usernames below each other
        insta_text = "@fitankeshparmar\n@extremegymmadhuram"

        font_instagram = ImageFont.truetype(font_path, 40)

        # Text position slightly left of the logo
        text_x = logo_x - 420
        text_y = logo_y + 5

        draw.text((text_x, text_y), insta_text, font=font_instagram, fill="black")

    except Exception as e:
        print("ERROR adding Instagram logo:", e)

    # =====================================================

    out_path = resource_path(os.path.join("static", "generated", f"invoice_{member['id']}.jpg"))
    img.convert("RGB").save(out_path, quality=95)

    return out_path


# =====================================================
# GENERATE ALL IMAGES (ONLY EXPIRING IN 1–4 DAYS)
# =====================================================
@app.route("/generate_all_images")
def generate_all_images():
    db = get_db()
    today = date.today()
    cutoff = today + timedelta(days=4)

    members = db.execute("""
        SELECT * FROM members
        WHERE date(end_date) >= date(?) 
        AND date(end_date) <= date(?)
    """, (today, cutoff)).fetchall()

    if not members:
        return render_template("no_images_to_generate.html")

    count = 0
    for m in members:
        generate_invoice(m)
        count += 1

    return render_template("generate_all_done.html", count=count)


# =====================================================
# SEND WHATSAPP REMINDERS (TEXT ONLY)
# =====================================================
@app.route("/send_reminder")
def send_reminder():
    db = get_db()
    today = date.today()
    cutoff = today + timedelta(days=4)

    members = db.execute("""
        SELECT * FROM members
        WHERE date(end_date) >= date(?)
        AND date(end_date) <= date(?)
    """, (today, cutoff)).fetchall()

    if not members:
        flash("No members expiring soon.", "info")
        return redirect(url_for("view_members"))

    sent = 0

    for m in members:
        msg = (
            f"Dear {m['name']}, your gym membership expires on {m['end_date']}. "
            f"Please renew soon. — Extreme Gym"
        )

        if send_whatsapp_text(m["mobile"], msg):
            sent += 1

    return render_template("reminder_sent.html", count=sent)


# =====================================================
# SEND WHATSAPP REMINDERS (IMAGE + CAPTION)
# =====================================================
@app.route("/send_reminder_images")
def send_reminder_images():
    if "admin_id" not in session:
        return redirect(url_for("login"))

    db = get_db()
    today = date.today()
    cutoff = today + timedelta(days=4)

    members = db.execute("""
        SELECT * FROM members
        WHERE date(end_date) >= date(?)
        AND date(end_date) <= date(?)
    """, (today, cutoff)).fetchall()

    if not members:
        flash("No members expiring soon.", "info")
        return redirect(url_for("view_members"))

    sent = 0

    for m in members:
        try:
            # 1) Generate personalized invoice image
            img_path = generate_invoice(m)

            # 🔍 DEBUG 1: print which member image is sending
            print("==========================================")
            print(f"Sending to Member: {m['name']} ({m['mobile']})")
            print(f"Local Image Path: {img_path}")

            # 2) Upload the image to Wasender
            public_url = wasender_upload_image(img_path)

            # 🔍 DEBUG 2: show upload public URL
            print(f"Uploaded Image URL: {public_url}")

            # 3) Caption text
            caption = (
                f"Dear {m['name']}, your {m['plan']} membership ends on {m['end_date']}. "
                f"Please renew soon. — Extreme Gym"
            )

            # 4) Send WhatsApp Image
            success = send_whatsapp_image(m['mobile'], public_url, caption)

            if success:
                sent += 1
                print("Status: ✔️ Image sent successfully")
            else:
                print("Status: ❌ Image send failed")

            # 🔥 IMPORTANT: Protect number (5 sec delay)
            time.sleep(5)

        except Exception as e:
            print("Error:", e)

    flash(f"Sent reminder images to {sent} members.", "success")
    return render_template("reminder_sent.html", count=sent)


# =====================================================
# RENEW MEMBER
# =====================================================
@app.route('/renew/<int:id>', methods=['GET', 'POST'])
def renew_member(id):
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    if request.method == 'POST':
        new_start = request.form['joined_date']
        new_end = request.form['end_date']

        cur.execute("""
            UPDATE members
            SET joined_date=?, end_date=?
            WHERE id=?
        """, (new_start, new_end, id))

        conn.commit()
        conn.close()
        flash("Renewed successfully!")
        return redirect(url_for('view_members'))

    cur.execute("SELECT * FROM members WHERE id=?", (id,))
    member = cur.fetchone()
    conn.close()

    return render_template("renew_member.html", member=member)


# =====================================================
# START FLASK (AUTO BROWSER OPEN)
# =====================================================


