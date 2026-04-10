from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from app import ask_askhelm

app = Flask(__name__)

@app.route("/whatsapp", methods=["POST"])
def whatsapp_reply():
    incoming = request.form.get("Body", "").strip()

    if incoming.lower() in ["hi", "hello", "start"]:
        answer = """AskHelm ready.

Send your question."""
    else:
        try:
            answer = ask_askhelm(incoming)
        except Exception as e:
            answer = f"""DECISION: ERROR

WHY: AskHelm failed: {e}

ACTIONS:
- Try again
- Keep question simple
- Check app is running"""

    resp = MessagingResponse()
    resp.message(f"AskHelm\n\n{answer}")
    return str(resp)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)