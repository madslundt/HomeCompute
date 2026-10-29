"""Build narrow Mattermost presentation patches from freshly inspected workflows."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {
    'news': ('Mirror daily news to Mattermost',
             'automations/semantic-research/news-mattermost.js',
             'Format and validate Telegram digest', 'renderNewsMattermost(item.json.message)'),
    'weekly': ('Mirror weekly review to Mattermost',
               'automations/battery-and-system-review/mattermost-report.js',
               'Format Telegram Report', "renderWeeklyMattermost(item.json.report, $('Prepare Review Snapshot').item.json.reportType)"),
}
START = '// BEGIN VALIDATED REPORT BEFORE MATTERMOST PRESENTATION\n'
END = '\n// END VALIDATED REPORT BEFORE MATTERMOST PRESENTATION'


def patch(workflow: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    name, path, formatter_name, render = TARGETS[kind]
    matches = [node for node in workflow['nodes'] if node['name'] == name]
    if len(matches) != 1 or matches[0]['type'] != 'n8n-nodes-base.httpRequest':
        raise ValueError('Expected existing Mattermost mirror requires inspection')
    original = matches[0]['parameters']
    if (original.get('method') != 'POST' or original.get('url') != 'https://mattermost.home.arpa/api/v4/posts'
            or original.get('nodeCredentialType') != 'mattermostApi'):
        raise ValueError('Existing notification transport differs')
    parameters = deepcopy(original)
    fields = parameters.get('bodyParameters', {}).get('parameters', [])
    messages = [field for field in fields if field.get('name') == 'message']
    if len(messages) != 1 or not any(field.get('name') == 'channel_id' for field in fields):
        raise ValueError('Expected existing message and audience fields')
    code = (ROOT / path).read_text()
    formatters = [node for node in workflow['nodes'] if node['name'] == formatter_name]
    if len(formatters) != 1 or formatters[0]['type'] != 'n8n-nodes-base.code':
        raise ValueError('Expected existing validated Code formatter')
    formatter_parameters = deepcopy(formatters[0]['parameters'])
    original = formatter_parameters['jsCode']
    if START in original:
        if original.count(START) != 1 or original.count(END) != 1:
            raise ValueError('Ambiguous prior presentation wrapper')
        original = original.split(START, 1)[1].split(END, 1)[0]
    formatter_parameters['jsCode'] = (code + '\nconst validatedItems = (() => {\n' + START + original + END
        + '\n})();\nreturn validatedItems.map(item => {\ntry { return {...item,json:{...item.json,mattermostMessage:' + render
        + '}}; } catch (_) { return {...item,json:{...item.json,mattermostMessage:"",mattermostPresentationError:"Unsupported validated report presentation"}}; }\n});\n')
    messages[0]['value'] = "={{ $('" + formatter_name + "').item.json.mattermostMessage }}"
    return [{'type': 'updateNodeParameters', 'nodeName': formatter_name, 'parameters': formatter_parameters, 'replace': True},
            {'type': 'updateNodeParameters', 'nodeName': name, 'parameters': parameters, 'replace': True}]
