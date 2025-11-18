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

# Local audio directory in your repo
BASE_AUDIO_DIR = os.path.join(os.path.dirname(__file__), "audio")


# ------------------------
# Helpers – file loading
# ------------------------

def load_audio(rel_path: str) -> AudioSegment:
    full_path = os.path.join(BASE_AUDIO_DIR, rel_path)
    if not os.path.isfile(full_path):
        raise FileNotFoundError(f"Audio file not found: {full_path}")
    return AudioSegment.from_file(full_path)


def random_audio_path(subdir: str) -> str:
    dir_path = os.path.join(BASE_AUDIO_DIR, subdir)
    files = [f for f in os.listdir(dir_path) if f.lower().endswith(".mp3")]
    if not files:
        raise RuntimeError(f"No mp3 files found in {dir_path}")
    filename = random.choice(files)
    return os.path.join(subdir, filename)


def overlay(base: AudioSegment, clip: AudioSegment, start_ms: int) -> AudioSegment:
    if start_ms < 0:
        start_ms = 0
    if start_ms > len(base):
        base = base + AudioSegment.silent(duration=start_ms - len(base))
    return base.overlay(clip, position=start_ms)


# ------------------------
# Class plan generation
# ------------------------

def compute_num_rounds(length_min: int) -> int:
    non_round = 11
    usable = max(0, length_min - non_round)
    num_rounds = max(1, int(usable // 3.5))
    return num_rounds


def build_round_segment(round_number: int, difficulty: str, pace: str) -> dict:
    duration_sec = 180
    break_duration_sec = 30

    spacing = 15
    if pace.lower() == "fast":
        spacing = 12
    elif pace.lower() == "slow":
        spacing = 18

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

    coach_candidates = list(range(9, 160, spacing))
    random.shuffle(coach_candidates)
    coach_candidates = coach_candidates[:4]

    for ts in coach_candidates:
        if any(abs(ts - c) < 4 for c in combo_times):
            continue
        if random.random() < 0.5:
            events.append({"event_type": "tip", "time_sec": ts})
        else:
            events.append({"event_type": "motivation", "time_sec": ts})

    events.append({
        "event_type": "countdown",
        "time_sec": duration_sec - 10,
        "variant": "last-ten-seconds-push"
    })

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

    segments.append({"type": "intro", "file": "intro_outro/intro.mp3"})
    segments.append({"type": "warmup", "file": "warmup/warmup.mp3", "duration_sec": 300})

    for r in range(1, num_rounds + 1):
        segments.append(build_round_segment(r, difficulty, pace))

    segments.append({"type": "core", "file": "core/core.mp3", "duration_sec": 300})
    segments.append({"type": "cooldown", "file": "cooldown/cooldown.mp3", "duration_sec": 60})
    segments.append({"type": "outro", "file": "intro_outro/outro.mp3"})

    return {
        "difficulty": difficulty,
        "length_min": length_min,
        "music": music,
        "pace": pace,
        "num_rounds": num_rounds,
        "segments": segments
    }


# ------------------------
# Audio assembly
# ------------------------

def build_audio_from_plan(plan: dict) -> AudioSegment:
    master = AudioSegment.silent(duration=0)

    for seg in plan["segments"]:
        stype = seg["type"]

        if stype in ("intro", "outro", "warmup", "core", "cooldown"):
            master += load_audio(seg["file"])
            continue

        if stype == "round":
            round_len_total = (seg["duration_sec"] + seg["break_duration_sec"]) * 1000
            block = AudioSegment.silent(duration=round_len_total)

            block = overlay(block, load_audio(seg["start_file"]), 0)
            block = overlay(block, load_audio(seg["round_callout_file"]), 2000)

            end_clip = load_audio(seg["end_file"])
            end_pos_ms = max((seg["duration_sec"] - 4) * 1000, 0)
            block = overlay(block, end_clip, end_pos_ms)

            for e in seg.get("events", []):
                t_ms = int(e["time_sec"] * 1000)
                et = e["event_type"]

                if et == "combo":
                    clip = load_audio(random_audio_path(e["difficulty"]))
                elif et == "tip":
                    clip = load_audio(random_audio_path("tips"))
                elif et == "motivation":
                    clip = load_audio(random_audio_path("motivation"))
                elif et == "countdown":
                    variant = e.get("variant", "")
                    rel = ("countdowns/last-ten-seconds-push.mp3"
                           if variant == "last-ten-seconds-push"
                           else "countdowns/5-4-3-2-1.mp3")
                    clip = load_audio(rel)
                else:
                    continue

                block = overlay(block, clip, t_ms)

            for be in seg.get("break_events", []):
                if be["event_type"] != "countdown":
                    continue

                rel_path = ("countdowns/break-in-3-2-1.mp3"
                            if be.get("variant") == "break-in-3-2-1"
                            else "countdowns/5-4-3-2-1.mp3")

                t_ms = int((seg["duration_sec"] + be["time_sec"]) * 1000)
                block = overlay(block, load_audio(rel_path), t_ms)

            master += block

    return master


def export_and_upload(master, difficulty, length_min, pace):
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp_path = tmp.name

    master.export(tmp_path, format="mp3")

    timestamp = int(time.time())
    filename = f"class_{difficulty}_{length_min}min_{pace}_{timestamp}.mp3"
    object_path = f"generated/{filename}"

    with open(tmp_path, "rb") as f:
        res = supabase.storage.from_("audio").upload(
            object_path,
            f,
            {"content-type": "audio/mpeg", "upsert": True}
        )

    os.remove(tmp_path)

    return f"{SUPABASE_URL}/storage/v1/object/public/audio/{object_path}"


# ------------------------
# Supabase job system (WRONG TABLE FIXED)
# ------------------------

def create_db_job(plan):
    job_id = uuid.uuid4().hex

    insert_data = {
        "id": job_id,
        "status": "queued",
        "plan": plan,
    }

    result = supabase.table("jobs").insert(insert_data).execute()

    if result.error:
        raise Exception(f"Failed to insert job: {result.error}")

    return job_id


def update_db_job(job_id, fields):
    result = supabase.table("jobs").update(fields).eq("id", job_id).execute()
    if result.error:
        print("Error updating job:", result.error)


def fetch_next_job():
    result = (
        supabase.table("jobs")
        .select("*")
        .eq("status", "queued")
        .order("created_at", desc=False)
        .limit(1)
        .execute()
    )

    if result.error:
        print("Job fetch error:", result.error)
        return None

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
            difficulty = plan["difficulty"]
            length_min = plan["length_min"]
            pace = plan["pace"]

            master = build_audio_from_plan(plan)
            url = export_and_upload(master, difficulty, length_min, pace)

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

    difficulty = (data.get("difficulty") or "beginner").lower()
    length_min = int(data.get("length") or 60)
    pace = data.get("pace") or "Normal"
    music = data.get("music") or "None"

    plan = build_class_plan(difficulty, length_min, pace, music)

    try:
        job_id = create_db_job(plan)
        return jsonify({"status": "queued", "job_id": job_id}), 202
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 400


@app.route("/job-status/<job_id>")
def job_status(job_id):
    result = supabase.table("jobs").select("*").eq("id", job_id).limit(1).execute()

    if result.error:
        return jsonify({"status": "error", "error": str(result.error)}), 400

    if not result.data:
        return jsonify({"status": "not_found"}), 404

    return jsonify(result.data[0])


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
