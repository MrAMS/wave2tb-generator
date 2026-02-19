from __future__ import annotations

import ast
from pathlib import Path
import re
from typing import Iterable

from tree_sitter import Language, Node, Parser
import tree_sitter_verilog

from .models import PortSpec

_LANGUAGE = Language(tree_sitter_verilog.language())

_DEFINE_RE = re.compile(r"^\s*`define\s+([A-Za-z_][A-Za-z0-9_]*)(?:\s+(.*?))?\s*$", re.MULTILINE)
_INCLUDE_RE = re.compile(r'^\s*`include\s+["<]([^">]+)[">]\s*$')
_MACRO_REF_RE = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*)")
_IDENTIFIER_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")
_SV_BASE_LITERAL_RE = re.compile(r"(?i)(\d+)?'([sS]?)([bodh])([0-9a-fxz?_]+)")
_SV_UNSIZED_LITERAL_RE = re.compile(r"(?i)'([01xz?])")

_DIR_BY_DECL_TYPE = {
    "input_declaration": "input",
    "output_declaration": "output",
    "inout_declaration": "inout",
}

_ALLOWED_BINOPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a // b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.Mod: lambda a, b: a % b,
    ast.LShift: lambda a, b: a << b,
    ast.RShift: lambda a, b: a >> b,
    ast.BitOr: lambda a, b: a | b,
    ast.BitAnd: lambda a, b: a & b,
    ast.BitXor: lambda a, b: a ^ b,
    ast.Pow: lambda a, b: a**b,
}
_ALLOWED_UNARYOPS = {
    ast.UAdd: lambda a: +a,
    ast.USub: lambda a: -a,
    ast.Invert: lambda a: ~a,
}


def _node_text(source: bytes, node: Node) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="ignore")


def _walk(node: Node) -> Iterable[Node]:
    yield node
    for child in node.children:
        yield from _walk(child)


def _first_child(node: Node, node_type: str) -> Node | None:
    for child in node.children:
        if child.type == node_type:
            return child
    return None


def _find_children(node: Node, node_type: str) -> list[Node]:
    return [child for child in node.children if child.type == node_type]


def _find_descendants(node: Node, node_type: str) -> list[Node]:
    return [candidate for candidate in _walk(node) if candidate.type == node_type]


def _strip_underscores(text: str) -> str:
    return text.replace("_", "")


def _parse_sv_based_literal(text: str) -> int | None:
    match = _SV_BASE_LITERAL_RE.fullmatch(text)
    if not match:
        return None
    _, _, base_char, digits = match.groups()
    digits = _strip_underscores(digits)
    if any(ch in "xXzZ?" for ch in digits):
        return None
    base = {"b": 2, "o": 8, "d": 10, "h": 16}[base_char.lower()]
    return int(digits, base)


def _parse_sv_unsized_literal(text: str) -> int | None:
    match = _SV_UNSIZED_LITERAL_RE.fullmatch(text)
    if not match:
        return None
    bit = match.group(1).lower()
    if bit == "0":
        return 0
    if bit == "1":
        return 1
    return None


def _eval_ast_int(node: ast.AST) -> int | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    if isinstance(node, ast.UnaryOp):
        op = _ALLOWED_UNARYOPS.get(type(node.op))
        if op is None:
            return None
        operand = _eval_ast_int(node.operand)
        return None if operand is None else op(operand)
    if isinstance(node, ast.BinOp):
        op = _ALLOWED_BINOPS.get(type(node.op))
        if op is None:
            return None
        left = _eval_ast_int(node.left)
        right = _eval_ast_int(node.right)
        if left is None or right is None:
            return None
        try:
            return op(left, right)
        except ZeroDivisionError:
            return None
    return None


def _resolve_symbol(name: str, symbols: dict[str, int | str], seen: set[str]) -> int | None:
    if name in seen:
        return None
    raw = symbols.get(name)
    if raw is None:
        return None
    if isinstance(raw, int):
        return raw
    seen.add(name)
    resolved = _eval_const_expr(raw, symbols, seen)
    seen.remove(name)
    if resolved is not None:
        symbols[name] = resolved
    return resolved


def _eval_const_expr(expr: str, symbols: dict[str, int | str], seen: set[str] | None = None) -> int | None:
    seen = seen or set()

    def replace_macro(match: re.Match[str]) -> str:
        name = match.group(1)
        value = _resolve_symbol(name, symbols, seen)
        return str(value) if value is not None else match.group(0)

    expanded = _MACRO_REF_RE.sub(replace_macro, expr)

    def replace_based_literal(match: re.Match[str]) -> str:
        literal = match.group(0)
        value = _parse_sv_based_literal(literal)
        return str(value) if value is not None else literal

    expanded = _SV_BASE_LITERAL_RE.sub(replace_based_literal, expanded)

    def replace_unsized_literal(match: re.Match[str]) -> str:
        literal = match.group(0)
        value = _parse_sv_unsized_literal(literal)
        return str(value) if value is not None else literal

    expanded = _SV_UNSIZED_LITERAL_RE.sub(replace_unsized_literal, expanded)

    def replace_identifier(match: re.Match[str]) -> str:
        name = match.group(0)
        value = _resolve_symbol(name, symbols, seen)
        return str(value) if value is not None else name

    expanded = _IDENTIFIER_RE.sub(replace_identifier, expanded)
    if re.search(r"[A-Za-z_`]", expanded):
        return None
    if re.search(r"[^0-9\s\+\-\*\/%\(\)<>&\|\^~\.]", expanded):
        return None

    try:
        parsed = ast.parse(expanded, mode="eval")
    except SyntaxError:
        return None
    return _eval_ast_int(parsed.body)


