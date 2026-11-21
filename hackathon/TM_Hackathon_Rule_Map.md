# Fin Crime Hackathon · Transaction Monitoring

*Diligent AI — Source: https://hosted-pages.godiligent.ai/hackathon-tm-track/ (captured 21.11.2026)*

## Build a system that closes alerts safely

Banks, payment firms and investment platforms run monitoring rules that raise alerts on unusual activity. Most alerts turn out to be explained, but every one has to be reviewed by an analyst. Build a system that reads an alert with its evidence and decides how to close it, as carefully and consistently as a good analyst would.

| Four institutions | Ten monitoring rules | 4,000 development cases | 800 unseen test cases |
|---|---|---|---|

UK rules, amounts in pounds.

---

## What is transaction monitoring?

*A short introduction for anyone new to financial crime.*

Criminals need banks and payment firms to move money: to hide where it came from, to pay for crime, or to pass on money stolen from scam victims. So every bank, payment firm and investment platform has to watch the money moving through its customers' accounts for signs of crime, and report what it suspects. Watching for those signs is called **transaction monitoring**.

### Why firms must do it

- **The law requires it.** In the UK, firms must monitor their customers' activity and report suspicion to the National Crime Agency. Firms that fail to can be fined heavily.
- **It stops real harm.** It catches money laundering, the proceeds of fraud and scams, and money moving to fund terrorism.
- **It takes a lot of work.** Automatic rules raise many alerts, and most turn out to have an ordinary explanation. Each one still needs a person to review it, which is why a system that closes the explained ones safely is valuable.

### How it works

1. **Customers move money** — Card payments, transfers, cash, investments
2. **Rules check every transaction** — For example, cash of £10,000 or more, or money in and straight out again
3. **A rule raises an alert** — A flag that something looks unusual, not proof of crime
4. **An analyst reviews the alert** *(YOUR SYSTEM)* — Reads the customer's profile, transactions and documents, then decides:
   - **CLOSE** — The evidence explains it
   - **RFI** — Ask the customer for one document or answer
   - **ESCALATE** — Pass to the MLRO, who may report it to the authorities

### Who does what

- **The customer** — A person or a business with an account. They move money, and they are the one asked for documents in an RFI.
- **The analyst, the user of your system** — Works through the queue of alerts. For each one they gather the evidence and decide: close it, ask the customer, or escalate. This review is called *triage*, and it is the step your system does.
- **The MLRO** — The Money Laundering Reporting Officer: the senior person every regulated firm must appoint. They review escalated alerts and decide whether to file a suspicious activity report with the authorities.

### An example: Fernbrook Bakery Ltd

A small bakery with a business account. When it joined, it said it takes about £20,000 a month from its shop and pays UK suppliers and staff. Here is part of its month, with the three alerts the rules raised.

| Date | What happened | In | Out | Alert |
|---|---|---|---|---|
| 1 Sep | Card takings from the shop | £4,210 | | |
| 3 Sep | Flour from Millstone Foods Ltd | | £1,860 | |
| 5 Sep | Staff wages | | £6,400 | |
| 9 Sep | Payment to Coldline Ovens Ltd | | £18,500 | A |
| 16 Sep | Money from Jordan Fernley, a private person | £25,000 | | B |
| 22 to 23 Sep | 12 payments from different people, £400 to £950 each | £8,900 | | C |
| 23 Sep | Sent to an account in another country | | £8,700 | C |

| Alert | Why the rule fired | What the analyst finds | Outcome |
|---|---|---|---|
| A | A payment far bigger than the bakery usually makes | An invoice from Coldline Ovens Ltd for a new oven, for the same amount, to the same company | **CLOSE** — The invoice explains the payment. |
| B | Money in far above what the bakery said it expects | Nothing on file says what the money is. Nothing about it looks criminal. | **RFI** — Ask what it is, for example the loan agreement if it is a loan from the director. |
| C | Money in from many people, and straight out again | Two of the senders' banks report their customers were scammed. The bakery calls the payments catering orders, but there are no orders. | **ESCALATE** — The MLRO decides whether to report it. |

Every case in the hackathon looks like this. You get the alerts, the customer, their transactions and the documents on file, and your system plays the analyst. **Closing alert C would be the worst possible mistake.**

---

## The challenge

