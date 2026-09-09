// UHIS shared AI panel — typewriter reveal + persistent follow-up chat
// Loaded globally via app_include_js

// ── Inject keyframe animations once ──────────────────────────────────────────
(function() {
	if (document.getElementById("uhis-keyframes")) return;
	const s = document.createElement("style");
	s.id = "uhis-keyframes";
	s.textContent =
		"@keyframes uhis-blink{0%,60%,100%{opacity:.2;transform:scale(.8)}30%{opacity:1;transform:scale(1.2)}}" +
		"@keyframes uhis-cursor{0%,100%{opacity:1}50%{opacity:0}}";
	document.head.appendChild(s);
})();

// ── Text highlighter ──────────────────────────────────────────────────────────
window.uhis_highlight_ai_text = function(text) {
	if (!text) return "";
	let h = frappe.utils.escape_html(text);
	h = h.replace(/\b(\d+(?:\.\d+)?)\s*(mmHg|mmol\/L|%|kg\/m²|kg|cm)\b/g,
		'<strong style="color:#1D4ED8">$1</strong><span style="color:#94A3B8;font-size:11px"> $2</span>');
	h = h.replace(/\b(high risk|uncontrolled|urgent|critical|severe|elevated|overdue|poorly controlled)\b/gi,
		'<strong style="color:#DC2626">$1</strong>');
	h = h.replace(/\b(moderate|requires? attention|warrants?|concerning)\b/gi,
		'<strong style="color:#EA580C">$1</strong>');
	h = h.replace(/\b(stable|normal|well-controlled|controlled|improving)\b/gi,
		'<strong style="color:#16A34A">$1</strong>');
	h = h.replace(/(Recommended action|Next action|Priority action|Most important recommended action)/gi,
		'<strong style="color:#0E7490">$1</strong>');
	return h;
};

// ── Typewriter reveal (summary panel only) ────────────────────────────────────
window.uhis_typewriter = function(el, plainText, onDone, msPerWord) {
	if (!plainText) { if (onDone) onDone(); return; }
	const speed = msPerWord || 22;
	const words = plainText.split(/(\s+)/);
	let i = 0, buf = "";
	el.innerHTML = "";
	function tick() {
		if (i >= words.length) {
			el.innerHTML = window.uhis_highlight_ai_text(plainText);
			if (onDone) onDone();
			return;
		}
		buf += words[i++];
		el.textContent = buf;
		setTimeout(tick, speed + (Math.random() * speed * 0.4 - speed * 0.2));
	}
	tick();
};

// ── Persistent chat history ───────────────────────────────────────────────────
// Stored in localStorage keyed by "Doctype:docname", capped at 12 messages.

window._uhis_chat_history = {};
const _HIST_PREFIX  = "uhis_chat_hist_";
const _HIST_MAX_MSG = 12;   // 6 turns

function _hist_key(doctype, docname) { return doctype + ":" + docname; }

function _hist_load(doctype, docname) {
	const k = _hist_key(doctype, docname);
	if (window._uhis_chat_history[k]) return window._uhis_chat_history[k];
	// Seed from localStorage cache while DB fetch is in-flight
	try {
		const raw = localStorage.getItem(_HIST_PREFIX + k);
		window._uhis_chat_history[k] = raw ? JSON.parse(raw) : [];
	} catch(e) {
		window._uhis_chat_history[k] = [];
	}
	return window._uhis_chat_history[k];
}

function _hist_load_from_db(doctype, docname, onDone) {
	frappe.call({
		method: "spice_next_core.ai.chat.get_chat_thread",
		args: { doctype, docname },
		callback: function(r) {
			const msgs = (r && Array.isArray(r.message)) ? r.message : [];
			window._uhis_chat_history[_hist_key(doctype, docname)] = msgs;
			// Sync local cache too
			try { localStorage.setItem(_HIST_PREFIX + _hist_key(doctype, docname), JSON.stringify(msgs)); } catch(e) {}
			if (onDone) onDone(msgs);
		},
		error: function() {
			// DB unreachable — fall back to whatever localStorage has
			_hist_load(doctype, docname);
			if (onDone) onDone(window._uhis_chat_history[_hist_key(doctype, docname)] || []);
		}
	});
}

function _hist_save(doctype, docname) {
	const k = _hist_key(doctype, docname);
	const thread = window._uhis_chat_history[k] || [];
	// Keep localStorage in sync as a fast local cache
	try { localStorage.setItem(_HIST_PREFIX + k, JSON.stringify(thread)); } catch(e) {}
	// Persist to Frappe DB (fire-and-forget)
	frappe.call({
		method: "spice_next_core.ai.chat.save_chat_thread",
		args: { doctype, docname, thread: JSON.stringify(thread) }
	});
}

