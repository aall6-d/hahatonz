import json
from fastapi import FastAPI, HTTPException
...
app = FastAPI(title="Актион Ассист API")
import json
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app import db, knowledge, classifier
from app.schemas import ChatRequest, ChatResponse, FeedbackRequest

app = FastAPI(title="Актион Ассист API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- вспомогательные функции ----------

def is_yes(text: str) -> bool:
    t = (text or "").strip().lower()
    return t.startswith("да") or "помогло" in t or "решено" in t


def is_no(text: str) -> bool:
    t = (text or "").strip().lower()
    return t.startswith("нет") or "не помогло" in t or "не решено" in t


def respond(ticket_id, type_, message, category=None, confidence=None,
            options=None, steps=None, success_question=None,
            solution_id=None, status=None):
    db.add_message(ticket_id, "assistant", message)
    return ChatResponse(
        ticket_id=ticket_id, type=type_, category=category,
        confidence=confidence, message=message, options=options or [],
        steps=steps or [], success_question=success_question,
        solution_id=solution_id, status=status,
    )


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
                   cat, conf, steps=scenario["steps"],
                   success_question=scenario["success_question"],
                   solution_id=scenario["id"])


def start_diagnosis(ticket_id, text, cat, conf):
    scenario = knowledge.find_scenario(cat, text)
    if not scenario:
        db.update_ticket(ticket_id, state="clarification")
        return respond(ticket_id, "clarification",
                       "Уточните, пожалуйста, что именно не работает?",
                       cat, conf, options=knowledge.CATEGORIES)
    db.update_ticket(ticket_id, state="awaiting_question", category=cat,
                     confidence=conf, scenario_id=scenario["id"],
                     question_index=0, title=scenario["title"])
    intro = (f"Я понял, в чём проблему. Категория: {cat}. "
             f"Уверенность: {int(conf * 100)}%.")
    db.add_message(ticket_id, "assistant", intro)
    return ask_or_solve(ticket_id, scenario, cat, conf, 0)


def escalate_ticket(ticket_id):
    ticket = db.get_ticket(ticket_id)
    scenario = knowledge.get_by_id(ticket["scenario_id"])
    reason = (scenario or {}).get("escalation_reason",
                                  "Стандартные решения не помогли")
    db.update_ticket(ticket_id, status="escalated", state="escalated")
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


# ---------- запуск ----------

@app.on_event("startup")
def startup():
    db.init_db()


# ---------- ручки (endpoints) ----------

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/chat", response_model=ChatResponse)
def chat(payload: ChatRequest):
    text = (payload.message or payload.answer or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Пустое сообщение")

    ticket_id = payload.ticket_id
    state = "new"
    if ticket_id:
        ticket = db.get_ticket(ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="Обращение не найдено")
        state = ticket["state"]
        if state in ("resolved", "escalated"):
            ticket_id = db.create_ticket(text)
            state = "new"
    else:
        ticket_id = db.create_ticket(text)

    db.add_message(ticket_id, "user", text)

    if state in ("new", "clarification"):
        cat, conf = classifier.classify(text)
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
        return start_diagnosis(ticket_id, text, cat, conf)

    if state == "awaiting_question":
        db.add_answer(ticket_id, text)
        ticket = db.get_ticket(ticket_id)
        scenario = knowledge.get_by_id(ticket["scenario_id"])
        if not scenario:
            db.update_ticket(ticket_id, state="clarification")
            return respond(ticket_id, "clarification",
                           "Уточните, что именно не работает?",
                           ticket["category"], ticket["confidence"],
                           options=knowledge.CATEGORIES)
        return ask_or_solve(ticket_id, scenario, ticket["category"],
                            ticket["confidence"],
                            ticket["question_index"] + 1)

    if state == "awaiting_result":
        ticket = db.get_ticket(ticket_id)
        if is_yes(text):
            db.update_ticket(ticket_id, status="resolved", state="resolved")
            db.add_message(ticket_id, "system",
                           "Обращение закрыто: проблема решена")
            return respond(ticket_id, "resolved",
                           f"Отлично! Проблема решена. "
                           f"Обращение #{ticket_id} закрыто. "
                           "Оцените, пожалуйста, мою помощь.",
                           ticket["category"], ticket["confidence"],
                           status="resolved")
        if is_no(text):
            db.update_ticket(ticket_id, state="escalation_offer")
            return respond(ticket_id, "escalation",
                           "Похоже, стандартные способы не помогли. Я передам "
                           "обращение специалисту вместе со всей историей "
                           "диагностики.",
                           ticket["category"], ticket["confidence"],
                           options=["Передать специалисту"],
                           status="in_progress")
        return respond(ticket_id, "question",
                       "Подскажите, получилось выполнить шаги? "
                       "Ответьте «да» или «нет».",
                       ticket["category"], ticket["confidence"],
                       options=["Да", "Нет"])

    if state == "escalation_offer":
        if is_yes(text):
            card, reason = escalate_ticket(ticket_id)
            return respond(ticket_id, "escalated",
                           f"Обращение #{ticket_id} передано специалисту. "
                           f"Причина: {reason}",
                           None, None, status="escalated")
        db.update_ticket(ticket_id, state="new")
        return respond(ticket_id, "clarification",
                       "Опишите, что именно происходит сейчас — "
                       "я попробую подобрать другой способ.", None, None)

    db.update_ticket(ticket_id, state="new")
    return respond(ticket_id, "clarification",
                   "Опишите проблему подробнее.", None, None)


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

@app.get("/api/support/tickets")
def support_queue():
    return db.list_tickets()


@app.post("/api/feedback")
def feedback(payload: FeedbackRequest):
    if not 1 <= payload.rating <= 5:
        raise HTTPException(status_code=400,
                            detail="Оценка должна быть от 1 до 5")
    db.add_feedback(payload.ticket_id, payload.rating, payload.comment or "")
    return {"status": "saved"}


@app.get("/api/knowledge/search")
def search(q: str = ""):
    return knowledge.search(q)