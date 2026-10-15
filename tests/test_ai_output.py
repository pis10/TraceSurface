import json
import unittest

from tracesurface.ai.output import AIOutputError, parse_text_result, validate_coverage
from tracesurface.ai.prompt import SYSTEM_PROMPT
from tracesurface.ai.service import safe_error


class OutputRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.result = {
            "results": [
                {
                    "replay_id": 1,
                    "suspected": False,
                    "reason": '业务拒绝：{"code":401}；字符 [ ] 与转义 "',
                    "recommendation": "",
                }
            ]
        }
        self.text = json.dumps(self.result, ensure_ascii=False)

    def test_presentation_wrappers_preserve_exact_data(self):
        for text in [
            self.text,
            "\ufeff" + self.text,
            "```json\n" + self.text + "\n```",
            "分析结果如下：\n" + self.text + "\n分析完毕。",
            "<think>reasoning {example} [1]</think>\n" + self.text,
        ]:
            with self.subTest(text=text):
                self.assertEqual(parse_text_result(text).model_dump(), self.result)

    def test_incomplete_ambiguous_or_wrong_shape_is_rejected(self):
        for text in [
            "",
            "no result",
            self.text[:-1],
            self.text + self.text,
            "<think>" + self.text,
            '[{ "results": [] }]',
            '{"broken": ' + self.text,
            "{bad}" + self.text,
            '{"results": [], "results": []}',
        ]:
            with self.subTest(text=text), self.assertRaises(AIOutputError):
                parse_text_result(text)

    def test_no_type_coercion_or_fabricated_required_fields(self):
        for changes in [
            {"suspected": "false"},
            {"suspected": 0},
            {"replay_id": "1"},
            {"reason": ""},
            {"recommendation": None},
            {"secret-field": "secret-value"},
        ]:
            result = {"results": [{**self.result["results"][0], **changes}]}
            with (
                self.subTest(changes=changes),
                self.assertRaises(AIOutputError) as caught,
            ):
                parse_text_result(json.dumps(result))
            self.assertNotIn("secret", safe_error(caught.exception))
        del self.result["results"][0]["suspected"]
        with self.assertRaises(AIOutputError):
            parse_text_result(json.dumps(self.result))

    def test_coverage_reports_missing_extra_and_duplicate_counts(self):
        result = parse_text_result(self.text)
        validate_coverage(result, {1})
        result.results *= 2
        with self.assertRaisesRegex(AIOutputError, "遗漏 1 个，多出 1 个，重复 1 个"):
            validate_coverage(result, {2})

    def test_prompt_examples_are_valid_and_cover_their_inputs(self):
        source = SYSTEM_PROMPT.split("示例输入：\n", 1)[1].splitlines()[0]
        payload = json.loads(source)
        for record in payload["records"]:
            json.loads(record["response"]["body"])
        output = SYSTEM_PROMPT.split("示例输出：\n", 1)[1].splitlines()[0]
        result = parse_text_result(output)
        validate_coverage(
            result, {record["replay_id"] for record in payload["records"]}
        )
        self.assertEqual([item.suspected for item in result.results], [False, True])
