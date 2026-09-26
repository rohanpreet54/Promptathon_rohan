#!/usr/bin/env python3
"""
NSRH HUB — Distributed Object Storage
Local backend server (server.py)

Real local storage layer for the NSRH HUB demo.

- Uses ONLY the Python standard library (no pip installs required).
- Binds to 127.0.0.1 only (never exposed to the local network).
- Serves the NSRH HUB frontend (index.html) and a small JSON API.
- Actually saves uploaded files to disk under nsrh_storage/files/
  and tracks metadata in nsrh_storage/metadata.json.
- Independently verifies SHA-256 checksums on every upload; never
  trusts the checksum calculated by the browser alone.

Run with:
    python server.py

Then open:
    http://127.0.0.1:8000
"""

import hashlib
import json
import mimetypes
import os
import shutil
import sys
import tempfile
import threading
import time
import traceback
import uuid
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

HOST = "127.0.0.1"
PORT = 8000

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STORAGE_DIR = os.path.join(BASE_DIR, "nsrh_storage")
FILES_DIR = os.path.join(STORAGE_DIR, "files")
METADATA_PATH = os.path.join(STORAGE_DIR, "metadata.json")
INDEX_PATH = os.path.join(BASE_DIR, "index.html")

# Maximum size for a single uploaded object (512 MB). Generous for a
# local hackathon demo while still protecting the disk/server.
MAX_UPLOAD_BYTES = 512 * 1024 * 1024

UPLOAD_CHUNK_SIZE = 64 * 1024  # 64 KB read chunks while streaming uploads
DOWNLOAD_CHUNK_SIZE = 256 * 1024  # 256 KB write chunks while streaming downloads


# --------------------------------------------------------------------------
# Storage helpers
# --------------------------------------------------------------------------

def ensure_storage_layout():
    """Create nsrh_storage/, nsrh_storage/files/ and metadata.json if missing."""
    os.makedirs(FILES_DIR, exist_ok=True)
    if not os.path.isfile(METADATA_PATH):
        _atomic_write_json(METADATA_PATH, {"files": {}})


def _atomic_write_json(path, data):
    """Write JSON to disk atomically so a crash mid-write can't corrupt it."""
    directory = os.path.dirname(path)
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".metadata_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


class MetadataStore:
    """Thread-safe accessor for nsrh_storage/metadata.json."""

    def __init__(self, path):
        self._path = path
        self._lock = threading.Lock()
        self._data = {"files": {}}
        self._load()

    def _load(self):
        with self._lock:
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if not isinstance(raw, dict) or "files" not in raw or not isinstance(raw["files"], dict):
                    raw = {"files": {}}
                self._data = raw
            except (FileNotFoundError, json.JSONDecodeError):
                self._data = {"files": {}}
                _atomic_write_json(self._path, self._data)

    def _save_locked(self):
        _atomic_write_json(self._path, self._data)

    def all(self):
        with self._lock:
            records = list(self._data["files"].values())
        records.sort(key=lambda r: r.get("createdAt", 0), reverse=True)
        return records

    def get(self, file_id):
        with self._lock:
            record = self._data["files"].get(file_id)
            return dict(record) if record else None

    def add(self, record):
        with self._lock:
            self._data["files"][record["id"]] = record
            self._save_locked()
            return dict(record)

    def update(self, file_id, patch):
        with self._lock:
            record = self._data["files"].get(file_id)
            if record is None:
                return None
            record.update(patch)
            self._data["files"][file_id] = record
            self._save_locked()
            return dict(record)

    def pop(self, file_id):
        with self._lock:
            record = self._data["files"].pop(file_id, None)
            if record is not None:
                self._save_locked()
            return dict(record) if record else None


store = None  # initialized in main()


# --------------------------------------------------------------------------
# Filename / path safety helpers
# --------------------------------------------------------------------------

def sanitize_filename(raw_name):
    """
    Turn arbitrary user-supplied text into a safe display filename.

    - Strips null bytes and directory components (prevents path traversal
      such as ../../etc/passwd or absolute paths like /etc/passwd).
    - Falls back to a generic name if nothing usable remains.
    """
    if not isinstance(raw_name, str):
        return "file"
    name = raw_name.replace("\x00", "")
    name = name.replace("\\", "/")
    name = os.path.basename(name)
    name = name.strip()
    if not name or name in (".", ".."):
        name = "file"
    # Keep filenames to a sane length.
    if len(name) > 255:
        name = name[:255]
    return name


