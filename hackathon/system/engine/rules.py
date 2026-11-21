"""Decision rules. Each rule looks at one case and either decides it or passes.

Rules run in order. Signs of crime are checked first, so that a document
which would otherwise explain the activity never hides them. Then the rules
that close a case when the documents explain every alert, and finally the
requests for information when the activity is plausible but undocumented.
"""
import re
from datetime import date

from context import Decision, words
from data import close_amount, fmt_date, fmt_gbp, norm, parse_long_date, to_date, MONEY_RE


def D(verdict, code, reason, evidence, tag):
    seen, ev = set(), []
    for e in evidence:
        if e and e not in seen:
            seen.add(e)
            ev.append(e)
    return Decision(verdict, code, reason, ev, tag)


def tids(txs, n=None):
    ids = [t["transaction_id"] for t in txs]
    return ids if n is None else ids[:n]


def amt(t):
    return t.get("amount_gbp") or 0.0


def total(txs):
    return round(sum(amt(t) for t in txs), 2)


def day(t):
    return fmt_date(to_date(t.get("booked_at")))


def cp(t):
    return t.get("counterparty_name") or "an unnamed counterparty"


def join_names(names, limit=3):
    names = list(dict.fromkeys(n for n in names if n))
    if len(names) <= limit:
        return ", ".join(names[:-1]) + (" and " if len(names) > 1 else "") + names[-1] if names else ""
    return ", ".join(names[:limit]) + " and %d others" % (len(names) - limit)


# =====================================================================
# 1. Signs of crime
# =====================================================================

def rule_mule(c):
    reports = c.docs_of("sending_bank_fraud_report")
    chat = c.chat_text()
    recruited = ("crypto account" in chat or "keep" in chat and "wages" in chat
                 or "payments assistant" in chat)
    if not reports and not recruited:
        return None
    victims = []
    for r in reports:
        m = re.search(r"Our customer ([A-Z][\w'-]+(?: [A-Z][\w'-]+)+) sent", r.text)
        if m:
            victims.append(m.group(1))
    vtx = [t for t in c.a_in() if t.get("counterparty_name") in victims]
    outs = [t for t in c.a_out() if t.get("channel") not in (
        "cash_withdrawal", "direct_debit", "standing_order", "card")]
    crypto = sorted({cp(t) for t in outs})
    chats = [d for d in c.chats() if "crypto" in d.low or "wages" in d.low] or c.chats()
    firm = re.search(r"for ([A-Z][\w ]+? (?:Ltd|Limited|LLP))", " ".join(
        " ".join(c.customer_lines(d)) for d in chats))
    parts = []
    if reports:
        parts.append("%d payer(s) into the account, including %s, have been reported by their banks "
                     "as authorised push payment scam victims (%s)." % (
                         len(reports), join_names(victims, 2), ", ".join(d.id for d in reports)))
    parts.append("The account received %s from %d different people and paid most of it straight "
                 "on to %s and ATM withdrawals within hours." % (
                     fmt_gbp(total(c.a_in())), len({cp(t) for t in c.a_in()}), join_names(crypto, 2)
                     or "other accounts"))
    if recruited and chats:
        parts.append("In %s the customer says they pass client money on to the crypto account of %s "
                     "and keep a cut as wages, a recruited money mule pattern." % (
                         chats[0].id, firm.group(1) if firm else "an online employer"))
    ev = [d.id for d in reports] + [d.id for d in chats] + tids(vtx) + tids(outs, 3)
    return D("ESCALATE", "ESC_MULE_ACTIVITY", " ".join(parts), ev, "mule")


def rule_device(c):
    for d in c.docs_of("device_export"):
        for t in d.table_dicts():
            for r in t:
                ids = re.findall(r"[A-Z]{2}-C\d+", r.get("customers who logged in from it", ""))
                if len(ids) > 1:
                    others = [i for i in ids if i != c.cust_id]
                    ins = c.a_in()
                    outs = c.a_out()
                    reason = ("Device %s, first seen %s, was used to log in to %d customer accounts "
                              "(%s) from %s, so someone other than the customer appears to control "
                              "this account. Money from %d third parties (%s) was moved on within "
                              "hours, %s in total, mostly to %s." % (
                                  r.get("device", "?"), r.get("first seen", "?"), len(ids),
                                  ", ".join(ids[:4]), r.get("login countries", "several countries"),
                                  len({cp(t) for t in ins if not c.is_own_name(t.get('counterparty_name'))}),
                                  fmt_gbp(total(ins)), fmt_gbp(total(outs)),
                                  join_names([cp(t) for t in outs], 2) or "other accounts"))
                    return D("ESCALATE", "ESC_ACCOUNT_MISUSE", reason,
                             [d.id] + tids(ins, 4) + tids(outs, 4), "device")
    return None


