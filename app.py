"""
ENAH website - Flask-applicatie
Start met: python app.py
Bereikbaar op: http://127.0.0.1:5000
"""

import os
import re
import sqlite3
from datetime import datetime

from flask import (
    Flask, render_template, request, redirect, url_for, flash, g
)
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    login_required, current_user
)
from werkzeug.security import generate_password_hash, check_password_hash

try:
    from flask_mail import Mail, Message
    MAIL_AVAILABLE = True
except ImportError:
    MAIL_AVAILABLE = False

# ---------------------------------------------------------------------------
# Configuratie
# ---------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, "enah.db")
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "mp4", "webm", "ogg"}
ALLOWED_VIDEO_EXTENSIONS = {"mp4", "webm", "ogg"}

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-key-verander-dit-in-productie")

# Standaard admin-account (wordt eenmalig aangemaakt als er nog geen admin bestaat).
# BELANGRIJK: verander dit wachtwoord na de eerste login, of zet het via omgevingsvariabelen.
DEFAULT_ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "cuneytmutlu")
DEFAULT_ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "elni6767")

# Mailconfiguratie (optioneel). Als MAIL_USERNAME niet is ingesteld,
# wordt de afspraak enkel gelogd in de console i.p.v. effectief gemaild.
app.config["MAIL_SERVER"] = os.environ.get("MAIL_SERVER", "smtp.gmail.com")
app.config["MAIL_PORT"] = int(os.environ.get("MAIL_PORT", 587))
app.config["MAIL_USE_TLS"] = True
app.config["MAIL_USERNAME"] = os.environ.get("MAIL_USERNAME")
app.config["MAIL_PASSWORD"] = os.environ.get("MAIL_PASSWORD")
app.config["MAIL_DEFAULT_SENDER"] = os.environ.get("MAIL_USERNAME")
app.config["MAIL_DEFAULT_RECIPIENT"] = os.environ.get("MAIL_DEFAULT_RECIPIENT", "info@enah.be")

mail = Mail(app) if MAIL_AVAILABLE else None

login_manager = LoginManager(app)
login_manager.login_view = "admin_login"
login_manager.login_message = "Log in als admin om deze pagina te bekijken."
login_manager.login_message_category = "error"

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def allowed_video_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_VIDEO_EXTENSIONS


