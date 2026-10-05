from __future__ import annotations

import ipaddress
import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol
from urllib import error, parse, request

from src.utils.report_export import (
    ReportExportTarget,
    export_report_image,
    write_report_sidecar,
)
from src.utils.user_report import build_user_report_data_from_rally_record, render_user_report_html as render_standalone_user_report_html


REPORT_INTERFACE_VERSION = "wfbar-report.v1"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
TABLER_REPORT_TEMPLATE_DIR = PROJECT_ROOT / "assets" / "report_template"
TABLER_REPORT_TEMPLATE_PATH = TABLER_REPORT_TEMPLATE_DIR / "report-template.html"
TABLER_REPORT_ASSETS_DIR = TABLER_REPORT_TEMPLATE_DIR / "static"
TABLER_REPORT_LEGACY_SOURCE_DIR = PROJECT_ROOT / "assets" / "temp"
TABLER_REPORT_DIST_DIR = PROJECT_ROOT / "assets" / "dist"
TABLER_REPORT_LEGACY_TEMPLATE_PATH = TABLER_REPORT_LEGACY_SOURCE_DIR / "report-template.html"
TABLER_REPORT_FALLBACK_TEMPLATE_PATH = TABLER_REPORT_DIST_DIR / "report-template.html"
TABLER_REPORT_OUTPUT_PATH = TABLER_REPORT_DIST_DIR / "index.html"


class ReportGenerationError(RuntimeError):
    """Raised when a report cannot be generated locally or through an API."""


# --- API request sizing --------------------------------------------------
# Local models run with a small context window. Ollama's default ``num_ctx``
# has been 2048 and later 4096; llama.cpp/others default similarly. **Anything
# that does not fit is dropped silently** — and because the per-hit event list
# dominates the request (measured: 72.5% of the record, ~345 chars per hit),
# a long video pushes the *system prompt* or the aggregate summary out of the
# window and the model then writes a report about data it never saw.
#
# Measured growth with the shipped rally record:
#     20s /  23 hits ->  10.2 KB  ~2.7k tokens   (already over a 2048 window)
#     60s /  70 hits ->  24.4 KB  ~6.6k tokens
#    180s / 210 hits ->  67.5 KB  ~18.3k tokens
#
# So the request is compacted to fit the 4096 floor with room for the reply.
_API_RECORD_CHAR_BUDGET = 8000
_MAX_API_EVENTS = 80
# Bounded so a reasoning model cannot spend the whole context on deliberation.
_API_MAX_OUTPUT_TOKENS = 1024

# Reasoning models (qwen3 and friends) wrap chain-of-thought in think blocks.
# The delimiters differ by model family, so accept both spellings:
#   * qwen3 uses the dedicated special tokens ＜think＞ … ＜｜end▁of▁thinking｜＞
#   * most others (DeepSeek-R1 style, many fine-tunes) use ＜think＞ … ＜/think＞
_THINK_PATTERNS = (
    re.compile(r"(?is)<think[^>]*>.*?<｜end▁of▁thinking｜>"),
    re.compile(r"(?is)<think(?:ing)?>.*?</think(?:ing)?>"),
    # A truncated stream can leave an unterminated block; drop the remainder
    # rather than letting raw deliberation reach the report body.
    re.compile(r"(?is)<think(?:ing)?>.*$"),
)


def _strip_reasoning(text: str) -> str:
    """Remove chain-of-thought deliberation from model output.

    qwen3 emits it by default. Leaving it in would put the model's raw
    reasoning into the report body, and an unterminated block can also swallow
    the answer (or hide a JSON body from the parser).
    """
    cleaned = text or ""
    for pattern in _THINK_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    return cleaned.strip()


def _compact_event(item: object) -> dict[str, Any]:
    """One per-hit row reduced to short keys and rounded numbers."""
    row = _mapping(item)
    out: dict[str, Any] = {}
    if row.get("timestamp_ms") is not None:
        out["t"] = round(_float(row.get("timestamp_ms")) / 1000.0, 2)
    for source, target in (("player", "p"), ("stroke", "s"), ("zone", "z")):
        value = row.get(source)
        if value not in (None, ""):
            text = str(value)
            if text.lower() not in ("unknown", "none"):
                out[target] = text
    if row.get("confidence") is not None:
        out["c"] = round(_float(row.get("confidence")), 2)
    if row.get("speed_kmh") is not None:
        out["v"] = round(_float(row.get("speed_kmh")), 1)
    return out


def _downsample(items: list[Any], limit: int) -> tuple[list[Any], int]:
    """Evenly sample ``items`` down to ``limit``, preserving order.

    A uniform sample keeps the stroke/zone *distribution* intact, which is what
    the report analyses. Taking only the tail would bias every conclusion
    towards the end of the rally.
    """
    if limit <= 0 or len(items) <= limit:
        return list(items), 0
    step = len(items) / float(limit)
    picked = [items[min(len(items) - 1, int(index * step))] for index in range(limit)]
    return picked, len(items) - len(picked)


def compact_rally_record_for_api(
    record: Mapping[str, Any],
    *,
    char_budget: int = _API_RECORD_CHAR_BUDGET,
) -> dict[str, Any]:
    """Shrink a rally record so a small local context window can hold it.

    The aggregate ``summary`` and ``details.players`` are always kept in full —
    they carry the numbers every conclusion is drawn from. Only the per-hit
    event list is condensed, capped, and (as a last resort) dropped, with an
    explicit note so the model knows the data was trimmed rather than absent.
    """
    out: dict[str, Any] = dict(record)
    details = dict(_mapping(out.get("details")))
    hits = details.pop("hits", None)
    out["details"] = details

    if isinstance(hits, list) and hits:
        rows = [row for row in (_compact_event(item) for item in hits) if row]
        total = len(rows)
        kept_rows, _ = _downsample(rows, _MAX_API_EVENTS)
        details["events"] = kept_rows
        details["events_info"] = {
            "total": total,
            "included": len(kept_rows),
            "sampled": len(kept_rows) < total,
        }

    def _size(payload: Mapping[str, Any]) -> int:
        return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))

    if _size(out) > char_budget and "events" in details:
        total = details.get("events_info", {}).get("total", 0)
        details.pop("events", None)
        details["events_info"] = {
            "total": total,
            "included": 0,
            "note": "事件逐条列表过大已省略，请仅依据 summary 与 players 的汇总指标分析。",
        }

    # Even without events the record can be large (e.g. many players); say so
    # rather than letting the server truncate silently.
    if _size(out) > char_budget:
        out["truncated"] = True
    return out


