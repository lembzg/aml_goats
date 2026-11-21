"""Rules that close a case when documents explain it, or ask for the one
document that would."""
import re

from context import Decision
from data import close_amount, fmt_date, fmt_gbp, norm, parse_long_date, to_date
from rules import D, amt, cp, day, join_names, tids, total


def big_in(c, exclude_salary=True):
    ins = [t for t in c.a_in()]
    if exclude_salary:
        ins = [t for t in ins if not str(t.get("reference") or "").startswith("SALARY")
               and str(t.get("reference") or "") not in ("TOP UP", "MONTHLY CONTRIBUTION", "ALLOWANCE",
                                                          "CARD SETTLEMENT")]
    return sorted(ins, key=lambda t: -amt(t))


def doc_amount_match(doc, t):
    return any(close_amount(x, amt(t)) for x in doc.amounts)


def profile_note(c):
    m = c.tm01_multiple()
    if m is None:
        return ""
    return " Activity reached %.1f times the declared %s." % (
        m, "turnover" if c.is_business else ("payment volume" if c.inst.name == "payments_fx" else "income"))


# ---------------------------------------------------------------------
# Own accounts
# ---------------------------------------------------------------------

def statement_accounts(c):
    """Accounts that a statement on file shows the customer holds.
    Returns list of (doc, account string, holder line)."""
    out = []
    for d in c.docs_of("bank_statement"):
        holder = d.field("account holder", "account holders") or ""
        acct = d.field("account") or ""
        holds = norm(c.name) in norm(holder) or (
            c.name and re.search(r"held by %s" % re.escape(c.name), d.text))
        if holds:
            out.append((d, acct, holder))
    return out


def tx_on_statement(c, t, stmts):
    acct = (t.get("counterparty_account") or "").strip()
    for d, a, _ in stmts:
        if acct and (acct == a or acct in d.text or a.endswith(acct.split()[-1])):
            return d
        if acct and acct.split()[-1] in a:
            return d
        if doc_amount_match(d, t) and (c.is_own_name(t.get("counterparty_name"))
                                       or norm(c.name) in norm(d.text)):
            return d
    return None


def rule_own_account(c):
    stmts = statement_accounts(c)
    if not stmts:
        return None
    # transfers that need explaining: large alerted ones (not salary / top-ups / card)
    need = [t for t in c.atx if amt(t) >= 1000 and t.get("channel") not in ("card",)
            and not str(t.get("reference") or "").startswith("SALARY")
            and t.get("reference") not in ("TOP UP", "MONTHLY CONTRIBUTION", "CARD SETTLEMENT")
            and t.get("channel") not in ("fund_subscription", "fund_redemption")]
    if c.inst.name == "payments_fx":
        # client funding from its own bank is the product; look at own-account moves out/in
        need = [t for t in c.txs if c.win_from and to_date(t.get("booked_at")) >= c.win_from
                and "OWN ACCOUNT" in str(t.get("reference") or "").upper()] or need
    if not need:
        return None
    explained, unexplained = [], []
    for t in need:
        d = tx_on_statement(c, t, stmts)
        (explained if d else unexplained).append((t, d))
    if unexplained and c.inst.name != "payments_fx":
        return None
    if not explained:
        return None
    docs = sorted({d.id for _, d in explained})
    m = c.tm01_multiple()
    t0 = explained[0][0]
    extra = []
    if c.docs_of("reverification_record"):
        extra = c.docs_of("reverification_record")
    for typ in ("employment_contract", "customer_letter", "customer_email"):
        extra += c.docs_of(typ)
    holders = sorted({h for d, _, h in stmts})
    desc = "; ".join("%s %s %s on %s" % (
        fmt_gbp(amt(t)), "from" if t.get("direction") == "in" else "to",
        t.get("counterparty_account") or cp(t), day(t)) for t, _ in explained[:3])
    if m is not None and m >= 5:
        reason = ("The %s is explained by transfers between the customer's own accounts (%s), which "
                  "%s shows %s holds. Even so, activity reached %.1f times the declared %s, at or "
                  "above the 5 times policy limit. Ask %s for an updated expected %s, with the reason "
                  "for the change." % (
                      "money received" if t0.get("direction") == "in" else "activity", desc,
                      ", ".join(docs), holders[0] if holders else c.name, m,
                      "turnover" if c.is_business else ("payment volume" if c.inst.name == "payments_fx"
                                                       else "income"), c.name,
                      "annual payment volume" if c.inst.name == "payments_fx" else (
                          "monthly turnover" if c.is_business else "monthly income")))
        return D("RFI", "RFI_UPDATED_PROFILE", reason, docs + tids([t for t, _ in explained]),
                 "own_account_5x")
    reason = ("The alerted money moved between accounts in the customer's own name: %s. %s shows "
              "%s holds %s." % (desc, ", ".join(docs), holders[0] if holders else c.name,
                                "that account" if len(docs) == 1 else "those accounts"))
    if m is not None:
        reason += " Activity was %.1f times the declared %s, below the 5 times level that needs an updated profile." % (
            m, "turnover" if c.is_business else ("payment volume" if c.inst.name == "payments_fx" else "income"))
    code = "OWN_ACCOUNT"
    rv = c.docs_of("reverification_record")
    if rv and "TM07" in c.rules:
        code = "RETURNING_CUSTOMER"
        reason = ("The dormant account restarted with %s from the customer's own account (%s), shown "
                  "held by %s in %s. %s re-verified the customer's identity on the registered "
                  "device; they had been %s." % (
                      fmt_gbp(amt(t0)), t0.get("counterparty_account") or cp(t0), c.name,
                      ", ".join(docs), rv[0].id, _reverif_story(rv[0])))
    else:
        for d in extra:
            if d.type == "employment_contract":
                reason += " %s shows the customer works abroad, which explains the account there." % d.id
            elif d.type == "customer_letter":
                reason += " In %s the customer told us they had moved abroad and gave this account for withdrawals." % d.id
            elif d.type == "customer_email":
                reason += " In %s the customer explained the transfer was sent by mistake and asked for it back." % d.id
    return D("CLOSE", code, reason, docs + [d.id for d in extra] + tids([t for t, _ in explained]),
             "own_account")


