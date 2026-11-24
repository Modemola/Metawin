"""Complaint categories, promo names, and the keyword rules used offline.

How the rules work (designed to be read and tuned by a support lead):

1. The text is split into sentences, and sentences into clauses. A clause that
   describes something as fine ("withdrawal was fast", "fees are ok-ish") and
   contains no trouble word is dropped, so "X was fine, but Y is broken" is
   judged on Y alone.
2. A category matches if one of its STRONG patterns appears anywhere, or if
   one of its TOPIC words and one of its TROUBLE words appear in the same
   sentence ("my rakeback" + "wiped", "w/d" + "stuck").
3. When several categories match, the earliest in CATEGORIES wins: problems
   that cost the player money or access outrank everything else.

The optional Claude classifier (classify.py) uses the same category keys, so
reports look identical either way. Measure changes with
`radar eval tests/fixtures/challenge_set.csv tests/fixtures/challenge_set.csv`.
"""

from __future__ import annotations

import re

# Order matters: the first matching category becomes the primary one.
CATEGORIES: dict[str, dict] = {
    "deposit_missing": {"label": "Deposit not credited"},
    "withdrawal": {"label": "Withdrawal delayed or blocked"},
    "promo_not_credited": {"label": "Promo / bonus not credited"},
    "account_restricted": {"label": "Account closed / restricted / flagged"},
    "kyc": {"label": "KYC / verification demands"},
    "fairness": {"label": "Game fairness / 'rigged'"},
    "fees": {"label": "Fees"},
    "ux_bugs": {"label": "Bugs / site problems"},
    "support": {"label": "Support slow / unhelpful"},
    "other": {"label": "Other"},
}

# Generic trouble words shared by the money categories.
_CORE_TROUBLE = (
    r"not|never|no|nothing|missing|gone|disappear\w*|vanish\w*|wiped|void(ed)?|removed|revoked|cancell?ed|cancel|"
    r"reversed|rejected|declined|stuck|pending|processing|under review|in review|security review|waiting|still|"
    r"delay\w*|fail\w*|blocked|frozen|on hold|held|zero|empty|didn'?t|doesn'?t|haven'?t|hasn'?t|isn'?t|wasn'?t|"
    r"aren'?t|can'?t|cant|won'?t|where'?s|where (is|are)|stopped|paused|forever|neither|nor|again|try again|"
    r"\d+\s?(h|hrs?|hours?|days?|weeks?)|since \w+|\?\?"
)

# Did the player NOT receive something? Used for promos, where "never" alone is
# too loose ("free spins designed to never hit" is a fairness complaint).
_NOT_RECEIVED = (
    r"(not|never|didn'?t|did not|haven'?t|hasn'?t|no|nothing)\s+(\w+\s+){0,3}(credit\w*|receiv\w*|paid|get|got|arriv\w*|came|come|"
    r"show\w*|land\w*|added|appear\w*|in my (balance|account))|"
    r"gone|disappear\w*|vanish\w*|wiped|void(ed)?|removed|revoked|stopped|paused|where'?s|where (is|are)|empty|showing 0|"
    r"nothing( credited)?|missing|cancel\w*|took (it )?(back|away)|reversed|being calculated|'shortly'|weeks? ago|\?\?|"
    r"(not|isn'?t|wasn'?t) in my (balance|account)|i get nothing|got nothing|(balance|it) didn'?t change|didn'?t hit|(zero|0|nothing) back|still waiting|'soon'|never saw a cent|no (reward|prize)|"
    r"reduced|'in progress'"
)

