import os
import json
import urllib.request


def _enabled():
    return bool(os.environ.get("YC_API_KEY") and os.environ.get("YC_FOLDER_ID"))


def _call_yandex(prompt, max_tokens=150):
    url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
    body = {
        "modelUri": f"gpt://{os.environ['YC_FOLDER_ID']}/yandexgpt/latest",
        "completionOptions": {"stream": False, "temperature": 0.2,
                              "maxTokens": str(max_tokens)},
        "messages": [{"role": "user", "text": prompt}],
    }
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Api-Key {os.environ['YC_API_KEY']}"})
    with urllib.request.urlopen(req, timeout=8) as r:
        data = json.loads(r.read().decode())
    return data["result"]["alternatives"][0]["message"]["text"]


def interpret(message, options):
    """Свободный ответ -> вариант из списка. Любая ошибка -> None."""
    if not _enabled() or not options:
        return None
    prompt = (
        "Ты помогаешь технической поддержке понять ответ пользователя.\n"
        f"Предложенные варианты: {', '.join(options)}.\n"
        f"Ответ пользователя: «{message}».\n"
        "Верни СТРОГО один вариант из списка одной строкой, "
        "а если ни один не подходит — слово NONE."
    )
    try:
        ans = _call_yandex(prompt).strip().strip('"').lower()
        if ans.startswith("none"):
            return None
        for o in options:
            if o.lower() in ans or ans in o.lower():
                return o
        return None
    except Exception:
        return None


def is_real_problem(message):
    """Второе мнение: техническая проблема или шутка/бессмыслица?"""
    if not _enabled():
        return None
    prompt = (
        "Это сообщение в техподдержку. Описывает ли оно техническую "
        "проблему (что-то не работает, не открывается, ошибка) или это "
        f"шутка/бессмыслица? Сообщение: «{message}». Верни строго YES или NO."
    )
    try:
        return _call_yandex(prompt).strip().upper().startswith("YES")
    except Exception:
        return None
