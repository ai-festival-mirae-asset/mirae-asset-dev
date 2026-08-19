# -*- coding: utf-8 -*-
"""
지식그래프 데이터 규칙 검사 — SHACL(ontology/shapes.ttl)로 kg/output/*.nt 를 검증한다.

무엇: build_kg.py 가 만든 인스턴스가 온톨로지의 값 규칙(위험등급 1~6, 해외ETF 위험등급
      없음, 신용등급 AAA~D, ETF/ETN 배타 …)을 지키는지 선언문(SHACL)으로 독립 재검사한다.
왜  : 같은 규칙을 build_kg.py 가 적재 전에 코드로 검사하지만(1차 방어), 검사 코드 자체가
      틀리거나 빠뜨린 규칙이 있을 수 있다. 규칙을 선언문으로 한 번 더 적어 두고 다른 엔진
      (pyshacl)으로 돌리면 두 검사가 서로를 확인한다(8/18 기술세션의 SHACL 강조에 대응).
      서버 실행에는 필요 없다 — 오프라인 검사·테스트 전용(rdflib·pyshacl).

실행:
  python kg/validate_shacl.py                          # 5개 .nt 전부 앞 20,000줄씩 표본 검사
  python kg/validate_shacl.py --tables kr_etf --limit 0    # 국내ETF 전량(0 = 제한 없음)
  python kg/validate_shacl.py --tables kr_bond --limit 100000
출력: 위반 0건이면 "규칙 준수", 아니면 규칙(메시지)별 건수와 예시 노드.
      --report 로 결과 텍스트 파일 저장 가능.

표본 방식: 파일 앞부분 N줄을 읽되 마지막 노드의 트리플이 잘리지 않게 같은 주어가 끝날
      때까지 더 읽는다(잘리면 "상품번호 없음" 같은 가짜 위반이 생긴다). build_kg 는 노드
      단위로 연속 기록하므로 이 방식이 안전하다. 전량 검사(--limit 0)는 채권 133MB 기준
      수 분 이상 걸릴 수 있다.
"""
import argparse
import io
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

ONTOLOGY_DIR = os.path.join(ROOT, "ontology")
ONTOLOGY_FILES = ("common.ttl", "bond_kr.ttl", "etf_kr.ttl", "etf_gl.ttl", "fund_pub.ttl")
SHAPES_FILE = os.path.join(ONTOLOGY_DIR, "shapes.ttl")
OUT_DEFAULT = os.path.join(HERE, "output")
SH = "http://www.w3.org/ns/shacl#"


def load_ontology(files=ONTOLOGY_FILES, ontology_dir=ONTOLOGY_DIR):
    """온톨로지 5파일 → rdflib Graph (rdfs:subClassOf 등 — 상위 클래스 대상 shape 가 잡히게)."""
    import rdflib
    g = rdflib.Graph()
    for f in files:
        g.parse(os.path.join(ontology_dir, f), format="turtle")
    return g


def load_shapes(path=SHAPES_FILE):
    import rdflib
    g = rdflib.Graph()
    g.parse(path, format="turtle")
    return g


def read_head(path, limit):
    """N-Triples 파일 앞 limit 줄(+마지막 주어의 나머지 줄) — limit 0/None 이면 전체."""
    lines = []
    with io.open(path, "r", encoding="utf-8") as fh:
        if not limit:
            return fh.read()
        last_subject = None
        for i, line in enumerate(fh):
            subject = line.split(" ", 1)[0] if line.strip() else None
            if i >= limit and subject != last_subject:
                break
            lines.append(line)
            if subject:
                last_subject = subject
    return "".join(lines)


def load_data(nt_paths, limit=20000):
    """.nt 파일들(표본) → rdflib Graph."""
    import rdflib
    g = rdflib.Graph()
    for p in nt_paths:
        g.parse(data=read_head(p, limit), format="nt")
    return g


