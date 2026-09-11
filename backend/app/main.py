import json
import re
import os
import difflib
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware

from app import db, knowledge, classifier
from app.schemas import ChatRequest, ChatResponse, FeedbackRequest

app = FastAPI(title="Актион Ассист API")
db.init_db()

SUPPORT_CODE = os.environ.get("SUPPORT_CODE", "hacaton")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

OTHER_WORDS = {"другое", "другой", "не знаю", "сложно сказать",
               "затрудняюсь ответить"}
MAX_RETRIES = 2

PROBLEM_MARKERS = (
    "не ", "нет ", "не могу", "не работает", "не открывается",
    "не загружается", "не приходит", "не удается", "невозможно",
    "ошибка", "сломал", "пропал", "пропала", "исчез", "тормозит",
    "виснет", "зависает", "медленно", "не видно", "не слышно",
    "забыл", "забыла", "неверный", "отключ", "блокиру", "не пускает",
    "закончил", "нет доступа", "не отображается", "не слышат",
)
REFUSAL_WORDS = ("не хочу", "не буду", "не стану", "не собираюсь",
                 "отказываюсь", "мне некогда", "не хочу выполнять")


# ---------- анализ текста ----------

def has_problem_marker(text):
    t = (text or "").lower()
    return any(m in t for m in PROBLEM_MARKERS)

def is_refusal(text):
    t = (text or "").strip().lower()
    return any(w in t for w in REFUSAL_WORDS)

def is_no(text):
    t = (text or "").strip().lower()
    return (t.startswith("нет") or "не помогло" in t
            or "не решено" in t or "осталась" in t
            or "не получилось" in t or "не вышло" in t
            or "безрезультатно" in t)

def is_yes(text):
    t = (text or "").strip().lower()
    if is_no(t) or is_refusal(t):
        return False
    if t.startswith("да") and " но " in f" {t} ":
        return False
    return (t.startswith("да") or "помогло" in t
            or "решено" in t or "получилось" in t)

def is_nonsense(text):
    t = (text or "").strip().lower()
    if len(t) < 3:
        return True
    letters = re.findall(r"[а-яa-zё]", t)
    if not letters:
        return True
    if len(set(letters)) <= 2:
        return True
    return False

def match_option(answer, options):
    if not options:
        return None
    a = answer.strip().lower().strip(".!? ")
    for o in options:
        if a == o.strip().lower():
            return o
    for o in options:
        ol = o.strip().lower()
        if len(ol) >= 4 and (ol in a or a in ol):
            return o
    close = difflib.get_close_matches(
        a, [o.strip().lower() for o in options], n=1, cutoff=0.72)
    if close:
        for o in options:
            if o.strip().lower() == close[0]:
                return o
    return None


# ---------- ответы ----------

def respond(ticket_id, type_, message, category=None, confidence=None,
            options=None, steps=None, success_question=None,
            solution_id=None, status=None):
    db.add_message(ticket_id, "ai", message)
    return ChatResponse(
        ticket_id=ticket_id, type=type_, category=category,
        confidence=confidence, message=message, options=options or [],
        steps=steps or [], success_question=success_question,
        solution_id=solution_id, status=status,
    )

def reask_question(ticket_id, scenario, cat, conf, index, note):
    qs = (scenario or {}).get("questions", [])
    q = qs[index] if index < len(qs) else None
    if not q:
        return respond(ticket_id, "question",
                       note + " Уточните, что именно происходит?",
                       cat, conf, options=knowledge.CATEGORIES)
    return respond(ticket_id, "question", note + " " + q["text"],
                   cat, conf, options=q.get("options", []))

