import os

from flask import Flask

from app.webapp import bp as webapp_bp
from app.webhook import bp as webhook_bp

app = Flask(__name__)
app.register_blueprint(webhook_bp)
app.register_blueprint(webapp_bp)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
