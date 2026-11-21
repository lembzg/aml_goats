"""Last checks before any CLOSE is written.

A close rule decides that the documents explain the case. These guards then
look for details that would make a careful analyst stop: documents made out
to someone else, a payer the documents never mention, explained money that
leaves straight away to someone unexplained, paperwork created after the
alert, or a customer who is on the institution's own internal list. If any
check fails, the case is not closed: it gets an RFI for the missing piece, or
an escalation when the detail is a sign of crime.

On the development cases no guard fires; they are there for unseen cases
that look like a documented close but carry one of these details.
"""
import re
from datetime import timedelta

from data import fmt_date, fmt_gbp, norm, to_date
from rules import D, amt, cp, day, join_names, tids, total

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


GUARDS = [g_customer_on_list, g_held_payment_documented, g_documents_in_customer_name, g_payer_in_documents,
          g_money_moved_on, g_documents_after_alert]


def check_close(c, d):
    """Return a replacement decision if a guard blocks this close, else None."""
    if d.verdict != "CLOSE":
        return None
    for g in GUARDS:
        r = g(c, d)
        if r:
            return r
    return None