def apply_branch(ticket_id, scenario, option_label, cat, conf):
    branches = scenario.get("branches") or {}
    br = branches.get(option_label)
    if not br:
        return None
    btype = br.get("type")
    if btype == "steps":
        db.update_ticket(ticket_id, state="awaiting_result")
        return respond(ticket_id, "solution",
                       "Я нашёл решение именно для вашего случая. "
                       "Выполните шаги по порядку:",
                       cat, conf, steps=br.get("steps", []),
                       success_question=br.get("success_question")
                       or scenario.get("success_question"),
                       solution_id=scenario.get("id"))
    if btype == "scenario":
        target = knowledge.get_by_id(br.get("scenario_id"))
        if target:
            db.update_ticket(ticket_id, scenario_id=target.get("id"),
                             title=target.get("title"), question_index=0,
                             state="awaiting_question")
            db.add_message(ticket_id, "ai",
                           f"Уточняю: это про «{target.get('title')}».")
            return ask_or_solve(ticket_id, target, cat, conf, 0)
    if btype == "escalate":
        db.update_ticket(ticket_id, state="escalation_offer")
        return respond(ticket_id, "escalation",
                       br.get("message", "Передам обращение специалисту "
                                         "вместе с историей диалога."),
                       cat, conf, options=["Передать специалисту"],
                       status="in_progress")
    return None

def ask_or_solve(ticket_id, scenario, cat, conf, index):
    questions = scenario.get("questions", [])
    if index < len(questions):
        q = questions[index]
        db.update_ticket(ticket_id, state="awaiting_question",
                         question_index=index)
        return respond(ticket_id, "question", q["text"], cat, conf,
                       options=q.get("options", []))
    db.update_ticket(ticket_id, state="awaiting_result")
    return respond(ticket_id, "solution",
                   "Я нашёл решение. Выполните шаги по порядку:",
                   cat, conf, steps=scenario.get("steps", []),
                   success_question=scenario.get("success_question"),
                   solution_id=scenario.get("id"))

def start_diagnosis(ticket_id, text, cat, conf):
    scenario = knowledge.find_scenario(cat, text)
    if not scenario:
        db.update_ticket(ticket_id, state="clarification")
        return respond(ticket_id, "clarification",
                       "Уточните, пожалуйста, что именно не работает?",
                       cat, conf, options=knowledge.CATEGORIES)
    db.update_ticket(ticket_id, state="awaiting_question", category=cat,
                     confidence=conf, scenario_id=scenario.get("id"),
                     question_index=0, title=scenario.get("title"))
    intro = (f"Я понял, в чём проблема. Категория: {cat}. "
             f"Уверенность: {int(conf * 100)}%.")
    db.add_message(ticket_id, "ai", intro)
    return ask_or_solve(ticket_id, scenario, cat, conf, 0)

def escalate_ticket(ticket_id):
    ticket = db.get_ticket(ticket_id)
    scenario = knowledge.get_by_id(ticket["scenario_id"])
    reason = (scenario or {}).get("escalation_reason",
                                  "Стандартные решения не помогли")
    db.update_ticket(ticket_id, status="escalated", state="escalated",
                     escalated_at=db.now())
    db.add_message(ticket_id, "system",
                   "Обращение передано специалисту")
    db.add_message(ticket_id, "system", f"Эскалация: {reason}")
    card = {
        "category": ticket["category"],
        "detected_problem": ticket["title"],
        "user_message": ticket["problem_text"],
        "clarification_answers": json.loads(ticket["answers"] or "[]"),
        "performed_steps": (scenario or {}).get("steps", []),
        "ai_confidence": ticket["confidence"],
        "escalation_reason": reason,
    }
    return card, reason


def safe_classify(text):
    """Обёртка: LLM/классификатор → при ошибке fallback."""
    try:
        cat, conf = classifier.classify(text)
        if cat and conf and conf >= 0.5:
            return cat, conf
    except Exception:
        pass
    return knowledge.fallback_classify(text)


# ---------- запуск ----------

@app.on_event("startup")
def startup():
    db.init_db()


@app.get("/health")
def health():
    return {"status": "ok"}


# ---------- чат пользователя ----------

