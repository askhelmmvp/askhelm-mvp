# AskHelm compliance knowledge base

Files:
- `askhelm_compliance_chunks.jsonl`: pre-processed compliance chunks for LYC, MARPOL Annex VI and ISM.
- `askhelm_vector_index.pkl`: persisted TF-IDF search index built from the chunks.
- `askhelm_retriever.py`: simple retrieval helper Claude Code can import.

## Intended use
This is a narrow, grounded compliance KB for AskHelm. It is designed to answer operational questions about:
- REG Yacht Code / LYC
- MARPOL Annex VI ECAs, NOx Tier III and IAPP impacts
- ISM Code definitions, SMS requirements, reporting and maintenance

## Example
```python
from askhelm_retriever import AskHelmComplianceRetriever

retriever = AskHelmComplianceRetriever("/mnt/data/askhelm_vector_index.pkl")
hits = retriever.search("does Tier III apply in Norwegian Sea", top_k=4)
for hit in hits:
    print(hit["topic"], hit["source_reference"], hit["score"])
```

Pass only the top retrieved chunks into the LLM. Do not pass full PDFs directly.