def ensure_upload_folder():
    if not os.path.exists(UPLOAD_FOLDER):
        os.makedirs(UPLOAD_FOLDER)


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    db.execute("""
        CREATE TABLE IF NOT EXISTS afspraken (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            naam TEXT NOT NULL,
            email TEXT NOT NULL,
            telefoon TEXT,
            dienst TEXT,
            bericht TEXT,
            datum_aangemaakt TEXT NOT NULL
        )
    """)
    # Migratie: bestaande databases (van vóór de 'dienst'-kolom) krijgen de kolom erbij.
    try:
        db.execute("ALTER TABLE afspraken ADD COLUMN dienst TEXT")
    except sqlite3.OperationalError:
        pass  # kolom bestaat al
    db.execute("""
        CREATE TABLE IF NOT EXISTS autos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            merk TEXT NOT NULL,
            model TEXT NOT NULL,
            jaar INTEGER NOT NULL,
            prijs REAL NOT NULL,
            beschrijving TEXT,
            extra_info TEXT,
            datum_aangemaakt TEXT NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS auto_fotos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            auto_id INTEGER NOT NULL,
            foto_url TEXT NOT NULL,
            volgorde INTEGER DEFAULT 0,
            FOREIGN KEY (auto_id) REFERENCES autos(id) ON DELETE CASCADE
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS site_content (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content_key TEXT UNIQUE NOT NULL,
            content_value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS page_videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            page_name TEXT UNIQUE NOT NULL,
            video_url TEXT,
            updated_at TEXT NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS activity_contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            activity_name TEXT UNIQUE NOT NULL,
            address TEXT,
            phone TEXT,
            email TEXT,
            updated_at TEXT NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS activity_photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            activity_name TEXT NOT NULL,
            photo_url TEXT NOT NULL,
            photo_order INTEGER DEFAULT 0,
            updated_at TEXT NOT NULL
        )
    """)
    db.commit()

    # Maak een standaard-admin aan als er nog geen enkele admin bestaat.
    existing = db.execute("SELECT COUNT(*) AS c FROM admins").fetchone()
    if existing["c"] == 0:
        db.execute(
            "INSERT INTO admins (username, password_hash) VALUES (?, ?)",
            (DEFAULT_ADMIN_USERNAME, generate_password_hash(DEFAULT_ADMIN_PASSWORD)),
        )
        db.commit()
        print("=" * 60)
        print(" Admin-account aangemaakt:")
        print(f"   gebruikersnaam: {DEFAULT_ADMIN_USERNAME}")
        print(f"   wachtwoord:     {DEFAULT_ADMIN_PASSWORD}")
        print(" Log in via /admin/login en verander dit wachtwoord!")
        print("=" * 60)

    # Initialiseer standaard activiteit contactgegevens
    default_activity_contacts = {
        "lukoil": {
            "address": "Meylandtlaan 169, 3550 Heusen-Zolder",
            "phone": "+32 2 254 15 11",
            "email": "info@eu.lukoil.be"
        },
        "schoonmaak": {
            "address": "Genk, België",
            "phone": "+32 4 00 00 00",
            "email": "info@enah.be"
        },
        "transport": {
            "address": "Genk, België",
            "phone": "+32 4 00 00 00",
            "email": "info@enah.be"
        },
        "auto_onderhoud": {
            "address": "Genk, België",
            "phone": "+32 4 00 00 00",
            "email": "info@enah.be"
        },
        "auto_verkoop": {
            "address": "Genk, België",
            "phone": "+32 4 00 00 00",
            "email": "info@enah.be"
        }
    }
    
    for activity_name, contact_data in default_activity_contacts.items():
        existing = db.execute("SELECT COUNT(*) AS c FROM activity_contacts WHERE activity_name = ?", (activity_name,)).fetchone()
        if existing["c"] == 0:
            db.execute(
                "INSERT INTO activity_contacts (activity_name, address, phone, email, updated_at) VALUES (?, ?, ?, ?, ?)",
                (activity_name, contact_data["address"], contact_data["phone"], contact_data["email"], datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
    db.commit()

    # Initialiseer standaard site content
    default_content = {
        "contact_email": "info@enah.be",
        "contact_location": "Genk, België",
        "contact_company": "ENAH",
        "contact_phone": "+32 4 00 00 00",
        "contact_region": "België",
        "about_title": "ENAH – Ondernemen met vertrouwen en ambitie",
        "about_text": "ENAH is een familiebedrijf uit Genk dat sinds 2015 actief is en door de jaren heen is uitgegroeid tot een veelzijdige en betrouwbare onderneming. Met een sterke focus op kwaliteit, service en klanttevredenheid bouwen wij voortdurend verder aan onze onderneming en aan duurzame relaties met onze klanten en partners.\n\nOnze onderneming startte in 2015 met industriële reiniging en schoonmaak, aangevuld met transport van goederen. Dankzij onze persoonlijke aanpak, flexibiliteit en inzet wisten we al snel een stevige basis op te bouwen.\n\nVanuit deze basis hebben we onze activiteiten verder uitgebreid. Een belangrijke volgende stap was de overname van de franchise van het LUKOIL-tankstation in Heusden-Zolder. Hiermee versterkten we onze aanwezigheid in de regio en breidden we onze dienstverlening verder uit.\n\nVandaag zetten we onze ondernemersvisie voort met onze activiteiten binnen auto-onderhoud en autoverkoop. We streven ernaar onze klanten een complete en betrouwbare service te bieden, waarbij vakmanschap, transparantie en persoonlijke aandacht centraal staan.",
        "vision_title": "Onze visie",
        "vision_text": "Doorheen de jaren is één uitgangspunt steeds hetzelfde gebleven: kwaliteit leveren en vertrouwen opbouwen. Als familiebedrijf hechten wij veel belang aan een persoonlijke benadering en aan langdurige relaties met onze klanten.\n\nWij blijven investeren in onze dienstverlening en kijken voortdurend naar nieuwe mogelijkheden om verder te groeien. Met onze ervaring, ondernemingszin en betrokkenheid streven wij ernaar een betrouwbare partner te zijn voor zowel particuliere als professionele klanten.",
        "about_footer": "ENAH – een familiebedrijf met een sterke basis, een brede dienstverlening en een duidelijke blik op de toekomst."
    }
    
    for key, value in default_content.items():
        existing = db.execute("SELECT COUNT(*) AS c FROM site_content WHERE content_key = ?", (key,)).fetchone()
        if existing["c"] == 0:
            db.execute(
                "INSERT INTO site_content (content_key, content_value, updated_at) VALUES (?, ?, ?)",
                (key, value, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
    db.commit()

    # Initialiseer standaard activiteit foto's
    default_activity_photos = {
        "transport": [
            "/static/images/transport1.jpeg",
            "/static/images/transport2.jpeg"
        ],
        "schoonmaak": [
            "/static/images/schoonmaak1.jpeg",
            "/static/images/schoonmaak2.jpeg",
            "/static/images/schoonmaak3.jpeg",
            "/static/images/schoonmaak4.jpeg",
            "/static/images/schoonmaak5.jpeg",
            "/static/images/schoonmaak6.jpeg"
        ]
    }
    
    for activity_name, photo_urls in default_activity_photos.items():
        for i, photo_url in enumerate(photo_urls):
            existing = db.execute(
                "SELECT COUNT(*) AS c FROM activity_photos WHERE activity_name = ? AND photo_url = ?",
                (activity_name, photo_url)
            ).fetchone()
            if existing["c"] == 0:
                db.execute(
                    "INSERT INTO activity_photos (activity_name, photo_url, photo_order, updated_at) VALUES (?, ?, ?, ?)",
                    (activity_name, photo_url, i + 1, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                )
    db.commit()

    db.close()


# ---------------------------------------------------------------------------
# Flask-Login: admin-gebruiker
# ---------------------------------------------------------------------------

class AdminUser(UserMixin):
    def __init__(self, id, username):
        self.id = id
        self.username = username


@login_manager.user_loader
def load_user(user_id):
    db = get_db()
    row = db.execute("SELECT id, username FROM admins WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        return None
    return AdminUser(row["id"], row["username"])


# ---------------------------------------------------------------------------
# Content helpers
# ---------------------------------------------------------------------------

def get_site_content(key, default=""):
    db = get_db()
    row = db.execute("SELECT content_value FROM site_content WHERE content_key = ?", (key,)).fetchone()
    return row["content_value"] if row else default


def get_activity_contact(activity_name):
    db = get_db()
    row = db.execute("SELECT * FROM activity_contacts WHERE activity_name = ?", (activity_name,)).fetchone()
    if row:
        return {
            "address": row["address"],
            "phone": row["phone"],
            "email": row["email"]
        }
    return None


def get_activity_photos(activity_name):
    db = get_db()
    rows = db.execute(
        "SELECT photo_url FROM activity_photos WHERE activity_name = ? ORDER BY photo_order",
        (activity_name,)
    ).fetchall()
    return [row["photo_url"] for row in rows]


@app.context_processor
def inject_content():
    """Make content available in all templates"""
    db = get_db()
    content_rows = db.execute("SELECT content_key, content_value FROM site_content").fetchall()
    content_dict = {row["content_key"]: row["content_value"] for row in content_rows}
    return dict(site_content=content_dict)


# ---------------------------------------------------------------------------
# Publieke routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html", active_page="home")


@app.route("/about")
def about():
    return render_template("about.html", active_page="about")


@app.route("/contact")
def contact():
    dienst_voorkeur = request.args.get("dienst", "").strip()
    form_data = {"dienst": dienst_voorkeur} if dienst_voorkeur else None
    return render_template("contact.html", active_page="contact", form_data=form_data)


@app.route("/lukoil")
def lukoil():
    db = get_db()
    video_row = db.execute("SELECT video_url FROM page_videos WHERE page_name = ?", ("lukoil",)).fetchone()
    video_url = video_row["video_url"] if video_row else None
    contact = get_activity_contact("lukoil")
    return render_template("lukoil.html", active_page="lukoil", video_url=video_url, contact=contact)


@app.route("/schoonmaak")
def schoonmaak():
    contact = get_activity_contact("schoonmaak")
    photos = get_activity_photos("schoonmaak")
    return render_template("schoonmaak.html", active_page="schoonmaak", contact=contact, photos=photos)


@app.route("/transport")
def transport():
    contact = get_activity_contact("transport")
    photos = get_activity_photos("transport")
    return render_template("transport.html", active_page="transport", contact=contact, photos=photos)


@app.route("/auto-onderhoud")
def auto_onderhoud():
    contact = get_activity_contact("auto_onderhoud")
    return render_template("auto_onderhoud.html", active_page="auto_onderhoud", contact=contact)


@app.route("/auto-verkoop")
def auto_verkoop():
    db = get_db()
    autos = db.execute("SELECT * FROM autos ORDER BY datum_aangemaakt DESC").fetchall()
    contact = get_activity_contact("auto_verkoop")
    
    # Get photos for each auto
    autos_with_fotos = []
    for auto in autos:
        fotos = db.execute(
            "SELECT foto_url FROM auto_fotos WHERE auto_id = ? ORDER BY volgorde",
            (auto["id"],)
        ).fetchall()
        autos_with_fotos.append({
            "id": auto["id"],
            "merk": auto["merk"],
            "model": auto["model"],
            "jaar": auto["jaar"],
            "prijs": auto["prijs"],
            "beschrijving": auto["beschrijving"],
            "extra_info": auto["extra_info"],
            "datum_aangemaakt": auto["datum_aangemaakt"],
            "fotos": [f["foto_url"] for f in fotos]
        })
    
    return render_template("auto_verkoop.html", active_page="auto_verkoop", autos=autos_with_fotos, contact=contact)


# ---------------------------------------------------------------------------
# Afspraakformulier
# ---------------------------------------------------------------------------

@app.route("/afspraak", methods=["POST"])
def afspraak():
    naam = request.form.get("naam", "").strip()
    email = request.form.get("email", "").strip()
    telefoon = request.form.get("telefoon", "").strip()
    dienst = request.form.get("dienst", "").strip()
    bericht = request.form.get("bericht", "").strip()

    fouten = []
    if not naam:
        fouten.append("Naam is verplicht.")
    if not email:
        fouten.append("E-mail is verplicht.")
    elif not EMAIL_REGEX.match(email):
        fouten.append("Vul een geldig e-mailadres in.")
    if not dienst:
        fouten.append("Kies een dienst.")

    if fouten:
        for fout in fouten:
            flash(fout, "error")
        return render_template(
            "contact.html",
            active_page="contact",
            form_data={"naam": naam, "email": email, "telefoon": telefoon, "dienst": dienst, "bericht": bericht},
        ), 400

    db = get_db()
    db.execute(
        "INSERT INTO afspraken (naam, email, telefoon, dienst, bericht, datum_aangemaakt) VALUES (?, ?, ?, ?, ?, ?)",
        (naam, email, telefoon, dienst, bericht, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    db.commit()

    # E-mail versturen indien geconfigureerd, anders loggen naar console.
    onderwerp = f"Nieuwe afspraakaanvraag van {naam}"
    inhoud = (
        f"Naam: {naam}\nE-mail: {email}\nTelefoon: {telefoon}\nDienst: {dienst}\n\nBericht:\n{bericht}"
    )
    recipient = app.config.get("MAIL_DEFAULT_RECIPIENT", "info@enah.be")
    if mail is not None and app.config.get("MAIL_USERNAME"):
        try:
            msg = Message(onderwerp, recipients=[recipient], body=inhoud)
            mail.send(msg)
        except Exception as e:
            print(f"[MAIL-FOUT] Kon e-mail niet versturen: {e}")
            print(f"[MAIL-LOG]\n{inhoud}")
    else:
        print("[MAIL NIET GECONFIGUREERD - afspraak gelogd in console]")
        print(f"[MAIL-LOG] Onderwerp: {onderwerp}\n{inhoud}")

    flash("Bedankt! Uw aanvraag is verzonden. We nemen snel contact met u op.", "success")
    return redirect(url_for("contact"))


# ---------------------------------------------------------------------------
# Admin: login / logout
# ---------------------------------------------------------------------------

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if current_user.is_authenticated:
        return redirect(url_for("admin_dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        db = get_db()
        row = db.execute("SELECT * FROM admins WHERE username = ?", (username,)).fetchone()

        if row and check_password_hash(row["password_hash"], password):
            login_user(AdminUser(row["id"], row["username"]))
            flash("Welkom terug!", "success")
            next_page = request.args.get("next")
            return redirect(next_page or url_for("admin_dashboard"))

        flash("Ongeldige gebruikersnaam of wachtwoord.", "error")

    return render_template("admin/login.html", active_page="admin")


@app.route("/admin/logout")
@login_required
def admin_logout():
    logout_user()
    flash("U bent uitgelogd.", "success")
    return redirect(url_for("admin_login"))


# ---------------------------------------------------------------------------
# Admin: dashboard + autobeheer (toevoegen / verwijderen)
# ---------------------------------------------------------------------------

@app.route("/admin/dashboard")
@login_required
def admin_dashboard():
    db = get_db()
    autos = db.execute("SELECT * FROM autos ORDER BY datum_aangemaakt DESC").fetchall()
    afspraken = db.execute(
        "SELECT * FROM afspraken ORDER BY datum_aangemaakt DESC LIMIT 20"
    ).fetchall()
    return render_template(
        "admin/dashboard.html", active_page="admin", autos=autos, afspraken=afspraken
    )


@app.route("/admin/auto/toevoegen", methods=["GET", "POST"])
@login_required
def admin_auto_toevoegen():
    if request.method == "POST":
        merk = request.form.get("merk", "").strip()
        model = request.form.get("model", "").strip()
        jaar = request.form.get("jaar", "").strip()
        prijs = request.form.get("prijs", "").strip()
        beschrijving = request.form.get("beschrijving", "").strip()
        extra_info = request.form.get("extra_info", "").strip()
        
        # Handle multiple photo uploads
        foto_urls = []
        if "fotos" in request.files:
            files = request.files.getlist("fotos")
            ensure_upload_folder()
            for file in files:
                if file and file.filename and allowed_file(file.filename):
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
                    filename = f"{timestamp}_{file.filename}"
                    file.save(os.path.join(UPLOAD_FOLDER, filename))
                    foto_urls.append(f"/static/uploads/{filename}")

        fouten = []
        if not merk:
            fouten.append("Merk is verplicht.")
        if not model:
            fouten.append("Model is verplicht.")
        try:
            jaar_int = int(jaar)
        except (TypeError, ValueError):
            fouten.append("Bouwjaar moet een geldig getal zijn.")
            jaar_int = None
        try:
            prijs_float = float(prijs)
        except (TypeError, ValueError):
            fouten.append("Prijs moet een geldig getal zijn.")
            prijs_float = None

        if fouten:
            for fout in fouten:
                flash(fout, "error")
            return render_template("admin/add_auto.html", active_page="admin"), 400

        db = get_db()
        cursor = db.execute(
            "INSERT INTO autos (merk, model, jaar, prijs, beschrijving, extra_info, datum_aangemaakt) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (merk, model, jaar_int, prijs_float, beschrijving, extra_info,
             datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        auto_id = cursor.lastrowid
        
        # Save photos with ordering
        for volgorde, foto_url in enumerate(foto_urls):
            db.execute(
                "INSERT INTO auto_fotos (auto_id, foto_url, volgorde) VALUES (?, ?, ?)",
                (auto_id, foto_url, volgorde)
            )
        
        db.commit()
        flash(f"{merk} {model} is toegevoegd.", "success")
        return redirect(url_for("admin_dashboard"))

    return render_template("admin/add_auto.html", active_page="admin")


@app.route("/admin/auto/verwijderen/<int:auto_id>", methods=["POST"])
@login_required
def admin_auto_verwijderen(auto_id):
    db = get_db()
    db.execute("DELETE FROM autos WHERE id = ?", (auto_id,))
    db.commit()
    flash("Wagen verwijderd.", "success")
    return redirect(url_for("admin_dashboard"))


# ---------------------------------------------------------------------------
# Admin: content beheer
# ---------------------------------------------------------------------------

@app.route("/admin/content")
@login_required
def admin_content():
    db = get_db()
    content = db.execute("SELECT * FROM site_content ORDER BY content_key").fetchall()
    return render_template("admin/content.html", active_page="admin", content=content)


@app.route("/admin/content/bewerken/<content_key>", methods=["GET", "POST"])
@login_required
def admin_content_bewerken(content_key):
    db = get_db()
    
    if request.method == "POST":
        content_value = request.form.get("content_value", "").strip()
        db.execute(
            "UPDATE site_content SET content_value = ?, updated_at = ? WHERE content_key = ?",
            (content_value, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), content_key)
        )
        db.commit()
        flash("Content bijgewerkt.", "success")
        return redirect(url_for("admin_content"))
    
    content_row = db.execute("SELECT * FROM site_content WHERE content_key = ?", (content_key,)).fetchone()
    if not content_row:
        flash("Content niet gevonden.", "error")
        return redirect(url_for("admin_content"))
    
    return render_template("admin/edit_content.html", active_page="admin", content=content_row)


@app.route("/admin/video/upload", methods=["GET", "POST"])
@login_required
def admin_video_upload():
    if request.method == "POST":
        page_name = request.form.get("page_name", "").strip()
        video = request.files.get("video")
        
        if not page_name:
            flash("Pagina is verplicht.", "error")
            return render_template("admin/video_upload.html", active_page="admin"), 400
        
        if not video or not video.filename:
            flash("Video bestand is verplicht.", "error")
            return render_template("admin/video_upload.html", active_page="admin"), 400
        
        if not allowed_video_file(video.filename):
            flash("Ongeldig video formaat. Alleen mp4, webm, ogg zijn toegestaan.", "error")
            return render_template("admin/video_upload.html", active_page="admin"), 400
        
        ensure_upload_folder()
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        filename = f"{timestamp}_{video.filename}"
        video.save(os.path.join(UPLOAD_FOLDER, filename))
        video_url = f"/static/uploads/{filename}"
        
        db = get_db()
        existing = db.execute("SELECT id FROM page_videos WHERE page_name = ?", (page_name,)).fetchone()
        if existing:
            db.execute(
                "UPDATE page_videos SET video_url = ?, updated_at = ? WHERE page_name = ?",
                (video_url, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), page_name)
            )
        else:
            db.execute(
                "INSERT INTO page_videos (page_name, video_url, updated_at) VALUES (?, ?, ?)",
                (page_name, video_url, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
        db.commit()
        flash("Video geüpload.", "success")
        return redirect(url_for("admin_dashboard"))
    
    return render_template("admin/video_upload.html", active_page="admin")


@app.route("/admin/activity-contacts")
@login_required
def admin_activity_contacts():
    db = get_db()
    contacts = db.execute("SELECT * FROM activity_contacts ORDER BY activity_name").fetchall()
    return render_template("admin/activity_contacts.html", active_page="admin", contacts=contacts)


@app.route("/admin/activity-contacts/bewerken/<activity_name>", methods=["GET", "POST"])
@login_required
def admin_activity_contact_bewerken(activity_name):
    db = get_db()
    
    if request.method == "POST":
        address = request.form.get("address", "").strip()
        phone = request.form.get("phone", "").strip()
        email = request.form.get("email", "").strip()
        
        db.execute(
            "UPDATE activity_contacts SET address = ?, phone = ?, email = ?, updated_at = ? WHERE activity_name = ?",
            (address, phone, email, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), activity_name)
        )
        db.commit()
        flash("Contactgegevens bijgewerkt.", "success")
        return redirect(url_for("admin_activity_contacts"))
    
    contact_row = db.execute("SELECT * FROM activity_contacts WHERE activity_name = ?", (activity_name,)).fetchone()
    if not contact_row:
        flash("Contactgegevens niet gevonden.", "error")
        return redirect(url_for("admin_activity_contacts"))
    
    return render_template("admin/edit_activity_contact.html", active_page="admin", contact=contact_row)


@app.route("/admin/activity-photos")
@login_required
def admin_activity_photos():
    db = get_db()
    photos = db.execute("SELECT * FROM activity_photos ORDER BY activity_name, photo_order").fetchall()
    return render_template("admin/activity_photos.html", active_page="admin", photos=photos)


@app.route("/admin/activity-photos/upload", methods=["GET", "POST"])
@login_required
def admin_activity_photo_upload():
    if request.method == "POST":
        activity_name = request.form.get("activity_name", "").strip()
        photo = request.files.get("photo")
        
        if not activity_name:
            flash("Activiteit is verplicht.", "error")
            return render_template("admin/activity_photo_upload.html", active_page="admin"), 400
        
        if not photo or not photo.filename:
            flash("Foto bestand is verplicht.", "error")
            return render_template("admin/activity_photo_upload.html", active_page="admin"), 400
        
        if not allowed_file(photo.filename):
            flash("Ongeldig foto formaat. Alleen png, jpg, jpeg, gif, webp zijn toegestaan.", "error")
            return render_template("admin/activity_photo_upload.html", active_page="admin"), 400
        
        ensure_upload_folder()
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        filename = f"{timestamp}_{photo.filename}"
        photo.save(os.path.join(UPLOAD_FOLDER, filename))
        photo_url = f"/static/uploads/{filename}"
        
        # Get current max order for this activity
        db = get_db()
        max_order = db.execute(
            "SELECT MAX(photo_order) as max_order FROM activity_photos WHERE activity_name = ?",
            (activity_name,)
        ).fetchone()
        next_order = (max_order["max_order"] or 0) + 1
        
        db.execute(
            "INSERT INTO activity_photos (activity_name, photo_url, photo_order, updated_at) VALUES (?, ?, ?, ?)",
            (activity_name, photo_url, next_order, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        db.commit()
        flash("Foto geüpload.", "success")
        return redirect(url_for("admin_activity_photos"))
    
    return render_template("admin/activity_photo_upload.html", active_page="admin")


@app.route("/admin/activity-photos/verwijderen/<int:photo_id>", methods=["POST"])
@login_required
def admin_activity_photo_verwijderen(photo_id):
    db = get_db()
    photo_row = db.execute("SELECT * FROM activity_photos WHERE id = ?", (photo_id,)).fetchone()
    if photo_row:
        # Delete file from filesystem
        photo_url = photo_row["photo_url"]
        if photo_url.startswith("/static/uploads/"):
            filename = photo_url.replace("/static/uploads/", "")
            filepath = os.path.join(UPLOAD_FOLDER, filename)
            if os.path.exists(filepath):
                os.remove(filepath)
        
        db.execute("DELETE FROM activity_photos WHERE id = ?", (photo_id,))
        db.commit()
        flash("Foto verwijderd.", "success")
    else:
        flash("Foto niet gevonden.", "error")
    return redirect(url_for("admin_activity_photos"))


# ---------------------------------------------------------------------------
# Opstarten
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    init_db()
    app.run(debug=True)
