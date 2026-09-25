from evals.run_eval import field_correct, line_items_score, run, to_markdown
from tests.conftest import FakeLLMClient


def test_eval_runs_and_reports_metrics(dataset):
    report = run(dataset, "rules")
    assert report["documents"] == 24
    assert report["by_template"]["classic"]["mean_field_accuracy"] == 1.0
    assert report["by_template"]["freeform"]["mean_field_accuracy"] < 0.6  # held-out layout
    assert report["error_detection"]["seeded"] > 0
    assert report["cost"]["llm_calls"] == 0
    assert "Mean field accuracy" in to_markdown(report)


def test_eval_counts_llm_calls_in_hybrid_mode(dataset):
    # A fake LLM that always returns an empty invoice: we only check the plumbing here.
    fake = FakeLLMClient({"line_items": []})
    report = run(dataset, "hybrid", llm_client=fake)
    freeform = report["by_template"]["freeform"]["documents"]
    assert report["cost"]["llm_calls"] >= freeform  # every held-out doc needed the LLM
    assert fake.calls < report["documents"]  # but known layouts didn't


def test_field_comparison_is_forgiving_about_format():
    assert field_correct("vendor_name", "Acme Ltd.", "ACME LTD")
    assert field_correct("total", "1234.50", "1234.5")
    assert not field_correct("invoice_number", "INV-1", "INV-2")


def test_line_item_matching():
    expected = [{"description": "A", "quantity": "2", "amount": "10.00"}]
    assert line_items_score(expected, [{"description": "a", "quantity": 2, "amount": 10}]) == (1, 1, 1)
    assert line_items_score(expected, [{"description": "A", "quantity": 3, "amount": 10}]) == (0, 1, 1)
