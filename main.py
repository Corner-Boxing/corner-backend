import os
import random
import tempfile
import time
import uuid
import threading
from datetime import datetime

from flask import Flask, request, jsonify
from flask_cors import CORS
from supabase import create_client, Client
from pydub import AudioSegment
import requests


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

BASE_AUDIO_DIR = os.path.join(os.path.dirname(__file__), "audio")


# ------------------------
# Audio Helpers
# ------------------------

def load_audio(rel_path: str) -> AudioSegment:
    full_path = os.path.join(BASE_AUDIO_DIR, rel_path)
    if not os.path.isfile(full_path):
        raise FileNotFoundError(f"Audio file not found: {full_path}")
    return AudioSegment.from_file(full_path)


def random_audio_path(subdir: str) -> str:
    dir_path = os.path.join(BASE_AUDIO_DIR, subdir)
    files = [f for f in os.listdir(dir_path) if f.endswith(".mp3")]
    if not files:
        raise Exception(f"No MP3s found in {dir_path}")
    return os.path.join(subdir, random.choice(files))


def overlay(base, clip, start_ms):
    if start_ms > len(base):
        base += AudioSegment.silent(duration=start_ms - len(base))
    return base.overlay(clip, position=start_ms)


# ------------------------
# Class Plan Generation
# ------------------------