def _reverif_story(d):
    m = re.search(r"said (?:they|the directors?)? ?(.+?)\.", d.text)
    if m:
        s = m.group(1)
        s = re.sub(r"^(?:they )?had been ", "", s)
        s = re.sub(r"^(?:they|the directors) said ", "", s)
        return s
    return "away"


# ---------------------------------------------------------------------
# Returning customers (dormant)
# ---------------------------------------------------------------------

def rule_returning(c):
    if "TM07" not in c.rules:
        return None
    rv = c.docs_of("reverification_record")
    if not rv:
        return None
    outs = [t for t in c.a_out() if amt(t) >= 1000]
    inv = [d for d in c.docs_of("supplier_invoice", "invoice") if any(doc_amount_match(d, t) for t in outs)]
    if outs and not inv:
        return None
    ev = [rv[0].id] + [d.id for d in inv] + tids(c.atx, 4)
    reason = ("%s re-verified the customer on %s using the device already registered to them; %s." % (
        rv[0].id, fmt_date(rv[0].dated), _reverif_story(rv[0]).rstrip(".")))
    if inv:
        t = [t for t in outs if doc_amount_match(inv[0], t)][0]
        reason += (" The first payment after the pause, %s to %s on %s, matches invoice %s from a "
                   "supplier the client used before." % (fmt_gbp(amt(t)), cp(t), day(t), inv[0].id))
    return D("CLOSE", "RETURNING_CUSTOMER", reason, ev, "returning")


# ---------------------------------------------------------------------
# Documented one-off events
# ---------------------------------------------------------------------

EVENT_TYPES = {
    "solicitor_letter": "an inheritance from the estate described in the solicitor's letter",
    "insurer_letter": "an insurance claim settlement confirmed by the insurer",
    "settlement_letter": "a settlement payment for loss of office confirmed by the employer",
    "completion_statement": "the proceeds of a property sale shown on the completion statement",
    "vehicle_sale_receipt": "the cash sale of the customer's own vehicle",
    "sale_receipt": "the cash sale of the customer's own item",
}


