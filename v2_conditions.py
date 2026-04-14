"""
v2_conditions.py — Moteur de qualification par service
-------------------------------------------------------
Évalue chaque restaurant contre les conditions d'un service donné.
Retourne un dict structuré : conditions profil, conditions timing, badge, meilleur moment.

Services disponibles :
  - click_collect : Click & Collect / Commande en ligne
  (KDS, Bornes à venir)
"""

import ast
import json
import re
from datetime import date, datetime

# ─── Catégories à volume élevé (pertinentes pour Click & Collect) ──────────────

CATEGORIES_VOLUME = {
    "restauration rapide", "restaurant", "pizzeria", "brasserie",
    "restaurant africain", "restaurant italien", "restaurant méditerranéen",
    "restaurant grec", "restaurant turc", "restaurant français",
    "restaurant brunch", "restaurant familial", "restaurant halal",
    "pizzas à emporter", "fast food", "burger", "sandwicherie",
    "kebab", "sushi", "japonais", "chinois", "thaïlandais",
    "libanais", "indien", "mexicain", "asiatique", "tacos",
    # variantes anglaises / mixtes présentes dans les données
    "italian", "pizza", "pizzas", "african", "greek", "turkish",
    "french", "mediterranean", "asian", "japanese", "chinese",
    "thai", "lebanese", "indian", "halal", "brunch",
}

# ─── Mots-clés douleur attente ─────────────────────────────────────────────────

ATTENTE_KEYWORDS = [
    "attente", "queue", "lent", "long", "débordé", "déborde",
    "rush", "service lent", "trop long", "patience", "tarde",
    "interminable", "file d'attente", "attendre",
]

# ─── Helpers ───────────────────────────────────────────────────────────────────

def _safe_float(val, default=0.0):
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _safe_int(val, default=0):
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return default


def _parse_json(val):
    if not val or str(val).strip() in ("", "nan", "None"):
        return None
    try:
        return json.loads(val)
    except Exception:
        return None


def _noon_peak(popular_times_dict):
    """
    True si le restaurant a un pic midi fort (≥ 70%) sur au moins un jour
    ET que ce jour-là le pic midi est supérieur au pic soir.
    Évaluation jour par jour pour éviter de mélanger des jours différents.
    """
    if not popular_times_dict:
        return False, 0
    best_noon = 0
    noon_dominates = False
    for day, hours in popular_times_dict.items():
        noon_max = max((int(v) for h, v in hours.items() if int(h) in [11, 12, 13, 14]), default=0)
        eve_max  = max((int(v) for h, v in hours.items() if int(h) in [18, 19, 20, 21, 22, 23]), default=0)
        if noon_max >= 70 and noon_max >= eve_max:
            noon_dominates = True
        best_noon = max(best_noon, noon_max)
    return noon_dominates, best_noon


def _best_call_time(popular_times_dict):
    """Trouve le créneau avec le moins d'affluence pendant les heures ouvrables."""
    if not popular_times_dict:
        return "Matin avant 11h"

    DAY_FR = {
        "Monday": "Lundi", "Tuesday": "Mardi", "Wednesday": "Mercredi",
        "Thursday": "Jeudi", "Friday": "Vendredi",
    }
    candidates = []
    for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]:
        if day not in popular_times_dict:
            continue
        hours = popular_times_dict[day]
        for hour_str, val in hours.items():
            h = int(hour_str)
            v = int(val)
            # Créneau utile : restaurant ouvert + hors rush midi + heures de bureau
            if h in [9, 10, 15, 16, 17] and v > 0:
                candidates.append((v, h, day))

    if not candidates:
        return "Matin avant 11h"

    candidates.sort()
    _, best_hour, best_day = candidates[0]
    return f"{DAY_FR.get(best_day, best_day)} {best_hour}h–{best_hour + 1}h"


def _parse_price_tier(price_range_str) -> str:
    """
    Normalise price_range en 3 niveaux : 'low', 'mid', 'high', ou 'unknown'.
    low  = 1–10€ / €
    mid  = 10–20€ / €€
    high = 20€+ / €€€ / +de 100€
    """
    v = str(price_range_str).strip()
    if not v or v in ("", "nan"):
        return "unknown"
    # Tier symbols
    clean = v.replace("\xa0", "").replace("\ufffd", "")
    if clean in ("\u20ac", "e", "\u20ac"):
        return "low"
    if clean in ("\u20ac\u20ac", "ee"):
        return "mid"
    if "\u20ac\u20ac\u20ac" in clean or "eee" in clean:
        return "high"
    # Numeric ranges — extract first number
    import re as _re
    nums = _re.findall(r'\d+', v)
    if nums:
        low_val = int(nums[0])
        if low_val < 10:
            return "low"
        elif low_val < 20:
            return "mid"
        else:
            return "high"
    return "unknown"


