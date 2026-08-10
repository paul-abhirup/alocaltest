"""Tests for the qa_bank_download credit feature."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pricing
from credit_engine import FEATURE_ALIASES


class TestQaBankDownloadCost:
    def test_cost_is_four(self):
        # The product requirement: 4 credits to download Q&A without doing the interview.
        assert pricing.credit_cost("qa_bank_download") == 4

    def test_alias_maps_to_key(self):
        assert FEATURE_ALIASES["Q&A Bank Download"] == "qa_bank_download"

    def test_cheaper_than_full_interview(self):
        # Sanity: a download should never cost more than the cheapest interview session.
        from pricing import INTERVIEW_QA_SESSION_CREDITS
        cheapest_session = min(INTERVIEW_QA_SESSION_CREDITS.values())
        assert pricing.credit_cost("qa_bank_download") < cheapest_session


class TestConstantExposed:
    def test_interview_module_constant(self):
        # The interview module exposes the cost as a top-level constant
        # (avoid the full module-import side effects by importing it explicitly).
        from interview_module import QA_BANK_DOWNLOAD_CREDITS
        assert QA_BANK_DOWNLOAD_CREDITS == 4

    def test_api_module_constant(self):
        # The api server uses the same source-of-truth number.
        from api_server import QA_BANK_DOWNLOAD_CREDITS
        assert QA_BANK_DOWNLOAD_CREDITS == 4


class TestDownloadHelperLogic:
    """Test the deterministic bits of the download gate without spinning up Streamlit."""

    def test_export_qa_produces_buffers(self):
        # The helper renders PDF + DOCX. Make sure the underlying export still works.
        from cv_generator import export_interview_qa

        sample = (
            "# Technical Questions\n\n"
            "1. Tell me about your most challenging project.\n"
            "   Ideal answer: Talk about X, Y, Z.\n\n"
            "2. How do you debug a memory leak?\n"
        )
        pdf_buf, docx_buf = export_interview_qa(sample)
        # PDFs start with %PDF
        assert pdf_buf.getvalue()[:4] == b"%PDF"
        # DOCX is a zip; PK\x03\x04
        assert docx_buf.getvalue()[:4] == b"PK\x03\x04"

    def test_qa_bank_to_text_round_trip(self):
        from interview_module import _qa_bank_to_text
        bank = {
            "technical": [
                {"question": "Q1", "intent": "I1", "ideal_answer": "A1"},
                {"question": "Q2", "intent": "I2", "ideal_answer": "A2"},
            ],
            "behavioral": [
                {"question": "BQ1", "intent": "BI1", "ideal_answer": "BA1"},
            ],
        }
        text = _qa_bank_to_text(bank)
        # Sections are wrapped in `=== ... ===` to match the existing test contract.
        assert "=== Technical Questions (JD-based) ===" in text
        assert "=== Behavioral / Situational Questions ===" in text
        assert "Q1" in text and "A1" in text
        assert "BQ1" in text


class TestCreditEngineAliasResolution:
    """When Q&A download goes through spend_credits, the feature name should
    resolve to the qa_bank_download key so the cost lookup is correct."""

    def test_alias_resolves_to_correct_cost(self):
        from credit_engine import FEATURE_ALIASES, _feature_cost

        feature = "Q&A Bank Download"
        alias = FEATURE_ALIASES[feature]
        assert alias == "qa_bank_download"
        assert _feature_cost(feature) == 4