def compute_num_rounds(length_min):
    usable = max(0, length_min - 11)
    return max(1, int(usable // 3.5))


def build_round_segment(r, difficulty, pace):
    duration_sec = 180
    spacing = {"slow": 18, "fast": 12}.get(pace.lower(), 15)

    combo_times = []
    t = 2
    while t < 160:
        combo_times.append(t)
        t += spacing

    events = [{"event_type": "combo", "difficulty": difficulty, "time_sec": ct}
              for ct in combo_times]

    coach_candidates = list(range(9, 160, spacing))
    random.shuffle(coach_candidates)
    coach_candidates = coach_candidates[:4]

    for ts in coach_candidates:
        if any(abs(ts - c) < 4 for c in combo_times):
            continue
        events.append({
            "event_type": random.choice(["tip", "motivation"]),
            "time_sec": ts
        })

    events.append({
        "event_type": "countdown",
        "variant": "last-ten-seconds-push",
        "time_sec": duration_sec - 10
    })

    return {
        "type": "round",
        "round_number": r,
        "duration_sec": duration_sec,
        "break_duration_sec": 30,
        "start_file": "round_start_end/get-ready-round-starting.mp3",
        "round_callout_file": f"rounds/round-{r}.mp3",
        "end_file": "round_start_end/time-recover-and-breathe.mp3",
        "events": sorted(events, key=lambda e: e["time_sec"]),
        "break_events": [{
            "event_type": "countdown",
            "time_sec": 27,
            "variant": "break-in-3-2-1"
        }]
    }


def build_class_plan(difficulty, length_min, pace, music):
    num_rounds = compute_num_rounds(length_min)

    segs = [
        {"type": "intro", "file": "intro_outro/intro.mp3"},
        {"type": "warmup", "file": "warmup/warmup.mp3", "duration_sec": 300},
    ]

    for r in range(1, num_rounds + 1):
        segs.append(build_round_segment(r, difficulty, pace))

    segs += [
        {"type": "core", "file": "core/core.mp3", "duration_sec": 300},
        {"type": "cooldown", "file": "cooldown/cooldown.mp3", "duration_sec": 60},
        {"type": "outro", "file": "intro_outro/outro.mp3"},
    ]

    return {
        "difficulty": difficulty,
        "length_min": length_min,
        "pace": pace,
        "music": music,
        "num_rounds": num_rounds,
        "segments": segs
    }


# ------------------------
# Audio Assembly
# ------------------------

def build_audio_from_plan(plan):
    master = AudioSegment.silent(duration=0)

    for seg in plan["segments"]:
        if seg["type"] in ("intro", "outro", "warmup", "core", "cooldown"):
            master += load_audio(seg["file"])
            continue

        if seg["type"] == "round":
            block = AudioSegment.silent(duration=(seg["duration_sec"] + seg["break_duration_sec"]) * 1000)

            block = overlay(block, load_audio(seg["start_file"]), 0)
            block = overlay(block, load_audio(seg["round_callout_file"]), 2000)

            end_pos = max((seg["duration_sec"] - 4) * 1000, 0)
            block = overlay(block, load_audio(seg["end_file"]), end_pos)

            for e in seg["events"]:
                t = e["time_sec"] * 1000
                if e["event_type"] == "combo":
                    clip = load_audio(random_audio_path(e["difficulty"]))
                elif e["event_type"] == "tip":
                    clip = load_audio(random_audio_path("tips"))
                elif e["event_type"] == "motivation":
                    clip = load_audio(random_audio_path("motivation"))
                else:
                    variant = e.get("variant", "")
                    clip = load_audio(
                        "countdowns/last-ten-seconds-push.mp3"
                        if variant == "last-ten-seconds-push"
                        else "countdowns/5-4-3-2-1.mp3"
                    )
                block = overlay(block, clip, t)

            for be in seg["break_events"]:
                t = (seg["duration_sec"] + be["time_sec"]) * 1000
                clip = load_audio(
                    "countdowns/break-in-3-2-1.mp3"
                    if be["variant"] == "break-in-3-2-1"
                    else "countdowns/5-4-3-2-1.mp3"
                )
                block = overlay(block, clip, t)

            master += block

    return master


def export_and_upload(master, difficulty, length_min, pace):
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp_path = tmp.name

    master.export(tmp_path, format="mp3")

    filename = f"class_{difficulty}_{length_min}min_{pace}_{int(time.time())}.mp3"
    object_path = f"generated/{filename}"

    with open(tmp_path, "rb") as f:
        supabase.storage.from_("audio").upload(object_path, f, {
            "content-type": "audio/mpeg",
            "upsert": True
        })

    os.remove(tmp_path)

    return f"{SUPABASE_URL}/storage/v1/object/public/audio/{object_path}"


# ------------------------
# Supabase Job Logic (FIXED version)
# ------------------------

def create_db_job(plan):
    job_id = uuid.uuid4().hex

    result = supabase.table("jobs").insert({
        "id": job_id,
        "status": "queued",
        "plan": plan
    }).execute()

    if result.status_code >= 300:
        raise Exception(f"Insert failed: {result.status_code}")

    return job_id


def update_db_job(job_id, fields):
    supabase.table("jobs").update(fields).eq("id", job_id).execute()


def fetch_next_job():
    result = supabase.table("jobs") \
        .select("*") \
        .eq("status", "queued") \
        .order("created_at", desc=False) \
        .limit(1) \
        .execute()

    if not result.data:
        return None

    return result.data[0]


def worker_loop():
    print("[WORKER] Worker running")

    while True:
        job = fetch_next_job()
        if not job:
            time.sleep(1)
            continue

        job_id = job["id"]
        plan = job["plan"]

        update_db_job(job_id, {"status": "processing"})

        try:
            master = build_audio_from_plan(plan)
            url = export_and_upload(
                master,
                plan["difficulty"],
                plan["length_min"],
                plan["pace"]
            )
            update_db_job(job_id, {"status": "done", "file_url": url})

        except Exception as e:
            update_db_job(job_id, {"status": "error", "error": str(e)})

        time.sleep(0.2)


# Start background worker
threading.Thread(target=worker_loop, daemon=True).start()


# ------------------------
# Routes
# ------------------------

@app.route("/")
def home():
    return "Corner Backend Running"


@app.route("/generate", methods=["POST"])
def generate():
    data = request.get_json() or {}

    plan = build_class_plan(
        (data.get("difficulty") or "beginner").lower(),
        int(data.get("length") or 60),
        data.get("pace") or "Normal",
        data.get("music") or "None"
    )

    try:
        job_id = create_db_job(plan)
        return jsonify({"status": "queued", "job_id": job_id}), 202
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 400


@app.route("/job-status/<job_id>")
def job_status(job_id):
    result = supabase.table("jobs").select("*").eq("id", job_id).execute()

    if not result.data:
        return jsonify({"status": "not_found"}), 404

    return jsonify(result.data[0])


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