function _uhis_push_history(doctype, docname, role, content) {
	if (!content || !content.trim()) return;   // never save empty bubbles
	const k = _hist_key(doctype, docname);
	_hist_load(doctype, docname);
	window._uhis_chat_history[k].push({ role, content });
	if (window._uhis_chat_history[k].length > _HIST_MAX_MSG) {
		window._uhis_chat_history[k].splice(0, 2);
	}
	_hist_save(doctype, docname);
}

// Render saved history bubbles into the message list
function _hist_render(doctype, docname) {
	const msgs = _hist_load(doctype, docname);
	if (!msgs.length) return;
	const container = document.getElementById("uhis-chat-msgs-" + docname);
	if (!container) return;
	msgs.forEach(function(m) {
		const div = document.createElement("div");
		div.style.cssText = _S.bubble_base + (m.role === "user" ? _S.user : _S.asst);
		if (m.role === "assistant") {
			div.innerHTML = "&#x2756;&nbsp;" + window.uhis_highlight_ai_text(m.content);
		} else {
			div.textContent = m.content;
		}
		container.appendChild(div);
	});
	container.scrollTop = container.scrollHeight;
}

// ── Inline style constants ────────────────────────────────────────────────────
const _S = {
	bubble_base: "max-width:88%;padding:9px 13px;font-size:13px;line-height:1.6;word-break:break-word;",
	user:    "align-self:flex-end;background:#0E7490;color:#fff;border-radius:18px 18px 4px 18px;margin-top:6px;",
	asst:    "align-self:flex-start;background:transparent;color:#1E293B;padding:9px 13px 9px 4px;",
	msgs:    "max-height:300px;overflow-y:auto;display:flex;flex-direction:column;gap:2px;padding:0 0 8px;scroll-behavior:smooth;",
	row:     "display:flex;align-items:center;margin-top:10px;background:#F8FAFC;border:1.5px solid #E2E8F0;border-radius:14px;padding:4px 4px 4px 14px;",
	input:   "flex:1;border:none;background:transparent;outline:none;font-size:13px;color:#1E293B;padding:5px 0;min-width:0;font-family:inherit;",
	btn:     "flex-shrink:0;width:32px;height:32px;display:flex;align-items:center;justify-content:center;border-radius:10px;background:#0E7490;color:#fff;border:none;cursor:pointer;font-size:16px;line-height:1;",
	section: "margin-top:14px;border-top:1px solid rgba(226,232,240,0.8);padding-top:10px;",
	cursor:  "display:inline-block;width:2px;height:0.85em;background:#0E7490;margin-left:2px;vertical-align:text-bottom;animation:uhis-cursor 0.8s step-end infinite;border-radius:1px;",
};

// ── Bubble helpers ────────────────────────────────────────────────────────────
function _bubble(docname, role, text) {
	const container = document.getElementById("uhis-chat-msgs-" + docname);
	if (!container) return null;
	const div = document.createElement("div");
	div.style.cssText = _S.bubble_base + (role === "user" ? _S.user : _S.asst);
	if (role === "assistant") {
		div.innerHTML = "&#x2756;&nbsp;" + window.uhis_highlight_ai_text(text);
	} else {
		div.textContent = text;
	}
	container.appendChild(div);
	container.scrollTop = container.scrollHeight;
	return div;
}

// Create an assistant bubble that supports append-only text streaming
function _stream_bubble(docname) {
	const container = document.getElementById("uhis-chat-msgs-" + docname);
	if (!container) return null;
	const div = document.createElement("div");
	div.style.cssText = _S.bubble_base + _S.asst;

	// ✦ icon
	const icon = document.createElement("span");
	icon.innerHTML = "&#x2756;&nbsp;";
	icon.style.cssText = "color:#0E7490;flex-shrink:0;";
	div.appendChild(icon);

	// text node — we only append to this, never replace
	const textNode = document.createTextNode("");
	div.appendChild(textNode);

	// blinking cursor
	const cursor = document.createElement("span");
	cursor.style.cssText = _S.cursor;
	div.appendChild(cursor);

	container.appendChild(div);
	container.scrollTop = container.scrollHeight;
	return { el: div, textNode, cursor, container };
}

function _show_thinking(docname) {
	const container = document.getElementById("uhis-chat-msgs-" + docname);
	if (!container) return;
	const div = document.createElement("div");
	div.id = "uhis-thinking-" + docname;
	div.style.cssText = "display:flex;gap:5px;align-items:center;padding:10px 0 10px 4px;";
	div.innerHTML =
		'<span style="font-size:11px;color:#94A3B8;margin-right:2px;">Thinking</span>' +
		[0,1,2].map(function(i) {
			return '<span style="width:5px;height:5px;border-radius:50%;background:#94A3B8;display:inline-block;' +
				'animation:uhis-blink 1.2s ease-in-out ' + (i*0.2) + 's infinite;"></span>';
		}).join("");
	container.appendChild(div);
	container.scrollTop = container.scrollHeight;
}