def safe_extension(filename):
    """Return a short, alphanumeric lowercase extension (with leading dot) or ''."""
    dot = filename.rfind(".")
    if dot <= 0 or dot == len(filename) - 1:
        return ""
    ext = filename[dot:]
    body = ext[1:]
    if 0 < len(body) <= 12 and all(c.isalnum() for c in body):
        return ext.lower()
    return ""


def guess_content_type(filename):
    content_type, _ = mimetypes.guess_type(filename)
    return content_type or "application/octet-stream"


def content_disposition_header(filename):
    """Build a Content-Disposition header value safe for arbitrary filenames."""
    ascii_fallback = filename.encode("ascii", "ignore").decode("ascii") or "download"
    ascii_fallback = ascii_fallback.replace('"', "'")
    from urllib.parse import quote

    utf8_quoted = quote(filename, safe="")
    return 'attachment; filename="{}"; filename*=UTF-8\'\'{}'.format(ascii_fallback, utf8_quoted)


# --------------------------------------------------------------------------
# HTTP request handler
# --------------------------------------------------------------------------

class NsrhRequestHandler(BaseHTTPRequestHandler):
    server_version = "NSRHHubBackend/1.0"
    protocol_version = "HTTP/1.1"

    # ---- low-level response helpers ----------------------------------

    def _send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_error_json(self, status, message, **extra):
        payload = {"ok": False, "error": message}
        payload.update(extra)
        self._send_json(status, payload)

    def _send_text(self, status, text, content_type="text/plain; charset=utf-8"):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, fmt, *args):
        sys.stdout.write("[NSRH HUB] %s - %s\n" % (self.address_string(), fmt % args))

    # ---- routing --------------------------------------------------------

    def do_GET(self):
        try:
            path = urlsplit(self.path).path
            if path in ("/", "/index.html"):
                self._serve_index()
            elif path == "/api/health":
                self._api_health()
            elif path == "/api/files":
                self._api_list_files()
            elif path.startswith("/api/download/"):
                file_id = unquote(path[len("/api/download/"):])
                self._api_download(file_id)
            else:
                self._send_error_json(HTTPStatus.NOT_FOUND, "Not found")
        except Exception as exc:  # noqa: BLE001 - top level safety net
            self._handle_unexpected_error(exc)

    def do_POST(self):
        try:
            path = urlsplit(self.path).path
            if path == "/api/upload":
                self._api_upload()
            else:
                self._send_error_json(HTTPStatus.NOT_FOUND, "Not found")
        except Exception as exc:  # noqa: BLE001
            self._handle_unexpected_error(exc)

    def do_PATCH(self):
        try:
            path = urlsplit(self.path).path
            if path.startswith("/api/files/"):
                file_id = unquote(path[len("/api/files/"):])
                self._api_rename(file_id)
            else:
                self._send_error_json(HTTPStatus.NOT_FOUND, "Not found")
        except Exception as exc:  # noqa: BLE001
            self._handle_unexpected_error(exc)

    def do_DELETE(self):
        try:
            path = urlsplit(self.path).path
            if path.startswith("/api/files/"):
                file_id = unquote(path[len("/api/files/"):])
                self._api_delete(file_id)
            else:
                self._send_error_json(HTTPStatus.NOT_FOUND, "Not found")
        except Exception as exc:  # noqa: BLE001
            self._handle_unexpected_error(exc)

    def _handle_unexpected_error(self, exc):
        traceback.print_exc()
        try:
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, "Internal server error: " + str(exc))
        except Exception:
            # Headers may already have been sent (e.g. mid-download); nothing more we can do.
            pass

    # ---- static frontend --------------------------------------------------

    def _serve_index(self):
        try:
            with open(INDEX_PATH, "rb") as f:
                body = f.read()
        except FileNotFoundError:
            self._send_text(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "index.html was not found next to server.py. Make sure both files are in the same folder.",
            )
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ---- API: health / listing --------------------------------------------

    def _api_health(self):
        self._send_json(HTTPStatus.OK, {"ok": True, "service": "NSRH HUB local backend"})

    def _api_list_files(self):
        self._send_json(HTTPStatus.OK, {"ok": True, "files": store.all()})

    # ---- API: upload --------------------------------------------------------

    def _api_upload(self):
        content_length_header = self.headers.get("Content-Length")
        if content_length_header is None or not content_length_header.strip().isdigit():
            self._send_error_json(HTTPStatus.LENGTH_REQUIRED, "Missing or invalid Content-Length header")
            return

        length = int(content_length_header)
        if length <= 0:
            self._send_error_json(HTTPStatus.BAD_REQUEST, "Empty upload — no file data received")
            return

        if length > MAX_UPLOAD_BYTES:
            # Refuse without reading the body; close the connection so we
            # don't have to drain hundreds of megabytes we're going to discard.
            self.close_connection = True
            self._send_error_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "File exceeds the maximum allowed size ({} MB)".format(MAX_UPLOAD_BYTES // (1024 * 1024)),
            )
            return

        raw_filename_header = self.headers.get("X-Filename")
        checksum_header = self.headers.get("X-Checksum")

        if not raw_filename_header:
            self._drain(length)
            self._send_error_json(HTTPStatus.BAD_REQUEST, "Missing X-Filename header")
            return
        if not checksum_header:
            self._drain(length)
            self._send_error_json(HTTPStatus.BAD_REQUEST, "Missing X-Checksum header")
            return

        expected_checksum = checksum_header.strip().lower()
        if len(expected_checksum) != 64 or any(c not in "0123456789abcdef" for c in expected_checksum):
            self._drain(length)
            self._send_error_json(HTTPStatus.BAD_REQUEST, "X-Checksum must be a 64-character hex SHA-256 digest")
            return

        try:
            filename = unquote(raw_filename_header)
        except Exception:
            filename = raw_filename_header
        filename = sanitize_filename(filename)

        # Stream the body straight to a temp file on the same volume as
        # permanent storage, hashing as we go, so a failed/incomplete
        # upload never touches or corrupts an existing stored file.
        tmp_fd, tmp_path = tempfile.mkstemp(dir=FILES_DIR, prefix=".upload_", suffix=".part")
        hasher = hashlib.sha256()
        received = 0
        try:
            with os.fdopen(tmp_fd, "wb") as tmp_file:
                remaining = length
                while remaining > 0:
                    chunk = self.rfile.read(min(UPLOAD_CHUNK_SIZE, remaining))
                    if not chunk:
                        raise IOError("Connection closed before the full upload was received")
                    tmp_file.write(chunk)
                    hasher.update(chunk)
                    received += len(chunk)
                    remaining -= len(chunk)
        except Exception as exc:
            self._cleanup_temp(tmp_path)
            self._send_error_json(
                HTTPStatus.BAD_REQUEST,
                "Upload interrupted after {} of {} bytes: {}".format(received, length, exc),
            )
            return

        actual_checksum = hasher.hexdigest()
        if actual_checksum != expected_checksum:
            self._cleanup_temp(tmp_path)
            self._send_error_json(
                HTTPStatus.BAD_REQUEST,
                "SHA-256 mismatch — upload rejected. The file was not saved.",
                expected=expected_checksum,
                actual=actual_checksum,
            )
            return

        file_id = uuid.uuid4().hex
        ext = safe_extension(filename)
        stored_name = file_id + ext
        final_path = os.path.join(FILES_DIR, stored_name)

        try:
            os.replace(tmp_path, final_path)
        except OSError as exc:
            self._cleanup_temp(tmp_path)
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, "Could not finalize stored file: " + str(exc))
            return

        record = {
            "id": file_id,
            "filename": filename,
            "storedName": stored_name,
            "size": length,
            "checksum": actual_checksum,
            "contentType": guess_content_type(filename),
            "createdAt": int(time.time() * 1000),
        }
        store.add(record)
        self._send_json(HTTPStatus.CREATED, {"ok": True, "file": record})

    def _drain(self, length):
        """Consume and discard `length` bytes from the request body."""
        remaining = length
        while remaining > 0:
            chunk = self.rfile.read(min(UPLOAD_CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)

    @staticmethod
    def _cleanup_temp(tmp_path):
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    # ---- API: download --------------------------------------------------

    def _api_download(self, file_id):
        if not file_id:
            self._send_error_json(HTTPStatus.BAD_REQUEST, "Missing file id")
            return
        record = store.get(file_id)
        if record is None:
            self._send_error_json(HTTPStatus.NOT_FOUND, "No stored object with id " + file_id)
            return

        file_path = os.path.join(FILES_DIR, record["storedName"])
        if not os.path.isfile(file_path):
            self._send_error_json(HTTPStatus.NOT_FOUND, "Stored file is missing from disk on the server")
            return

        try:
            file_size = os.path.getsize(file_path)
            f = open(file_path, "rb")
        except OSError as exc:
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, "Could not open stored file: " + str(exc))
            return

        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", record.get("contentType") or "application/octet-stream")
            self.send_header("Content-Length", str(file_size))
            self.send_header("Content-Disposition", content_disposition_header(record.get("filename", "download")))
            self.end_headers()
            try:
                shutil.copyfileobj(f, self.wfile, DOWNLOAD_CHUNK_SIZE)
            except (BrokenPipeError, ConnectionResetError):
                pass
        finally:
            f.close()

    # ---- API: rename --------------------------------------------------

    def _api_rename(self, file_id):
        if not file_id:
            self._send_error_json(HTTPStatus.BAD_REQUEST, "Missing file id")
            return

        content_length_header = self.headers.get("Content-Length")
        if content_length_header is None or not content_length_header.strip().isdigit():
            self._send_error_json(HTTPStatus.LENGTH_REQUIRED, "Missing or invalid Content-Length header")
            return
        length = int(content_length_header)
        if length <= 0 or length > 1024 * 1024:
            self._drain(length)
            self._send_error_json(HTTPStatus.BAD_REQUEST, "Invalid request body size")
            return

        raw_body = self.rfile.read(length)
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_error_json(HTTPStatus.BAD_REQUEST, "Request body must be valid JSON")
            return

        if not isinstance(payload, dict) or "filename" not in payload:
            self._send_error_json(HTTPStatus.BAD_REQUEST, "JSON body must include a 'filename' field")
            return

        original_name = payload.get("filename")
        if not isinstance(original_name, str) or not original_name.strip():
            self._send_error_json(HTTPStatus.BAD_REQUEST, "Invalid filename")
            return
        new_name = sanitize_filename(original_name)
        if not new_name:
            self._send_error_json(HTTPStatus.BAD_REQUEST, "Invalid filename")
            return

        record = store.get(file_id)
        if record is None:
            self._send_error_json(HTTPStatus.NOT_FOUND, "No stored object with id " + file_id)
            return

        updated = store.update(file_id, {
            "filename": new_name,
            "contentType": guess_content_type(new_name),
        })
        self._send_json(HTTPStatus.OK, {"ok": True, "file": updated})

    # ---- API: delete --------------------------------------------------

    def _api_delete(self, file_id):
        if not file_id:
            self._send_error_json(HTTPStatus.BAD_REQUEST, "Missing file id")
            return

        record = store.pop(file_id)
        if record is None:
            self._send_error_json(HTTPStatus.NOT_FOUND, "No stored object with id " + file_id)
            return

        file_path = os.path.join(FILES_DIR, record["storedName"])
        try:
            if os.path.isfile(file_path):
                os.remove(file_path)
        except OSError:
            pass  # metadata is already gone; nothing more useful we can do

        self._send_json(HTTPStatus.OK, {"ok": True, "id": file_id})


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def _open_browser_when_ready(url, delay_seconds=1.0):
    time.sleep(delay_seconds)
    try:
        webbrowser.open(url)
    except Exception:
        pass  # not fatal — the user can open the URL manually


def main():
    global store

    ensure_storage_layout()
    store = MetadataStore(METADATA_PATH)

    try:
        httpd = ThreadingHTTPServer((HOST, PORT), NsrhRequestHandler)
    except OSError as exc:
        print("=" * 70)
        print("NSRH HUB could not start the local server on {}:{}".format(HOST, PORT))
        print("Reason: {}".format(exc))
        print("Another program may already be using that port.")
        print("Close it, or edit PORT near the top of server.py, then try again.")
        print("=" * 70)
        sys.exit(1)

    url = "http://{}:{}/".format(HOST, PORT)
    print("=" * 70)
    print(" NSRH HUB — Distributed Object Storage — local backend")
    print(" Serving:        {}".format(url))
    print(" Real storage:   {}".format(FILES_DIR))
    print(" Metadata file:  {}".format(METADATA_PATH))
    print(" Press Ctrl+C to stop the server.")
    print("=" * 70)

    threading.Thread(target=_open_browser_when_ready, args=(url,), daemon=True).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down NSRH HUB local backend...")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