class ReportApiTransport(Protocol):
    def __call__(
        self,
        url: str,
        payload: Mapping[str, Any],
        headers: Mapping[str, str],
        timeout_s: float,
    ) -> Mapping[str, Any]:
        ...


@dataclass(slots=True)
class ReportGenerationRequest:
    """Structured payload sent to a report generation API."""

    report_id: str
    rally_record: Mapping[str, Any]
    athlete_name: str = "训练用户"
    user_player: str = "bottom"
    video_name: str | None = None
    locale: str = "zh-CN"
    generated_at: str | None = None
    prompt: str = "请基于羽毛球视频分析数据生成训练复盘报告。"
    options: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_rally_record(
        cls,
        rally_record: Mapping[str, Any],
        *,
        report_id: str | None = None,
        athlete_name: str = "训练用户",
        user_player: str = "bottom",
        video_name: str | None = None,
        locale: str = "zh-CN",
        generated_at: str | None = None,
        prompt: str | None = None,
        options: Mapping[str, Any] | None = None,
    ) -> "ReportGenerationRequest":
        summary = _mapping(rally_record.get("summary"))
        inferred_id = str(
            report_id
            or rally_record.get("rally_id")
            or summary.get("rally_id")
            or rally_record.get("rally_name")
            or "wfbar-report"
        )
        return cls(
            report_id=inferred_id,
            rally_record=rally_record,
            athlete_name=athlete_name,
            user_player=user_player,
            video_name=video_name,
            locale=locale,
            generated_at=generated_at,
            prompt=prompt or "请基于羽毛球视频分析数据生成训练复盘报告。",
            options=dict(options or {}),
        )

    def to_payload(self) -> dict[str, Any]:
        """Return the JSON-serializable interface payload for API transmission.

        The rally record is compacted (see :func:`compact_rally_record_for_api`)
        so the request fits a small local context window instead of being
        silently truncated by the server.
        """
        return {
            "interface_version": REPORT_INTERFACE_VERSION,
            "task": "badminton_report_generation",
            "report_id": self.report_id,
            "locale": self.locale,
            "generated_at": self.generated_at,
            "prompt": self.prompt,
            "options": dict(self.options),
            "data": {
                "athlete_name": self.athlete_name,
                "user_player": self.user_player,
                "video_name": self.video_name,
                "rally_record": compact_rally_record_for_api(self.rally_record),
            },
        }


@dataclass(slots=True)
class ReportGenerationResponse:
    report_id: str
    status: str
    provider: str
    report_data: dict[str, Any]
    html: str
    report_text: str = ""
    raw_response: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "interface_version": REPORT_INTERFACE_VERSION,
            "report_id": self.report_id,
            "status": self.status,
            "provider": self.provider,
            "report_text": self.report_text,
            "report_data": self.report_data,
            "html": self.html,
            "raw_response": self.raw_response,
        }


class ReportApiClient:
    """Minimal JSON API client for external report generation services."""

    def __init__(
        self,
        endpoint_url: str,
        *,
        api_key: str | None = None,
        timeout_s: float = 30.0,
        headers: Mapping[str, str] | None = None,
        transport: ReportApiTransport | None = None,
    ) -> None:
        self.endpoint_url = str(endpoint_url).strip()
        self.api_key = str(api_key).strip() if api_key else None
        self.timeout_s = max(1.0, float(timeout_s))
        self.headers = dict(headers or {})
        self.transport = transport or _urllib_post_json
        if not self.endpoint_url:
            raise ValueError("endpoint_url is required for ReportApiClient.")

    def generate(self, generation_request: ReportGenerationRequest) -> Mapping[str, Any]:
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
            **self.headers,
        }
        if self.api_key:
            headers.setdefault("Authorization", f"Bearer {self.api_key}")
        return self.transport(self.endpoint_url, generation_request.to_payload(), headers, self.timeout_s)


class OpenAICompatibleReportApiClient(ReportApiClient):
    """Report client for OpenAI-compatible chat/completions APIs."""

    def __init__(
        self,
        endpoint_url: str,
        *,
        model: str,
        api_key: str | None = None,
        timeout_s: float = 60.0,
        headers: Mapping[str, str] | None = None,
        transport: ReportApiTransport | None = None,
    ) -> None:
        super().__init__(
            endpoint_url,
            api_key=api_key,
            timeout_s=timeout_s,
            headers=headers,
            transport=transport,
        )
        self.model = str(model).strip()
        if not self.model:
            raise ValueError("model is required for OpenAICompatibleReportApiClient.")

    def generate(self, generation_request: ReportGenerationRequest) -> Mapping[str, Any]:
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是严谨的羽毛球训练报告分析模型。只能使用用户提供的结构化数据，"
                        "不得补造击球类型、落点位置、失误原因、技术动作或比分；字段缺失时必须"
                        "明确写“数据不足，无法判断”。每条结论和建议都应能对应到输入中的实际"
                        "指标，区分事实、计算结果和建议，不输出思考过程。请输出中文训练复盘，"
                        "优先返回 JSON 对象，字段包含 report_text；如能基于现有数据生成完整"
                        "可视化数据，可附带 report_data 或 html。\n"
                        "直接给出最终结论，不要展开推理。\n"
                        "/no_think"
                    ),
                },
                {
                    "role": "user",
                    # Compact, not indented: pretty-printing this payload with
                    # indent=2 inflated it ~2.4x (measured 4.2 KB -> 8.6 KB,
                    # ~1100 wasted tokens) for whitespace the model never needs.
                    "content": json.dumps(
                        generation_request.to_payload(),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            ],
            "temperature": 0.2,
            # Bounded explicitly: local servers otherwise pick their own limit,
            # and a reasoning model can spend the entire window on deliberation
            # and return a truncated report.
            "max_tokens": _API_MAX_OUTPUT_TOKENS,
        }
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
            **self.headers,
        }
        if self.api_key:
            headers.setdefault("Authorization", f"Bearer {self.api_key}")
        raw = dict(self.transport(self.endpoint_url, payload, headers, self.timeout_s))
        content = _openai_message_content(raw)
        parsed = _json_object_from_text(content)
        if parsed is not None:
            parsed.setdefault("provider", "openai-compatible")
            parsed.setdefault("raw_response", raw)
            # Models often follow "return JSON" loosely. If the object carries
            # neither the prose field we asked for nor replacement data, keep
            # whatever they wrote rather than silently discarding the analysis.
            has_text = bool(str(parsed.get("report_text") or "").strip())
            if not has_text and not parsed.get("report_data"):
                parsed["report_text"] = content
            return parsed
        return {
            "status": "ok",
            "provider": "openai-compatible",
            "report_text": content,
            "raw_response": raw,
        }


