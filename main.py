cat > main.py << 'EOF'
from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from google.cloud import pubsub_v1
from datetime import datetime
import os
import json

app = Flask(__name__)

# Configure MySQL connection
DATABASE_USER = os.environ.get('DB_USER', 'user-asia')
DATABASE_PASSWORD = os.environ.get('DB_PASSWORD', 'Password@123')
DATABASE_HOST = os.environ.get('DB_HOST', 'localhost')
DATABASE_PORT = os.environ.get('DB_PORT', '3306')
DATABASE_NAME = os.environ.get('DB_NAME', 'asia-db')
PROJECT_ID = os.environ.get('GCP_PROJECT_ID', 'app-bigquery-478916')
PUBSUB_TOPIC = os.environ.get('PUBSUB_TOPIC', 'user-notifications')

app.config['SQLALCHEMY_DATABASE_URI'] = f'mysql+mysqlconnector://{DATABASE_USER}:{DATABASE_PASSWORD}@{DATABASE_HOST}:{DATABASE_PORT}/{DATABASE_NAME}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_recycle': 280,
    'pool_pre_ping': True,
}

db = SQLAlchemy(app)

# Models
class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class UnauthorizedAccessLog(db.Model):
    __tablename__ = 'unauthorized_access_logs'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    email = db.Column(db.String(120), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    reason = db.Column(db.String(255), nullable=False)
    ip_address = db.Column(db.String(45))

class LoginLog(db.Model):
    __tablename__ = 'login_logs'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    email = db.Column(db.String(120), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    ip_address = db.Column(db.String(45))

def get_user_email_from_iap():
    """Extract user email from IAP headers"""
    iap_email = request.headers.get('X-Goog-Authenticated-User-Email', '')
    
    if iap_email:
        parts = iap_email.split(':')
        if len(parts) > 1:
            return parts[1]
    
    # Fallback for testing without IAP
    return request.form.get('email') or request.headers.get('X-Test-Email', 'prvnrajh@gmail.com')

def get_client_ip():
    """Get client IP address"""
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0]
    return request.remote_addr

def publish_notification(user_email, activity_type):
    """Publish notification to Pub/Sub"""
    try:
        publisher = pubsub_v1.PublisherClient()
        topic_path = publisher.topic_path(PROJECT_ID, PUBSUB_TOPIC)
        
        message_data = {
            'user_email': user_email,
            'activity_type': activity_type,
            'timestamp': datetime.now().isoformat(),
            'message': f'{activity_type} by {user_email}'
        }
        
        message_json = json.dumps(message_data)
        future = publisher.publish(topic_path, message_json.encode('utf-8'))
        message_id = future.result(timeout=10)
        
        print(f"Published message ID: {message_id}")
        return True
    except Exception as e:
        print(f"Error publishing to Pub/Sub: {e}")
        return False

@app.route('/')
def index():
    """Main landing page"""
    return render_template('email_form.html')

@app.route('/login', methods=['POST'])
def login():
    """Handle login"""
    email = get_user_email_from_iap()
    client_ip = get_client_ip()
    
    # Check if user exists
    user = User.query.filter_by(email=email).first()
    
    if user:
        # Log successful login
        log_entry = LoginLog(email=email, ip_address=client_ip)
        db.session.add(log_entry)
        db.session.commit()
        
        # Publish to Pub/Sub
        publish_notification(email, 'Successful Login')
        
        # Get recent logs
        recent_logs = LoginLog.query.filter_by(email=email).order_by(LoginLog.timestamp.desc()).limit(10).all()
        
        return render_template('dashboard.html', user_email=email, logs=recent_logs)
    else:
        # Log unauthorized access
        log_entry = UnauthorizedAccessLog(
            email=email,
            ip_address=client_ip,
            reason='User not in authorized list'
        )
        db.session.add(log_entry)
        db.session.commit()
        
        # Publish to Pub/Sub
        publish_notification(email, 'Unauthorized Access Attempt')
        
        user_name = email.split('@')[0].replace('.', ' ').replace('_', ' ').title()
        return render_template('unauthorized.html', user_email=email, user_name=user_name)

@app.route('/api/logs')
def api_logs():
    """API endpoint to get user logs"""
    email = get_user_email_from_iap()
    
    user = User.query.filter_by(email=email).first()
    if not user:
        return jsonify({'error': 'Unauthorized'}), 403
    
    logs = LoginLog.query.filter_by(email=email).order_by(LoginLog.timestamp.desc()).limit(10).all()
    logs_data = [{'id': log.id, 'timestamp': log.timestamp.isoformat(), 'ip': log.ip_address} for log in logs]
    
    return jsonify({'logs': logs_data})

@app.route('/api/notify', methods=['POST'])
def api_notify():
    """API endpoint to send notification"""
    email = get_user_email_from_iap()
    
    user = User.query.filter_by(email=email).first()
    if not user:
        return jsonify({'error': 'Unauthorized'}), 403
    
    success = publish_notification(email, 'Manual Notification Request')
    
    if success:
        return jsonify({'message': 'Notification sent successfully'})
    else:
        return jsonify({'error': 'Failed to send notification'}), 500

@app.route('/health')
def health():
    """Health check endpoint"""
    try:
        db.session.execute(db.text('SELECT 1'))
        return jsonify({'status': 'healthy', 'database': 'connected'}), 200
    except Exception as e:
        return jsonify({'status': 'unhealthy', 'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)), debug=False)
EOF