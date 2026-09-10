import json
import os

KB_PATH = os.path.join(os.path.dirname(__file__), "..", "data",
                       "knowledge_items.json")

CATEGORIES = ["Доступы", "Корпоративная почта", "Программное обеспечение",
              "Рабочее место", "Wi-Fi", "VPN", "Оборудование"]


def load_kb():
    with open(KB_PATH, encoding="utf-8") as f:
        return json.load(f)


def get_by_id(scenario_id):
    for item in load_kb():
        if item.get("id") == scenario_id:
            return item
    return None


def find_scenario(category, text):
    text = (text or "").lower()
    best, best_score = None, 0
    for item in load_kb():
        if item.get("category") != category:
            continue
        score = sum(1 for kw in item.get("keywords", []) if kw in text)
        if score > best_score:
            best, best_score = item, score
    return best if best_score > 0 else None


def match_category_button(text):
    t = (text or "").strip().lower()
    for cat in CATEGORIES:
        if t == cat.lower():
            return cat
    return None


def search(q):
    q = (q or "").lower().strip()
    if not q:
        return []
    out = []
    for item in load_kb():
        if (q in item.get("title", "").lower()
                or any(q in kw for kw in item.get("keywords", []))):
            out.append({"id": item.get("id"), "title": item.get("title"),
                        "category": item.get("category"),
                        "steps": item.get("steps", [])})
    return out