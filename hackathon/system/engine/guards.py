"""Final checks on every CLOSE before it is written.

A close rule usually proves one fact, such as a document matching one payment.
These checks look at the rest of the case and block the close (returning a new
Decision) when a detail would make a careful analyst stop. Two sets of checks
run, in this order:

A. Checks on the activity around the documented money
   1. Money moved on: explained money sent within three days to a new payee.
   2. Returned cash: no earlier review of the withdrawal, or paid in elsewhere.
   3. Payer named: a letter explaining money in must name who paid it.
   4. Dates fit: the deciding document must not post-date the money by weeks,
      nor be more than a year older than it.
   5. A one-off is a one-off: a documented one-off event must not repeat.

B. Checks on the parties and documents
   6. The customer is not on the internal list (name and date of birth).
   7. Payments firm: a held payment of £10,000 or more to a third party has an
      invoice or contract showing that amount.
   8. Documents relied on are in the customer's name; a sale receipt does not
      show the customer as the buyer.
   9. Every payer relied on is named in the documents.
  10. Explained money is not sent on within a week to someone unexplained.
  11. No document relied on is dated after the case was opened.

C. Large payments (where the policy requires an invoice): an alerted payment
   to a new payee with no invoice blocks the close; payments to suppliers the
   client has paid regularly before are named in the reason instead.

On the development cases no check blocks a close. Each one is tested by
planting its trap into a copy of a real closed case (system/tests).
"""
import re
from datetime import timedelta

from context import words
from data import close_amount, fmt_date, fmt_gbp, norm, to_date
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
    note = ("the other alerted %s, %s to %s, went to %s the client has paid regularly since %s%s." % (
        "payment" if one else "payments", fmt_gbp(total(known)), join_names(list(by), 3),
        "a supplier" if len(by) == 1 else "suppliers", fmt_date(first), held_note))
    if len(re.findall(r"[.!?](?:\s|$)", d.reason)) >= 4:
        # keep the reason within four sentences: join the note to the last one
        d.reason = d.reason.rstrip().rstrip(".") + "; " + note
    else:
        d.reason += " " + note[0].upper() + note[1:]
    d.evidence = d.evidence + [t["transaction_id"] for t in known[:3] if t["transaction_id"] not in d.evidence]
    return None



# fields that must name the customer, by document type ("*" = any type)
PARTY_FIELDS = {
    "*": ("account holder", "account holders", "borrower", "policyholder", "client", "seller"),
    "dealer_invoice": ("customer",),
    "credit_note": ("customer",),
    "supplier_invoice": ("buyer",),
    "carrier_invoice": ("bill to",),
    "invoice": ("bill to",),
}
SALE_DOCS = ("vehicle_sale_receipt", "sale_receipt")
SKIP_FLOW = ("card", "direct_debit", "standing_order", "fund_subscription", "fund_redemption")


def _docs(c, d):
    by = {x.id: x for x in c.docs}
    return [by[e] for e in d.evidence if e in by]


def _txs(c, d):
    return [c.inst.tx[e] for e in d.evidence if e in c.inst.tx]


def _core(name):
    return norm(re.sub(r"\bclient account\b|\baccount ending \d+\b|\biban ending \d+\b", "", name or "", flags=re.I))


def g_documents_in_customer_name(c, d):
    """A statement, loan, policy, invoice or receipt relied on must be made out to the customer."""
    for x in _docs(c, d):
        fields = PARTY_FIELDS["*"] + PARTY_FIELDS.get(x.type, ())
        for f in fields:
            v = x.field(f)
            if not v:
                continue
            if f == "seller" and x.type not in SALE_DOCS:
                continue
            if norm(c.name) not in norm(v):
                return D("RFI", "RFI_PURPOSE_DOCUMENTS",
                         "%s, relied on to explain the activity, is made out to %s, not to %s. A document in "
                         "someone else's name does not show the customer's own position. Ask %s for the "
                         "equivalent document in their own name, or to explain their link to %s." % (
                             x.id, v, c.name, c.name, v),
                         [x.id] + d.evidence, "guard_doc_name")
        if x.type in SALE_DOCS:
            buyer = x.field("buyer")
            if buyer and norm(buyer) == norm(c.name):
                return D("RFI", "RFI_SOURCE_OF_FUNDS",
                         "%s records the customer as the buyer, not the seller, so it does not explain cash "
                         "the customer received. Ask %s for proof of where the cash came from." % (x.id, c.name),
                         [x.id] + d.evidence, "guard_sale_buyer")
    return None


def g_payer_in_documents(c, d):
    """Every third party whose payment the close relies on must be named in the documents."""
    docs = _docs(c, d)
    if not docs:
        return None
    text = " ".join(norm(x.text) for x in docs)
    for t in _txs(c, d):
        if t.get("direction") != "in" or c.is_own_name(t.get("counterparty_name")):
            continue
        name = t.get("counterparty_name")
        if not name or t.get("channel") in ("cash_deposit", "cash_topup", "fund_redemption"):
            continue
        if (t.get("reference") or "").upper() in ("CARD SETTLEMENT", "TOP UP", "MONTHLY CONTRIBUTION"):
            continue
        if _core(name) and _core(name) not in text:
            return D("RFI", "RFI_COUNTERPARTY_DETAILS",
                     "The %s received from %s on %s is relied on to close the case, but none of the documents "
                     "(%s) mention %s. Ask %s who %s is and how the payment relates to them." % (
                         fmt_gbp(amt(t)), name, day(t), ", ".join(x.id for x in docs), name, c.name, name),
                     tids([t]) + d.evidence, "guard_payer")
    return None