function _hide_thinking(docname) {
	var el = document.getElementById("uhis-thinking-" + docname);
	if (el) el.remove();
}

// ── Chat send ─────────────────────────────────────────────────────────────────
window.uhis_chat_send = function(doctype, docname) {
	const input = document.getElementById("uhis-chat-input-" + docname);
	if (!input) return;
	const question = (input.value || "").trim();
	if (!question) return;

	input.value = "";
	input.disabled = true;
	const btn = document.getElementById("uhis-chat-send-btn-" + docname);
	if (btn) { btn.disabled = true; btn.style.background = "#CBD5E1"; }

	_bubble(docname, "user", question);
	_uhis_push_history(doctype, docname, "user", question);
	_show_thinking(docname);

	// Send full history minus the user message we just pushed (backend re-appends it)
	const history = _hist_load(doctype, docname).slice(0, -1);

	frappe.call({
		method: "spice_next_core.ai.chat.ask_ai_stream",
		args: { doctype, docname, question, history: JSON.stringify(history) },
		callback: function(r) {
			if (!r.message || !r.message.session_id) {
				_hide_thinking(docname);
				_bubble(docname, "assistant", "Sorry, could not reach the AI service.");
				_re_enable(docname);
				return;
			}

			const session_id = r.message.session_id;
			let   stream_ref = null;   // {el, textNode, cursor, container}
			let   last_len   = 0;      // chars already appended to textNode
			let   finished   = false;
			let   poll_id    = null;

			function _get_stream() {
				if (!stream_ref) {
					_hide_thinking(docname);
					stream_ref = _stream_bubble(docname);
				}
				return stream_ref;
			}

			// Append only NEW characters — never replace existing text
			function _append_delta(full_text) {
				if (finished || !full_text) return;
				const s = _get_stream();
				if (!s) return;
				const delta = full_text.slice(last_len);
				if (!delta) return;
				last_len = full_text.length;
				s.textNode.nodeValue += delta;
				s.container.scrollTop = s.container.scrollHeight;
			}

			function _done(full_text) {
				if (finished) return;
				finished = true;
				clearInterval(poll_id);
				try { frappe.realtime.off("ai_chat:" + session_id); } catch(e) {}
				_hide_thinking(docname);
				// Prefer full_text from polling cache (always complete); fall back to DOM textNode
				const finalText = (full_text && full_text.trim())
					? full_text
					: (stream_ref ? stream_ref.textNode.nodeValue : "");
				const s = _get_stream();
				if (s) {
					if (finalText) {
						s.el.innerHTML = "&#x2756;&nbsp;" + window.uhis_highlight_ai_text(finalText);
					} else if (s.cursor) {
						s.cursor.remove();  // just drop the blinking cursor, keep whatever streamed
					}
					s.container.scrollTop = s.container.scrollHeight;
				}
				_uhis_push_history(doctype, docname, "assistant", finalText);
				_re_enable(docname);
			}

			// Realtime (websocket) — token display only; never triggers _done
			// Polling is the sole authority for completion so history is always saved
			// with the full text from the cache (not a potentially-empty textNode).
			try {
				frappe.realtime.on("ai_chat:" + session_id, function(data) {
					if (data.token) {
						const current = stream_ref ? stream_ref.textNode.nodeValue : "";
						_append_delta(current + data.token);
					}
					// data.done and data.error are intentionally ignored here
				});
			} catch(e) {}

			// Polling (every 1.5s) — sole authority for done/error state
			poll_id = setInterval(function() {
				if (finished) { clearInterval(poll_id); return; }
				frappe.call({
					method: "spice_next_core.ai.chat.poll_chat_status",
					args: { session_id },
					callback: function(pr) {
						if (!pr || !pr.message || finished) return;
						const d = pr.message;
						if (d.status === "error") { _done(d.text || "Sorry, an error occurred."); return; }
						if (d.text && d.text.length > last_len) _append_delta(d.text);
						if (d.done && d.text) _done(d.text);
					},
				});
			}, 1500);

			setTimeout(function() {
				if (!finished) _done(stream_ref ? stream_ref.textNode.nodeValue : "");
			}, 120000);
		},
		error: function() {
			_hide_thinking(docname);
			_bubble(docname, "assistant", "Could not reach server.");
			_re_enable(docname);
		},
	});
};

function _re_enable(docname) {
	const input = document.getElementById("uhis-chat-input-" + docname);
	if (input) { input.disabled = false; input.focus(); }
	const btn = document.getElementById("uhis-chat-send-btn-" + docname);
	if (btn) { btn.disabled = false; btn.style.background = "#0E7490"; }
}

