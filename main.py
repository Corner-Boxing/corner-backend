from flask import Flask, request, jsonify
from flask_cors import CORS
import requests

app = Flask(__name__)
CORS(app)

# -----------------------------
# SUPABASE (REST PUBLIC URL)
# -----------------------------

SUPABASE_URL = "https://lbhmfkmrluoropzfleaa.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImxiaG1ma21ybHVvcm9wemZsZWFhIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2MzIyMjAyOSwiZXhwIjoyMDc4Nzk4MDI5fQ.Bmqu3Y9Woe4JPVO9bNviXN9ePJWc0LeIsItLjUT2mgQ"

BUCKET = "audio"


def public_url(path: str) -> str:
    """
    Build a direct public URL for a file in the Supabase 'audio' bucket.
    Example input: 'beginner/1-1-2.mp3'
    """
    return f"{SUPABASE_URL}/storage/v1/object/public/{BUCKET}/{path}"


# -----------------------------
# BASIC ROUTES
# -----------------------------

@app.route("/")
def home():
    return "Corner Backend Running"


@app.route("/test-audio", methods=["GET"])
def test_audio():
    """
    Simple sanity check: can we download a real file from Supabase?
    """
    test_path = "beginner/1-1-2.mp3"

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


# -----------------------------
# CLASS FLOW LOGIC (MVP)
# -----------------------------

def compute_num_rounds(class_length_min: int) -> int:
    """
    Given total class length in minutes, compute how many 3:00 rounds + 0:30 breaks fit,
    after subtracting warmup (5), core (5), cooldown (1).
    We ignore intro/outro duration (they're short).
    """
    fixed_blocks_min = 5 + 5 + 1  # warmup + core + cooldown
    available_for_rounds = max(0, class_length_min - fixed_blocks_min)

    # Each cycle: 3:00 round + 0:30 break = 3.5 minutes
    rounds = int(available_for_rounds // 3.5)

    # Never go below 1 round
    return max(1, rounds)


def build_class_flow(difficulty: str, length_min: int, music: str, pace: str) -> dict:
    """
    Build a high-level class plan as JSON.
    This is COACH-ONLY for now (no music layer yet).
    """
    num_rounds = compute_num_rounds(length_min)

    segments = []

    # Intro
    segments.append({
        "type": "intro",
        "file": "intro_outro/intro.mp3"
    })

    # Warmup (fixed 5 min)
    segments.append({
        "type": "warmup",
        "file": "warmup/warmup.mp3",
        "duration_sec": 5 * 60
    })

    # Rounds
    for r in range(1, num_rounds + 1):
        segments.append({
            "type": "round",
            "round_number": r,
            "duration_sec": 180,  # 3 min
            "round_callout_file": f"rounds/round-{r}.mp3",  # you have round-one, round-two, etc.
            "start_file": "round_start_end/get-ready-round-starting.mp3",
            "end_file": "round_start_end/time-recover-and-breathe.mp3",
            "break_duration_sec": 30
        })

    # Core (fixed 5 min)
    segments.append({
        "type": "core",
        "file": "core/core.mp3",
        "duration_sec": 5 * 60
    })

    # Cooldown (fixed 1 min)
    segments.append({
        "type": "cooldown",
        "file": "cooldown/cooldown.mp3",
        "duration_sec": 60
    })

    # Outro
    segments.append({
        "type": "outro",
        "file": "intro_outro/outro.mp3"
    })

    return {
        "difficulty": difficulty,
        "length_min": length_min,
        "pace": pace,
        "music": music,
        "num_rounds": num_rounds,
        "segments": segments
    }


# -----------------------------
# ROUTES: GENERATE CLASS PLAN
# -----------------------------

@app.route("/generate", methods=["POST"])
def generate():
    """
    Real endpoint used by your frontend.
    Expects JSON: { difficulty, length, music, pace }
    Returns: class flow JSON (no audio mixing yet).
    """
    data = request.json or {}

    difficulty = data.get("difficulty", "beginner")
    length_raw = data.get("length", 30)
    music = data.get("music", "None")
    pace = data.get("pace", "Normal")

    try:
        length_min = int(length_raw)
    except Exception:
        length_min = 30

    flow = build_class_flow(difficulty, length_min, music, pace)
    return jsonify(flow)


@app.route("/debug-generate", methods=["GET"])
def debug_generate():
    """
    Simple browser-friendly test.
    Open this in your browser to see a sample generated class plan.
    """
    sample = {
        "difficulty": "beginner",
        "length_min": 60,
        "music": "None",
        "pace": "Normal"
    }
    flow = build_class_flow(**sample)
    return jsonify(flow)


# -----------------------------
# LOCAL DEV
# -----------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
