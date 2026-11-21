"""Requests for information: nothing suspicious, but one document would settle it."""
from data import fmt_gbp
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
        return say("RFI_PURPOSE_DOCUMENTS",
                   "A %s loan advance from %s on %s was paid out to %s the next day%s." % (
                       fmt_gbp(amt(t)), cp(t), when, cp(o) if o else "another party",
                       (" (%s, reference %s)" % (fmt_gbp(amt(o)), o.get("reference"))) if o else ""),
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
        return say("RFI_PURPOSE_DOCUMENTS",
                   "%d people paid %s small, equal amounts with trip references (%s in total) and %s was paid "
                   "to %s%s." % (len({cp(x) for x in payers}), c.name, fmt_gbp(total(payers)),
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
        return say("RFI_SOURCE_OF_FUNDS",
                   "The customer paid in %s in cash on %s%s." % (
                       fmt_gbp(amt(t)), when,
                       (" and said in %s it came from a private sale" % ch[0].id) if ch else ""),
                   "Ask the customer for proof of the sale, such as a receipt or the buyer's details.",
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
        return say("RFI_PURPOSE_DOCUMENTS",
                   "%d payments to %s each just under £10,000 (%s in total) were made within 10 days, "
                   "with staged references." % (len(outs), cp(outs[0]) if outs else "", fmt_gbp(total(outs))),
                   "Ask the client for the contract or invoice schedule covering these stage payments.",
                   tids(outs), "rfi_stages")
    if "TM04" in c.rules:
        return say("RFI_BUSINESS_RECORDS",
                   "The client paid more than 25 different beneficiaries in 30 days (%s alerted)." % fmt_gbp(
                       (c.by_rule["TM04"][0].get("measured") or {}).get("total_gbp") or total(outs)),
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