STRUCT_RE = re.compile(r"aggregat|exempt|threshold|avoid (?:the )?(?:report|check|invoice)|"
                       r"(?:split|break)\w* .{0,40}(?:under|below) £|(?:under|below) (?:the )?£[\d,]+ "
                       r"(?:limit|so|to avoid)")
EVASIVE_RE = re.compile(r"unable|unwilling|could ?n[o']t|did ?n[o']t know|vague|inconsistent|evasive|"
                        r"declined to|refused to|failed to provide|no (?:clear )?explanation|"
                        r"not (?:able|willing)")


def rule_structuring_note(c):
    for d in c.docs_of("staff_note"):
        low = d.low
        if STRUCT_RE.search(low) and not EVASIVE_RE.search(low):
            tm03 = set()
            for a in c.by_rule.get("TM03", []):
                tm03.update(a.get("transaction_ids") or [])
            deps = [t for t in c.atx if t["transaction_id"] in tm03] or [
                t for t in c.atx if 0 < amt(t) < 10000 and t.get("channel") in ("cash_deposit", "cash_topup")]
            if not deps:
                deps = c.atx
            limit = "£10,000.00" if c.inst.name != "emoney" else "£1,000.00"
            n = len(deps)
            reason = ("%s records the customer asking whether amounts kept below the %s threshold "
                      "avoid reporting or checks. The alerted activity is %d transactions of %s to %s "
                      "each, %s in total, spread across dates and locations, which is consistent with "
                      "splitting to stay under the threshold." % (
                          d.id, limit if "invoice" not in low else "£10,000.00 invoice", n,
                          fmt_gbp(min(amt(t) for t in deps)), fmt_gbp(max(amt(t) for t in deps)),
                          fmt_gbp(total(deps))))
            return D("ESCALATE", "ESC_STRUCTURING", reason, [d.id] + tids(deps), "structuring")
    return None


def rule_dissolved_payer(c):
    for d in c.docs_of("company_register_extract"):
        status = d.field("status") or ""
        comp = d.field("company") or ""
        if "dissolved" not in status.lower():
            continue
        pays = [t for t in c.a_in() if norm(t.get("counterparty_name")) == norm(comp)]
        if not pays:
            continue
        outs = [t for t in c.a_out()]
        chat = [x for x in c.chats() if norm(comp) in norm(x.text) or "loan" in x.low]
        reason = ("%s paid %s into the account in %d transfers referenced %s, but the company register "
                  "(%s) shows it was %s, before the payments." % (
                      comp, fmt_gbp(total(pays)), len(pays), pays[0].get("reference") or "",
                      d.id, status[0].lower() + status[1:]))
        if chat:
            reason += (" The customer described the money in %s as a personal loan from a friend, "
                       "which does not fit payments from a company." % chat[0].id)
        if outs:
            reason += " %s was then paid out to %s within days." % (
                fmt_gbp(total(outs)), join_names([cp(t) for t in outs], 2))
        return D("ESCALATE", "ESC_UNEXPLAINED_FUNDS", reason,
                 [d.id] + [x.id for x in chat] + tids(pays) + tids(outs), "dissolved")
    return None


