# ClearWay

A traceable, context-aware search and decision-support tool for FAA Airworthiness Directive (AD) proposals.

**ACACIA Hackathon Society | National University of Singapore**  
**Autonomous SG 2026 | AI & Aerospace Track**

## 🎯 Problem Statement

### Challenge #4

> How might AI help people find, understand, and verify regulated technical information?

Mechanics and engineers work with sensitive regulatory documents such as airworthiness directives, maintenance manuals and accident reports. They need precise instructions quickly, with confidence that the information applies to the correct equipment and configuration.

Generative AI models can hallucinate information or combine instructions across different equipment types, potentially leading to dangerous mistakes.

The challenge is to build a traceable, context-aware search engine that processes technical documents, interprets applicability rules and answers natural-language queries with verifiable citations. When information is ambiguous or insufficient, the system must explicitly refuse to provide an answer.

### Challenge Requirements

| Requirement | Description |
|---|---|
| Specific references | Every claim links to its source metadata and supporting text. |
| Zero hallucination | No unsupported information or fabricated responses. |
| Configuration filtering | Evaluate conditional applicability, including aircraft models and serial-number ranges. |
| Revision logic | Identify document revisions and determine the latest version. |
| Clear uncertainty | Refuse ambiguous, contradictory or out-of-scope queries. |
| Fast retrieval | Retrieve information across 1,000+ pages in under 3 seconds. |

## 💡 Solution

ClearWay addresses these challenges through a retrieval and verification pipeline designed around source-grounded information rather than unrestricted LLM generation.

### Features

- **Natural-language search:** Retrieve relevant FAA proposals using BM25.
- **Applicability checking:** Evaluate aircraft models, serial-number ranges, exclusions and configuration requirements.
- **Revision tracking:** Identify superseded proposals and determine the latest version.
- **Source verification:** Return verbatim supporting excerpts with document references and page information.
- **Uncertainty handling:** Refuse to provide definitive answers when evidence is insufficient, ambiguous or contradictory.

## 📊 Dataset

The project uses the provided Challenge #4 dataset:

- 431 FAA Airworthiness Directive proposals (NPRMs).
- `challenge4_ad_index.xlsx`, containing stable record IDs, publication dates, supersession information and revision chains.
- Full-text documents retrieved from public Federal Register links.

**Dataset considerations:**
- These documents are proposed rules (NPRMs), not necessarily final Airworthiness Directives.
- Only 21 records have a final AD number.
- 99 dockets appear more than once, requiring revision resolution.
- 62 summaries identify the AD they supersede.
- Applicability information is often incomplete in the spreadsheet, requiring examination of the full-text documents.

## 📦 Project Structure

```text
clearway/
├── api/                  # Vercel API endpoints
├── data/                 # Dataset and search index
├── preprocessing/        # Data ingestion and evaluation
│   └── eval/              # Evaluation scripts and results
├── static/               # Frontend
├── app.py                # Application server and API handler
├── engine.py             # Search and applicability logic
├── vercel.json           # Vercel deployment configuration
└── README.md
```

## 🚀 Usage

### Hosted Application

The application is deployed on Vercel:

**[https://clearway-acacia.vercel.app/](https://clearway-acacia.vercel.app/)**

The repository includes a prebuilt `data/index.pkl`, so preprocessing is not required to run the hosted application.

### 1. Rebuilding the Backend Index

Only required when modifying the dataset or preprocessing logic.

**Install dependencies**

```bash
pip install -r preprocessing/requirements.txt
```

**Download full-text documents**

```bash
python preprocessing/fetch_fulltext.py
```

**Build the search index**

```bash
python -m preprocessing.ingest
```

The generated index will be saved to `data/index.pkl`.

**Run evaluation**

```bash
python -m preprocessing.eval.run_eval
```

### 2. Running Locally

To run the application locally using the existing search index:

**Start the backend**

```bash
python app.py
```

**Access the frontend**

Open [http://localhost:8000](http://localhost:8000) in your browser.

The backend serves the frontend and handles API requests locally. No preprocessing is required when using the committed index.

## 🛠️ Tech Stack

| Component | Technology |
|---|---|
| Backend | Python |
| Retrieval | BM25 |
| Data Processing | Pandas, OpenPyXL |
| Frontend | HTML, CSS, JavaScript |
| Deployment | Vercel |

## 📄 Evaluation

Evaluation questions, scripts and results are available in `preprocessing/eval/`.

## ⚠️ Disclaimer

ClearWay is a research and decision-support tool. Its dataset primarily contains proposed Airworthiness Directives (NPRMs), which are not equivalent to final regulations.

Always verify applicability and regulatory requirements against the original FAA documents and applicable final ADs. ClearWay does not replace official regulatory records or professional aviation compliance assessments.