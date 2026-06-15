"""Follow-up chat for Case / Patient / Household / Facility AI panels."""

import json
import frappe
from uhis_next_core.ai.case_narrative import _build_context as _build_case_context
from uhis_next_core.ai.patient_narrative import _build_patient_context
from uhis_next_core.ai.household_narrative import _build_household_context
from uhis_next_core.ai.facility_narrative import _build_facility_context


_SYSTEM = (
    "You are a clinical supervisor assistant for a CHW programme in sub-Saharan Africa. "
    "Answer questions about the health record shown in JSON context below. "
    "Be concise (2-4 sentences unless more is needed), clinically precise, and avoid jargon. "
    "Never invent data not present in the context. Plain text only."
)

_MAX_CTX_CHARS = 3000
_CACHE_TTL     = 300   # seconds


@frappe.whitelist()
def ask_ai_stream(doctype, docname, question, history="[]"):
    """Enqueue a background response. Returns session_id for realtime + polling."""
    session_id   = frappe.generate_hash(length=16)
    history_list = json.loads(history) if isinstance(history, str) else (history or [])

    # Mark session as pending immediately so the client can start polling
    frappe.cache().set_value(
        f"uhis_chat_{session_id}",
        json.dumps({"status": "pending", "text": "", "done": False}),
        expires_in_sec=_CACHE_TTL,
    )

    frappe.enqueue(
        "uhis_next_core.ai.chat._run_response",
        doctype=doctype,
        docname=docname,
        question=question,
        history=history_list,
        session_id=session_id,
        user=frappe.session.user,
        queue="short",
        now=False,
    )
    return {"session_id": session_id}


@frappe.whitelist()
def get_chat_thread(doctype, docname):
    """Return the persisted conversation thread for this user + document."""
    key = f"{doctype}:{docname}:{frappe.session.user}"
    raw = frappe.db.get_value("AI Chat Thread", {"thread_key": key}, "thread")
    if not raw:
        return []
    try:
        return json.loads(raw)
    except Exception:
        return []


@frappe.whitelist()
def save_chat_thread(doctype, docname, thread):
    """Upsert the full conversation thread for this user + document."""
    key = f"{doctype}:{docname}:{frappe.session.user}"
    thread_list = json.loads(thread) if isinstance(thread, str) else (thread or [])
    thread_data = json.dumps(thread_list)
    existing = frappe.db.get_value("AI Chat Thread", {"thread_key": key}, "name")
    if existing:
        frappe.db.set_value("AI Chat Thread", existing, "thread", thread_data, update_modified=False)
    else:
        doc = frappe.new_doc("AI Chat Thread")
        doc.thread_key  = key
        doc.ref_doctype = doctype
        doc.ref_docname = docname
        doc.thread      = thread_data
        doc.insert(ignore_permissions=True)


@frappe.whitelist()
def poll_chat_status(session_id):
    """Client polls this until done=True."""
    raw = frappe.cache().get_value(f"uhis_chat_{session_id}")
    if not raw:
        return {"status": "missing", "text": "", "done": True}
    try:
        return json.loads(raw)
    except Exception:
        return {"status": "error", "text": str(raw), "done": True}


def _run_response(doctype, docname, question, history, session_id, user):
    event     = f"ai_chat:{session_id}"
    cache_key = f"uhis_chat_{session_id}"
    accumulated = ""

    def _emit(token=None, done=False, error=None):
        nonlocal accumulated
        if token:
            accumulated += token
        payload = {"status": "streaming", "text": accumulated, "done": done}
        if error:
            payload.update({"status": "error", "text": error, "done": True})
        # Write to cache (polling fallback)
        frappe.cache().set_value(cache_key, json.dumps(payload), expires_in_sec=_CACHE_TTL)
        # Also publish realtime (websocket path)
        rt_msg = {"done": done}
        if token:
            rt_msg["token"] = token
        if error:
            rt_msg["error"] = error
        frappe.publish_realtime(event=event, message=rt_msg, user=user)

    try:
        cfg = frappe.get_single("UHIS Settings")
        if not cfg.ai_insights_enabled:
            _emit(error="AI insights disabled", done=True)
            return

        provider = cfg.ai_provider or "Claude"
        model    = cfg.ai_model or "claude-haiku-4-5-20251001"
        try:
            key = cfg.get_password("ai_api_key") or ""
        except Exception:
            key = ""

        ctx  = _get_context(doctype, docname)
        msgs = _build_messages(ctx, history, question)

        if provider == "Claude":
            _stream_claude(key, model, msgs, _emit)
        else:
            base_url = cfg.ai_base_url if provider == "Local (OpenAI-compatible)" else None
            _stream_openai_compat(key, model, base_url, msgs, _emit)

    except Exception as exc:
        frappe.log_error(frappe.get_traceback(), "UHIS AI chat failed")
        _emit(error=str(exc), done=True)


def _stream_openai_compat(key, model, base_url, msgs, emit):
    from openai import OpenAI
    client = OpenAI(api_key=key or "not-needed", base_url=base_url or None)
    stream = client.chat.completions.create(
        model=model, messages=msgs, stream=True, temperature=0.4, max_tokens=512,
    )
    for chunk in stream:
        token = chunk.choices[0].delta.content or ""
        if token:
            emit(token=token)
    emit(done=True)


def _stream_claude(key, model, msgs, emit):
    import anthropic
    system    = next((m["content"] for m in msgs if m["role"] == "system"), _SYSTEM)
    chat_msgs = [m for m in msgs if m["role"] != "system"]
    client    = anthropic.Anthropic(api_key=key)
    with client.messages.stream(
        model=model, max_tokens=512, system=system, messages=chat_msgs,
    ) as stream:
        for text in stream.text_stream:
            emit(token=text)
    emit(done=True)


def _get_context(doctype, docname):
    builders = {
        "Case":      lambda: _build_case_context(docname),
        "Patient":   lambda: _build_patient_context(docname),
        "Household": lambda: _build_household_context(docname),
        "Facility":  lambda: _build_facility_context(docname),
    }
    builder = builders.get(doctype)
    if not builder:
        return {"doctype": doctype, "docname": docname}
    ctx = builder()
    for k in ("case_summaries", "member_profiles", "top_patient_summaries"):
        if k in ctx:
            ctx[k] = [s[:400] if isinstance(s, str) else s for s in ctx[k]]
    return ctx


def _build_messages(ctx, history, question):
    ctx_str = json.dumps(ctx, default=str)
    if len(ctx_str) > _MAX_CTX_CHARS:
        ctx_str = ctx_str[:_MAX_CTX_CHARS] + "…"
    messages = [
        {"role": "system", "content": f"{_SYSTEM}\n\nHealth record context:\n{ctx_str}"},
    ]
    for turn in (history or []):
        if turn.get("role") in ("user", "assistant") and turn.get("content"):
            messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": question})
    return messages
