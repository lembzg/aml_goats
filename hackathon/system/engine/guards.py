"""Final checks on every CLOSE before it is written.

A close rule usually proves one fact: a document matches one payment. These
checks look at the rest of the case, so that a document which explains part of
the activity cannot close a case whose other parts are unexplained or
suspicious. Each check either passes (returns None), blocks the close (returns a
new Decision), or adds a sentence to the reason.

1. Money moved on. Money a document explains must not be sent straight on to
   someone new that nothing on file explains.
2. Payer matches. A letter explaining a payment in must name who paid it.
3. Dates fit. The deciding document must not post-date the money by weeks, nor
   be years older than it.
4. A one-off is a one-off. A documented one-off event must not repeat.
5. Returned cash. The earlier withdrawal must have been reviewed, and the cash
   paid back in where it was taken out.
6. Large payments need an invoice (where the policy says so). An alerted payment
   to someone other than the client, at or above the policy's limit, needs an
   invoice, contract or payment file. A payee the client has paid regularly
   before is named in the reason; a new payee blocks the close.
"""
import re
from datetime import timedelta

from data import close_amount, fmt_date, fmt_gbp, norm, to_date
from context import words
from rules import D, amt, cp, day, join_names, tids, total

ROUTINE = ("card", "direct_debit", "standing_order")


def _stays(t):
    """Buying fund units keeps the money on the platform: it is investing, not moving money on."""
    ch = (t.get("channel") or "").lower()
    return ch in ROUTINE or ch.startswith("fund_") or "invest" in ch
ONE_OFF = ("event:", "redundancy", "cash_return")


def _when(t):
    return to_date(t.get("booked_at"))


def _doc_amounts(c):
    out = set()
    for d in c.docs:
        for x in d.amounts:
            out.add(round(x, 2))
    return out


def _paid_before(c, name, before):
    return [t for t in c.txs if t.get("direction") == "out" and norm(cp(t)) == norm(name)
            and _when(t) and before and _when(t) < before]


def _explained(c, d):
    ids = set(d.evidence)
    return [c.inst.tx[i] for i in d.evidence if i in c.inst.tx], ids


# 1 -------------------------------------------------------------------------
def moved_on(c, d):
    if not d.tag.startswith(ONE_OFF):
        return None
    txs, ids = _explained(c, d)
    ins = [t for t in txs if t.get("direction") == "in"]
    if not ins:
        return None
    covered = _doc_amounts(c)
    flagged = []
    for t in ins:
        start = _when(t)
        if not start:
            continue
        for u in c.txs:
            w = _when(u)
            if (u.get("direction") != "out" or u["transaction_id"] in ids or not w
                    or not (start <= w <= start + timedelta(days=3))
                    or _stays(u) or c.is_own_name(u.get("counterparty_name"))
                    or amt(u) < 0.5 * amt(t) or round(amt(u), 2) in covered
                    or _paid_before(c, cp(u), c.win_from)):
                continue
            flagged.append(u)
    if not flagged:
        return None
    reason = ("The money in is documented, but it did not stay: within three days %s went to %s, a new "
              "payee that nothing on file explains. A document explaining where money came from does not "
              "explain it being sent straight on." % (
                  fmt_gbp(total(flagged)), join_names([cp(u) for u in flagged], 2)))
    return D("ESCALATE", "ESC_UNEXPLAINED_FUNDS", reason, tids(flagged) + d.evidence, d.tag + "|moved_on")


# 2 -------------------------------------------------------------------------
def payer_named(c, d):
    if not d.tag.startswith("event:"):
        return None
    txs, _ = _explained(c, d)
    docs = [x for x in c.docs if x.id in d.evidence]
    if not docs:
        return None
    for t in txs:
        if t.get("direction") != "in" or t.get("channel") in ("cash_deposit", "cash_topup"):
            continue
        if not (words(cp(t)) & words(docs[0].text)):
            reason = ("%s states the amount of the %s received on %s, but it does not name the payer, %s. "
                      "The document may describe a different payment. Ask %s for evidence that %s made this "
                      "payment and why." % (docs[0].id, fmt_gbp(amt(t)), day(t), cp(t), c.name or "the customer",
                                            cp(t)))
            return D("RFI", "RFI_SOURCE_OF_FUNDS", reason, [docs[0].id, t["transaction_id"]],
                     d.tag + "|payer")
    return None


# 3 -------------------------------------------------------------------------
def dates_fit(c, d):
    if not (d.tag.startswith("event:") or d.code == "INVOICE_MATCHES"):
        return None
    txs, _ = _explained(c, d)
    docs = [x for x in c.docs if x.id in d.evidence]
    if not txs or not docs or not docs[0].dated:
        return None
    doc = docs[0]
    first = min(_when(t) for t in txs if _when(t))
    last = max(_when(t) for t in txs if _when(t))
    if (doc.dated - last).days > 30:
        why = "is dated %s, more than a month after the money moved on %s" % (fmt_date(doc.dated), fmt_date(last))
    elif (first - doc.dated).days > 400:
        why = "is dated %s, more than a year before the money moved on %s" % (fmt_date(doc.dated), fmt_date(first))
    else:
        return None
    reason = ("%s matches the amount but %s, so it cannot be relied on to explain this activity. Ask %s for "
              "a current document showing what the payment was for." % (doc.id, why, c.name or "the customer"))
    return D("RFI", "RFI_PURPOSE_DOCUMENTS", reason, [doc.id] + tids(txs), d.tag + "|dates")


