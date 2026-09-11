import os

from flask import Flask

from app.webhook import bp

app = Flask(__name__)
app.register_blueprint(bp)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