// ── Chat wiring ───────────────────────────────────────────────────────────────
function _uhis_wire_chat(doctype, docname) {
	const btn   = document.getElementById("uhis-chat-send-btn-" + docname);
	const input = document.getElementById("uhis-chat-input-" + docname);
	const row   = document.getElementById("uhis-chat-input-row-" + docname);

	if (btn) {
		btn.onclick      = function() { window.uhis_chat_send(doctype, docname); };
		btn.onmouseenter = function() { if (!this.disabled) this.style.background = "#0C6678"; };
		btn.onmouseleave = function() { if (!this.disabled) this.style.background = "#0E7490"; };
	}
	if (input && row) {
		input.onfocus  = function() { row.style.borderColor="#0E7490"; row.style.boxShadow="0 0 0 3px rgba(14,116,144,0.12)"; row.style.background="#fff"; };
		input.onblur   = function() { row.style.borderColor="#E2E8F0"; row.style.boxShadow="none"; row.style.background="#F8FAFC"; };
		input.onkeydown = function(e) { if (e.key==="Enter"&&!e.shiftKey){ e.preventDefault(); window.uhis_chat_send(doctype, docname); } };
	}

	// Load conversation history from DB (localStorage seeded as fallback while loading)
	_hist_load_from_db(doctype, docname, function() {
		_hist_render(doctype, docname);
	});
}

// ── Panel renderers ───────────────────────────────────────────────────────────

window.uhis_render_ai_panel = function(frm, fieldName, d, opts) {
	const field   = frm.get_field(fieldName);
	if (!field) return;
	const label   = (opts && opts.label)   || "AI Summary";
	const doctype = (opts && opts.doctype) || frm.doctype;
	const docname = frm.doc.name;

	if (!d.ai_insights_enabled) {
		field.$wrapper.html(_panel_no_config(label));
		return;
	}
	if (!d.ai_summary || !d.ai_summary_generated_at) {
		field.$wrapper.html(_panel_generating(label, docname));
		return;
	}
	const parts      = d.ai_summary.split("\n\n— Generated ");
	const meta_text  = parts[1] ? "Generated " + parts[1] : "";
	field.$wrapper.html(_panel_ready(label, docname, meta_text));

	const text_el = document.getElementById("uhis-ai-text-" + docname);
	if (text_el) window.uhis_typewriter(text_el, parts[0], null, 20);

	_uhis_wire_chat(doctype, docname);
};

window.uhis_init_case_ai = function(frm, d) {
	if (!d.ai_insights_enabled || !d.ai_summary) return;
	const docname = frm.doc.name;
	const text_el = document.getElementById("uhis-ai-text-" + docname);
	if (text_el) {
		const parts = d.ai_summary.split("\n\n— Generated ");
		window.uhis_typewriter(text_el, parts[0], null, 20);
	}
	_uhis_wire_chat("Case", docname);
};

// ── Panel HTML ────────────────────────────────────────────────────────────────
function _panel_no_config(label) {
	return `<div class="uhis-ai-panel">
		<div class="uhis-ai-panel-header"><span class="uhis-ai-label">🤖 ${frappe.utils.escape_html(label)}</span></div>
		<span class="uhis-ai-configure-note">AI insights available — configure API key in <a href="/app/uhis-settings" target="_blank">UHIS Settings</a></span>
	</div>`;
}
function _panel_generating(label, docname) {
	return `<div class="uhis-ai-panel" id="uhis-pt-ai-panel-${docname}">
		<div class="uhis-ai-panel-header"><span class="uhis-ai-label">🤖 ${frappe.utils.escape_html(label)}</span></div>
		<span class="uhis-ai-placeholder">Generating summary…</span>
	</div>`;
}
function _panel_ready(label, docname, meta_text) {
	return `<div class="uhis-ai-panel">
		<div class="uhis-ai-panel-header">
			<span class="uhis-ai-label">🤖 ${frappe.utils.escape_html(label)}</span>
			<button class="uhis-btn-ai" id="uhis-regen-btn-${docname}">↺ Regenerate</button>
		</div>
		<div class="uhis-ai-text" id="uhis-ai-text-${docname}"></div>
		${meta_text ? `<div class="uhis-ai-meta">${frappe.utils.escape_html(meta_text)}</div>` : ""}
		<div id="uhis-chat-section-${docname}" style="${_S.section}">
			<div id="uhis-chat-msgs-${docname}" style="${_S.msgs}"></div>
			<div id="uhis-chat-input-row-${docname}" style="${_S.row}">
				<input id="uhis-chat-input-${docname}" type="text"
					placeholder="Ask a follow-up question…" autocomplete="off"
					style="${_S.input}" />
				<button id="uhis-chat-send-btn-${docname}" title="Send" style="${_S.btn}">&#x2191;</button>
			</div>
		</div>
	</div>`;
}