def _dimension_width(text: str, symbols: dict[str, int | str]) -> int | None:
    stripped = text.strip()
    if not (stripped.startswith("[") and stripped.endswith("]")):
        return None
    body = stripped[1:-1].strip()
    if not body:
        return None

    if "+:" in body:
        _, width_expr = body.split("+:", 1)
        width = _eval_const_expr(width_expr.strip(), symbols)
        return width if width is not None and width > 0 else None
    if "-:" in body:
        _, width_expr = body.split("-:", 1)
        width = _eval_const_expr(width_expr.strip(), symbols)
        return width if width is not None and width > 0 else None
    if ":" in body:
        left_expr, right_expr = body.split(":", 1)
        left = _eval_const_expr(left_expr.strip(), symbols)
        right = _eval_const_expr(right_expr.strip(), symbols)
        if left is None or right is None:
            return None
        return abs(left - right) + 1
    # Single index dimensions are uncommon for packed dimensions.
    value = _eval_const_expr(body, symbols)
    return value if value is not None and value > 0 else None


def _packed_width(node: Node, source: bytes, symbols: dict[str, int | str]) -> int:
    dims = _find_descendants(node, "packed_dimension")
    if not dims:
        return 1
    width = 1
    for dim in dims:
        dim_width = _dimension_width(_node_text(source, dim), symbols)
        if dim_width is None:
            return -1
        width *= dim_width
    return width


def _extract_module_name(module_node: Node, source: bytes) -> str | None:
    header = _first_child(module_node, "module_header")
    if header is None:
        return None
    ident = _first_child(header, "simple_identifier")
    if ident is None:
        ident = _first_child(header, "escaped_identifier")
    return _node_text(source, ident).strip() if ident is not None else None


def _collect_macro_symbols(source_text: str, cli_defines: list[str]) -> dict[str, int | str]:
    symbols: dict[str, int | str] = {}
    for entry in cli_defines:
        if "=" in entry:
            name, value = entry.split("=", 1)
            symbols[name.strip()] = value.strip() or "1"
        else:
            symbols[entry.strip()] = "1"

    for match in _DEFINE_RE.finditer(source_text):
        name = match.group(1)
        value = (match.group(2) or "1").strip()
        symbols[name] = value
    return symbols


def _resolve_include(
    include_path: str,
    current_file: Path,
    include_dirs: list[Path],
) -> Path | None:
    candidates = [current_file.parent / include_path]
    candidates.extend(directory / include_path for directory in include_dirs)
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return None


def _expand_includes(
    path: Path,
    include_dirs: list[Path],
    stack: list[Path] | None = None,
) -> str:
    stack = stack or []
    real_path = path.resolve()
    if real_path in stack:
        chain = " -> ".join(str(item) for item in stack + [real_path])
        raise ValueError(f"Cyclic `include detected: {chain}")
    stack.append(real_path)

    lines: list[str] = []
    for raw_line in real_path.read_text(encoding="utf-8").splitlines(keepends=True):
        match = _INCLUDE_RE.match(raw_line)
        if not match:
            lines.append(raw_line)
            continue
        include_name = match.group(1)
        include_file = _resolve_include(include_name, current_file=real_path, include_dirs=include_dirs)
        if include_file is None:
            raise ValueError(
                f"Cannot resolve `include \"{include_name}\" referenced by {real_path}"
            )
        lines.append(_expand_includes(include_file, include_dirs=include_dirs, stack=stack))
    stack.pop()
    return "".join(lines)


def _collect_parameter_symbols(
    module_node: Node,
    source: bytes,
    symbols: dict[str, int | str],
) -> dict[str, int | str]:
    for decl in _walk(module_node):
        if decl.type not in {"parameter_declaration", "local_parameter_declaration"}:
            continue
        assign_lists = _find_children(decl, "list_of_param_assignments")
        for assign_list in assign_lists:
            for assign in _find_children(assign_list, "param_assignment"):
                text = _node_text(source, assign)
                if "=" not in text:
                    continue
                name_text, expr_text = text.split("=", 1)
                name = name_text.strip()
                expr = expr_text.strip()
                if not name:
                    continue
                value = _eval_const_expr(expr, symbols)
                symbols[name] = value if value is not None else expr
    return symbols