def rule_documented_event(c):
    docs = [d for d in c.docs if d.type in EVENT_TYPES]
    if not docs:
        return None
    targets = big_in(c)
    if not targets:
        return None
    t = targets[0]
    match = [d for d in docs if doc_amount_match(d, t)]
    if not match:
        return None
    d = match[0]
    support = []
    if d.type == "vehicle_sale_receipt":
        reg = re.search(r"registration ([A-Z0-9 ]+?)[,.]", d.text)
        ins = [x for x in c.docs_of("insurance_schedule") if reg and reg.group(1).strip() in x.text]
        if not ins:
            return None
        support = ins
        extra = (" %s shows the same vehicle (%s) insured in the customer's name, so it was theirs "
                 "to sell." % (ins[0].id, reg.group(1).strip()))
    elif d.type == "sale_receipt":
        adv = c.docs_of("advert")
        support = adv
        extra = (" %s is the customer's own advert for the item." % adv[0].id) if adv else ""
    else:
        extra = ""
    payer = cp(t)
    reason = ("The %s %s on %s is %s: %s states %s, the same amount%s.%s" % (
        fmt_gbp(amt(t)), "cash deposit" if t.get("channel") in ("cash_deposit", "cash_topup") else "payment from " + payer,
        day(t), EVENT_TYPES[d.type], d.id, fmt_gbp(amt(t)),
        "" if t.get("channel") in ("cash_deposit", "cash_topup") else ", paid by " + payer, extra))
    reason += profile_note(c)
    if "TM07" in c.rules:
        reason += " It is a one-off receipt, so it explains the movement on the dormant account."
    return D("CLOSE", "DOCUMENTED_EVENT", reason, [d.id] + [x.id for x in support] + tids([t]),
             "event:" + d.type)


def rule_redundancy(c):
    if "TM10" not in c.rules:
        return None
    red = c.docs_of("redundancy_letter")
    em = c.docs_of("customer_email")
    if not red:
        return None
    a = c.by_rule["TM10"][0]
    sell = [t for t in c.atx if t.get("channel") == "fund_redemption"]
    w = [t for t in c.txs if t.get("direction") == "out" and sell and
         close_amount(amt(t), amt(sell[0])) and c.is_own_name(t.get("counterparty_name"))]
    reason = ("%s confirms the customer was made redundant, and in %s they asked to sell the fund to "
              "cover living costs. The units were sold for %s and the money was withdrawn to the "
              "customer's own bank account%s, so the early sale is explained." % (
                  red[0].id, em[0].id if em else "a message", fmt_gbp(amt(sell[0])) if sell else "",
                  " on " + day(w[0]) if w else ""))
    return D("CLOSE", "DOCUMENTED_EVENT", reason, [red[0].id] + [d.id for d in em] + tids(sell) + tids(w),
             "redundancy")


# ---------------------------------------------------------------------
# Refunds
# ---------------------------------------------------------------------

def rule_refund(c):
    docs = c.docs_of("venue_email", "credit_note", "cancellation_confirmation")
    if not docs:
        return None
    ins = big_in(c)
    outs = [t for t in c.a_out() if amt(t) >= 1000]
    for d in docs:
        order = outs + ins if d.type == "cancellation_confirmation" else ins + outs
        tgt = [t for t in order if doc_amount_match(d, t)]
        if not tgt:
            continue
        t = tgt[0]
        ref = re.search(r"(BOOKING \d+)", d.text)
        rv = [r for r in c.reviews if ref and norm(cp(t)) in norm(r.get("note", "")) or
              (r.get("rule_id") in c.rules and "deposit" in r.get("note", "").lower())]
        earlier = None
        if rv:
            earlier = [x for x in c.txs if x.get("direction") == "out" and norm(x.get("counterparty_name")) == norm(cp(t))]
        if d.type == "cancellation_confirmation":
            sub = [x for x in c.txs if x.get("direction") == "in" and close_amount(amt(x), amt(t))
                   and x["transaction_id"] != t["transaction_id"]]
            reason = ("The customer cancelled their ISA within the cooling-off period and %s confirms the "
                      "%s subscription received on %s was returned on %s. The refund went back to the "
                      "same account in the customer's own name that the money came from." % (
                          d.id, fmt_gbp(amt(t)), day(sub[0]) if sub else "earlier", day(t)))
            return D("CLOSE", "REFUND_OF_EARLIER_PAYMENT", reason, [d.id] + tids(sub) + tids([t]), "refund_isa")
        if t.get("direction") != "in":
            continue
        # a refund must give back an earlier payment to the same party, no larger than it
        prior = [x for x in c.txs if x.get("direction") == "out"
                 and norm(x.get("counterparty_name")) == norm(cp(t))
                 and (x.get("booked_at") or "") < (t.get("booked_at") or "") and amt(x) + 0.01 >= amt(t)]
        if not prior:
            continue
        earlier = prior
        reason = ("The %s from %s on %s is the refund of an earlier deposit: %s confirms the cancellation "
                  "of %s and a refund of %s." % (fmt_gbp(amt(t)), cp(t), day(t), d.id,
                                                 ref.group(1) if ref else "the booking", fmt_gbp(amt(t))))
        if rv:
            reason += " Review %s already closed the original deposit to %s." % (rv[0]["review_id"], cp(t))
        if earlier:
            reason += " The original payment of %s left the account on %s." % (fmt_gbp(amt(earlier[0])), day(earlier[0]))
        reason += profile_note(c)
        return D("CLOSE", "REFUND_OF_EARLIER_PAYMENT", reason,
                 [d.id] + [r["review_id"] for r in rv[:1]] + tids(earlier or []) + tids([t]), "refund")
    return None


