from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from typing import Any, Mapping

from src.utils.report_generation import (
    OpenAICompatibleReportApiClient,
    REPORT_INTERFACE_VERSION,
    TABLER_REPORT_TEMPLATE_PATH,
    ReportApiClient,
    ReportGenerationRequest,
    ReportGenerationService,
    _API_MAX_OUTPUT_TOKENS,
    _API_RECORD_CHAR_BUDGET,
    _MAX_API_EVENTS,
    _analysis_error_note,
    _inject_report_text,
    _is_loopback_host,
    _strip_reasoning,
    _url_opener,
    compact_rally_record_for_api,
    copy_tabler_report_assets,
    generate_report_from_rally_record,
    render_tabler_report_html,
)


def _rally_record() -> dict[str, Any]:
    return {
        "rally_id": "clip-001",
        "rally_name": "clip.mp4",
        "summary": {
            "duration_s": 30.0,
            "rally_hit_count": 10,
            "avg_hit_interval_ms": 1800.0,
            "hit_confidence_avg": 0.8,
            "out_of_frame_count": 0,
            "stroke_distribution": {"高远球": 4, "杀球": 2, "搓放": 1},
            "data_reliability": {
                "ball_visible_rate": 0.9,
                "pose_valid_rate": 0.86,
                "court_valid_rate": 0.82,
                "avg_ball_confidence": 0.78,
            },
            "players": {
                "bottom": {
                    "label": "测试用户",
                    "distance_m": 40.0,
                    "avg_speed_mps": 1.1,
                    "max_speed_mps": 3.8,
                    "stop_count": 8,
                    "start_count": 9,
                    "hit_count": 10,
                    "zone_hits": {"front": 1, "mid": 3, "back": 6},
                    "passive_hit_count": 4,
                    "high_intensity_count": 5,
                    "max_continuous_m": 5.0,
                },
                "top": {
                    "label": "对手",
                    "distance_m": 36.0,
                    "avg_speed_mps": 1.0,
                    "max_speed_mps": 3.5,
                    "stop_count": 7,
                    "start_count": 8,
                },
            },
        },
        "details": {
            "hits": [
                {
                    "timestamp_ms": 1200,
                    "player": "bottom",
                    "zone": "back",
                    "stroke": "高远球",
                    "confidence": 0.8,
                }
            ]
        },
    }


