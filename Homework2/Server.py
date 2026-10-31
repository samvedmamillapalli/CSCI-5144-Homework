import os
import json
import logging
import threading
from flask import Flask, request, jsonify

app = Flask(__name__)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("../kv_store.log"),
        logging.StreamHandler()
    ]
)

SAVED_DATA_FILE = "kv_store.json"
lock = threading.Lock()
kv_store = {}

def load_data():
    global kv_store
    if os.path.exists(SAVED_DATA_FILE):
        try:
            with open(SAVED_DATA_FILE, 'r') as f:
                kv_store = json.load(f)
            logging.info("Data is loaded")
        except Exception as e:
            logging.error(f"Failed to load data file: {e}. New store is used instead")
            kv_store = {}


def save_data():
    try:
        with open(SAVED_DATA_FILE, 'w') as f:
            json.dump(kv_store, f)
    except Exception as e:
        logging.error(f"Error saving {e}")


@app.route('/<key>', methods=['POST'])
def put_value(key):
    data = request.get_json(silent=True)
    if not data or 'value' not in data:
        logging.warning(f"Invalid PUT request for key '{key}': Missing 'value'")
        return jsonify({"error": "Bad Request. JSON payload must include 'value'."}), 400
    value = data['value']
    with lock:
        kv_store[key] = value
        save_data()
    logging.info(f"PUT success - Key: {key}")
    return jsonify({"status": "success"}), 200


@app.route('/<key>', methods=['GET'])
def get_value(key):
    with lock:
        value = kv_store.get(key)
    if value is None:
        logging.warning(f"GET failed as {key} key not found")
        return jsonify({"error": f"Key '{key}' not found."}), 404
    logging.info(f"GET success - Key: {key}")
    return jsonify({"value": value}), 200


@app.route('/<key>', methods=['DELETE'])
def delete_value(key):
    with lock:
        if key in kv_store:
            del kv_store[key]
            save_data()
            existed = True
        else:
            existed = False
    if not existed:
        logging.warning(f"Delete failed as {key} key not found")
        return jsonify({"error": f"Key '{key}' not found."}), 404
    logging.info(f"Deleted Key: {key}")
    return jsonify({"status": "success"}), 200


if __name__ == '__main__':
    load_data()
    app.run(host='0.0.0.0', port=8080, threaded=True)