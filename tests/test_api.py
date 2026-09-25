from __future__ import annotations

import io
import json

from tests.conftest import pdf_for


def upload(client, data: bytes, name: str = "invoice.pdf"):
    return client.post(
        "/api/documents",
        data={"files": (io.BytesIO(data), name)},
        content_type="multipart/form-data",
    )


def clean_label(labels, template="classic"):
    return next(lb for lb in labels if lb["template"] == template and not lb["seeded_issues"] and not lb["expected"]["po_number"])


def test_health(client):
    assert client.get("/api/health").json == {"status": "ok"}


def test_upload_processes_and_auto_approves_clean_invoice(client, dataset, labels):
    label = clean_label(labels)
    res = upload(client, pdf_for(dataset, label), label["file"])
    assert res.status_code == 201
    doc = res.json["documents"][0]
    assert doc["status"] == "auto_approved"
    assert doc["invoice_number"] == label["expected"]["invoice_number"]

    detail = client.get(f"/api/documents/{doc['id']}").json
    assert detail["invoice"]["total"] == label["expected"]["total"]
    assert len(detail["invoice"]["line_items"]) == len(label["expected"]["line_items"])


def test_same_file_twice_is_not_duplicated(client, dataset, labels):
    pdf = pdf_for(dataset, labels[0])
    first = upload(client, pdf)
    second = upload(client, pdf, "copy.pdf")
    assert second.status_code == 200
    assert second.json["documents"][0]["duplicate_upload"] is True
    assert second.json["documents"][0]["id"] == first.json["documents"][0]["id"]
    assert client.get("/api/documents").json["total"] == 1


def test_non_pdf_is_rejected(client):
    res = upload(client, b"hello, not a pdf", "notes.txt")
    assert res.status_code == 400


def test_file_endpoint_returns_pdf(client, dataset, labels):
    doc_id = upload(client, pdf_for(dataset, labels[0])).json["documents"][0]["id"]
    res = client.get(f"/api/documents/{doc_id}/file")
    assert res.status_code == 200 and res.data[:4] == b"%PDF"


def test_total_mismatch_goes_to_review_and_blocks_approval(client, dataset, labels):
    label = next(lb for lb in labels if "TOTAL_MISMATCH" in lb["seeded_issues"] and lb["template"] != "freeform")
    doc = upload(client, pdf_for(dataset, label)).json["documents"][0]
    assert doc["status"] == "needs_review"

    blocked = client.post(f"/api/documents/{doc['id']}/approve", json={})
    assert blocked.status_code == 409
    no_note = client.post(f"/api/documents/{doc['id']}/approve", json={"override": True})
    assert no_note.status_code == 400
    ok = client.post(f"/api/documents/{doc['id']}/approve", json={"override": True, "note": "Vendor confirmed by phone"})
    assert ok.status_code == 200 and ok.json["status"] == "approved"
    assert ok.json["history"][-1]["note"] == "Vendor confirmed by phone"


def test_correction_is_audited_and_revalidated(client, dataset, labels):
    label = clean_label(labels)
    doc = upload(client, pdf_for(dataset, label)).json["documents"][0]
    res = client.put(
        f"/api/documents/{doc['id']}/invoice",
        json={"total": "1.00"},
        headers={"X-Reviewer": "jas"},
    )
    assert res.status_code == 200
    body = res.json
    assert body["status"] == "needs_review"
    assert any(i["code"] == "TOTAL_MISMATCH" for i in body["issues"])
    event = body["history"][-1]
    assert event["action"] == "correct" and event["field"] == "total" and event["reviewer"] == "jas"
    assert json.loads(event["new_value"]) == "1.00"
    assert body["invoice"]["confidence"]["total"] == 1.0


def test_invalid_correction_is_rejected(client, dataset, labels):
    doc = upload(client, pdf_for(dataset, clean_label(labels))).json["documents"][0]
    res = client.put(f"/api/documents/{doc['id']}/invoice", json={"invoice_date": "not a date"})
    assert res.status_code == 422