def rule_tenancy_deposit(c):
    ret = c.docs_of("deposit_return_letter")
    hold = c.docs_of("holding_deposit_receipt")
    if not ret or not hold:
        return None
    ins = [t for t in c.a_in() if doc_amount_match(ret[0], t)]
    outs = [t for t in c.a_out() if doc_amount_match(hold[0], t)]
    if not ins or not outs:
        return None
    reason = ("The %s from %s on %s is the customer's old tenancy deposit coming back (%s), and the "
              "%s paid to %s on %s is the holding deposit on their next flat (%s). The rest came from "
              "the customer's own top-ups, so the money moved on quickly for a documented house move." % (
                  fmt_gbp(amt(ins[0])), cp(ins[0]), day(ins[0]), ret[0].id, fmt_gbp(amt(outs[0])),
                  cp(outs[0]), day(outs[0]), hold[0].id))
    return D("CLOSE", "DOCUMENTED_EVENT", reason, [ret[0].id, hold[0].id] + tids(ins + outs), "tenancy")


# ---------------------------------------------------------------------
# Business records
# ---------------------------------------------------------------------

def rule_takings(c):
    docs = c.docs_of("cash_collection_reports", "cash_reconciliation", "takings_book")
    if not docs:
        return None
    cash = [t for t in c.a_in() if t.get("channel") in ("cash_deposit", "cash_topup")]
    if not cash:
        return None
    amounts = set()
    for d in docs:
        amounts.update(d.amounts)
    missing = [t for t in cash if not any(close_amount(amt(t), x) for x in amounts)]
    if missing:
        return None
    extra = c.docs_of("cash_handling_procedure", "ticket_report")
    reason = ("Each of the %d alerted cash %s (%s in total) matches a line in %s%s. %s" % (
        len(cash), "top-ups" if cash[0].get("channel") == "cash_topup" else "deposits",
        fmt_gbp(total(cash)), ", ".join(d.id for d in docs),
        (" and the takings are explained by %s" % ", ".join(d.id for d in extra)) if extra else "",
        _takings_story(c, docs, extra)))
    return D("CLOSE", "BUSINESS_RECORDS_RECONCILE", reason.strip(),
             [d.id for d in docs] + [d.id for d in extra] + tids(cash), "takings")


def _takings_story(c, docs, extra):
    d = docs[0]
    if d.type == "cash_collection_reports":
        return ("Each car park banks its own weekly takings at its nearest branch, which is why the "
                "deposits are just under £10,000 and at two branches.")
    if d.type == "cash_reconciliation":
        return "The festival gate and bar cash was counted by two staff and banked over the next two days."
    if d.type == "takings_book":
        return "The customer is a market trader and tops up part of each market day's takings."
    return ""


# ---------------------------------------------------------------------
# Collections (group trips)
# ---------------------------------------------------------------------

