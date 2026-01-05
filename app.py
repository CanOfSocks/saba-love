import os
from flask import Flask, render_template, request, redirect, url_for, make_response
from flask_sqlalchemy import SQLAlchemy
from flask_caching import Cache
from sqlalchemy import func
import geoip2.database
from dotenv import load_dotenv

app = Flask(__name__)

load_dotenv()
# --- Configuration ---
# Update with your actual DB credentials
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL') 
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Cache Configuration (Simple in-memory cache)
app.config['CACHE_TYPE'] = 'SimpleCache'
app.config['CACHE_DEFAULT_TIMEOUT'] = 10
cache = Cache(app)

db = SQLAlchemy(app)

# Path to local MaxMind DB
GEOIP_DB_PATH = 'ip-to-country.mmdb'

# --- Database Model ---
class Clicks(db.Model):
    country_code = db.Column(db.String(2), primary_key=True)
    country_name = db.Column(db.String(100), nullable=False)
    click_count = db.Column(db.BigInteger, default=0)

# --- Helpers ---
def get_ip():
    """Get IP considering Nginx Reverse Proxy"""
    if request.headers.getlist("X-Forwarded-For"):
        return request.headers.getlist("X-Forwarded-For")[0]
    return request.remote_addr

def get_country_from_ip(ip_address):
    """Returns (code, name). Defaults to ('XX', 'Atlantis') if unknown."""
    try:
        with geoip2.database.Reader(GEOIP_DB_PATH) as reader:
            response = reader.country(ip_address)
            return response.country.iso_code, response.country.name
    except Exception:
        return 'XX', 'Atlantis'

# --- Routes ---

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        # Handle the click
        is_anonymous = request.form.get('anonymous') == 'on'
        
        if is_anonymous:
            code, name = 'XX', 'Atlantis'
        else:
            ip = get_ip()
            code, name = get_country_from_ip(ip)
            if not code: # Fallback if GeoIP fails
                code, name = 'XX', 'Atlantis'

        # Atomic Increment: Handles high concurrency
        # Insert if not exists, otherwise update count = count + 1
        stmt = (
            "INSERT INTO clicks (country_code, country_name, click_count) "
            "VALUES (:code, :name, 1) "
            "ON DUPLICATE KEY UPDATE click_count = click_count + 1"
        )
        
        db.session.execute(db.text(stmt), {'code': code, 'name': name})
        db.session.commit()
        
        # Invalidate main page cache so user sees new count immediately
        cache.delete('view/index') 
        return redirect(url_for('index'))

    # GET Request: Cached
    return cached_index()

@app.route('/cached_index')
@cache.cached(timeout=10, key_prefix='view/index')
def cached_index():
    # Calculate total clicks
    total_clicks = db.session.query(func.sum(Clicks.click_count)).scalar() or 0
    return render_template('index.html', total_clicks=total_clicks)

@app.route('/map')
@cache.cached(timeout=10)
def world_map():
    all_data = Clicks.query.all()
    # Create a dict structure: { 'US': {'name': 'United States', 'count': 123}, ... }
    map_data = {
        c.country_code: {
            'name': c.country_name, 
            'count': c.click_count
        } for c in all_data
    }
    return render_template('map.html', map_data=map_data)

# --- Asset Caching ---
@app.after_request
def add_header(response):
    """Add header to cache static assets for 30 minutes"""
    if request.path.startswith('/static'):
        response.cache_control.max_age = 1800 # 30 minutes
        response.cache_control.public = True
    return response

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(host='0.0.0.0', port=os.getenv("FLASK_RUN_PORT", 5000))