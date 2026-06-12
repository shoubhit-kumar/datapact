import requests
import base64
import json
import os
import time

BASE_URL     = "https://nullhyp-datapact-func.azurewebsites.net/api"
FUNCTION_KEY = "<FUNCTION_KEY_HERE>"  # Replace with your actual function key

# All documents to upload
# Format: (filename, doc_type, table_name)
# table_name = None means it applies to all tables
DOCUMENTS = [
    ("01_sites_schema.sql",              "ddl",            "inspection_sites"),
    ("02_inspectors_schema.sql",         "ddl",            "inspectors"),
    ("03a_records_schema.sql",           "ddl",            "inspection_records"),
    ("03b_records_addl_cols.sql",        "ddl",            "inspection_records"),
    ("04_data_contract_v2.yaml",         "data_contract",  "inspection_sites"),
    ("05_issue_log_2024.csv",            "issue_log",      "inspection_sites"),
    ("06_onboarding_wiki.txt",           "business_doc",   "inspection_sites"),
    ("07_slack_export.txt",              "business_doc",   "inspection_sites"),
    ("08_email_governance_decisions.txt","business_doc",   "inspection_sites"),
    ("09_sql_profiler_queries.sql",      "profiler_query", "inspection_sites"),
]

DOCS_FOLDER   = "./datapact_inputs"
DATABASE_NAME = "inspectiondb"
UPLOADED_BY   = "Shoubhit Kumar"

def upload_document(filepath, filename, doc_type, table_name):
    with open(filepath, "rb") as f:
        content = base64.b64encode(f.read()).decode("utf-8")

    response = requests.post(
        f"{BASE_URL}/uploaddocument?code={FUNCTION_KEY}",
        json={
            "filename":      filename,
            "content":       content,
            "doc_type":      doc_type,
            "table_name":    table_name or "",
            "database_name": DATABASE_NAME,
            "uploaded_by":   UPLOADED_BY
        },
        timeout=60
    )
    return response.status_code, response.json()

def process_document(doc_id):
    response = requests.post(
        f"{BASE_URL}/processdocument?code={FUNCTION_KEY}",
        json={"doc_id": doc_id},
        timeout=120
    )
    return response.status_code, response.json()

def main():
    print("=" * 60)
    print("DataPact — Bulk Document Upload & Index")
    print("=" * 60)

    results = []

    for filename, doc_type, table_name in DOCUMENTS:
        filepath = os.path.join(DOCS_FOLDER, filename)

        if not os.path.exists(filepath):
            print(f"\n⚠️  SKIP — file not found: {filepath}")
            continue

        print(f"\n📄 Uploading: {filename}")
        print(f"   Type: {doc_type} | Table: {table_name or 'all'}")

        # Upload
        status, resp = upload_document(filepath, filename, doc_type, table_name)
        if status not in (200, 201):
            print(f"   ❌ Upload failed: {resp}")
            continue

        doc_id = resp.get("doc_id")
        print(f"   ✅ Uploaded → doc_id: {doc_id}")

        # Small delay before processing
        time.sleep(2)

        # Process and index
        print(f"   🔄 Processing & indexing...")
        p_status, p_resp = process_document(doc_id)
        if p_status == 200:
            chunks = p_resp.get("chunks_indexed", 0)
            print(f"   ✅ Indexed → {chunks} chunks")
            results.append({
                "filename": filename,
                "doc_id": doc_id,
                "chunks": chunks,
                "status": "SUCCESS"
            })
        else:
            print(f"   ❌ Processing failed: {p_resp}")
            results.append({
                "filename": filename,
                "doc_id": doc_id,
                "chunks": 0,
                "status": "FAILED"
            })

        # Delay to avoid rate limiting on embeddings
        time.sleep(3)

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    total_chunks = 0
    for r in results:
        status_icon = "✅" if r["status"] == "SUCCESS" else "❌"
        print(f"{status_icon} {r['filename']} → doc_id={r['doc_id']} | {r['chunks']} chunks")
        total_chunks += r["chunks"]

    print(f"\nTotal documents: {len(results)}")
    print(f"Total chunks indexed: {total_chunks}")
    print("\nNow run analysis to see RAG-enriched rules:")
    print(f"POST {BASE_URL}/queueanalysis")
    print('Body: {"table_name": "inspection_sites", "database_name": "inspectiondb", "triggered_by": "Shoubhit Kumar"}')

if __name__ == "__main__":
    main()