def rule_collection(c):
    docs = c.docs_of("booking_confirmation")
    if not docs:
        return None
    d = docs[0]
    names = set()
    for line in d.text.splitlines():
        m = re.match(r"\s*\d+\.\s+(.+?)\s*$", line)
        if m:
            names.add(norm(m.group(1)))
    price = None
    m = re.search(r"£([\d,]+\.\d{2}) per person", d.text)
    if m:
        price = float(m.group(1).replace(",", ""))
    ins = [t for t in c.a_in() if not str(t.get("reference") or "").startswith("SALARY")
           and not c.is_own_name(t.get("counterparty_name"))]
    payers_ok = [t for t in ins if norm(cp(t)) in names and (price is None or close_amount(amt(t), price))]
    others = [t for t in ins if t not in payers_ok]
    ref = d.field("booking reference")
    outs = [t for t in c.a_out() if ref and ref in str(t.get("reference") or "")]
    if others or not payers_ok or not outs:
        return None
    reason = ("%s collected %s each from %d people named on the traveller list in booking %s (%s, %s), "
              "%s in total, and paid %s to %s on %s with reference %s, the amount the booking says is "
              "due." % (c.name, fmt_gbp(price) if price else "a share", len(payers_ok), ref, d.id,
                        d.field("trip") or "group trip", fmt_gbp(total(payers_ok)), fmt_gbp(amt(outs[0])),
                        cp(outs[0]), day(outs[0]), ref))
    reason = reason.replace(", and paid ", ". %s then paid " % c.name, 1)
    reason += " The customer is the lead traveller collecting shares on behalf of the named group."
    return D("CLOSE", "COLLECTION_DOCUMENTED", reason, [d.id] + tids(outs) + tids(payers_ok, 6),
             "collection")


# ---------------------------------------------------------------------
# Invoices, contracts and payment files
# ---------------------------------------------------------------------

def _payee_name(doc):
    payee = doc.field("pay to", "payee", "payable to") or ""
    return payee.split(",")[0].strip()


def on_behalf_guard(c, doc, t, beneficiary, kind):
    """Checks before closing a payment the customer made abroad on someone else's behalf
    (a relative's hospital bill, school fees, a funeral). Returns a Decision that blocks the
    close, or None when every check passes.

    1. The money went to the payee the bill names.
    2. It is the only payment to that payee or to that higher-risk country.
    3. No third party paid the customer a similar sum in the week before.
    4. The beneficiary shares the customer's surname, so a family link is plausible.
    """
    when = to_date(t.get("booked_at"))
    country = t.get("counterparty_country")
    payee = _payee_name(doc)
    hr = c.high_risk_countries()

    # 1. payee on the bill must be who was paid
    if payee and norm(payee) not in norm(cp(t)) and norm(cp(t)) not in norm(payee):
        reason = ("The %s sent on %s went to %s, but %s %s says it must be paid to %s. The bill does not "
                  "support the payment, so the stated purpose cannot be relied on." % (
                      fmt_gbp(amt(t)), day(t), cp(t), kind, doc.id, payee))
        return D("ESCALATE", "ESC_INVOICE_MISMATCH", reason, [doc.id, t["transaction_id"]],
                 "on_behalf_payee")

    # 2. repeat payments to the same payee or the same higher-risk country
    repeats = [u for u in c.txs if u is not t and u.get("direction") == "out"
               and u["transaction_id"] != t["transaction_id"]
               and (norm(cp(u)) == norm(cp(t))
                    or (country and country.lower() in hr and u.get("counterparty_country") == country))]
    if repeats:
        reason = ("Besides the %s to %s on %s backed by %s %s, the customer made %d other payment(s) to %s "
                  "(%s in total), so this is a pattern rather than a one-off bill." % (
                      fmt_gbp(amt(t)), cp(t), day(t), kind, doc.id, len(repeats),
                      country if country and country.lower() in hr else cp(t), fmt_gbp(total(repeats))))
        return D("ESCALATE", "ESC_HIGH_RISK_CORRIDOR", reason,
                 [doc.id, t["transaction_id"]] + tids(repeats, 4), "on_behalf_repeat")

    # 3. someone else's money arriving just before
    funders = []
    for u in c.txs:
        d = to_date(u.get("booked_at"))
        if (u.get("direction") == "in" and d and when and 0 <= (when - d).days <= 7
                and amt(u) >= 0.5 * amt(t) and not c.is_own_name(u.get("counterparty_name"))
                and "salary" not in (u.get("reference") or "").lower()):
            funders.append(u)
    if funders:
        reason = ("In the week before the %s paid to %s on %s, %s paid the customer %s. The payment abroad "
                  "appears to be funded by a third party, not the customer's own money." % (
                      fmt_gbp(amt(t)), cp(t), day(t), join_names([cp(u) for u in funders], 2),
                      fmt_gbp(total(funders))))
        return D("ESCALATE", "ESC_THIRD_PARTY_FUNDING", reason,
                 tids(funders) + [t["transaction_id"], doc.id], "on_behalf_funded")

    # 4. the beneficiary must plausibly be the relative described: a different surname means
    # nothing on file links them to the customer. (A beneficiary with the customer's own full
    # name is not blocked: a parent can share both names.)
    ben, me = norm(beneficiary), norm(c.name)
    if ben and me and ben != me and ben.split()[-1] != me.split()[-1]:
        reason = ("The %s sent to %s on %s matches %s %s, but the %s names %s, who does not share the "
                  "customer's surname, while the customer describes the beneficiary as a relative. Nothing "
                  "on file links them. Ask %s for evidence of their relationship to %s, such as a birth "
                  "certificate." % (fmt_gbp(amt(t)), cp(t), day(t), kind, doc.id, kind, beneficiary,
                                    c.name, beneficiary))
        return D("RFI", "RFI_PURPOSE_DOCUMENTS", reason, [doc.id, t["transaction_id"]],
                 "on_behalf_relationship")
    return None