RULES: dict[str, dict[str, str]] = {
    "deposit_missing": {
        "topic": r"deposit\w*|\bdep\b|top[\s-]?up|deposit address|sent \d[\d,.]*\s?(k\s)?(usdt|usdc|eth|btc|sol|ltc)|funds left my wallet|on-?ramp|"
                 r"card purchase|\d+\+? conf(s|irmations?)?\b",
        "trouble": r"not (credited|showing|in my|reflected|arriv\w*|there)|never (land\w*|arriv\w*|show\w*|credited|came|reached)|"
                   r"balance (is )?still (0|zero|empty)|still 0|not in my (balance|account|wallet)|missing|gone|where'?s|where is|"
                   r"(stuck|forever|still) (on )?'?(confirming|pending|processing)|confirming'? forever|nothing|neither|still '?pending|no crypto arrived|never credited|"
                   r"didn'?t (arrive|show|land|go through|reflect|credit)|hasn'?t (arrived|shown|landed|credited|been credited)|isn'?t (there|showing)",
        "strong": r"(missing|lost|stuck|uncredited)\s+deposit|deposit\w* (is |are )?(missing|lost|stuck)",
    },
    "withdrawal": {
        "topic": r"withdraw\w*|withdrawl\w*|w/d|\bwd\b|cash[\s-]?outs?|pay[\s-]?outs?|retiro|sacar|saque|auszahlung|requested \d",
        "trouble": _CORE_TROUBLE + r"|locked|'sent'|shows nothing|review\w*|sits|wager .{0,20} first|day \d+|different address|pendiente|imposible",
        "strong": r"(can'?t|cannot|unable to|won'?t let me) (withdraw|cash ?out)",
    },
    "promo_not_credited": {
        "topic": r"loss\s?back|rake\s?back|win\s?back|cash\s?back|bonus\w*|free\s?spins?|cash\s?drops?|giveaway|rank[\s-]?up|"
                 r"rewards?|prize|race|leaderboard|competition|tournament|commission|affiliate|\bearn\b|promo\w*|metamax|vip|"
                 r"winnings|jackpot credit",
        "trouble": _NOT_RECEIVED,
        "strong": r"(bonus|promo|lossback|rakeback)\b.{0,40}\b(confiscat\w*|voided|cancell?ed)",
    },
    "account_restricted": {
        "topic": r"account|\bacc\b|\bacct\b|profile|\bme\b|locked out|self[\s-]?exclu\w+",
        "trouble": r"banned|ban|suspended|locked|frozen|restricted|disabled|closed|terminated|blocked|flagged|deactivated|"
                   r"can'?t (re)?open|reopen|locked out|under review|permanent\w*",
        "strong": r"\bbanned\b|multi[\s-]?account\w*|(multiple|duplicate|linked) accounts?|bonus (abuse|hunt\w*)|flagged as|"
                  r"self[\s-]?exclu\w+.{0,60}(can'?t|still|reopen)|\b(acc|acct|account) (is |got |was |been )?(frozen|disabled|locked|closed|suspended|restricted)",
    },
    "kyc": {
        "topic": r"$^",  # strong-only
        "trouble": r"$^",
        "strong": r"\bkyc\b|verif\w*|passport|selfie|proof of (address|funds|income)|source of (funds|wealth)|utility bill|"
                  r"bank statement|payslip|pay slip|\bid check\b|\bid\b.{0,30}(rejected|unsupported|required)|documents? (required|rejected|unclear)",
    },
    "fairness": {
        "topic": r"$^",
        "trouble": r"$^",
        "strong": r"\brigged\b|\brtp\b|provably|(server|client)\s+seed|seed hash|go(es)? cold|algorithm|never win|"
                  r"lost \d+ in a row|\d+ (losses|spins) (in a row|no)|always (bust\w*|lose\w*|loses)|designed to never|tuned to|"
                  r"\bfake\b|don'?t trust|no proof|scam (game|slot)s?|\bnever (hit|wins?)\b|no wins?\b|cheat\w*|goes dead|\balgo\b|feels off|"
                  r"streaks? of \d+|house wallet",
    },
    "fees": {
        "topic": r"$^",
        "trouble": r"$^",
        "strong": r"\bfees?\b|charged (gas|twice|me)|gas twice|conversion|credited less than",
    },
    "ux_bugs": {
        "topic": r"$^",
        "trouble": r"$^",
        "strong": r"crash\w*|\bfroze\b|freez\w*|not loading|won'?t load|doesn'?t load|white screen|spinner|login loop|"
                  r"\berror\w*|\bbug\w*|glitch\w*|doesn'?t update|not updating|unreadable|reload\w*|balance shows 0|"
                  r"pop up|no way to turn|\blag\w*|broken|wrong amount|credited (as )?only|page is blank|\bblank\b|resets|tiny|"
                  r"widget|showed the old|says \w+, profile says",
    },
    "support": {
        "topic": r"support|live chat|\bchat\b|customer service|ticket|\bagent\b|intercom|repl(y|ies)|respon\w+|answer\w*|"
                 r"\bhuman\b|\bbot\b|mods?\b|emailed|\bfin\b",
        "trouble": r"slow|rude|useless|worst|loop\w*|automated|\bbot\b|copy[\s-]?paste|didn'?t know|don'?t help|no help|"
                   r"zero|no (reply|replies|answer|response)|no one|nobody|never (reply|answer|respond)\w*|ignor\w+|waited|"
                   r"\d+\s?(h|hrs?|hours?|days?|weeks?)|closed my|only english|be patient|hello\?\?|not resolved|"
                   r"(can'?t|cannot) (reach|get)|unhelpful|doesn'?t help|frustrat\w+",
        "strong": r"\b(no|zero) (response|reply|replies|answer)\b|\bnobody answers\b|\bno answer\b|how do i talk to a (real )?(person|human)|"
                  r"no support in \w+|nadie responde|google translate|closes the (convo|chat)|bounced",
    },
}

