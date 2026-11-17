"""UI chrome strings (buttons, states, errors) in the 8 supported languages.

Non-English strings are drafts that NEED NATIVE-SPEAKER REVIEW. Domain texts (welcome,
starters, disclaimer, emergency) live in ``app/domain.py``.
"""

from __future__ import annotations

from typing import Final

from app.services.errors import ErrorCode

KEYS: Final = (
    "language",
    "new_chat",
    "delete",
    "delete_confirm",
    "cancel",
    "send",
    "stop",
    "placeholder",
    "copy",
    "copied",
    "regenerate",
    "sources",
    "helpful",
    "not_helpful",
    "thanks",
    "typing",
    "stopped",
    "chars_left",
    "saved_hint",
    "rate_limited",
    "you",
    "assistant",
)

STRINGS: Final[dict[str, dict[str, str]]] = {
    "en": {
        "language": "Language",
        "new_chat": "New chat",
        "delete": "Delete conversation",
        "delete_confirm": "Delete this conversation permanently?",
        "cancel": "Cancel",
        "send": "Send",
        "stop": "Stop generating",
        "placeholder": "Type your question here…",
        "copy": "Copy",
        "copied": "Copied",
        "regenerate": "Regenerate",
        "sources": "Sources",
        "helpful": "Helpful",
        "not_helpful": "Not helpful",
        "thanks": "Thanks for the feedback",
        "typing": "Preparing an answer…",
        "stopped": "(stopped)",
        "chars_left": "{n} characters left",
        "saved_hint": "This conversation is saved on this device for {days} days.",
        "rate_limited": "Too many messages. Please wait {seconds} seconds and try again.",
        "you": "You",
        "assistant": "Assistant",
    },
    "ta": {
        "language": "மொழி",
        "new_chat": "புதிய உரையாடல்",
        "delete": "உரையாடலை நீக்கு",
        "delete_confirm": "இந்த உரையாடலை நிரந்தரமாக நீக்கவா?",
        "cancel": "ரத்து",
        "send": "அனுப்பு",
        "stop": "நிறுத்து",
        "placeholder": "உங்கள் கேள்வியை இங்கே தட்டச்சு செய்யுங்கள்…",
        "copy": "நகலெடு",
        "copied": "நகலெடுக்கப்பட்டது",
        "regenerate": "மீண்டும் உருவாக்கு",
        "sources": "ஆதாரங்கள்",
        "helpful": "பயனுள்ளது",
        "not_helpful": "பயனில்லை",
        "thanks": "கருத்துக்கு நன்றி",
        "typing": "பதில் தயாராகிறது…",
        "stopped": "(நிறுத்தப்பட்டது)",
        "chars_left": "இன்னும் {n} எழுத்துகள்",
        "saved_hint": "இந்த உரையாடல் இந்த சாதனத்தில் {days} நாட்கள் சேமிக்கப்படும்.",
        "rate_limited": "அதிக செய்திகள். {seconds} விநாடிகள் காத்திருந்து மீண்டும் முயற்சிக்கவும்.",
        "you": "நீங்கள்",
        "assistant": "உதவியாளர்",
    },
}

ERRORS: Final[dict[str, dict[ErrorCode, str]]] = {
    "en": {
        ErrorCode.INVALID_INPUT: "Please check your message and try again.",
        ErrorCode.RATE_LIMIT: "Too many messages. Please wait a moment.",
        ErrorCode.PROVIDER_DOWN: "The answer service is unavailable right now. Please try again later.",
        ErrorCode.TIMEOUT: "The answer took too long. Please try again.",
        ErrorCode.KB_UNAVAILABLE: "The information library is unavailable right now.",
        ErrorCode.SESSION_NOT_FOUND: "This conversation is no longer available. A new one was started.",
        ErrorCode.NOT_CONFIGURED: "The assistant is not set up yet. Please contact the administrator.",
        ErrorCode.INTERNAL: "Something went wrong. Please try again.",
    },
    "ta": {
        ErrorCode.INVALID_INPUT: "உங்கள் செய்தியைச் சரிபார்த்து மீண்டும் முயற்சிக்கவும்.",
        ErrorCode.RATE_LIMIT: "அதிக செய்திகள். சிறிது நேரம் காத்திருக்கவும்.",
        ErrorCode.PROVIDER_DOWN: "பதில் சேவை இப்போது கிடைக்கவில்லை. பின்னர் முயற்சிக்கவும்.",
        ErrorCode.TIMEOUT: "பதில் வர அதிக நேரம் ஆனது. மீண்டும் முயற்சிக்கவும்.",
        ErrorCode.KB_UNAVAILABLE: "தகவல் தொகுப்பு இப்போது கிடைக்கவில்லை.",
        ErrorCode.SESSION_NOT_FOUND: "இந்த உரையாடல் இனி கிடைக்காது. புதிய உரையாடல் தொடங்கப்பட்டது.",
        ErrorCode.NOT_CONFIGURED: "உதவியாளர் இன்னும் அமைக்கப்படவில்லை. நிர்வாகியைத் தொடர்பு கொள்ளுங்கள்.",
        ErrorCode.INTERNAL: "ஏதோ தவறு நடந்தது. மீண்டும் முயற்சிக்கவும்.",
    },
}


def t(language: str, key: str, **values: object) -> str:
    table = STRINGS.get(language) or STRINGS["en"]
    return (table.get(key) or STRINGS["en"][key]).format(**values)


def error_text(language: str, code: str) -> str:
    try:
        error_code = ErrorCode(code)
    except ValueError:
        error_code = ErrorCode.INTERNAL
    return (ERRORS.get(language) or ERRORS["en"])[error_code]
