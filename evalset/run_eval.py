# -*- coding: utf-8 -*-
"""105문항 API E2E 평가 실행기.

기본은 DB·KG·벡터·HCX Router/Generator를 모두 사용하는 라이브 평가다.
`--offline`은 비용 없는 규칙·DB·KG 회귀 검사에 사용한다.
"""
import argparse
import collections
import json
import os
import re
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient  # noqa: E402

from server.app import build_runtime, create_app  # noqa: E402

FIVE_FIELDS = {"question_id", "question", "retrieved_context", "think_trace", "answer"}
REFUSE_HEAD = "요청하신 내용은 보유 데이터 기준으로 확인할 수 없습니다."
DEGRADED_MARKERS = ("오류 ", "폴백", "강등", "생략", "전역 오류")


def percentile(values, pct):
    """선형 보간 percentile — 외부 수치 패키지 없이 결정적으로 계산."""
    if not values:
        return 0.0
    ordered = sorted(values)
    pos = (len(ordered) - 1) * pct
    lo, hi = int(pos), min(int(pos) + 1, len(ordered) - 1)
    frac = pos - lo
    return ordered[lo] * (1 - frac) + ordered[hi] * frac


def parse_behavior(trace):
    match = re.search(r"\bbehavior=(answer|partial|refuse)\b", trace)
    return match.group(1) if match else "unknown"


def parse_channels(trace):
    return sorted(set(re.findall(r"^call (sql|keyword|graph|vector)\.", trace, re.M)))


def parse_generation(trace):
    match = re.search(r"^생성: (.+)$", trace, re.M)
    return match.group(1).strip() if match else "none"


def score_case(item, response, latency):
    trace = response.get("think_trace", "")
    answer = response.get("answer", "")
    actual = parse_behavior(trace)
    expected = item["behavior"]
    expected_channels = sorted(item.get("channels", []))
    called_channels = parse_channels(trace)
    validation_used = "채널: validation" in response.get("retrieved_context", "")
    channel_errors = re.findall(r"^오류 ([^:]+): (.+)$", trace, re.M)
    contract_ok = set(response) == FIVE_FIELDS and all(
        isinstance(response.get(key), str) for key in FIVE_FIELDS)
    refusal_surface_ok = actual != "refuse" or answer.startswith(REFUSE_HEAD)
    evidence_ok = response.get("retrieved_context", "") not in ("", "(근거 없음)")
    channel_ok = all(channel in called_channels or (channel == "validation" and validation_used)
                     for channel in expected_channels)
    global_error = "전역 오류" in trace
    degraded = any(marker in trace for marker in DEGRADED_MARKERS)
    empty_answer = (not answer.strip() or
                    answer.startswith("조건에 일치하는 결과를 보유 데이터에서 확인하지 못했습니다."))
    behavior_ok = actual == expected
    strict = all((behavior_ok, refusal_surface_ok, contract_ok,
                  evidence_ok, channel_ok, not global_error))
    return {
        **item,
        "actual_behavior": actual,
        "behavior_ok": behavior_ok,
        "refusal_surface_ok": refusal_surface_ok,
        "contract_ok": contract_ok,
        "evidence_ok": evidence_ok,
        "expected_channels": expected_channels,
        "called_channels": called_channels,
        "channel_ok": channel_ok,
        "channel_errors": [{"call": call, "error": error} for call, error in channel_errors],
        "global_error": global_error,
        "degraded": degraded,
        "empty_answer": empty_answer,
        "generation": parse_generation(trace),
        "latency_seconds": round(latency, 6),
        "response": response,
        "strict_proxy": strict,
    }


def count_true(rows, field):
    return sum(bool(row[field]) for row in rows)


def build_summary(rows, runtime_seconds, health, mode):
    latencies = [row["latency_seconds"] for row in rows]
    by_level = {}
    for level in ("하", "중", "상", "트랩"):
        selected = [row for row in rows if row["level"] == level]
        by_level[level] = {
            "total": len(selected),
            "behavior_ok": count_true(selected, "behavior_ok"),
            "channel_ok": count_true(selected, "channel_ok"),
            "evidence_ok": count_true(selected, "evidence_ok"),
            "strict_proxy": count_true(selected, "strict_proxy"),
            "degraded": count_true(selected, "degraded"),
        }
    confusion = collections.Counter(
        f"{row['behavior']}->{row['actual_behavior']}" for row in rows)
    generation = collections.Counter(row["generation"] for row in rows)
    return {
        "mode": mode,
        "runtime_load_seconds": round(runtime_seconds, 3),
        "health": health,
        "total": len(rows),
        "behavior_ok": count_true(rows, "behavior_ok"),
        "refusal_surface_ok": count_true(rows, "refusal_surface_ok"),
        "contract_ok": count_true(rows, "contract_ok"),
        "evidence_ok": count_true(rows, "evidence_ok"),
        "channel_ok": count_true(rows, "channel_ok"),
        "strict_proxy": count_true(rows, "strict_proxy"),
        "global_errors": count_true(rows, "global_error"),
        "channel_error_cases": sum(bool(row["channel_errors"]) for row in rows),
        "degraded_cases": count_true(rows, "degraded"),
        "empty_answer_cases": count_true(rows, "empty_answer"),
        "latency": {
            "mean": round(statistics.mean(latencies), 4),
            "p50": round(percentile(latencies, 0.50), 4),
            "p95": round(percentile(latencies, 0.95), 4),
            "max": round(max(latencies), 4),
            "over_15s": sum(value > 15 for value in latencies),
        },
        "by_level": by_level,
        "behavior_confusion": dict(confusion),
        "generation": dict(generation),
        "behavior_fail_ids": [row["id"] for row in rows if not row["behavior_ok"]],
        "channel_fail_ids": [row["id"] for row in rows if not row["channel_ok"]],
        "evidence_fail_ids": [row["id"] for row in rows if not row["evidence_ok"]],
        "degraded_ids": [row["id"] for row in rows if row["degraded"]],
        "empty_answer_ids": [row["id"] for row in rows if row["empty_answer"]],
    }


