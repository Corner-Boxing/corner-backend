# main.py

import os
import random
import tempfile
import time

from flask import Flask, request, jsonify
from flask_cors import CORS
from supabase import create_client, Client
from pydub import AudioSegment

import requests  # still used by /test-audio


# ------------------------
# Flask + Supabase setup
# ------------------------

app = Flask(__name__)
CORS(app)

SUPABASE_URL = "https://lbhmfkmrluoropzfleaa.supabase.co"
SUPABASE_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImxiaG1m"
    "a21ybHVvcm9wemZsZWFhIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2MzIyMjAyOSwi"
    "ZXhwIjoyMDc4Nzk4MDI5fQ.Bmqu3Y9Woe4JPVO9bNviXN9ePJWc0LeIsItLjUT2mgQ"
)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Local audio directory in your repo (matches what you showed: audio/advanced, audio/beginner, etc.)
BASE_AUDIO_DIR = os.path.join(os.path.dirname(__file__), "audio")


# ------------------------
# Helpers – file loading
# ------------------------

def load_audio(rel_path: str) -> AudioSegment:
    """
    rel_path examples:
      'intro_outro/intro.mp3'
      'beginner/1-1-2.mp3'
      'tips/breathe-between-combos.mp3'
    """
    full_path = os.path.join(BASE_AUDIO_DIR, rel_path)
    if not os.path.isfile(full_path):
        raise FileNotFoundError(f"Audio file not found: {full_path}")
    return AudioSegment.from_file(full_path)


def random_audio_path(subdir: str) -> str:
    """
    Returns a random .mp3 inside audio/<subdir>/
    e.g. subdir='beginner' -> 'beginner/1-1-2.mp3'
         subdir='tips'     -> 'tips/whatever.mp3'
    """
    dir_path = os.path.join(BASE_AUDIO_DIR, subdir)
    files = [
        f for f in os.listdir(dir_path)
        if f.lower().endswith(".mp3")
    ]
    if not files:
        raise RuntimeError(f"No mp3 files found in {dir_path}")
    filename = random.choice(files)
    return os.path.join(subdir, filename)


def overlay(base: AudioSegment, clip: AudioSegment, start_ms: int) -> AudioSegment:
    """Overlay clip onto base at start_ms, safely."""
    if start_ms < 0:
        start_ms = 0
    if start_ms > len(base):
        # extend with silence if needed
        base = base + AudioSegment.silent(duration=start_ms - len(base))
    return base.overlay(clip, position=start_ms)


# ------------------------
# Class plan generation
# ------------------------