# Player says the support is automated or they can't reach a person. MetaWin's
# chat runs on Intercom (whose AI agent is "Fin"), so this is a direct signal.
BOT_SUPPORT_PATTERNS = [
    r"\b(bot|chat\s?bot|ai|a\.i\.|automated|auto[\s-]?reply|copy[\s-]?paste\w*|canned|scripted)\b[^.]{0,60}\b(support|chat|reply|replies|response|answer|ticket|agent)",
    r"\b(support|chat|reply|replies|response|answer)\w*\b[^.]{0,60}\b(bot|ai|automated|robot|copy[\s-]?paste\w*|canned|scripted)\b",
    r"\bfin\b[^.]{0,40}\b(ai|agent|bot)\b|\b(ai|agent|bot)\b[^.]{0,10}\bfin\b",
    r"\b(no|not|never|can'?t|cannot|couldn'?t|impossible|unable|is there|any|an)\b[^.]{0,25}\b(real|actual|live)\s+(person|human|agent)\b",
    r"\bask(ed)? for a (human|person)\b|\bexcept a bot\b|\btalking to a (bot|robot|wall)\b|\bfeels automated\b|\bthe bot\b",
]

# Promotions named in MetaWin's public marketing. A complaint can mention several.
PROMOS: dict[str, str] = {
    "Lossback": r"\bloss\s?back\b|\blosses back\b|\bcover(ed)? (my|your) loss",
    "WinBack / Rakeback": r"\b(win\s?back|rake\s?back)\b",
    "MetaMax rank rewards": r"\b(metamax|rank[\s-]?up|rank (reward|bonus)|vip (reward|level|rank|program))\b",
    "X / username giveaway": r"\b(giveaway|drop (your|my) username|username drop|skel'?s? (drop|giveaway|post|tweet)|on (x|twitter))\b",
    "Cash Drop": r"\bcash\s?drops?\b|\bmake it rain\b",
    "Free spins": r"\bfree\s?spins?\b",
    "Promo code / deposit bonus": r"\bpromo\s?code\b|\bbonus code\b|\bdeposit bonus\b|\bwelcome bonus\b|\bmatch(ed)? bonus\b",
    "Challenges / races": r"\b(challenge|race|leaderboard|tournament)s?\b",
    "Prize draws / competitions": r"\b(prize draw|competition|raffle|free entr(y|ies)|draws?)\b",
    "EARN affiliate": r"\b(earn program|earn\b|affiliate|referral|commission)\b",
    "Red Room": r"\bred\s?room\b",
}

