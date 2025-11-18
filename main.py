# main.py  — CLEAN LONG-TERM VERSION
import os
import random
import tempfile
import time

from flask import Flask, request, jsonify
from flask_cors import CORS
from supabase import create_client, Client
from pydub import AudioSegment
import requests

app = Flask(__name__)
CORS(app)

# ------------------------
# Supabase setup
# ------------------------
SUPABASE_URL = "https://lbhmfkmrluoropzfleaa.supabase.co"
SUPABASE_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImxiaG1m"
    "a21ybHVvcm9wemZsZWFhIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2MzIyMjAyOSwi"
    "ZXhwIjoyMDc4Nzk4MDI5fQ.Bmqu3Y9Woe4JPVO9bNviXN9ePJWc0LeIsItLjUT2mgQ"
)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Local audio directory
BASE_AUDIO_DIR = os.path.join(os.path.dirname(__file__), "audio")


# ------------------------
# Audio file loading helpers
# ------------------------
def load_audio(rel_path: str) -> AudioSegment:
    full_path = os.path.join(BASE_AUDIO_DIR, rel_path)
    if not os.path.isfile(full_path):
        raise FileNotFoundError(f"Audio file not found: {full_path}")
    return AudioSegment.from_file(full_path)


def random_audio_path(subdir: str) -> str:
    dir_path = os.path.join(BASE_AUDIO_DIR, subdir)
    if not os.path.isdir(dir_path):
        raise RuntimeError(f"Directory does not exist: {dir_path}")

    files = [f for f in os.listdir(dir_path) if f.lower().endswith(".mp3")]
    if not files:
        raise RuntimeError(f"No .mp3 files found in: {dir_path}")

    return os.path.join(subdir, random.choice(files))


def overlay(base: AudioSegment, clip: AudioSegment, start_ms: int) -> AudioSegment:
    if start_ms < 0:
        start_ms = 0
    if start_ms > len(base):
        base += AudioSegment.silent(duration=(start_ms - len(base)))
    return base.overlay(clip, position=start_ms)


# ------------------------
# Plan generation
# ------------------------
def compute_num_rounds(length_min: int) -> int:
    non_round = 11   # warmup+core+cooldown
    usable = max(0, length_min - non_round)
    return max(1, int(usable // 3.5))


def build_round_segment(round_number: int, difficulty: str, pace: str) -> dict:
    duration_sec = 180
    break_duration_sec = 30

    # Combo pacing
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

    events = [{"event_type": "combo", "time_sec": ct, "difficulty": difficulty}
              for ct in combo_times]

    # Tips/motivation
    coach_slots = list(range(9, 160, spacing))
    random.shuffle(coach_slots)
    coach_slots = coach_slots[:4]

    for ts in coach_slots:
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
        "segments": segments,
    }


# ------------------------
# Audio Assembly
# ------------------------
def build_audio_from_plan(plan: dict) -> AudioSegment:
    master = AudioSegment.silent(duration=0)

    for seg in plan["segments"]:
        t = seg["type"]

        if t in ("intro", "warmup", "core", "cooldown", "outro"):
            master += load_audio(seg["file"])
            continue

        if t == "round":
            round_ms = (seg["duration_sec"] + seg["break_duration_sec"]) * 1000
            block = AudioSegment.silent(duration=round_ms)

            block = overlay(block, load_audio(seg["start_file"]), 0)
            block = overlay(block, load_audio(seg["round_callout_file"]), 2000)

            end_call_ms = max((seg["duration_sec"] - 4) * 1000, 0)
            block = overlay(block, load_audio(seg["end_file"]), end_call_ms)

            for e in seg["events"]:
                pos = int(e["time_sec"] * 1000)
                if e["event_type"] == "combo":
                    clip = load_audio(random_audio_path(e["difficulty"]))
                elif e["event_type"] == "tip":
                    clip = load_audio(random_audio_path("tips"))
                elif e["event_type"] == "motivation":
                    clip = load_audio(random_audio_path("motivation"))
                elif e["event_type"] == "countdown":
                    if e.get("variant") == "last-ten-seconds-push":
                        clip = load_audio("countdowns/last-ten-seconds-push.mp3")
                    else:
                        clip = load_audio("countdowns/5-4-3-2-1.mp3")
                else:
                    continue
                block = overlay(block, clip, pos)

            for be in seg["break_events"]:
                if be["event_type"] == "countdown":
                    pos = int((seg["duration_sec"] + be["time_sec"]) * 1000)
                    clip = load_audio("countdowns/break-in-3-2-1.mp3")
                    block = overlay(block, clip, pos)

            master += block

    return master


def export_and_upload(master: AudioSegment, difficulty: str, length_min: int, pace: str) -> str:
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp_path = tmp.name

    master.export(tmp_path, format="mp3")

    filename = f"class_{difficulty}_{length_min}min_{pace}_{int(time.time())}.mp3"
    object_path = f"generated/{filename}"

    with open(tmp_path, "rb") as f:
        supabase.storage.from_("audio").upload(
            object_path,
            f,
            {"content-type": "audio/mpeg", "upsert": True}
        )

    os.remove(tmp_path)

    return f"{SUPABASE_URL}/storage/v1/object/public/audio/{object_path}"


# ------------------------
# REAL Queue System – DB backed
# ------------------------

@app.route("/schedule-job", methods=["POST"])
def schedule_job():
    data = request.get_json() or {}

    difficulty = (data.get("difficulty") or "beginner").lower()
    length = int(data.get("length") or 60)
    pace = data.get("pace") or "Normal"
    music = data.get("music") or "None"

    try:
        result = supabase.table("jobs").insert({
            "difficulty": difficulty,
            "length_min": length,
            "pace": pace,
            "music": music,
            "status": "pending"
        }).execute()

        job = result.data[0]
        return jsonify({"status": "queued", "job_id": job["id"]})

    except Exception as e:
        print("ERROR scheduling job:", repr(e))
        return jsonify({"status": "failed", "message": str(e)}), 500


@app.route("/process-job/<int:job_id>", methods=["POST"])
def process_job(job_id):
    try:
        result = supabase.table("jobs").select("*").eq("id", job_id).single().execute()
    except Exception:
        return jsonify({"status": "error", "message": "Job not found"}), 404

    job = result.data

    if job["status"] not in ("pending", "processing"):
        return jsonify({"status": "skipped", "message": "Job already processed"})

    # Mark processing
    supabase.table("jobs").update({"status": "processing"}).eq("id", job_id).execute()

    try:
        plan = build_class_plan(job["difficulty"], job["length_min"], job["pace"], job["music"])
        master = build_audio_from_plan(plan)
        url = export_and_upload(master, job["difficulty"], job["length_min"], job["pace"])

        supabase.table("jobs").update({
            "status": "done",
            "file_url": url
        }).eq("id", job_id).execute()

        return jsonify({"status": "done", "file_url": url})

    except Exception as e:
        supabase.table("jobs").update({
            "status": "error",
            "error": str(e)
        }).eq("id", job_id).execute()

        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/job-status/<int:job_id>", methods=["GET"])
def job_status(job_id):
    result = supabase.table("jobs").select("*").eq("id", job_id).single().execute()
    if not result.data:
        return jsonify({"status": "not_found"}), 404
    return jsonify(result.data)


@app.route("/")
def home():
    return "Corner Backend Running"