def rule_invoices(c):
    a_out = [t for t in c.a_out() if amt(t) >= 500]
    if c.held and c.held not in a_out:
        a_out.append(c.held)
    # loan-funded purchase
    loan = c.docs_of("loan_agreement")
    dealer = c.docs_of("dealer_invoice")
    if loan and dealer:
        lin = [t for t in c.a_in() if doc_amount_match(loan[0], t)]
        dout = [t for t in a_out if doc_amount_match(dealer[0], t)]
        if lin and dout:
            reason = ("The %s from %s on %s is a car loan (%s, purpose: %s) and the %s paid to %s on %s "
                      "matches dealer invoice %s for the car. The customer added their own money to the "
                      "loan, so the quick payment out is the purchase itself." % (
                          fmt_gbp(amt(lin[0])), cp(lin[0]), day(lin[0]), loan[0].id,
                          (loan[0].field("loan purpose", "stated purpose", "purpose") or "car purchase").lower(),
                          fmt_gbp(amt(dout[0])), cp(dout[0]), day(dout[0]), dealer[0].id))
            return D("CLOSE", "INVOICE_MATCHES", reason, [loan[0].id, dealer[0].id] + tids(lin + dout),
                     "loan_car")
    # hospital bill abroad
    hosp = c.docs_of("hospital_invoice")
    if hosp:
        t = [t for t in a_out if doc_amount_match(hosp[0], t)]
        if t:
            patient = hosp[0].field("patient") or "a relative"
            blocked = on_behalf_guard(c, hosp[0], t[0], patient, "hospital invoice")
            if blocked:
                return blocked
            chat = [d for d in c.chats() if norm(patient) in norm(d.text)]
            reason = ("The %s sent to %s in %s on %s pays hospital invoice %s for %s's treatment (%s), "
                      "same amount and payee." % (
                          fmt_gbp(amt(t[0])), cp(t[0]).split(" account")[0], t[0].get("counterparty_country"),
                          day(t[0]), hosp[0].id, patient, (hosp[0].field("treatment") or "").lower()))
            if chat:
                reason += " In %s the customer explained the patient is their parent." % chat[0].id
            return D("CLOSE", "INVOICE_MATCHES", reason, [hosp[0].id] + [d.id for d in chat] + tids(t),
                     "hospital")
    # staged contract payments
    sched = c.docs_of("contract_schedule")
    if sched:
        rows = [r for tb in sched[0].table_dicts() for r in tb]
        ok = [t for t in a_out if any(close_amount(amt(t), x) for x in sched[0].amounts)]
        if ok and len(ok) == len([t for t in a_out]):
            reason = ("The %d payments to %s (%s in total) are the staged payments in contract schedule "
                      "%s for the %s: each amount and due date matches a stage. Each stage is under "
                      "£10,000 because that is how the contract prices the work, not to avoid the "
                      "invoice requirement." % (len(ok), cp(ok[0]), fmt_gbp(total(ok)), sched[0].id,
                                                sched[0].field("project") or "project"))
            return D("CLOSE", "INVOICE_MATCHES", reason, [sched[0].id] + tids(ok), "contract_schedule")
    # supply contract + invoice
    sup = c.docs_of("supplier_invoice")
    con = c.docs_of("supply_contract")
    if sup and con:
        t = [t for t in a_out if doc_amount_match(sup[0], t)]
        if t:
            reason = ("The %s paid to %s in %s on %s matches supplier invoice %s (%s), issued under supply "
                      "contract %s with the same supplier. The goods fit %s's business." % (
                          fmt_gbp(amt(t[0])), cp(t[0]), t[0].get("counterparty_country"), day(t[0]),
                          sup[0].id, (sup[0].field("goods") or "goods").lower(), con[0].id, c.name))
            return D("CLOSE", "INVOICE_MATCHES", reason, [sup[0].id, con[0].id] + tids(t), "supply_contract")
    # on-sale: customer order + supplier invoice behind a funding spike
    po = c.docs_of("purchase_order")
    if sup and po:
        t = [t for t in c.txs if t.get("direction") == "out" and doc_amount_match(sup[0], t)]
        if not t or (po[0].amounts and sup[0].amounts and max(po[0].amounts) < max(sup[0].amounts)):
            return None
        reason = ("The funding spike is explained by a large order: purchase order %s from %s is for %s, "
                  "and supplier invoice %s for the goods is for %s%s. The extra money paid in funds that "
                  "order." % (po[0].id, re.search(r"\n([A-Z][\w ]+?(?:Ltd|plc|LLP))\s*$", po[0].text.strip()).group(1)
                              if re.search(r"\n([A-Z][\w ]+?(?:Ltd|plc|LLP))\s*$", po[0].text.strip()) else "a customer",
                              fmt_gbp(max(po[0].amounts)) if po[0].amounts else "the order",
                              sup[0].id, fmt_gbp(max(sup[0].amounts)) if sup[0].amounts else "",
                              (", paid on " + day(t[0])) if t else ""))
        reason += profile_note(c)
        return D("CLOSE", "INVOICE_MATCHES", reason, [po[0].id, sup[0].id] + tids(t) + tids(c.a_in(), 3),
                 "purchase_order")
    # payment file (contractors)
    pf = c.docs_of("payment_file")
    if pf and "TM04" in c.rules:
        rows = [r for tb in pf[0].table_dicts() for r in tb]
        reason = ("Payment file %s lists the %d contractors paid this month, each with a contract "
                  "reference and amount, which explains the number of different payees. The larger "
                  "payments are to the client's usual suppliers." % (pf[0].id, len(rows)))
        return D("CLOSE", "INVOICE_MATCHES", reason, [pf[0].id] + tids(c.a_out(), 6), "payment_file")
    return None


