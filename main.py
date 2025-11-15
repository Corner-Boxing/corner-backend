from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)  # ENABLE CORS FOR ALL ROUTES

@app.route("/")
def home():
    return "Corner Backend Running"

@app.route("/generate", methods=["POST"])
def generate():
    data = request.json

    difficulty = data.get("difficulty")
    length = data.get("length")
    music = data.get("music")
    pace = data.get("pace")

    return jsonify({
        "status": "received",
        "difficulty": difficulty,
        "length": length,
        "music": music,
        "pace": pace
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
