import ast, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
path = sys.argv[1]
src = open(path, encoding="utf-8").read()
tree = ast.parse(src)


def deps_of(fn):
    out = []
    for a in list(fn.args.args) + list(fn.args.kwonlyargs):
        pass
    defaults = fn.args.defaults
    args = fn.args.args[len(fn.args.args) - len(defaults):] if defaults else []
    for name, d in zip(args, defaults):
        if isinstance(d, ast.Call) and getattr(d.func, "id", "") == "Depends":
            a0 = d.args[0] if d.args else None
            out.append(ast.unparse(a0) if a0 is not None else "?")
    return out


for node in tree.body:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        continue
    for dec in node.decorator_list:
        if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
            if getattr(dec.func.value, "id", "") == "router":
                meth = dec.func.attr.upper()
                p = ast.unparse(dec.args[0]) if dec.args else "?"
                body = ast.unparse(node)
                marks = []
                if "get_platform_org_ids" in body:
                    marks.append("SCOPED")
                if "platform_id" in body:
                    marks.append("plat_id")
                print("%-6s %-46s %-34s L%-5d deps=%s %s" % (
                    meth, p, node.name, node.lineno, ",".join(deps_of(node)), " ".join(marks)))
