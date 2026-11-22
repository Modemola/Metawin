"""Generate SYNTHETIC reviews in Trustpilot copy-paste format.

Nothing here is real MetaWin feedback. It exists so the parser and report can be
tested end to end before real data is collected. The data deliberately contains
one lossback-driven spike so the early-warning section has something to find.

    python samples/make_sample.py > samples/synthetic_trustpilot.txt
"""

import random
from datetime import date, timedelta

random.seed(7)
END = date(2026, 11, 20)
WEEKS = 16

NEGATIVE = [
    ("Lossback never arrived", "Deposited $250 for the 100% lossback offer, lost it and never got the lossback credited. Support chat keeps sending the same copy-paste answer."),
    ("Where is my lossback", "The lossback promo said losses back up to $1k. Mine was never credited and nobody explains why."),
    ("Promo credits blocked", "Got flagged for multi-accounting and my promo credits were removed. I have one account. Can't reach a real person."),
    ("Withdrawal stuck", "My withdrawal has been pending for 3 days. Used to be instant. Support says wait."),
    ("Cash out blocked after win", "Won $4,200 and now they want KYC before I can withdraw. Never asked before."),
    ("Deposit missing", "Sent 0.2 ETH, transaction confirmed on chain, deposit never credited to my balance."),
    ("Rigged slots", "No wins in 2 weeks on the slots, feels rigged. RTP claims are nonsense."),
    ("Account closed", "Account closed with no reason given after I won. Balance still inside."),
    ("Bot support", "Live chat is just an AI bot that loops. Impossible to talk to a human."),
    ("Fees too high", "Fixed withdrawal fee is too much on small amounts."),
    ("Rank up reward missing", "Reached a new MetaMax rank and the rank up reward was never credited."),
    ("Free spins didn't work", "Free spins from the promo code never showed up in my account."),
    ("Site keeps crashing", "Game froze mid round and my balance was wrong afterwards. Bug reported, no reply."),
    ("Giveaway credit missing", "Was picked in the X giveaway, dropped my username, never received the credit."),
]
POSITIVE = [
    ("Fast payouts", "Withdrew in minutes, smooth as always."),
    ("Great promos", "Rakeback and promos are generous, support sorted my question quickly."),
    ("Love the originals", "Plinko and dice are fun and the site is fast."),
]
NAMES = ["Alex", "Sam", "Jordan", "Riley", "Casey", "Morgan", "Taylor", "Jamie", "Drew", "Quinn"]


def review(d: date, rating: int, title: str, body: str, replied: bool) -> str:
    who = f"{random.choice(NAMES)} {random.choice('ABCDEFGHJKLMNPRSTVW')}."
    out = [who, random.choice(["US", "GB", "CA", "DE", "AU"]), f"{random.randint(1, 9)} reviews",
           f"Rated {rating} out of 5 stars", d.strftime("%b %d, %Y"), title, body,
           f"Date of experience: {d.strftime('%B %d, %Y')}", "Useful", "Share"]
    if replied:
        out += ["Reply from MetaWin", "Hi, sorry to hear this. Please reach out to support."]
    return "\n".join(out)


def main() -> None:
    blocks = []
    start = END - timedelta(weeks=WEEKS)
    for i in range(WEEKS * 7):
        d = start + timedelta(days=i)
        n = random.choice([0, 1, 1, 2])
        # The spike: a lossback promo goes wrong three weeks before the end.
        in_spike = timedelta(weeks=3) <= END - d < timedelta(weeks=4)
        if in_spike:
            n += 3
        for _ in range(n):
            if random.random() < 0.25 and not in_spike:
                t, b = random.choice(POSITIVE)
                blocks.append(review(d, random.choice([4, 5]), t, b, False))
            else:
                t, b = random.choice(NEGATIVE[:2]) if in_spike and random.random() < 0.7 else random.choice(NEGATIVE)
                blocks.append(review(d, random.choice([1, 1, 2]), t, b, random.random() < 0.06))
    print("\n\n".join(reversed(blocks)))


if __name__ == "__main__":
    main()
