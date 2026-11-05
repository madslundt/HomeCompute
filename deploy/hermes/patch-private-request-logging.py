"""Disable upstream error-path request dumps; retain metadata diagnostics."""
import ast
from pathlib import Path
import sys


def patch(source: str) -> str:
    tree = ast.parse(source)
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name == 'dump_api_request_debug']
    if len(functions) != 1:
        raise ValueError('exact upstream request dump function required')
    function = functions[0]
    if [arg.arg for arg in function.args.args] != ['agent', 'api_kwargs']:
        raise ValueError('upstream request dump signature changed')
    if ast.get_docstring(function) == 'HomeCompute: request payload logging is disabled.':
        return source
    body = ast.get_source_segment(source, function)
    if 'atomic_json_write(dump_file' not in body or 'request_dump_' not in body:
        raise ValueError('upstream request dump implementation changed')
    lines = source.splitlines(keepends=True)
    lines[function.body[0].lineno - 1:function.end_lineno] = [
        '    """HomeCompute: request payload logging is disabled."""\n',
        '    return None\n',
    ]
    result = ''.join(lines)
    ast.parse(result)
    return result


if __name__ == '__main__':
    target = Path(sys.argv[1])
    target.write_text(patch(target.read_text()))
