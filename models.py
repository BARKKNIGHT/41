from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from itsdangerous import URLSafeTimedSerializer as Serializer
from flask import current_app
from extensions import db

db = db

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    email_verified = db.Column(db.Boolean, default=False)
    videos = db.relationship("Video", backref="owner", lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    def get_reset_token(self, expires_sec=3600):
        s = Serializer(current_app.config["SECRET_KEY"])
        return s.dumps({"user_id": self.id})
    @staticmethod
    def verify_reset_token(token):
        s = Serializer(current_app.config["SECRET_KEY"])
        try:
            user_id = s.loads(token, max_age=3600)["user_id"]
        except Exception:
            return None
        return User.query.get(user_id)
    def get_email_token(self, expires_sec=3600):
        s = Serializer(current_app.config["SECRET_KEY"])
        return s.dumps({"user_id": self.id})
    @staticmethod
    def verify_email_token(token):
        s = Serializer(current_app.config["SECRET_KEY"])
        try:
            user_id = s.loads(token, max_age=3600)["user_id"]
        except Exception:
            return None
        return User.query.get(user_id)

class Video(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(256), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    upload_time = db.Column(db.DateTime, nullable=False)
    duration = db.Column(db.Float, nullable=True)
    summary = db.Column(db.Text, nullable=True)
    transcript = db.Column(db.Text, nullable=True)
    frames_json = db.Column(db.Text, nullable=True)  # Store frame info as JSON