def rule_pass_through(c):
    ctrl = c.controllers()
    if not ctrl:
        return None
    linked = []
    for d in c.docs_of("company_register_extract"):
        comp = d.field("company")
        if not comp or norm(comp) == norm(c.name):
            continue
        people = set()
        for t in d.table_dicts():
            for r in t:
                n = r.get("officer") or r.get("person with significant control") or r.get("person")
                if n:
                    people.add(n.strip())
        if people & ctrl:
            linked.append((comp, d, sorted(people & ctrl)[0]))
    if not linked:
        return None
    names = {norm(x[0]) for x in linked}
    outs = [t for t in c.a_out() if norm(t.get("counterparty_name")) in names]
    if not outs:
        return None
    ins = [t for t in c.a_in() if not c.is_own_name(t.get("counterparty_name"))
           and (t.get("reference") or "").upper() != "CARD SETTLEMENT"]
    person = linked[0][2]
    contracts = c.docs_of("consulting_contract")
    reason = ("%s received %s from %s and within a day paid %s on to %s, companies the register "
              "extracts (%s) show are owned and run by its own director %s." % (
                  c.name, fmt_gbp(total(ins)), join_names([cp(t) for t in ins]),
                  fmt_gbp(total(outs)), join_names(sorted({cp(t) for t in outs})),
                  ", ".join(x[1].id for x in linked), person))
    if contracts:
        reason += (" The only contract on file (%s) is a %s engagement with %s, which does not "
                   "explain these payments." % (contracts[0].id, contracts[0].field("fee") or "small",
                                                contracts[0].field("client") or "another client"))
    return D("ESCALATE", "ESC_PASS_THROUGH", reason,
             [x[1].id for x in linked] + [d.id for d in contracts] + tids(ins) + tids(outs),
             "pass_through")


def _register_people(d):
    rows = []
    for t in d.table_dicts():
        for r in t:
            n = r.get("officer") or r.get("person") or r.get("person with significant control")
            if n:
                rows.append((n.strip(), r))
    return rows


def _first_sentence(s):
    s = (s or "").strip()
    m = re.match(r"(.+?[.!?])(\s|$)", s)
    return (m.group(1) if m else s).rstrip(".")