# Clauses that describe something as working. Dropped before matching unless
# they also contain a trouble word ("fast" in "not fast" doesn't count).
_POSITIVE = re.compile(
    r"\b(fine|ok|okay|ok-ish|okish|instant\w*|fast|quick\w*|smooth\w*|like clockwork|legit|good|great|best|respect|sorted|"
    r"same day|actually pay|arrives?|arrived|works?|in (like )?\d+ (min|mins|minutes|seconds))\b",
    re.I,
)
_NEGATIVE = re.compile(r"\b(" + _CORE_TROUBLE + r"|but)\b", re.I)
_CLAUSE_SPLIT = re.compile(r",|;|\b(?:but|however|though|although|whereas|the (?:real )?(?:problem|issue) is)\b", re.I)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")

# Rough markers of severity: money amounts and legal/escalation language.
_MONEY = re.compile(r"(\$|usd|usdt|usdc|eth|btc|sol|€|£)\s?\d[\d,]*(\.\d+)?|\d[\d,]*(\.\d+)?\s?(\$|usd|usdt|usdc|eth|btc|sol|k\b)", re.I)
_ESCALATION = re.compile(r"\b(lawyer|legal|police|regulator|licen[cs]e (holder|authority)|anjouan|chargeback|report(ing)? (you|them|this)|fraud|stole|theft|scam)\b", re.I)
_BIG_MONEY = re.compile(r"(\$|usd|usdt|usdc)\s?(\d{1,3}(,\d{3})+|\d{4,})|\b\d+(\.\d+)?\s?k\b|\b\d+(\.\d+)?\s?(eth|btc)\b", re.I)


def _rx(p: str) -> re.Pattern:
    return re.compile(rf"(?:{p})", re.I)


_RULES_RX = {k: {part: _rx(v) for part, v in spec.items()} for k, spec in RULES.items()}
_BOT_RX = [re.compile(p, re.I) for p in BOT_SUPPORT_PATTERNS]
_PROMO_RX = {k: re.compile(v, re.I) for k, v in PROMOS.items()}
_ORDER = list(CATEGORIES)


def category_label(key: str) -> str:
    return CATEGORIES.get(key, CATEGORIES["other"])["label"]


_PRAISE = re.compile(r"\bno (\w+ )?(complaints?|drama|issues?|problems?)\b|\bnot bad\b", re.I)


def _sentences(text: str) -> list[str]:
    """Sentences with 'this part was fine' clauses removed."""
    text = _PRAISE.sub("fine", text)
    out = []
    for sent in _SENTENCE_SPLIT.split(text):
        clauses = [c for c in _CLAUSE_SPLIT.split(sent) if c and c.strip()]
        kept = [c for c in clauses if not (_POSITIVE.search(c) and not _NEGATIVE.search(c))]
        if kept:
            out.append(" , ".join(kept))
    return out


def match_categories(text: str) -> list[str]:
    """All categories whose rules match, in priority order (empty if none)."""
    sentences = _sentences(text)
    kept_text = " . ".join(sentences)
    cats = []
    for key, rx in _RULES_RX.items():
        if rx["strong"].search(kept_text) or any(rx["topic"].search(s) and rx["trouble"].search(s) for s in sentences):
            cats.append(key)
    # Promos are often referred back to across sentences ("The lossback said
    # up to $1k. Mine was never credited."), so allow that pairing too.
    promo = _RULES_RX["promo_not_credited"]
    if "promo_not_credited" not in cats and promo["topic"].search(kept_text) and promo["trouble"].search(kept_text):
        cats.append("promo_not_credited")
    return sorted(cats, key=_ORDER.index)


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