class ReportGenerationService:
    """Generate badminton reports from current WFBARNet rally records."""

    def __init__(self, api_client: ReportApiClient | None = None) -> None:
        self.api_client = api_client

    def build_local_report(
        self,
        generation_request: ReportGenerationRequest,
        *,
        report_text: str = "",
    ) -> ReportGenerationResponse:
        generated_at = generation_request.generated_at or datetime.now().strftime("%Y-%m-%d %H:%M")
        report_data = build_user_report_data_from_rally_record(
            generation_request.rally_record,
            user_player=generation_request.user_player,
            athlete_name=generation_request.athlete_name,
            video_name=generation_request.video_name,
            generated_at=generated_at,
        )
        if report_text.strip():
            report_data = _inject_report_text(report_data, report_text)
        html = render_tabler_report_html(report_data)
        return ReportGenerationResponse(
            report_id=generation_request.report_id,
            status="ok",
            provider="local",
            report_text=report_text,
            report_data=report_data,
            html=html,
            raw_response={},
        )

    def generate(
        self,
        generation_request: ReportGenerationRequest,
        *,
        use_api: bool = False,
    ) -> ReportGenerationResponse:
        if not use_api:
            return self.build_local_report(generation_request)
        if self.api_client is None:
            raise ReportGenerationError("API report generation requested but no ReportApiClient was configured.")

        raw = dict(self.api_client.generate(generation_request))
        report_text = str(raw.get("report_text") or "")
        local = self.build_local_report(generation_request, report_text=report_text)
        report_data = _merge_api_report_data(local.report_data, raw.get("report_data"), report_text)
        html = render_tabler_report_html(report_data)
        return ReportGenerationResponse(
            report_id=str(raw.get("report_id") or generation_request.report_id),
            status=str(raw.get("status") or "ok"),
            provider=str(raw.get("provider") or "api"),
            report_text=str(raw.get("report_text") or ""),
            report_data=report_data,
            html=html,
            raw_response=raw,
        )

    def export_html(self, response: ReportGenerationResponse, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(response.html, encoding="utf-8")
        copy_tabler_report_assets(path.parent)

    def export_text(self, response: ReportGenerationResponse, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(response.report_text, encoding="utf-8")

    def export_to_target(self, response: ReportGenerationResponse, target: ReportExportTarget) -> Path:
        """Write a report (and its JSON sidecar) to a resolved export target.

        PDF/PNG need a GPU-backed render process, so they are produced in an
        isolated child process; the HTML source is always written regardless.
        """
        path = target.path
        html_path = path.with_suffix(".html") if target.format in ("pdf", "png") else path
        self.export_html(response, html_path)

        if target.format in ("pdf", "png"):
            export_report_image(html_path, path, fmt=target.format, landscape=True)
        else:
            path = html_path

        payload = {
            "report_id": response.report_id,
            "status": response.status,
            "provider": response.provider,
            "report_text": response.report_text,
            "report_data": response.report_data,
            "raw_response": response.raw_response,
        }
        write_report_sidecar(target, payload)
        return path


def generate_report_from_rally_record(
    rally_record: Mapping[str, Any],
    *,
    athlete_name: str = "训练用户",
    user_player: str = "bottom",
    video_name: str | None = None,
    api_client: ReportApiClient | None = None,
    use_api: bool = False,
    output_html_path: Path | None = None,
) -> ReportGenerationResponse:
    generation_request = ReportGenerationRequest.from_rally_record(
        rally_record,
        athlete_name=athlete_name,
        user_player=user_player,
        video_name=video_name,
    )
    service = ReportGenerationService(api_client)
    response = service.generate(generation_request, use_api=use_api)
    if output_html_path is not None:
        service.export_html(response, output_html_path)
    return response


def render_tabler_report_html(report_data: Mapping[str, Any]) -> str:
    """Render the user report with the stable local report template."""
    template_path = _tabler_report_template_path()
    if template_path is None:
        return render_standalone_user_report_html(report_data)

    template = template_path.read_text(encoding="utf-8")
    template_data = _tabler_template_data(report_data)
    meta = _mapping(report_data.get("meta"))
    summary = _mapping(report_data.get("summary"))
    players = _mapping(report_data.get("players"))
    user = _mapping(players.get("user"))
    opponent = _mapping(players.get("opponent"))
    reliability = list(_sequence(report_data.get("reliability")))
    zone_distribution = list(_sequence(report_data.get("zone_distribution")))
    metrics = list(_sequence(summary.get("metrics")))

    template = _inject_tabler_report_data(template, template_data)

    title = str(template_data.get("title") or meta.get("title") or "羽毛球水平分析报告")
    subtitle = str(template_data.get("subtitle") or meta.get("subtitle") or "上传视频后由 WFBARNet 自动生成的训练复盘")
    template = re.sub(
        r'(<h1 class="report-title mb-3">)(.*?)(</h1>)',
        lambda match: f"{match.group(1)}{escape(title)}{match.group(3)}",
        template,
        count=1,
        flags=re.DOTALL,
    )

    zones = _zone_percentages(zone_distribution)
    user_player = _mapping(players.get("user"))
    opponent_player = _mapping(players.get("opponent"))
    fields = {
        "title": title,
        "subtitle": subtitle,
        "athlete_name": str(meta.get("athlete_name") or "训练用户"),
        "generated_at": str(meta.get("generated_at") or datetime.now().strftime("%Y-%m-%d %H:%M")),
        "video_name": str(meta.get("video_name") or "training_clip.mp4"),
        "duration_s": _metric_value(metrics, "时长", fallback="0.00秒"),
        "duration": _fmt_seconds_short(template_data.get("duration")),
        "hit_count": _metric_value(metrics, "击球", fallback=_fmt_number(user.get("hit_count") or opponent.get("hit_count") or 0)),
        "rally_count": _fmt_number(template_data.get("rally_count")),
        "avg_hit_interval": _metric_value(metrics, "间隔", fallback="0.00秒"),
        "reliability_score": _fmt_percent(_avg_reliability(reliability)),
        "reliability": f"可信度 {_fmt_percent(template_data.get('reliability'))}",
        "distance": _fmt_meters(template_data.get("distance")),
        "front_zone": _fmt_percent(zones.get("front", 0.0)),
        "mid_zone": _fmt_percent(zones.get("mid", 0.0)),
        "back_zone": _fmt_percent(zones.get("back", 0.0)),
        "front_zone_bar": _bar_html(zones.get("front", 0.0)),
        "mid_zone_bar": _bar_html(zones.get("mid", 0.0)),
        "back_zone_bar": _bar_html(zones.get("back", 0.0)),
        "ai_summary": _summary_html(summary),
        "ai_highlights": _render_highlights(report_data),
        "analysis_source": _analysis_source_badge(report_data),
        "analysis_error": _analysis_error_note(report_data),
        "bottom_distance": _fmt_meters(user.get("distance_m")),
        "top_distance": _fmt_meters(opponent.get("distance_m")),
        "bottom_label": f"{_player_label(user_player, '下方')}跑动",
        "top_label": f"{_player_label(opponent_player, '上方')}跑动",
        "avg_speed": _fmt_speed(_player_avg_speed(user_player)),
        "avg_speed_bar": _bar_html(_speed_ratio(user_player)),
        "track_visible_rate": _fmt_percent(_find_reliability(reliability, ("球", "track", "visible"))),
        "pose_valid_rate": _fmt_percent(_find_reliability(reliability, ("姿态", "pose"))),
        "court_status": _court_status_text(report_data),
        "bst_coverage": _bst_coverage_text(report_data),
        "court_heat": _render_court_heat(report_data.get("court_heat")),
        "stroke_rows": _render_stroke_rows(report_data.get("stroke_distribution")),
        "event_timeline": _render_event_timeline(report_data.get("events")),
        "metric_rows": _render_metric_rows(report_data, reliability),
        "training_rows": _render_training_rows(report_data.get("training_plan")),
        "report_disclaimer": escape(str(report_data.get("disclaimer") or "")),
        "notes": _notes_text(report_data),
        "rally_rows": _render_temp_rally_rows(template_data.get("rallies")),
        "event_rows": _render_temp_event_rows(template_data.get("events")),
    }
    for field, value in fields.items():
        template = _replace_report_field(template, field, value)

    return template


def copy_tabler_report_assets(target_dir: Path) -> None:
    """Copy the Tabler dist assets next to exported reports for local HTML preview."""
    source = _tabler_report_assets_source()
    if not source.exists():
        return
    destination_name = "static" if source == TABLER_REPORT_ASSETS_DIR else "dist"
    destination = target_dir / destination_name
    if source.resolve() == destination.resolve():
        return
    if destination.exists() and not destination.is_dir():
        if destination.stat().st_size == 0:
            destination.unlink()
        else:
            raise FileExistsError(
                f"报告静态资源目标路径已存在且不是目录，请手动检查：{destination}"
            )
    shutil.copytree(source, destination, dirs_exist_ok=True)


def _tabler_report_template_path() -> Path | None:
    if TABLER_REPORT_TEMPLATE_PATH.exists():
        return TABLER_REPORT_TEMPLATE_PATH
    if TABLER_REPORT_LEGACY_TEMPLATE_PATH.exists():
        return TABLER_REPORT_LEGACY_TEMPLATE_PATH
    if TABLER_REPORT_FALLBACK_TEMPLATE_PATH.exists():
        return TABLER_REPORT_FALLBACK_TEMPLATE_PATH
    if TABLER_REPORT_OUTPUT_PATH.exists():
        return TABLER_REPORT_OUTPUT_PATH
    return None


def _tabler_report_assets_source() -> Path:
    if TABLER_REPORT_ASSETS_DIR.exists():
        return TABLER_REPORT_ASSETS_DIR
    legacy = TABLER_REPORT_LEGACY_SOURCE_DIR / "dist"
    return legacy if legacy.exists() else TABLER_REPORT_DIST_DIR / "dist"


def _inject_tabler_report_data(template: str, payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    if "__REPORT_DATA_PLACEHOLDER__" in template:
        return template.replace("__REPORT_DATA_PLACEHOLDER__", encoded, 1)
    pattern = re.compile(
        r'(<script\b[^>]*\bid="report-data"[^>]*>\s*)(.*?)(\s*</script>)',
        re.DOTALL | re.IGNORECASE,
    )
    return pattern.sub(lambda match: f"{match.group(1)}{encoded}{match.group(3)}", template, count=1)


def _tabler_template_data(report_data: Mapping[str, Any]) -> dict[str, Any]:
    meta = _mapping(report_data.get("meta"))
    summary = _mapping(report_data.get("summary"))
    players = _mapping(report_data.get("players"))
    user = _mapping(players.get("user"))
    reliability = list(_sequence(report_data.get("reliability")))
    metrics = list(_sequence(summary.get("metrics")))
    duration = _metric_number(metrics, "时长")
    hit_count = _metric_number(metrics, "击球")
    distance = _float(user.get("distance_m"))
    score = _float(summary.get("overall_score")) / 100.0
    reliability_score = _avg_reliability(reliability) or score
    events = _tabler_events(report_data.get("events"))
    return {
        "title": str(meta.get("title") or "羽毛球水平分析报告"),
        "subtitle": _tabler_subtitle(meta, summary),
        "rally_count": 1 if duration > 0 or hit_count > 0 else 0,
        "hit_count": int(hit_count),
        "duration": duration,
        "distance": distance,
        "reliability": reliability_score,
        "notes": _notes_text(report_data),
        "rallies": _tabler_rallies(duration, hit_count, reliability_score),
        "events": events,
        "metrics": metrics,
    }


def _tabler_subtitle(meta: Mapping[str, Any], summary: Mapping[str, Any]) -> str:
    parts = [
        str(meta.get("video_name") or "training_clip.mp4"),
        str(meta.get("athlete_name") or "训练用户"),
        str(meta.get("generated_at") or datetime.now().strftime("%Y-%m-%d %H:%M")),
    ]
    level = str(summary.get("level_label") or "")
    if level:
        parts.append(level)
    return " · ".join(part for part in parts if part)


def _analysis_source_badge(report_data: Mapping[str, Any]) -> str:
    """A small badge naming how the narrative analysis was actually produced.

    Without this a local, rule-based report is visually identical to one whose
    prose came from the configured LLM — the card is even titled "AI 复盘摘要".
    A user who configured a model and silently fell back then reasonably
    concludes the report ignores the video content.
    """
    source = str(report_data.get("analysis_source") or "local").strip().lower()
    if source == "llm":
        return (
            '<span class="badge bg-green-lt ms-1" '
            'title="本报告的结论文字由已配置的大模型分析生成">大模型分析</span>'
        )
    return (
        '<span class="badge bg-secondary-lt ms-1" '
        'title="未使用大模型；结论由本地规则依据本视频的统计数据推导">本地规则分析</span>'
    )


def _analysis_error_note(report_data: Mapping[str, Any]) -> str:
    """Surface an LLM failure inside the report instead of only in the log."""
    message = str(report_data.get("analysis_error") or "").strip()
    if not message:
        return ""
    return (
        '<div class="alert alert-warning py-2 px-3 small" role="alert">'
        "<strong>大模型分析未生效</strong>，本报告为本地规则分析。"
        f"原因：{escape(message)}"
        "</div>"
    )


def _notes_text(report_data: Mapping[str, Any]) -> str:
    summary = _mapping(report_data.get("summary"))
    takeaways = [str(item) for item in _sequence(summary.get("takeaways")) if str(item).strip()]
    if takeaways:
        return "\n".join(takeaways[:5])
    note = str(summary.get("level_note") or "").strip()
    if note:
        return note
    return str(report_data.get("disclaimer") or "本报告基于视频分析自动生成，适合训练复盘和趋势比较。")


def _tabler_rallies(duration: float, hit_count: float, reliability: float) -> list[dict[str, Any]]:
    if duration <= 0 and hit_count <= 0:
        return []
    return [
        {
            "start_time": 0.0,
            "end_time": max(0.0, duration),
            "hit_count": int(hit_count),
            "reliability": reliability,
        }
    ]


def _tabler_events(value: object) -> list[dict[str, Any]]:
    events = []
    for item in _sequence(value)[:12]:
        event = _mapping(item)
        stroke = str(event.get("stroke") or event.get("type") or "击球事件")
        player = str(event.get("player_label") or "")
        if player.lower() == "none":
            player = ""
        zone = str(event.get("zone_label") or "")
        time_value = _event_seconds(event.get("time"))
        description = " · ".join(part for part in (player, zone, stroke) if part)
        events.append(
            {
                "type": stroke,
                "time": time_value,
                "description": description or "WFBARNet 自动识别事件",
                "confidence": _float(event.get("confidence")),
            }
        )
    return events


def _render_temp_rally_rows(value: object) -> str:
    rows = []
    for index, item in enumerate(_sequence(value)):
        rally = _mapping(item)
        rows.append(
            "<tr>"
            f"<td>{index + 1}</td>"
            f"<td>{escape(_fmt_seconds_short(rally.get('start_time')))}</td>"
            f"<td>{escape(_fmt_seconds_short(rally.get('end_time')))}</td>"
            f"<td>{int(_float(rally.get('hit_count')))}</td>"
            f'<td><span class="badge bg-green-lt">{escape(_fmt_percent(rally.get("reliability")))}</span></td>'
            "</tr>"
        )
    if not rows:
        return '<tr><td colspan="5" class="text-secondary">暂无回合数据。</td></tr>'
    return "".join(rows)


def _render_temp_event_rows(value: object) -> str:
    rows = []
    for item in _sequence(value):
        event = _mapping(item)
        rows.append(
            '<div class="list-group-item">'
            '<div class="row align-items-center">'
            '<div class="col">'
            f'<div class="font-weight-medium">{escape(str(event.get("type") or "击球事件"))}</div>'
            f'<div class="text-secondary">{escape(_fmt_seconds_short(event.get("time")))} · {escape(str(event.get("description") or "WFBARNet 自动识别事件"))}</div>'
            "</div>"
            '<div class="col-auto">'
            f'<span class="badge bg-blue-lt">{escape(_fmt_percent(event.get("confidence")))}</span>'
            "</div>"
            "</div>"
            "</div>"
        )
    if not rows:
        return '<div class="list-group-item text-secondary">暂无事件数据。</div>'
    return "".join(rows)


def _bar_html(ratio: float, *, color: str = "") -> str:
    """Render an inline bar-track block from a 0..1 ratio."""
    width = max(0.0, min(1.0, float(ratio))) * 100.0
    style = f"width: {width:.1f}%"
    if color:
        style += f"; background: {color}"
    return f'<div class="bar-fill" style="{style}"></div>'


def _player_label(player: Mapping[str, Any], fallback: str) -> str:
    return str(player.get("label") or fallback)


def _player_avg_speed(player: Mapping[str, Any]) -> float:
    return _float(player.get("avg_speed_mps"))


def _speed_ratio(player: Mapping[str, Any]) -> float:
    """Map average speed onto a 0..1 bar, saturating at a fast 4 m/s."""
    return _clamp(_player_avg_speed(player) / 4.0)


def _fmt_speed(value: object) -> str:
    return f"{_float(value):.2f} m/s"


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    if value != value:  # NaN
        return lower
    return max(lower, min(upper, value))


def _render_highlights(report_data: Mapping[str, Any]) -> str:
    """Render 优势/风险/训练 highlight rows derived from real data."""
    summary = _mapping(report_data.get("summary"))
    improvements = [_mapping(item) for item in _sequence(report_data.get("improvements"))]
    dimensions = [_mapping(item) for item in _sequence(report_data.get("dimensions"))]
    reliability = list(_sequence(report_data.get("reliability")))

    scored = [item for item in dimensions if _float(item.get("score")) > 0]
    best = max(scored, key=lambda item: _float(item.get("score")), default=None)
    worst = min(scored, key=lambda item: _float(item.get("score")), default=None)

    rows: list[tuple[str, str, str]] = []
    if best is not None:
        rows.append(
            (
                "优势",
                "bg-green-lt text-green",
                f"{str(best.get('label') or '能力项')} {_float(best.get('score')):.0f} 分，为本次表现最好的维度。",
            )
        )
    low_reliability = [
        str(_mapping(item).get("label") or "")
        for item in reliability
        if 0.0 < _float(_mapping(item).get("value")) < 0.8
    ]
    if worst is not None:
        detail = f"{str(worst.get('label') or '能力项')} {_float(worst.get('score')):.0f} 分"
        if low_reliability:
            detail += f"，且 {('、'.join(low_reliability[:2]))} 偏低，相关结论建议人工复核"
        rows.append(("风险", "bg-yellow-lt text-yellow", detail + "。"))
    elif low_reliability:
        rows.append(("风险", "bg-yellow-lt text-yellow", f"{'、'.join(low_reliability[:2])} 偏低，相关结论建议人工复核。"))

    training = [_mapping(item) for item in _sequence(report_data.get("training_plan"))]
    if training:
        titles = "、".join(str(item.get("title") or "") for item in training[:2] if item.get("title"))
        if titles:
            rows.append(("训练", "bg-blue-lt text-blue", f"建议优先安排：{titles}。"))
    elif improvements:
        titles = "、".join(str(item.get("title") or "") for item in improvements[:2] if item.get("title"))
        if titles:
            rows.append(("训练", "bg-blue-lt text-blue", f"建议优先改进：{titles}。"))

    if not rows:
        level_note = str(summary.get("level_note") or "").strip()
        if level_note:
            rows.append(("结论", "bg-blue-lt text-blue", level_note))
        else:
            return '<div class="text-secondary">数据不足，无法生成分项结论。</div>'

    blocks = [
        '<div class="d-flex align-items-start gap-3 mb-3">'
        f'<span class="badge {badge}">{escape(str(label))}</span>'
        f"<div>{escape(detail)}</div>"
        "</div>"
        for label, badge, detail in rows
    ]
    return "".join(blocks)


# Tabler court-svg viewBox is 0 0 244 536; map report heat points into it.
_COURT_VIEWBOX_WIDTH = 244.0
_COURT_VIEWBOX_HEIGHT = 536.0
_COURT_ASPECT = 1340.0 / 610.0


def _render_court_heat(value: object) -> str:
    points = [_mapping(item) for item in _sequence(value)]
    rendered: list[str] = []
    for item in points:
        x = _float(item.get("x"))
        y = _float(item.get("y"))
        radius = _float(item.get("radius"))
        intensity = _clamp(_float(item.get("intensity")), 0.0, 1.0)
        if radius <= 0:
            continue
        # Report heat points use a 610x1340 court; scale into the 244x536 viewBox.
        cx = _clamp(x / 610.0, 0.0, 1.0) * _COURT_VIEWBOX_WIDTH
        cy = _clamp(y / 1340.0, 0.0, 1.0) * _COURT_VIEWBOX_HEIGHT
        r = _clamp(radius / 610.0) * _COURT_VIEWBOX_WIDTH
        opacity = 0.18 + intensity * 0.5
        rendered.append(
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="#ffdd57" fill-opacity="{opacity:.2f}">'
            f"<title>{escape(str(item.get('label') or '热区'))}</title>"
            "</circle>"
        )
    return "".join(rendered)


def _render_stroke_rows(value: object) -> str:
    strokes = [_mapping(item) for item in _sequence(value)]
    total = sum(_float(item.get("value")) for item in strokes)
    if not strokes or total <= 0:
        return '<div class="text-secondary">数据不足，无法判断击球类型分布。</div>'
    rows = []
    for item in strokes:
        count = _float(item.get("value"))
        ratio = count / total
        rows.append(
            '<div class="mb-3">'
            '<div class="d-flex justify-content-between mb-1">'
            f"<span>{escape(str(item.get('name') or '未知类型'))}</span>"
            f"<span>{escape(f'{int(count)} 次 · {ratio * 100:.0f}%')}</span>"
            "</div>"
            f'<div class="bar-track">{_bar_html(ratio)}</div>'
            "</div>"
        )
    return "".join(rows)


def _render_event_timeline(value: object) -> str:
    events = [_mapping(item) for item in _sequence(value)]
    if not events:
        return '<div class="text-secondary">数据不足，无法判断关键事件。</div>'
    items = []
    for event in events:
        time_text = str(event.get("time") or "0.00s")
        stroke = str(event.get("stroke") or "击球")
        player = str(event.get("player_label") or "")
        zone = str(event.get("zone_label") or "")
        confidence = _float(event.get("confidence"))
        headline = escape(" · ".join(part for part in (time_text, player, zone, stroke) if part))
        note_parts = []
        if confidence > 0:
            note_parts.append(f"识别置信度 {confidence * 100:.0f}%")
        if event.get("passive"):
            note_parts.append("被动球")
        note = escape("，".join(note_parts)) if note_parts else "由轨迹事件与姿态模型联合判定"
        margin = "" if len(items) == len(events) - 1 else " mb-3"
        items.append(
            f'<div class="timeline-item{margin}">'
            f'<div class="fw-bold">{headline}</div>'
            f'<div class="text-secondary">{note}</div>'
            "</div>"
        )
    return "".join(items)


_METRIC_NOTES = {
    "ball_visible_rate": ("TrackNet V3", "球点可见率", "短时丢失由滤波补偿，长丢失片段应标为低可信。"),
    "pose_valid_rate": ("Pose", "姿态有效帧", "远端球员丢失会影响移动距离和动作识别覆盖。"),
    "court_valid_rate": ("Court", "场地映射", "四角点标定后用于热区和距离换算。"),
    "avg_ball_confidence": ("BST", "动作识别置信度", "模型输出需结合姿态质量和关键点格式一致性解释。"),
}


def _render_metric_rows(report_data: Mapping[str, Any], reliability: list[Any]) -> str:
    summary = _mapping(report_data.get("summary"))
    rows: list[str] = []
    for item in reliability:
        metric = _mapping(item)
        label = str(metric.get("label") or "")
        value = _float(metric.get("value"))
        module, name, note = _metric_note_for(label)
        note = str(metric.get("note") or "").strip() or note
        level = "不足" if value < 0.5 else ("偏低" if value < 0.8 else "良好")
        rows.append(
            "<tr>"
            f"<td>{escape(module)}</td>"
            f"<td>{escape(name)}</td>"
            f"<td>{escape(level)}</td>"
            f"<td>{escape(note)}</td>"
            "</tr>"
        )

    raw_reliability = _mapping(summary.get("data_reliability"))
    for key, (module, name, note) in _METRIC_NOTES.items():
        if key in raw_reliability:
            continue
        rows.append(
            "<tr>"
            f"<td>{escape(module)}</td>"
            f"<td>{escape(name)}</td>"
            '<td class="text-secondary">缺失</td>'
            f"<td>{escape(note)}</td>"
            "</tr>"
        )

    if not rows:
        return '<tr><td colspan="4" class="text-secondary">数据不足，无法判断指标明细。</td></tr>'
    return "".join(rows)


def _metric_note_for(label: str) -> tuple[str, str, str]:
    normalized = label.lower()
    for key, (module, name, note) in _METRIC_NOTES.items():
        if key.lower() in normalized or normalized in key.lower():
            return module, name, note
    return "Other", label or "未命名指标", "由报告数据管道输出。"


def _render_training_rows(value: object) -> str:
    items = [_mapping(item) for item in _sequence(value)]
    if not items:
        return '<div class="text-secondary">数据不足，无法生成训练建议。</div>'
    cards = []
    for item in items[:6]:
        title = str(item.get("title") or "训练建议")
        content = str(item.get("content") or "")
        meta = " · ".join(
            str(item.get(key) or "") for key in ("frequency", "target") if item.get(key)
        )
        meta_html = f'<div class="text-secondary mt-1">{escape(meta)}</div>' if meta else ""
        cards.append(
            '<div class="col-md-4">'
            f'<div class="fw-bold mb-1">{escape(title)}</div>'
            f'<div class="text-secondary">{escape(content)}</div>'
            f"{meta_html}"
            "</div>"
        )
    return "".join(cards)


def _court_status_text(report_data: Mapping[str, Any]) -> str:
    summary = _mapping(_mapping(report_data.get("summary")))
    reliability = _mapping(summary.get("data_reliability"))
    rate = _float(reliability.get("court_valid_rate"))
    if rate <= 0:
        return "未标定"
    backend = str(summary.get("court_backend") or "").strip()
    backend_text = {
        "opencv": "自动(OpenCV)",
        "courtkeynet": "自动(CourtKeyNet)",
        "monotrack": "自动(MonoTrack)",
        "shuttlecourt": "自动(Seg)",
        "manual": "手动",
    }.get(backend.lower(), backend or "自动")
    return f"{backend_text} {rate * 100:.0f}%"


def _bst_coverage_text(report_data: Mapping[str, Any]) -> str:
    strokes = _sequence(report_data.get("stroke_distribution"))
    total = sum(_float(_mapping(item).get("value")) for item in strokes)
    if total <= 0:
        return "数据不足"
    return f"{int(total)} 次"


def _metric_number(metrics: list[Any], keyword: str) -> float:
    value = _metric_value(metrics, keyword, fallback="0")
    return _number_from_text(value)


def _number_from_text(value: object) -> float:
    text = str(value)
    minute_match = re.search(r"(-?\d+(?:\.\d+)?)\s*分", text)
    second_match = re.search(r"(-?\d+(?:\.\d+)?)\s*秒", text)
    if minute_match and second_match:
        return _float(minute_match.group(1)) * 60.0 + _float(second_match.group(1))
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return _float(match.group(0)) if match else 0.0


def _event_seconds(value: object) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    seconds_match = re.search(r"(-?\d+(?:\.\d+)?)\s*s$", text, re.IGNORECASE)
    if seconds_match:
        return _float(seconds_match.group(1))
    parts = text.split(":")
    try:
        if len(parts) == 2:
            return float(parts[0]) * 60.0 + float(parts[1])
        if len(parts) == 3:
            return float(parts[0]) * 3600.0 + float(parts[1]) * 60.0 + float(parts[2])
        return float(text)
    except ValueError:
        return 0.0


def _fmt_seconds_short(value: object) -> str:
    return f"{_float(value):.2f}秒"


def _replace_report_field(template: str, field: str, value: str) -> str:
    pattern = re.compile(
        rf'(<(?P<tag>[a-zA-Z0-9]+)(?P<attrs>[^>]*\bdata-report-field="{re.escape(field)}"[^>]*)>)(?P<body>.*?)(</(?P=tag)>)',
        re.DOTALL,
    )
    return pattern.sub(lambda match: f"{match.group(1)}{value}{match.group(5)}", template, count=1)


def _sequence(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _metric_value(metrics: list[Any], keyword: str, *, fallback: str) -> str:
    for item in metrics:
        metric = _mapping(item)
        label = str(metric.get("label", ""))
        if keyword in label:
            return escape(str(metric.get("value", fallback)))
    return escape(str(fallback))


def _zone_percentages(items: list[Any]) -> dict[str, float]:
    values: dict[str, float] = {}
    total = 0.0
    for item in items:
        zone = _mapping(item)
        key = str(zone.get("key", ""))
        value = _float(zone.get("value"))
        if key:
            values[key] = value
            total += value
    if total <= 0:
        return {"front": 0.0, "mid": 0.0, "back": 0.0}
    return {key: value / total for key, value in values.items()}


def _summary_html(summary: Mapping[str, Any]) -> str:
    takeaways = _sequence(summary.get("takeaways"))
    if not takeaways:
        return escape(str(summary.get("level_note") or "暂无自动复盘摘要。"))
    return "<br>".join(escape(str(item)) for item in takeaways[:3])


def _find_reliability(items: list[Any], keywords: tuple[str, ...]) -> float:
    for item in items:
        metric = _mapping(item)
        label = str(metric.get("label", "")).lower()
        if any(keyword.lower() in label for keyword in keywords):
            return _float(metric.get("value"))
    return _avg_reliability(items)


def _avg_reliability(items: list[Any]) -> float:
    values = [_float(_mapping(item).get("value")) for item in items]
    values = [value for value in values if value > 0]
    if not values:
        return 0.0
    return sum(values) / len(values)


def _fmt_percent(value: object) -> str:
    return f"{_float(value) * 100:.0f}%"


def _fmt_meters(value: object) -> str:
    return f"{_float(value):.1f}米"


def _fmt_number(value: object) -> str:
    number = _float(value)
    return str(int(number)) if number.is_integer() else f"{number:.1f}"


def _float(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number or number in (float("inf"), float("-inf")):
        return 0.0
    return number


def _inject_report_text(report_data: Mapping[str, Any], report_text: str) -> dict[str, Any]:
    payload = dict(report_data)
    summary = dict(_mapping(payload.get("summary")))
    lines = [
        line.strip(" -\t")
        for line in str(report_text).replace("\r", "\n").split("\n")
        if line.strip(" -\t")
    ]
    if lines:
        summary["takeaways"] = lines[:5]
        summary["level_note"] = lines[0]
        # Only claim LLM authorship when there is actual LLM prose to show.
        payload["analysis_source"] = "llm"
    payload["summary"] = summary
    return payload


def _merge_api_report_data(
    local_report_data: Mapping[str, Any],
    api_report_data: object,
    report_text: str,
) -> dict[str, Any]:
    payload = dict(local_report_data)
    api_data = _mapping(api_report_data)
    if report_text.strip():
        payload = _inject_report_text(payload, report_text)
    if not api_data:
        return payload

    api_summary = _mapping(api_data.get("summary"))
    if api_summary:
        summary = dict(_mapping(payload.get("summary")))
        takeaways = api_summary.get("takeaways")
        if isinstance(takeaways, list) and takeaways:
            summary["takeaways"] = takeaways
        level_note = api_summary.get("level_note")
        if level_note:
            summary["level_note"] = str(level_note)
        payload["summary"] = summary

    for key in ("improvements", "training_plan", "events"):
        value = api_data.get(key)
        if isinstance(value, list) and value:
            payload[key] = value
            payload["analysis_source"] = "llm"
    return payload


def _openai_message_content(raw: Mapping[str, Any]) -> str:
    choices = raw.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ReportGenerationError("OpenAI-compatible API response missing choices.")
    first = choices[0]
    if not isinstance(first, Mapping):
        raise ReportGenerationError("OpenAI-compatible API choice must be an object.")
    message = first.get("message")
    if isinstance(message, Mapping):
        content = message.get("content")
    else:
        content = first.get("text")
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, Mapping):
                parts.append(str(item.get("text", "")))
            else:
                parts.append(str(item))
        return _strip_reasoning("\n".join(part for part in parts if part))
    return _strip_reasoning(str(content or ""))


def _json_object_from_text(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped:
        return None
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        decoded = json.loads(stripped[start : end + 1])
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None


def _is_loopback_host(host: str) -> bool:
    """Whether a hostname is a genuine reference to this machine.

    Only literal loopback addresses and the exact name ``localhost`` count.
    A *prefix* match would be a hole: ``127.0.0.1.evil.com`` is a legitimate
    remote hostname that merely starts with the loopback prefix, and letting it
    skip the proxy would be wrong. Names are therefore not resolved here — DNS
    could be spoofed, and resolving on every call is needless latency.
    """
    name = (host or "").strip().strip("[]").lower()
    if not name:
        return False
    if name in ("localhost", "0.0.0.0"):
        return True
    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        return False
    return bool(address.is_loopback or address.is_unspecified)


def _url_opener(url: str) -> request.OpenerDirector:
    """Build an opener that never proxies loopback traffic.

    A report endpoint on ``127.0.0.1``/``localhost`` is a local model server
    (Ollama, LM Studio, llama.cpp, ...). ``urllib`` honours the ambient
    ``HTTP_PROXY``/``HTTPS_PROXY`` environment variables by default, so when a
    corporate proxy, VPN client or sandbox proxy is configured the request for
    the *local* server is sent to the proxy instead and comes back as a
    confusing ``502 Bad Gateway`` even though the model server is running fine.

    Remote endpoints still go through the configured proxy, which is what an
    actual cloud API needs.
    """
    try:
        host = parse.urlsplit(url).hostname or ""
    except ValueError:
        host = ""
    if _is_loopback_host(host):
        return request.build_opener(request.ProxyHandler({}))
    return request.build_opener()


def _urllib_post_json(
    url: str,
    payload: Mapping[str, Any],
    headers: Mapping[str, str],
    timeout_s: float,
) -> Mapping[str, Any]:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    http_request = request.Request(url, data=body, headers=dict(headers), method="POST")
    opener = _url_opener(url)
    try:
        with opener.open(http_request, timeout=timeout_s) as response:
            response_body = response.read().decode("utf-8")
    except error.URLError as exc:
        raise ReportGenerationError(f"Report API request failed: {exc}") from exc
    try:
        decoded = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise ReportGenerationError("Report API response is not valid JSON.") from exc
    if not isinstance(decoded, dict):
        raise ReportGenerationError("Report API response must be a JSON object.")
    return decoded


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}
