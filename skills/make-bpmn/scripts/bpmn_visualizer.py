"""
bpmn_visualizer.py — standalone BPMN 2.0 → SVG/PNG renderer.

Цель: позволить ИИ-ассистенту (или человеку) проверять визуальную раскладку
сгенерированных BPMN-схем без открытия Sparx EA / Camunda Modeler / bpmn.io.

Как работает: парсит файл BPMN 2.0 XML, читает координаты из секции
BPMN DI (BPMNDiagram → BPMNPlane → BPMNShape/BPMNEdge), отрисовывает фигуры
(пулы, дорожки, события, шлюзы, задачи, call activities, аннотации) и
стрелки (sequence flows и associations) в SVG. Если установлен cairosvg —
дополнительно конвертирует SVG в PNG.

Поддерживает:
  • collaboration / participant / lane (горизонтальные дорожки)
  • startEvent, endEvent, intermediate*Event
  • exclusiveGateway, parallelGateway, inclusiveGateway
  • task, userTask, serviceTask, manualTask, scriptTask, businessRuleTask
  • callActivity, subProcess (со значком «+»)
  • textAnnotation + association (пунктирная связь)
  • sequenceFlow с метками («Да», «Нет» и т. п.)
  • messageFlow (пунктир со стрелкой)
  • Метки событий/шлюзов располагаются по координатам из BPMNLabel,
    если они есть в DI; иначе — снизу по умолчанию.

Использование (CLI):
    python bpmn_visualizer.py file.bpmn                 # → file.svg
    python bpmn_visualizer.py file.bpmn -o out.svg
    python bpmn_visualizer.py file.bpmn --png           # → file.svg + file.png
    python bpmn_visualizer.py *.bpmn --png              # пакетно

Использование (как библиотека):
    from bpmn_visualizer import render_svg, render_png
    render_svg('flow.bpmn', 'flow.svg')
    render_png('flow.bpmn', 'flow.png', width=2000)              # удалит временный SVG
    render_png('flow.bpmn', 'flow.png', keep_svg=True)           # сохранит flow.svg
    render_png('flow.bpmn', 'flow.png', keep_svg='other.svg')    # SVG в указанный путь

Для PNG: pip install cairosvg

Файл одностраничный, без внешних зависимостей кроме stdlib (и cairosvg для PNG).
Лицензия: MIT-style — можешь свободно копировать, менять, делиться.
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

NS = {
    'bpmn': 'http://www.omg.org/spec/BPMN/20100524/MODEL',
    'bpmndi': 'http://www.omg.org/spec/BPMN/20100524/DI',
    'dc': 'http://www.omg.org/spec/DD/20100524/DC',
    'di': 'http://www.omg.org/spec/DD/20100524/DI',
}

# Approximate font metrics for an 11px sans-serif font
CHAR_W = 6.2
LINE_H = 13


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _local(tag: str) -> str:
    """Strip XML namespace from a tag name."""
    return tag.split('}')[-1] if '}' in tag else tag


def _wrap(text: str, max_chars: int) -> list[str]:
    """Word-wrap text into lines up to max_chars wide; preserves explicit \\n."""
    if not text:
        return []
    out: list[str] = []
    for paragraph in text.split('\n'):
        words = paragraph.split()
        if not words:
            out.append('')
            continue
        cur: list[str] = []
        cur_len = 0
        for w in words:
            need = (1 if cur else 0) + len(w)
            if cur and cur_len + need > max_chars:
                out.append(' '.join(cur))
                cur = [w]
                cur_len = len(w)
            else:
                cur.append(w)
                cur_len += need
        if cur:
            out.append(' '.join(cur))
    return out


def _draw_outside_label(out, shape, name, cx, shape_y, shape_h, max_w):
    """Render an event/gateway label outside the shape.
    If BPMNLabel/dc:Bounds is provided in DI, honour those coords (so above /
    below placement specified by the model is preserved); otherwise default to
    centred-below-the-shape."""
    if not name:
        return
    lb = shape.find('bpmndi:BPMNLabel/dc:Bounds', NS)
    if lb is not None:
        lx = float(lb.get('x'))
        ly = float(lb.get('y'))
        lw = float(lb.get('width'))
        text_x = lx + lw / 2
        text_top = ly
    else:
        text_x = cx
        text_top = shape_y + shape_h + 4
    max_chars = max(10, int(max_w / CHAR_W))
    for i, line in enumerate(_wrap(name, max_chars)):
        out.append(
            f'<text x="{text_x}" y="{text_top + 12 + i * LINE_H}" '
            f'text-anchor="middle">{escape(line)}</text>'
        )


def _draw_label_inside(out, name, cx, cy, max_w, max_h):
    """Render a task/subprocess label centred inside the shape, truncating
    to the box when there are too many wrapped lines."""
    if not name:
        return
    max_chars = max(8, int(max_w / CHAR_W))
    lines = _wrap(name, max_chars)
    fits = max(1, int(max_h / LINE_H))
    if len(lines) > fits:
        lines = lines[:fits]
        if lines:
            lines[-1] = lines[-1][:max_chars - 1] + '…'
    total_h = len(lines) * LINE_H
    start_y = cy - total_h / 2 + LINE_H * 0.7
    for i, line in enumerate(lines):
        out.append(
            f'<text x="{cx}" y="{start_y + i * LINE_H}" '
            f'text-anchor="middle">{escape(line)}</text>'
        )


# ---------------------------------------------------------------------------
# Main renderer
# ---------------------------------------------------------------------------

# Tag classifications
_EVENT_TAGS_START = {'startEvent'}
_EVENT_TAGS_END = {'endEvent'}
_EVENT_TAGS_INTERMEDIATE = {
    'intermediateCatchEvent', 'intermediateThrowEvent',
    'boundaryEvent',
}
_GATEWAY_TAGS = {
    'exclusiveGateway', 'parallelGateway', 'inclusiveGateway',
    'eventBasedGateway', 'complexGateway',
}
_TASK_TAGS = {
    'task', 'userTask', 'serviceTask', 'manualTask', 'scriptTask',
    'businessRuleTask', 'sendTask', 'receiveTask',
}
_SUBPROCESS_TAGS = {'callActivity', 'subProcess', 'transaction', 'adHocSubProcess'}


def render_svg(bpmn_path: str, svg_path: str) -> None:
    """Read a BPMN 2.0 XML file and write an SVG visualization."""
    tree = ET.parse(bpmn_path)
    root = tree.getroot()

    # Index every element with an id for fast lookup
    elements: dict[str, ET.Element] = {}
    for el in root.iter():
        if el.get('id'):
            elements[el.get('id')] = el

    plane = root.find('.//bpmndi:BPMNPlane', NS)
    if plane is None:
        raise RuntimeError(f'No BPMNPlane found in {bpmn_path}')

    # ---- Compute SVG bounding box from all DI bounds + waypoints + labels ----
    xs: list[float] = []
    ys: list[float] = []

    for shape in plane.findall('bpmndi:BPMNShape', NS):
        b = shape.find('dc:Bounds', NS)
        if b is not None:
            x = float(b.get('x'))
            y = float(b.get('y'))
            w = float(b.get('width'))
            h = float(b.get('height'))
            xs += [x, x + w]
            ys += [y, y + h]
        lb = shape.find('bpmndi:BPMNLabel/dc:Bounds', NS)
        if lb is not None:
            xs += [float(lb.get('x')), float(lb.get('x')) + float(lb.get('width'))]
            ys += [float(lb.get('y')), float(lb.get('y')) + float(lb.get('height'))]

    for edge in plane.findall('bpmndi:BPMNEdge', NS):
        for wp in edge.findall('di:waypoint', NS):
            xs.append(float(wp.get('x')))
            ys.append(float(wp.get('y')))
        lb = edge.find('bpmndi:BPMNLabel/dc:Bounds', NS)
        if lb is not None:
            xs += [float(lb.get('x')), float(lb.get('x')) + float(lb.get('width'))]
            ys += [float(lb.get('y')), float(lb.get('y')) + float(lb.get('height'))]

    if not xs or not ys:
        raise RuntimeError(f'No geometry found in {bpmn_path}')

    pad = 20
    minx, miny = min(xs) - pad, min(ys) - pad
    maxx, maxy = max(xs) + pad, max(ys) + pad
    width, height = maxx - minx, maxy - miny

    out: list[str] = []
    out.append('<?xml version="1.0" encoding="UTF-8"?>')
    out.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="{minx} {miny} {width} {height}" '
        'font-family="Arial, sans-serif" font-size="11px">'
    )
    out.append('<defs>')
    out.append(
        '  <marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="9" markerHeight="9" orient="auto-start-reverse">'
        '<path d="M 0 0 L 10 5 L 0 10 z" fill="black"/></marker>'
    )
    out.append('</defs>')
    out.append(
        f'<rect x="{minx}" y="{miny}" width="{width}" height="{height}" fill="white"/>'
    )

    # Draw pools/lanes first (background), then nodes, then edges
    order = {'participant': 0, 'lane': 1}

    def shape_order(shape: ET.Element) -> int:
        eid = shape.get('bpmnElement')
        el = elements.get(eid)
        return order.get(_local(el.tag) if el is not None else '', 2)

    shapes = sorted(plane.findall('bpmndi:BPMNShape', NS), key=shape_order)

    for shape in shapes:
        b = shape.find('dc:Bounds', NS)
        if b is None:
            continue
        x = float(b.get('x'))
        y = float(b.get('y'))
        w = float(b.get('width'))
        h = float(b.get('height'))
        cx, cy = x + w / 2, y + h / 2

        eid = shape.get('bpmnElement')
        elem = elements.get(eid)
        if elem is None:
            continue
        tag = _local(elem.tag)
        name = elem.get('name', '') or ''
        if tag == 'textAnnotation':
            text_el = elem.find('bpmn:text', NS)
            if text_el is not None and text_el.text:
                name = text_el.text

        is_horizontal = (shape.get('isHorizontal') or 'true').lower() == 'true'

        # ---- Pool (participant) ----
        if tag == 'participant':
            out.append(
                f'<rect x="{x}" y="{y}" width="{w}" height="{h}" '
                'fill="#fbfbfb" stroke="black" stroke-width="2"/>'
            )
            if is_horizontal:
                lx, ly = x + 14, y + h / 2
                out.append(
                    f'<text x="{lx}" y="{ly}" transform="rotate(-90 {lx} {ly})" '
                    f'text-anchor="middle" font-weight="bold">{escape(name)}</text>'
                )
            else:
                out.append(
                    f'<text x="{x + w / 2}" y="{y + 18}" '
                    f'text-anchor="middle" font-weight="bold">{escape(name)}</text>'
                )

        # ---- Lane ----
        elif tag == 'lane':
            out.append(
                f'<rect x="{x}" y="{y}" width="{w}" height="{h}" '
                'fill="white" stroke="black" stroke-width="1"/>'
            )
            if is_horizontal:
                lx, ly = x + 16, y + h / 2
                out.append(
                    f'<text x="{lx}" y="{ly}" transform="rotate(-90 {lx} {ly})" '
                    f'text-anchor="middle">{escape(name)}</text>'
                )
            else:
                out.append(
                    f'<text x="{x + w / 2}" y="{y + 18}" '
                    f'text-anchor="middle">{escape(name)}</text>'
                )

        # ---- Events ----
        elif tag in _EVENT_TAGS_START:
            out.append(
                f'<circle cx="{cx}" cy="{cy}" r="{w / 2}" fill="white" '
                'stroke="black" stroke-width="1.2"/>'
            )
            _draw_outside_label(out, shape, name, cx, y, h, w + 60)
        elif tag in _EVENT_TAGS_INTERMEDIATE:
            out.append(
                f'<circle cx="{cx}" cy="{cy}" r="{w / 2}" fill="white" '
                'stroke="black" stroke-width="1.2"/>'
            )
            out.append(
                f'<circle cx="{cx}" cy="{cy}" r="{w / 2 - 3}" '
                'fill="none" stroke="black" stroke-width="1"/>'
            )
            _draw_outside_label(out, shape, name, cx, y, h, w + 60)
        elif tag in _EVENT_TAGS_END:
            out.append(
                f'<circle cx="{cx}" cy="{cy}" r="{w / 2}" fill="white" '
                'stroke="black" stroke-width="3"/>'
            )
            _draw_outside_label(out, shape, name, cx, y, h, w + 60)

        # ---- Gateways ----
        elif tag in _GATEWAY_TAGS:
            out.append(
                f'<polygon points="{cx},{y} {x + w},{cy} {cx},{y + h} {x},{cy}" '
                'fill="white" stroke="black" stroke-width="1.5"/>'
            )
            if tag == 'exclusiveGateway':
                m = w * 0.18
                out.append(
                    f'<path d="M {cx - m} {cy - m} L {cx + m} {cy + m} '
                    f'M {cx + m} {cy - m} L {cx - m} {cy + m}" '
                    'stroke="black" stroke-width="2" fill="none"/>'
                )
            elif tag == 'parallelGateway':
                m = w * 0.22
                out.append(
                    f'<path d="M {cx - m} {cy} L {cx + m} {cy} '
                    f'M {cx} {cy - m} L {cx} {cy + m}" '
                    'stroke="black" stroke-width="2.5" fill="none"/>'
                )
            elif tag == 'inclusiveGateway':
                out.append(
                    f'<circle cx="{cx}" cy="{cy}" r="{w * 0.25}" '
                    'fill="none" stroke="black" stroke-width="2"/>'
                )
            elif tag == 'eventBasedGateway':
                out.append(
                    f'<circle cx="{cx}" cy="{cy}" r="{w * 0.30}" '
                    'fill="none" stroke="black" stroke-width="1"/>'
                )
                out.append(
                    f'<circle cx="{cx}" cy="{cy}" r="{w * 0.22}" '
                    'fill="none" stroke="black" stroke-width="1"/>'
                )
            _draw_outside_label(out, shape, name, cx, y, h, w + 80)

        # ---- Tasks ----
        elif tag in _TASK_TAGS:
            out.append(
                f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" ry="8" '
                'fill="white" stroke="black" stroke-width="1"/>'
            )
            _draw_label_inside(out, name, cx, cy, w - 14, h - 6)

        # ---- Subprocesses / call activities ----
        elif tag in _SUBPROCESS_TAGS:
            stroke_w = 3 if tag == 'callActivity' else 1.5
            out.append(
                f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" ry="8" '
                f'fill="white" stroke="black" stroke-width="{stroke_w}"/>'
            )
            is_expanded = (shape.get('isExpanded') or 'false').lower() == 'true'
            if not is_expanded:
                mx, my = cx, y + h - 11
                out.append(
                    f'<rect x="{mx - 7}" y="{my - 7}" width="14" height="14" '
                    'fill="white" stroke="black" stroke-width="1"/>'
                )
                out.append(
                    f'<path d="M {mx - 4} {my} L {mx + 4} {my} '
                    f'M {mx} {my - 4} L {mx} {my + 4}" '
                    'stroke="black" stroke-width="1.5"/>'
                )
                _draw_label_inside(out, name, cx, cy - 7, w - 14, h - 22)
            else:
                _draw_label_inside(out, name, cx, y + 14, w - 14, 14)

        # ---- Text annotations ----
        elif tag == 'textAnnotation':
            out.append(
                f'<rect x="{x}" y="{y}" width="{w}" height="{h}" '
                'fill="#fffce5" stroke="none"/>'
            )
            out.append(
                f'<path d="M {x + 10} {y} L {x} {y} L {x} {y + h} L {x + 10} {y + h}" '
                'stroke="black" stroke-width="1" fill="none"/>'
            )
            max_chars = max(8, int((w - 16) / CHAR_W))
            for i, line in enumerate(_wrap(name, max_chars)):
                out.append(
                    f'<text x="{x + 8}" y="{y + 14 + i * LINE_H}">'
                    f'{escape(line)}</text>'
                )

        # ---- Data objects, data stores: simple placeholder ----
        elif tag in ('dataObject', 'dataObjectReference', 'dataStoreReference'):
            out.append(
                f'<rect x="{x}" y="{y}" width="{w}" height="{h}" '
                'fill="white" stroke="black" stroke-width="1"/>'
            )
            _draw_label_inside(out, name, cx, cy, w - 8, h - 6)

        # ---- Unknown — visible warning rectangle ----
        else:
            out.append(
                f'<rect x="{x}" y="{y}" width="{w}" height="{h}" '
                'fill="#fee" stroke="red" stroke-width="1"/>'
            )
            _draw_label_inside(out, f'?{tag}: {name}', cx, cy, w - 6, h - 4)

    # ---- Edges ----
    for edge in plane.findall('bpmndi:BPMNEdge', NS):
        eid = edge.get('bpmnElement')
        elem = elements.get(eid)
        tag = _local(elem.tag) if elem is not None else 'sequenceFlow'

        wps = [
            (float(wp.get('x')), float(wp.get('y')))
            for wp in edge.findall('di:waypoint', NS)
        ]
        if not wps:
            continue
        pts = ' '.join(f'{x},{y}' for x, y in wps)

        if tag == 'association':
            out.append(
                f'<polyline points="{pts}" fill="none" stroke="black" '
                'stroke-width="1" stroke-dasharray="5,3"/>'
            )
        elif tag == 'messageFlow':
            out.append(
                f'<polyline points="{pts}" fill="none" stroke="black" '
                'stroke-width="1" stroke-dasharray="6,4" marker-end="url(#arr)"/>'
            )
        elif tag == 'dataAssociation' or tag.endswith('DataAssociation'):
            out.append(
                f'<polyline points="{pts}" fill="none" stroke="black" '
                'stroke-width="1" stroke-dasharray="3,3" marker-end="url(#arr)"/>'
            )
        else:
            # sequenceFlow and any other directed flow
            out.append(
                f'<polyline points="{pts}" fill="none" stroke="black" '
                'stroke-width="1.3" marker-end="url(#arr)"/>'
            )

        # Edge label, if present
        if elem is not None and elem.get('name'):
            lb = edge.find('bpmndi:BPMNLabel/dc:Bounds', NS)
            if lb is not None:
                lx = float(lb.get('x'))
                ly = float(lb.get('y'))
                lw = float(lb.get('width'))
                lh = float(lb.get('height'))
                # White rectangle behind text so it stays readable over arrows
                out.append(
                    f'<rect x="{lx - 1}" y="{ly - 1}" width="{lw + 2}" '
                    f'height="{lh + 2}" fill="white" stroke="none"/>'
                )
                # Multi-line label support
                lines = elem.get('name').split('\n')
                tx = lx + lw / 2
                line_count = len(lines)
                start_y = ly + lh - (line_count - 1) * LINE_H - 2
                for i, line in enumerate(lines):
                    out.append(
                        f'<text x="{tx}" y="{start_y + i * LINE_H}" '
                        f'text-anchor="middle">{escape(line)}</text>'
                    )

    out.append('</svg>')

    with open(svg_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(out))


def render_png(bpmn_path: str, png_path: str, width: int = 2000,
               keep_svg: bool | str = False) -> None:
    """Render BPMN → PNG via SVG (requires `cairosvg`).

    keep_svg:
      • False  — intermediate SVG is written to a tempfile and deleted.
      • True   — keep the intermediate SVG next to the PNG (same basename).
      • str    — path where the SVG should be written.
    """
    try:
        import cairosvg  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            'PNG rendering requires the cairosvg package. '
            'Install with: pip install cairosvg'
        ) from exc

    import tempfile
    if keep_svg is False:
        fd, svg_path = tempfile.mkstemp(suffix='.svg')
        os.close(fd)
        delete_after = True
    elif keep_svg is True:
        svg_path = png_path.rsplit('.', 1)[0] + '.svg'
        delete_after = False
    else:
        svg_path = keep_svg
        delete_after = False

    try:
        render_svg(bpmn_path, svg_path)
        cairosvg.svg2png(url=svg_path, write_to=png_path, output_width=width)
    finally:
        if delete_after:
            try:
                os.remove(svg_path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _expand_inputs(inputs: list[str]) -> list[str]:
    """Expand glob patterns and verify files exist."""
    expanded: list[str] = []
    for pat in inputs:
        matches = glob.glob(pat)
        if matches:
            expanded.extend(matches)
        elif os.path.exists(pat):
            expanded.append(pat)
        else:
            print(f'WARN: no match for {pat!r}', file=sys.stderr)
    return expanded


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Render BPMN 2.0 XML files to SVG (and optionally PNG).',
    )
    parser.add_argument('inputs', nargs='+',
                        help='BPMN files (globs allowed, e.g. *.bpmn)')
    parser.add_argument('-o', '--output',
                        help='Output path (only valid with one input file).')
    parser.add_argument('--png', action='store_true',
                        help='Also produce a PNG (requires cairosvg).')
    parser.add_argument('--png-only', action='store_true',
                        help='Produce only PNG (delete the intermediate SVG).')
    parser.add_argument('--width', type=int, default=2000,
                        help='PNG output width in pixels (default: 2000).')

    args = parser.parse_args()
    files = _expand_inputs(args.inputs)
    if not files:
        print('No input files found.', file=sys.stderr)
        return 1
    if args.output and len(files) > 1:
        print('--output can only be used with a single input file.',
              file=sys.stderr)
        return 1

    errors = 0
    for bpmn_path in files:
        base = os.path.splitext(bpmn_path)[0]
        svg_path = args.output or f'{base}.svg'
        png_path = (svg_path.rsplit('.', 1)[0] + '.png'
                    if args.output and args.png else f'{base}.png')
        try:
            if args.png_only:
                render_png(bpmn_path, png_path, width=args.width, keep_svg=False)
                print(f'  {bpmn_path} → {png_path}')
            else:
                render_svg(bpmn_path, svg_path)
                if args.png:
                    # Pass the SVG path explicitly so render_png reuses it
                    # instead of writing to a tempfile.
                    render_png(bpmn_path, png_path, width=args.width,
                               keep_svg=svg_path)
                    print(f'  {bpmn_path} → {svg_path} + {png_path}')
                else:
                    print(f'  {bpmn_path} → {svg_path}')
        except Exception as exc:  # noqa: BLE001
            print(f'  FAIL {bpmn_path}: {exc}', file=sys.stderr)
            errors += 1

    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