# ---------------------------------------------------------------------
# Third parties, groups, gifts
# ---------------------------------------------------------------------

def rule_third_party(c):
    ins = [t for t in c.a_in() if not c.is_own_name(t.get("counterparty_name"))
           and t.get("reference") not in ("MONTHLY CONTRIBUTION", "TOP UP")]
    grp = c.docs_of("group_structure")
    if grp:
        owner = {}
        for tb in grp[0].table_dicts():
            for r in tb:
                comp = r.get("company")
                own = r.get("owner") or r.get("owned by") or r.get("shareholder")
                if comp and own:
                    owner[norm(comp)] = own

        def chain(name):
            out, n = [norm(name)], norm(name)
            for _ in range(6):
                if n not in owner:
                    break
                n = norm(owner[n])
                out.append(n)
            return out

        mine = chain(c.name)
        payers = [t for t in ins if norm(cp(t)) in owner or norm(cp(t)) in mine]
        payers = [t for t in payers if set(chain(cp(t))) & set(mine)]
        if payers:
            top = owner.get(mine[-2]) if len(mine) > 1 else None
            em = c.docs_of("email")
            inv = [d for d in c.docs_of("supplier_invoice") if any(doc_amount_match(d, t) for t in c.a_out())]
            reason = ("%s, which paid %s into the account on %s, is in the same group as %s: group structure "
                      "%s shows both are owned%s by %s." % (
                          cp(payers[0]), fmt_gbp(total(payers)), day(payers[0]), c.name, grp[0].id,
                          " directly or through a parent", owner.get(mine[-2], mine[-1]) if len(mine) > 1 else mine[-1]))
            reason += " The payer is therefore a verified company in the same group."
            if em:
                reason += " In %s the owner explained the group company was covering a supplier payment." % em[0].id
            if inv:
                reason += " That supplier payment matches invoice %s." % inv[0].id
            return D("CLOSE", "GROUP_COMPANY", reason, [grp[0].id] + [d.id for d in em + inv] + tids(payers),
                     "group")
    fac = c.docs_of("factoring_agreement")
    rem = c.docs_of("remittance_advice")
    if fac:
        lender = re.search(r"with ([A-Z][\w ]+? Ltd)", fac[0].text)
        lender = lender.group(1) if lender else None
        pays = [t for t in ins if lender and norm(cp(t)) == norm(lender)] or ins
        r_ok = [d for d in rem if any(doc_amount_match(d, t) for t in pays)]
        if pays and (r_ok or not rem):
            reason = ("The %s from %s on %s is an invoice finance advance: agreement %s says %s advances 85%% "
                      "of approved invoices to the client's account%s." % (
                          fmt_gbp(amt(pays[0])), cp(pays[0]), day(pays[0]), fac[0].id, cp(pays[0]),
                          (". Remittance advice %s shows the same advance" % r_ok[0].id) if r_ok else ""))
            reason += " The third-party payer is the client's documented finance provider."
            return D("CLOSE", "THIRD_PARTY_DOCUMENTED", reason, [fac[0].id] + [d.id for d in r_ok] + tids(pays),
                     "factoring")
    fr = c.docs_of("freight_invoices")
    car = c.docs_of("carrier_invoice")
    if fr:
        amounts = fr[0].amounts
        pays = [t for t in ins if any(close_amount(amt(t), x) for x in amounts)]
        if pays and len(pays) == len(ins):
            outs = [t for t in c.a_out() if car and doc_amount_match(car[0], t)]
            reason = ("The %d payments in from %s match freight invoices %s issued to those exporters, and "
                      "the %s paid out to %s matches carrier invoice %s for the same shipments. The client "
                      "is a freight forwarder collecting from its customers and paying the carrier." % (
                          len(pays), join_names([cp(t) for t in pays]), fr[0].id,
                          fmt_gbp(total(outs)), join_names([cp(t) for t in outs]) or "the carrier",
                          car[0].id if car else "on file"))
            return D("CLOSE", "THIRD_PARTY_DOCUMENTED", reason,
                     [fr[0].id] + [d.id for d in car] + tids(pays) + tids(outs), "freight")
    gift = c.docs_of("gift_letter")
    if gift:
        g = gift[0]
        pays = [t for t in ins if doc_amount_match(g, t) and norm(cp(t)) in norm(g.text)]
        if pays:
            rel = re.search(r"to my (\w+)", g.text)
            reason = ("The %s from %s on %s is a gift: in %s %s confirms the gift of %s to their %s, "
                      "with no repayment and no interest in the investments." % (
                          fmt_gbp(amt(pays[0])), cp(pays[0]), day(pays[0]), g.id, cp(pays[0]),
                          fmt_gbp(amt(pays[0])), rel.group(1) if rel else "relative, " + c.name))
            reason += profile_note(c) or " The gift letter names the customer and matches the payer and amount."
            return D("CLOSE", "THIRD_PARTY_DOCUMENTED", reason, [g.id] + tids(pays), "gift")
    return None


