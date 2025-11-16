from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import random

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

# Pace settings: seconds between combos
PACE_CONFIG = {
    "Slow": 20,
    "Normal": 15,
    "Fast": 12
}


def normalize_pace(pace_raw: str) -> str:
    """
    Map whatever comes from the frontend into one of: Slow, Normal, Fast.
    Default to Normal if unknown.
    """
    if not pace_raw:
        return "Normal"

    p = pace_raw.strip().lower()
    if p in ["slow"]:
        return "Slow"
    if p in ["fast"]:
        return "Fast"
    # default
    return "Normal"


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


def build_round_events(round_number: int, difficulty: str, pace_label: str,
                       round_duration_sec: int = 180) -> dict:
    """
    Build the internal timeline for a single round:
      - combos at fixed intervals, based on pace
      - 0–2 tips in between combos
      - 0–2 motivation lines in between combos
      - last-10-seconds countdown at 2:50
      - break countdown during the 30s break

    We DO NOT choose specific file names for combos/tips/motivation yet.
    We only describe the event types & times. The audio builder will map
    these to actual audio files from the correct folders.
    """
    pace_seconds = PACE_CONFIG.get(pace_label, 15)

    # ---- COMBO TIMING ----
    # First combo at t=2s (give a moment after the bell)
    # Stop scheduling new combos after t = round_duration_sec - 15
    # so we have room for last-10-seconds countdown.
    combo_times = []
    t = 2
    last_combo_cutoff = round_duration_sec - 15  # e.g. 165 for a 180s round

    while t <= last_combo_cutoff:
        combo_times.append(t)
        t += pace_seconds

    events = []

    # Add combo events
    for ct in combo_times:
        events.append({
            "time_sec": ct,
            "event_type": "combo",
            "difficulty": difficulty  # tells the builder which folder to pull from
        })

    # ---- TIPS & MOTIVATION ----
    # 0–2 tips, 0–2 motivation, placed in gaps between combos
    num_tips = random.randint(0, 2)
    num_motivation = random.randint(0, 2)

    # Build gaps between combo slots (start->first, between combos, last->170)
    gap_windows = []

    if combo_times:
        # From round start (0) to first combo
        gap_windows.append((0, combo_times[0]))

        # Between combos
        for i in range(len(combo_times) - 1):
            gap_windows.append((combo_times[i], combo_times[i + 1]))

        # From last combo to last-ten countdown (~170s)
        gap_windows.append((combo_times[-1], round_duration_sec - 10))
    else:
        # Edge case: no combos (shouldn't happen with current settings)
        gap_windows.append((0, round_duration_sec - 10))

    # Only use gaps that are big enough
    usable_gaps = [g for g in gap_windows if g[1] - g[0] >= 5]

    # Randomly pick gaps for tips and motivation (no overlap)
    random.shuffle(usable_gaps)

    tip_gaps = usable_gaps[:num_tips]
    remaining_gaps = [g for g in usable_gaps if g not in tip_gaps]
    random.shuffle(remaining_gaps)
    mot_gaps = remaining_gaps[:num_motivation]

    # Place tips in middle of their gap
    for g in tip_gaps:
        start, end = g
        mid = int((start + end) / 2)
        # Avoid exact collision with combo times
        if mid not in combo_times:
            events.append({
                "time_sec": mid,
                "event_type": "tip"
            })

    # Place motivation in middle of their gap
    for g in mot_gaps:
        start, end = g
        mid = int((start + end) / 2)
        if mid not in combo_times:
            events.append({
                "time_sec": mid,
                "event_type": "motivation"
            })

    # ---- LAST 10 SECONDS COUNTDOWN ----
    last_ten_start = round_duration_sec - 10  # e.g. 170
    events.append({
        "time_sec": last_ten_start,
        "event_type": "countdown",
        "variant": "last-ten-seconds-push"
    })

    # Sort events by time
    events.sort(key=lambda e: e["time_sec"])

    # ---- BREAK EVENTS ----
    # 30s break after the round, with a "break in 3-2-1" near the end
    break_events = [
        {
            "time_sec": 27,  # 3 seconds before break ends
            "event_type": "countdown",
            "variant": "break-in-3-2-1"
        }
    ]

    return {
        "round_number": round_number,
        "duration_sec": round_duration_sec,
        "events": events,
        "break_duration_sec": 30,
        "break_events": break_events
    }


def build_class_flow(difficulty: str, length_min: int, music: str, pace_raw: str) -> dict:
    """
    Build a high-level class plan as JSON.
    This is COACH-ONLY for now (no music layer yet).
    """
    pace_label = normalize_pace(pace_raw)
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
        round_data = build_round_events(
            round_number=r,
            difficulty=difficulty,
            pace_label=pace_label,
            round_duration_sec=180
        )

        segments.append({
            "type": "round",
            "round_number": r,
            "duration_sec": round_data["duration_sec"],
            "round_callout_file": f"rounds/round-{r}.mp3",  # adjust if your names differ
            "start_file": "round_start_end/get-ready-round-starting.mp3",
            "end_file": "round_start_end/time-recover-and-breathe.mp3",
            "events": round_data["events"],
            "break_duration_sec": round_data["break_duration_sec"],
            "break_events": round_data["break_events"]
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
        "pace": pace_label,
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
    pace_raw = data.get("pace", "Normal")

    try:
        length_min = int(length_raw)
    except Exception:
        length_min = 30

    flow = build_class_flow(difficulty, length_min, music, pace_raw)
    return jsonify(flow)


@app.route("/debug-generate", methods=["GET"])
def debug_generate():
    """
    Browser-friendly test.
    Optional query params:
      ?difficulty=beginner&length=60&pace=Fast&music=None
    """
    difficulty = request.args.get("difficulty", "beginner")
    length_raw = request.args.get("length", "60")
    music = request.args.get("music", "None")
    pace_raw = request.args.get("pace", "Normal")

    try:
        length_min = int(length_raw)
    except Exception:
        length_min = 60

    flow = build_class_flow(difficulty, length_min, music, pace_raw)
    return jsonify(flow)


# -----------------------------
# LOCAL DEV
# -----------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