@app.post("/api/chat", response_model=ChatResponse)
def chat(payload: ChatRequest):
    text = (payload.message or payload.answer or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="Пустое сообщение")
    if len(text) > 2000:
        raise HTTPException(status_code=422,
                            detail="Сообщение длиннее 2000 символов")

    ticket_id = payload.ticket_id
    state = "new"
    if ticket_id:
        ticket = db.get_ticket(ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="Обращение не найдено")
        state = ticket["state"]
        # Если тикет закрыт — создаём новый с parent_id
        if ticket["status"] in ("resolved", "resolved_by_specialist",
                                "escalated", "abandoned"):
            new_id = db.create_ticket(text, parent_id=ticket_id)
            ticket_id = new_id
            state = "new"
            ticket = db.get_ticket(ticket_id)
    else:
        ticket_id = db.create_ticket(text)

    db.add_message(ticket_id, "user", text)

    if state in ("new", "clarification"):
        cat, conf = safe_classify(text)
        picked = knowledge.match_category_button(text)
        if picked:
            cat, conf = picked, 0.9
        if not cat or conf < 0.6:
            db.update_ticket(ticket_id, state="clarification",
                             category=cat, confidence=conf)
            return respond(
                ticket_id, "clarification",
                "Я хочу убедиться, что правильно понял проблему. "
                "Выберите категорию или опишите проблему подробнее:",
                cat, conf, options=knowledge.CATEGORIES)
        if not picked and not has_problem_marker(text):
            from app import llm
            if llm.is_real_problem(text) is not True:
                db.update_ticket(ticket_id, state="clarification",
                                 category=cat, confidence=conf)
                return respond(
                    ticket_id, "clarification",
                    "Похоже, это не описание технической проблемы 🙂 "
                    "Расскажите, что не работает, или выберите категорию:",
                    cat, conf, options=knowledge.CATEGORIES)
        return start_diagnosis(ticket_id, text, cat, conf)

    if state == "awaiting_question":
        ticket = db.get_ticket(ticket_id)
        scenario = knowledge.get_by_id(ticket["scenario_id"])
        if not scenario:
            db.update_ticket(ticket_id, state="clarification")
            return respond(ticket_id, "clarification",
                           "Уточните, что именно не работает?",
                           ticket["category"], ticket["confidence"],
                           options=knowledge.CATEGORIES)
        idx = ticket["question_index"]
        qs = scenario.get("questions", [])
        q = qs[idx] if idx < len(qs) else None
        options = q.get("options", []) if q else []
        cat, conf = ticket["category"], ticket["confidence"]

        matched = match_option(text, options)
        if matched is None:
            from app import llm
            matched = llm.interpret(text, options)

        if matched is None:
            cat2, conf2 = safe_classify(text)
            if (cat2 and cat2 != cat and conf2 >= 0.7
                    and not is_nonsense(text)):
                db.update_ticket(ticket_id, state="topic_switch",
                                 pending_category=cat2)
                return respond(ticket_id, "question",
                               f"Похоже, это уже другая проблема: {cat2}. "
                               "Оформить её отдельным обращением?",
                               cat, conf,
                               options=["Да, другая проблема",
                                        "Нет, продолжаем"])
            if is_nonsense(text):
                note = "Я не понял ответ 🙂"
            else:
                note = ("Я не совсем понял: такого варианта нет. "
                        "Выберите кнопкой или опишите подробнее.")
            retries = (ticket["retry_count"] or 0) + 1
            if retries > MAX_RETRIES:
                db.update_ticket(ticket_id, state="escalation_offer",
                                 retry_count=retries)
                return respond(ticket_id, "escalation",
                               "Мои варианты не подошли к вашей ситуации. "
                               "Передам обращение специалисту вместе "
                               "со всем диалогом.",
                               cat, conf,
                               options=["Передать специалисту"],
                               status="in_progress")
            db.update_ticket(ticket_id, retry_count=retries)
            return reask_question(ticket_id, scenario, cat, conf, idx, note)

        db.add_answer(ticket_id, matched)
        db.update_ticket(ticket_id, retry_count=0)

        if matched.strip().lower() in OTHER_WORDS:
            br = apply_branch(ticket_id, scenario, matched, cat, conf)
            if br:
                return br
            db.update_ticket(ticket_id, state="awaiting_other")
            return respond(ticket_id, "question",
                           "Расскажите своими словами, что происходит? "
                           "Я подберу решение по вашему описанию.",
                           cat, conf)

        br = apply_branch(ticket_id, scenario, matched, cat, conf)
        if br:
            return br
        return ask_or_solve(ticket_id, scenario, cat, conf, idx + 1)

    if state == "awaiting_other":
        ticket = db.get_ticket(ticket_id)
        cat, conf = ticket["category"], ticket["confidence"]
        if is_nonsense(text):
            retries = (ticket["retry_count"] or 0) + 1
            if retries > MAX_RETRIES:
                db.update_ticket(ticket_id, state="escalation_offer",
                                 retry_count=retries)
                return respond(ticket_id, "escalation",
                               "Мне не хватает деталей, чтобы помочь "
                               "автоматически. Передам обращение специалисту.",
                               cat, conf,
                               options=["Передать специалисту"],
                               status="in_progress")
            db.update_ticket(ticket_id, retry_count=retries)
            return respond(ticket_id, "question",
                           "Я не понял. Опишите своими словами, "
                           "что происходит на экране?",
                           cat, conf)
        db.add_answer(ticket_id, text)
        items = [i for i in knowledge.load_kb() if i["category"] == cat]
        best, best_score = None, 0
        for it in items:
            s = sum(1 for kw in it.get("keywords", []) if kw in text.lower())
            if s > best_score:
                best, best_score = it, s
        if best and best_score > 0:
            db.update_ticket(ticket_id, scenario_id=best.get("id"),
                             title=best.get("title"), question_index=0,
                             state="awaiting_question", retry_count=0)
            db.add_message(ticket_id, "ai",
                           f"Спасибо, стало понятнее: это про "
                           f"«{best.get('title')}».")
            return ask_or_solve(ticket_id, best, cat, conf, 0)
        db.update_ticket(ticket_id, state="escalation_offer")
        return respond(ticket_id, "escalation",
                       "По этому описанию у меня пока нет готовой "
                       "инструкции. Передам обращение специалисту "
                       "вместе с диалогом.",
                       cat, conf,
                       options=["Передать специалисту"],
                       status="in_progress")

    if state == "topic_switch":
        ticket = db.get_ticket(ticket_id)
        if is_yes(text):
            cat2 = ticket["pending_category"] or ticket["category"]
            db.update_ticket(ticket_id, category=cat2, confidence=0.9,
                             scenario_id=None, question_index=0,
                             retry_count=0, title=None)
            return start_diagnosis(ticket_id, ticket["problem_text"],
                                   cat2, 0.9)
        db.update_ticket(ticket_id, state="awaiting_question")
        scenario = knowledge.get_by_id(ticket["scenario_id"])
        return reask_question(ticket_id, scenario, ticket["category"],
                              ticket["confidence"],
                              ticket["question_index"],
                              "Хорошо, продолжаем.")

    if state == "awaiting_result":
        ticket = db.get_ticket(ticket_id)
        cat, conf = ticket["category"], ticket["confidence"]
        if is_refusal(text):
            db.update_ticket(ticket_id, state="escalation_offer")
            return respond(ticket_id, "question",
                           "Понимаю: выполнять шаги не всегда удобно. "
                           "Могу передать обращение специалисту — "
                           "он поможет удалённо. Передать?",
                           cat, conf,
                           options=["Да, передать специалисту",
                                    "Попробую ещё раз"])
        if is_no(text):
            scenario = knowledge.get_by_id(ticket["scenario_id"])
            alt = (scenario or {}).get("alt_steps") or []
            if alt and not ticket.get("alt_used"):
                db.update_ticket(ticket_id, alt_used=1,
                                 state="awaiting_result")
                return respond(ticket_id, "solution",
                               "Понял, первый способ не сработал. "
                               "Попробуем следующий — выполните шаги "
                               "по порядку:",
                               cat, conf, steps=alt,
                               success_question=scenario.get(
                                   "success_question"),
                               solution_id=scenario.get("id"))
            db.update_ticket(ticket_id, state="escalation_offer")
            return respond(ticket_id, "escalation",
                           "Я выполнил доступные шаги, но проблема "
                           "сохраняется. Передам обращение специалисту "
                           "вместе со всей историей диагностики.",
                           cat, conf, options=["Передать специалисту"],
                           status="in_progress")
        if is_yes(text):
            db.update_ticket(ticket_id, status="resolved", state="resolved")
            db.add_message(ticket_id, "system",
                           "Обращение закрыто: проблема решена")
            return respond(ticket_id, "resolved",
                           f"Отлично! Проблема решена. "
                           f"Обращение #{ticket_id} закрыто. "
                           "Оцените, пожалуйста, мою помощь.",
                           cat, conf, status="resolved")
        return respond(ticket_id, "question",
                       "Подскажите, получилось выполнить шаги? "
                       "Ответьте «да» или «нет».",
                       cat, conf, options=["Да", "Нет"])

    if state == "escalation_offer":
        if is_yes(text) or "передать" in text.lower():
            card, reason = escalate_ticket(ticket_id)
            return respond(ticket_id, "escalated",
                           f"Обращение #{ticket_id} передано специалисту. "
                           f"Причина: {reason}",
                           None, None, status="escalated")
        db.update_ticket(ticket_id, state="awaiting_result")
        return respond(ticket_id, "question",
                       "Хорошо, продолжаем. Подскажите, получилось "
                       "выполнить шаги? Ответьте «да» или «нет».",
                       ticket["category"], ticket["confidence"],
                       options=["Да", "Нет"])

    db.update_ticket(ticket_id, state="new")
    return respond(ticket_id, "clarification",
                   "Опишите проблему подробнее.", None, None)


