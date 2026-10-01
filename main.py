import os

from flask import Flask, render_template

from blueprints.discord_bp import discord_bp
from blueprints.twitch_bp import twitch_bp

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY")

DEBUG = bool(os.getenv("DEBUG"))

app.register_blueprint(twitch_bp)
app.register_blueprint(discord_bp)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/bot-management")
def bot_page():
    return render_template("login.html")


@app.route("/portfolio")
def portfolio():
    return render_template("portfolio.html")


if __name__ == "__main__":
    app.run(port=3000, host="0.0.0.0", debug=DEBUG)
