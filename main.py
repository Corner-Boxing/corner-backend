from flask import Flask, request, jsonify

app = Flask(__name__)

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

    # TEMP RESPONSE: just echo data back
    return jsonify({
        "status": "received",
        "difficulty": difficulty,
        "length": length,
        "music": music,
        "pace": pace
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
