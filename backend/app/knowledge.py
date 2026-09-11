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

FALLBACK_KEYS = {
    "Доступы": ["вход", "пароль", "логин", "аккаунт", "доступ", "забыл",
                "авториз", "подписк", "актион 360"],
    "Корпоративная почта": ["письм", "почт", "email", "спам",
                            "восстановление", "уведомлен"],
    "Рабочее место": ["страниц", "браузер", "белый экран",
                      "не открывается", "сайт"],
    "Wi-Fi": ["вайфай", "wi-fi", "wifi", "интернет", "роутер",
              "не подключается"],
    "VPN": ["vpn", "впн", "офис", "корпоративн"],
    "Оборудование": ["звук", "микрофон", "наушник", "динамик",
                     "вебинар"],
    "Программное обеспечение": ["курс", "обучен", "вебинар",
                                "видео", "ии-ассистент", "тест"],
}


def fallback_classify(text):
    """Keyword fallback: возвращает (category, 0.55) или (None, 0)."""
    t = (text or "").lower()
    best_cat, best_hits = None, 0
    for cat, keys in FALLBACK_KEYS.items():
        hits = sum(1 for k in keys if k in t)
        if hits > best_hits:
            best_cat, best_hits = cat, hits
    if best_cat and best_hits > 0:
        return best_cat, 0.55
    return None, 0.0

def find_by_title(text):
    t = (text or "").strip().lower()
    for item in load_kb():
        if item.get("title", "").strip().lower() == t:
            return item
    return None


def category_examples(category, limit=4):
    out = []
    for item in load_kb():
        if item.get("category") == category:
            out.append(item.get("title"))
        if len(out) >= limit:
            break
    return out or CATEGORIES