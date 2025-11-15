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

from flask import Flask, request, jsonify
from flask_cors import CORS
from supabase import create_client, Client
import requests

app = Flask(__name__)
CORS(app)

# TODO — replace these with your actual keys
SUPABASE_URL = https://lbhmfkmrluoropzfleaa.supabase.co
SUPABASE_KEY = eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImxiaG1ma21ybHVvcm9wemZsZWFhIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2MzIyMjAyOSwiZXhwIjoyMDc4Nzk4MDI5fQ.Bmqu3Y9Woe4JPVO9bNviXN9ePJWc0LeIsItLjUT2mgQ

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


@app.route("/")
def home():
    return "Corner Backend Running"


@app.route("/test-audio", methods=["GET"])
def test_audio():

    # Replace with one of your test files:
    file_path = "audio/beginner/your_test_file.mp3"

    try:
        # Generate a public URL
        public_url = f"{SUPABASE_URL}/storage/v1/object/public/{file_path}"

        # Try downloading the file
        r = requests.get(public_url)

        if r.status_code == 200:
            return jsonify({"status": "success", "public_url": public_url})
        else:
            return jsonify({"status": "error", "code": r.status_code}), 500

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

