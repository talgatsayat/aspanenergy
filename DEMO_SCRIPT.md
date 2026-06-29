# Demo video script (3–5 minutes)

A tight, confident walkthrough that hits every evaluation criterion: problem
framing, the working system, AI usage, the quality of the output, and the
business value. Aim for ~4 minutes.

---

## 0:00–0:30 — The problem (frame the value)

> "Preparing a client solar proposal at Aspan today means reading a stack of
> CIE bills by hand, doing the sizing and savings maths in a spreadsheet, and
> formatting slides — hours of work per client. I built the **Aspan Proposal
> Engine** to do that in under five minutes, and produce a deck you can send
> straight to a CFO."

Show: the folder of scanned NESKAO bills + the final proposal side by side.

---

## 0:30–1:30 — Extract the bills (AI in action)

- Open the app: `streamlit run app.py`.
- Drag in several NESKAO bills → click **Extract**.
- Talk over it:
  > "These bills are scanned images in French — no text to copy. A vision model
  > reads each one into structured data: monthly kWh, the day tariff, costs. Each
  > row gets a **confidence score**, and I can edit anything before it goes near a
  > client — important for a financial document."
- Point at the day/night split and the confirmed **83.87 FCFA/kWh** day tariff.

> The engine never fabricates numbers: without a key the rows come up empty for
> manual entry. Here the Anthropic key is set, so it reads the real scans.

---

## 1:30–2:30 — Client, roof & geolocation

- Point out the client form is **auto-filled from the bills** (subscriber name,
  address, connection power) — editable if anything is wrong. Confirm grid
  **1250 kVA**, diesel **on**.
- Enter roof area **5149 m²** (or paste GPS `5.2649, -4.0034`).
  > "From the roof area the engine works out how many panels fit and the
  > installable capacity — about **1000 modules, 600 kWc** — and with a Maps key
  > it shows the site by satellite. So the system is sized to the actual roof, not
  > a guess."
- Highlight the sizing line:
  > "Capacity is the **minimum** of what the roof, the grid connection and the
  > daytime consumption allow — here the roof is the binding constraint. That's
  > what keeps the proposal technically honest."

---

## 2:30–3:30 — Generate & show the deck

- Click **Generate proposal** → open the `.pptx`.
- Flip through the 11 slides:
  > "Executive summary, current situation from the bills, the proposed system,
  > the PPA model, the two savings scenarios — grid replacement and grid-plus-
  > diesel — a 15-year savings chart, a CFO financial analysis with NPV and a
  > cashflow table, scope of services, and next steps."
- Then show the **cover email**: click Generate, and point out it's ready to send
  in French or English ("written by Claude when a key is set").
- Land the headline numbers:
  > "**14.6 million FCFA a year, 219 million over 15 years**, at a guaranteed 20%
  > discount — and these match Aspan's real NESKAO offer, because the engine is
  > calibrated to it."
- Note: "It's a normal editable PowerPoint, and it generates in French or English."

---

## 3:30–4:15 — Why it's production-ready

- Show `config/assumptions.yaml`:
  > "Every assumption — yield, tariffs, diesel cost, the discount — lives in one
  > config file. An operator tunes it without touching code."
- Run the tests:
  > "And a calibration test suite locks the numbers to the real offer, so the
  > tool stays trustworthy as it evolves."

> "That's the Aspan Proposal Engine: bills in, a client-ready proposal out, in
> minutes — the kind of system the first AI Operator at Aspan would build."

---

## Shot list / checklist

- [ ] Bills folder + final deck on screen (intro)
- [ ] Upload + extract, point to confidence + tariff
- [ ] Client form + roof/GPS + sizing line
- [ ] Generated deck, scroll all slides, headline numbers
- [ ] `assumptions.yaml` + `python tests/test_engine.py` passing
- [ ] Keep total under 5:00