def _years_without_digital(date_str) -> int | None:
    """Années écoulées depuis l'ouverture (si avant 2021)."""
    if not date_str or str(date_str).strip() in ("", "nan"):
        return None
    try:
        d = datetime.strptime(str(date_str).strip()[:10], "%Y-%m-%d").date()
        if d.year >= 2021:
            return None          # trop récent, pas "retard assumé"
        years = (date.today() - d).days // 365
        return years if years >= 3 else None
    except Exception:
        return None


def _has_short_service(open_hours_str) -> bool:
    """
    Vrai si le restaurant ferme définitivement avant 15h30 en semaine
    (pas de service soir). Un restaurant ouvert jusqu'à 17h+ ou minuit
    n'est PAS un service court.
    """
    if not open_hours_str or str(open_hours_str).strip() in ("", "nan"):
        return False
    try:
        hours = ast.literal_eval(open_hours_str)
    except Exception:
        try:
            hours = json.loads(open_hours_str)
        except Exception:
            return False
    if not isinstance(hours, dict):
        return False

    WEEKDAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi"]
    close_times = []

    for day in WEEKDAYS:
        slots = hours.get(day, [])
        for slot in slots:
            slot_str = str(slot).lower().strip()
            if "fermé" in slot_str or "ferm" in slot_str:
                continue
            parts = re.split(r'[–\-\u2013\u2014\ufffd\s]+', slot_str)
            if len(parts) >= 2:
                close_raw = parts[-1].strip().replace("h", ":").replace("H", ":")
                mc = re.search(r'(\d{1,2})[:\.](\d{2})', close_raw)
                if mc:
                    close_h = int(mc.group(1))
                    close_m = int(mc.group(2))
                    # 00:00 = minuit → 1440 min (fin de journée)
                    minutes = 1440 if (close_h == 0 and close_m == 0) else close_h * 60 + close_m
                    close_times.append(minutes)

    if not close_times:
        return False
    # Si au moins un slot ferme après 17h (1020 min) → pas un service court
    if any(t >= 1020 for t in close_times):
        return False
    # Court si la majorité ferme avant 15h30 (=930 min)
    early = sum(1 for t in close_times if t <= 930)
    return early >= len(close_times) // 2


def _count_attente(user_reviews_str):
    """Compte les avis récents mentionnant des problèmes d'attente."""
    if not user_reviews_str or str(user_reviews_str).strip() in ("", "nan"):
        return 0
    try:
        reviews = json.loads(user_reviews_str)
        return sum(
            1 for r in reviews[:15]
            if any(kw in str(r.get("Description", "")).lower() for kw in ATTENTE_KEYWORDS)
        )
    except Exception:
        return 0


