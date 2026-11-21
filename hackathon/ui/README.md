# Alert review workbench

A local, read-only screen for reviewing the system's decisions the way an analyst works a case.

## Start it

From the `hackathon` folder:

```
python3 ui/serve.py
```

The page opens at http://127.0.0.1:8000. Standard Python only, nothing to install.

To review a different set of cases, such as a new test folder, pass the folders:

```
python3 ui/serve.py <institutions folder> <output folder> --port 8000
```

The institutions folder holds one folder per institution (`bank`, `emoney`, ...). The output folder holds the system's `<institution>.jsonl` files.

## What it shows

- The case queue for each institution, filterable by verdict and alert rule, searchable by case ID, customer or reason code.
- Totals of closed, requests for information and escalated cases.
- For each case:
  - the decision, reason code and reason;
  - the evidence IDs it relies on (select one to jump to that transaction, document, list entry or review);
  - the alerts and the customer's declared profile;
  - transactions, with alerted, held and relied-on items marked;
  - every document on file, with the ones relied on opened;
  - earlier reviews and internal list entries.

Use the up and down arrow keys in the queue to move between cases. Each case has its own link, for example `#bank/BK-CS365131`.

The workbench never changes the data or the system's output, and it lives outside `system/`, so it has no effect on the graded run.
