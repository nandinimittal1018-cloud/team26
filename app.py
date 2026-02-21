from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, join_room
from datetime import datetime
from translations import TRANSLATIONS, get_translation
import math

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secretkey'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///blood.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
socketio = SocketIO(app, async_mode='threading', manage_session=True)

# ---------------- MODELS ---------------- #
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    blood_group = db.Column(db.String(10))
    phone = db.Column(db.String(15), unique=True)
    role = db.Column(db.String(20))
    latitude = db.Column(db.Float, default=0.0)
    longitude = db.Column(db.Float, default=0.0)
    donation_count = db.Column(db.Integer, default=0)
    available = db.Column(db.Boolean, default=True)

class EmergencyRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    requester_id = db.Column(db.Integer)
    blood_group = db.Column(db.String(10))
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    urgency_level = db.Column(db.String(20))
    status = db.Column(db.String(20), default="active")
    responder_id = db.Column(db.Integer, nullable=True)
    responder_name = db.Column(db.String(100), nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class ChatMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer)
    receiver_id = db.Column(db.Integer)
    message = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

# ---------------- CONTEXT PROCESSOR (i18n) ---------------- #
@app.context_processor
def inject_translations():
    lang = session.get('lang', 'en')
    def t(key):
        return get_translation(key, lang)
    return dict(t=t, current_lang=lang)

# ---------------- UTILITY ---------------- #
def haversine(lat1, lon1, lat2, lon2):
    R = 6371  # Earth radius in KM
    lat1 = math.radians(lat1)
    lon1 = math.radians(lon1)
    lat2 = math.radians(lat2)
    lon2 = math.radians(lon2)

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    c = 2*math.atan2(math.sqrt(a), math.sqrt(1-a))

    return R * c

# ---------------- ROUTES ---------------- #
@app.route('/')
def home():
    total_users = User.query.count()
    total_donors = User.query.filter_by(role="donor").count()
    available_donors = User.query.filter_by(role="donor", available=True).count()
    total_requests = EmergencyRequest.query.count()
    user = None
    if 'user_id' in session:
        user = User.query.get(session['user_id'])

    return render_template(
        'index.html',
        total_users=total_users,
        total_donors=total_donors,
        available_donors=available_donors,
        total_requests=total_requests,
        user=user
    )

@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        name = request.form['name']
        blood_group = request.form['blood_group']
        phone = request.form['phone']
        role = request.form['role']
        lat_str = request.form.get('latitude', '').strip()
        lon_str = request.form.get('longitude', '').strip()
        lat = float(lat_str) if lat_str else 0.0
        lon = float(lon_str) if lon_str else 0.0

        if User.query.filter_by(phone=phone).first():
            return "Phone already registered!"

        user = User(name=name, blood_group=blood_group, phone=phone,
                    role=role, latitude=lat, longitude=lon)
        db.session.add(user)
        db.session.commit()
        session['user_id'] = user.id
        return redirect(url_for('dashboard'))
    return render_template('register.html')

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        phone = request.form['phone'].strip()
        user = User.query.filter_by(phone=phone).first()
        if user:
            session['user_id'] = user.id
            return redirect(url_for('dashboard'))
        return "User not found!"
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/set_language', methods=['POST'])
def set_language():
    lang = request.form.get('lang', 'en')
    if lang in TRANSLATIONS:
        session['lang'] = lang
    referrer = request.referrer or url_for('home')
    return redirect(referrer)

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user = User.query.get(session['user_id'])
    all_donors = User.query.filter_by(role="donor", available=True).all()
    nearby_donors = []

    for donor in all_donors:
        if donor.id == user.id or not donor.latitude or not donor.longitude:
            continue
        distance = haversine(user.latitude, user.longitude, donor.latitude, donor.longitude)
        if distance <= 10:
            nearby_donors.append((donor, round(distance,2)))

    total_donors = User.query.filter_by(role="donor").count()
    available_donors = User.query.filter_by(role="donor", available=True).count()
    total_requests = EmergencyRequest.query.count()

    # Get active emergency requests
    active_emergencies = EmergencyRequest.query.filter_by(status="active").order_by(EmergencyRequest.timestamp.desc()).all()
    # Attach requester names
    emergency_list = []
    for em in active_emergencies:
        requester = User.query.get(em.requester_id)
        emergency_list.append({
            'id': em.id,
            'blood_group': em.blood_group,
            'urgency_level': em.urgency_level,
            'requester_name': requester.name if requester else 'Unknown',
            'requester_id': em.requester_id,
            'timestamp': em.timestamp.strftime('%H:%M'),
        })

    return render_template('dashboard.html', user=user, donors=nearby_donors,
                           total_donors=total_donors, available_donors=available_donors,
                           total_requests=total_requests, emergencies=emergency_list)

