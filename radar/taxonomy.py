"""Complaint categories, promo names, and the keyword rules used offline.

The rules are deliberately simple and transparent so a support lead can read and
tune them. The optional Claude classifier (classify.py) uses the same category
keys, so reports look identical either way.
"""

from __future__ import annotations

import re

# Order matters: when several categories match, the first one listed becomes the
# primary category. Money problems outrank everything else because they are what
# players escalate publicly.
CATEGORIES: dict[str, dict] = {
    "deposit_missing": {
        "label": "Deposit not credited",
        "patterns": [
            r"deposit\w*\b[^.]{0,80}\b(not|never|didn'?t|wasn'?t|hasn'?t|no)\b[^.]{0,40}\b(credit|arriv|show|appear|receiv|reflect)",
            r"(missing|lost|stuck)\s+deposit",
            r"deposit\w*\s+(is\s+)?(missing|lost|stuck|pending)",
            r"funds?\s+(were\s+)?(debited|deducted|taken)[^.]{0,60}(not|never)\s+(credit|receiv)",
        ],
    },
    "withdrawal": {
        "label": "Withdrawal delayed or blocked",
        "patterns": [
            r"\b(withdraw\w*|cash\s?outs?|pay\s?outs?)\b[^.]{0,60}\b(pending|stuck|delay\w*|blocked|cancel\w*|reject\w*|declined|hold|waiting|not|never|days|weeks|hours|frozen|refus\w*)\b",
            r"\b(pending|stuck|delayed|blocked|cancel\w*|reject\w*|waiting|refus\w*)\b[^.]{0,40}\b(withdraw\w*|cash\s?outs?|pay\s?outs?)\b",
            r"(can'?t|cannot|unable to|won'?t let me)\s+(withdraw|cash)",
        ],
    },
    "promo_not_credited": {
        "label": "Promo / bonus not credited",
        "patterns": [
            r"\b(loss\s?back|rake\s?back|win\s?back|cash\s?back|bonus|promo\w*|free\s?spins?|cash\s?drop|giveaway|rank[\s-]?up|reward\w*|credits?)\b[^.]{0,80}\b(not|never|didn'?t|wasn'?t|haven'?t|no|missing|paused|removed|revoked|voided)\b",
            r"\b(never|didn'?t|did not|haven'?t|not)\s+(get|got|receive\w*|credited|paid)\b[^.]{0,60}\b(loss\s?back|rake\s?back|bonus|promo|free\s?spins?|reward|giveaway|credit)",
            r"\b(bonus|promo|lossback|rakeback)\b[^.]{0,40}\b(confiscat|void|cancel)",
        ],
    },
    "account_restricted": {
        "label": "Account closed / restricted / flagged",
        "patterns": [
            r"\b(banned|suspended|locked|frozen|restricted|blocked|closed|disabled|terminated)\b[^.]{0,30}\b(account|me|my)\b",
            r"\baccount\b[^.]{0,30}\b(banned|suspended|locked|frozen|restricted|blocked|closed|disabled|terminated)\b",
            r"multi[\s-]?account\w*",
            r"(multiple|duplicate|linked)\s+accounts?",
            r"bonus\s+abuse",
        ],
    },
    "kyc": {
        "label": "KYC / verification demands",
        "patterns": [
            r"\bkyc\b",
            r"\bverif\w*",
            r"\b(id|passport|selfie|proof of (address|funds|income)|source of (funds|wealth))\b",
        ],
    },
    "fairness": {
        "label": "Game fairness / 'rigged'",
        "patterns": [
            r"\brigged\b",
            r"\b(provably fair|server seed|client seed|hash)\b",
            r"\brtp\b",
            r"\b(never|no)\s+wins?\b",
            r"\bscam\s+(game|slot|casino)",
        ],
    },
    "fees": {
        "label": "Fees",
        "patterns": [r"\bfees?\b", r"\bcharged\b[^.]{0,30}\b(withdraw|fee)"],
    },
    "ux_bugs": {
        "label": "Bugs / site problems",
        "patterns": [
            r"\b(bug|bugged|glitch\w*|crash\w*|froze|freez\w*|error|not loading|won'?t load|lag\w*)\b",
            r"\bwrong amount\b",
            r"credited\s+(as\s+)?only\b",
        ],
    },
    "support": {
        "label": "Support slow / unhelpful",
        "patterns": [
            r"\b(support|live chat|customer service|ticket|agent)\b",
            r"\b(no|zero)\s+(response|reply|answer)\b",
            r"\bignor\w+\b",
            r"\bdiscord\b",
        ],
    },
    "other": {"label": "Other", "patterns": []},
}

# Player is complaining specifically that support is automated. MetaWin runs an
# Intercom chat (Intercom's AI agent is "Fin"), so this is a direct signal on it.
BOT_SUPPORT_PATTERNS = [
    r"\b(bot|chat\s?bot|ai|a\.i\.|automated|auto[\s-]?reply|copy[\s-]?paste\w*|canned|scripted|fin)\b[^.]{0,60}\b(support|chat|reply|replies|response|answer)",
    r"\b(support|chat|reply|replies|response|answer)\w*\b[^.]{0,60}\b(bot|ai|automated|robot|copy[\s-]?paste\w*|canned|scripted)\b",
    r"\b(real|actual|live)\s+(person|human|agent)\b",
    r"\btalking to a (bot|robot|wall)\b",
]