def rule_listed_party(c):
    """TM22: decide whether the counterparty is the listed party."""
    alerts = c.by_rule.get("TM22")
    if not alerts:
        return None
    a = alerts[0]
    m = a.get("measured") or {}
    entry = c.inst.internal_list.get(m.get("list_entry"))
    lid = m.get("list_entry")
    on = (m.get("matched_on") or "").lower()
    party = m.get("matched_party") or ""
    t = c.inst.tx.get((a.get("transaction_ids") or [None])[0])
    tdate = to_date(t.get("booked_at")) if t else None
    tx_ids = tids([t]) if t else []
    lname = (entry or {}).get("name", party.split(",")[0])

    # --- company officer match: is the listed person still in control at payment?
    if "officer" in on:
        person = party.split(",")[0].strip()
        own = c.own_register()
        regs = [d for d in c.docs_of("company_register_extract", "significant_control_register") if d is not own]
        still, ended = [], []
        for d in regs:
            for n, r in _register_people(d):
                if norm(n) != norm(person):
                    continue
                end = parse_long_date(r.get("ceased") or r.get("resigned") or "")
                if end is None or (tdate and end >= tdate):
                    still.append((d, r, end))
                else:
                    ended.append((d, r, end))
        inv = [d for d in c.docs_of("invoice") if t and any(close_amount(x, amt(t)) for x in d.amounts)]
        if still:
            d, r, end = still[0]
            role = "a person with significant control" if d.type == "significant_control_register" else "a director"
            share = r.get("control") or ""
            reason = ("%s, who matches list entry %s (%s), was still %s of %s when the %s payment was "
                      "made on %s: %s shows %s%s%s. The listed party therefore controlled the "
                      "counterparty at the time of payment, even though they had resigned as director." % (
                          person, lid, _first_sentence((entry or {}).get("reason", "internal list")),
                          role, cp(t), fmt_gbp(amt(t)), fmt_date(tdate), d.id,
                          share.lower() + " " if share else "",
                          "until " + fmt_date(end) if end else "with no end date",
                          ""))
            return D("ESCALATE", "ESC_LISTED_PARTY", reason,
                     [d.id for d, _, _ in still] + [x.id for x in regs] + [lid] + tx_ids + [x.id for x in inv],
                     "list_officer_controls")
        if ended and inv:
            reason = ("%s matched list entry %s through %s, but the registers (%s) show they had left "
                      "the company and given up control before the %s payment on %s. The payment "
                      "matches invoice %s from %s." % (
                          person, lid, cp(t), ", ".join(sorted({d.id for d, _, _ in ended})),
                          fmt_gbp(amt(t)), fmt_date(tdate), inv[0].id, cp(t)))
            return D("CLOSE", "LIST_MATCH_DIFFERENT_PARTY", reason,
                     [x.id for x in regs] + [inv[0].id, lid] + tx_ids, "list_officer_left")
        return D("RFI", "RFI_COUNTERPARTY_DETAILS",
                 "%s matched list entry %s on a company officer of %s. Nothing on file shows whether "
                 "%s still controls %s. Ask the customer for the counterparty's details and what the "
                 "%s payment on %s was for." % (cp(t), lid, cp(t), person, cp(t), fmt_gbp(amt(t)),
                                                fmt_date(tdate)),
                 [lid] + tx_ids, "list_officer_unknown")

    # --- address match (e.g. a business at a shared works address)
    if "address" in on and entry:
        own = c.own_register()
        regs = [d for d in c.docs_of("company_register_extract") if d is not own]
        rcpt = c.docs_of("receipt", "invoice")
        listed_addr = entry.get("address", "")
        people = set()
        for d in regs:
            for n, r in _register_people(d):
                people.add(norm(n))
        same_person = norm(entry.get("name")) in people
        unit_l = re.search(r"Unit\s+(\w+)", listed_addr)
        unit_c = None
        for d in rcpt:
            mm = re.search(r"Unit\s+(\w+)", d.text)
            if mm:
                unit_c = mm.group(1)
        if regs and not same_person and (unit_l is None or unit_c is None or unit_l.group(1) != unit_c):
            reason = ("%s matched list entry %s only on its registered address. The listed party is %s "
                      "at %s, while %s shows %s is run by %s%s. The %s payment on %s matches %s." % (
                          cp(t), lid, entry.get("name"), listed_addr, regs[0].id, cp(t),
                          join_names([n for d in regs for n, _ in _register_people(d)][:1]),
                          (" and the receipt places it at Unit %s" % unit_c) if unit_c else "",
                          fmt_gbp(amt(t)), fmt_date(tdate), rcpt[0].id if rcpt else "the payment record"))
            return D("CLOSE", "LIST_MATCH_DIFFERENT_PARTY", reason,
                     [d.id for d in regs] + [d.id for d in rcpt] + [lid] + tx_ids, "list_address_diff")
        if same_person:
            return D("ESCALATE", "ESC_LISTED_PARTY",
                     "%s is run by %s, the person on list entry %s (%s). The %s payment on %s goes to a "
                     "company the listed party controls." % (cp(t), entry.get("name"), lid,
                                                             _first_sentence(entry.get("reason", "")), fmt_gbp(amt(t)),
                                                             fmt_date(tdate)),
                     [d.id for d in regs] + [lid] + tx_ids, "list_address_same")

    # --- name match: look for a document giving the counterparty's date of birth
    if entry:
        ldob = to_date(entry.get("date_of_birth"))
        for d in c.docs:
            if norm(lname) not in norm(d.text):
                continue
            mm = re.search(r"(?:\bborn(?: on)?|\bDOB|date of birth)[:*\s]*"
                           r"(\d{1,2} [A-Z][a-z]+ \d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})",
                           d.text, re.I)
            ddob = None
            if mm:
                s = mm.group(1)
                if "/" in s:
                    dd, mo, yy = s.split("/")
                    s = "%s-%02d-%02d" % (yy, int(mo), int(dd))
                ddob = to_date(s)
            if not ddob or not ldob:
                continue
            if ddob == ldob:
                return ("same", d, ldob, entry, t)
            return ("different", d, ddob, entry, t)
    return ("unknown", None, None, entry, t)


def listed_decision(c, res):
    """Turn a name-match result into a decision (or None to carry on)."""
    kind, d, dob, entry, t = res
    lid = entry["entry_id"] if entry else (c.by_rule["TM22"][0].get("measured") or {}).get("list_entry")
    tx_ids = tids([t]) if t else []
    if kind == "same":
        return D("ESCALATE", "ESC_LISTED_PARTY",
                 "%s gives %s's date of birth as %s, the same as list entry %s (%s). The %s %s on %s "
                 "therefore involves the listed party itself." % (
                     d.id, entry["name"], fmt_date(dob), lid, _first_sentence(entry.get("reason", "")),
                     fmt_gbp(amt(t)), "from them" if t and t.get("direction") == "in" else "to them",
                     day(t) if t else ""),
                 [d.id, lid] + tx_ids, "list_name_same")
    return None