# ---------- тикеты ----------

@app.get("/api/tickets")
def list_tickets():
    return db.list_tickets()


@app.get("/api/tickets/{ticket_id}")
def get_ticket(ticket_id: int):
    ticket = db.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Обращение не найдено")
    scenario = knowledge.get_by_id(ticket["scenario_id"])
    return {
        **ticket,
        "answers": json.loads(ticket["answers"] or "[]"),
        "messages": db.get_messages(ticket_id),
        "steps_performed": (scenario or {}).get("steps", []),
    }


@app.get("/api/tickets/{ticket_id}/messages")
def ticket_messages(ticket_id: int):
    if not db.get_ticket(ticket_id):
        raise HTTPException(status_code=404, detail="Обращение не найдено")
    return db.get_messages(ticket_id)


@app.post("/api/tickets/{ticket_id}/resolve")
def resolve(ticket_id: int):
    if not db.get_ticket(ticket_id):
        raise HTTPException(status_code=404, detail="Обращение не найдено")
    db.update_ticket(ticket_id, status="resolved", state="resolved")
    db.add_message(ticket_id, "system", "Обращение закрыто: проблема решена")
    return {"ticket_id": ticket_id, "status": "resolved"}


@app.post("/api/tickets/{ticket_id}/escalate")
def escalate(ticket_id: int):
    if not db.get_ticket(ticket_id):
        raise HTTPException(status_code=404, detail="Обращение не найдено")
    card, reason = escalate_ticket(ticket_id)
    return {"ticket_id": ticket_id, "status": "escalated", "card": card}