# Promotions named in MetaWin's public marketing. A complaint can mention several.
PROMOS: dict[str, str] = {
    "Lossback": r"\bloss\s?back\b|\blosses back\b|\bcover(ed)? (my|your) loss",
    "WinBack / Rakeback": r"\b(win\s?back|rake\s?back)\b",
    "MetaMax rank rewards": r"\b(metamax|rank[\s-]?up|rank (reward|bonus)|vip (reward|level|rank))\b",
    "X / username giveaway": r"\b(giveaway|drop (your|my) username|username drop|skel'?s? (drop|giveaway|post|tweet)|on (x|twitter))\b",
    "Cash Drop": r"\bcash\s?drops?\b|\bmake it rain\b",
    "Free spins": r"\bfree\s?spins?\b",
    "Promo code / deposit bonus": r"\bpromo\s?code\b|\bbonus code\b|\bdeposit bonus\b|\bwelcome bonus\b|\bmatch(ed)? bonus\b",
    "Challenges / races": r"\b(challenge|race|leaderboard|tournament)s?\b",
    "Prize draws / competitions": r"\b(prize draw|competition|raffle|free entr(y|ies))\b",
    "EARN affiliate": r"\b(earn program|affiliate|referral|commission)\b",
    "Red Room": r"\bred\s?room\b",
}

# Rough markers of severity: money amounts and legal/escalation language.
_MONEY = re.compile(r"(\$|usd|usdt|usdc|eth|btc|sol|€|£)\s?\d[\d,]*(\.\d+)?|\d[\d,]*(\.\d+)?\s?(\$|usd|usdt|usdc|eth|btc|sol|k\b)", re.I)
_ESCALATION = re.compile(r"\b(lawyer|legal|police|regulator|licen[cs]e (holder|authority)|anjouan|chargeback|report(ing)? (you|them|this)|fraud|stole|theft|scam)\b", re.I)
_BIG_MONEY = re.compile(r"(\$|usd|usdt|usdc)\s?(\d{1,3}(,\d{3})+|\d{4,})|\b\d+(\.\d+)?\s?k\b|\b\d+(\.\d+)?\s?(eth|btc)\b", re.I)


def _compiled(patterns: list[str]) -> list[re.Pattern]:
    return [re.compile(p, re.I) for p in patterns]


_CAT_RX = {k: _compiled(v["patterns"]) for k, v in CATEGORIES.items()}
_BOT_RX = _compiled(BOT_SUPPORT_PATTERNS)
_PROMO_RX = {k: re.compile(v, re.I) for k, v in PROMOS.items()}


def category_label(key: str) -> str:
    return CATEGORIES.get(key, CATEGORIES["other"])["label"]


_PROMO_WORD = re.compile(r"\b(loss\s?back|rake\s?back|win\s?back|cash\s?back|bonus|promo\w*|free\s?spins?|cash\s?drop|giveaway|rank[\s-]?up|metamax)\b", re.I)
_NOT_RECEIVED = re.compile(r"\b(never|not|wasn'?t|hasn'?t|haven'?t|didn'?t|did not)\s+(been\s+|get\s+|got\s+)?(credited|received|paid|arrived|applied|added|show\w*)\b", re.I)
_ONCHAIN = re.compile(r"\b(tx|txn|transaction|hash|confirm\w*|on[\s-]?chain|block explorer|etherscan|solscan|sent\s+\d)", re.I)


def match_categories(text: str) -> list[str]:
    """All categories whose rules match, in priority order.

    Two corrections the plain rules get wrong:
    * a promo named anywhere + "never credited" anywhere is a promo complaint,
      even when the two are in different sentences;
    * "deposited $250 for the lossback offer ... never credited" is about the
      promo, not a missing deposit, unless there's on-chain evidence (tx hash,
      "confirmed") that the deposit itself is what's missing.
    """
    cats = [k for k, rxs in _CAT_RX.items() if any(rx.search(text) for rx in rxs)]
    promo_complaint = bool(_PROMO_WORD.search(text) and _NOT_RECEIVED.search(text))
    if promo_complaint and "promo_not_credited" not in cats:
        cats.append("promo_not_credited")
    if "deposit_missing" in cats and "promo_not_credited" in cats and _PROMO_WORD.search(text) and not _ONCHAIN.search(text):
        cats.remove("deposit_missing")
    order = list(CATEGORIES)
    return sorted(cats, key=order.index)


def match_promos(text: str) -> list[str]:
    return [k for k, rx in _PROMO_RX.items() if rx.search(text)]


def mentions_bot_support(text: str) -> bool:
    return any(rx.search(text) for rx in _BOT_RX)


def estimate_severity(text: str, categories: list[str]) -> int:
    if _ESCALATION.search(text) or _BIG_MONEY.search(text):
        return 3
    money_cats = {"deposit_missing", "withdrawal", "promo_not_credited", "account_restricted"}
    if _MONEY.search(text) or money_cats.intersection(categories):
        return 2
    return 1