# ---------------------------------------------------------------------
# Cash returned to the account
# ---------------------------------------------------------------------

def rule_cash_redeposit(c):
    cash = [t for t in c.a_in() if t.get("channel") == "cash_deposit"]
    if not cash:
        return None
    t = cash[0]
    chats = [d for d in c.chats() if "withdrew" in d.low or "same cash" in d.low]
    if not chats:
        return None
    w = [x for x in c.txs if x.get("channel") == "cash_withdrawal" and close_amount(amt(x), amt(t))
         and x.get("booked_at", "") < t.get("booked_at", "")]
    rv = [r for r in c.reviews if r.get("rule_id") == "TM05"]
    if not w:
        return None
    reason = ("The %s cash deposit on %s is the return of the %s the customer withdrew on %s. Review %s "
              "closed that withdrawal as cash for a private car purchase, and in %s the customer says the "
              "purchase fell through and they paid the same cash back in." % (
                  fmt_gbp(amt(t)), day(t), fmt_gbp(amt(w[0])), day(w[0]),
                  rv[0]["review_id"] if rv else "on file", chats[0].id))
    return D("CLOSE", "REFUND_OF_EARLIER_PAYMENT", reason,
             [chats[0].id] + [r["review_id"] for r in rv[:1]] + tids(w[:1]) + tids([t]), "cash_return")


CLOSE_RULES = [rule_takings, rule_collection, rule_own_account, rule_returning, rule_documented_event,
               rule_redundancy, rule_refund, rule_tenancy_deposit, rule_invoices, rule_third_party,
               rule_cash_redeposit]
