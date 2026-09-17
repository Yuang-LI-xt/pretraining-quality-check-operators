import unittest

from quality_checks_V6 import clean_record
from quality_checks_V6.clean_variation_chars import DEFAULT_RULES, clean_text


class RepeatedNoiseTokenTests(unittest.TestCase):
    def test_deletes_target_at_five_repeats(self):
        token = "a111111111"
        source = "Lead.\n\n" + " ".join([token] * 5) + " 1. Introduction"

        cleaned, changes = clean_text(source)

        self.assertEqual(cleaned, "Lead.\n\n1. Introduction")
        self.assertEqual({item["rule"] for item in changes}, {"repeated_noise_token"})

    def test_preserves_target_below_threshold(self):
        token = "a111111111"
        source = "Before " + " ".join([token] * 4) + " After"

        cleaned, changes = clean_text(source)

        self.assertEqual(cleaned, source)
        self.assertEqual(changes, [])

    def test_deletes_newline_and_punctuation_separated_variants(self):
        token = "B7777777"
        cases = (
            "Before\n" + "\n".join([token] * 6) + "\nAfter",
            "Before " + ", ".join([token] * 5) + " After",
        )

        for source in cases:
            with self.subTest(source=source):
                cleaned, changes = clean_text(source)
                self.assertNotIn(token, cleaned)
                self.assertIn("Before", cleaned)
                self.assertIn("After", cleaned)
                self.assertIn("repeated_noise_token", {item["rule"] for item in changes})

    def test_preserves_legitimate_repetition_and_identifiers(self):
        cases = (
            "Before " + " ".join(["very"] * 5) + " After",
            "Before " + " ".join(["2024"] * 5) + " After",
            "Before " + " ".join(["00000000"] * 5) + " After",
            "Before " + " ".join(["abc12345"] * 5) + " After",
            "value=0x" + "ab" * 50,
        )

        for source in cases:
            with self.subTest(source=source):
                cleaned, changes = clean_text(source, rules=("repeated_noise_token",))
                self.assertEqual(cleaned, source)
                self.assertEqual(changes, [])

    def test_preserves_markdown_fenced_code(self):
        token = "a111111111"
        source = "```text\n" + " ".join([token] * 5) + "\n```"

        cleaned, changes = clean_text(source, rules=("repeated_noise_token",))

        self.assertEqual(cleaned, source)
        self.assertEqual(changes, [])

    def test_records_compact_span_metadata(self):
        token = "a111111111"
        source = "Lead.\n\n" + " ".join([token] * 5) + " 1. Introduction"

        result = clean_record({"content": source}, fields=["content"], rules=["all"])
        cleaning = result["record"]["meta"]["cleaning_v6"]
        span = cleaning["by_rule"]["repeated_noise_token"]["spans"][0]

        self.assertIn("repeated_noise_token", DEFAULT_RULES)
        self.assertEqual(result["record"]["content"], "Lead.\n\n1. Introduction")
        self.assertEqual(cleaning["rule_counts"], {"repeated_noise_token": 55})
        self.assertEqual((span["start"], span["end"]), (7, 62))
        self.assertEqual(span["reasons"], ["repeat_count=5"])


if __name__ == "__main__":
    unittest.main()