def _weeks_since(date_str):
    """Nombre de semaines depuis une date ISO (YYYY-MM-DD)."""
    if not date_str or str(date_str).strip() in ("", "nan"):
        return None
    try:
        d = datetime.strptime(str(date_str).strip()[:10], "%Y-%m-%d").date()
        delta = (date.today() - d).days
        return max(0, delta // 7) if delta >= 0 else None
    except Exception:
        return None


# ─── Évaluateur principal ──────────────────────────────────────────────────────

def evaluate_click_collect(row: dict) -> dict:
    """
    Évalue un restaurant contre les 8 conditions Click & Collect.

    Retourne :
      profil      : liste de 5 conditions (statiques)
      timing      : liste de 3 conditions (dynamiques)
      profil_met  : int
      timing_met  : int
      total_met   : int
      total       : int (= 8)
      badge       : 'maintenant' | 'surveiller' | 'non_qualifie'
      best_call   : str
    """
    rating      = _safe_float(row.get("review_rating"))
    count       = _safe_int(row.get("review_count"))
    category    = str(row.get("category", "")).lower().strip()
    pop_times   = _parse_json(row.get("popular_times"))

    # ── Profil ────────────────────────────────────────────────────────────────

    sans_commande  = str(row.get("signal_sans_commande_en_ligne", "False")) == "True"
    plateforme     = str(row.get("signal_plateforme_uniquement", "False")) == "True"
    has_demand     = rating >= 3.8 and count >= 40
    high_volume    = count > 100
    has_noon, _    = _noon_peak(pop_times)
    short_service  = _has_short_service(row.get("open_hours", ""))
    is_volume      = any(c in category for c in CATEGORIES_VOLUME)

    rating_str = f"{rating:.1f}" if rating > 0 else "—"

    profil = [
        {
            "id":     "sans_commande",
            "met":    sans_commande,
            "label":  "Pas de commande en ligne propre",
            "detail": "Aucun canal digital propriétaire détecté"
                      if sans_commande
                      else "Commande en ligne déjà présente",
        },
        {
            "id":     "plateforme",
            "met":    plateforme,
            "label":  "Livraison plateforme uniquement",
            "detail": "Dépendant Deliveroo / Uber Eats — commissions 25–30 %"
                      if plateforme
                      else "Pas de dépendance plateforme détectée",
        },
        {
            "id":     "demande",
            "met":    has_demand,
            "label":  f"Note {rating_str} — {count} avis",
            "detail": "Demande réelle confirmée"
                      if has_demand
                      else "Volume insuffisant (minimum : 40 avis, note ≥ 3.8)",
        },
        {
            "id":     "volume_eleve",
            "met":    high_volume,
            "label":  f"{count} avis — volume élevé confirmé" if high_volume else f"{count} avis",
            "detail": "Restaurant qui tourne — base client large"
                      if high_volume
                      else "Volume modéré (seuil : 100 avis)",
        },
        {
            "id":     "pic_midi",
            "met":    has_noon,
            "label":  "Pic midi fort (popular_times)",
            "detail": "Rush identifié — clients en attente chaque midi"
                      if has_noon
                      else "Pas de pic midi significatif détecté",
        },
        {
            "id":     "service_court",
            "met":    short_service,
            "label":  "Service concentré sur créneau court",
            "detail": "Fermeture avant 15h30 — tout le CA sur 2h, moindre absence = chaos"
                      if short_service
                      else "Horaires étendus — tension de flux moins critique",
        },
        {
            "id":     "categorie",
            "met":    is_volume,
            "label":  f"Catégorie volume ({row.get('category', '').strip()})",
            "detail": "Segment adapté au Click & Collect"
                      if is_volume
                      else "Catégorie non prioritaire",
        },
    ]

    # ── Timing ────────────────────────────────────────────────────────────────

    is_new      = str(row.get("signal_nouveau", "False")) == "True"
    weeks       = _weeks_since(row.get("date_debut_activite"))
    new_gerant  = str(row.get("signal_changement_gerant", "False")) == "True"
    attente_n   = _count_attente(row.get("user_reviews", ""))
    has_attente = attente_n >= 2 or str(row.get("signal_avis_negatifs_recents", "False")) == "True"
    years_late  = _years_without_digital(row.get("date_debut_activite"))

    new_label = (
        f"Ouvert il y a {weeks} semaine{'s' if weeks and weeks > 1 else ''}"
        if (is_new and weeks is not None)
        else "Ouverture récente"
        if is_new
        else "Établissement non récent"
    )

    attente_label = (
        f"Avis récents : « attente » ×{attente_n}"
        if attente_n >= 2
        else "Douleur attente détectée"
        if has_attente
        else "Pas de douleur attente visible"
    )

    timing = [
        {
            "id":     "nouveau",
            "met":    is_new,
            "label":  new_label,
            "detail": "Fenêtre de décision encore ouverte"
                      if is_new
                      else "Outils probablement déjà en place",
        },
        {
            "id":     "retard_assume",
            "met":    bool(years_late and sans_commande),
            "label":  f"{years_late} ans sans digital" if years_late else "Pas de retard digital identifié",
            "detail": "Ouvert avant 2021, toujours sans commande en ligne — conscience probable du retard"
                      if (years_late and sans_commande)
                      else "Restaurant récent ou déjà équipé",
        },
        {
            "id":     "avis_attente",
            "met":    has_attente,
            "label":  attente_label,
            "detail": "Douleur active et publique"
                      if has_attente
                      else "Avis récents sans mention d'attente",
        },
        {
            "id":     "gerant",
            "met":    new_gerant,
            "label":  "Changement de gérant récent" if new_gerant else "Pas de changement de gérant",
            "detail": "Nouveau décideur — remise à plat en cours"
                      if new_gerant
                      else "Signal absent ce mois",
        },
    ]

    # ── Contexte établissement ────────────────────────────────────────────────

    price_tier   = _parse_price_tier(row.get("price_range", ""))
    has_thefork  = bool(str(row.get("thefork_url", "")).strip())
    has_reserv   = str(row.get("signal_reservation_gmap", "False")) == "True"

    PRICE_LABELS = {"low": "1–10 €", "mid": "10–20 €", "high": "20 € +", "unknown": "—"}
    contexte = {
        "price_tier":   price_tier,
        "price_label":  PRICE_LABELS[price_tier],
        "thefork":      has_thefork,
        "reservation":  has_reserv,
        # sit-down = établissement probablement axé sur l'expérience en salle
        "sit_down": has_thefork or (price_tier == "high") or (has_reserv and price_tier in ("mid", "high")),
    }

    # ── Badge & métriques ─────────────────────────────────────────────────────

    profil_met = sum(1 for c in profil  if c["met"])
    timing_met = sum(1 for c in timing  if c["met"])
    total_met  = profil_met + timing_met
    total      = len(profil) + len(timing)

    if profil_met >= 3 and timing_met >= 1:
        badge = "maintenant"
    elif profil_met >= 3:
        badge = "surveiller"
    else:
        badge = "non_qualifie"

    return {
        "profil":     profil,
        "timing":     timing,
        "profil_met": profil_met,
        "timing_met": timing_met,
        "total_met":  total_met,
        "total":      total,
        "badge":      badge,
        "contexte":   contexte,
        "best_call":  _best_call_time(pop_times),
    }
