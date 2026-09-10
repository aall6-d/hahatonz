import json
import os

KB_PATH = os.path.join(os.path.dirname(__file__), "..", "data",
                       "knowledge_items.json")

CATEGORIES = ["Доступы", "Корпоративная почта", "Программное обеспечение",
              "Рабочее место", "Wi-Fi", "VPN", "Оборудование"]


def load_kb():
    with open(KB_PATH, encoding="utf-8") as f:
        return json.load(f)


def get_by_id(item_id):
    if not item_id:
        return None
    for item in load_kb():
        if item["id"] == item_id:
            return item
    return None


def match_category_button(text: str):
    t = text.strip().lower()
    for cat in CATEGORIES:
        if t == cat.lower():
            return cat
    return None


def find_scenario(category: str, text: str):
    items = [i for i in load_kb() if i["category"] == category]
    if not items:
        return None
    t = text.lower()

    def score(item):
        return sum(1 for kw in item.get("keywords", []) if kw in t)

    return max(items, key=score)


def search(query: str, limit: int = 5):
    t = query.lower()
    results = []
    for item in load_kb():
        s = sum(1 for kw in item.get("keywords", []) if kw in t)
        if t and t in item["title"].lower():
            s += 3
        if s > 0:
            results.append((s, item))
    results.sort(key=lambda x: -x[0])
    return [{"id": i["id"], "title": i["title"], "category": i["category"],
             "steps": i["steps"]} for _, i in results[:limit]]