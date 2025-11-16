from flask import Flask, request, jsonify
from flask_cors import CORS
import requests

app = Flask(__name__)
CORS(app)

# --------------------------------------
# SUPABASE (REST VERSION — LIGHTWEIGHT)
# --------------------------------------

SUPABASE_URL = "https://lbhmfkmrluoropzfleaa.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImxiaG1ma21ybHVvcm9wemZsZWFhIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2MzIyMjAyOSwiZXhwIjoyMDc4Nzk4MDI5fQ.Bmqu3Y9Woe4JPVO9bNviXN9ePJWc0LeIsItLjUT2mgQ"

BUCKET = "audio"   # your bucket name


def public_url(path):
    """
    Returns the direct public URL to a file in Supabase storage.
    Example: public_url("beginner/1-2.mp3")
    """
    return f"{SUPABASE_URL}/storage/v1/object/public/{BUCKET}/{path}"


# --------------------------------------
# BASIC ROUTES
# --------------------------------------

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


# --------------------------------------
# TEST AUDIO DOWNLOAD (IMPORTANT)
# --------------------------------------

@app.route("/test-audio", methods=["GET"])
def test_audio():
    """
    This tests downloading a single audio file from Supabase.
    Replace the path with ANY real file in your bucket.
    """
    test_path = "1-1-2.mp3"   # <-- Replace with ANY real file

    url = public_url(test_path)
    print("Attempting:", url)

    try:
        r = requests.get(url)

        if r.status_code == 200:
            return jsonify({
                "status": "success",
                "url": url,
                "size_bytes": len(r.content)
            })
        else:
            return jsonify({
                "status": "failed",
                "code": r.status_code,
                "url": url
            }), 500

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# --------------------------------------
# LOCAL DEV MODE (Render ignores this)
# --------------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)

# redeploy
