name: "Task: SEC Filings & Sector Synthesis"

on:
  workflow_dispatch:
  workflow_call:

jobs:
  filings-ai:
    runs-on: ubuntu-latest
    timeout-minutes: 25
    env:
      DATABASE_URL: ${{ secrets.DATABASE_URL }}
      GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
      GROQ_API_KEY: ${{ secrets.GROQ_API_KEY }}
    steps:
      - uses: ./.github/actions/setup-python
      - name: Parse filings & synthesize insights
        run: python pipeline/filings_ai.py
