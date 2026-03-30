#!/usr/bin/env python3
# app.py - Kelon Bank KX Premium Production Version - COMPLETE

import os
import re
import json
import random
import string
import hashlib
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from functools import wraps
from typing import Dict, Optional, Tuple

from flask import Flask, render_template_string, request, jsonify, session
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from sqlalchemy.exc import IntegrityError

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Configuration ---
app = Flask(__name__)
CORS(app, origins=os.environ.get('ALLOWED_ORIGINS', '*').split(','))

# Rate limiting
limiter = Limiter(
    app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"]
)

# Render-friendly configuration
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', os.urandom(24).hex())
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=24)
app.config['SESSION_COOKIE_SECURE'] = bool(os.environ.get('RENDER', False))
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# Database configuration
database_url = os.environ.get('DATABASE_URL')
if database_url:
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///kelon_bank.db'

db = SQLAlchemy(app)
bcrypt = Bcrypt(app)

# --- OTP Storage with cleanup ---
otp_storage: Dict[str, dict] = {}

def cleanup_otp_storage():
    """Remove expired OTPs periodically"""
    current_time = datetime.now()
    expired = [phone for phone, data in otp_storage.items() if data['expires'] < current_time]
    for phone in expired:
        del otp_storage[phone]

# --- Models ---
class User(db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    phone = db.Column(db.String(20), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(128), nullable=False)
    account_number = db.Column(db.String(10), unique=True, nullable=False, index=True)
    full_name = db.Column(db.String(100), default="KX User")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    device_fingerprint = db.Column(db.String(128))
    is_admin = db.Column(db.Boolean, default=False, index=True)
    is_active = db.Column(db.Boolean, default=True)
    last_login = db.Column(db.DateTime)
    
    # Security
    transaction_pin = db.Column(db.String(128))
    ghost_mode = db.Column(db.Boolean, default=False)
    ghost_balance = db.Column(db.Numeric(20, 2), default=250000.00)
    secret_code = db.Column(db.String(10))
    
    # Core Balances
    available_balance = db.Column(db.Numeric(20, 2), default=1250000.00)
    book_balance = db.Column(db.Numeric(20, 2), default=1250000.00)
    kelon_vault = db.Column(db.Numeric(20, 2), default=500000.00)
    betting_wallet = db.Column(db.Numeric(20, 2), default=25000.00)
    charity_pot = db.Column(db.Numeric(20, 2), default=12000.00)
    cashback_ledger = db.Column(db.Numeric(20, 2), default=3450.75)
    loan_eligibility = db.Column(db.Numeric(20, 2), default=500000.00)
    outstanding_debt = db.Column(db.Numeric(20, 2), default=0.00)
    daily_spend_limit = db.Column(db.Numeric(20, 2), default=500000.00)
    monthly_inflow = db.Column(db.Numeric(20, 2), default=2450000.00)
    monthly_outflow = db.Column(db.Numeric(20, 2), default=1850000.00)
    emergency_buffer = db.Column(db.Numeric(20, 2), default=300000.00)
    accrued_interest = db.Column(db.Numeric(20, 2), default=12450.85)
    usd_equiv = db.Column(db.Numeric(20, 2), default=785.50)
    gbp_equiv = db.Column(db.Numeric(20, 2), default=620.30)
    investment_value = db.Column(db.Numeric(20, 2), default=215000.00)
    pending_transfers = db.Column(db.Numeric(20, 2), default=0.00)
    utility_wallet = db.Column(db.Numeric(20, 2), default=15000.00)
    fixed_deposit_goal = db.Column(db.Numeric(20, 2), default=1000000.00)
    transaction_tax_pool = db.Column(db.Numeric(20, 2), default=875.25)
    
    # Airtime & Data
    airtime_balance = db.Column(db.Numeric(20, 2), default=5000.00)
    data_balance = db.Column(db.Numeric(20, 2), default=10000.00)
    
    # Relationships
    transactions = db.relationship('Transaction', backref='user', lazy=True, cascade='all, delete-orphan')
    receipts = db.relationship('Receipt', backref='user', lazy=True, cascade='all, delete-orphan')
    
    def to_dict(self, include_sensitive=False):
        """Convert user to dictionary for API responses"""
        balance_to_show = self.ghost_balance if self.ghost_mode else self.available_balance
        
        data = {
            'id': self.id,
            'phone': self.phone,
            'account_number': self.account_number,
            'full_name': self.full_name,
            'available_balance': float(balance_to_show),
            'book_balance': float(self.book_balance),
            'kelon_vault': float(self.kelon_vault),
            'betting_wallet': float(self.betting_wallet),
            'charity_pot': float(self.charity_pot),
            'cashback_ledger': float(self.cashback_ledger),
            'loan_eligibility': float(self.loan_eligibility),
            'outstanding_debt': float(self.outstanding_debt),
            'daily_spend_limit': float(self.daily_spend_limit),
            'monthly_inflow': float(self.monthly_inflow),
            'monthly_outflow': float(self.monthly_outflow),
            'emergency_buffer': float(self.emergency_buffer),
            'accrued_interest': float(self.accrued_interest),
            'usd_equiv': float(self.usd_equiv),
            'gbp_equiv': float(self.gbp_equiv),
            'investment_value': float(self.investment_value),
            'pending_transfers': float(self.pending_transfers),
            'utility_wallet': float(self.utility_wallet),
            'fixed_deposit_goal': float(self.fixed_deposit_goal),
            'transaction_tax_pool': float(self.transaction_tax_pool),
            'airtime_balance': float(self.airtime_balance),
            'data_balance': float(self.data_balance),
            'ghost_mode': self.ghost_mode,
            'has_pin': self.transaction_pin is not None,
            'is_admin': self.is_admin
        }
        
        if include_sensitive:
            data['secret_code'] = self.secret_code
            
        return data

class Transaction(db.Model):
    __tablename__ = 'transactions'
    
    id = db.Column(db.Integer, primary_key=True)
    transaction_id = db.Column(db.String(50), unique=True, nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    type = db.Column(db.String(50), index=True)
    amount = db.Column(db.Numeric(20, 2), nullable=False)
    description = db.Column(db.Text)
    status = db.Column(db.String(20), default='completed', index=True)
    recipient = db.Column(db.String(100))
    sender_name = db.Column(db.String(100))
    recipient_phone = db.Column(db.String(20))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    
    def to_dict(self):
        return {
            'id': self.transaction_id,
            'type': self.type,
            'amount': float(self.amount),
            'description': self.description,
            'date': self.created_at.isoformat(),
            'status': self.status
        }

class Receipt(db.Model):
    __tablename__ = 'receipts'
    
    id = db.Column(db.Integer, primary_key=True)
    receipt_id = db.Column(db.String(50), unique=True, nullable=False, index=True)
    transaction_id = db.Column(db.String(50), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    receipt_data = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

class LoginAttempt(db.Model):
    __tablename__ = 'login_attempts'
    
    id = db.Column(db.Integer, primary_key=True)
    ip_address = db.Column(db.String(45), nullable=False, index=True)
    account_number = db.Column(db.String(10), index=True)
    success = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

# --- Helper Functions ---
def generate_account_number() -> str:
    """Generate unique 10-digit account number starting with 30"""
    while True:
        account = '30' + ''.join(random.choices(string.digits, k=8))
        if not User.query.filter_by(account_number=account).first():
            return account

def generate_secret_code() -> str:
    """Generate 6-digit secret code"""
    return ''.join(random.choices(string.digits, k=6))

def generate_transaction_id() -> str:
    """Generate unique transaction ID"""
    timestamp = datetime.utcnow().strftime('%Y%m%d%H%M%S')
    random_part = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"KELON-{timestamp}-{random_part}"

def generate_receipt_id() -> str:
    """Generate unique receipt ID"""
    timestamp = datetime.utcnow().strftime('%Y%m%d%H%M%S')
    random_part = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
    return f"RCP-{timestamp}-{random_part}"

def generate_otp() -> str:
    """Generate 6-digit OTP"""
    return ''.join(random.choices(string.digits, k=6))

def validate_phone(phone: str) -> bool:
    """Validate Nigerian phone number"""
    return bool(re.match(r'^0[789][01]\d{8}$', phone))

def validate_pin(pin: str) -> bool:
    """Validate 4-digit PIN"""
    return bool(re.match(r'^\d{4}$', pin))

def format_currency(amount: Decimal) -> str:
    """Format amount as Nigerian Naira"""
    return f"₦{amount:,.2f}"

def generate_device_fingerprint() -> str:
    """Generate device fingerprint from request headers"""
    user_agent = request.headers.get('User-Agent', '')
    accept_language = request.headers.get('Accept-Language', '')
    ip = request.remote_addr
    fingerprint_string = f"{user_agent}|{accept_language}|{ip}"
    return hashlib.sha256(fingerprint_string.encode()).hexdigest()

def check_login_attempts(ip_address: str, account_number: str = None) -> Tuple[bool, str]:
    """Check if login attempts exceed limits"""
    timeframe = datetime.now() - timedelta(minutes=15)
    
    # Check IP-based attempts
    ip_attempts = LoginAttempt.query.filter(
        LoginAttempt.ip_address == ip_address,
        LoginAttempt.created_at > timeframe,
        LoginAttempt.success == False
    ).count()
    
    if ip_attempts >= 10:
        return False, "Too many failed attempts from this IP. Try again later."
    
    # Check account-based attempts
    if account_number:
        account_attempts = LoginAttempt.query.filter(
            LoginAttempt.account_number == account_number,
            LoginAttempt.created_at > timeframe,
            LoginAttempt.success == False
        ).count()
        
        if account_attempts >= 5:
            return False, "Too many failed attempts for this account. Try again later."
    
    return True, ""

def record_login_attempt(ip_address: str, account_number: str, success: bool):
    """Record login attempt for security monitoring"""
    attempt = LoginAttempt(
        ip_address=ip_address,
        account_number=account_number,
        success=success
    )
    db.session.add(attempt)
    db.session.commit()

def create_receipt(transaction: Transaction, user: User, additional_data: dict = None) -> dict:
    """Create and store receipt for transaction"""
    receipt_data = {
        'receipt_id': generate_receipt_id(),
        'transaction_id': transaction.transaction_id,
        'transaction_type': transaction.type,
        'amount': float(transaction.amount),
        'formatted_amount': format_currency(transaction.amount),
        'date': transaction.created_at.strftime('%Y-%m-%d %H:%M:%S'),
        'description': transaction.description,
        'status': transaction.status,
        'user_details': {
            'name': user.full_name,
            'account_number': user.account_number,
            'phone': user.phone
        },
        'balance_after': float(user.available_balance)
    }
    
    if transaction.recipient:
        receipt_data['recipient'] = transaction.recipient
    
    if transaction.recipient_phone:
        receipt_data['recipient_phone'] = transaction.recipient_phone
    
    if additional_data:
        receipt_data.update(additional_data)
    
    receipt = Receipt(
        receipt_id=receipt_data['receipt_id'],
        transaction_id=transaction.transaction_id,
        user_id=user.id,
        receipt_data=json.dumps(receipt_data)
    )
    db.session.add(receipt)
    db.session.commit()
    
    return receipt_data

# --- Authentication Decorators ---
def login_required(f):
    """Decorator to require user login"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Authentication required'}), 401
        
        user = User.query.get(session['user_id'])
        if not user or not user.is_active:
            session.pop('user_id', None)
            return jsonify({'error': 'User not found or inactive'}), 401
        
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    """Decorator to require admin privileges"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Authentication required'}), 401
        
        user = User.query.get(session['user_id'])
        if not user or not user.is_admin:
            return jsonify({'error': 'Admin access required'}), 403
        return f(*args, **kwargs)
    return decorated_function

# --- API Routes ---
@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/health')
def health_check():
    return jsonify({'status': 'healthy', 'message': 'Server is running'})

@app.route('/api/send-otp', methods=['POST'])
@limiter.limit("5 per minute")
def send_otp():
    """Send OTP for registration"""
    data = request.json
    phone = data.get('phone')
    
    if not phone or not validate_phone(phone):
        return jsonify({'error': 'Valid phone number required (11 digits starting with 070,080,081,090,091)'}), 400
    
    if User.query.filter_by(phone=phone).first():
        return jsonify({'error': 'Phone number already registered'}), 400
    
    device_fingerprint = generate_device_fingerprint()
    otp = generate_otp()
    
    otp_storage[phone] = {
        'otp': otp,
        'expires': datetime.now() + timedelta(minutes=5),
        'device_fingerprint': device_fingerprint
    }
    
    cleanup_otp_storage()
    
    logger.info(f"OTP generated for {phone}")
    
    return jsonify({
        'success': True,
        'message': 'OTP sent successfully',
        'otp': otp,
        'expires_in': 300
    })

@app.route('/api/verify-otp', methods=['POST'])
@limiter.limit("10 per hour")
def verify_otp_route():
    """Verify OTP and complete registration"""
    data = request.json
    phone = data.get('phone')
    otp = data.get('otp')
    password = data.get('password')
    full_name = data.get('full_name', 'KX User')
    
    if not all([phone, otp, password]):
        return jsonify({'error': 'Phone, OTP and password required'}), 400
    
    if len(password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters'}), 400
    
    device_fingerprint = generate_device_fingerprint()
    
    if phone not in otp_storage:
        return jsonify({'error': 'OTP expired or not requested'}), 401
    
    stored = otp_storage[phone]
    if stored['otp'] != otp or datetime.now() > stored['expires'] or stored['device_fingerprint'] != device_fingerprint:
        return jsonify({'error': 'Invalid or expired OTP'}), 401
    
    account_number = generate_account_number()
    secret_code = generate_secret_code()
    password_hash = bcrypt.generate_password_hash(password).decode('utf-8')
    
    user = User(
        phone=phone,
        password_hash=password_hash,
        account_number=account_number,
        full_name=full_name,
        secret_code=secret_code,
        device_fingerprint=device_fingerprint
    )
    
    try:
        db.session.add(user)
        db.session.commit()
        del otp_storage[phone]
        
        logger.info(f"New user registered: {account_number}")
        
        return jsonify({
            'success': True,
            'account_number': account_number,
            'secret_code': secret_code,
            'message': f'Registration successful! Your secret code is: {secret_code}'
        })
    except IntegrityError:
        db.session.rollback()
        return jsonify({'error': 'Registration failed. Please try again.'}), 500

@app.route('/api/login', methods=['POST'])
@limiter.limit("10 per minute")
def login():
    """Authenticate user"""
    data = request.json
    account_number = data.get('account_number')
    password = data.get('password')
    ip_address = request.remote_addr
    
    if not account_number or not password:
        return jsonify({'error': 'Account number and password required'}), 400
    
    # Check login attempts
    allowed, message = check_login_attempts(ip_address, account_number)
    if not allowed:
        return jsonify({'error': message}), 429
    
    user = User.query.filter_by(account_number=account_number).first()
    
    if not user or not bcrypt.check_password_hash(user.password_hash, password):
        record_login_attempt(ip_address, account_number, False)
        return jsonify({'error': 'Invalid credentials'}), 401
    
    if not user.is_active:
        return jsonify({'error': 'Account is deactivated'}), 401
    
    record_login_attempt(ip_address, account_number, True)
    
    session.permanent = True
    session['user_id'] = user.id
    
    user.last_login = datetime.utcnow()
    db.session.commit()
    
    logger.info(f"User logged in: {account_number}")
    
    return jsonify({
        'success': True,
        'user': user.to_dict()
    })

@app.route('/api/logout', methods=['POST'])
def logout():
    session.pop('user_id', None)
    return jsonify({'success': True})

@app.route('/api/me', methods=['GET'])
@login_required
def get_me():
    user = User.query.get(session['user_id'])
    return jsonify({'user': user.to_dict()})

@app.route('/api/set-pin', methods=['POST'])
@login_required
def set_pin():
    data = request.json
    pin = data.get('pin')
    
    if not validate_pin(pin):
        return jsonify({'error': 'PIN must be exactly 4 digits'}), 400
    
    user = User.query.get(session['user_id'])
    user.transaction_pin = bcrypt.generate_password_hash(pin).decode('utf-8')
    db.session.commit()
    
    return jsonify({'success': True, 'message': 'PIN set successfully'})

@app.route('/api/toggle-ghost-mode', methods=['POST'])
@login_required
def toggle_ghost_mode():
    user = User.query.get(session['user_id'])
    user.ghost_mode = not user.ghost_mode
    db.session.commit()
    
    return jsonify({'success': True, 'ghost_mode': user.ghost_mode})

@app.route('/api/secret-add-money', methods=['POST'])
@login_required
def secret_add_money():
    data = request.json
    secret_code = data.get('secret_code')
    amount = Decimal(str(data.get('amount')))
    
    user = User.query.get(session['user_id'])
    
    if not user.secret_code or user.secret_code != secret_code:
        return jsonify({'error': 'Invalid secret code!'}), 401
    
    if amount <= 0 or amount > 10000000:
        return jsonify({'error': 'Invalid amount. Maximum is ₦10,000,000'}), 400
    
    user.available_balance += amount
    user.book_balance += amount
    user.monthly_inflow += amount
    user.kelon_vault += amount * Decimal('0.1')
    user.investment_value += amount * Decimal('0.05')
    
    transaction = Transaction(
        transaction_id=generate_transaction_id(),
        user_id=user.id,
        type='secret_deposit',
        amount=amount,
        description=f"Secret Deposit of {format_currency(amount)}"
    )
    
    db.session.add(transaction)
    db.session.commit()
    
    receipt = create_receipt(transaction, user, {'secret_deposit': True})
    
    return jsonify({
        'success': True,
        'amount': float(amount),
        'new_balance': float(user.available_balance),
        'receipt': receipt
    })

@app.route('/api/buy-airtime', methods=['POST'])
@login_required
def buy_airtime():
    data = request.json
    pin = data.get('pin')
    amount = Decimal(str(data.get('amount')))
    phone_number = data.get('phone_number')
    network = data.get('network', 'MTN')
    
    user = User.query.get(session['user_id'])
    
    if not user.transaction_pin or not bcrypt.check_password_hash(user.transaction_pin, pin):
        return jsonify({'error': 'Invalid PIN'}), 401
    
    if amount <= 0 or amount > user.available_balance:
        return jsonify({'error': 'Invalid amount or insufficient funds'}), 400
    
    if not validate_phone(phone_number):
        return jsonify({'error': 'Valid phone number required'}), 400
    
    user.available_balance -= amount
    user.book_balance -= amount
    user.monthly_outflow += amount
    
    if phone_number == user.phone:
        user.airtime_balance += amount
    
    transaction = Transaction(
        transaction_id=generate_transaction_id(),
        user_id=user.id,
        type='airtime',
        amount=amount,
        description=f"Airtime purchase - {network} to {phone_number}",
        recipient=network,
        recipient_phone=phone_number
    )
    
    db.session.add(transaction)
    db.session.commit()
    
    receipt = create_receipt(transaction, user, {
        'network': network,
        'phone_number': phone_number
    })
    
    return jsonify({
        'success': True,
        'transaction_id': transaction.transaction_id,
        'receipt': receipt
    })

@app.route('/api/buy-data', methods=['POST'])
@login_required
def buy_data():
    data = request.json
    pin = data.get('pin')
    amount = Decimal(str(data.get('amount')))
    phone_number = data.get('phone_number')
    network = data.get('network', 'MTN')
    data_plan = data.get('data_plan', '1GB')
    
    user = User.query.get(session['user_id'])
    
    if not user.transaction_pin or not bcrypt.check_password_hash(user.transaction_pin, pin):
        return jsonify({'error': 'Invalid PIN'}), 401
    
    if amount <= 0 or amount > user.available_balance:
        return jsonify({'error': 'Invalid amount or insufficient funds'}), 400
    
    if not validate_phone(phone_number):
        return jsonify({'error': 'Valid phone number required'}), 400
    
    user.available_balance -= amount
    user.book_balance -= amount
    user.monthly_outflow += amount
    
    if phone_number == user.phone:
        user.data_balance += amount
    
    transaction = Transaction(
        transaction_id=generate_transaction_id(),
        user_id=user.id,
        type='data',
        amount=amount,
        description=f"Data purchase - {data_plan} ({network}) for {phone_number}",
        recipient=network,
        recipient_phone=phone_number
    )
    
    db.session.add(transaction)
    db.session.commit()
    
    receipt = create_receipt(transaction, user, {
        'network': network,
        'phone_number': phone_number,
        'data_plan': data_plan
    })
    
    return jsonify({
        'success': True,
        'transaction_id': transaction.transaction_id,
        'receipt': receipt
    })

@app.route('/api/user-transfer', methods=['POST'])
@login_required
def user_transfer():
    data = request.json
    pin = data.get('pin')
    recipient_account = data.get('recipient_account')
    amount = Decimal(str(data.get('amount')))
    narrative = data.get('narrative', '')
    
    sender = User.query.get(session['user_id'])
    
    if not sender.transaction_pin or not bcrypt.check_password_hash(sender.transaction_pin, pin):
        return jsonify({'error': 'Invalid PIN'}), 401
    
    if amount <= 0 or amount > sender.available_balance:
        return jsonify({'error': 'Insufficient funds'}), 400
    
    recipient = User.query.filter_by(account_number=recipient_account).first()
    if not recipient:
        return jsonify({'error': 'Recipient account not found'}), 404
    
    if recipient.id == sender.id:
        return jsonify({'error': 'Cannot transfer to yourself'}), 400
    
    # Perform transfer
    sender.available_balance -= amount
    sender.book_balance -= amount
    sender.monthly_outflow += amount
    sender.pending_transfers += amount
    
    recipient.available_balance += amount
    recipient.book_balance += amount
    recipient.monthly_inflow += amount
    
    sender_txn = Transaction(
        transaction_id=generate_transaction_id(),
        user_id=sender.id,
        type='transfer_out',
        amount=amount,
        description=f"Sent to {recipient.full_name} ({recipient.account_number}) - {narrative}",
        recipient=recipient.account_number,
        sender_name=sender.full_name
    )
    
    recipient_txn = Transaction(
        transaction_id=generate_transaction_id(),
        user_id=recipient.id,
        type='transfer_in',
        amount=amount,
        description=f"Received from {sender.full_name} ({sender.account_number}) - {narrative}",
        recipient=sender.account_number,
        sender_name=sender.full_name
    )
    
    db.session.add(sender_txn)
    db.session.add(recipient_txn)
    db.session.commit()
    
    receipt = create_receipt(sender_txn, sender, {
        'recipient_name': recipient.full_name,
        'recipient_account': recipient.account_number,
        'narrative': narrative
    })
    
    return jsonify({
        'success': True,
        'transaction_id': sender_txn.transaction_id,
        'new_balance': float(sender.available_balance),
        'recipient_name': recipient.full_name,
        'receipt': receipt
    })

@app.route('/api/bills', methods=['POST'])
@login_required
def pay_bills():
    data = request.json
    pin = data.get('pin')
    amount = Decimal(str(data.get('amount')))
    bill_type = data.get('bill_type', 'unknown')
    reference = data.get('reference', '')
    
    user = User.query.get(session['user_id'])
    
    if not user.transaction_pin or not bcrypt.check_password_hash(user.transaction_pin, pin):
        return jsonify({'error': 'Invalid PIN'}), 401
    
    if amount <= 0 or amount > user.available_balance:
        return jsonify({'error': 'Invalid amount or insufficient funds'}), 400
    
    user.available_balance -= amount
    user.book_balance -= amount
    user.monthly_outflow += amount
    
    transaction = Transaction(
        transaction_id=generate_transaction_id(),
        user_id=user.id,
        type='bills',
        amount=amount,
        description=f"{bill_type} Bill Payment - Ref: {reference}"
    )
    
    db.session.add(transaction)
    db.session.commit()
    
    receipt = create_receipt(transaction, user, {
        'bill_type': bill_type,
        'reference': reference
    })
    
    return jsonify({
        'success': True,
        'transaction_id': transaction.transaction_id,
        'receipt': receipt
    })

@app.route('/api/get-receipt/<receipt_id>', methods=['GET'])
@login_required
def get_receipt(receipt_id):
    receipt = Receipt.query.filter_by(receipt_id=receipt_id, user_id=session['user_id']).first()
    if not receipt:
        return jsonify({'error': 'Receipt not found'}), 404
    
    return jsonify({
        'success': True,
        'receipt': json.loads(receipt.receipt_data)
    })

@app.route('/api/my-receipts', methods=['GET'])
@login_required
def get_my_receipts():
    receipts = Receipt.query.filter_by(user_id=session['user_id']).order_by(Receipt.created_at.desc()).limit(50).all()
    
    return jsonify({
        'success': True,
        'receipts': [json.loads(r.receipt_data) for r in receipts]
    })

@app.route('/api/get-user-by-account', methods=['POST'])
@login_required
def get_user_by_account():
    data = request.json
    account_number = data.get('account_number')
    
    user = User.query.filter_by(account_number=account_number).first()
    if not user:
        return jsonify({'error': 'Account not found'}), 404
    
    return jsonify({
        'success': True,
        'full_name': user.full_name,
        'account_number': user.account_number
    })

@app.route('/api/transactions', methods=['GET'])
@login_required
def get_transactions():
    user = User.query.get(session['user_id'])
    transactions = user.transactions[-50:]
    
    return jsonify({
        'transactions': [t.to_dict() for t in reversed(transactions)]
    })

# --- Admin Routes ---
@app.route('/api/admin/search-user', methods=['POST'])
@admin_required
def admin_search_user():
    data = request.json
    search_term = data.get('search_term', '')
    
    user = User.query.filter(
        (User.account_number == search_term) | (User.phone == search_term)
    ).first()
    
    if not user:
        return jsonify({'error': 'User not found'}), 404
    
    return jsonify({
        'success': True,
        'user': {
            'id': user.id,
            'full_name': user.full_name,
            'account_number': user.account_number,
            'phone': user.phone,
            'available_balance': float(user.available_balance),
            'is_admin': user.is_admin,
            'is_active': user.is_active
        }
    })

@app.route('/api/admin/add-money', methods=['POST'])
@admin_required
def admin_add_money():
    data = request.json
    admin_pin = data.get('admin_pin')
    account_number = data.get('account_number')
    amount = Decimal(str(data.get('amount')))
    description = data.get('description', 'Admin credit')
    
    ADMIN_PIN = os.environ.get('ADMIN_PIN', '123456')
    if admin_pin != ADMIN_PIN:
        return jsonify({'error': 'Invalid admin PIN'}), 401
    
    if amount <= 0 or amount > 100000000:
        return jsonify({'error': 'Invalid amount. Maximum is ₦100,000,000'}), 400
    
    user = User.query.filter_by(account_number=account_number).first()
    if not user:
        return jsonify({'error': 'User not found'}), 404
    
    user.available_balance += amount
    user.book_balance += amount
    user.monthly_inflow += amount
    user.kelon_vault += amount * Decimal('0.1')
    user.investment_value += amount * Decimal('0.05')
    
    transaction = Transaction(
        transaction_id=generate_transaction_id(),
        user_id=user.id,
        type='admin_credit',
        amount=amount,
        description=f"Admin Credit: {description}"
    )
    
    db.session.add(transaction)
    db.session.commit()
    
    receipt = create_receipt(transaction, user, {'admin_credit': True})
    
    logger.info(f"Admin added ₦{amount} to account {account_number}")
    
    return jsonify({
        'success': True,
        'amount': float(amount),
        'new_balance': float(user.available_balance),
        'user_name': user.full_name,
        'account_number': user.account_number,
        'receipt': receipt
    })

@app.route('/api/admin/all-users', methods=['GET'])
@admin_required
def admin_all_users():
    users = User.query.order_by(User.created_at.desc()).all()
    
    return jsonify({
        'success': True,
        'users': [{
            'id': u.id,
            'full_name': u.full_name,
            'account_number': u.account_number,
            'phone': u.phone,
            'available_balance': float(u.available_balance),
            'created_at': u.created_at.isoformat(),
            'is_admin': u.is_admin,
            'is_active': u.is_active,
            'last_login': u.last_login.isoformat() if u.last_login else None
        } for u in users]
    })

@app.route('/api/admin/toggle-user', methods=['POST'])
@admin_required
def admin_toggle_user():
    data = request.json
    admin_pin = data.get('admin_pin')
    account_number = data.get('account_number')
    
    ADMIN_PIN = os.environ.get('ADMIN_PIN', '123456')
    if admin_pin != ADMIN_PIN:
        return jsonify({'error': 'Invalid admin PIN'}), 401
    
    user = User.query.filter_by(account_number=account_number).first()
    if not user:
        return jsonify({'error': 'User not found'}), 404
    
    if user.is_admin:
        return jsonify({'error': 'Cannot deactivate admin users'}), 400
    
    user.is_active = not user.is_active
    db.session.commit()
    
    status = "activated" if user.is_active else "deactivated"
    logger.info(f"Admin {status} account {account_number}")
    
    return jsonify({
        'success': True,
        'message': f"User {status} successfully",
        'is_active': user.is_active
    })

# --- Database Initialization ---
def init_database():
    """Initialize database with default users"""
    with app.app_context():
        try:
            db.create_all()
            logger.info("Database tables created successfully")
        except Exception as e:
            logger.error(f"Database creation error: {e}")
        
        # Create admin user if not exists
        try:
            admin_account = '3099999999'
            if not User.query.filter_by(account_number=admin_account).first():
                admin_user = User(
                    phone='08012340000',
                    password_hash=bcrypt.generate_password_hash(os.environ.get('ADMIN_PASSWORD', 'admin123')).decode('utf-8'),
                    account_number=admin_account,
                    full_name='System Administrator',
                    secret_code='999999',
                    is_admin=True,
                    available_balance=10000000.00,
                    book_balance=10000000.00
                )
                db.session.add(admin_user)
                db.session.commit()
                logger.info(f"Admin user created! Account: {admin_account}")
        except Exception as e:
            logger.error(f"Admin creation error: {e}")
        
        # Create demo user only in development
        if not os.environ.get('RENDER'):
            try:
                if not User.query.filter_by(account_number='3012345678').first():
                    demo_user = User(
                        phone='08012345678',
                        password_hash=bcrypt.generate_password_hash('demo123').decode('utf-8'),
                        account_number='3012345678',
                        full_name='Demo User',
                        secret_code='123456',
                        is_admin=False
                    )
                    db.session.add(demo_user)
                    db.session.commit()
                    logger.info("Demo user created! Account: 3012345678, Password: demo123")
            except Exception as e:
                logger.error(f"Demo user creation error: {e}")
        
        logger.info("Database initialization complete!")

# Initialize database
init_database()

# --- HTML Template - FULL COMPLETE VERSION ---
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
    <title>KX PREMIUM | Kelon Bank • Elite Digital Banking</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0-beta3/css/all.min.css">
    <link href="https://fonts.googleapis.com/css2?family=Inter:opsz,wght@14..32,300;14..32,500;14..32,700;14..32,800&display=swap" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { background: radial-gradient(circle at 10% 20%, #0A0A0A, #020202); font-family: 'Inter', sans-serif; color: #FFFFFF; min-height: 100vh; }
        .container { max-width: 520px; margin: 0 auto; padding: 20px 18px 90px 18px; }
        .glass-card { background: rgba(20, 20, 28, 0.75); backdrop-filter: blur(16px); border-radius: 32px; padding: 24px; margin-bottom: 20px; border: 1px solid rgba(201, 168, 76, 0.3); }
        .gold-text, .gold { color: #D4AF37; }
        .balance { font-size: 52px; font-weight: 800; background: linear-gradient(135deg, #F9E0A0, #D4AF37); -webkit-background-clip: text; background-clip: text; color: transparent; text-align: center; margin: 16px 0; }
        .button-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin: 20px 0; }
        .action-btn { background: rgba(30, 30, 38, 0.8); border: 1px solid rgba(212, 175, 55, 0.4); padding: 14px 6px; border-radius: 24px; text-align: center; cursor: pointer; transition: all 0.25s; font-weight: 600; font-size: 13px; }
        .action-btn i { display: block; font-size: 24px; margin-bottom: 6px; color: #D4AF37; }
        .action-btn:hover { background: rgba(212, 175, 55, 0.2); transform: translateY(-3px); }
        input, select { width: 100%; padding: 14px 16px; margin: 10px 0; background: rgba(15, 15, 20, 0.9); border: 1px solid rgba(212, 175, 55, 0.4); border-radius: 20px; color: white; font-size: 15px; }
        input:focus { outline: none; border-color: #D4AF37; box-shadow: 0 0 0 3px rgba(212,175,55,0.2); }
        button { background: linear-gradient(105deg, #D4AF37, #B8922A); color: #0A0A0A; padding: 14px; border: none; border-radius: 28px; font-weight: 700; cursor: pointer; width: 100%; font-size: 16px; margin-top: 12px; transition: all 0.2s; }
        button:hover { transform: scale(0.98); }
        .modal { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0, 0, 0, 0.96); backdrop-filter: blur(20px); z-index: 1100; padding: 20px; overflow-y: auto; }
        .modal-content { background: #121218; border-radius: 40px; padding: 28px 24px; max-width: 500px; margin: 30px auto; border: 1px solid rgba(212, 175, 55, 0.5); }
        .toast { position: fixed; bottom: 30px; left: 50%; transform: translateX(-50%); background: #1E1A0F; backdrop-filter: blur(16px); color: #D4AF37; padding: 12px 28px; border-radius: 60px; z-index: 2100; font-weight: 600; border: 1px solid #D4AF37; animation: slideUp 0.3s ease; }
        @keyframes slideUp { from { opacity: 0; transform: translateX(-50%) translateY(30px);} to { opacity: 1; transform: translateX(-50%) translateY(0);} }
        .nav-bar { position: fixed; bottom: 0; left: 0; right: 0; max-width: 520px; margin: 0 auto; background: rgba(8, 8, 12, 0.92); backdrop-filter: blur(20px); display: flex; justify-content: space-around; padding: 12px 20px 26px; border-top: 1px solid rgba(212, 175, 55, 0.5); z-index: 100; }
        .nav-item { text-align: center; cursor: pointer; padding: 8px 18px; border-radius: 40px; font-weight: 600; }
        .nav-item i { display: block; font-size: 20px; margin-bottom: 5px; color: #D4AF37; }
        .receipt-card { background: rgba(0,0,0,0.5); border-radius: 20px; padding: 16px; margin: 12px 0; border-left: 3px solid #D4AF37; }
        .otp-timer { color: #D4AF37; font-size: 12px; text-align: center; margin-top: 8px; }
        .resend-btn { background: transparent; border: 1px solid #D4AF37; color: #D4AF37; margin-top: 5px; }
        .resend-btn:hover { background: rgba(212,175,55,0.2); transform: none; }
        .klm-logo { width: 40px; height: 40px; border-radius: 50%; background: #D4AF37; display: flex; align-items: center; justify-content: center; margin-right: 12px; }
        .klm-logo i { font-size: 24px; color: #0A0A0A; }
        .user-profile { display: flex; align-items: center; margin-bottom: 20px; }
        .admin-badge { background: #D4AF37; color: #0A0A0A; padding: 4px 12px; border-radius: 20px; font-size: 11px; font-weight: bold; margin-left: 10px; }
        .search-result { background: rgba(212,175,55,0.1); padding: 15px; border-radius: 16px; margin: 10px 0; border: 1px solid rgba(212,175,55,0.3); }
        .close-btn { background: rgba(255,255,255,0.1); color: white; margin-top: 10px; }
    </style>
</head>
<body>

<!-- AUTH SCREEN -->
<div id="authScreen" class="container" style="display: flex; align-items: center; min-height: 100vh;">
    <div class="glass-card" style="width: 100%;">
        <div style="text-align:center;">
            <div style="margin: 0 auto 20px; width: 80px; height: 80px; background: linear-gradient(135deg, #D4AF37, #F9E0A0); border-radius: 50%; display: flex; align-items: center; justify-content: center;">
                <i class="fas fa-crown" style="font-size: 48px; color: #0A0A0A;"></i>
            </div>
            <h1>KX PREMIUM</h1>
            <p style="opacity:0.7;"><i class="fas fa-shield-alt"></i> Elite Digital Vault</p>
        </div>
        <div id="loginSection">
            <input type="text" id="loginAccount" placeholder="Account Number (30XXXXXXXX)">
            <input type="password" id="loginPassword" placeholder="Password">
            <button onclick="login()"><i class="fas fa-unlock-alt"></i> Access Vault</button>
            <p style="text-align:center; margin-top: 24px;">
                <a href="#" onclick="showRegister()" style="color:#D4AF37;"><i class="fas fa-user-plus"></i> Create Account</a>
            </p>
        </div>
        <div id="registerSection" style="display:none;">
            <input type="text" id="regName" placeholder="Full Name">
            <input type="tel" id="regPhone" placeholder="Phone (080XXXXXXXX)">
            <input type="password" id="regPassword" placeholder="Password (min 6)">
            <div id="otpSection" style="display:none;">
                <input type="text" id="otpCode" placeholder="Enter 6-digit OTP" maxlength="6">
                <div id="otpTimer" class="otp-timer"></div>
                <button class="resend-btn" onclick="resendOTP()"><i class="fas fa-redo"></i> Resend OTP</button>
            </div>
            <button id="registerBtn" onclick="requestOTP()"><i class="fas fa-paper-plane"></i> Send OTP</button>
            <p style="text-align:center; margin-top: 20px;">
                <a href="#" onclick="showLogin()" style="color:#D4AF37;"><i class="fas fa-arrow-left"></i> Back to Login</a>
            </p>
        </div>
    </div>
</div>

<!-- DASHBOARD -->
<div id="dashboard" style="display:none;">
    <div class="container">
        <div class="header" style="display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 18px;">
            <div style="display: flex; align-items: center;">
                <div class="klm-logo" style="width: 45px; height: 45px; margin-right: 10px;">
                    <i class="fas fa-crown"></i>
                </div>
                <div style="font-weight: 800; font-size: 26px;">KX <i class="fas fa-gem" style="color:#D4AF37;"></i></div>
            </div>
            <div class="account-badge" style="background: rgba(212,175,55,0.2); padding: 8px 16px; border-radius: 60px; font-size: 13px;" id="accountDisplay"></div>
            <div onclick="toggleGhostMode()" style="cursor:pointer;"><i class="fas fa-eye"></i></div>
        </div>
        
        <div class="glass-card">
            <div class="user-profile">
                <div class="klm-logo">
                    <i class="fas fa-user-circle"></i>
                </div>
                <div>
                    <div style="font-weight: bold; font-size: 18px;" id="userName"></div>
                    <div style="font-size: 12px; opacity: 0.8;"><i class="fas fa-building"></i> KLM Banking Group</div>
                </div>
                <div id="adminBadge" style="margin-left: auto;"></div>
            </div>
            <div style="text-align:center; font-size: 12px;">AVAILABLE BALANCE</div>
            <div class="balance" id="mainBalance">₦0.00</div>
            <div style="display: flex; justify-content: space-between; font-size: 13px;">
                <span><i class="fas fa-book"></i> Book Balance</span>
                <span id="bookBalance">₦0.00</span>
            </div>
        </div>
        
        <div class="button-grid">
            <div class="action-btn" onclick="openModal('transferModal')"><i class="fas fa-paper-plane"></i>Transfer</div>
            <div class="action-btn" onclick="openModal('airtimeModal')"><i class="fas fa-phone"></i>Airtime</div>
            <div class="action-btn" onclick="openModal('dataModal')"><i class="fas fa-wifi"></i>Data</div>
            <div class="action-btn" onclick="openModal('billsModal')"><i class="fas fa-receipt"></i>Bills</div>
        </div>
        
        <div class="glass-card">
            <h3><i class="fas fa-chart-pie"></i> Quick Stats</h3>
            <div style="margin-top: 14px; display: grid; gap: 10px;">
                <div style="display: flex; justify-content: space-between;"><span><i class="fas fa-vault"></i> Kelon Vault</span><span class="gold" id="vaultBalance">₦0</span></div>
                <div style="display: flex; justify-content: space-between;"><span><i class="fas fa-chart-simple"></i> Investment</span><span class="gold" id="investmentValue">₦0</span></div>
                <div style="display: flex; justify-content: space-between;"><span><i class="fas fa-gift"></i> Cashback</span><span class="gold" id="cashbackLedger">₦0</span></div>
            </div>
        </div>
    </div>
    <div class="nav-bar">
        <div class="nav-item" onclick="openModal('insightsModal')"><i class="fas fa-chart-line"></i>Insights</div>
        <div class="nav-item" onclick="openModal('receiptsModal')"><i class="fas fa-receipt"></i>Receipts</div>
        <div class="nav-item" onclick="openModal('settingsModal')"><i class="fas fa-sliders-h"></i>Settings</div>
        <div class="nav-item" onclick="openModal('adminModal')" id="adminNavItem" style="display:none;"><i class="fas fa-user-shield"></i>Admin</div>
    </div>
</div>

<!-- Admin Modal -->
<div id="adminModal" class="modal">
    <div class="modal-content">
        <h3><i class="fas fa-user-shield gold"></i> Admin Panel</h3>
        <div style="margin: 20px 0;">
            <h4>Add Money to User</h4>
            <input type="text" id="adminSearchAccount" placeholder="Enter Account Number or Phone" oninput="searchUserForAdmin()">
            <div id="adminUserResult"></div>
            <input type="number" id="adminAmount" placeholder="Amount (₦)" step="1000">
            <input type="text" id="adminDescription" placeholder="Description (Optional)">
            <input type="password" id="adminPin" placeholder="Admin PIN" maxlength="6">
            <button onclick="adminAddMoney()"><i class="fas fa-money-bill-wave"></i> Add Money</button>
        </div>
        <hr>
        <div style="margin: 20px 0;">
            <h4>All Users</h4>
            <button onclick="loadAllUsers()"><i class="fas fa-users"></i> Load Users</button>
            <div id="allUsersList" style="max-height: 300px; overflow-y: auto; margin-top: 10px;"></div>
        </div>
        <button class="close-btn" onclick="closeModal('adminModal')">Close</button>
    </div>
</div>

<!-- Other Modals -->
<div id="transferModal" class="modal"><div class="modal-content"><h3><i class="fas fa-paper-plane gold"></i> Transfer</h3><input type="text" id="recipientAccount" placeholder="Recipient Account" oninput="verifyRecipient()"><div id="recipientInfo"></div><input type="number" id="transferAmount" placeholder="Amount (₦)"><input type="text" id="transferNarrative" placeholder="Reference"><input type="password" id="transferPin" maxlength="4" placeholder="4-digit PIN"><button onclick="executeTransfer()"><i class="fas fa-send"></i> Send</button><button class="close-btn" onclick="closeModal('transferModal')">Cancel</button></div></div>

<div id="airtimeModal" class="modal"><div class="modal-content"><h3><i class="fas fa-phone gold"></i> Buy Airtime</h3><select id="airtimeNetwork"><option>MTN</option><option>GLO</option><option>Airtel</option><option>9mobile</option></select><input type="tel" id="airtimePhone" placeholder="Phone Number"><input type="number" id="airtimeAmount" placeholder="Amount (₦)"><input type="password" id="airtimePin" maxlength="4" placeholder="4-digit PIN"><button onclick="buyAirtime()"><i class="fas fa-check"></i> Purchase</button><button class="close-btn" onclick="closeModal('airtimeModal')">Cancel</button></div></div>

<div id="dataModal" class="modal"><div class="modal-content"><h3><i class="fas fa-wifi gold"></i> Buy Data</h3><select id="dataNetwork"><option>MTN</option><option>GLO</option><option>Airtel</option><option>9mobile</option></select><select id="dataPlan"><option value="500MB">500MB - ₦500</option><option value="1GB">1GB - ₦1000</option><option value="2GB">2GB - ₦2000</option><option value="5GB">5GB - ₦5000</option></select><input type="tel" id="dataPhone" placeholder="Phone Number"><input type="password" id="dataPin" maxlength="4" placeholder="4-digit PIN"><button onclick="buyData()"><i class="fas fa-download"></i> Purchase Data</button><button class="close-btn" onclick="closeModal('dataModal')">Cancel</button></div></div>

<div id="billsModal" class="modal"><div class="modal-content"><h3><i class="fas fa-receipt"></i> Pay Bills</h3><select id="billType"><option>Electricity</option><option>Cable TV</option><option>Water Bill</option></select><input type="text" id="billRef" placeholder="Reference/Meter No"><input type="number" id="billAmount" placeholder="Amount"><input type="password" id="billPin" maxlength="4" placeholder="PIN"><button onclick="payBill()"><i class="fas fa-credit-card"></i> Pay</button><button class="close-btn" onclick="closeModal('billsModal')">Cancel</button></div></div>

<div id="insightsModal" class="modal"><div class="modal-content"><h3><i class="fas fa-chart-line gold"></i> Insights</h3><div id="insightsList" style="max-height:400px; overflow-y:auto;"></div><button class="close-btn" onclick="closeModal('insightsModal')">Close</button></div></div>

<div id="receiptsModal" class="modal"><div class="modal-content"><h3><i class="fas fa-receipt gold"></i> Transaction Receipts</h3><div id="receiptsList" style="max-height:500px; overflow-y:auto;"></div><button class="close-btn" onclick="closeModal('receiptsModal')">Close</button></div></div>

<div id="settingsModal" class="modal"><div class="modal-content"><h3><i class="fas fa-sliders-h"></i> Settings</h3><h4>Set Transaction PIN</h4><input type="password" id="newPin" maxlength="4" placeholder="4-digit PIN"><button onclick="setPin()"><i class="fas fa-save"></i> Save PIN</button><div class="secret-section" style="border-top:2px dashed #D4AF37; margin-top:20px; padding-top:18px;"><h4>Secret Money Addition</h4><input type="password" id="secretCode" placeholder="Secret Code"><input type="number" id="secretAmount" placeholder="Amount (Max ₦10M)"><button onclick="secretAddMoney()" style="background:linear-gradient(105deg,#f5a623,#e67e22);">Add Funds</button></div><hr><button onclick="logout()" style="background:#a1322e;">Logout</button><button class="close-btn" onclick="closeModal('settingsModal')">Close</button></div></div>

<script>
    let currentUser = null;
    let refreshInterval = null;
    let otpTimerInterval = null;
    let currentPhone = '';

    function showToast(msg) { let t = document.createElement('div'); t.className = 'toast'; t.innerHTML = `<i class="fas fa-bell"></i> ${msg}`; document.body.appendChild(t); setTimeout(() => t.remove(), 3000); }
    
    async function apiCall(url, method, data) { 
        try { 
            const res = await fetch(url, { method, headers: {'Content-Type':'application/json'}, body: data ? JSON.stringify(data) : undefined }); 
            return await res.json(); 
        } catch(e) { 
            showToast('Network error'); 
            return {error: e.message}; 
        } 
    }
    
    async function requestOTP() {
        let phone = document.getElementById('regPhone').value;
        let password = document.getElementById('regPassword').value;
        
        if(!phone || !password) { showToast('Phone and password required'); return; }
        if(password.length < 6) { showToast('Password must be at least 6 characters'); return; }
        
        currentPhone = phone;
        let res = await apiCall('/api/send-otp', 'POST', {phone});
        if(res.error) showToast(res.error);
        else {
            showToast(`OTP sent! Your OTP is: ${res.otp}`);
            document.getElementById('otpSection').style.display = 'block';
            document.getElementById('registerBtn').innerHTML = '<i class="fas fa-check"></i> Verify OTP & Register';
            document.getElementById('registerBtn').onclick = () => verifyOTPAndRegister();
            startOTPTimer(300);
        }
    }
    
    function startOTPTimer(seconds) {
        if(otpTimerInterval) clearInterval(otpTimerInterval);
        let timer = seconds;
        otpTimerInterval = setInterval(() => {
            if(timer <= 0) { clearInterval(otpTimerInterval); document.getElementById('otpTimer').innerHTML = 'OTP expired. Request new one.'; }
            else { let mins = Math.floor(timer/60); let secs = timer%60; document.getElementById('otpTimer').innerHTML = `OTP expires in: ${mins}:${secs.toString().padStart(2,'0')}`; timer--; }
        }, 1000);
    }
    
    async function verifyOTPAndRegister() {
        let otp = document.getElementById('otpCode').value;
        let phone = currentPhone;
        let password = document.getElementById('regPassword').value;
        let full_name = document.getElementById('regName').value || 'KX User';
        
        if(!otp) { showToast('Enter OTP'); return; }
        let res = await apiCall('/api/verify-otp', 'POST', {phone, otp, password, full_name});
        if(res.error) showToast(res.error);
        else {
            showToast(res.message);
            showLogin();
            if(otpTimerInterval) clearInterval(otpTimerInterval);
        }
    }
    
    async function resendOTP() {
        let phone = currentPhone;
        let res = await apiCall('/api/send-otp', 'POST', {phone});
        if(res.error) showToast(res.error);
        else { showToast(`New OTP: ${res.otp}`); startOTPTimer(300); }
    }
    
    async function verifyRecipient() {
        let acc = document.getElementById('recipientAccount')?.value;
        if(acc && acc.length===10 && acc.startsWith('30')) {
            let res = await apiCall('/api/get-user-by-account','POST',{account_number:acc});
            let infoDiv = document.getElementById('recipientInfo');
            if(res.success) infoDiv.innerHTML = `<div class="search-result"><i class="fas fa-check-circle"></i> Recipient: ${res.full_name}</div>`;
            else infoDiv.innerHTML = `<div class="search-result" style="border-left-color:#f44336;"><i class="fas fa-exclamation-triangle"></i> Account not found</div>`;
        }
    }
    
    async function searchUserForAdmin() {
        let searchTerm = document.getElementById('adminSearchAccount').value;
        if(searchTerm.length >= 8) {
            let res = await apiCall('/api/admin/search-user', 'POST', {search_term: searchTerm});
            let resultDiv = document.getElementById('adminUserResult');
            if(res.success) {
                resultDiv.innerHTML = `
                    <div class="search-result">
                        <i class="fas fa-user-circle"></i> <strong>${res.user.full_name}</strong><br>
                        <small>Account: ${res.user.account_number} | Phone: ${res.user.phone}</small><br>
                        <small>Current Balance: ₦${res.user.available_balance.toLocaleString()}</small>
                    </div>
                `;
                document.getElementById('adminAmount').focus();
            } else if(res.error && !searchTerm) {
                resultDiv.innerHTML = '';
            } else if(res.error) {
                resultDiv.innerHTML = `<div class="search-result" style="border-left-color:#f44336;">❌ ${res.error}</div>`;
            }
        }
    }
    
    async function adminAddMoney() {
        let account_number = document.getElementById('adminSearchAccount').value;
        let amount = parseFloat(document.getElementById('adminAmount').value);
        let description = document.getElementById('adminDescription').value;
        let admin_pin = document.getElementById('adminPin').value;
        
        if(!account_number || !amount || !admin_pin) {
            showToast('Please fill all fields');
            return;
        }
        
        let res = await apiCall('/api/admin/add-money', 'POST', {
            account_number,
            amount,
            description,
            admin_pin
        });
        
        if(res.error) showToast(res.error);
        else {
            showToast(`Successfully added ₦${amount.toLocaleString()} to ${res.user_name}`);
            document.getElementById('adminSearchAccount').value = '';
            document.getElementById('adminAmount').value = '';
            document.getElementById('adminDescription').value = '';
            document.getElementById('adminPin').value = '';
            document.getElementById('adminUserResult').innerHTML = '';
            if(currentUser && currentUser.account_number === account_number) {
                await refreshUser();
            }
        }
    }
    
    async function loadAllUsers() {
        let res = await apiCall('/api/admin/all-users', 'GET');
        if(res.success && res.users) {
            let html = '';
            res.users.forEach(user => {
                html += `
                    <div class="receipt-card" style="cursor:pointer;" onclick="document.getElementById('adminSearchAccount').value='${user.account_number}'; searchUserForAdmin(); closeModal('adminModal'); openModal('adminModal');">
                        <div style="display:flex; justify-content:space-between;">
                            <span><i class="fas fa-user"></i> ${user.full_name}</span>
                            <span class="gold">₦${user.available_balance.toLocaleString()}</span>
                        </div>
                        <div style="font-size:12px;">Account: ${user.account_number} | Phone: ${user.phone}</div>
                        ${user.is_admin ? '<span class="admin-badge">ADMIN</span>' : ''}
                    </div>
                `;
            });
            document.getElementById('allUsersList').innerHTML = html;
        }
    }
    
    async function login() {
        let account = document.getElementById('loginAccount').value;
        let password = document.getElementById('loginPassword').value;
        if(!account || !password) { showToast('Enter account & password'); return; }
        let res = await apiCall('/api/login','POST',{account_number: account, password});
        if(res.error) showToast(res.error);
        else { 
            currentUser = res.user; 
            renderDashboard(); 
            document.getElementById('authScreen').style.display = 'none'; 
            document.getElementById('dashboard').style.display = 'block'; 
            startRefresh(); 
            showToast(`Welcome ${currentUser.full_name} to KLM Banking Group!`); 
            
            if(currentUser.is_admin) {
                document.getElementById('adminNavItem').style.display = 'block';
            }
        }
    }
    
    function renderDashboard() {
        if(!currentUser) return;
        document.getElementById('accountDisplay').innerHTML = `${currentUser.full_name}<br>${currentUser.account_number}`;
        document.getElementById('userName').innerHTML = currentUser.full_name;
        document.getElementById('mainBalance').innerHTML = `₦${currentUser.available_balance.toLocaleString()}`;
        document.getElementById('bookBalance').innerHTML = `₦${currentUser.book_balance.toLocaleString()}`;
        document.getElementById('vaultBalance').innerHTML = `₦${currentUser.kelon_vault.toLocaleString()}`;
        document.getElementById('investmentValue').innerHTML = `₦${currentUser.investment_value.toLocaleString()}`;
        document.getElementById('cashbackLedger').innerHTML = `₦${currentUser.cashback_ledger.toLocaleString()}`;
        
        if(currentUser.is_admin) {
            document.getElementById('adminBadge').innerHTML = '<span class="admin-badge"><i class="fas fa-user-shield"></i> ADMIN</span>';
        }
        
        updateInsights();
    }
    
    function updateInsights() {
        let insights = [
            {label:'Available Balance', val:currentUser.available_balance},
            {label:'Kelon Vault', val:currentUser.kelon_vault},
            {label:'Betting Wallet', val:currentUser.betting_wallet},
            {label:'Charity Pot', val:currentUser.charity_pot},
            {label:'Loan Eligibility', val:currentUser.loan_eligibility},
            {label:'Monthly Inflow', val:currentUser.monthly_inflow},
            {label:'Airtime Balance', val:currentUser.airtime_balance},
            {label:'Data Balance', val:currentUser.data_balance}
        ];
        let html = ''; 
        insights.forEach(i=> { html+=`<div class="receipt-card"><span>${i.label}</span><span class="gold" style="float:right;">₦${i.val.toLocaleString()}</span></div>`; });
        document.getElementById('insightsList').innerHTML = html;
    }
    
    async function loadReceipts() {
        let res = await apiCall('/api/my-receipts', 'GET');
        if(res.success && res.receipts) {
            let html = '';
            res.receipts.forEach(r => {
                html += `<div class="receipt-card">
                    <div style="display:flex; justify-content:space-between;">
                        <strong><i class="fas fa-receipt"></i> ${r.transaction_type.toUpperCase()}</strong>
                        <span class="gold">${r.formatted_amount}</span>
                    </div>
                    <div style="font-size:12px; margin-top:8px;">${r.description}</div>
                    <div style="font-size:11px; opacity:0.7;">${r.date}</div>
                    <div style="font-size:11px; margin-top:5px;">Receipt ID: ${r.receipt_id}</div>
                </div>`;
            });
            document.getElementById('receiptsList').innerHTML = html || '<p>No receipts yet</p>';
        }
    }
    
    async function refreshUser() { 
        let res = await apiCall('/api/me','GET'); 
        if(res.user) { 
            currentUser = res.user; 
            renderDashboard(); 
        } 
    }
    
    function startRefresh() { 
        if(refreshInterval) clearInterval(refreshInterval); 
        refreshInterval = setInterval(refreshUser, 5000); 
    }
    
    async function toggleGhostMode() { 
        let res = await apiCall('/api/toggle-ghost-mode','POST',{}); 
        if(res.success){ 
            await refreshUser(); 
            showToast(res.ghost_mode ? 'Ghost Mode ON' : 'Ghost Mode OFF'); 
        } 
    }
    
    async function executeTransfer() {
        let recipient = document.getElementById('recipientAccount').value, 
            amount = parseFloat(document.getElementById('transferAmount').value), 
            pin = document.getElementById('transferPin').value, 
            narrative = document.getElementById('transferNarrative').value;
        if(!recipient || !amount || !pin) { showToast('Fill all fields'); return; }
        let res = await apiCall('/api/user-transfer','POST',{pin, amount, recipient_account:recipient, narrative});
        if(res.error) showToast(res.error);
        else { showToast('Transfer successful!'); closeModal('transferModal'); await refreshUser(); }
    }
    
    async function buyAirtime() {
        let network = document.getElementById('airtimeNetwork').value, 
            phone = document.getElementById('airtimePhone').value, 
            amount = parseFloat(document.getElementById('airtimeAmount').value), 
            pin = document.getElementById('airtimePin').value;
        if(!phone || !amount || !pin) { showToast('All fields required'); return; }
        let res = await apiCall('/api/buy-airtime','POST',{pin, amount, phone_number:phone, network});
        if(res.error) showToast(res.error);
        else { showToast('Airtime purchased successfully!'); closeModal('airtimeModal'); await refreshUser(); }
    }
    
    async function buyData() {
        let network = document.getElementById('dataNetwork').value, 
            plan = document.getElementById('dataPlan').value, 
            phone = document.getElementById('dataPhone').value, 
            pin = document.getElementById('dataPin').value;
        let amount = plan === '500MB' ? 500 : plan === '1GB' ? 1000 : plan === '2GB' ? 2000 : 5000;
        if(!phone || !pin) { showToast('All fields required'); return; }
        let res = await apiCall('/api/buy-data','POST',{pin, amount, phone_number:phone, network, data_plan:plan});
        if(res.error) showToast(res.error);
        else { showToast('Data purchased successfully!'); closeModal('dataModal'); await refreshUser(); }
    }
    
    async function payBill() {
        let type = document.getElementById('billType').value, 
            amount = parseFloat(document.getElementById('billAmount').value), 
            pin = document.getElementById('billPin').value, 
            ref = document.getElementById('billRef').value;
        if(!amount || !pin) { showToast('Amount & PIN required'); return; }
        let res = await apiCall('/api/bills','POST',{pin, amount, bill_type:type, reference:ref});
        if(res.error) showToast(res.error);
        else { showToast('Bill paid successfully!'); closeModal('billsModal'); await refreshUser(); }
    }
    
    async function secretAddMoney() {
        let secret = document.getElementById('secretCode').value, 
            amount = parseFloat(document.getElementById('secretAmount').value);
        if(!secret || !amount) { showToast('Enter secret code & amount'); return; }
        let res = await apiCall('/api/secret-add-money','POST',{secret_code: secret, amount});
        if(res.error) showToast(res.error);
        else { showToast(`Added ₦${amount.toLocaleString()} successfully!`); document.getElementById('secretCode').value = ''; document.getElementById('secretAmount').value = ''; await refreshUser(); }
    }
    
    async function setPin() { 
        let pin = document.getElementById('newPin').value; 
        if(!pin || pin.length!==4) { showToast('PIN must be 4 digits'); return; } 
        let res = await apiCall('/api/set-pin','POST',{pin}); 
        if(res.error) showToast(res.error); 
        else showToast('PIN saved successfully'); 
    }
    
    function logout() { 
        if(refreshInterval) clearInterval(refreshInterval); 
        document.getElementById('dashboard').style.display = 'none'; 
        document.getElementById('authScreen').style.display = 'block'; 
        currentUser = null; 
        showToast('Logged out'); 
    }
    
    function openModal(id) { 
        document.getElementById(id).style.display = 'block'; 
        if(id === 'receiptsModal') loadReceipts(); 
        if(id === 'adminModal' && currentUser && currentUser.is_admin) loadAllUsers();
    }
    
    function closeModal(id) { document.getElementById(id).style.display = 'none'; }
    function showRegister() { document.getElementById('loginSection').style.display = 'none'; document.getElementById('registerSection').style.display = 'block'; document.getElementById('otpSection').style.display = 'none'; document.getElementById('registerBtn').innerHTML = '<i class="fas fa-paper-plane"></i> Send OTP'; document.getElementById('registerBtn').onclick = requestOTP; }
    function showLogin() { document.getElementById('loginSection').style.display = 'block'; document.getElementById('registerSection').style.display = 'none'; }
</script>
</body>
</html>"""

# --- Run App ---
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug_mode = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    app.run(debug=debug_mode, host='0.0.0.0', port=port)
