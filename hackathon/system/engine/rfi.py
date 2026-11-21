"""Requests for information: nothing suspicious, but one document would settle it."""
import re

from data import fmt_gbp, to_date
from rules import D, amt, cp, day, join_names, tids, total


def _ref(t):
    return str(t.get("reference") or "").upper()


def _main_in(c):
    ins = [t for t in c.a_in() if not _ref(t).startswith("SALARY")
           and _ref(t) not in ("TOP UP", "MONTHLY CONTRIBUTION", "ALLOWANCE", "CARD SETTLEMENT")]
    return sorted(ins, key=lambda t: -amt(t))


def rule_rfi(c, listed=None):
    ins = _main_in(c)
    outs = sorted(c.a_out(), key=lambda t: -amt(t))
    t = ins[0] if ins else (outs[0] if outs else (c.atx[0] if c.atx else None))
    if t is None:
        return D("RFI", "RFI_SOURCE_OF_FUNDS",
                 "The alerts on %s are not explained by anything on file. Ask the customer to explain "
                 "and evidence the activity in the review window." % c.name, [], "rfi_empty")
    ref = _ref(t)
    when = day(t)

    def say(code, what, ask, ev, tag):
        return D("RFI", code, "%s Nothing on file explains it and there is no sign of crime. %s" % (what, ask),
                 ev, tag)

    # internal list: name only, nothing settles it
    if listed is not None:
        kind, d, dob, entry, lt = listed
        lt = lt or t
        return say("RFI_COUNTERPARTY_DETAILS",
                   "The %s payment %s %s on %s matched internal list entry %s on name only, and a name "
                   "alone does not show it is the listed person." % (
                       fmt_gbp(amt(lt)), "from" if lt.get("direction") == "in" else "to", cp(lt), day(lt),
                       entry["entry_id"] if entry else "on the internal list"),
                   "Ask the customer for %s's full name, date of birth and address and what the payment "
                   "(%s) was for." % (cp(lt), lt.get("reference") or "no reference"),
                   ([entry["entry_id"]] if entry else []) + tids([lt]), "rfi_list_name")

    if "ESTATE OF" in ref:
        return say("RFI_SOURCE_OF_FUNDS",
                   "%s received %s from %s on %s, referenced %s." % (c.name, fmt_gbp(amt(t)), cp(t), when, t.get("reference")),
                   "Ask the customer for the solicitor's or executor's letter confirming their inheritance and the amount.",
                   tids([t]), "rfi_estate")
    if "SETTLEMENT AGREEMENT" in ref:
        return say("RFI_SOURCE_OF_FUNDS",
                   "%s received %s from %s on %s, referenced settlement agreement." % (c.name, fmt_gbp(amt(t)), cp(t), when),
                   "Ask the customer for the signed settlement agreement or employer's letter showing the payment.",
                   tids([t]), "rfi_settlement")
    if "SALE PROCEEDS" in ref:
        return say("RFI_SOURCE_OF_FUNDS",
                   "%s received %s from %s on %s, described as sale proceeds." % (c.name, fmt_gbp(amt(t)), cp(t), when),
                   "Ask the customer for the solicitor's completion statement for the property sale.",
                   tids([t]), "rfi_sale_proceeds")
    if "CLAIM" in ref:
        return say("RFI_SOURCE_OF_FUNDS",
                   "%s received %s from %s on %s, referenced %s." % (c.name, fmt_gbp(amt(t)), cp(t), when, t.get("reference")),
                   "Ask the customer for the insurer's settlement letter for this claim.", tids([t]), "rfi_claim")
    if "LOAN ADVANCE" in ref:
        o = outs[0] if outs else None
        gap = (to_date(o.get("booked_at")) - to_date(t.get("booked_at"))).days if o else None
        when_out = ("the same day" if gap == 0 else "the next day" if gap == 1 else
                    "%d days later" % gap if gap is not None and gap > 1 else "")
        what = ("A %s loan advance from %s on %s was paid out to %s %s (%s, reference %s)." % (
            fmt_gbp(amt(t)), cp(t), when, cp(o), when_out, fmt_gbp(amt(o)), o.get("reference"))
            if o and when_out else "A %s loan advance from %s arrived on %s." % (fmt_gbp(amt(t)), cp(t), when))
        return say("RFI_PURPOSE_DOCUMENTS", what,
                   "Ask the customer for the loan agreement and the invoice for what was bought.",
                   tids([t] + ([o] if o else [])), "rfi_loan")
    if "DEPOSIT RETURN" in ref or any("HOLDING DEPOSIT" in _ref(x) for x in outs):
        o = [x for x in outs if "DEPOSIT" in _ref(x)]
        return say("RFI_PURPOSE_DOCUMENTS",
                   "%s came in from %s on %s and %s went out to %s soon after%s." % (
                       fmt_gbp(amt(t)), cp(t), when, fmt_gbp(amt(o[0])) if o else "most of it",
                       cp(o[0]) if o else "another party", " as a holding deposit" if o else ""),
                   "Ask the customer for the letting agents' letters confirming the returned deposit and the new holding deposit.",
                   tids([t] + o[:1]), "rfi_deposit")
    trip = [x for x in c.a_in() if any(k in _ref(x) for k in ("TRIP",)) or "trip" in str(x.get("reference") or "")]
    if trip or any("BOOKING" in _ref(x) for x in outs) and "TM04" in c.rules:
        o = [x for x in outs if "BOOKING" in _ref(x)]
        payers = [x for x in c.a_in() if not _ref(x).startswith("SALARY")]
        others = [x for x in payers if not c.is_own_name(x.get("counterparty_name"))] or payers
        amounts = sorted({round(amt(x), 2) for x in others})
        if len(amounts) == 1:
            shape = "small, equal amounts" if amounts[0] <= 1000 else "equal amounts of %s" % fmt_gbp(amounts[0])
        else:
            shape = "amounts of %s to %s" % (fmt_gbp(amounts[0]), fmt_gbp(amounts[-1]))
        if trip:
            shape += " with trip references"
        return say("RFI_PURPOSE_DOCUMENTS",
                   "%d people paid %s %s (%s in total) and %s was paid "
                   "to %s%s." % (len({cp(x) for x in payers}), c.name, shape, fmt_gbp(total(payers)),
                                 fmt_gbp(amt(o[0])) if o else "a payment", cp(o[0]) if o else "a travel company",
                                 (" on %s, reference %s" % (day(o[0]), o[0].get("reference"))) if o else ""),
                   "Ask the customer for the group booking confirmation showing the traveller list and price per person.",
                   tids(o[:1] + payers[:4]), "rfi_trip")
    if c.rules == {"TM10"} or ("TM10" in c.rules and not ins):
        sell = [x for x in c.atx if x.get("channel") == "fund_redemption"]
        a = c.by_rule["TM10"][0]
        own_w = [x for x in c.txs if sell and x.get("direction") == "out" and c.is_own_name(x.get("counterparty_name"))
                 and abs(amt(x) - amt(sell[0])) <= 0.011]
        return say("RFI_PURPOSE_DOCUMENTS",
                   "Fund units were sold back %s days after purchase (%s)%s." % (
                       (a.get("measured") or {}).get("held_days", "a few"),
                       fmt_gbp(amt(sell[0])) if sell else "",
                       " and the proceeds withdrawn to the customer's own account" if own_w else ""),
                   "Ask the customer why they sold early and for any document showing what the money is needed for.",
                   tids(c.atx), "rfi_redemption")
    if "ISA" in ref and "TM08" in c.rules and not c.is_own_name(cp(t)):
        return say("RFI_COUNTERPARTY_DETAILS",
                   "%s was paid in by %s on %s, an account whose name is not exactly the customer's." % (
                       fmt_gbp(amt(t)), cp(t), when),
                   "Ask the customer for a statement showing who holds the %s account." % cp(t),
                   tids([t]), "rfi_joint")
    if c.is_own_name(t.get("counterparty_name")) or any(k in ref for k in ("SAVINGS", "OWN TRANSFER", "ISA", "FUNDING")):
        what = "%s %s %s, an account in the customer's own name, on %s." % (
            fmt_gbp(amt(t)), "came in from" if t.get("direction") == "in" else "went to",
            t.get("counterparty_account") or cp(t), when)
        if "TM07" in c.rules:
            what = "The dormant account restarted when %s %s %s, an account in the customer's own name, on %s." % (
                fmt_gbp(amt(t)), "came in from" if t.get("direction") == "in" else "went to",
                t.get("counterparty_account") or cp(t), when)
        return say("RFI_SOURCE_OF_FUNDS", what,
                   "Ask the customer for a statement of that account showing they hold it and where the money came from.",
                   tids([t]), "rfi_own_name")
    if t.get("channel") in ("cash_deposit", "cash_topup"):
        ch = c.chats()
        sale = [d for d in ch if re.search(r"\b(sell|selling|sold|sale)\b", " ".join(c.customer_lines(d)).lower())]
        return say("RFI_SOURCE_OF_FUNDS",
                   "The customer paid in %s in cash on %s%s." % (
                       fmt_gbp(amt(t)), when,
                       (" and said in %s it came from a private sale" % sale[0].id) if sale else ""),
                   "Ask the customer for proof of the sale, such as a receipt or the buyer's details." if sale else
                   "Ask the customer for evidence of where the cash came from.",
                   [x.id for x in ch] + tids([t]), "rfi_cash")
    if "ADVANCE" in ref:
        return say("RFI_COUNTERPARTY_DETAILS",
                   "%s paid %s into the account on %s, referenced %s." % (cp(t), fmt_gbp(amt(t)), when, t.get("reference")),
                   "Ask the client who %s is and for the agreement under which it pays them." % cp(t),
                   tids([t]), "rfi_advance")
    if "FREIGHT" in ref:
        pays = [x for x in c.a_in() if "FREIGHT" in _ref(x)]
        return say("RFI_COUNTERPARTY_DETAILS",
                   "%d third parties (%s) paid %s into the account referenced FREIGHT and it was paid on." % (
                       len(pays), join_names([cp(x) for x in pays]), fmt_gbp(total(pays))),
                   "Ask the client for the freight invoices it issued to these payers.",
                   tids(pays + outs[:1]), "rfi_freight")
    if "TM03" in c.rules:
        dates = [to_date(x.get("booked_at")) for x in outs if x.get("booked_at")]
        span = (max(dates) - min(dates)).days + 1 if dates else None
        staged = bool(outs) and all("STAGE" in _ref(x) for x in outs)
        return say("RFI_PURPOSE_DOCUMENTS",
                   "%d payments to %s each just under £10,000 (%s in total) were made%s%s." % (
                       len(outs), cp(outs[0]) if outs else "", fmt_gbp(total(outs)),
                       (" within 10 days" if span <= 10 else " over %d days" % span) if span else "",
                       ", with staged references" if staged else ""),
                   "Ask the client for the contract or invoice schedule covering these stage payments.",
                   tids(outs), "rfi_stages")
    if "TM04" in c.rules:
        m = c.by_rule["TM04"][0].get("measured") or {}
        who = "client" if c.inst.name == "payments_fx" else "customer"
        limit, days = m.get("limit"), m.get("window_days")
        if m.get("direction") == "incoming":
            what = "More than %s different people or companies paid the %s's account in %s days" % (limit, who, days)
        else:
            what = "The %s paid more than %s different %s in %s days" % (
                who, limit, "beneficiaries" if c.inst.name == "payments_fx" else "people or companies", days)
        return say("RFI_BUSINESS_RECORDS",
                   "%s (%s alerted)." % (what, fmt_gbp(m.get("total_gbp") or total(outs))),
                   "Ask the client for the payment file or contractor list behind the payments.",
                   tids(outs, 6), "rfi_many_payees")
    if "TM07" in c.rules:
        return say("RFI_BUSINESS_RECORDS",
                   "The dormant account restarted with %s in payments, the first to %s on %s." % (
                       fmt_gbp((c.by_rule["TM07"][0].get("measured") or {}).get("moved_gbp") or amt(t)), cp(t), when),
                   "Ask the client why trading paused and for the invoice behind the first payment.",
                   tids([t]), "rfi_dormant")
    if "TM08" in c.rules:
        return say("RFI_COUNTERPARTY_DETAILS",
                   "%s paid %s into the account on %s; the account name is not the customer's." % (cp(t), fmt_gbp(amt(t)), when),
                   "Ask the customer who %s is and why it is paying them." % cp(t), tids([t]), "rfi_third_party")
    if "TM01" in c.rules:
        return say("RFI_SOURCE_OF_FUNDS",
                   "The account received %s, mostly %s from %s on %s, well above the declared profile." % (
                       fmt_gbp((c.by_rule["TM01"][0].get("measured") or {}).get("received_gbp") or amt(t)),
                       fmt_gbp(amt(t)), cp(t), when),
                   "Ask the customer for evidence of where this money came from.", tids([t]), "rfi_tm01")
    return say("RFI_PURPOSE_DOCUMENTS",
               "%s %s %s on %s raised the alert." % (fmt_gbp(amt(t)), "from" if t.get("direction") == "in" else "to", cp(t), when),
               "Ask the customer for documents showing what the payment was for.", tids([t]), "rfi_default")
