#!/usr/bin/env python3
"""Read-only, credential-free GB10 inventory for the TensorFold migration gate.

Run on the compute host: python3 tensorfold_inventory.py > inventory.json.
GPU allocations overlap host unified memory; never add these to host usage.
"""
import csv
import datetime
import io
import json
import pathlib
import subprocess

GIB = 1024 ** 3
SAFE_ENV = {
    'MODEL', 'MODEL_ID', 'MODEL_REVISION', 'SERVED_NAME', 'SERVED_MODEL_NAME',
    'CTX', 'CONTEXT', 'MTP', 'SEQS', 'PARALLEL', 'KV_BYTES', 'KV_DTYPE',
    'GPU_MEM', 'LOAD_FORMAT', 'PREWARM', 'PLE_PREFETCH', 'PLE_MMAP',
    'HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE', 'TF_FLASHNEXT_YARN',
}
SAFE_FLAGS = {
    '--model', '--revision', '--served-model-name', '--max-model-len',
    '--gpu-memory-utilization', '--kv-cache-memory-bytes', '--kv-cache-dtype',
    '--max-num-seqs', '--load-format', '--dtype', '--port', '--host',
    '--generation-config', '--tool-call-parser', '--reasoning-parser',
    '--speculative-config', '--model-impl', '--language', '--voice',
    '--name', '--context', '--parallel', '--kv-dtype', '--download-dir',
}


def run(args):
    result = subprocess.run(args, text=True, capture_output=True, timeout=30)
    if result.returncode:
        # Do not echo daemon errors, which may contain unreviewed command data.
        raise RuntimeError(f'{args[0]} failed with exit {result.returncode}')
    return result.stdout


def safe_args(args):
    result = {}
    for index, arg in enumerate(args):
        key, sep, value = arg.partition('=')
        if key in SAFE_FLAGS:
            result[key] = value if sep else (args[index + 1] if index + 1 < len(args) else None)
    return result


def host_memory():
    rows = {}
    for line in pathlib.Path('/proc/meminfo').read_text().splitlines():
        key, value = line.split(':', 1)
        if key in {'MemTotal', 'MemAvailable', 'MemFree', 'Cached', 'Buffers',
                   'SwapTotal', 'SwapFree', 'SwapCached', 'Active', 'Inactive',
                   'SUnreclaim', 'Shmem', 'Unevictable'}:
            rows[key + '_gib'] = round(int(value.split()[0]) * 1024 / GIB, 6)
    rows['SwapUsed_gib'] = round(rows['SwapTotal_gib'] - rows['SwapFree_gib'], 6)
    return rows


def collect():
    names = run(['docker', 'ps', '-aq']).split()
    raw = json.loads(run(['docker', 'inspect', *names])) if names else []
    containers = []
    pid_owner = {}
    for item in raw:
        name = item['Name'].lstrip('/')
        if item['State']['Running']:
            for pid in run(['docker', 'top', name, '-eo', 'pid']).splitlines()[1:]:
                pid_owner[pid.strip()] = name
        cfg, host = item['Config'], item['HostConfig']
        image = json.loads(run(['docker', 'image', 'inspect', item['Image']]))[0]
        containers.append({
            'name': name, 'running': item['State']['Running'],
            'health': item['State'].get('Health', {}).get('Status'),
            'oom_killed': item['State'].get('OOMKilled'),
            'restart_count': item['RestartCount'], 'started_at': item['State']['StartedAt'],
            'image_id': item['Image'], 'image_reference': cfg['Image'],
            'image_repo_digests': image.get('RepoDigests', []),
            'image_size_gib': round(image['Size'] / GIB, 4),
            'runtime_arguments': safe_args(cfg.get('Cmd') or []),
            'runtime_environment': {k: v for entry in cfg.get('Env', [])
                                    for k, _, v in [entry.partition('=')] if k in SAFE_ENV},
            'restart_policy': host['RestartPolicy'],
            'memory_limit_gib': round(host['Memory'] / GIB, 4),
            'nano_cpus': host.get('NanoCpus'), 'pids_limit': host.get('PidsLimit'),
            'network_mode': host['NetworkMode'],
            'networks': sorted(item['NetworkSettings']['Networks']),
            'ports': item['NetworkSettings']['Ports'],
            'mounts': [{'type': m['Type'], 'source': m['Source'],
                        'destination': m['Destination'], 'writable': m['RW']}
                       for m in item['Mounts']],
            'compose': {k: v for k, v in (cfg.get('Labels') or {}).items()
                        if k in {'com.docker.compose.project', 'com.docker.compose.service',
                                 'com.docker.compose.project.config_files',
                                 'com.docker.compose.project.working_dir', 'homecompute.managed'}},
        })
    gpu = []
    for row in csv.reader(io.StringIO(run(['nvidia-smi', '--query-compute-apps=pid,process_name,used_memory',
                                         '--format=csv,noheader,nounits']))):
        pid, process, mib = (value.strip() for value in row)
        gpu.append({'pid': int(pid), 'process': process, 'container': pid_owner.get(pid),
                    'allocation_gib': round(float(mib) / 1024, 6) if mib.replace('.', '').isdigit() else None})
    vm = dict(line.split()[:2] for line in pathlib.Path('/proc/vmstat').read_text().splitlines())
    return {
        'schema_version': 1, 'captured_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'hostname': run(['hostname']).strip(), 'memory': host_memory(),
        'memory_pressure': pathlib.Path('/proc/pressure/memory').read_text().splitlines(),
        'vm_counters': {k: int(vm[k]) for k in ['pswpin', 'pswpout', 'oom_kill', 'pgmajfault']},
        'gpu': run(['nvidia-smi', '--query-gpu=name,driver_version,temperature.gpu,utilization.gpu,power.draw',
                    '--format=csv,noheader,nounits']).strip(),
        'gpu_processes': gpu, 'containers': containers,
        'container_stats': run(['docker', 'stats', '--no-stream', '--format', '{{json .}}']).splitlines(),
        'disks': run(['df', '-B1', '/', '/srv']).splitlines(),
        'running_services': run(['systemctl', 'list-units', '--type=service', '--state=running',
                                 '--no-legend', '--no-pager']).splitlines(),
        'unified_memory_note': 'GPU allocations overlap host RAM and may not appear in Docker RSS accounting.',
    }


if __name__ == '__main__':
    print(json.dumps(collect(), indent=2, sort_keys=True))