def write_report(path, summary):
    before = {
        "behavior_ok": 97, "refusal_surface_ok": 105, "contract_ok": 105,
        "evidence_ok": 99, "channel_ok": 78, "strict_proxy": 70,
        "degraded_cases": 12, "empty_answer_cases": 3,
    }
    labels = {
        "behavior_ok": "행동 판정", "refusal_surface_ok": "답변 불가 표면 판정",
        "contract_ok": "응답 계약", "evidence_ok": "근거 충족",
        "channel_ok": "기대 채널", "strict_proxy": "엄격 통과",
        "degraded_cases": "저하 응답", "empty_answer_cases": "빈 일반 답변",
    }
    lines = ["# Agent Evaluation After Improvements", "", "## 전체 지표", "",
             "| 지표 | 개선 전 | 개선 후 | 변화 |", "|---|---:|---:|---:|"]
    for key, old in before.items():
        new = summary[key]
        lines.append(f"| {labels[key]} | {old} | {new} | {new - old:+d} |")
    lines += ["", "## 응답 시간", "",
              f"- 평균: {summary['latency']['mean']}초",
              f"- p50: {summary['latency']['p50']}초",
              f"- p95: {summary['latency']['p95']}초",
              f"- 최대: {summary['latency']['max']}초",
              f"- 15초 초과: {summary['latency']['over_15s']}건",
              "", "## 난이도별", "",
              "| 난이도 | 행동 | 근거 | 채널 | 엄격 통과 | 저하 | 전체 |",
              "|---|---:|---:|---:|---:|---:|---:|"]
    for level, values in summary["by_level"].items():
        lines.append(f"| {level} | {values['behavior_ok']} | {values['evidence_ok']} | "
                     f"{values['channel_ok']} | {values['strict_proxy']} | "
                     f"{values['degraded']} | {values['total']} |")
    for title, key in (("행동 실패", "behavior_fail_ids"),
                       ("근거 실패", "evidence_fail_ids"),
                       ("채널 실패", "channel_fail_ids"),
                       ("저하 응답", "degraded_ids"),
                       ("빈 답변", "empty_answer_ids")):
        ids = summary[key]
        lines += ["", f"## {title}", "", ", ".join(ids) if ids else "없음"]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--evalset", default=os.path.join(ROOT, "evalset", "evalset_v1.jsonl"))
    parser.add_argument("--output-prefix", required=True)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()

    with open(args.evalset, encoding="utf-8") as fh:
        items = [json.loads(line) for line in fh if line.strip()]

    started = time.perf_counter()
    ctx, llm_router, generator = build_runtime(
        with_vector=not args.offline,
        with_llm=not args.offline,
        with_generator=not args.offline,
    )
    runtime_seconds = time.perf_counter() - started
    app = create_app(ctx, llm_router, generator, cache_path=None)

    os.makedirs(os.path.dirname(os.path.abspath(args.output_prefix)), exist_ok=True)
    jsonl_path = args.output_prefix + ".jsonl"
    rows = []
    with TestClient(app) as client, open(jsonl_path, "w", encoding="utf-8") as out_fh:
        health = client.get("/health").json()
        for number, item in enumerate(items, 1):
            t0 = time.perf_counter()
            response = client.get("/answer", params={
                "question_id": item["id"], "question": item["question"]}).json()
            row = score_case(item, response, time.perf_counter() - t0)
            rows.append(row)
            out_fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            out_fh.flush()
            print(f"[{number:03d}/{len(items)}] {item['id']} "
                  f"behavior={row['actual_behavior']} ok={row['behavior_ok']} "
                  f"strict={row['strict_proxy']} latency={row['latency_seconds']:.2f}s",
                  flush=True)

    mode = ("API E2E offline: rule, SQL, keyword, KG, validation" if args.offline else
            "API E2E full: HCX-005 router+generator, CLOVA embedding, SQL, keyword, KG, validation")
    summary = build_summary(rows, runtime_seconds, health, mode)
    summary_path = args.output_prefix + "_summary.json"
    report_path = args.output_prefix + "_report.md"
    with open(summary_path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    write_report(report_path, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
