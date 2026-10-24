import json
import logging
import os
import threading

from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

WAL_FILE = "wal.log"
SNAPSHOT_FILE = "snapshot.json"
SNAPSHOT_TMP = "snapshot.json.tmp"
OPS_LOG_FILE = "ops.log"
COMPACT_AFTER = 10000

# Set FSYNC_EVERY_WRITE=0 to skip fsync (faster, but a power cut can lose writes).
FSYNC_EVERY_WRITE = os.environ.get("FSYNC_EVERY_WRITE", "1") != "0"

# ops.log is for monitoring (what requests came in and how they went).
# wal.log is for recovery (the data needed to rebuild the store). They are
# separate files on purpose: one is for humans, the other is for the server.
ops_logger = logging.getLogger("ops")
ops_logger.setLevel(logging.INFO)
_handler = logging.FileHandler(OPS_LOG_FILE, encoding="utf-8")
_handler.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
ops_logger.addHandler(_handler)

app = Flask(__name__)

# The only shared state: the dictionary, the lock, and the log handle
# (plus a count of log entries so we know when to compact).
#
# One plain Lock guards the dictionary AND the log file together. With a
# single lock there is no lock ordering to get wrong, so deadlock can't happen.
#
# Concurrent writes to the same key are last writer wins: whichever thread
# gets the lock last decides the final value.
store = {}
lock = threading.Lock()
wal = None
wal_entries = 0


def log_op(op, key, outcome):
    ops_logger.info("%s | %s | %s", op, key, outcome)


# ---------- persistence ----------

def load_snapshot():
    if os.path.exists(SNAPSHOT_FILE):
        with open(SNAPSHOT_FILE, encoding="utf-8") as f:
            store.update(json.load(f))


def replay_wal():
    """Rebuild the dictionary from wal.log. Returns how many entries were applied."""
    if not os.path.exists(WAL_FILE):
        return 0
    applied = 0
    with open(WAL_FILE, encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line)
                if entry["op"] == "PUT":
                    store[entry["key"]] = entry["value"]
                elif entry["op"] == "DEL":
                    store.pop(entry["key"], None)
                else:
                    continue
            except (ValueError, KeyError, TypeError):
                # A crash mid-write leaves a half line at the end. Skip it.
                continue
            applied += 1
    return applied


def append_to_wal(entry):
    # Called with the lock held. The record must be on disk before we touch
    # the dictionary or reply, otherwise a crash could lose a write we
    # already told the client succeeded.
    wal.write(json.dumps(entry) + "\n")
    wal.flush()
    if FSYNC_EVERY_WRITE:
        os.fsync(wal.fileno())


def compact_wal():
    # Called with the lock held so no write can slip in halfway through.
    #
    # Crash safety: the snapshot is written to a temp file and swapped in with
    # os.replace, which is atomic, so snapshot.json is always complete. If we
    # crash after the replace but before the truncate, the next start loads the
    # snapshot and then replays the old log on top of it. That is harmless
    # because PUT and DEL are idempotent: replaying them in order against a
    # state that already includes them gives the same final state.
    global wal_entries
    with open(SNAPSHOT_TMP, "w", encoding="utf-8") as f:
        json.dump(store, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(SNAPSHOT_TMP, SNAPSHOT_FILE)
    wal.truncate(0)
    wal.flush()
    os.fsync(wal.fileno())
    wal_entries = 0


def maybe_compact():
    if wal_entries > COMPACT_AFTER:
        try:
            compact_wal()
        except OSError:
            # The write that triggered this is already safely in the log, so
            # don't fail the request. We'll just try again on a later write.
            app.logger.exception("compaction failed")


def start_store():
    global wal, wal_entries
    load_snapshot()
    wal_entries = replay_wal()
    wal = open(WAL_FILE, "a", encoding="utf-8")


# ---------- request handling ----------

def parse_value():
    """Return (ok, value). Done before taking the lock to keep it short."""
    data = request.get_json(silent=True, force=True)
    if not isinstance(data, dict) or "value" not in data:
        return False, None
    return True, data["value"]


@app.route("/<key>", methods=["PUT", "POST"])
def put_key(key):
    ok, value = parse_value()
    if not ok:
        log_op("PUT", key, "bad_request")
        return jsonify(error="bad request"), 400

    global wal_entries
    with lock:
        existed = key in store
        try:
            append_to_wal({"op": "PUT", "key": key, "value": value})
        except OSError:
            failed = True
        else:
            failed = False
            store[key] = value
            wal_entries += 1
            maybe_compact()

    if failed:
        log_op("PUT", key, "log_write_failed")
        return jsonify(error="internal error"), 500
    outcome = "overwritten" if existed else "created"
    log_op("PUT", key, outcome)
    return jsonify(value=value), (200 if existed else 201)


@app.route("/<key>", methods=["GET"])
def get_key(key):
    with lock:
        found = key in store
        value = store.get(key)

    if not found:
        log_op("GET", key, "not_found")
        return jsonify(error="key not found"), 404
    log_op("GET", key, "ok")
    return jsonify(value=value), 200


@app.route("/<key>", methods=["DELETE"])
def delete_key(key):
    global wal_entries
    with lock:
        found = key in store
        failed = False
        if found:
            try:
                append_to_wal({"op": "DEL", "key": key})
            except OSError:
                failed = True
            else:
                del store[key]
                wal_entries += 1
                maybe_compact()

    if not found:
        # Nothing changed, so nothing goes in the log.
        log_op("DEL", key, "not_found")
        return jsonify(error="key not found"), 404
    if failed:
        log_op("DEL", key, "log_write_failed")
        return jsonify(error="internal error"), 500
    log_op("DEL", key, "deleted")
    return jsonify(status="deleted"), 200


@app.errorhandler(Exception)
def handle_any_error(err):
    # Catch-all so the client always gets JSON, never an HTML error page.
    if isinstance(err, HTTPException):
        return jsonify(error=err.name.lower()), err.code
    app.logger.exception("unexpected error")
    log_op(request.method, request.path.lstrip("/"), "internal_error")
    return jsonify(error="internal error"), 500


if __name__ == "__main__":
    start_store()
    app.run(host="127.0.0.1", port=8080, threaded=True)
