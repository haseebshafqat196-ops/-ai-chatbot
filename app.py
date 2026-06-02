from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from database import db, User, ChatHistory, init_db
import os
import requests
from functools import wraps
from datetime import timedelta

# Initialize Flask app
app = Flask(__name__)

# Configuration
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///instance/chatbot.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your-secret-key-change-in-production')
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)

# Initialize database
init_db(app)

# Google Gemini API Configuration
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"

# Decorator to check if user is logged in
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# ==================== AUTHENTICATION ROUTES ====================

@app.route('/register', methods=['GET', 'POST'])
def register():
    """User registration"""
    if request.method == 'POST':
        data = request.get_json() if request.is_json else request.form
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        confirm_password = data.get('confirm_password', '').strip()
        
        # Validation
        if not username or not password:
            return jsonify({'error': 'Username and password are required'}), 400
        
        if len(username) < 3:
            return jsonify({'error': 'Username must be at least 3 characters'}), 400
        
        if len(password) < 6:
            return jsonify({'error': 'Password must be at least 6 characters'}), 400
        
        if password != confirm_password:
            return jsonify({'error': 'Passwords do not match'}), 400
        
        # Check if user exists
        if User.query.filter_by(username=username).first():
            return jsonify({'error': 'Username already exists'}), 400
        
        # Create new user
        try:
            user = User(username=username)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            
            return jsonify({'success': 'User registered successfully. Please login.'}), 201
        except Exception as e:
            db.session.rollback()
            return jsonify({'error': 'An error occurred during registration'}), 500
    
    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    """User login"""
    if request.method == 'POST':
        data = request.get_json() if request.is_json else request.form
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        
        if not username or not password:
            return jsonify({'error': 'Username and password are required'}), 400
        
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            session.permanent = True
            session['user_id'] = user.id
            session['username'] = user.username
            
            if request.is_json:
                return jsonify({'success': 'Logged in successfully'}), 200
            return redirect(url_for('chat'))
        
        error_msg = 'Invalid username or password'
        if request.is_json:
            return jsonify({'error': error_msg}), 401
        return render_template('login.html', error=error_msg)
    
    return render_template('login.html')


@app.route('/logout')
def logout():
    """User logout"""
    session.clear()
    return redirect(url_for('chat'))


# ==================== CHAT ROUTES ====================

@app.route('/')
def chat():
    """Main chat interface - accessible to all users"""
    username = session.get('username', None)
    is_authenticated = 'user_id' in session
    return render_template('index.html', username=username, is_authenticated=is_authenticated)


@app.route('/api/chat', methods=['POST'])
def get_ai_response():
    """Send message to Gemini AI and save to database if user is logged in"""
    data = request.get_json()
    user_message = data.get('message', '').strip()
    
    if not user_message:
        return jsonify({'error': 'Message cannot be empty'}), 400
    
    if not GEMINI_API_KEY:
        return jsonify({'error': 'API key not configured. Please set GEMINI_API_KEY environment variable.'}), 500
    
    try:
        # Call Google Gemini API
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": GEMINI_API_KEY
        }
        
        payload = {
            "contents": [
                {
                    "parts": [
                        {
                            "text": user_message
                        }
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.7,
                "maxOutputTokens": 500,
            }
        }
        
        response = requests.post(GEMINI_API_URL, headers=headers, json=payload, timeout=30)
        
        if response.status_code != 200:
            error_data = response.json()
            error_msg = error_data.get('error', {}).get('message', 'AI service temporarily unavailable')
            return jsonify({'error': f'AI Error: {error_msg}'}), response.status_code
        
        result = response.json()
        
        # Extract AI response from Gemini
        try:
            ai_message = result['candidates'][0]['content']['parts'][0]['text'].strip()
        except (KeyError, IndexError, TypeError):
            ai_message = 'Sorry, I could not generate a response.'
        
        # Save to database only if user is logged in
        if 'user_id' in session:
            try:
                chat_entry = ChatHistory(
                    user_id=session['user_id'],
                    user_message=user_message,
                    ai_response=ai_message
                )
                db.session.add(chat_entry)
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                print(f"Error saving chat: {str(e)}")
        
        return jsonify({
            'success': True,
            'ai_response': ai_message,
            'saved': 'user_id' in session
        }), 200
        
    except requests.exceptions.Timeout:
        return jsonify({'error': 'AI service timeout. Please try again.'}), 504
    except requests.exceptions.ConnectionError:
        return jsonify({'error': 'Connection error. Please check your internet.'}), 503
    except Exception as e:
        print(f"Error: {str(e)}")
        return jsonify({'error': f'Error: {str(e)}'}), 500


@app.route('/api/chat-history', methods=['GET'])
def get_chat_history():
    """Get user's chat history - only if logged in"""
    if 'user_id' not in session:
        return jsonify({'success': True, 'chats': []}), 200
    
    try:
        user_id = session['user_id']
        chats = ChatHistory.query.filter_by(user_id=user_id).order_by(ChatHistory.timestamp.asc()).all()
        
        return jsonify({
            'success': True,
            'chats': [chat.to_dict() for chat in chats]
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/clear-history', methods=['DELETE'])
def clear_history():
    """Clear user's chat history - only if logged in"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    try:
        user_id = session['user_id']
        ChatHistory.query.filter_by(user_id=user_id).delete()
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Chat history cleared'}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


# ==================== ERROR HANDLERS ====================

@app.errorhandler(404)
def not_found(error):
    """Handle 404 errors"""
    return jsonify({'error': 'Page not found'}), 404


@app.errorhandler(500)
def internal_error(error):
    """Handle 500 errors"""
    db.session.rollback()
    return jsonify({'error': 'Internal server error'}), 500


if __name__ == '__main__':
    # Create instance directory if it doesn't exist
    os.makedirs('instance', exist_ok=True)
    
    # Run Flask app
    app.run(debug=True, host='0.0.0.0', port=5000)
