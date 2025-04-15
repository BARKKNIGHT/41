from flask_mail import Message
from flask import url_for, current_app
from extensions import mail  # <-- import here

def send_reset_email(user):
    token = user.get_reset_token()
    msg = Message("Password Reset Request",
                  recipients=[user.email])
    msg.body = f'''To reset your password, visit the following link:
{url_for('reset_token', token=token, _external=True)}

If you did not make this request, simply ignore this email.
'''
    mail.send(msg)

def send_verification_email(user):
    token = user.get_email_token()
    msg = Message("Email Verification",
                  recipients=[user.email])
    msg.body = f'''To verify your email, visit the following link:
{url_for('verify_email', token=token, _external=True)}

If you did not register, simply ignore this email.
'''
    mail.send(msg)