def g_money_moved_on(c, d):
    """Explained money that an individual sends straight on to an unexplained third party."""
    if c.is_business:
        return None
    cited = set(d.evidence)
    ins = [t for t in _txs(c, d) if t.get("direction") == "in"]
    if not ins:
        return None
    start = min(to_date(t.get("booked_at")) for t in ins)
    received = total(ins)
    hr = c.high_risk_countries()
    onward = []
    for t in c.txs:
        when = to_date(t.get("booked_at"))
        if (t.get("direction") != "out" or t["transaction_id"] in cited or t.get("channel") in SKIP_FLOW
                or c.is_own_name(t.get("counterparty_name")) or not when
                or not (start <= when <= start + timedelta(days=7))):
            continue
        if t.get("channel") == "cash_withdrawal" or t.get("counterparty_name"):
            onward.append(t)
    if not onward or total(onward) < 0.5 * received:
        return None
    risky = [t for t in onward if (t.get("counterparty_country") or "").lower() in hr]
    if risky:
        return D("ESCALATE", "ESC_HIGH_RISK_CORRIDOR",
                 "The documented %s was followed within a week by %s sent to %s in %s, a higher-risk country, "
                 "with nothing on file explaining those payments. The documents explain where the money came "
                 "from but not where it went." % (fmt_gbp(received), fmt_gbp(total(risky)),
                                                  join_names([cp(t) for t in risky]),
                                                  risky[0].get("counterparty_country")),
                 tids(risky) + d.evidence, "guard_onward_risky")
    return D("RFI", "RFI_PURPOSE_DOCUMENTS",
             "The documented %s explains the money received, but within a week %s left the account to %s, "
             "which nothing on file explains. Ask %s what these payments were for, with supporting documents." % (
                 fmt_gbp(received), fmt_gbp(total(onward)),
                 join_names([cp(t) if t.get("counterparty_name") else "cash withdrawals" for t in onward]), c.name),
             tids(onward, 5) + d.evidence, "guard_onward")


def g_documents_after_alert(c, d):
    """Paperwork dated after the case was opened was produced in response to the alert."""
    opened = to_date((c.case.get("opened_at") or "")[:10])
    if not opened:
        return None
    late = [x for x in _docs(c, d) if x.dated and x.dated > opened
            and x.type not in ("company_register_extract", "significant_control_register", "support_chat",
                               "contact_note", "staff_note")]
    if not late:
        return None
    x = late[0]
    return D("RFI", "RFI_PURPOSE_DOCUMENTS",
             "%s is dated %s, after this case was opened on %s, so it was produced after the activity was "
             "flagged and cannot be relied on alone. Ask %s for independent, contemporaneous evidence of the "
             "same facts." % (x.id, fmt_date(x.dated), fmt_date(opened), c.name),
             [x.id] + d.evidence, "guard_late_doc")


def g_customer_on_list(c, d):
    """The customer themselves matches an internal list entry on name and date of birth."""
    dob = c.cust.get("date_of_birth")
    if not dob:
        return None
    for e in c.inst.internal_list.values():
        if norm(e.get("name")) == norm(c.name) and e.get("date_of_birth") == dob:
            return D("ESCALATE", "ESC_LISTED_PARTY",
                     "%s matches internal list entry %s on both name and date of birth (%s): %s The case "
                     "cannot be closed while the customer is a listed party." % (
                         c.name, e["entry_id"], dob, e.get("reason", "")),
                     [e["entry_id"]] + d.evidence, "guard_customer_listed")
    return None


def g_held_payment_documented(c, d):
    """Payments firm: a held payment of £10,000 or more to anyone other than the client needs an
    invoice or contract on file before it is released."""
    t = c.held
    if not t or amt(t) < 10000 or c.is_own_name(t.get("counterparty_name")):
        return None
    if any(any(abs(x - amt(t)) <= 0.011 for x in doc.amounts) for doc in c.docs):
        return None
    return D("RFI", "RFI_PURPOSE_DOCUMENTS",
             "The held payment of %s to %s on %s is £10,000 or more and goes to someone other than the "
             "client, so policy requires an invoice or contract on file before it is released, and none on "
             "file shows this amount. Ask %s for the invoice or contract behind this payment." % (
                 fmt_gbp(amt(t)), cp(t), day(t), c.name),
             tids([t]) + d.evidence, "guard_held_payment")



BLOCKING = [moved_on, returned_cash, payer_named, dates_fit, one_off_repeats,
            g_customer_on_list, g_held_payment_documented, g_documents_in_customer_name, g_payer_in_documents,
            g_money_moved_on, g_documents_after_alert,
            large_payments]  # last: may only add a sentence about known suppliers


def check_close(c, d):
    """Run every check on a CLOSE. Returns the decision to write."""
    if d.verdict != "CLOSE":
        return d
    for chk in BLOCKING:
        res = chk(c, d)
        if res is not None:
            return res
    return d
