"""Tests for the key value store. Start the server first: python server.py
Then run: python -m unittest test_server -v
"""
import threading
import unittest
import uuid

import requests

BASE = "http://127.0.0.1:8080"


def new_key(name="k"):
    # Unique per test so reruns against the same server don't collide.
    return f"{name}_{uuid.uuid4().hex[:8]}"


class BasicTests(unittest.TestCase):
    def test_put_then_get(self):
        k = new_key()
        requests.put(f"{BASE}/{k}", json={"value": "hello"})
        r = requests.get(f"{BASE}/{k}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"value": "hello"})

    def test_new_key_returns_201(self):
        r = requests.put(f"{BASE}/{new_key()}", json={"value": 1})
        self.assertEqual(r.status_code, 201)

    def test_overwrite_returns_200(self):
        k = new_key()
        requests.put(f"{BASE}/{k}", json={"value": 1})
        r = requests.put(f"{BASE}/{k}", json={"value": 2})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(requests.get(f"{BASE}/{k}").json(), {"value": 2})

    def test_get_missing_returns_404(self):
        r = requests.get(f"{BASE}/{new_key()}")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json(), {"error": "key not found"})

    def test_delete_existing_then_get_404(self):
        k = new_key()
        requests.put(f"{BASE}/{k}", json={"value": "x"})
        self.assertEqual(requests.delete(f"{BASE}/{k}").status_code, 200)
        self.assertEqual(requests.get(f"{BASE}/{k}").status_code, 404)

    def test_delete_missing_returns_404(self):
        r = requests.delete(f"{BASE}/{new_key()}")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json(), {"error": "key not found"})

    def test_bad_json_returns_400(self):
        r = requests.put(f"{BASE}/{new_key()}", data="{not json",
                         headers={"Content-Type": "application/json"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json(), {"error": "bad request"})

    def test_missing_value_returns_400(self):
        r = requests.put(f"{BASE}/{new_key()}", json={"val": 1})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json(), {"error": "bad request"})

    def test_non_object_body_returns_400(self):
        r = requests.put(f"{BASE}/{new_key()}", json=[1, 2, 3])
        self.assertEqual(r.status_code, 400)

    def test_post_and_put_are_equivalent(self):
        a, b = new_key("post"), new_key("put")
        self.assertEqual(requests.post(f"{BASE}/{a}", json={"value": "v"}).status_code, 201)
        self.assertEqual(requests.put(f"{BASE}/{b}", json={"value": "v"}).status_code, 201)
        self.assertEqual(requests.post(f"{BASE}/{a}", json={"value": "w"}).status_code, 200)
        self.assertEqual(requests.put(f"{BASE}/{b}", json={"value": "w"}).status_code, 200)
        self.assertEqual(requests.get(f"{BASE}/{a}").json(), requests.get(f"{BASE}/{b}").json())


class ConcurrencyTests(unittest.TestCase):
    def test_same_key_last_writer_wins(self):
        k = new_key()
        values = [f"v{i}" for i in range(20)]
        codes = []

        def writer(v):
            codes.append(requests.put(f"{BASE}/{k}", json={"value": v}).status_code)

        threads = [threading.Thread(target=writer, args=(v,)) for v in values]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertTrue(all(c in (200, 201) for c in codes))
        self.assertEqual(codes.count(201), 1)  # exactly one thread created it
        r = requests.get(f"{BASE}/{k}")
        self.assertEqual(r.status_code, 200)
        self.assertIn(r.json()["value"], values)
        # Server still healthy.
        self.assertEqual(requests.put(f"{BASE}/{new_key()}", json={"value": 1}).status_code, 201)

    def test_many_distinct_keys(self):
        prefix = new_key("bulk")
        failures = []

        def writer(t):
            with requests.Session() as s:
                for i in range(100):
                    r = s.put(f"{BASE}/{prefix}_{t}_{i}", json={"value": f"{t}-{i}"})
                    if r.status_code != 201:
                        failures.append(r.status_code)

        threads = [threading.Thread(target=writer, args=(t,)) for t in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(failures, [])
        with requests.Session() as s:
            for t in range(20):
                for i in range(100):
                    r = s.get(f"{BASE}/{prefix}_{t}_{i}")
                    self.assertEqual(r.status_code, 200)
                    self.assertEqual(r.json(), {"value": f"{t}-{i}"})


if __name__ == "__main__":
    unittest.main()