# ---------- кабинет специалиста ----------

def require_support_code(x_support_code: str = Header(default=None)):
    code = (x_support_code or "").strip()
    if not code or code != SUPPORT_CODE:
        raise HTTPException(status_code=401, detail="Неверный код специалиста")
    return code


@app.get("/api/support/tickets")
def support_tickets(x_support_code: str = Header(default=None)):
    # оставим открытым для MVP-совместимости (старый фронт мог дёргать без кода)
    return db.list_tickets()


@app.get("/api/support/queue")
def support_queue(x_support_code: str = Header(default=None)):
    require_support_code(x_support_code)
    return db.queue_tickets()


@app.post("/api/support/tickets/{ticket_id}/take")
def support_take(ticket_id: int,
                  request: Request,
                  x_support_code: str = Header(default=None)):
    agent = require_support_code(x_support_code)
    ok = db.take_ticket_atomic(ticket_id, agent)
    if not ok:
        raise HTTPException(status_code=409,
                            detail="already taken")
    db.add_message(ticket_id, "system",
                   f"Специалист взял обращение в работу")
    return {"ticket_id": ticket_id, "status": "in_progress"}


@app.post("/api/support/tickets/{ticket_id}/reply")
def support_reply(ticket_id: int,
                  payload: dict,
                  x_support_code: str = Header(default=None)):
    require_support_code(x_support_code)
    if not db.get_ticket(ticket_id):
        raise HTTPException(status_code=404, detail="Обращение не найдено")
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="Пустой текст")
    if len(text) > 2000:
        raise HTTPException(status_code=422,
                            detail="Текст длиннее 2000 символов")
    db.add_message(ticket_id, "specialist", text)
    return {"ticket_id": ticket_id, "status": "replied"}