def compute_num_rounds(length_min: int) -> int:
    """
    Each round block = 3 min work + 0.5 min break = 3.5 min
    Warmup + core + cooldown = 5 + 5 + 1 = 11 min
    """
    non_round = 11
    usable = max(0, length_min - non_round)
    num_rounds = max(1, int(usable // 3.5))
    return num_rounds


def build_round_segment(round_number: int, difficulty: str, pace: str) -> dict:
    duration_sec = 180
    break_duration_sec = 30

    # Pace affects combo spacing
    if pace.lower() == "fast":
        spacing = 12
    elif pace.lower() == "slow":
        spacing = 18
    else:
        spacing = 15  # Normal

    # main combo times
    combo_times = []
    t = 2
    while t < 160:
        combo_times.append(t)
        t += spacing

    events = []

    for ct in combo_times:
        events.append({
            "event_type": "combo",
            "time_sec": ct,
            "difficulty": difficulty
        })

    # Candidate coach (tip/motivation) slots roughly between combos
    coach_candidates = list(range(9, 160, spacing))
    random.shuffle(coach_candidates)
    coach_candidates = coach_candidates[:4]  # up to 4 per round

    for ts in coach_candidates:
        # Don’t drop a coach call < 4 sec away from a combo
        if any(abs(ts - c) < 4 for c in combo_times):
            continue
        if random.random() < 0.5:
            events.append({"event_type": "tip", "time_sec": ts})
        else:
            events.append({"event_type": "motivation", "time_sec": ts})

    # Last 10 seconds push
    events.append({
        "event_type": "countdown",
        "time_sec": duration_sec - 10,
        "variant": "last-ten-seconds-push"
    })

    # Break events – 'break-in-3-2-1' at 27s of a 30s break
    break_events = [{
        "event_type": "countdown",
        "time_sec": 27,
        "variant": "break-in-3-2-1"
    }]

    return {
        "type": "round",
        "round_number": round_number,
        "duration_sec": duration_sec,
        "break_duration_sec": break_duration_sec,
        "start_file": "round_start_end/get-ready-round-starting.mp3",
        "round_callout_file": f"rounds/round-{round_number}.mp3",
        "end_file": "round_start_end/time-recover-and-breathe.mp3",
        "events": sorted(events, key=lambda e: e["time_sec"]),
        "break_events": break_events
    }


def build_class_plan(difficulty: str, length_min: int, pace: str, music: str) -> dict:
    num_rounds = compute_num_rounds(length_min)

    segments = []

    # Intro
    segments.append({
        "type": "intro",
        "file": "intro_outro/intro.mp3"
    })

    # Warmup
    segments.append({
        "type": "warmup",
        "file": "warmup/warmup.mp3",
        "duration_sec": 300
    })

    # Rounds
    for r in range(1, num_rounds + 1):
        segments.append(build_round_segment(r, difficulty, pace))

    # Core
    segments.append({
        "type": "core",
        "file": "core/core.mp3",
        "duration_sec": 300
    })

    # Cooldown
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
        "music": music,
        "pace": pace,
        "num_rounds": num_rounds,
        "segments": segments
    }


# ------------------------
# Audio assembly from plan
# ------------------------

def build_audio_from_plan(plan: dict) -> AudioSegment:
    master = AudioSegment.silent(duration=0)

    for seg in plan["segments"]:
        stype = seg["type"]

        if stype in ("intro", "outro", "warmup", "core", "cooldown"):
            clip = load_audio(seg["file"])
            master += clip
            continue

        if stype == "round":
            round_len_total = (seg["duration_sec"] + seg["break_duration_sec"]) * 1000
            round_block = AudioSegment.silent(duration=round_len_total)

            # Start and callout
            start_clip = load_audio(seg["start_file"])
            round_block = overlay(round_block, start_clip, 0)

            callout_clip = load_audio(seg["round_callout_file"])
            round_block = overlay(round_block, callout_clip, 2000)

            # End-of-round call
            end_clip = load_audio(seg["end_file"])
            end_start_ms = max((seg["duration_sec"] - 4) * 1000, 0)
            round_block = overlay(round_block, end_clip, end_start_ms)

            # In-round events
            for e in seg.get("events", []):
                t_ms = int(e["time_sec"] * 1000)
                etype = e["event_type"]

                if etype == "combo":
                    rel_path = random_audio_path(e["difficulty"])
                    clip = load_audio(rel_path)
                elif etype == "tip":
                    rel_path = random_audio_path("tips")
                    clip = load_audio(rel_path)
                elif etype == "motivation":
                    rel_path = random_audio_path("motivation")
                    clip = load_audio(rel_path)
                elif etype == "countdown":
                    variant = e.get("variant", "")
                    if variant == "last-ten-seconds-push":
                        rel_path = "countdowns/last-ten-seconds-push.mp3"
                    else:
                        rel_path = "countdowns/5-4-3-2-1.mp3"
                    clip = load_audio(rel_path)
                else:
                    continue

                round_block = overlay(round_block, clip, t_ms)

            # Break events (after round duration)
            for be in seg.get("break_events", []):
                if be["event_type"] != "countdown":
                    continue
                variant = be.get("variant", "")
                if variant == "break-in-3-2-1":
                    rel_path = "countdowns/break-in-3-2-1.mp3"
                else:
                    rel_path = "countdowns/5-4-3-2-1.mp3"
                clip = load_audio(rel_path)
                t_ms = int((seg["duration_sec"] + be["time_sec"]) * 1000)
                round_block = overlay(round_block, clip, t_ms)

            master += round_block

    return master


def export_and_upload(master: AudioSegment, difficulty: str, length_min: int, pace: str) -> str:
    """
    Export final MP3 to /tmp, upload to Supabase 'audio' bucket under generated/,
    return public URL.
    """
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp_path = tmp.name

    master.export(tmp_path, format="mp3")

    timestamp = int(time.time())
    filename = f"class_{difficulty}_{length_min}min_{pace}_{timestamp}.mp3"
    object_path = f"generated/{filename}"

    with open(tmp_path, "rb") as f:
        # upload to existing 'audio' bucket
        res = supabase.storage.from_("audio").upload(
            object_path,
            f,
            {"content-type": "audio/mpeg", "upsert": True}
        )

    os.remove(tmp_path)

    # Build public URL
    public_url = f"{SUPABASE_URL}/storage/v1/object/public/audio/{object_path}"
    return public_url


# ------------------------
# Routes
# ------------------------

@app.route("/")
def home():
    return "Corner Backend Running"


@app.route("/test-audio", methods=["GET"])
def test_audio():
    # basic sanity check that a file is reachable from Supabase
    file_path = "audio/beginner/1-1-2.mp3"
    public_url = f"{SUPABASE_URL}/storage/v1/object/public/{file_path}"

    r = requests.get(public_url)
    if r.status_code == 200:
        return jsonify({
            "status": "success",
            "url": public_url,
            "size_bytes": len(r.content)
        })
    else:
        return jsonify({
            "status": "failed",
            "code": r.status_code,
            "url": public_url
        }), 400


@app.route("/debug-generate", methods=["GET"])
def debug_generate():
    difficulty = request.args.get("difficulty", "beginner").lower()
    length = int(request.args.get("length", "60"))
    pace = request.args.get("pace", "Normal")
    music = request.args.get("music", "None")

    plan = build_class_plan(difficulty, length, pace, music)
    return jsonify(plan)


@app.route("/generate", methods=["POST"])
def generate():
    data = request.get_json() or {}

    difficulty = (data.get("difficulty") or "beginner").lower()
    length_raw = data.get("length") or 60
    try:
        length_min = int(length_raw)
    except ValueError:
        length_min = 60

    pace = data.get("pace") or "Normal"
    music = data.get("music") or "None"

    plan = build_class_plan(difficulty, length_min, pace, music)

    try:
        master = build_audio_from_plan(plan)
        file_url = export_and_upload(master, difficulty, length_min, pace)

        return jsonify({
            "status": "success",
            "file_url": file_url,
            "plan": plan
        })
    except Exception as e:
        # log to stdout for Render logs
        print("ERROR assembling or uploading class:", repr(e))
        return jsonify({
            "status": "failed",
            "message": str(e)
        }), 500


if __name__ == "__main__":
    # For local testing; Render uses gunicorn
    app.run(host="0.0.0.0", port=10000)

# redeploy
