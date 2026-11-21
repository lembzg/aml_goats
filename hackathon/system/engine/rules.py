"""Decision rules. Each rule looks at one case and either decides it or passes.

Rules run in order. Signs of crime are checked first, so that a document
which would otherwise explain the activity never hides them. Then the rules
that close a case when the documents explain every alert, and finally the
requests for information when the activity is plausible but undocumented.
"""
import re
from datetime import date

from context import Decision, core_name, payee_ok, same_party, words
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

PASS_ON_RE = re.compile(r"\bpass(?:ed|ing)?\b.{0,30}\bon\b|\bforward(?:ed|ing)?\b|\bsend(?:ing)? (?:it|the money|them) on\b|"
                        r"\bkeep (?:a |my |the )?(?:cut|share|percentage|commission)|\bcommission\b|\bwages\b")


def _lag_days(ins, outs):
    """The longest time, in days, between a payment out and the last money in before it."""
    worst = 0
    for o in outs:
        od = to_date(o.get("booked_at"))
        prior = [to_date(i.get("booked_at")) for i in ins if to_date(i.get("booked_at")) and od
                 and to_date(i.get("booked_at")) <= od]
        if od and prior:
            worst = max(worst, (od - max(prior)).days)
    return worst


def _lag_text(ins, outs):
    """'within hours' when every payment out left within a day of the last money in before it."""
    worst = _lag_days(ins, outs)
    return "within hours" if worst <= 1 else "within %d days" % worst


def rule_mule(c):
    reports = c.docs_of("sending_bank_fraud_report")
    chat = c.chat_text()
    payers = {cp(t) for t in c.a_in() if not c.is_own_name(t.get("counterparty_name"))}
    # the customer's own words count only when they describe passing other people's money on, and
    # other people's money actually came in
    recruited = (("payments assistant" in chat or ("crypto" in chat and PASS_ON_RE.search(chat))
                  or ("keep" in chat and "wages" in chat)) and len(payers) >= 2)
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
    atm = [t for t in c.a_out() if t.get("channel") == "cash_withdrawal"]
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
                 "on to %s%s %s." % (
                     fmt_gbp(total(c.a_in())), len({cp(t) for t in c.a_in()}), join_names(crypto, 2)
                     or "other accounts", " and ATM withdrawals" if atm else "", _lag_text(c.a_in(), outs + atm)))
    if recruited and chats:
        said = " ".join(" ".join(c.customer_lines(d)) for d in chats).lower()
        cut = ("keep a cut as wages" if "wages" in said else
               ("keep a commission" if "commission" in said else "keep a share"))
        parts.append("In %s the customer says they pass client money on to the crypto account of %s "
                     "and %s, a recruited money mule pattern." % (
                         chats[0].id, firm.group(1) if firm else "an online employer", cut))
    ev = [d.id for d in reports] + [d.id for d in chats] + tids(vtx) + tids(outs, 3)
    return D("ESCALATE", "ESC_MULE_ACTIVITY", " ".join(parts), ev, "mule")