- **You get:** 4,000 development cases, 1,000 from each institution, with each institution's SOP. Every case has its alerts, the customer's profile, their transactions, the documents on file, earlier reviews and the institution's internal list. The cases have no answers.
- **You build:** A system that reads a folder of cases and writes a verdict, a reason and the evidence it relied on for every case. It can use rules, language models or both.
- **We test:** Your system, five times, on 800 cases it has never seen, 200 from each institution, from the same institutions, in the same format and under the same policies. Those unseen cases decide the result.

---

## How a case closes

Every case ends in one of three verdicts, with a reason.

- **CLOSE** — The evidence explains all the activity the alerts raised and it fits what the institution knows about the customer. At the payments and FX firm, a held payment is released.
- **RFI** — Request for information: nothing is suspicious yet, and one specific document or answer from the customer would settle the case. The reason must say what to ask for.
- **ESCALATE** — Escalate to the MLRO: the activity is unexplained or there are signs of crime. The MLRO decides whether to report it.

> **Once there is suspicion, escalate rather than ask the customer.** Asking questions at that point risks tipping them off, which is an offence.

> **One case gets one verdict, even when several rules fired.** Close a case only when the evidence explains every alert in it. At the payments and FX firm, an outgoing payment held by an alert is one case. Everywhere else, a case is one customer over the review window.

---

## The four institutions

Four separate companies. Each produces a different kind of data and runs its own rules, so the same rule looks different from one to the next. Each has its own SOP in `POLICY.md`; read all four.

### Bank — 8 rules
- **Customers:** Individuals and small businesses with current accounts
- **Data:** Cash at branches and ATMs, bank transfers and card spending, with each counterparty's name and country

### Payments and FX firm — 8 rules
- **Customers:** Businesses paying suppliers and staff abroad
- **Data:** Funding from the client's own bank, payments abroad in other currencies, invoices and payment files
- **Alerts:** An alert on an outgoing payment holds it until a decision is made

### E-money firm — 8 rules
- **Customers:** Individuals and small businesses with app-based accounts
- **Data:** Top-ups by bank transfer and cash at shops, transfers to other people, card spending and ATM withdrawals

### Investment firm — 7 rules
- **Customers:** Individual investors on a broker and fund platform
- **Data:** Deposits and withdrawals with sender and recipient names, fund purchases and sales

---

## Rules by institution

A cell shows what the rule watches at that institution; "–" means the rule does not run there. Exact limits are in each `POLICY.md`.

> **Note:** The PDF print of this page cut the table off after the Payments and FX firm column. The **E-money firm** and **Investment firm** columns are not in the source PDF.

| Rule | Bank | Payments and FX firm |
|---|---|---|
| **TM01 — Activity exceeds the expected profile**<br>Money received over 30 days is well above what the customer declared. | Inflows against declared income or turnover | Funding against the client's expected payment volume |
| **TM02 — Rapid movement of funds**<br>Most of the money that comes in leaves again quickly. | Paid out within 48 hours of arriving | Money from anyone other than the client, paid out within 48 hours<br>*Funding and paying out at once is the product, so the client's own funding does not count* |
| **TM03 — Repeated transactions below a threshold**<br>Several transactions sit just under a limit within 10 days. | Cash deposits just under £10,000 | Payments to one beneficiary just under £10,000, the point where an invoice is needed |
| **TM04 — Many-to-one or one-to-many payments**<br>Unusually many different people pay the account, or are paid by it. | More than six payers or payees in 30 days | More than 25 beneficiaries in 30 days |
| **TM05 — Large or unexpected cash**<br>One large cash transaction. | Cash deposit or withdrawal of £10,000 or more | – *No cash* |
| **TM06 — Unexpected higher-risk country**<br>Money to or from a higher-risk country the customer did not declare. | Transfers to or from the country | Beneficiary country |
| **TM07 — Dormant account reactivation**<br>Money moves after 180 days with no activity. | Dormant account | Dormant client account |
| **TM08 — Third-party funding or payout**<br>The account holder's name on the other side is not the customer's. | – *Third-party payments are normal on a current account* | Funding from an account not in the client's name |
| **TM10 — Unusually early fund redemption**<br>Fund units sold back within 90 days of being bought. | – *Does not run* | – *Does not run* |
| **TM22 — Counterparty on an internal list**<br>A payer, recipient or company officer matches the internal list by name, address, date of birth or identifier. | Payer or recipient | Beneficiary or its officers |
| **Rules that run** | 8 | 8 |

---

## Policy settings to apply

The SOPs set these out in full. They are the ones that most often decide a case.