class ReportGenerationTest(unittest.TestCase):
    def test_request_payload_uses_structured_interface(self) -> None:
        generation_request = ReportGenerationRequest.from_rally_record(
            _rally_record(),
            athlete_name="测试用户",
            generated_at="2026-05-19 12:00",
            options={"style": "coach"},
        )

        payload = generation_request.to_payload()

        self.assertEqual(payload["interface_version"], REPORT_INTERFACE_VERSION)
        self.assertEqual(payload["task"], "badminton_report_generation")
        self.assertEqual(payload["data"]["athlete_name"], "测试用户")
        self.assertEqual(payload["data"]["rally_record"]["rally_id"], "clip-001")
        self.assertEqual(payload["options"]["style"], "coach")

    def test_local_service_generates_html_report(self) -> None:
        generation_request = ReportGenerationRequest.from_rally_record(
            _rally_record(),
            athlete_name="测试用户",
            generated_at="2026-05-19 12:00",
        )
        response = ReportGenerationService().generate(generation_request)

        self.assertEqual(response.provider, "local")
        self.assertIn("羽毛球水平分析报告", response.html)
        self.assertEqual(response.report_data["meta"]["athlete_name"], "测试用户")

    def test_api_client_sends_json_payload_and_service_uses_response(self) -> None:
        captured: dict[str, Any] = {}

        def fake_transport(
            url: str,
            payload: Mapping[str, Any],
            headers: Mapping[str, str],
            timeout_s: float,
        ) -> Mapping[str, Any]:
            captured["url"] = url
            captured["payload"] = dict(payload)
            captured["headers"] = dict(headers)
            captured["timeout_s"] = timeout_s
            return {
                "status": "ok",
                "provider": "fake-api",
                "report_text": "API 自动生成的训练复盘",
            }

        api_client = ReportApiClient(
            "https://example.test/report",
            api_key="secret-token",
            transport=fake_transport,
        )
        generation_request = ReportGenerationRequest.from_rally_record(_rally_record())
        response = ReportGenerationService(api_client).generate(generation_request, use_api=True)

        self.assertEqual(captured["url"], "https://example.test/report")
        self.assertEqual(captured["payload"]["interface_version"], REPORT_INTERFACE_VERSION)
        self.assertEqual(captured["headers"]["Authorization"], "Bearer secret-token")
        self.assertEqual(response.provider, "fake-api")
        self.assertEqual(response.report_text, "API 自动生成的训练复盘")
        self.assertIn("羽毛球水平分析报告", response.html)

    def test_openai_compatible_client_parses_chat_completion_text(self) -> None:
        captured: dict[str, Any] = {}

        def fake_transport(
            url: str,
            payload: Mapping[str, Any],
            headers: Mapping[str, str],
            timeout_s: float,
        ) -> Mapping[str, Any]:
            captured["url"] = url
            captured["payload"] = dict(payload)
            captured["headers"] = dict(headers)
            return {
                "choices": [
                    {
                        "message": {
                            "content": "本次回合节奏偏短，建议等待完整视频分析后再导出报告。"
                        }
                    }
                ]
            }

        api_client = OpenAICompatibleReportApiClient(
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
            model="qwen-plus",
            api_key="secret-token",
            transport=fake_transport,
        )
        generation_request = ReportGenerationRequest.from_rally_record(_rally_record())
        response = ReportGenerationService(api_client).generate(generation_request, use_api=True)

        self.assertEqual(captured["payload"]["model"], "qwen-plus")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer secret-token")
        self.assertEqual(response.provider, "openai-compatible")
        self.assertIn("节奏偏短", response.report_text)
        self.assertIn("节奏偏短", response.html)

    def test_api_report_data_cannot_clear_local_metrics(self) -> None:
        def fake_transport(
            url: str,
            payload: Mapping[str, Any],
            headers: Mapping[str, str],
            timeout_s: float,
        ) -> Mapping[str, Any]:
            return {
                "choices": [
                    {
                        "message": {
                            "content": '{"report_text":"API 复盘文本","report_data":{}}'
                        }
                    }
                ]
            }

        api_client = OpenAICompatibleReportApiClient(
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
            model="qwen-plus",
            api_key="secret-token",
            transport=fake_transport,
        )
        generation_request = ReportGenerationRequest.from_rally_record(_rally_record())
        response = ReportGenerationService(api_client).generate(generation_request, use_api=True)

        self.assertEqual(response.report_data["summary"]["metrics"][0]["value"], "30.0秒")
        self.assertEqual(response.report_data["summary"]["metrics"][1]["value"], "10 次")
        self.assertIn("30.0秒", response.html)
        self.assertIn("10 次", response.html)

    def test_generate_report_can_export_html(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "report.html"
            response = generate_report_from_rally_record(
                _rally_record(),
                athlete_name="测试用户",
                output_html_path=output_path,
            )

            self.assertTrue(output_path.is_file())
            self.assertIn("羽毛球水平分析报告", output_path.read_text(encoding="utf-8"))
            self.assertEqual(response.status, "ok")

    def test_stable_report_template_is_used_for_tabler_rendering(self) -> None:
        self.assertTrue(TABLER_REPORT_TEMPLATE_PATH.is_file())

        response = ReportGenerationService().generate(
            ReportGenerationRequest.from_rally_record(_rally_record())
        )
        html = render_tabler_report_html(response.report_data)

        self.assertIn("./static/css/tabler.css", html)
        self.assertIn("羽毛球水平分析报告", html)

    def test_report_assets_replace_empty_file_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            target_dir = Path(tmp_dir)
            destination = target_dir / "static"
            destination.write_text("", encoding="utf-8")

            copy_tabler_report_assets(target_dir)

            self.assertTrue(destination.is_dir())
            self.assertTrue((destination / "css" / "tabler.css").is_file())

    def test_report_assets_keep_non_empty_file_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            target_dir = Path(tmp_dir)
            destination = target_dir / "static"
            destination.write_text("keep me", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                copy_tabler_report_assets(target_dir)

            self.assertTrue(destination.is_file())
            self.assertEqual(destination.read_text(encoding="utf-8"), "keep me")


class LoopbackEndpointTest(unittest.TestCase):
    """A local model server must never be reached through an ambient proxy.

    urllib honours HTTP_PROXY/HTTPS_PROXY by default. With a proxy configured
    (corporate proxy, VPN client, sandbox) a request for a *local* Ollama server
    is sent to the proxy and comes back as a confusing "502 Bad Gateway", which
    made a perfectly healthy local model look broken.
    """

    def test_loopback_hosts_are_detected(self) -> None:
        for host in ("127.0.0.1", "127.1.2.3", "localhost", "LOCALHOST", "::1", "[::1]", "0.0.0.0"):
            self.assertTrue(_is_loopback_host(host), msg=host)

    def test_lookalike_hostnames_are_not_loopback(self) -> None:
        """A prefix match would be a hole: these are remote hostnames."""
        for host in ("127.0.0.1.evil.com", "localhost.evil.com", "1270.0.0.1.nip.io"):
            self.assertFalse(_is_loopback_host(host), msg=host)

    def test_remote_and_private_hosts_are_not_loopback(self) -> None:
        for host in ("api.openai.com", "dashscope.aliyuncs.com", "192.168.1.5", "10.0.0.1", ""):
            self.assertFalse(_is_loopback_host(host), msg=host)

    def _proxy_handler(self, url: str):
        opener = _url_opener(url)
        for handler in opener.handlers:
            if handler.__class__.__name__ == "ProxyHandler":
                return handler
        return None

    def test_loopback_url_has_no_proxy_handler(self) -> None:
        """Passing ProxyHandler({}) makes build_opener drop the env-based one.

        That is the mechanism: the default ProxyHandler is removed, so the
        request cannot be routed to a proxy. An empty handler is not itself
        registered for any protocol, so its absence is the correct assertion.
        """
        opener = _url_opener("http://127.0.0.1:11434/v1/chat/completions")
        self.assertNotIn("ProxyHandler", [h.__class__.__name__ for h in opener.handlers])

    def test_localhost_url_has_no_proxy_handler(self) -> None:
        opener = _url_opener("http://localhost:11434/v1/chat/completions")
        self.assertNotIn("ProxyHandler", [h.__class__.__name__ for h in opener.handlers])

    def test_remote_url_keeps_a_proxy_handler(self) -> None:
        """Cloud APIs must still honour the configured proxy."""
        handler = self._proxy_handler("https://api.openai.com/v1/chat/completions")
        self.assertIsNotNone(handler)

    def test_unparseable_url_does_not_raise(self) -> None:
        self.assertIsNotNone(_url_opener("http://[::1"))


class AnalysisSourceLabelTest(unittest.TestCase):
    """A local template report must not masquerade as an LLM analysis.

    The report card used to be titled "AI 复盘摘要" even when the narrative was
    produced by local rules after an API failure, so the user could not tell
    that the configured model never ran.
    """

    def _report_data(self) -> dict[str, Any]:
        request = ReportGenerationRequest.from_rally_record(_rally_record(), video_name="clip.mp4")
        service = ReportGenerationService(None)
        return service.generate(request, use_api=False).report_data

    def test_local_report_is_labelled_local(self) -> None:
        report_data = self._report_data()
        self.assertNotEqual(report_data.get("analysis_source"), "llm")
        html = render_tabler_report_html(report_data)
        self.assertIn("本地规则分析", html)
        self.assertNotIn("AI 复盘摘要", html)

    def test_llm_marker_switches_the_label(self) -> None:
        report_data = _inject_report_text(self._report_data(), "第一行结论\n第二行结论")
        self.assertEqual(report_data.get("analysis_source"), "llm")
        html = render_tabler_report_html(report_data)
        self.assertIn("大模型分析", html)

    def test_empty_report_text_does_not_claim_llm(self) -> None:
        report_data = _inject_report_text(self._report_data(), "   \n  ")
        self.assertNotEqual(report_data.get("analysis_source"), "llm")

    def test_api_failure_is_rendered_inside_the_report(self) -> None:
        report_data = self._report_data()
        report_data["analysis_error"] = "connection refused"
        html = render_tabler_report_html(report_data)
        self.assertIn("大模型分析未生效", html)
        self.assertIn("connection refused", html)

    def test_no_error_note_when_nothing_failed(self) -> None:
        self.assertEqual(_analysis_error_note({}), "")
        html = render_tabler_report_html(self._report_data())
        self.assertNotIn("大模型分析未生效", html)


class ApiPayloadFitTest(unittest.TestCase):
    """The request must fit a small local context window.

    Measured on the shipped record: the per-hit list was 72.5% of the request
    and grew without bound (20s -> 10.2 KB, 600s -> 219 KB, ~59k tokens), so a
    long video pushed the system prompt out of Ollama's window and the model
    wrote a report about data it never saw.
    """

    def _hits(self, count: int) -> list[dict[str, Any]]:
        return [
            {
                "frame_id": index,
                "timestamp_ms": 1000 + index * 50,
                "source": "bst",
                "player": "bottom",
                "player_label": "下方球员",
                "stroke": "发短球",
                "raw_stroke": "Bottom_發短球",
                "zone": "mid",
                "confidence": 0.49751096963882446,
                "event_confidence": 0.6,
                "speed_kmh": 140.25,
            }
            for index in range(count)
        ]

    def _record(self, hit_count: int) -> dict[str, Any]:
        record = _rally_record()
        record["summary"] = dict(record["summary"])
        record["summary"]["rally_hit_count"] = hit_count
        record["details"] = {"hits": self._hits(hit_count), "players": {"user": {"distance_m": 32.0}}}
        return record

    def _payload_text(self, hit_count: int) -> str:
        request = ReportGenerationRequest.from_rally_record(
            self._record(hit_count), video_name="clip.mp4"
        )
        return json.dumps(request.to_payload(), ensure_ascii=False, separators=(",", ":"))

    def test_payload_stays_bounded_as_events_grow(self) -> None:
        small = len(self._payload_text(23))
        big = len(self._payload_text(2000))
        self.assertLessEqual(big, _API_RECORD_CHAR_BUDGET + 1000)
        # The 2000-hit request must not be an order of magnitude bigger.
        self.assertLess(big, small * 3)

    def test_summary_and_players_are_never_trimmed(self) -> None:
        compact = compact_rally_record_for_api(self._record(2000))
        self.assertIsInstance(compact.get("summary"), dict)
        self.assertIn("players", compact.get("details", {}))
        self.assertGreater(len(json.dumps(compact["summary"], ensure_ascii=False)), 100)

    def test_events_are_sampled_with_an_explicit_note(self) -> None:
        compact = compact_rally_record_for_api(self._record(2000))
        info = compact["details"]["events_info"]
        self.assertEqual(info["total"], 2000)
        self.assertEqual(info["included"], _MAX_API_EVENTS)
        self.assertTrue(info["sampled"])

    def test_short_clip_keeps_every_event(self) -> None:
        compact = compact_rally_record_for_api(self._record(5))
        info = compact["details"]["events_info"]
        self.assertEqual(info["included"], 5)
        self.assertFalse(info["sampled"])

    def test_events_use_short_keys(self) -> None:
        compact = compact_rally_record_for_api(self._record(3))
        row = compact["details"]["events"][0]
        self.assertIn("t", row)
        self.assertIn("s", row)
        self.assertNotIn("player_label", row)
        self.assertNotIn("raw_stroke", row)

    def test_sampling_preserves_order_and_spread(self) -> None:
        """A uniform sample, not just the tail: keeps the distribution honest."""
        compact = compact_rally_record_for_api(self._record(400))
        events = compact["details"]["events"]
        times = [row["t"] for row in events]
        self.assertEqual(times, sorted(times))
        # Spans nearly the whole clip rather than clustering at the end.
        self.assertGreater(times[-1] - times[0], 5.0)

    def test_record_without_details_is_left_alone(self) -> None:
        record = {"summary": {"rally_hit_count": 1}}
        compact = compact_rally_record_for_api(record)
        self.assertEqual(compact.get("summary"), {"rally_hit_count": 1})
        self.assertFalse(compact.get("truncated", False))


class ReasoningStripTest(unittest.TestCase):
    """qwen3 emits ＜think＞ blocks by default; they must not reach the report."""

    _O = "<" + "think" + ">"
    _C = "<" + "/" + "think" + ">"
    _Q_C = "<" + "｜end▁of▁thinking｜" + ">"

    def test_plain_answer_untouched(self) -> None:
        self.assertEqual(_strip_reasoning("这是报告正文。"), "这是报告正文。")

    def test_qwen3_special_token_block_removed(self) -> None:
        raw = f"{self._O}先分析数据{self._Q_C}最终结论：网前偏弱。"
        self.assertEqual(_strip_reasoning(raw), "最终结论：网前偏弱。")

    def test_ordinary_think_tags_removed(self) -> None:
        """Other reasoning models close with </think>, not the qwen3 token."""
        raw = f"{self._O}推理{self._C}最终结论。"
        self.assertEqual(_strip_reasoning(raw), "最终结论。")

    def test_thinking_spelling_removed(self) -> None:
        raw = "<" + "thinking" + ">推理<" + "/" + "thinking" + ">答案"
        self.assertEqual(_strip_reasoning(raw), "答案")

    def test_unterminated_block_is_dropped(self) -> None:
        """A truncated stream must not leak raw deliberation."""
        self.assertEqual(_strip_reasoning(f"{self._O}被截断的推理没有结束"), "")

    def test_json_after_block_survives_parsing(self) -> None:
        raw = self._O + "想一下" + self._Q_C + '{"report_text":"结论"}'
        stripped = _strip_reasoning(raw)
        self.assertEqual(json.loads(stripped)["report_text"], "结论")

    def test_empty_input(self) -> None:
        self.assertEqual(_strip_reasoning(""), "")


class LocalServerRoundTripTest(unittest.TestCase):
    """Drive the real client against a real HTTP server on loopback.

    Proves three things at once: the loopback proxy bypass lets the request
    through even with *_proxy set, a qwen3-style ＜think＞ response is cleaned,
    and a JSON body inside the answer is parsed into report fields.
    """

    @classmethod
    def setUpClass(cls) -> None:
        import http.server
        import threading

        cls.requests: list[dict[str, Any]] = []

        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                type(self)._captured = body  # type: ignore[attr-defined]
                cls.requests.append(body)
                answer = (
                    "<" + "think" + ">先看看数据" + "<" + "｜end▁of▁thinking｜" + ">"
                    + json.dumps(
                        {"status": "ok", "report_text": "网前参与率偏低，建议加强搓放跟进。"},
                        ensure_ascii=False,
                    )
                )
                payload = json.dumps(
                    {"choices": [{"message": {"role": "assistant", "content": answer}}]},
                    ensure_ascii=False,
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):  # silence
                return

        cls.server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def _client(self):
        return OpenAICompatibleReportApiClient(
            f"http://127.0.0.1:{self.port}/v1/chat/completions",
            model="qwen3:8b",
            api_key="",
            timeout_s=10.0,
        )

    def test_round_trip_through_loopback(self) -> None:
        request = ReportGenerationRequest.from_rally_record(
            _rally_record(), video_name="clip.mp4"
        )
        raw = self._client().generate(request)
        # ＜think＞ stripped, JSON parsed.
        self.assertEqual(raw["report_text"], "网前参与率偏低，建议加强搓放跟进。")
        self.assertEqual(raw["status"], "ok")

    def test_request_carries_bounded_payload_and_output_cap(self) -> None:
        # Drives its own request: unittest orders methods alphabetically, so it
        # cannot rely on the round-trip test having run first.
        self._client().generate(
            ReportGenerationRequest.from_rally_record(_rally_record(), video_name="clip.mp4")
        )
        self.assertTrue(self.requests, "no request captured")
        body = self.requests[-1]
        self.assertEqual(body["max_tokens"], _API_MAX_OUTPUT_TOKENS)
        sent = body["messages"][1]["content"]
        self.assertLessEqual(len(sent), _API_RECORD_CHAR_BUDGET + 2000)
        self.assertNotIn("\n  ", sent, "payload should be sent compact, not indented")
        self.assertIn("/no_think", body["messages"][0]["content"])

    def test_llm_text_reaches_the_rendered_report(self) -> None:
        """The user-visible outcome: LLM prose lands in the report, labelled.

        This is what "报告要根据视频内容进行分析" means in practice — before
        the fix the report body was always local template text.
        """
        request = ReportGenerationRequest.from_rally_record(
            _rally_record(), athlete_name="CHANG", video_name="clip.mp4"
        )
        service = ReportGenerationService(self._client())
        response = service.generate(request, use_api=True)

        self.assertEqual(response.report_text, "网前参与率偏低，建议加强搓放跟进。")
        self.assertEqual(response.report_data.get("analysis_source"), "llm")
        self.assertIn("网前参与率偏低", response.html)
        self.assertIn("大模型分析", response.html)
        self.assertNotIn("大模型分析未生效", response.html)


class LooseJsonResponseTest(unittest.TestCase):
    """Models follow "return JSON" loosely; a usable answer must not be lost."""

    def _client(self, content: str) -> OpenAICompatibleReportApiClient:
        def transport(url, payload, headers, timeout_s):
            return {"choices": [{"message": {"role": "assistant", "content": content}}]}

        return OpenAICompatibleReportApiClient(
            "http://127.0.0.1:11434/v1/chat/completions",
            model="qwen3:8b",
            api_key="",
            timeout_s=5.0,
            transport=transport,
        )

    def _request(self) -> ReportGenerationRequest:
        return ReportGenerationRequest.from_rally_record(_rally_record(), video_name="c.mp4")

    def test_json_with_report_text(self) -> None:
        raw = self._client('{"report_text": "结论A"}').generate(self._request())
        self.assertEqual(raw["report_text"], "结论A")

    def test_json_with_unexpected_field_keeps_the_prose(self) -> None:
        """A JSON object with neither report_text nor report_data."""
        raw = self._client('{"analysis": "网前偏弱"}').generate(self._request())
        self.assertIn("网前偏弱", str(raw.get("report_text")))

    def test_json_with_only_report_data_defers_to_the_data(self) -> None:
        raw = self._client('{"report_data": {"summary": {"takeaways": ["x"]}}}').generate(
            self._request()
        )
        self.assertEqual(raw.get("report_text") or "", "")
        self.assertIsInstance(raw.get("report_data"), dict)

    def test_plain_prose_falls_back_to_text(self) -> None:
        raw = self._client("网前参与率偏低。").generate(self._request())
        self.assertEqual(raw["report_text"], "网前参与率偏低。")

    def test_fenced_json_is_parsed(self) -> None:
        raw = self._client('```json\n{"report_text": "结论B"}\n```').generate(self._request())
        self.assertEqual(raw["report_text"], "结论B")


if __name__ == "__main__":
    unittest.main()