def _extract_direction_from_header(header_node: Node, source: bytes) -> str | None:
    for candidate in _walk(header_node):
        if candidate.type == "port_direction":
            value = _node_text(source, candidate).strip()
            if value in {"input", "output", "inout"}:
                return value
    for candidate in _walk(header_node):
        if candidate.type in {"input", "output", "inout"}:
            return candidate.type
    return None


def _unique_in_order(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _parse_ansi_ports(
    module_node: Node,
    source: bytes,
    symbols: dict[str, int | str],
) -> list[PortSpec]:
    header = _first_child(module_node, "module_ansi_header")
    if header is None:
        return []
    port_list = _first_child(header, "list_of_port_declarations")
    if port_list is None:
        return []

    ports: list[PortSpec] = []
    current_direction: str | None = None
    current_width = 1

    for declaration in port_list.children:
        if declaration.type != "ansi_port_declaration":
            continue

        names = _unique_in_order(
            _node_text(source, node).strip()
            for node in _find_descendants(declaration, "port_identifier")
        )
        if not names:
            continue
        name = names[0]

        header_node = None
        for candidate in declaration.children:
            if candidate.type in {"variable_port_header", "net_port_header", "interface_port_header"}:
                header_node = candidate
                break

        if header_node is not None:
            current_direction = _extract_direction_from_header(header_node, source) or current_direction
            current_width = _packed_width(header_node, source, symbols)

        if current_direction is None:
            raise ValueError(
                f"Cannot determine direction for ANSI port '{name}' in module "
                f"'{_extract_module_name(module_node, source) or '<unknown>'}'"
            )
        ports.append(PortSpec(name=name, direction=current_direction, width=current_width))

    return ports


def _parse_nonansi_ports(
    module_node: Node,
    source: bytes,
    symbols: dict[str, int | str],
) -> list[PortSpec]:
    header = _first_child(module_node, "module_nonansi_header")
    if header is None:
        return []
    list_of_ports = _first_child(header, "list_of_ports")
    if list_of_ports is None:
        return []

    port_order = _unique_in_order(
        _node_text(source, ident).strip()
        for ident in _find_descendants(list_of_ports, "port_identifier")
    )
    if not port_order:
        return []

    declared: dict[str, PortSpec] = {}
    for child in module_node.children:
        if child.type != "port_declaration" or not child.children:
            continue
        decl = child.children[0]
        direction = _DIR_BY_DECL_TYPE.get(decl.type)
        if direction is None:
            continue
        width = _packed_width(decl, source, symbols)
        names = _unique_in_order(
            _node_text(source, ident).strip()
            for ident in _find_descendants(decl, "port_identifier")
        )
        for name in names:
            declared[name] = PortSpec(name=name, direction=direction, width=width)

    ports: list[PortSpec] = []
    missing: list[str] = []
    for name in port_order:
        item = declared.get(name)
        if item is None:
            missing.append(name)
            continue
        ports.append(item)

    if missing:
        raise ValueError(
            "Non-ANSI module declaration is missing matching port declarations for: "
            + ", ".join(missing)
        )
    return ports


def parse_top_ports(
    rtl_files: list[Path],
    top_module: str,
    include_dirs: list[Path] | None = None,
    defines: list[str] | None = None,
) -> list[PortSpec]:
    include_dirs = include_dirs or []
    defines = defines or []
    if not rtl_files:
        raise ValueError("No RTL files provided")

    source_text = "\n".join(
        _expand_includes(path=path, include_dirs=include_dirs, stack=[]) for path in rtl_files
    )
    source_bytes = source_text.encode("utf-8")

    parser = Parser(_LANGUAGE)
    tree = parser.parse(source_bytes)
    root = tree.root_node

    target_module: Node | None = None
    for child in root.children:
        if child.type != "module_declaration":
            continue
        module_name = _extract_module_name(child, source_bytes)
        if module_name == top_module:
            target_module = child
            break

    if target_module is None:
        raise ValueError(f"Top module '{top_module}' not found in provided RTL files")

    symbols = _collect_macro_symbols(source_text=source_text, cli_defines=defines)
    symbols = _collect_parameter_symbols(module_node=target_module, source=source_bytes, symbols=symbols)

    ports = _parse_ansi_ports(module_node=target_module, source=source_bytes, symbols=symbols)
    if not ports:
        ports = _parse_nonansi_ports(module_node=target_module, source=source_bytes, symbols=symbols)
    if not ports:
        raise ValueError(f"Unable to parse any top-level ports from module '{top_module}'")

    # Keep original declaration order, and reject duplicated names.
    names = [port.name for port in ports]
    if len(names) != len(set(names)):
        duplicates = sorted(name for name in set(names) if names.count(name) > 1)
        raise ValueError(f"Duplicated port declarations detected: {', '.join(duplicates)}")
    return ports