### Own accounts
Money moving between the customer's own accounts explains activity only when a document on file shows the customer holds the other account. When such transfers bring activity to **five times the declared income or volume or more**, send an RFI for an updated expected income or volume. Below five times, close.

### The internal list
A match on one detail alone does not prove it is the same party. Close a list match only when the evidence shows the counterparty is someone else. If nothing settles it either way, send an RFI. If the listed party is the counterparty or controls it, escalate.

### Earlier reviews and invoices
An earlier review covers only what it describes, never new or larger activity. At the payments and FX firm, a payment of **£10,000 or more** to anyone other than the client needs an invoice or contract on file before it is released.

> **Context, not triggers:** round amounts, a shared device, IP address or contact details, a recipient added the same day, and crypto or gambling counterparties add context to a case. None of them raises an alert on its own.

---

## The data, and where your output goes

Each institution comes as one folder, and the unseen test cases come in folders with exactly the same files. Your system writes one file per institution into `output/`.

```
hackathon/
├── README.md                  Start here: the short version of this brief, with the reason codes
├── BRIEF.html                 This page, to read offline
├── institutions/              The development cases, one folder per institution, 1,000 cases each
│   ├── bank/                  One institution's cases
│   │   ├── POLICY.md          The institution's SOP: verdicts, what a case is, its rules and limits, and its policy settings
│   │   ├── cases.jsonl        One case per line: the customer, the review window, the alert IDs in the case and any held payment
│   │   ├── alerts.jsonl       Each alert: its ID, the rule, what it measured and the transactions it fired on
│   │   ├── customers.jsonl    Customer details and what they declared when they joined
│   │   ├── transactions.jsonl Transactions on each customer's accounts
│   │   ├── evidence.jsonl     The documents on file for each customer, with their IDs
│   │   ├── evidence/          One Markdown file per document
│   │   │   ├── KYC-6179.md    For example, a due diligence summary
│   │   │   └── INV-7291.md    For example, an invoice
│   │   ├── previous_reviews.jsonl  Earlier alert reviews for these customers
│   │   └── internal_list.jsonl     The institution's internal list
│   ├── emoney/                Same files
│   ├── investment/            Same files
│   └── payments_fx/           Same files
├── system/                    YOU WRITE — your system: the `run` executable and a README.md on how it decides
└── output/                    YOU WRITE — the classification your system makes for every development case
    ├── bank.jsonl             One line per case in institutions/bank/cases.jsonl
    ├── emoney.jsonl           One line per case in institutions/emoney/cases.jsonl
    ├── investment.jsonl       One line per case in institutions/investment/cases.jsonl
    └── payments_fx.jsonl      One line per case in institutions/payments_fx/cases.jsonl
```

---

## What to deliver

A runnable system, a short README on how it decides, and its output on the development cases. We call the system once per institution folder:

```
system/run institutions/bank output/bank.jsonl
```

On the unseen cases we pass full paths and run it from inside `system/`, so it must not depend on any other working directory.

Each output file has one JSON line per case. The line covers every alert in the case, because one verdict decides them all:

```json
{"case_id": "BK-CS112964", "alert_ids": ["BK-AL4810357"], "verdict": "CLOSE", "reason_ ...
```

> *The example line is truncated in the source PDF (it sat in a horizontally scrolling box).*

| Field | Type | What it holds |
|---|---|---|
| `case_id` | ID | The case, from `cases.jsonl` |
| `alert_ids` | List of IDs | Every alert in the case, from `alerts.jsonl` |
| `verdict` | Enum | `CLOSE`, `RFI` or `ESCALATE` |
| `reason_code` | Enum | The main ground for the verdict, from the list below. It must belong to the verdict given |
| `reason` | Free text | Two to four sentences naming the specific facts and documents relied on. For an RFI, exactly what to ask the customer for |
| `evidence` | List of IDs | The documents, transactions or list entries relied on, exactly as their IDs appear in the files |

### Reason codes