def rule_device(c):
    """A device used by several customers is context on its own. It shows someone else controls the account
    only together with the money pattern: other people's money in and most of it moved straight out."""
    for d in c.docs_of("device_export"):
        for t in d.table_dicts():
            for r in t:
                ids = []
                for i in re.findall(r"[A-Z]{2}-C\d+", r.get("customers who logged in from it", "")):
                    if i not in ids:
                        ids.append(i)
                if len(ids) > 1:
                    ins = c.a_in()
                    outs = c.a_out()
                    third = [x for x in ins if not c.is_own_name(x.get("counterparty_name"))
                             and not str(x.get("reference") or "").upper().startswith("SALARY")]
                    if not third or total(outs) < 0.5 * total(third):
                        continue
                    reason = ("Device %s, first seen %s, was used to log in to %d customer accounts "
                              "(%s) from %s, so someone other than the customer appears to control "
                              "this account. Money from %d third parties (%s) was moved on %s, "
                              "%s in total, mostly to %s." % (
                                  r.get("device", "?"), r.get("first seen", "?"), len(ids),
                                  ", ".join(ids[:4]), r.get("login countries", "several countries"),
                                  len({cp(t) for t in ins if not c.is_own_name(t.get('counterparty_name'))}),
                                  fmt_gbp(total(ins)), _lag_text(ins, outs), fmt_gbp(total(outs)),
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


ASK_RE = re.compile(r"\b(?:ask(?:ed|ing|s)?|inquir\w*|enquir\w*|question\w*|quer(?:y|ied|ies)|"
                    r"want(?:ed|s)? to (?:know|check|confirm)|wonder(?:ed|ing)?|check(?:ed|ing)? (?:whether|if)|"
                    r"sought|curious)\b")
DENY_RE = re.compile(r"\b(?:did ?n[o’']t|did not|never|nobody|no one)\s+(?:ask|inquire|enquire|question|quer)|"
                     r"\bno questions?\b|\bwithout asking\b")
SUB_THRESHOLD = {"emoney": (800.0, 1000.0, ("cash_topup", "cash_deposit"))}


def _sub_threshold(c):
    """Alerted transactions sitting just under the institution's threshold, if there are three or more."""
    lo, hi, channels = SUB_THRESHOLD.get(c.inst.name, (9000.0, 10000.0, ("cash_deposit", "cash_topup")))
    if c.inst.name == "payments_fx":
        txs = [t for t in c.atx if t.get("direction") == "out" and lo <= amt(t) < hi]
    else:
        txs = [t for t in c.atx if t.get("channel") in channels and lo <= amt(t) < hi]
    return txs if len(txs) >= 3 else []


def rule_structuring_note(c):
    """A staff note records the customer asking whether staying under a threshold avoids checks, and the
    alerted activity really is split just under it. A note where staff explain the threshold, or where the
    customer did not ask, is not a sign of structuring."""
    for d in c.docs_of("staff_note"):
        low = d.low
        if not STRUCT_RE.search(low) or not ASK_RE.search(low) or DENY_RE.search(low):
            continue
        if "TM03" not in c.rules and not _sub_threshold(c):
            continue
        tm03 = set()
        for a in c.by_rule.get("TM03", []):
            tm03.update(a.get("transaction_ids") or [])
        deps = [t for t in c.atx if t["transaction_id"] in tm03] or _sub_threshold(c) or [
            t for t in c.atx if 0 < amt(t) < 10000 and t.get("channel") in ("cash_deposit", "cash_topup")]
        if not deps:
            deps = c.atx
        limit = "£10,000.00" if c.inst.name != "emoney" else "£1,000.00"
        n = len(deps)
        days = {str(t.get("booked_at") or "")[:10] for t in deps}
        places = {t.get("location") for t in deps if t.get("location")}
        spread = ("spread across dates and locations, " if len(days) > 1 and len(places) > 1 else
                  ("spread across %d days, " % len(days) if len(days) > 1 else ""))
        reason = ("%s records the customer asking whether amounts kept below the %s threshold "
                  "avoid reporting or checks. The alerted activity is %d transactions of %s to %s "
                  "each, %s in total, %swhich is consistent with "
                  "splitting to stay under the threshold." % (
                      d.id, limit if "invoice" not in low else "£10,000.00 invoice", n,
                      fmt_gbp(min(amt(t) for t in deps)), fmt_gbp(max(amt(t) for t in deps)),
                      fmt_gbp(total(deps)), spread))
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
        gone = parse_long_date(status)
        first = min(to_date(t.get("booked_at")) for t in pays if to_date(t.get("booked_at"))) if any(
            to_date(t.get("booked_at")) for t in pays) else None
        if gone and first and gone >= first:
            continue  # it still existed when it paid
        outs = [t for t in c.a_out()]
        chat = [x for x in c.chats() if norm(comp) in norm(x.text) or "loan" in x.low]
        reason = ("%s paid %s into the account in %d transfers referenced %s, but the company register "
                  "(%s) shows it was %s, before the payments." % (
                      comp, fmt_gbp(total(pays)), len(pays), pays[0].get("reference") or "",
                      d.id, status[0].lower() + status[1:]))
        said = " ".join(" ".join(c.customer_lines(x)) for x in chat).lower()
        if chat and "loan" in said:
            reason += (" The customer described the money in %s as %s, which does not fit payments from a "
                       "company." % (chat[0].id, "a personal loan from a friend" if "friend" in said else "a loan"))
        if outs:
            lag = _lag_days(pays, outs)
            reason += " %s was then paid out to %s %s." % (
                fmt_gbp(total(outs)), join_names([cp(t) for t in outs], 2),
                "within days" if lag <= 7 else "within %d days" % lag)
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
    lag = _lag_text(ins, outs)
    reason = ("%s received %s from %s and %s paid %s on to %s, companies the register "
              "extracts (%s) show are owned and run by its own director %s." % (
                  c.name, fmt_gbp(total(ins)), join_names([cp(t) for t in ins]),
                  "within a day" if lag == "within hours" else lag,
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


MONTH_NAMES = ["january", "february", "march", "april", "may", "june", "july", "august", "september",
               "october", "november", "december"]
_BORN_VALUE = (r"(\d{1,2} [A-Z][a-z]+ \d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4}|[A-Z][a-z]+ \d{4}|"
               r"(?:1[89]|20)\d{2})")
_BORN_KEY = r"(?:\bborn(?: on| in)?|\bDOB\b|\bdate of birth\b|\bbirth year(?: of)?|\byear of birth\b)"
BORN_AFTER_RE = re.compile(r"^[\s,(:*-]{0,4}" + _BORN_KEY + r"[\s:*,]*" + _BORN_VALUE, re.I)


def _parse_born(s):
    """(year, month or None, day or None) from '14 August 1978', 'August 1978', '1978', '1978-08-14' or '14/08/1978'."""
    s = (s or "").strip()
    if not s:
        return None
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        return (int(m.group(3)), int(m.group(2)), int(m.group(1)))
    d = to_date(s)
    if d:
        return (d.year, d.month, d.day)
    m = re.search(r"\b([A-Za-z]+) (\d{4})\b", s)
    if m and m.group(1).lower() in MONTH_NAMES:
        return (int(m.group(2)), MONTH_NAMES.index(m.group(1).lower()) + 1, None)
    m = re.search(r"\b(1[89]\d{2}|20\d{2})\b", s)
    if m:
        return (int(m.group(1)), None, None)
    return None


def _born_value(b):
    """A full date as a date (so it prints as before); a partial one as text such as 'August 1993' or '1993'."""
    y, m, d = b
    if d:
        return date(y, m, d)
    if m:
        return "%s %d" % (MONTH_NAMES[m - 1].capitalize(), y)
    return str(y)


def _dob_text(v):
    return fmt_date(v) if isinstance(v, date) else str(v or "")


def _compare_born(b, ldob):
    """'same', 'different', or None when the document cannot tell."""
    if not b or not ldob:
        return None
    y, m, d = b
    if y != ldob.year or (m is not None and m != ldob.month) or (d is not None and d != ldob.day):
        return "different"
    if m is not None:
        return "same"  # year and month (and day, when given) all agree
    return None        # the year alone agrees: not enough to say it is the same person


def _births_for(doc, name):
    """Birth dates a document gives for this person: in the same sentence, straight after the name, or in
    the same row of a register table. A date of birth elsewhere in the document belongs to someone else."""
    out = []
    pat = re.compile(r"\b" + r"\s+".join(re.escape(w) for w in (name or "").split()) + r"\b", re.I)
    if not (name or "").split():
        return out
    for line in doc.text.splitlines():
        if line.lstrip().startswith("|"):
            continue
        for m in pat.finditer(line):
            b = BORN_AFTER_RE.search(line[m.end():m.end() + 80])
            if b:
                out.append(_parse_born(b.group(1)))
            before = re.search(_BORN_KEY + r"\s+(?:of\s+)?$", line[max(0, m.start() - 30):m.start()], re.I)
            if before:
                v = re.match(r"[\s:*,]*" + _BORN_VALUE, line[m.end():m.end() + 40])
                if v:
                    out.append(_parse_born(v.group(1)))
    for tb in doc.table_dicts():
        for r in tb:
            who = r.get("officer") or r.get("person") or r.get("person with significant control") or r.get("name")
            if who and norm(who) == norm(name):
                born = r.get("born") or r.get("date of birth")
                if born:
                    out.append(_parse_born(born))
    return [b for b in out if b]


def _reg_company(d):
    """The company a register extract is about."""
    v = d.field("company")
    if v:
        return v
    first = d.text.strip().splitlines()[0] if d.text.strip() else ""
    m = re.search(r"(?:register extract|significant control) for (.+?)\s*$", first, re.I)
    return m.group(1) if m else None


def _listed_one(c, a):
    """TM22: decide whether the counterparty on one list alert is the listed party."""
    m = a.get("measured") or {}
    entry = c.inst.internal_list.get(m.get("list_entry"))
    lid = m.get("list_entry")
    on = (m.get("matched_on") or "").lower()
    party = m.get("matched_party") or ""
    t = c.inst.tx.get((a.get("transaction_ids") or [None])[0])
    tdate = to_date(t.get("booked_at")) if t else None
    tx_ids = tids([t]) if t else []
    lname = (entry or {}).get("name", party.split(",")[0])
    ldob = to_date((entry or {}).get("date_of_birth"))

    # --- company officer match: is the listed person still in control at payment?
    if "officer" in on:
        person = party.split(",")[0].strip()
        own = c.own_register()
        regs = [d for d in c.docs_of("company_register_extract", "significant_control_register") if d is not own]
        still, ended, other = [], [], []
        for d in regs:
            for n, r in _register_people(d):
                if norm(n) != norm(person):
                    continue
                born = _parse_born(r.get("born") or r.get("date of birth") or "")
                if born and ldob and _compare_born(born, ldob) == "different":
                    other.append((d, r, born))  # same name, but the register's birth date is someone else's
                    continue
                end = parse_long_date(r.get("ceased") or r.get("resigned") or "")
                if end is None or (tdate and end >= tdate):
                    still.append((d, r, end))
                else:
                    ended.append((d, r, end))
        inv = [d for d in c.docs_of("invoice") if t and any(close_amount(x, amt(t)) for x in d.amounts)
               and payee_ok(d, t)]
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
        if other and not ended:
            d0, _, born0 = other[0]
            listed_birth = (_dob_text(_born_value((ldob.year, ldob.month, None))) if born0[1]
                            else str(ldob.year))
            reason = ("%s of %s shares the name of the person on list entry %s, but %s gives a birth date of %s, "
                      "not %s, so it is a different person." % (
                          person, cp(t), lid, d0.id, _dob_text(_born_value(born0)), listed_birth))
            ev = sorted({d.id for d, _, _ in other}) + [lid] + tx_ids
            if inv:
                reason += " The %s payment on %s matches invoice %s from %s." % (
                    fmt_gbp(amt(t)), fmt_date(tdate), inv[0].id, cp(t))
                return D("CLOSE", "LIST_MATCH_DIFFERENT_PARTY", reason, ev[:1] + [inv[0].id] + ev[1:],
                         "list_officer_different")
            res = D("CLOSE", "LIST_MATCH_DIFFERENT_PARTY", reason, ev, "list_officer_different")
            res.needs_closer = True  # the list match is cleared, but nothing yet explains the payment
            return res
        return D("RFI", "RFI_COUNTERPARTY_DETAILS",
                 "%s matched list entry %s on a company officer of %s. Nothing on file shows whether "
                 "%s still controls %s. Ask the customer for the counterparty's details and what the "
                 "%s payment on %s was for." % (cp(t), lid, cp(t), person, cp(t), fmt_gbp(amt(t)),
                                                fmt_date(tdate)),
                 [lid] + tx_ids, "list_officer_unknown")

    # --- address match (e.g. a business at a shared works address)
    if "address" in on and entry:
        own = c.own_register()
        # only the counterparty's own register can show who runs it
        regs = [d for d in c.docs_of("company_register_extract") if d is not own and t
                and same_party(_reg_company(d), cp(t))]
        rcpt = c.docs_of("receipt", "invoice")
        paid = [d for d in rcpt if t and any(close_amount(x, amt(t)) for x in d.amounts)]
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
                      "at %s, while %s shows %s is run by %s%s. The %s payment on %s %s." % (
                          cp(t), lid, entry.get("name"), listed_addr, regs[0].id, cp(t),
                          join_names([n for d in regs for n, _ in _register_people(d)][:1]),
                          (" and the receipt places it at Unit %s" % unit_c) if unit_c else "",
                          fmt_gbp(amt(t)), fmt_date(tdate),
                          ("matches " + paid[0].id) if paid else "is not yet matched to a receipt or invoice"))
            res = D("CLOSE", "LIST_MATCH_DIFFERENT_PARTY", reason,
                    [d.id for d in regs] + [d.id for d in rcpt] + [lid] + tx_ids, "list_address_diff")
            if not paid:
                res.needs_closer = True
            return res
        if same_person:
            return D("ESCALATE", "ESC_LISTED_PARTY",
                     "%s is run by %s, the person on list entry %s (%s). The %s payment on %s goes to a "
                     "company the listed party controls." % (cp(t), entry.get("name"), lid,
                                                             _first_sentence(entry.get("reason", "")), fmt_gbp(amt(t)),
                                                             fmt_date(tdate)),
                     [d.id for d in regs] + [lid] + tx_ids, "list_address_same")

    # --- name match: look for a document giving the counterparty's date of birth next to their name.
    # A document saying it is the same person outweighs one saying it is not.
    if entry:
        found = []
        for d in c.docs:
            if norm(lname) not in norm(d.text):
                continue
            for b in _births_for(d, lname):
                k = _compare_born(b, ldob)
                if k:
                    found.append((k, d, b))
        for kind in ("same", "different"):
            hits = [x for x in found if x[0] == kind]
            if hits:
                return (kind, hits[0][1], _born_value(hits[0][2]), entry, t)
    return ("unknown", None, None, entry, t)


def rule_listed_party(c):
    """TM22: decide every list alert in the case. Any listed party escalates; any match nothing settles
    asks; the case can clear only when every match is shown to be someone else."""
    alerts = c.by_rule.get("TM22")
    if not alerts:
        return None
    results = [_listed_one(c, a) for a in alerts]
    if len(results) == 1:
        return results[0]
    for r in results:
        if isinstance(r, Decision) and r.verdict == "ESCALATE":
            return r
    for r in results:
        if isinstance(r, tuple) and r[0] == "same":
            return r
    for r in results:
        if isinstance(r, Decision) and r.verdict == "RFI":
            return r
    for r in results:
        if isinstance(r, tuple) and r[0] == "unknown":
            return r
    leads, ev = [], []
    for r in results:
        if isinstance(r, Decision):
            leads.append(_first_sentence(r.reason) + ".")
            ev += r.evidence
        else:
            kind, d, dob, entry, lt = r
            leads.append("%s gives %s's date of birth as %s, but list entry %s was born on %s." % (
                d.id, entry["name"], _dob_text(dob), entry["entry_id"], fmt_date(to_date(entry.get("date_of_birth")))))
            ev += [d.id, entry["entry_id"]] + (tids([lt]) if lt else [])
    res = D("CLOSE", "LIST_MATCH_DIFFERENT_PARTY", " ".join(leads), ev, "list_multi")
    res.needs_closer = True
    return res


def listed_decision(c, res):
    """Turn a name-match result into a decision (or None to carry on)."""
    kind, d, dob, entry, t = res
    lid = entry["entry_id"] if entry else (c.by_rule["TM22"][0].get("measured") or {}).get("list_entry")
    tx_ids = tids([t]) if t else []
    if kind == "same":
        return D("ESCALATE", "ESC_LISTED_PARTY",
                 "%s gives %s's date of birth as %s, the same as list entry %s (%s). The %s %s on %s "
                 "therefore involves the listed party itself." % (
                     d.id, entry["name"], _dob_text(dob), lid, _first_sentence(entry.get("reason", "")),
                     fmt_gbp(amt(t)), "from them" if t and t.get("direction") == "in" else "to them",
                     day(t) if t else ""),
                 [d.id, lid] + tx_ids, "list_name_same")
    return None


def rule_family_corridor(c):
    if "TM06" not in c.rules:
        return None
    hr = c.high_risk_countries()
    outs = [t for t in c.a_out() if (t.get("counterparty_country") or "").lower() in hr]
    explaining = c.docs_of(*EXPLAINING_DOCS)
    if explaining and outs and all(any(close_amount(x, amt(t)) for d in explaining for x in d.amounts) for t in outs):
        return None  # every payment to the higher-risk country is covered by a document
    ins = [t for t in c.atx if t.get("direction") == "in" and not c.is_own_name(t.get("counterparty_name"))
           and "SALARY" not in (t.get("reference") or "") and t.get("channel") != "cash_deposit"
           and (t.get("reference") or "") not in ("TOP UP",)]
    if len(outs) < 2 or len(ins) < 2:
        return None
    ctry = outs[0].get("counterparty_country")
    ch = c.chats()
    reason = ("%s was paid in by %d unrelated people (%s) and sent on within days to %d different "
              "recipients in %s, %s in total in amounts of about %s each. %s is a higher-risk country "
              "the customer never declared, and nothing on file explains the payments%s." % (
                  fmt_gbp(total(ins)), len(ins), join_names([cp(t) for t in ins]), len(outs), ctry,
                  fmt_gbp(total(outs)), fmt_gbp(amt(outs[0])), ctry,
                  (": the only explanation, in %s, is general family support with no names or documents" % ch[0].id)
                  if ch else ""))
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
    said = " ".join(" ".join(c.customer_lines(d)) for d in chats).lower()
    gap = None
    if outs and cash and to_date(cash[0].get("booked_at")) and to_date(outs[0].get("booked_at")):
        gap = (to_date(outs[0].get("booked_at")) - to_date(cash[0].get("booked_at"))).days
    when = {0: "the same day", 1: "the next day"}.get(gap, "within %d days" % gap if gap is not None else "")
    abroad = [t for t in outs if (t.get("counterparty_country") or "United Kingdom") != "United Kingdom"]
    reason = ("%s in cash was paid in (%s) and %s was sent %s to %s %s. The customer "
              "gave two different sources for the cash, a gift in %s and the sale of %s in %s, and "
              "nothing on file supports either%s." % (
                  fmt_gbp(total(cash)), ", ".join(fmt_gbp(amt(t)) for t in cash), fmt_gbp(total(outs)),
                  "abroad" if abroad else "on",
                  join_names(["%s in %s" % (cp(t), t.get("counterparty_country")) for t in outs]), when,
                  [d.id for k, d in stories if k == "gift"][0], "a car" if re.search(r"\bcar\b", said) else "an item",
                  [d.id for k, d in stories if k == "sale"][0],
                  "; the onward payment was described as family support for a friend of the family"
                  if "friend" in said and "family" in said else ""))
    return D("ESCALATE", "ESC_UNEXPLAINED_FUNDS", reason,
             [d.id for d in chats] + tids(cash) + tids(outs), "conflicting")


RESALE_GOODS = ("phone", "handset", "laptop", "tablet", "gold", "jewel", "watch", "consultan",
                "market access", "advisory", "software licen", "crypto")
# words too general to show a business deals in something
GENERIC_GOODS = {"services", "service", "supplies", "supply", "goods", "products", "product", "equipment",
                 "materials", "items", "parts", "shipment", "shipments", "order", "orders", "first", "second",
                 "third", "quarterly", "monthly", "invoice", "general", "trading", "business", "work", "works",
                 "january", "february", "march", "april", "may", "june", "july", "august", "september",
                 "october", "november", "december", "sprint", "batch", "delivery", "uk", "abroad", "using"}


PAYMENT_TOPIC_RE = re.compile(r"\b(?:payments?|invoices?|shipments?|goods|routed|routing|supplier|destination)\b")


def goods_fit_business(c, goods):
    """False when the goods are a classic misdescribed-trade item that the
    customer's declared business does not deal in."""
    g = (goods or "").lower()
    biz = (c.business + " " + c.name).lower()
    if (words(g) - GENERIC_GOODS) & (words(biz) - GENERIC_GOODS):
        return True  # the business deals in what the invoice is for
    for k in RESALE_GOODS:
        if re.search(r"\b" + re.escape(k), g) and not re.search(r"\b" + re.escape(k), biz):
            return False
    return True


def rule_invoice_mismatch(c):
    invs = c.docs_of("supplier_invoices", "supplier_invoice")
    notes = [d for d in c.docs_of("staff_note") if EVASIVE_RE.search(d.low) and c.is_business
             and (invs or PAYMENT_TOPIC_RE.search(d.low))]
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
    elif doc:
        reason = ("The payments of %s to %s are supported only by %s." % (
            fmt_gbp(total(outs)), join_names([cp(t) for t in outs]), doc.id))
    else:
        reason = ("No invoice on file supports the payments of %s to %s." % (
            fmt_gbp(total(outs)), join_names([cp(t) for t in outs])))
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




def rule_customer_listed(c):
    """The customer themselves matches an internal list entry on name and date of birth (or a company on
    name and company number). That is a listed party: escalate, never ask them."""
    for e in c.inst.internal_list.values():
        if not c.name or norm(e.get("name")) != norm(c.name):
            continue
        dob = c.cust.get("date_of_birth")
        num = (e.get("identifiers") or {}).get("company_number")
        if (dob and e.get("date_of_birth") == dob) or (num and num == c.cust.get("company_number")):
            reason = ("%s matches internal list entry %s on both name and %s (%s): %s The customer is a listed "
                      "party, so the case goes to the MLRO rather than being closed or put to the customer." % (
                          c.name, e["entry_id"], "date of birth" if dob and e.get("date_of_birth") == dob
                          else "company number", dob if dob and e.get("date_of_birth") == dob else num,
                          e.get("reason", "")))
            return D("ESCALATE", "ESC_LISTED_PARTY", reason, [e["entry_id"]] + tids(c.atx, 4), "customer_listed")
    return None


# ---------------------------------------------------------------------
# Pattern-based checks. These look only at the money and the documents,
# so they still work when a customer's chat is worded differently.
# ---------------------------------------------------------------------

EXPLAINING_DOCS = ("hospital_invoice", "invoice", "supplier_invoice", "supplier_invoices", "supply_contract",
                   "venue_email", "credit_note", "booking_confirmation", "gift_letter", "bank_statement",
                   "employment_contract", "customer_letter", "contract_schedule", "payment_file")
COMPANY_RE = re.compile(r"\b(ltd|limited|llp|plc|bank|council|trust|school|university|fund)\b", re.I)


def _person(name):
    return bool(name) and not COMPANY_RE.search(name)


def rule_mule_pattern(c):
    """Many unrelated people pay in and it goes straight out in repeated payments to one
    company plus cash machine withdrawals."""
    if c.docs_of("booking_confirmation", "cash_collection_reports", "cash_reconciliation", "takings_book"):
        return None
    payers = [t for t in c.a_in() if _person(t.get("counterparty_name"))
              and not c.is_own_name(t.get("counterparty_name"))]
    if len({cp(t) for t in payers}) < 5:
        return None
    outs = [t for t in c.a_out() if t.get("channel") not in ("direct_debit", "standing_order", "card")]
    by = {}
    for t in outs:
        if t.get("channel") != "cash_withdrawal":
            by.setdefault(cp(t), []).append(t)
    if not by:
        return None
    dest, dtx = max(by.items(), key=lambda kv: (len(kv[1]), total(kv[1])))
    atm = [t for t in outs if t.get("channel") == "cash_withdrawal"]
    if len(dtx) < 3 or total(dtx) + total(atm) < 0.6 * total(payers):
        return None
    reason = ("%d unrelated people paid %s into the account in small amounts and within hours it was sent on "
              "in %d payments to %s (%s) and %d cash machine withdrawals. Nothing on file explains why these "
              "people are paying the customer, and the in-and-straight-out pattern is typical of an account "
              "used to receive and pass on other people's money." % (
                  len({cp(t) for t in payers}), fmt_gbp(total(payers)), len(dtx), dest, fmt_gbp(total(dtx)),
                  len(atm)))
    ev = [d.id for d in c.docs_of("sending_bank_fraud_report")] + [d.id for d in c.chats()]
    return D("ESCALATE", "ESC_MULE_ACTIVITY", reason, ev + tids(payers, 4) + tids(dtx, 3), "mule_pattern")


def rule_investment_third_party_pattern(c):
    """Investment platform: money from several unrelated people, or redemptions paid to someone else."""
    if c.inst.name != "investment" and "TM08" not in c.rules:
        return None
    # a document on file switches this off only for the flows it covers (by amount and name)
    docs = c.docs_of(*EXPLAINING_DOCS) + c.docs_of("completion_statement", "insurer_letter", "redundancy_letter",
                                                    "cancellation_confirmation", "customer_letter", "customer_email",
                                                    "employment_contract")

    def covered(t):
        return any(any(close_amount(x, amt(t)) for x in d.amounts) and core_name(cp(t)) in norm(d.text) for d in docs)

    ins = [t for t in c.a_in() if _person(t.get("counterparty_name")) and not c.is_own_name(t.get("counterparty_name"))
           and not str(t.get("channel") or "").startswith("fund_") and not covered(t)]
    third_out = [t for t in c.a_out() if not c.is_own_name(t.get("counterparty_name"))
                 and not str(t.get("channel") or "").startswith("fund_") and not covered(t)]
    red = [t for t in c.atx if t.get("channel") == "fund_redemption"]
    if "TM10" in c.rules and red and not third_out:
        # the sale proceeds may leave in a withdrawal the alert did not list: follow them for ten days
        rd = to_date(red[0].get("booked_at"))
        third_out = [t for t in c.txs if t.get("direction") == "out" and rd and to_date(t.get("booked_at"))
                     and 0 <= (to_date(t.get("booked_at")) - rd).days <= 10 and amt(t) >= 0.5 * amt(red[0])
                     and not c.is_own_name(t.get("counterparty_name"))
                     and not str(t.get("channel") or "").startswith("fund_") and not covered(t)]
    ch = c.chats()
    if "TM10" in c.rules and red and third_out:
        held = (c.by_rule["TM10"][0].get("measured") or {}).get("held_days")
        reason = ("Fund units bought with %s were sold back %s and the %s proceeds were paid "
                  "to %s, not to the customer. Nothing on file explains why someone else receives the proceeds, "
                  "which is how fund purchases and early redemptions are used to move other people's money." % (
                      ("money from " + join_names([cp(t) for t in ins])) if ins else "the customer's money",
                      ("%s days after purchase" % held) if held is not None else "early",
                      fmt_gbp(total(red)), join_names([cp(t) for t in third_out])))
        return D("ESCALATE", "ESC_EARLY_REDEMPTION", reason,
                 [d.id for d in ch] + tids(ins) + tids(red) + tids(third_out), "early_redemption_pattern")
    if len({cp(t) for t in ins}) >= 2:
        ctry = sorted({t.get("counterparty_country") for t in ins} - {"United Kingdom"})
        reason = ("%d unrelated people (%s) paid %s into the account%s, and nothing on file shows a gift, "
                  "loan or other link to the customer." % (
                      len({cp(t) for t in ins}), join_names([cp(t) for t in ins]), fmt_gbp(total(ins)),
                      (" from " + ", ".join(ctry)) if ctry else ""))
        if third_out:
            reason += " %s was then paid out to %s." % (fmt_gbp(total(third_out)), join_names([cp(t) for t in third_out]))
        reason += " The account appears to be used for other people's money."
        return D("ESCALATE", "ESC_THIRD_PARTY_FUNDING", reason,
                 [d.id for d in ch] + tids(ins) + tids(third_out), "third_party_pattern")
    return None


def rule_cash_sent_abroad_pattern(c):
    """Large cash paid in and most of it sent abroad within days, with nothing explaining the cash."""
    if c.is_business or "TM02" not in c.rules:
        return None
    cash = [t for t in c.a_in() if t.get("channel") in ("cash_deposit", "cash_topup")]
    abroad = [t for t in c.a_out() if t.get("channel") == "swift"
              and (t.get("counterparty_country") or "United Kingdom") != "United Kingdom"
              and not c.is_own_name(t.get("counterparty_name"))]
    if not cash or not abroad or total(abroad) < 0.8 * total(cash):
        return None
    if any(d.type not in ("due_diligence_summary", "proof_of_address", "contact_note", "support_chat")
           for d in c.docs):
        return None
    reason = ("%s in cash was paid in (%s) and %s was sent abroad to %s within days. Nothing on file shows "
              "where the cash came from or why it was sent on%s." % (
                  fmt_gbp(total(cash)), ", ".join(fmt_gbp(amt(t)) for t in cash), fmt_gbp(total(abroad)),
                  join_names(["%s in %s" % (cp(t), t.get("counterparty_country")) for t in abroad]),
                  ", and the customer's explanations in %s are not supported by any document" % ", ".join(
                      d.id for d in c.chats()) if c.chats() else ""))
    return D("ESCALATE", "ESC_UNEXPLAINED_FUNDS", reason,
             [d.id for d in c.chats()] + tids(cash) + tids(abroad), "cash_abroad_pattern")


ESCALATION_RULES = [rule_customer_listed, rule_mule, rule_device, rule_structuring_note, rule_dissolved_payer,
                    rule_pass_through, rule_family_corridor, rule_investment_third_party,
                    rule_conflicting_stories, rule_invoice_mismatch, rule_payment_file_addresses,
                    rule_mule_pattern, rule_investment_third_party_pattern, rule_cash_sent_abroad_pattern]