def rule_family_corridor(c):
    if "TM06" not in c.rules:
        return None
    chat = c.chat_text()
    if "family" not in chat:
        return None
    hr = c.high_risk_countries()
    outs = [t for t in c.a_out() if (t.get("counterparty_country") or "").lower() in hr]
    ins = [t for t in c.atx if t.get("direction") == "in" and not c.is_own_name(t.get("counterparty_name"))
           and "SALARY" not in (t.get("reference") or "") and t.get("channel") != "cash_deposit"
           and (t.get("reference") or "") not in ("TOP UP",)]
    if len(outs) < 2 or len(ins) < 2:
        return None
    ctry = outs[0].get("counterparty_country")
    ch = [d for d in c.chats() if "family" in d.low]
    reason = ("%s was paid in by %d unrelated people (%s) and sent on within days to %d different "
              "recipients in %s, %s in total in near-identical amounts of %s. %s, a higher-risk country "
              "the customer never declared; the only explanation, in %s, is that it helps family there, "
              "with no names or documents." % (
                  fmt_gbp(total(ins)), len(ins), join_names([cp(t) for t in ins]), len(outs), ctry,
                  fmt_gbp(total(outs)), fmt_gbp(amt(outs[0])), ctry, ch[0].id if ch else "the chat"))
    return D("ESCALATE", "ESC_HIGH_RISK_CORRIDOR", reason,
             [d.id for d in ch] + tids(ins) + tids(outs), "family_corridor")


def rule_investment_third_party(c):
    chat = c.chat_text()
    if not chat:
        return None
    ch = c.chats()
    ins = [t for t in c.a_in() if not c.is_own_name(t.get("counterparty_name"))
           and "MONTHLY" not in (t.get("reference") or "")
           and not str(t.get("channel") or "").startswith("fund_")]
    outs = [t for t in c.a_out() if not str(t.get("channel") or "").startswith("fund_")]
    if "invest for a few people" in chat or ("invest for" in chat and "back quickly" in chat):
        red = [t for t in c.atx if t.get("channel") == "fund_redemption"]
        third = [t for t in outs if not c.is_own_name(t.get("counterparty_name"))]
        reason = ("Fund units were bought with %s and sold back after a few days, and the %s proceeds "
                  "were paid to %s, not the customer. In %s the customer says they invest for several "
                  "people who need their money back quickly, so the account is being used to move "
                  "other people's money through fund purchases and early redemptions." % (
                      join_names(["money from " + cp(t) for t in ins], 2) or "third-party money",
                      fmt_gbp(total(red) or total(third)), join_names([cp(t) for t in third]),
                      ch[0].id))
        return D("ESCALATE", "ESC_EARLY_REDEMPTION", reason,
                 [d.id for d in ch] + tids(ins) + tids(red) + tids(third), "early_redemption")
    if ("cannot open an account" in chat or "investing through" in chat or "through mine" in chat
            or "invest for them" in chat or "changed their minds" in chat):
        reason = ("%d unrelated third parties (%s) paid %s into the account. In %s the customer says "
                  "the money belongs to other people investing through their account" % (
                      len({cp(t) for t in ins}), join_names([cp(t) for t in ins]),
                      fmt_gbp(total(ins)), ch[0].id))
        if outs:
            reason += (", and %s was then paid out to %s." % (
                fmt_gbp(total(outs)), join_names([cp(t) for t in outs])))
        else:
            ctry = {t.get("counterparty_country") for t in ins} - {"United Kingdom"}
            reason += ("%s." % (", from %s" % ", ".join(sorted(ctry)) if ctry else ""))
        reason += " The account is being used for people who are not the customer."
        return D("ESCALATE", "ESC_THIRD_PARTY_FUNDING", reason,
                 [d.id for d in ch] + tids(ins) + tids(outs), "third_party_chat")
    return None


def rule_conflicting_stories(c):
    """Cash paid in and sent on, with explanations that contradict each other."""
    chats = c.chats()
    if len(chats) < 2:
        return None
    stories = []
    for d in chats:
        low = " ".join(c.customer_lines(d)).lower()
        if "gift" in low:
            stories.append(("gift", d))
        if "selling" in low or "sold" in low:
            stories.append(("sale", d))
    kinds = {k for k, _ in stories}
    if len(kinds) < 2:
        return None
    cash = [t for t in c.a_in() if t.get("channel") in ("cash_deposit", "cash_topup")]
    outs = c.a_out()
    if not cash:
        return None
    reason = ("%s in cash was paid in (%s) and %s was sent abroad to %s the next day. The customer "
              "gave two different sources for the cash, a gift in %s and the sale of a car in %s, and "
              "nothing on file supports either; the onward payment was described as family support "
              "for a friend of the family." % (
                  fmt_gbp(total(cash)), ", ".join(fmt_gbp(amt(t)) for t in cash), fmt_gbp(total(outs)),
                  join_names(["%s in %s" % (cp(t), t.get("counterparty_country")) for t in outs]),
                  [d.id for k, d in stories if k == "gift"][0], [d.id for k, d in stories if k == "sale"][0]))
    return D("ESCALATE", "ESC_UNEXPLAINED_FUNDS", reason,
             [d.id for d in chats] + tids(cash) + tids(outs), "conflicting")


