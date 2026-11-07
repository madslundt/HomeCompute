"""TorchScript graph policy. Importing this file does not import Torch."""
from __future__ import annotations

import json

ATEN = frozenset('abs add add_ amax arange batch_norm cat chunk clamp clamp_min clone contiguous conv1d '
                 'convolution _convolution detach div eq exp expand flatten full full_like index index_select '
                 'Int leaky_relu linear log log10 log_softmax lt masked_fill matmul max mean mul neg '
                 'ones ones_like pad pow relu repeat reshape rsub select sigmoid size slice softmax sqrt square '
                 'squeeze stack stft sub sub_ sum tanh to transpose type_as unsqueeze view view_as_real '
                 'where zeros zeros_like constant_pad_nd'.split())
PRIM = frozenset(('Constant', 'ListConstruct', 'ListUnpack', 'TupleConstruct', 'TupleUnpack',
                  'If', 'Loop', 'NumToTensor', 'dtype', 'device'))


def reject(operators: list[str]) -> None:
    names = sorted(set(operators))
    detail = {'unsupported_operator_count': len(names),
              'unsupported_operators': [name[:160] for name in names[:32]]}
    raise ValueError('Graph contains unreviewed operators: ' + json.dumps(detail, sort_keys=True))


def validate(module, torch) -> list[str]:
    operators = sorted(torch.jit.export_opnames(module))
    if not operators:
        raise ValueError('Graph contains no exported operators')
    unsupported = [name for name in operators if not name.startswith('aten::') or
                   name.split('::', 1)[1].split('.', 1)[0] not in ATEN]
    if unsupported:
        reject(unsupported)
    def nodes(block):
        for node in block.nodes():
            namespace, name = node.kind().split('::', 1)
            if node.kind() == 'prim::Constant':
                for result in node.outputs():
                    value = result.toIValue()
                    if isinstance(value, torch.Tensor) and value.device.type != 'cpu':
                        raise ValueError('Graph embeds a non-CPU tensor')
                    if isinstance(value, torch.device) and value.type != 'cpu':
                        raise ValueError('Graph embeds a non-CPU device')
                    if isinstance(value, str) and value.startswith('cuda'):
                        raise ValueError('Graph embeds a CUDA device')
            if namespace == 'prim' and name not in PRIM or namespace not in ('aten', 'prim'):
                reject([node.kind()])
            if namespace == 'aten' and name.split('.', 1)[0] not in ATEN:
                reject([node.kind()])
            for child in node.blocks():
                nodes(child)
    nodes(module.inlined_graph)
    if any(value.device.type != 'cpu' for value in [*module.parameters(), *module.buffers()]):
        raise ValueError('CPU graph required')
    return operators
