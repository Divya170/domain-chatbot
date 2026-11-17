"""Domain settings: everything that changes when the chatbot changes subject.

To build a different chatbot, edit this file, the prompt template values it feeds
(``app/prompts/system.v1.md``) and the files in ``knowledge/``. See
``docs/CHANGING_DOMAIN.md``.

TRANSLATIONS: every non-English string below is a draft that NEEDS NATIVE-SPEAKER
REVIEW before production use.

CONTACTS: no real helpline, portal or emergency number is filled in. They are left
empty on purpose until the project owner supplies verified values (see TODO_CONTACTS).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final


@dataclass(frozen=True, slots=True)
class Language:
    code: str
    label: str
    english_name: str


@dataclass(frozen=True, slots=True)
class Contact:
    label: str
    value: str


LANGUAGES: Final[tuple[Language, ...]] = (
    Language("en", "English", "English"),
    Language("ta", "தமிழ் (Tamil)", "Tamil"),
)
DEFAULT_LANGUAGE: Final = "en"

# TODO(owner): supply verified official contacts. Until then these stay empty and the
# bot only gives generic guidance ("contact the nearest health centre").
TODO_CONTACTS: Final[tuple[str, ...]] = (
    "National/State maternal-health helpline number",
    "Ambulance / maternal transport number",
    "General emergency number",
    "Official scheme portal URLs (PMMVY, PICME/RCH, …)",
    "Grievance portal / helpline",
)


def _texts(**values: str) -> MappingProxyType[str, str]:
    missing = {lang.code for lang in LANGUAGES} - values.keys()
    if missing:
        raise ValueError(f"missing translations: {sorted(missing)}")
    return MappingProxyType(dict(values))


def _lists(**values: tuple[str, ...]) -> MappingProxyType[str, tuple[str, ...]]:
    missing = {lang.code for lang in LANGUAGES} - values.keys()
    if missing:
        raise ValueError(f"missing translations: {sorted(missing)}")
    return MappingProxyType(dict(values))


@dataclass(frozen=True, slots=True)
class DomainSettings:
    title: str
    subtitle: str
    domain: str
    purpose: str
    allowed_topics: tuple[str, ...]
    out_of_domain_topics: tuple[str, ...]
    refusal_scope: str
    languages: tuple[Language, ...]
    default_language: str
    official_contacts: tuple[Contact, ...]
    welcome: MappingProxyType[str, str]
    starters: MappingProxyType[str, tuple[str, ...]]
    disclaimer: MappingProxyType[str, str]
    emergency: MappingProxyType[str, str]
    fallback: MappingProxyType[str, str]
    heading_keywords: MappingProxyType[str, tuple[str, ...]] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def language(self, value: str | None) -> Language:
        """Resolve a code or a legacy label; unknown values fall back to the default."""
        if value:
            needle = value.strip().casefold()
            for lang in self.languages:
                if needle in (lang.code, lang.label.casefold(), lang.english_name.casefold()):
                    return lang
        return next(lang for lang in self.languages if lang.code == self.default_language)

    def text(self, table: MappingProxyType[str, str], language: str) -> str:
        return table.get(language) or table[self.default_language]


DOMAIN: Final = DomainSettings(
    title="Pregnancy Schemes Assistant",
    subtitle="Pregnancy & maternity scheme information assistant",
    domain="Pregnancy, maternity benefits, maternal and newborn public services in India",
    purpose=(
        "Provide instant information and eligibility guidance; explain step-by-step "
        "registration and status-checking processes; provide reminder and health-alert "
        "information; guide users to official grievance channels and emergency support."
    ),
    allowed_topics=(
        "Indian pregnancy and maternity welfare schemes",
        "Dr. Muthulakshmi Reddy Maternity Benefit Scheme",
        "PMMVY - Pradhan Mantri Matru Vandana Yojana",
        "JSY - Janani Suraksha Yojana",
        "JSSK - Janani Shishu Suraksha Karyakram",
        "PMSMA - Pradhan Mantri Surakshit Matritva Abhiyan",
        "LaQshya",
        "SUMAN",
        "PICME / RCH",
        "Amma Baby Care Kit",
        "102 ambulance service and maternal transport information",
        "ICDS nutrition services",
        "Eligibility, benefits, required documents, application and registration steps",
        "Application status, reminders, antenatal care information and official contacts",
        "Grievance procedures and emergency referral guidance",
    ),
    out_of_domain_topics=(
        "unrelated general knowledge, entertainment, shopping, finance or technology",
        "personalized diagnosis, prescription, or replacement for professional medical care",
        "legal or financial advice unrelated to the listed maternity schemes",
    ),
    refusal_scope="pregnancy/maternity schemes and related maternal-newborn services",
    languages=LANGUAGES,
    default_language=DEFAULT_LANGUAGE,
    official_contacts=(),
    welcome=_texts(
        en=(
            "Hello! I can help you with pregnancy and maternity schemes in India: "
            "eligibility, benefits, documents, registration and where to get official "
            "help. What would you like to know?"
        ),
        ta=(
            "வணக்கம்! இந்தியாவின் கர்ப்பகால மற்றும் மகப்பேறு திட்டங்கள் பற்றி உதவ "
            "முடியும்: தகுதி, பலன்கள், ஆவணங்கள், பதிவு மற்றும் அதிகாரப்பூர்வ உதவி "
            "எங்கே கிடைக்கும். என்ன தெரிந்துகொள்ள விரும்புகிறீர்கள்?"
        ),
    ),
    starters=_lists(
        en=(
            "What pregnancy schemes are available?",
            "Explain the eligibility for PMMVY.",
            "How can I register on PICME / RCH?",
            "How can I check my application status?",
        ),
        ta=(
            "என்னென்ன கர்ப்பகால திட்டங்கள் உள்ளன?",
            "PMMVY திட்டத்திற்கான தகுதி என்ன?",
            "PICME / RCH-ல் எப்படி பதிவு செய்வது?",
            "என் விண்ணப்ப நிலையை எப்படி அறிவது?",
        ),
    ),
    disclaimer=_texts(
        en=(
            "Information support only. This is not a government website and not medical "
            "advice. Do not share Aadhaar numbers, OTPs, passwords or bank details here."
        ),
        ta=(
            "தகவல் உதவி மட்டுமே. இது அரசு இணையதளம் அல்ல, மருத்துவ ஆலோசனையும் அல்ல. "
            "ஆதார் எண், OTP, கடவுச்சொல் அல்லது வங்கி விவரங்களை இங்கே பகிர வேண்டாம்."
        ),
    ),
    emergency=_texts(
        en=(
            "If there is heavy bleeding, severe pain, fits, high fever, severe headache "
            "or blurred vision, or the baby stops moving, contact local emergency "
            "services or go to the nearest hospital immediately."
        ),
        ta=(
            "அதிக இரத்தப்போக்கு, கடுமையான வலி, வலிப்பு, அதிக காய்ச்சல், கடுமையான தலைவலி "
            "அல்லது மங்கலான பார்வை இருந்தால், அல்லது குழந்தையின் அசைவு நின்றால், உடனே "
            "உள்ளூர் அவசர சேவையை தொடர்பு கொள்ளுங்கள் அல்லது அருகிலுள்ள மருத்துவமனைக்குச் செல்லுங்கள்."
        ),
    ),
    fallback=_texts(
        en=(
            "Sorry, I can't answer right now. Please try again in a little while. For "
            "official information, contact your nearest primary health centre, anganwadi "
            "worker or the official scheme office."
        ),
        ta=(
            "மன்னிக்கவும், இப்போது பதில் அளிக்க முடியவில்லை. சிறிது நேரம் கழித்து மீண்டும் "
            "முயற்சிக்கவும். அதிகாரப்பூர்வ தகவலுக்கு அருகிலுள்ள ஆரம்ப சுகாதார நிலையம், "
            "அங்கன்வாடி பணியாளர் அல்லது திட்ட அலுவலகத்தை தொடர்பு கொள்ளுங்கள்."
        ),
    ),
    # Words in a question that point at a knowledge-file section (English and Tamil).
    heading_keywords=MappingProxyType(
        {
            "Overview": ("overview", "about", "பற்றி"),
            "Eligibility": (
                "eligibility",
                "eligible",
                "qualify",
                "who can",
                "criteria",
                "தகுதி",
            ),
            "Benefits": (
                "benefit",
                "benefits",
                "amount",
                "money",
                "how much",
                "payment",
                "cash",
                "பலன்",
                "தொகை",
                "பணம்",
            ),
            "Documents required": (
                "document",
                "documents",
                "papers",
                "proof",
                "certificate",
                "ஆவண",
                "சான்றிதழ்",
            ),
            "How to apply": (
                "apply",
                "application",
                "register",
                "registration",
                "enrol",
                "enroll",
                "steps",
                "விண்ணப்ப",
                "பதிவு",
            ),
            "Status check": (
                "status",
                "track",
                "check",
                "pending",
                "not received",
                "நிலை",
            ),
            "Official contacts": (
                "contact",
                "helpline",
                "phone",
                "number",
                "office",
                "call",
                "தொடர்பு",
                "உதவி எண்",
            ),
            "Notes": ("note", "notes", "important", "குறிப்பு"),
        }
    ),
)