def validate_graph(data_graph, shapes_graph=None, ont_graph=None):
    """pyshacl 실행 → (conforms, results_graph, results_text)."""
    from pyshacl import validate
    shapes_graph = shapes_graph if shapes_graph is not None else load_shapes()
    ont_graph = ont_graph if ont_graph is not None else load_ontology()
    conforms, results_graph, results_text = validate(
        data_graph, shacl_graph=shapes_graph, ont_graph=ont_graph,
        inference="none", abort_on_first=False, allow_warnings=False, meta_shacl=False)
    return conforms, results_graph, results_text


def summarize(results_graph, max_examples=3):
    """검증 결과 그래프 → [(메시지, 건수, 예시 노드들)] (건수 많은 순)."""
    import rdflib
    SHN = rdflib.Namespace(SH)
    buckets = {}
    for r in results_graph.subjects(rdflib.RDF.type, SHN.ValidationResult):
        msg = results_graph.value(r, SHN.resultMessage)
        node = results_graph.value(r, SHN.focusNode)
        path = results_graph.value(r, SHN.resultPath)
        key = str(msg) if msg is not None else f"(메시지 없음) path={path}"
        b = buckets.setdefault(key, {"count": 0, "examples": []})
        b["count"] += 1
        if len(b["examples"]) < max_examples:
            b["examples"].append(str(node))
    return sorted(((k, v["count"], v["examples"]) for k, v in buckets.items()),
                  key=lambda x: -x[1])


def validate_files(nt_paths, limit=20000):
    """편의 함수: 파일 목록 → (conforms, summary 리스트, 데이터 트리플 수)."""
    data = load_data(nt_paths, limit)
    conforms, results, _text = validate_graph(data)
    return conforms, summarize(results), len(data)


def main(argv=None):
    ap = argparse.ArgumentParser(description="SHACL 로 kg/output/*.nt 데이터 규칙 검사")
    ap.add_argument("--tables", default="kr_bond,kr_etf,global_etf,public_fund,constituents",
                    help="쉼표 구분 슬러그 (기본: 5개 전부)")
    ap.add_argument("--limit", type=int, default=20000,
                    help="파일당 표본 줄 수 (기본 20000, 0 = 전량)")
    ap.add_argument("--out", default=OUT_DEFAULT, help="kg 출력 폴더")
    ap.add_argument("--report", default=None, help="결과 텍스트 저장 경로(선택)")
    args = ap.parse_args(argv)

    paths = []
    for t in [x.strip() for x in args.tables.split(",") if x.strip()]:
        p = os.path.join(args.out, t + ".nt")
        if not os.path.exists(p):
            print(f"[{t}] 파일 없음: {p} — 먼저 python kg/build_kg.py")
            continue
        paths.append(p)
    if not paths:
        sys.exit("검사할 .nt 파일이 없다")

    t0 = time.time()
    data = load_data(paths, args.limit)
    conforms, results, _text = validate_graph(data)
    summary = summarize(results)
    elapsed = time.time() - t0

    lines = [f"검사 대상: {', '.join(os.path.basename(p) for p in paths)} "
             f"(파일당 표본 {'전량' if not args.limit else f'{args.limit:,}줄'}, 데이터 트리플 {len(data):,})",
             f"규칙 파일: ontology/shapes.ttl + 온톨로지 5파일 · 소요 {elapsed:.1f}초",
             f"결과: {'규칙 준수 (위반 0건)' if conforms else '위반 ' + str(sum(c for _, c, _ in summary)) + '건'}"]
    for msg, count, examples in summary:
        lines.append(f"  - {count:,}건 · {msg}")
        for ex in examples:
            lines.append(f"      예: {ex}")
    text = "\n".join(lines)
    print(text)
    if args.report:
        with io.open(args.report, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text + "\n")
        print(f"(저장: {args.report})")
    return 0 if conforms else 1


if __name__ == "__main__":
    sys.exit(main())
