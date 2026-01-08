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
SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET")    # REQUIRED for fast JWT verify (private jobs)

if not SUPABASE_SERVICE_KEY:
    raise RuntimeError("Missing SUPABASE_SERVICE_KEY env var on Render for corner-backend.")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

# -------------------------------------------------
# Helpers
# -------------------------------------------------

def jwt_claims_no_verify(token: str) -> dict:
    """
    Decode JWT payload WITHOUT verifying signature.
    Only used if you want a quick look at claims (non-security).
    """
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = base64.urlsafe_b64decode(payload_b64.encode("utf-8")).decode("utf-8")
        return json.loads(payload)
    except Exception:
        return {}

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
        # Fail closed: if you didn't set the secret, treat as unauthorized
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

def safe_job_response(job: dict, is_public: bool):
    """
    Only return the minimal fields frontend needs.
    (Avoid returning plan, storage_path, etc.)
    """
    return {
        "job_id": job.get("id"),
        "status": job.get("status"),
        "file_url": job.get("file_url"),
        "error": job.get("error"),
        "is_public": bool(is_public),
    }

# -------------------------------------------------
# Routes
# -------------------------------------------------

@app.route("/")
def home():
    return "Corner Backend OK"

@app.route("/generate", methods=["POST"])
def deprecated_generate():
    # Frontend should NOT call this anymore.
    return jsonify({
        "status": "error",
        "error": "This service does not handle /generate. Use the Corner API service.",
    }), 400

@app.route("/job-status/<job_id>", methods=["GET"])
def job_status(job_id):
    """
    Public jobs: readable by anyone.
    Private jobs: require Bearer JWT and must match jobs.user_id.
    """
    try:
        result = (
            supabase.table("jobs")
            .select("id,status,file_url,error,user_id")
            .eq("id", job_id)
            .limit(1)
            .execute()
        )

        if not result.data:
            return jsonify({"status": "not_found"}), 404

        job = result.data[0]
        is_public = bool(job.get("is_public"))

        # Look up session visibility (is_public lives on class_sessions, not jobs)
        sess_res = (
            supabase.table("class_sessions")
            .select("is_public,user_id")
            .eq("job_id", job_id)
            .limit(1)
            .execute()
        )

        sess = sess_res.data[0] if sess_res.data else {}
        is_public = bool(sess.get("is_public"))
        owner_id = sess.get("user_id")  # session owner (may be null for guests)


        # Public sessions are always readable
        if is_public:
            return jsonify({
                "job_id": job["id"],
                "status": job["status"],
                "file_url": job["file_url"],
                "error": job["error"],
                "is_public": True,
            }), 200


        # Private session → require auth
        uid = get_user_id_from_bearer()
        if not uid:
            return jsonify({"status": "error", "error": "Unauthorized"}), 401

        if uid != owner_id:
            return jsonify({"status": "error", "error": "Forbidden"}), 403


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
    Requires auth and ownership (via class_sessions.user_id).
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

        # supabase-py returns dict-like
        if not signed or not signed.get("signedURL"):
            return jsonify({"status": "error", "error": "Could not sign url", "details": signed}), 500

        return jsonify({"status": "ok", "url": signed["signedURL"]}), 200

    except Exception as e:
        return jsonify({
            "status": "error",
            "error": "Internal server error",
            "details": str(e),
        }), 500

# Optional quick sanity endpoint (keeps you from re-opening the idna rabbit hole)
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