def test_fixing_an_original_reopens_its_auto_approved_duplicate(client, dataset, labels):
    """A misread original lets its re-sent copy slip through; correcting the original catches it."""
    label = clean_label(labels)
    exp = label["expected"]
    # 1. The copy was read correctly and auto-approved.
    copy = upload(client, pdf_for(dataset, label)).json["documents"][0]
    assert copy["status"] == "auto_approved"
    # 2. The original was misread (simulate: a different file whose vendor/number came out wrong).
    other = next(lb for lb in labels if lb["file"] != label["file"] and lb["template"] == "freeform")
    original = upload(client, pdf_for(dataset, other)).json["documents"][0]
    # 3. A reviewer corrects the original to its true vendor and number.
    res = client.put(
        f"/api/documents/{original['id']}/invoice",
        json={"vendor_name": exp["vendor_name"], "invoice_number": exp["invoice_number"]},
    )
    assert copy["id"] in res.json["reopened_documents"]
    reopened = client.get(f"/api/documents/{copy['id']}").json
    assert reopened["status"] == "needs_review"
    assert any(i["code"] == "DUPLICATE_INVOICE" for i in reopened["issues"])


def test_reject_requires_note(client, dataset, labels):
    doc = upload(client, pdf_for(dataset, labels[0])).json["documents"][0]
    assert client.post(f"/api/documents/{doc['id']}/reject", json={}).status_code == 400
    res = client.post(f"/api/documents/{doc['id']}/reject", json={"note": "Not our vendor"})
    assert res.json["status"] == "rejected"


def test_purchase_orders_and_po_matching(client, dataset, labels):
    label = next(lb for lb in labels if lb["expected"]["po_number"] and not lb["seeded_issues"] and lb["template"] == "classic")
    exp = label["expected"]
    res = client.post("/api/purchase-orders", json=[
        {"po_number": exp["po_number"], "vendor_name": exp["vendor_name"], "amount": "1.00"}
    ])
    assert res.status_code == 201
    doc = upload(client, pdf_for(dataset, label)).json["documents"][0]
    detail = client.get(f"/api/documents/{doc['id']}").json
    assert any(i["code"] == "PO_AMOUNT_EXCEEDED" for i in detail["issues"])


def test_list_filter_stats_and_export(client, dataset, labels):
    for label in labels[:6]:
        upload(client, pdf_for(dataset, label), label["file"])
    listed = client.get("/api/documents?status=auto_approved,needs_review").json
    assert listed["total"] == 6
    stats = client.get("/api/stats").json
    assert stats["total_documents"] == 6
    assert 0 <= stats["touchless_rate"] <= 1
    csv_text = client.get("/api/export.csv").data.decode()
    assert csv_text.startswith("document_id,vendor")


def test_page_image_renders(client, dataset, labels):
    doc_id = upload(client, pdf_for(dataset, labels[0])).json["documents"][0]["id"]
    res = client.get(f"/api/documents/{doc_id}/pages/1.png")
    assert res.status_code == 200 and res.data[:8] == b"\x89PNG\r\n\x1a\n"
    assert client.get(f"/api/documents/{doc_id}/pages/9.png").status_code == 404


def test_corrupt_pdf_is_rejected(client):
    res = upload(client, b"%PDF-1.4 this is not really a pdf", "broken.pdf")
    assert res.status_code == 400


def test_po_is_checked_against_running_total(client, dataset, labels):
    """Two different invoices billed to one PO: the second pushes it over the limit."""
    first, second = [lb for lb in labels if lb["template"] == "classic" and not lb["seeded_issues"]][:2]
    po = "PO-SHARED-1"
    amount = float(first["expected"]["total"]) + 1  # room for the first invoice only
    client.post("/api/purchase-orders", json={"po_number": po, "vendor_name": first["expected"]["vendor_name"], "amount": amount})
    d1 = upload(client, pdf_for(dataset, first)).json["documents"][0]
    client.put(f"/api/documents/{d1['id']}/invoice", json={"po_number": po})
    d2 = upload(client, pdf_for(dataset, second)).json["documents"][0]
    res = client.put(
        f"/api/documents/{d2['id']}/invoice",
        json={"po_number": po, "vendor_name": first["expected"]["vendor_name"]},
    )
    exceeded = [i for i in res.json["issues"] if i["code"] == "PO_AMOUNT_EXCEEDED"]
    assert exceeded and "already billed" in exceeded[0]["message"]


def test_basic_auth_protects_everything_but_health(settings):
    from dataclasses import replace

    from app import create_app
    from app.extraction.rules import RulesExtractor

    app = create_app(replace(settings, basic_auth_user="admin", basic_auth_password="s3cret"), extractor=RulesExtractor())
    c = app.test_client()
    assert c.get("/api/health").status_code == 200
    assert c.get("/api/documents").status_code == 401
    assert c.get("/api/documents", auth=("admin", "wrong")).status_code == 401
    assert c.get("/api/documents", auth=("admin", "s3cret")).status_code == 200
