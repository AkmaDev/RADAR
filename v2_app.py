"""
v2_app.py — ANTIGRAVITY v2
---------------------------
Interface de qualification par service pour les acheteurs B2B.
Lancer : python v2_app.py  →  http://localhost:5002
"""

import json
from pathlib import Path

import pandas as pd
from flask import Flask, render_template

from v2_conditions import evaluate_click_collect

app   = Flask(__name__)
DATA  = Path("data/romainville_SIGNAUX.csv")


def _load(service: str = "click_collect") -> list[dict]:
    df = pd.read_csv(DATA, dtype=str).fillna("")

    restaurants = []
    for _, row in df.iterrows():
        r = row.to_dict()

        if service == "click_collect":
            eval_result = evaluate_click_collect(r)
        else:
            continue  # autres services à venir

        if eval_result["badge"] == "non_qualifie":
            continue

        # Extraire ville proprement
        address = r.get("address", "")
        city    = address.split(",")[-1].strip() if "," in address else address

        restaurants.append({
            "id":           r.get("place_id") or r.get("siret") or r.get("title", ""),
            "title":        r.get("title", "").strip(),
            "category":     r.get("category", "").strip(),
            "address":      address,
            "city":         city,
            "phone":        r.get("phone", "").strip(),
            "website":      r.get("website", "").strip(),
            "latitude":     r.get("latitude", ""),
            "longitude":    r.get("longitude", ""),
            "rating":       r.get("review_rating", ""),
            "review_count": r.get("review_count", ""),
            "cc":           eval_result,
        })

    # Dédoublonner sur siret (garder le meilleur score en cas de doublon)
    seen = {}
    for r in restaurants:
        key = r.get("siret") or r.get("id")
        if key not in seen or r["cc"]["total_met"] > seen[key]["cc"]["total_met"]:
            seen[key] = r
    restaurants = list(seen.values())

    # Tri : maintenant d'abord, puis surveiller ; à égalité par score décroissant
    order = {"maintenant": 0, "surveiller": 1}
    restaurants.sort(
        key=lambda x: (order[x["cc"]["badge"]], -x["cc"]["total_met"])
    )
    return restaurants


@app.route("/")
def index():
    restaurants  = _load("click_collect")
    n_maintenant = sum(1 for r in restaurants if r["cc"]["badge"] == "maintenant")
    n_surveiller = sum(1 for r in restaurants if r["cc"]["badge"] == "surveiller")

    return render_template(
        "v2.html",
        restaurants  = restaurants,
        n_maintenant = n_maintenant,
        n_surveiller = n_surveiller,
        total        = len(restaurants),
        restaurants_json = json.dumps(restaurants, ensure_ascii=False),
    )


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5002))
    app.run(debug=False, host="0.0.0.0", port=port)
