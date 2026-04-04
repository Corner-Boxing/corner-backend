import os
import base64
import json
import logging
import requests
from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS
from supabase import create_client, Client

# -------------------------------------------------
# Flask + CORS
# -------------------------------------------------

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("corner-backend")
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

REST_HEADERS = {
    "apikey": SUPABASE_SERVICE_KEY,
    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    "Content-Type": "application/json",
}

def rest_get(path: str, params: dict | None = None, timeout: int = 8):
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    return requests.get(url, headers=REST_HEADERS, params=params, timeout=timeout)

def storage_object_get(path: str, timeout: int = 20, stream: bool = False):
    url = f"{SUPABASE_URL}/storage/v1/object/audio/{path}"
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    }
    return requests.get(url, headers=headers, timeout=timeout, stream=stream)

def fetch_job_row(job_id: str) -> dict | None:
    resp = rest_get(
        "jobs",
        params={
            "select": "id,status,file_url,error,storage_path",
            "id": f"eq.{job_id}",
            "limit": "1",
        },
        timeout=8,
    )
    resp.raise_for_status()
    data = resp.json()
    return data[0] if data else None

def fetch_session_row(job_id: str) -> dict | None:
    resp = rest_get(
        "class_sessions",
        params={
            "select": "id,job_id,user_id,is_public,class_mode,status,file_url,error,storage_path,created_at,completed_at",
            "job_id": f"eq.{job_id}",
            "limit": "1",
        },
        timeout=8,
    )
    resp.raise_for_status()
    data = resp.json()
    return data[0] if data else None

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

def _extract_signed_url(resp) -> str | None:
    """
    Be tolerant of supabase-py response shape differences.
    Supports:
    - plain dicts
    - nested dicts under .data / ["data"]
    - response objects with attributes
    - pydantic-ish objects with model_dump()
    """
    if not resp:
        return None

    # Direct string case
    if isinstance(resp, str) and resp.strip():
        return resp.strip()

    candidates = []

    # Dict-shaped response
    if isinstance(resp, dict):
        candidates.append(resp)
        data = resp.get("data")
        if isinstance(data, dict):
            candidates.append(data)

    # Object-shaped response
    else:
        candidates.append(resp)

        data_attr = getattr(resp, "data", None)
        if data_attr is not None:
            candidates.append(data_attr)

        model_dump = getattr(resp, "model_dump", None)
        if callable(model_dump):
            try:
                dumped = model_dump()
                if isinstance(dumped, dict):
                    candidates.append(dumped)
                    dumped_data = dumped.get("data")
                    if dumped_data is not None:
                        candidates.append(dumped_data)
            except Exception:
                pass

    for obj in candidates:
        if obj is None:
            continue

        for key in ("signedURL", "signedUrl", "signed_url", "url"):
            value = None

            if isinstance(obj, dict):
                value = obj.get(key)
            else:
                value = getattr(obj, key, None)

            if isinstance(value, str) and value.strip():
                return value.strip()

    return None

def safe_job_response(
    job: dict,
    is_public: bool,
    class_mode: str | None = None,
    signed_url: str | None = None,
):
    return {
        "job_id": job.get("id"),
        "status": job.get("status"),
        "file_url": signed_url or job.get("file_url"),
        "error": job.get("error"),
        "is_public": bool(is_public),
        "class_mode": class_mode or None,
        "storage_path": job.get("storage_path"),
    }

