"""
Multi-provider AI client for UHIS Next.
Dispatches to Claude (Anthropic), OpenAI, or a local OpenAI-compatible endpoint
(Ollama, LM Studio, etc.) based on UHIS Settings.ai_provider.

Returns None when AI insights are disabled or no API key is configured so callers
can treat a None result as a graceful no-op.
"""

import frappe


def call_ai(system_prompt, user_content):
    """Call the configured AI provider and return the response text, or None."""
    cfg = frappe.get_single("UHIS Settings")
    if not cfg.ai_insights_enabled:
        return None

    provider = cfg.ai_provider or "Claude"
    model    = cfg.ai_model or "claude-haiku-4-5-20251001"
    try:
        key = cfg.get_password("ai_api_key") or ""
    except Exception:
        key = ""

    try:
        if provider == "Claude":
            return _call_claude(key, model, system_prompt, user_content)
        else:
            base_url = cfg.ai_base_url if provider == "Local (OpenAI-compatible)" else None
            return _call_openai_compat(key, model, base_url, system_prompt, user_content)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "UHIS AI call failed")
        return None


def _call_claude(api_key, model, system_prompt, user_content):
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=model,
        max_tokens=300,
        system=system_prompt,
        messages=[{"role": "user", "content": user_content}],
    )
    return msg.content[0].text


def _call_openai_compat(api_key, model, base_url, system_prompt, user_content):
    from openai import OpenAI
    client = OpenAI(
        api_key=api_key or "not-needed",
        base_url=base_url or None,
    )
    resp = client.chat.completions.create(
        model=model,
        max_tokens=300,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_content},
        ],
    )
    return resp.choices[0].message.content
