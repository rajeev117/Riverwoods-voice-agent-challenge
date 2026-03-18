from flask import Flask, request, jsonify, abort, Response
import os
from task_runner import send_sms, make_call
from dotenv import load_dotenv
load_dotenv()
import requests
from conversation_store import ConversationStore
from threading import Thread
from session_store import SessionStore

try:
    from twilio.twiml.voice_response import VoiceResponse, Gather
except Exception:
    VoiceResponse = None
    Gather = None

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

try:
    from pinecone import Pinecone
except Exception:
    Pinecone = None

app = Flask(__name__)

API_KEY = os.environ.get("INTERNAL_API_KEY", None)
N8N_WEBHOOK = os.environ.get("N8N_WEBHOOK", None)
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", None)
oai_client = OpenAI(api_key=OPENAI_API_KEY) if (OpenAI and OPENAI_API_KEY) else None

store = ConversationStore()

PINECONE_API_KEY = os.environ.get("PINECONE_API_KEY")
PINECONE_HOST = os.environ.get("PINECONE_HOST", "")
PINECONE_INDEX = os.environ.get("PINECONE_INDEX")
PINECONE_NAMESPACE_DOCS = os.environ.get("PINECONE_NAMESPACE_DOCS", "")
PINECONE_TOP_K = int(os.environ.get("PINECONE_TOP_K", "3"))
PINECONE_ORG_ID = os.environ.get("PINECONE_ORG_ID", "")
PINECONE_DOC_ENV = os.environ.get("PINECONE_DOC_ENV", "prod")
PINECONE_TEXT_FIELD = os.environ.get("PINECONE_TEXT_FIELD", "text")
pc = Pinecone(api_key=PINECONE_API_KEY) if (Pinecone and PINECONE_API_KEY) else None

def _pinecone_filter():
    flt = {}
    if PINECONE_ORG_ID:
        flt["org_id"] = {"$eq": PINECONE_ORG_ID}
    if PINECONE_DOC_ENV:
        flt["env"] = {"$eq": PINECONE_DOC_ENV}
    return flt or None