def _sign_storage_path(storage_path: str, ttl_seconds: int = 60 * 10) -> tuple[str | None, dict | None, str | None]:
    """
    Returns: (url, raw_response_dict_or_repr, error_string)
    Never raises.
    """
    if not storage_path:
        return None, None, "missing_storage_path"

    try:
        raw = supabase.storage.from_("audio").create_signed_url(storage_path, ttl_seconds)
        url = _extract_signed_url(raw)

        raw_debug = None
        if isinstance(raw, dict):
            raw_debug = raw
        else:
            try:
                model_dump = getattr(raw, "model_dump", None)
                if callable(model_dump):
                    dumped = model_dump()
                    raw_debug = dumped if isinstance(dumped, dict) else {"repr": repr(raw)}
                else:
                    raw_debug = {"repr": repr(raw), "type": type(raw).__name__}
            except Exception:
                raw_debug = {"repr": repr(raw), "type": type(raw).__name__}

        if url:
            return url, raw_debug, None

        return None, raw_debug, "signed_url_missing_in_response"

    except Exception as e:
        return None, {"exception_type": type(e).__name__}, str(e)

def _build_effective_job(job: dict, sess: dict | None) -> dict:
    if not sess:
        return {
            "id": job.get("id"),
            "status": job.get("status"),
            "error": job.get("error"),
            "file_url": job.get("file_url"),
            "storage_path": job.get("storage_path"),
        }

    return {
        "id": job.get("id"),
        "status": job.get("status") or sess.get("status"),
        "error": job.get("error") or sess.get("error"),
        "file_url": job.get("file_url") or sess.get("file_url"),
        "storage_path": job.get("storage_path") or sess.get("storage_path"),
    }

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
    Fast status route with hard timeouts on Supabase reads.
    Private sessions still require auth + ownership.
    """
    try:
        job = fetch_job_row(job_id)
        if not job:
            return jsonify({"status": "not_found"}), 404

        sess = fetch_session_row(job_id)

        class_mode = sess.get("class_mode") if sess else None
        is_public = bool(sess.get("is_public")) if sess else True
        owner_id = sess.get("user_id") if sess else None

        effective_job = _build_effective_job(job, sess)

        logger.info(
            "[job-status] job_id=%s status=%s storage_path=%s file_url_present=%s is_public=%s owner_id=%s",
            job_id,
            effective_job.get("status"),
            effective_job.get("storage_path"),
            bool(effective_job.get("file_url")),
            is_public,
            owner_id,
        )

        if not sess:
            return jsonify(safe_job_response(effective_job, True, class_mode)), 200

        if is_public:
            file_url = effective_job.get("file_url")
            if not file_url and effective_job.get("status") == "done" and effective_job.get("storage_path"):
                file_url = f"{request.host_url.rstrip('/')}/download/{job_id}"
            return jsonify(safe_job_response(effective_job, True, class_mode, signed_url=file_url)), 200

        uid = get_user_id_from_bearer_fast()
        if not uid:
            return jsonify({"status": "error", "error": "Unauthorized"}), 401

        if not owner_id or uid != owner_id:
            return jsonify({"status": "error", "error": "Forbidden"}), 403

        file_url = effective_job.get("file_url")
        if not file_url and effective_job.get("status") == "done" and effective_job.get("storage_path"):
            file_url = f"{request.host_url.rstrip('/')}/download/{job_id}"

        return jsonify(safe_job_response(effective_job, False, class_mode, signed_url=file_url)), 200

    except requests.Timeout:
        logger.exception("[job-status] timeout job_id=%s", job_id)
        return jsonify({
            "status": "error",
            "error": "Supabase timeout",
        }), 504
    except Exception as e:
        logger.exception("[job-status] fatal job_id=%s", job_id)
        return jsonify({
            "status": "error",
            "error": "Internal server error",
            "details": str(e),
        }), 500

@app.route("/signed-url/<job_id>", methods=["GET"])
def signed_url(job_id):
    """
    Returns a stable backend proxy URL instead of asking Supabase to sign on demand.
    Requires auth + ownership.
    """
    try:
        uid = get_user_id_from_bearer_fast()
        if not uid:
            return jsonify({"status": "error", "error": "Unauthorized"}), 401

        sess = fetch_session_row(job_id)
        if not sess:
            return jsonify({"status": "error", "error": "Not found"}), 404

        if sess.get("user_id") != uid:
            return jsonify({"status": "error", "error": "Forbidden"}), 403

        storage_path = sess.get("storage_path")
        session_status = sess.get("status")
        existing_file_url = sess.get("file_url")

        if not storage_path:
            job = fetch_job_row(job_id)
            if job:
                storage_path = job.get("storage_path")
                session_status = session_status or job.get("status")
                existing_file_url = existing_file_url or job.get("file_url")

        logger.info(
            "[signed-url] job_id=%s uid=%s status=%s storage_path=%s existing_file_url_present=%s",
            job_id,
            uid,
            session_status,
            storage_path,
            bool(existing_file_url),
        )

        if existing_file_url:
            return jsonify({"status": "ok", "url": existing_file_url, "source": "existing_file_url"}), 200

        if not storage_path:
            return jsonify({
                "status": "error",
                "error": "storage_path_missing",
                "job_id": job_id,
            }), 409

        proxy_url = f"{request.host_url.rstrip('/')}/download/{job_id}"

        return jsonify({
            "status": "ok",
            "url": proxy_url,
            "job_id": job_id,
            "storage_path": storage_path,
        }), 200

    except requests.Timeout:
        logger.exception("[signed-url] timeout job_id=%s", job_id)
        return jsonify({
            "status": "error",
            "error": "Supabase timeout",
        }), 504
    except Exception as e:
        logger.exception("[signed-url] fatal job_id=%s", job_id)
        return jsonify({
            "status": "error",
            "error": "Internal server error",
            "details": str(e),
        }), 500

@app.route("/download/<job_id>", methods=["GET"])
def download_audio(job_id):
    """
    Authenticated backend proxy for private audio files.
    Streams the MP3 from Supabase storage.
    """
    try:
        uid = get_user_id_from_bearer_fast()
        if not uid:
            return jsonify({"status": "error", "error": "Unauthorized"}), 401

        sess = fetch_session_row(job_id)
        if not sess:
            return jsonify({"status": "error", "error": "Not found"}), 404

        if sess.get("user_id") != uid:
            return jsonify({"status": "error", "error": "Forbidden"}), 403

        storage_path = sess.get("storage_path")
        if not storage_path:
            job = fetch_job_row(job_id)
            if job:
                storage_path = job.get("storage_path")

        if not storage_path:
            return jsonify({"status": "error", "error": "storage_path_missing"}), 409

        upstream = storage_object_get(storage_path, timeout=20, stream=True)
        upstream.raise_for_status()

        def generate():
            try:
                for chunk in upstream.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        yield chunk
            finally:
                upstream.close()

        filename = os.path.basename(storage_path) or f"{job_id}.mp3"

        return Response(
            stream_with_context(generate()),
            mimetype="audio/mpeg",
            headers={
                "Content-Disposition": f'inline; filename="{filename}"',
                "Cache-Control": "no-store",
            },
        )

    except requests.Timeout:
        logger.exception("[download] timeout job_id=%s", job_id)
        return jsonify({"status": "error", "error": "Storage timeout"}), 504
    except Exception as e:
        logger.exception("[download] fatal job_id=%s", job_id)
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

@app.route("/debug-job/<job_id>", methods=["GET"])
def debug_job(job_id):
    try:
        job_res = (
            supabase.table("jobs")
            .select("id,status,file_url,error,storage_path,updated_at,created_at")
            .eq("id", job_id)
            .limit(1)
            .execute()
        )

        sess_res = (
            supabase.table("class_sessions")
            .select("id,job_id,user_id,is_public,class_mode,status,file_url,error,storage_path,created_at,completed_at")
            .eq("job_id", job_id)
            .limit(1)
            .execute()
        )

        return jsonify({
            "job": job_res.data[0] if job_res.data else None,
            "class_session": sess_res.data[0] if sess_res.data else None,
        }), 200
    except Exception as e:
        logger.exception("[debug-job] fatal job_id=%s", job_id)
        return jsonify({"status": "error", "details": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