RESALE_GOODS = ("phone", "handset", "laptop", "tablet", "gold", "jewel", "watch", "consultan",
                "market access", "advisory", "services", "software licen", "crypto")


def goods_fit_business(c, goods):
    """False when the goods are a classic misdescribed-trade item that the
    customer's declared business does not deal in."""
    g = (goods or "").lower()
    biz = (c.business + " " + c.name).lower()
    for k in RESALE_GOODS:
        if k in g and k not in biz:
            return False
    return True


def rule_invoice_mismatch(c):
    notes = [d for d in c.docs_of("staff_note") if EVASIVE_RE.search(d.low)]
    invs = c.docs_of("supplier_invoices", "supplier_invoice")
    bad = []
    for d in invs:
        goods = []
        for t in d.table_dicts():
            for r in t:
                if r.get("goods"):
                    goods.append(r["goods"])
        if d.field("goods"):
            goods.append(d.field("goods"))
        for gds in goods:
            if not goods_fit_business(c, gds):
                bad.append((d, gds))
                break
    if not notes and not bad:
        return None
    outs = c.a_out() or c.atx
    doc = bad[0][0] if bad else (invs[0] if invs else None)
    if bad:
        reason = ("%s are invoices for %s, but %s %s. The goods do not fit the business, so the "
                  "invoices do not support the %s of payments." % (
                      doc.id, bad[0][1].lower(), c.name, c.business[0].lower() + c.business[1:],
                      fmt_gbp(total(outs))))
    else:
        reason = ("The payments of %s to %s are supported only by %s." % (
            fmt_gbp(total(outs)), join_names([cp(t) for t in outs]), doc.id if doc else "no invoices"))
    if notes:
        reason += (" In %s the director described the payments differently and could not explain "
                   "the mismatch." % notes[0].id)
    if not bad and doc:
        gds = [r.get("goods") for t in doc.table_dicts() for r in t if r.get("goods")]
        if gds:
            reason += " The invoices are for %s, which %s does not deal in." % (gds[0].lower(), c.name)
    ev = ([doc.id] if doc else []) + [d.id for d in notes] + tids(outs)
    return D("ESCALATE", "ESC_INVOICE_MISMATCH", reason, ev, "invoice_mismatch")


def rule_payment_file_addresses(c):
    for d in c.docs_of("payment_file"):
        for t in d.table_dicts():
            addrs = [r.get("address") for r in t if r.get("address")]
            if len(addrs) < 4:
                continue
            uniq = set(addrs)
            if len(uniq) * 2 <= len(addrs):
                funders = [x for x in c.a_in() if not c.is_own_name(x.get("counterparty_name"))]
                ctry = sorted({r.get("country") for r in t if r.get("country")})
                reason = ("Payment file %s lists %d payees in %s but only %d UK addresses between them, "
                          "so the payees are not independent." % (d.id, len(addrs), ", ".join(ctry),
                                                                   len(uniq)))
                if funders:
                    reason += (" The payouts were funded by %s, a third party with no documented link "
                               "to %s, which paid in %s." % (join_names([cp(x) for x in funders]),
                                                             c.name, fmt_gbp(total(funders))))
                    code = "ESC_THIRD_PARTY_FUNDING"
                else:
                    code = "ESC_PASS_THROUGH"
                return D("ESCALATE", code, reason, [d.id] + tids(funders) + tids(c.a_out(), 3),
                         "payment_file_addr")
    return None


ESCALATION_RULES = [rule_mule, rule_device, rule_structuring_note, rule_dissolved_payer,
                    rule_pass_through, rule_family_corridor, rule_investment_third_party,
                    rule_conflicting_stories, rule_invoice_mismatch, rule_payment_file_addresses]