| Verdict | Reason code | Use it when |
|---|---|---|
| CLOSE | `COLLECTION_DOCUMENTED` | A documented collection made on behalf of named people explains the payments |
| CLOSE | `BUSINESS_RECORDS_RECONCILE` | Business records, such as takings or collection reports, reconcile to the activity |
| CLOSE | `DOCUMENTED_EVENT` | A documented one-off event, such as a sale, an inheritance or a payout, explains the funds |
| CLOSE | `INVOICE_MATCHES` | An invoice, contract or payment file matches the amount, payee and date |
| CLOSE | `OWN_ACCOUNT` | The money moved between accounts the customer is shown to hold |
| CLOSE | `GROUP_COMPANY` | The counterparty is a verified company in the same group |
| CLOSE | `THIRD_PARTY_DOCUMENTED` | A documented relationship explains a third-party payer |
| CLOSE | `REFUND_OF_EARLIER_PAYMENT` | The money is a refund of an earlier payment |
| CLOSE | `RETURNING_CUSTOMER` | A verified customer resumed using a dormant account |
| CLOSE | `LIST_MATCH_DIFFERENT_PARTY` | The evidence shows the counterparty is not the listed party |
| CLOSE | `WITHIN_PROFILE` | The activity fits the customer's declared profile or instructions |
| RFI | `RFI_PURPOSE_DOCUMENTS` | Ask for documents showing what the payments were for |
| RFI | `RFI_BUSINESS_RECORDS` | Ask for the business records behind the activity |
| RFI | `RFI_SOURCE_OF_FUNDS` | Ask for proof of where the money came from |
| RFI | `RFI_COUNTERPARTY_DETAILS` | Ask who the counterparty is and what the payment was for |
| RFI | `RFI_UPDATED_PROFILE` | Ask for an updated expected income or volume |
| ESCALATE | `ESC_MULE_ACTIVITY` | The account receives and passes on other people's money for someone else |
| ESCALATE | `ESC_STRUCTURING` | Transactions were split to stay under a threshold |
| ESCALATE | `ESC_UNEXPLAINED_FUNDS` | The money has no credible explanation and was moved on |
| ESCALATE | `ESC_PASS_THROUGH` | Money passes through with no credible purpose, often to linked parties |
| ESCALATE | `ESC_INVOICE_MISMATCH` | The invoices do not support the payments |
| ESCALATE | `ESC_HIGH_RISK_CORRIDOR` | Unexplained payments to or from a higher-risk country |
| ESCALATE | `ESC_ACCOUNT_MISUSE` | Someone other than the customer appears to control or use the account |
| ESCALATE | `ESC_THIRD_PARTY_FUNDING` | Unrelated third parties fund the account or receive money from it |
| ESCALATE | `ESC_EARLY_REDEMPTION` | Fund units are bought and quickly sold back to move money |
| ESCALATE | `ESC_LISTED_PARTY` | The counterparty is the listed party or is controlled by it |

> **Build for cases you have not seen.** Nothing from the development cases, such as case IDs, names or amounts, may be built into the system. It reads only the folder it is given, writes only its output file, and a run of 200 cases should finish within 30 minutes. A case missing from the output counts as an escalation.

---

## How systems are assessed

We run your system five times on the unseen cases and look at four things.

### 1. The most correct results with the fewest mistakes

Close as many closable cases as you can while making as few wrong decisions as possible. A wrongful close, closing a case that needed an RFI or an escalation, is the worst mistake. Escalating everything is penalised too. Each decision is costed per case, and lower is better:

| Right verdict ↓ / You sent → | CLOSE | RFI | ESCALATE |
|---|---|---|---|
| **CLOSE** | 0 | 3 | 3 |
| **RFI** | 10 | 0 | 2 |
| **ESCALATE** | 10 | 3 | 0 |

### 2. Explainable evidence

Every verdict needs a reason code and a reason that names the specific facts it rests on, and `evidence` lists the IDs of the documents, transactions or list entries relied on. Cited IDs must exist in the case's files, and the evidence that decides the case should be among them.

### 3. The same results every time

The five runs must give the same verdicts and reason codes, and ideally the same evidence. A system whose answer changes from run to run cannot be trusted with an alert queue.

### 4. Precision on verdict and reason

For every case with the right verdict, the reason is scored on three things: whether `evidence` includes the documents that decide the case, whether the reason gives the grounds the right answer relies on, and whether it names this case's own documents, people and amounts rather than wording that would fit any case. A correct verdict with a generic or wrong reason scores little.

### The score, out of 100

| Part | Weight | What counts |
|---|---|---|
| Unseen cases | 60% | Verdicts 60%, reasons 40% |
| Development cases | 20% | Verdicts 60%, reasons 40% |
| Reproducibility | 20% | Same verdict and reason code in all five runs |

Escalating everything scores 0 on verdicts. Reproducibility points grow with the unseen score, so repeating poor results earns little.

---

*Every customer, company, account and document in the dataset is invented.*

*Shared by Diligent AI*