# 4 -------------------------------------------------------------------------
def one_off_repeats(c, d):
    if not d.tag.startswith(("event:", "redundancy")):
        return None
    txs, ids = _explained(c, d)
    ins = [t for t in txs if t.get("direction") == "in"]
    if not ins:
        return None
    t = max(ins, key=amt)
    cash = t.get("channel") in ("cash_deposit", "cash_topup")
    others = [u for u in c.txs if u.get("direction") == "in" and u["transaction_id"] not in ids
              and amt(u) >= 0.5 * amt(t)
              and ((cash and u.get("channel") == t.get("channel"))
                   or (not cash and norm(cp(u)) == norm(cp(t))))]
    if not others:
        return None
    reason = ("The %s on %s is documented as a one-off, but similar money has come in before: %s on %s%s. A "
              "repeated one-off is not explained by a single document. Ask %s where each of these payments came "
              "from." % (fmt_gbp(amt(t)), day(t), fmt_gbp(amt(others[0])), day(others[0]),
                         (" and %d more" % (len(others) - 1)) if len(others) > 1 else "", c.name or "the customer"))
    return D("RFI", "RFI_SOURCE_OF_FUNDS", reason, d.evidence + tids(others, 3), d.tag + "|repeat")


# 5 -------------------------------------------------------------------------
def returned_cash(c, d):
    if d.tag != "cash_return":
        return None
    txs, _ = _explained(c, d)
    wd = [t for t in txs if t.get("channel") == "cash_withdrawal"]
    dp = [t for t in txs if t.get("channel") in ("cash_deposit", "cash_topup")]
    reviewed = any(r.get("verdict") == "CLOSE" and "cash" in (r.get("note") or "").lower() for r in c.reviews)
    problem = None
    if not reviewed:
        problem = "there is no earlier review of the withdrawal on file"
    elif wd and dp and wd[0].get("location") and dp[0].get("location") \
            and norm(wd[0]["location"]) != norm(dp[0]["location"]):
        problem = "the cash was taken out at %s but paid back in at %s" % (wd[0]["location"], dp[0]["location"])
    if not problem:
        return None
    t = dp[0] if dp else txs[0]
    reason = ("The customer says the %s cash paid in on %s is the cash they withdrew earlier, but %s. Their "
              "word alone does not show it is the same money. Ask for evidence of where the cash was kept and "
              "why the purchase fell through, such as messages with the seller." % (fmt_gbp(amt(t)), day(t), problem))
    return D("RFI", "RFI_SOURCE_OF_FUNDS", reason, d.evidence, d.tag + "|cash")


# 6 -------------------------------------------------------------------------
INVOICE_LIMIT_RE = re.compile(r"Payments of £([\d,]+)(?:\.\d+)? or more to anyone other than the client need an "
                              r"invoice or contract", re.I)


def large_payments(c, d):
    m = INVOICE_LIMIT_RE.search(c.inst.policy or "")
    if not m:
        return None
    limit = float(m.group(1).replace(",", ""))
    covered = _doc_amounts(c)
    cited = set(d.evidence)
    uncovered = [t for t in c.atx if t.get("direction") == "out" and amt(t) >= limit
                 and not c.is_own_name(t.get("counterparty_name"))
                 and round(amt(t), 2) not in covered]
    if not uncovered:
        return None
    new, known = [], []
    for t in uncovered:
        (known if _paid_before(c, cp(t), c.win_from) else new).append(t)
    if new:
        reason = ("%s also paid %s to %s, which the client has not paid before, and no invoice or contract on "
                  "file covers it. Payments of %s or more to anyone other than the client need one. Ask the "
                  "client for the invoice or contract for each of these payments." % (
                      c.name or "The client", fmt_gbp(total(new)), join_names([cp(t) for t in new], 2),
                      fmt_gbp(limit)))
        return D("RFI", "RFI_PURPOSE_DOCUMENTS", reason, tids(new, 4) + d.evidence, d.tag + "|new_payee")
    # every uncovered payment is to an established supplier: say so in the reason
    by = {}
    for t in known:
        by.setdefault(cp(t), []).append(t)
    first = min(_when(p) for n in by for p in _paid_before(c, n, c.win_from))
    one = len(known) == 1
    held_note = ""
    if c.held:
        is_held = any(t["transaction_id"] == c.held["transaction_id"] for t in known)
        held_note = "" if is_held else (", and %s not the held payment" % ("is" if one else "are"))
    if "usual suppliers" in d.reason:
        d.reason = d.reason.replace(" The larger payments are to the client's usual suppliers.", "")
    d.reason += (" The other alerted %s, %s to %s, went to %s the client has paid regularly since %s%s." % (
        "payment" if one else "payments", fmt_gbp(total(known)), join_names(list(by), 3),
        "a supplier" if len(by) == 1 else "suppliers", fmt_date(first), held_note))
    d.evidence = d.evidence + [t["transaction_id"] for t in known[:3] if t["transaction_id"] not in d.evidence]
    return None


CHECKS = [moved_on, returned_cash, payer_named, dates_fit, one_off_repeats, large_payments]


def check_close(c, d):
    """Run every check on a CLOSE. Returns the decision to write."""
    if d.verdict != "CLOSE":
        return d
    for chk in CHECKS:
        res = chk(c, d)
        if res is not None:
            return res
    return d