@app.post("/api/support/tickets/{ticket_id}/resolve_specialist")
def support_resolve(ticket_id: int,
                    x_support_code: str = Header(default=None)):
    require_support_code(x_support_code)
    ticket = db.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Обращение не найдено")
    if ticket["status"] not in ("escalated", "in_progress"):
        raise HTTPException(status_code=409,
                            detail="Нельзя решить из этого статуса")
    db.update_ticket(ticket_id, status="resolved_by_specialist",
                     state="resolved")
    db.add_message(ticket_id, "system",
                   "Обращение решено специалистом")
    return {"ticket_id": ticket_id, "status": "resolved_by_specialist"}


@app.get("/api/support/stats")
def support_stats(period: str = "day",
                  x_support_code: str = Header(default=None)):
    require_support_code(x_support_code)
    days = 7 if period == "week" else 1
    return db.support_stats(days)

@app.get("/api/support/archive")
def support_archive(x_support_code: str = Header(default=None)):
    require_support_code(x_support_code)
    return db.archive_tickets()


@app.delete("/api/support/tickets/{ticket_id}")
def support_delete(ticket_id: int,
                   x_support_code: str = Header(default=None)):
    require_support_code(x_support_code)
    ticket = db.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Обращение не найдено")
    if ticket["status"] not in ("resolved", "resolved_by_specialist",
                                "abandoned"):
        raise HTTPException(status_code=409,
                            detail="Можно удалять только закрытые обращения")
    db.delete_ticket(ticket_id)
    return {"status": "deleted", "ticket_id": ticket_id}


@app.post("/api/support/cleanup")
def support_cleanup(payload: dict,
                    x_support_code: str = Header(default=None)):
    require_support_code(x_support_code)
    days = int(payload.get("days", 7))
    if days < 1:
        days = 1
    removed = db.cleanup_resolved(days)
    return {"status": "ok", "removed": removed}
# ---------- обратная связь ----------

@app.post("/api/feedback")
def feedback(payload: FeedbackRequest):
    if not 1 <= payload.rating <= 5:
        raise HTTPException(status_code=400,
                            detail="Оценка должна быть от 1 до 5")
    if db.ticket_already_rated(payload.ticket_id):
        return {"status": "already", "support": False,
                "ticket_id": payload.ticket_id}
    db.add_feedback(payload.ticket_id, payload.rating, payload.comment or "")
    db.mark_rated(payload.ticket_id)
    support = False
    if payload.rating <= 2:
        ticket = db.get_ticket(payload.ticket_id)
        if ticket and ticket["status"] != "escalated":
            reason = (f"Низкая оценка ({payload.rating}/5): "
                      "пользователь не удовлетворён решением")
            if payload.comment:
                reason += ". Комментарий: " + payload.comment
            db.update_ticket(payload.ticket_id, status="escalated",
                             state="escalated", escalated_at=db.now())
            db.add_message(payload.ticket_id, "system",
                           "Обращение передано специалисту")
            db.add_message(payload.ticket_id, "system",
                           "Автоэскалация по оценке: " + reason)
            support = True
    return {"status": "saved", "support": support,
            "ticket_id": payload.ticket_id}


@app.get("/api/knowledge/search")
def search(q: str = ""):
    return knowledge.search(q)


# ---------- статика ----------
import os
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

FRONT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "frontend")


@app.get("/", include_in_schema=False)
def root_index():
    return FileResponse(os.path.join(FRONT_DIR, "index.html"))


@app.get("/support", include_in_schema=False)
def support_page():
    return FileResponse(os.path.join(FRONT_DIR, "index.html"))


if os.path.isdir(FRONT_DIR):
    app.mount("/", StaticFiles(directory=FRONT_DIR, html=True),
              name="frontend")