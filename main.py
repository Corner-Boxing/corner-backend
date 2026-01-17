import os
import base64
import json
from flask import Flask, request, jsonify
from flask_cors import CORS
from supabase import create_client, Client

# -------------------------------------------------
# Flask + CORS
# -------------------------------------------------

app = Flask(__name__)
CORS(
    app,
    resources={r"/*": {"origins": "*"}},
    allow_headers=["Content-Type", "Authorization"],
    methods=["GET", "POST", "OPTIONS"],
)

# -------------------------------------------------
# Supabase
# -------------------------------------------------

SUPABASE_URL = os.getenv("SUPABASE_URL") or "https://lbhmfkmrluoropzfleaa.supabase.co"
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")  # REQUIRED on Render
SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET")    # REQUIRED for fast JWT verify (private sessions)

if not SUPABASE_SERVICE_KEY:
    raise RuntimeError("Missing SUPABASE_SERVICE_KEY env var on Render for corner-backend.")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

# -------------------------------------------------
# Helpers
# -------------------------------------------------

def get_bearer_token() -> str | None:
    auth = request.headers.get("Authorization") or ""
    if not auth.lower().startswith("bearer "):
        return None
    token = auth.split(" ", 1)[1].strip()
    return token or None

def get_user_id_from_bearer_fast() -> str | None:
    """
    Fast local validation (no network call) using SUPABASE_JWT_SECRET.
    Returns Supabase user_id (sub) or None.
    """
    token = get_bearer_token()
    if not token:
        return None

    if not SUPABASE_JWT_SECRET:
        return None

    try:
        import jwt  # PyJWT
        payload = jwt.decode(
            token,
            SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            options={"verify_aud": False},
        )
        return payload.get("sub")
    except Exception:
        return None

def safe_job_response(job: dict, is_public: bool, class_mode: str | None = None):
    return {
        "job_id": job.get("id"),
        "status": job.get("status"),
        "file_url": job.get("file_url"),
        "error": job.get("error"),
        "is_public": bool(is_public),
        "class_mode": class_mode or None,
    }

def _extract_signed_url(resp: dict | None) -> str | None:
    if not resp:
        return None
    # Supabase libs sometimes return different key casing
    for k in ("signedURL", "signedUrl", "signed_url"):
        if isinstance(resp.get(k), str) and resp.get(k):
            return resp[k]
    data = resp.get("data")
    if isinstance(data, dict):
        for k in ("signedURL", "signedUrl", "signed_url"):
            if isinstance(data.get(k), str) and data.get(k):
                return data[k]
    return None


# -------------------------------------------------
# Routes
# -------------------------------------------------

@app.route("/")
def home():
    return "Corner Backend OK"

@app.route("/generate", methods=["POST"])
def deprecated_generate():
    return jsonify({
        "status": "error",
        "error": "This service does not handle /generate. Use the Corner API service.",
    }), 400

@app.route("/job-status/<job_id>", methods=["GET"])
def job_status(job_id):
    """
    Source of truth:
      - jobs: status/file_url/error
      - class_sessions: is_public + user_id (ownership)

    Public sessions -> readable by anyone.
    Private sessions -> require Bearer JWT and must match class_sessions.user_id.
    Guest sessions with is_public=false will be 401 (expected until you flip them to public).
    """
    try:
        # 1) Fetch job from jobs table (ONLY columns that actually exist)
        job_res = (
            supabase.table("jobs")
            .select("id,status,file_url,error,storage_path")
            .eq("id", job_id)
            .limit(1)
            .execute()
        )

        if not job_res.data:
            return jsonify({"status": "not_found"}), 404

        job = job_res.data[0]

        # 2) Fetch session visibility + owner from class_sessions
        sess_res = (
            supabase.table("class_sessions")
            .select("is_public,user_id,class_mode")
            .eq("job_id", job_id)
            .limit(1)
            .execute()
        )
        sess = sess_res.data[0] if sess_res.data else None

        # If there's no class_sessions row (edge case), default to public READ of status only.
        # (This avoids breaking older jobs and prevents 500 spam.)
        if not sess:
            return jsonify(safe_job_response(job, True, class_mode)), 200

        is_public = bool(sess.get("is_public"))
        owner_id = sess.get("user_id")  # may be null for guests
        class_mode = sess.get("class_mode")

        # 3) Public sessions are always readable.
        # If the job is done and we have storage_path, return a signed URL (bucket is private).
        if is_public:
            if (job.get("status") == "done") and (job.get("storage_path") or ""):
                signed = supabase.storage.from_("audio").create_signed_url(job["storage_path"], 60 * 10)
                url = _extract_signed_url(signed)
                if url:
                    job = dict(job)
                    job["file_url"] = url
            return jsonify(safe_job_response(job, True, class_mode)), 200


        # 4) Private session -> require auth and ownership
        uid = get_user_id_from_bearer_fast()
        if not uid:
            return jsonify({"status": "error", "error": "Unauthorized"}), 401

        if not owner_id or uid != owner_id:
            return jsonify({"status": "error", "error": "Forbidden"}), 403

        # Owner can read; if done, attach a signed URL
        if (job.get("status") == "done") and (job.get("storage_path") or ""):
            signed = supabase.storage.from_("audio").create_signed_url(job["storage_path"], 60 * 10)
            url = _extract_signed_url(signed)
            if url:
                job = dict(job)
                job["file_url"] = url

        return jsonify(safe_job_response(job, False, class_mode)), 200


    except Exception as e:
        return jsonify({
            "status": "error",
            "error": "Internal server error",
            "details": str(e),
        }), 500

@app.route("/signed-url/<job_id>", methods=["GET"])
def signed_url(job_id):
    """
    Returns a short-lived signed URL for the audio file.
    Requires auth + ownership via class_sessions.user_id.
    """
    try:
        uid = get_user_id_from_bearer_fast()
        if not uid:
            return jsonify({"status": "error", "error": "Unauthorized"}), 401

        sess = (
            supabase.table("class_sessions")
            .select("user_id, storage_path, job_id")
            .eq("job_id", job_id)
            .limit(1)
            .execute()
        )

        if not sess.data:
            return jsonify({"status": "error", "error": "Not found"}), 404

        row = sess.data[0]
        if row.get("user_id") != uid:
            return jsonify({"status": "error", "error": "Forbidden"}), 403

        storage_path = row.get("storage_path") or f"classes/{job_id}.mp3"

        signed = supabase.storage.from_("audio").create_signed_url(storage_path, 60 * 10)
        url = _extract_signed_url(signed)

        if not url:
            return jsonify({"status": "error", "error": "Could not sign url", "details": signed}), 500

        return jsonify({"status": "ok", "url": url}), 200


    except Exception as e:
        return jsonify({
            "status": "error",
            "error": "Internal server error",
            "details": str(e),
        }), 500

@app.route("/_health")
def _health():
    return jsonify({
        "ok": True,
        "supabase_url_set": bool(SUPABASE_URL),
        "service_key_set": bool(SUPABASE_SERVICE_KEY),
        "jwt_secret_set": bool(SUPABASE_JWT_SECRET),
    }), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