@app.route('/emergency', methods=['POST'])
def emergency():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    blood_group = request.form['blood_group']
    urgency = request.form['urgency']

    req = EmergencyRequest(requester_id=user.id, blood_group=blood_group,
                           latitude=user.latitude, longitude=user.longitude,
                           urgency_level=urgency)
    db.session.add(req)
    db.session.commit()

    # Emit event via SocketIO
    socketio.emit('new_emergency', {
        'id': req.id,
        'blood_group': blood_group,
        'urgency': urgency,
        'requester': user.name,
        'requester_id': user.id,
        'timestamp': req.timestamp.strftime('%H:%M'),
    })

    return redirect(url_for('dashboard'))

@app.route('/emergency/<int:emergency_id>/respond', methods=['POST'])
def respond_emergency(emergency_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user = User.query.get(session['user_id'])
    em = EmergencyRequest.query.get(emergency_id)
    if not em or em.status != 'active':
        return jsonify({'error': 'Request not found or already responded'}), 404

    em.status = 'responded'
    em.responder_id = user.id
    em.responder_name = user.name
    db.session.commit()

    # Notify requester via socket
    socketio.emit('emergency_responded', {
        'emergency_id': emergency_id,
        'responder_name': user.name,
        'responder_blood': user.blood_group,
    }, room=str(em.requester_id))

    return jsonify({'status': 'success', 'responder': user.name})

@app.route('/emergencies')
def get_emergencies():
    """API endpoint to get active emergencies as JSON."""
    active = EmergencyRequest.query.filter_by(status="active").order_by(EmergencyRequest.timestamp.desc()).all()
    result = []
    for em in active:
        requester = User.query.get(em.requester_id)
        result.append({
            'id': em.id,
            'blood_group': em.blood_group,
            'urgency_level': em.urgency_level,
            'requester_name': requester.name if requester else 'Unknown',
            'requester_id': em.requester_id,
            'timestamp': em.timestamp.strftime('%H:%M'),
        })
    return jsonify(result)

@app.route('/chat/<int:receiver_id>')
def chat(receiver_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    receiver = User.query.get(receiver_id)
    return render_template('chat.html', receiver=receiver)

@app.route('/update_location', methods=['POST'])
def update_location():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json()
    user = User.query.get(session['user_id'])
    user.latitude = data.get('latitude', 0.0)
    user.longitude = data.get('longitude', 0.0)
    db.session.commit()
    return jsonify({'status': 'success'})

@app.route('/donate', methods=['POST'])
def donate():
    if 'user_id' not in session:
        return jsonify({'error':'Unauthorized'}), 401
    user = User.query.get(session['user_id'])
    user.donation_count += 1
    db.session.commit()
    return jsonify({'donation_count': user.donation_count})

# ---------------- SOCKETS ---------------- #
@socketio.on('connect')
def connect():
    if 'user_id' in session:
        join_room(str(session['user_id']))

@socketio.on('send_message')
def send_message(data):
    sender_id = session.get('user_id')
    receiver_id = data['receiver_id']
    message = data['message']

    msg = ChatMessage(sender_id=sender_id, receiver_id=receiver_id, message=message)
    db.session.add(msg)
    db.session.commit()

    socketio.emit('receive_message', {'sender_id': sender_id, 'message': message}, room=str(receiver_id))

# ---------------- RUN ---------------- #
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    socketio.run(app, debug=True, port=5001)