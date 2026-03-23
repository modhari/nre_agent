import requests

LATTICE_URL = "http://lattice:8080/run"

def call_lattice(scenario):
    headers = {
        "Authorization": "Bearer change_me"
    }

    payload = {
        "scenario": scenario
    }

    r = requests.post(LATTICE_URL, json=payload, headers=headers)
    return r.text