def embed_text_for_pinecone(text: str):
    if not oai_client:
        return None
    txt = (text or "").strip()
    if not txt:
        return None
    model = os.environ.get("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
    try:
        r = oai_client.embeddings.create(model=model, input=txt)
        return r.data[0].embedding
    except Exception:
        return None

def _extract_match_text(metadata: dict) -> str:
    if not metadata:
        return ""
    preferred = (PINECONE_TEXT_FIELD or "").strip()
    if preferred:
        v = metadata.get(preferred)
        if isinstance(v, str) and v.strip():
            return v.strip()

    for k in ("text", "content", "chunk", "body", "chunk_text"):
        v = metadata.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""

def retrieve_context_from_pinecone(query_text: str) -> str:
    if not pc or not PINECONE_INDEX:
        return ""
    emb = embed_text_for_pinecone(query_text)
    if not emb:
        return ""
    try:
        idx = pc.Index(name=PINECONE_INDEX, host=(PINECONE_HOST or ""))
        res = idx.query(
            vector=emb,
            top_k=PINECONE_TOP_K,
            namespace=(PINECONE_NAMESPACE_DOCS or None),
            include_metadata=True,
            filter=_pinecone_filter(),
        )
        matches = res.get("matches") if isinstance(res, dict) else getattr(res, "matches", None)
    except Exception:
        return ""

    parts = []
    for m in matches or []:
        md = m.get("metadata") if isinstance(m, dict) else getattr(m, "metadata", None)
        md = md or {}
        text = _extract_match_text(md)
        if not text:
            continue
        doc = md.get("doc_name") or md.get("source") or md.get("file") or ""
        page = md.get("page")
        header = doc
        if page is not None and str(page).strip() != "":
            header = f"{doc} (page {page})" if doc else f"page {page}"
        if header:
            parts.append(f"SOURCE: {header}\n{text}")
        else:
            parts.append(text)

    context = "\n\n---\n\n".join(parts)
    max_chars = int(os.environ.get("PINECONE_CONTEXT_MAX_CHARS", "2000"))
    if len(context) > max_chars:
        context = context[:max_chars] + "\n\n[context truncated]"
    return context

session_store = SessionStore()

def get_session(call_sid: str):
    return session_store.get(call_sid)

def require_api_key(req):
    if API_KEY is None:
        return True
    key = req.headers.get("Authorization") or req.headers.get("X-API-Key")
    if not key:
        return False
    if key.startswith("Bearer "):
        key = key.split(" ", 1)[1]
    return key == API_KEY

@app.route("/internal/send", methods=["POST"])
def internal_send():
    if not require_api_key(request):
        abort(401)
    data = request.get_json(force=True)
    action = data.get("action")
    to = data.get("to")
    message = data.get("message", "")
    twiml_url = data.get("twiml_url", "")

    if not action or not to:
        return jsonify({"error":"missing action or to"}), 400

    call_context = {}
    for key in ("client_name", "project_name", "progress_update"):
        val = (data.get(key) or "").strip()
        if val:
            call_context[key] = val

    try:
        if action == "sms":
            result = send_sms(to, message)
            return jsonify({"ok": True, "result": result}), 200
        elif action == "call":
            result = make_call(to, message, twiml_url)
            call_sid = result.get("sid")
            if call_sid and call_context:
                sess = get_session(call_sid)
                sess["call_context"] = call_context
                session_store.save(call_sid, sess)
            return jsonify({"ok": True, "result": result}), 200
        else:
            return jsonify({"error":"unknown action"}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/internal/conversation/<call_sid>", methods=["GET"])
def internal_conversation(call_sid):
    if not require_api_key(request):
        abort(401)
    if not store.enabled:
        return jsonify({"ok": False, "error": "mongo_disabled"}), 503

    include_turns = (request.args.get("include_turns") or "").strip().lower() in ("1", "true", "yes")
    doc = store.get_call_document(call_sid, include_turns=include_turns)
    if not doc:
        return jsonify({"ok": False, "error": "not_found"}), 404
    return jsonify({"ok": True, "conversation": doc}), 200

def push_transcript_to_n8n(payload):
    if not N8N_WEBHOOK:
        return
    try:
        requests.post(N8N_WEBHOOK, json=payload, timeout=2)
    except Exception:
        pass

@app.route("/twilio/status", methods=["POST"])
def twilio_status():
    data = request.form.to_dict() if request.form else {}

    call_sid = data.get("CallSid")
    call_status = (data.get("CallStatus") or "").lower()
    from_number = data.get("From")
    to_number = data.get("To")

    if call_sid:
        store.ensure_call(call_sid, from_number=from_number, to_number=to_number)
        if call_status:
            store.update_status(call_sid, call_status)

    terminal_statuses = {"completed", "busy", "failed", "no-answer", "canceled"}

    def _save_conversation_on_end(sid: str, session_snapshot: dict):
        try:
            messages = session_snapshot.get("messages") or []
            call_context = session_snapshot.get("call_context") or {}

            # Build turns from session message history
            turns = []
            for m in messages:
                if m.get("role") in ("user", "assistant"):
                    turns.append({
                        "role": m["role"],
                        "text": m["content"],
                        "channel": "voice",
                    })

            store.save_full_conversation(sid, turns=turns, call_context=call_context)
            if oai_client and turns:
                transcript_lines = [f"{t['role']}: {t['text']}" for t in turns]
                transcript_text = "\n".join(transcript_lines)
                structured = extract_structured_response(oai_client, transcript_text)
                if structured:
                    store.save_structured_response(sid, structured)
        except Exception as e:
            print("save_conversation_on_end error:", repr(e))

    if call_sid and call_status in terminal_statuses:
        sess_snapshot = session_store.get(call_sid)
        has_messages = bool((sess_snapshot or {}).get("messages"))

        store.mark_ended(call_sid, status=call_status, retention_hours=24)

        if has_messages:
            Thread(target=_save_conversation_on_end, args=(call_sid, dict(sess_snapshot)), daemon=True).start()

        session_store.clear(call_sid)

    return ("", 204)

TTS_VOICE = os.environ.get("TTS_VOICE", "Polly.Joanna")

def generate_reply_with_openai(messages):
    if not oai_client:
        return None
    try:
        resp = oai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            temperature=0.75,
            max_tokens=80,
            timeout=8,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print("OpenAI error:", e)
        return None

def generate_reply_fallback(user_text):
    txt = (user_text or "").strip()
    if not txt:
        return "Sorry, I didn't catch that. Could you say that again?"
    return "I think I missed part of that. Could you repeat it for me?"

@app.route("/voice", methods=["POST", "GET"])
def voice():
    if not VoiceResponse:
        return Response("Twilio library not installed", mimetype="text/plain"), 500
    call_sid = request.form.get("CallSid") or request.args.get("CallSid")
    sess = get_session(call_sid)
    resp = VoiceResponse()

    answered_by = (request.form.get("AnsweredBy") or "").strip().lower()
    if answered_by in ("machine_start", "machine_end_beep", "machine_end_silence", "fax"):
        ctx = sess.get("call_context") or {}
        client_name = ctx.get("client_name", "")
        project_name = ctx.get("project_name", "")
        vm_msg = f"Hi{' ' + client_name if client_name else ''}. "
        vm_msg += f"This is a follow-up call about {project_name}. " if project_name else "This is a follow-up call. "
        vm_msg += "Please call us back at your convenience. Thank you."
        resp.say(vm_msg, voice=TTS_VOICE)
        resp.hangup()
        if call_sid:
            store.save_structured_response(call_sid, {
                "visit_intent": "voicemail",
                "visit_date": "",
                "next_action": "callback_requested",
                "notes": "Voicemail left by AI agent",
            })
        return Response(str(resp), mimetype="application/xml")

    gather = Gather(input="speech", action="/gather", method="POST", timeout=5, speech_timeout="auto")
    no_input_count = sess.get("no_input_count", 0)

    if no_input_count >= 3:
        resp.say("It seems like you might be busy. I'll try again another time. Take care!", voice=TTS_VOICE)
        resp.hangup()
        if call_sid:
            _save_and_close_call(call_sid, sess)
        return Response(str(resp), mimetype="application/xml")

    if not sess.get("greeted"):
        ctx = sess.get("call_context") or {}
        client_name = ctx.get("client_name", "")
        project_name = ctx.get("project_name", "")
        greeting = f"Hi{' ' + client_name if client_name else ' there'}! "
        if project_name:
            greeting += f"This is a follow-up call about the construction project. "
        else:
            greeting += "This is a follow-up call from the Riverwoods construction team. "
        greeting += "Do you have a quick minute?"
        gather.say(greeting, voice=TTS_VOICE)
        sess["greeted"] = True
    elif no_input_count == 0:
        gather.say("I'm still here. Go ahead.", voice=TTS_VOICE)
    elif no_input_count == 1:
        gather.say("Hey, are you still there?", voice=TTS_VOICE)
    else:
        gather.say("I can wait if you need a moment. Just say something when you're ready.", voice=TTS_VOICE)

    sess["no_input_count"] = no_input_count + 1

    if sess.get("pinecone_context") is None:
        ctx = sess.get("call_context") or {}
        pn = ctx.get("project_name", "")
        query = f"{pn} construction progress update" if pn else "construction progress update"
        sess["pinecone_context"] = retrieve_context_from_pinecone(query)

    if call_sid:
        session_store.save(call_sid, sess)
    resp.append(gather)
    resp.redirect("/voice", method="POST")
    return Response(str(resp), mimetype="application/xml")

@app.route("/gather", methods=["POST"])
def gather():
    transcript = request.form.get("SpeechResult", "") or request.form.get("TranscriptionText", "")
    call_sid = request.form.get("CallSid")
    from_number = request.form.get("From")
    to_number = request.form.get("To")

    if call_sid:
        Thread(target=store.ensure_call, args=(call_sid,), kwargs={"from_number": from_number, "to_number": to_number}, daemon=True).start()
    Thread(target=push_transcript_to_n8n, args=({
        "call_sid": call_sid,
        "from": from_number,
        "to": to_number,
        "transcript": transcript
    },), daemon=True).start()
    sess = get_session(call_sid)
    sess["no_input_count"] = 0
    call_context = sess.get("call_context") or {}
    client_name = call_context.get("client_name", "")
    project_name = call_context.get("project_name", "")

    messages = sess.get("messages", [])
    if not messages:
        context_block = ""
        if client_name:
            context_block += f"\nThe customer's name is {client_name}."
        if project_name:
            context_block += f"\nThe project is called {project_name}."

        system_prompt = f"""You are a real person named Priya calling from the company on behalf of the construction team. You're making a friendly follow-up call to a client about their project.
{context_block}

YOUR PERSONALITY:
- You're warm, upbeat, and professional — like a helpful relationship manager.
- You speak casually, the way a colleague would on the phone, not like a script.
- You naturally use "um", "so", "actually", "by the way" once in a while — but don't overdo it.
- Use the customer's name once or twice during the call, not every sentence.

CALL FLOW (follow this order, one step per turn, don't rush):
1. FIRST TURN: You've already greeted them. When they respond, acknowledge warmly and share the construction progress update from the CONTEXT in 1-2 casual sentences. Make it sound like news you're excited about. If no progress update is found in the context, let them know everything is on track and offer to get more details.
2. SECOND TURN: After sharing the update, ask naturally if they're planning to visit the site anytime soon.
3. THIRD TURN: Based on their answer:
   - If YES: Ask which day works for them. Confirm you'll note it and mention the date.
   - If NO: That's totally fine. Offer to send a photo update or schedule another call.
   - If MAYBE / UNSURE: No pressure. Suggest you can check back in a few days.
   - If BUSY: Apologise for catching them at a bad time. Ask when's better for a quick call.
4. CLOSING TURN: Confirm the next step in one sentence. Thank them warmly and say goodbye.

STRICT RULES:
- One thing at a time. Never share the update AND ask about the visit in the same reply.
- Maximum 1-3 short sentences per turn. This is a phone call, not an email.
- Always use contractions ("I'm", "you'll", "that's", "we've", "it's").
- Acknowledge what the customer just said before moving on ("Oh great!", "Got it", "No worries at all").
- Never use bullet points, numbered lists, headings, markdown, asterisks, or special formatting.
- Never reveal you are an AI, a bot, or a language model. You are Priya from the team.
- If asked something outside your knowledge, say "Honestly, I'm not sure about that one. Want me to have someone from the team call you back?"
- Keep it natural. Vary your phrasing — don't repeat the same words across turns.
- End each reply with something that passes the turn back ("Sound good?", "What do you think?", "Does that work for you?").
- Get ALL project updates, construction details, and progress information ONLY from the CONTEXT provided below. Do NOT make up or assume any construction details.
- When you have said your final goodbye and the conversation is naturally over (CLOSING TURN complete), append the exact token [END_CALL] at the very end of your reply. Do NOT use it mid-conversation."""
        messages.append({"role": "system", "content": system_prompt})

    context = sess.get("pinecone_context")
    if context is None:
        pinecone_query = transcript
        if project_name:
            pinecone_query = f"{project_name} construction progress update. {transcript}"
        context = retrieve_context_from_pinecone(pinecone_query)
        sess["pinecone_context"] = context
    messages_for_openai = list(messages)
    if context:
        messages_for_openai.append({"role": "system", "content": "CONTEXT (use as source of truth):\n" + context})
    messages_for_openai.append({"role": "user", "content": transcript})

    reply_text = None
    if oai_client:
        reply_text = generate_reply_with_openai(messages_for_openai)
    if not reply_text:
        reply_text = generate_reply_fallback(transcript)

    end_call = "[END_CALL]" in reply_text
    if end_call:
        reply_text = reply_text.replace("[END_CALL]", "").strip()

    messages.append({"role": "user", "content": transcript})
    messages.append({"role": "assistant", "content": reply_text})
    sess["messages"] = messages

    if end_call and call_sid:
        _save_and_close_call(call_sid, sess)
    elif call_sid:
        session_store.save(call_sid, sess)

    if not VoiceResponse:
        return jsonify({"reply": reply_text}), 200

    resp = VoiceResponse()
    resp.say(reply_text, voice=TTS_VOICE)

    if end_call:
        resp.hangup()
    else:
        gather_next = Gather(input="speech", action="/gather", method="POST", timeout=5, speech_timeout="auto")
        resp.append(gather_next)
        resp.redirect("/voice", method="POST")
    return Response(str(resp), mimetype="application/xml")

from analysis_engine import extract_structured_response


def _save_and_close_call(call_sid: str, sess: dict):
    try:
        messages = sess.get("messages") or []
        call_context = sess.get("call_context") or {}

        turns = []
        for m in messages:
            if m.get("role") in ("user", "assistant"):
                turns.append({
                    "role": m["role"],
                    "text": m["content"],
                    "channel": "voice",
                })

        store.save_full_conversation(call_sid, turns=turns, call_context=call_context)
        store.mark_ended(call_sid, status="completed", retention_hours=24)
        if oai_client and turns:
            def _extract(sid, t):
                try:
                    lines = [f"{x['role']}: {x['text']}" for x in t]
                    structured = extract_structured_response(oai_client, "\n".join(lines))
                    if structured:
                        store.save_structured_response(sid, structured)
                except Exception as e:
                    print("structured extraction error:", repr(e))
            Thread(target=_extract, args=(call_sid, turns), daemon=True).start()

        session_store.clear(call_sid)
    except Exception as e:
        print("_save_and_close_call error:", repr(e))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port)
